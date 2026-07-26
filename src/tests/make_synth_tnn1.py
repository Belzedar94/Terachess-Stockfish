#!/usr/bin/env python3
"""Build a synthetic but fully valid TNN1 network for engine-side testing.

Self-contained on purpose: it depends only on numpy/struct/hashlib and never
imports tools/terannue, so the engine's loader and forward pass are exercised
against an INDEPENDENT implementation of the byte layout of
docs/nnue-tera-s.md section 7.

    python make_synth_tnn1.py out.tnn1 [--seed N] [--corrupt FIELD]

--corrupt writes a deliberately broken file so the fail-closed path of the
loader can be tested. FIELD is one of: magic, version, arch_hash, dims, size.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys

import numpy as np

# --- frozen dimensions (contract sections 3, 4 and 7) ----------------------
KING_BUCKETS = 8
PLANES = 51
SQUARES = 256
L1 = 256
OUT_BUCKETS = 8
NUM_FEATURES = KING_BUCKETS * PLANES * SQUARES          # 104448

FC0_IN, FC0_OUT = 256, 16      # pairwise reduces 512 -> 256 (contract 4)
FC1_IN, FC1_OUT = 16, 32
FC2_IN, FC2_OUT = 32, 1

FT_WEIGHT_MAX = 127
FT_BIAS_MAX = 8191

DESCRIPTOR = ("terachess-nnue-S;kb=8;planes=51;sq=256;L1=256;ob=8;"
              "ftscale=128;act=clip0_127;pairwise=shr7;stack=16-32-1")
ARCH_HASH = hashlib.sha256(DESCRIPTOR.encode("ascii")).digest()

HEADER = struct.Struct("<4sH32s4I")
STACK_BYTES = (FC0_IN * FC0_OUT + FC0_OUT * 4
               + FC1_IN * FC1_OUT + FC1_OUT * 4
               + FC2_IN * FC2_OUT + FC2_OUT * 4)
FILE_BYTES = (HEADER.size
              + NUM_FEATURES * L1 * 2
              + L1 * 2
              + NUM_FEATURES * OUT_BUCKETS * 4
              + OUT_BUCKETS * STACK_BYTES)


def build(seed: int) -> bytes:
    rng = np.random.default_rng(seed)

    # Small weights on purpose: the accumulator then lands around the 0..127
    # clipping window, so the clipped ReLU and the pairwise product are
    # genuinely exercised instead of saturating everywhere.
    ftw = rng.integers(-16, 17, (NUM_FEATURES, L1), dtype=np.int16)
    ftb = rng.integers(-64, 65, L1, dtype=np.int16)
    psqt = rng.integers(-2000, 2001, (NUM_FEATURES, OUT_BUCKETS), dtype=np.int32)

    assert int(np.abs(ftw).max()) <= FT_WEIGHT_MAX
    assert int(np.abs(ftb).max()) <= FT_BIAS_MAX

    chunks = [HEADER.pack(b"TNN1", 1, ARCH_HASH,
                          KING_BUCKETS, PLANES, L1, OUT_BUCKETS),
              ftw.astype("<i2", copy=False).tobytes(),
              ftb.astype("<i2", copy=False).tobytes(),
              psqt.astype("<i4", copy=False).tobytes()]

    for _ in range(OUT_BUCKETS):
        for (n_in, n_out), bias_lim in (((FC0_IN, FC0_OUT), 4096),
                                        ((FC1_IN, FC1_OUT), 4096),
                                        ((FC2_IN, FC2_OUT), 8192)):
            w = rng.integers(-32, 33, (n_in, n_out), dtype=np.int8)
            b = rng.integers(-bias_lim, bias_lim + 1, n_out, dtype=np.int32)
            chunks.append(w.astype("<i1", copy=False).tobytes())
            chunks.append(b.astype("<i4", copy=False).tobytes())

    blob = b"".join(chunks)
    assert len(blob) == FILE_BYTES, (len(blob), FILE_BYTES)
    return blob


def corrupt(blob: bytes, field: str) -> bytes:
    b = bytearray(blob)
    if field == "magic":
        b[0:4] = b"TNNX"
    elif field == "version":
        b[4:6] = (2).to_bytes(2, "little")
    elif field == "arch_hash":
        b[6] ^= 0xFF
    elif field == "dims":
        b[38:42] = (9).to_bytes(4, "little")     # kingBuckets = 9
    elif field == "size":
        b.append(0)
    else:
        raise SystemExit(f"unknown --corrupt field '{field}'")
    return bytes(b)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=20260726)
    ap.add_argument("--corrupt", default=None)
    args = ap.parse_args(argv)

    blob = build(args.seed)
    if args.corrupt:
        blob = corrupt(blob, args.corrupt)

    with open(args.out, "wb") as fh:
        fh.write(blob)

    print(f"wrote {args.out}  {len(blob):,} B  "
          f"(canonical size {FILE_BYTES:,})  seed {args.seed}"
          + (f"  CORRUPTED:{args.corrupt}" if args.corrupt else ""))
    print(f"arch_hash {ARCH_HASH.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
