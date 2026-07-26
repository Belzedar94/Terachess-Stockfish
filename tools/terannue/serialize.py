#!/usr/bin/env python3
"""TNN1 export/import for the Terachess NNUE "S" network.

Contract section 7 byte layout (little-endian, densely packed, no padding)::

    magic       4 B    "TNN1"
    version     u16    1
    arch_hash   32 B   sha256 of the canonical descriptor
    dims        u32x4  kingBuckets=8, planes=51, L1=256, outBuckets=8
    ft_weights  i16[104448 x 256]        row-major
    ft_bias     i16[256]
    psqt        i32[104448 x 8]          row-major
    stacks      8 x (fc0 i8[256 x 16] + b i32[16]
                   + fc1 i8[16 x 32]  + b i32[32]
                   + fc2 i8[32 x 1]   + b i32[1])

The ``fc0`` block is ``256 x 16`` and not the ``512 x 16`` printed in the
contract: see discrepancy D1 in ``quantized_forward.py``.  Everything else is
byte-for-byte what section 7 specifies.

Export pipeline (contract sections 5 and 6):

  1. coalesce the three train-only factors into the 104448 real rows;
  2. quantise with round-half-to-even onto the inference grid;
  3. **ABORT** -- never saturate, never wrap -- if any weight left its range;
  4. write atomically;
  5. reload and compare every array to the in-memory tensors.

``load_tnn1`` needs neither torch nor the model; it fails closed on magic,
version, arch_hash, dims and file size.
"""

from __future__ import annotations

import os
import struct

import numpy as np

import quantized_forward as QF

HEADER = struct.Struct("<4sH32s4I")
HEADER_SIZE = HEADER.size                      # 54

FT_BYTES = QF.NUM_FEATURES * QF.L1 * 2
BIAS_BYTES = QF.L1 * 2
PSQT_BYTES = QF.NUM_FEATURES * QF.OUT_BUCKETS * 4
STACK_BYTES = (QF.FC0_IN * QF.FC0_OUT + QF.FC0_OUT * 4
               + QF.FC1_IN * QF.FC1_OUT + QF.FC1_OUT * 4
               + QF.FC2_IN * QF.FC2_OUT + QF.FC2_OUT * 4)
FILE_BYTES = (HEADER_SIZE + FT_BYTES + BIAS_BYTES + PSQT_BYTES
              + QF.OUT_BUCKETS * STACK_BYTES)


class ExportAbort(RuntimeError):
    """A weight left its contractual range after coalescing.  Never saturate."""


# ---------------------------------------------------------------------------
# Quantisation with hard range enforcement
# ---------------------------------------------------------------------------


def _check(name: str, values: np.ndarray, limit: int) -> np.ndarray:
    worst = int(np.abs(values).max(initial=0))
    if worst > limit:
        bad = int(np.count_nonzero(np.abs(values) > limit))
        raise ExportAbort(
            f"{name}: {bad} value(s) outside +-{limit} after coalescing "
            f"(worst |v| = {worst}).  Export aborted; tighten the factor "
            f"budget in model.FactorBudget or lower the learning rate.")
    return values


def quantize_model(model, verbose: bool = False) -> QF.QuantizedNet:
    """Coalesce the factors and quantise, aborting on any out-of-range weight."""
    import torch

    with torch.no_grad():
        table = model.coalesced_table().to(torch.float64).cpu()
        ftw = torch.round(table[:, :QF.L1] * QF.FT_SCALE).numpy()
        psqt = torch.round(table[:, QF.L1:] * QF.OUTPUT_SCALE).numpy()
        ftb = torch.round(model.ft_bias.detach().to(torch.float64).cpu()
                          * QF.FT_SCALE).numpy()

        _check("ft_weights", ftw, QF.FT_WEIGHT_MAX)
        _check("ft_bias", ftb, QF.FT_BIAS_MAX)
        _check("psqt", psqt, 2 ** 31 - 1)

        stacks = []
        for bucket in range(QF.OUT_BUCKETS):
            arrays = []
            for layer, n_out in ((model.fc0, QF.FC0_OUT),
                                 (model.fc1, QF.FC1_OUT),
                                 (model.fc2, QF.FC2_OUT)):
                w = layer.weight.detach().to(torch.float64).cpu()
                b = layer.bias.detach().to(torch.float64).cpu()
                w = w.view(QF.OUT_BUCKETS, n_out, -1)[bucket]
                b = b.view(QF.OUT_BUCKETS, n_out)[bucket]
                qw = torch.round(w * QF.WEIGHT_SCALE_HIDDEN).numpy()
                qb = torch.round(b * QF.OUTPUT_SCALE).numpy()
                _check(f"stack{bucket}.weight", qw, QF.HIDDEN_WEIGHT_MAX)
                _check(f"stack{bucket}.bias", qb, 2 ** 31 - 1)
                # contract stores [in][out]; nn.Linear keeps [out][in]
                arrays.append(np.ascontiguousarray(qw.T, dtype=np.int8))
                arrays.append(np.ascontiguousarray(qb, dtype=np.int32))
            stacks.append(QF.Stack(*arrays))

    net = QF.QuantizedNet(
        np.ascontiguousarray(ftw, dtype=np.int16),
        np.ascontiguousarray(ftb, dtype=np.int16),
        np.ascontiguousarray(psqt, dtype=np.int32),
        stacks)
    net.validate()
    if verbose:
        print(f"  ft |w| max   {int(np.abs(ftw).max()):>6d} / {QF.FT_WEIGHT_MAX}")
        print(f"  ft |b| max   {int(np.abs(ftb).max()):>6d} / {QF.FT_BIAS_MAX}")
        print(f"  psqt |p| max {int(np.abs(psqt).max()):>6d}")
    return net


# ---------------------------------------------------------------------------
# Binary I/O
# ---------------------------------------------------------------------------


def _pack(net: QF.QuantizedNet) -> bytes:
    chunks = [HEADER.pack(QF.MAGIC, QF.VERSION, QF.ARCH_HASH, *QF.DIMS),
              net.ft_weights.astype("<i2", copy=False).tobytes(),
              net.ft_bias.astype("<i2", copy=False).tobytes(),
              net.psqt.astype("<i4", copy=False).tobytes()]
    for st in net.stacks:
        chunks.append(st.w0.astype("<i1", copy=False).tobytes())
        chunks.append(st.b0.astype("<i4", copy=False).tobytes())
        chunks.append(st.w1.astype("<i1", copy=False).tobytes())
        chunks.append(st.b1.astype("<i4", copy=False).tobytes())
        chunks.append(st.w2.astype("<i1", copy=False).tobytes())
        chunks.append(st.b2.astype("<i4", copy=False).tobytes())
    blob = b"".join(chunks)
    if len(blob) != FILE_BYTES:
        raise AssertionError(f"packed {len(blob)} bytes, expected {FILE_BYTES}")
    return blob


def write_tnn1(net: QF.QuantizedNet, path: str | os.PathLike) -> int:
    """Atomic write; returns the file size in bytes."""
    net.validate()
    blob = _pack(net)
    tmp = f"{os.fspath(path)}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return len(blob)


def load_tnn1(path: str | os.PathLike) -> QF.QuantizedNet:
    """Strict loader: any mismatch of magic/version/hash/dims/size rejects."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < HEADER_SIZE:
        raise ValueError("truncated TNN1 header")
    magic, version, arch_hash, *dims = HEADER.unpack_from(blob, 0)
    if magic != QF.MAGIC:
        raise ValueError(f"bad magic {magic!r} (expected {QF.MAGIC!r})")
    if version != QF.VERSION:
        raise ValueError(f"unsupported TNN1 version {version}")
    if arch_hash != QF.ARCH_HASH:
        raise ValueError(f"arch_hash mismatch: file {arch_hash.hex()} "
                         f"!= build {QF.ARCH_HASH.hex()}")
    if tuple(dims) != QF.DIMS:
        raise ValueError(f"dims {tuple(dims)} != {QF.DIMS}")
    if len(blob) != FILE_BYTES:
        raise ValueError(f"TNN1 size {len(blob)} != {FILE_BYTES}")

    pos = HEADER_SIZE
    ftw = np.frombuffer(blob, "<i2", QF.NUM_FEATURES * QF.L1, pos
                        ).reshape(QF.NUM_FEATURES, QF.L1)
    pos += FT_BYTES
    ftb = np.frombuffer(blob, "<i2", QF.L1, pos)
    pos += BIAS_BYTES
    psqt = np.frombuffer(blob, "<i4", QF.NUM_FEATURES * QF.OUT_BUCKETS, pos
                         ).reshape(QF.NUM_FEATURES, QF.OUT_BUCKETS)
    pos += PSQT_BYTES

    stacks = []
    shapes = ((QF.FC0_IN, QF.FC0_OUT), (QF.FC1_IN, QF.FC1_OUT),
              (QF.FC2_IN, QF.FC2_OUT))
    for _ in range(QF.OUT_BUCKETS):
        arrays = []
        for n_in, n_out in shapes:
            arrays.append(np.frombuffer(blob, "<i1", n_in * n_out, pos
                                        ).reshape(n_in, n_out))
            pos += n_in * n_out
            arrays.append(np.frombuffer(blob, "<i4", n_out, pos))
            pos += n_out * 4
        stacks.append(QF.Stack(*arrays))
    if pos != len(blob):
        raise ValueError(f"trailing bytes: consumed {pos} of {len(blob)}")

    net = QF.QuantizedNet(np.ascontiguousarray(ftw),
                          np.ascontiguousarray(ftb),
                          np.ascontiguousarray(psqt), stacks,
                          arch_hash=arch_hash, version=version)
    net.validate()
    return net


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.array_equal(a, b))


def verify_roundtrip(net: QF.QuantizedNet, path: str | os.PathLike) -> None:
    """Reload the file just written and compare every array bit for bit."""
    back = load_tnn1(path)
    if not _same(net.ft_weights, back.ft_weights):
        raise ExportAbort("ft_weights differ after reload")
    if not _same(net.ft_bias, back.ft_bias):
        raise ExportAbort("ft_bias differs after reload")
    if not _same(net.psqt, back.psqt):
        raise ExportAbort("psqt differs after reload")
    for b, (x, y) in enumerate(zip(net.stacks, back.stacks)):
        for name in ("w0", "b0", "w1", "b1", "w2", "b2"):
            if not _same(getattr(x, name), getattr(y, name)):
                raise ExportAbort(f"stack {b} {name} differs after reload")


def save_model(model, path: str | os.PathLike, verbose: bool = True
               ) -> tuple[QF.QuantizedNet, int]:
    """Coalesce, quantise, abort-on-range, write, reload and compare."""
    net = quantize_model(model, verbose=verbose)
    size = write_tnn1(net, path)
    verify_roundtrip(net, path)
    if verbose:
        print(f"  wrote {os.fspath(path)}  {size:,} B "
              f"({size / 1e6:.2f} MB / {size / 2**20:.2f} MiB), "
              "reload verified bit-exact")
    return net, size


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def self_test(tmpdir: str) -> list[str]:
    rng = np.random.default_rng(7)
    net = QF.QuantizedNet.zeros()
    net.ft_weights[:] = rng.integers(-QF.FT_WEIGHT_MAX, QF.FT_WEIGHT_MAX + 1,
                                     net.ft_weights.shape, dtype=np.int16)
    net.ft_bias[:] = rng.integers(-QF.FT_BIAS_MAX, QF.FT_BIAS_MAX + 1,
                                  net.ft_bias.shape, dtype=np.int16)
    net.psqt[:] = rng.integers(-5000, 5000, net.psqt.shape, dtype=np.int32)
    for st in net.stacks:
        st.w0[:] = rng.integers(-127, 128, st.w0.shape, dtype=np.int8)
        st.w1[:] = rng.integers(-127, 128, st.w1.shape, dtype=np.int8)
        st.w2[:] = rng.integers(-127, 128, st.w2.shape, dtype=np.int8)
        st.b0[:] = rng.integers(-4096, 4096, st.b0.shape, dtype=np.int32)
        st.b1[:] = rng.integers(-4096, 4096, st.b1.shape, dtype=np.int32)
        st.b2[:] = rng.integers(-4096, 4096, st.b2.shape, dtype=np.int32)

    path = os.path.join(tmpdir, "selftest.tnn1")
    size = write_tnn1(net, path)
    assert size == FILE_BYTES, (size, FILE_BYTES)
    verify_roundtrip(net, path)

    # Fail-closed checks.
    blob = bytearray(open(path, "rb").read())
    for label, mutate in (
            ("magic", lambda b: b.__setitem__(slice(0, 4), b"TNNX")),
            ("version", lambda b: b.__setitem__(slice(4, 6), b"\x02\x00")),
            ("arch_hash", lambda b: b.__setitem__(6, b[6] ^ 0xFF)),
            ("dims", lambda b: b.__setitem__(slice(38, 42),
                                             (9).to_bytes(4, "little"))),
            ("size", lambda b: b.extend(b"\x00")),
    ):
        bad = bytearray(blob)
        mutate(bad)
        broken = os.path.join(tmpdir, f"bad-{label}.tnn1")
        with open(broken, "wb") as handle:
            handle.write(bytes(bad))
        try:
            load_tnn1(broken)
        except ValueError:
            pass
        else:
            raise AssertionError(f"loader accepted a bad {label}")
    return ["roundtrip", "fail-closed", f"size={size}"]


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        print("serialize self-test OK (%s)" % ", ".join(self_test(tmp)))
    print(f"TNN1 file size = {FILE_BYTES:,} B "
          f"({FILE_BYTES / 1e6:.2f} MB / {FILE_BYTES / 2**20:.2f} MiB)")
