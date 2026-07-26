#!/usr/bin/env python3
"""Pure-integer reference forward pass for the Terachess NNUE "S" network.

This module is the AUTHORITY of the ==0 cp parity gate (contract section 8):
``model.py`` and the C++ engine must both reproduce these integers exactly.
It never imports torch.  numpy appears only as a typed container for the
weight blocks -- every arithmetic operation below is performed on Python
``int`` objects, so there is no hidden width, no wraparound and no rounding
mode to argue about.

Integer pipeline (contract sections 4 and 5)::

    acc[p][k]  = ft_bias[k] + sum(ft_weights[row][k] for row in active[p])
                 |acc| <= 8191 + 128*127 = 24447  <  32767          (i16 safe)
    x          = [clip(acc[stm], 0, 127) | clip(acc[nstm], 0, 127)]  (512)
    pair[j]    = (x[a] * x[a + 128]) >> 7                            (256, u8)
    fc0        = trunc((b0 + sum(w0 * pair)) / 64), clipped to 0..127 (16)
    fc1        = trunc((b1 + sum(w1 * fc0 )) / 64), clipped to 0..127 (32)
    positional = b2 + sum(w2 * fc1)                                  (i32)
    psqt       = trunc((sum psqt[stm] - sum psqt[nstm]) / 2)         (i32)
    eval_cp    = trunc((psqt + positional) / FV_SCALE)

Every division truncates toward zero, as the contract demands.

TWO POINTS WHERE THE FROZEN CONTRACT IS UNDER-SPECIFIED OR SELF-CONTRADICTORY
(implemented as faithfully as possible, never silently altered -- see the
report and ``DISCREPANCIES`` below):

  D1  Section 4 states ``pairwise mul >>7 --> 256`` yet section 7 declares
      ``fc0 i8[512x16]``.  A layer fed by the 256-wide pairwise output cannot
      have 512 input rows.  ``FC0_IN`` is therefore 256 (section 4 wins,
      because it is the only self-consistent reading of the computation) and
      the constant is isolated so the file layout can be switched with one
      edit if the contract is amended the other way.
  D2  Sections 4 and 7 never fix ``FV_SCALE`` nor the psqt combination rule.
      We use the Stockfish convention the port will inherit: ``FV_SCALE = 16``
      and ``psqt = (psqt_stm - psqt_nstm) / 2``.  Both are powers of two with
      truncating division, as section 5 requires.
"""

from __future__ import annotations

import dataclasses
import hashlib
from operator import add

import numpy as np

# ---------------------------------------------------------------------------
# Frozen architecture constants (docs/nnue-tera-s.md)
# ---------------------------------------------------------------------------

MAGIC = b"TNN1"
VERSION = 1

KING_BUCKETS = 8
PLANES = 51
SQUARES = 256
L1 = 256                                   # accumulator width per perspective
OUT_BUCKETS = 8
NUM_FEATURES = KING_BUCKETS * PLANES * SQUARES        # 104448
MAX_ACTIVE = 128

FT_SCALE = 128                             # activation scale, contract 5
FT_ACT_MAX = 127                           # clamp(acc, 0, 127)
FT_WEIGHT_MAX = 127                        # |quantised ft weight| <= 127
FT_BIAS_MAX = 8191                         # |quantised ft bias|   <= 8191
ACC_BOUND = MAX_ACTIVE * FT_WEIGHT_MAX + FT_BIAS_MAX  # 24447, the guarantee
assert ACC_BOUND == 24447 and ACC_BOUND < 32767

PAIRWISE_SHIFT = 7
PAIRWISE_OUT = L1                          # 512 clipped values -> 256 products

WEIGHT_SCALE_HIDDEN = 64                   # i8 stack weights represent w/64
HIDDEN_WEIGHT_MAX = 127
HIDDEN_SHIFT = 6                           # 2**6 == WEIGHT_SCALE_HIDDEN
assert 1 << HIDDEN_SHIFT == WEIGHT_SCALE_HIDDEN

OUTPUT_SCALE = FT_SCALE * WEIGHT_SCALE_HIDDEN         # 8192
FV_SCALE = 16                                          # D2
NNUE2SCORE = OUTPUT_SCALE // FV_SCALE                  # 512 cp per float unit

FC0_IN = PAIRWISE_OUT                      # 256  (D1)
FC0_OUT = 16
FC1_IN = FC0_OUT
FC1_OUT = 32
FC2_IN = FC1_OUT
FC2_OUT = 1

DESCRIPTOR = ("terachess-nnue-S;kb=8;planes=51;sq=256;L1=256;ob=8;"
              "ftscale=128;act=clip0_127;pairwise=shr7;stack=16-32-1")
ARCH_HASH = hashlib.sha256(DESCRIPTOR.encode("ascii")).digest()
DIMS = (KING_BUCKETS, PLANES, L1, OUT_BUCKETS)

DISCREPANCIES = (
    "D1 fc0 declared i8[512x16] in contract 7 but fed by the 256-wide "
    "pairwise output of contract 4; implemented as i8[256x16].",
    "D2 FV_SCALE and the psqt combination rule are unspecified; using the "
    "Stockfish convention FV_SCALE=16 and (psqt_stm - psqt_nstm) / 2.",
    "D3 the royal-relative factor of contract 6 is not exactly coalescible "
    "into real rows (the offset depends on the king square, the real row "
    "only on its bucket); serialize.py folds the bucket-uniform mean.",
)


# ---------------------------------------------------------------------------
# Truncating integer helpers
# ---------------------------------------------------------------------------


def sdiv(value: int, divisor: int) -> int:
    """Integer division truncated toward zero (contract section 5)."""
    return value // divisor if value >= 0 else -((-value) // divisor)


def clip(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


# ---------------------------------------------------------------------------
# Network container
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class Stack:
    """One output bucket's fc0/fc1/fc2 block.  Weights are stored [in][out],
    matching the ``i8[in x out]`` notation of contract section 7."""

    w0: np.ndarray      # int8  (FC0_IN, FC0_OUT)
    b0: np.ndarray      # int32 (FC0_OUT,)
    w1: np.ndarray      # int8  (FC1_IN, FC1_OUT)
    b1: np.ndarray      # int32 (FC1_OUT,)
    w2: np.ndarray      # int8  (FC2_IN, FC2_OUT)
    b2: np.ndarray      # int32 (FC2_OUT,)


@dataclasses.dataclass(slots=True)
class QuantizedNet:
    """A loaded TNN1 file.  All arrays hold exact integers."""

    ft_weights: np.ndarray          # int16 (NUM_FEATURES, L1)
    ft_bias: np.ndarray             # int16 (L1,)
    psqt: np.ndarray                # int32 (NUM_FEATURES, OUT_BUCKETS)
    stacks: list[Stack]             # OUT_BUCKETS entries
    arch_hash: bytes = ARCH_HASH
    version: int = VERSION

    def validate(self) -> None:
        if self.ft_weights.shape != (NUM_FEATURES, L1):
            raise ValueError(f"ft_weights shape {self.ft_weights.shape}")
        if self.ft_weights.dtype != np.int16:
            raise ValueError(f"ft_weights dtype {self.ft_weights.dtype}")
        if self.ft_bias.shape != (L1,) or self.ft_bias.dtype != np.int16:
            raise ValueError("ft_bias must be int16[L1]")
        if self.psqt.shape != (NUM_FEATURES, OUT_BUCKETS):
            raise ValueError(f"psqt shape {self.psqt.shape}")
        if self.psqt.dtype != np.int32:
            raise ValueError(f"psqt dtype {self.psqt.dtype}")
        if len(self.stacks) != OUT_BUCKETS:
            raise ValueError(f"{len(self.stacks)} stacks, expected {OUT_BUCKETS}")
        shapes = ((FC0_IN, FC0_OUT), (FC0_OUT,), (FC1_IN, FC1_OUT),
                  (FC1_OUT,), (FC2_IN, FC2_OUT), (FC2_OUT,))
        for b, st in enumerate(self.stacks):
            arrays = (st.w0, st.b0, st.w1, st.b1, st.w2, st.b2)
            for arr, shape in zip(arrays, shapes):
                if arr.shape != shape:
                    raise ValueError(f"stack {b}: shape {arr.shape} != {shape}")
            for arr in (st.w0, st.w1, st.w2):
                if arr.dtype != np.int8:
                    raise ValueError(f"stack {b}: weights must be int8")
            for arr in (st.b0, st.b1, st.b2):
                if arr.dtype != np.int32:
                    raise ValueError(f"stack {b}: biases must be int32")
        if self.arch_hash != ARCH_HASH:
            raise ValueError("arch_hash does not match the frozen descriptor")
        if int(np.abs(self.ft_weights).max(initial=0)) > FT_WEIGHT_MAX:
            raise ValueError("ft weight outside +-127")
        if int(np.abs(self.ft_bias).max(initial=0)) > FT_BIAS_MAX:
            raise ValueError("ft bias outside +-8191")

    # -- convenience --------------------------------------------------------

    @staticmethod
    def zeros() -> "QuantizedNet":
        return QuantizedNet(
            np.zeros((NUM_FEATURES, L1), dtype=np.int16),
            np.zeros(L1, dtype=np.int16),
            np.zeros((NUM_FEATURES, OUT_BUCKETS), dtype=np.int32),
            [Stack(np.zeros((FC0_IN, FC0_OUT), np.int8),
                   np.zeros(FC0_OUT, np.int32),
                   np.zeros((FC1_IN, FC1_OUT), np.int8),
                   np.zeros(FC1_OUT, np.int32),
                   np.zeros((FC2_IN, FC2_OUT), np.int8),
                   np.zeros(FC2_OUT, np.int32)) for _ in range(OUT_BUCKETS)])


# ---------------------------------------------------------------------------
# The forward pass
# ---------------------------------------------------------------------------


def accumulate(net: QuantizedNet, rows) -> list[int]:
    """``ft_bias + sum of the active feature rows``, exact Python integers."""
    acc = net.ft_bias.tolist()
    weights = net.ft_weights
    for row in rows:
        acc = list(map(add, acc, weights[row].tolist()))
    return acc


def accumulator_bound(net: QuantizedNet, rows) -> int:
    """Max |accumulator| actually reached (used by the overflow selftest)."""
    return max(abs(v) for v in accumulate(net, rows))


def transform(acc_stm: list[int], acc_nstm: list[int]) -> list[int]:
    """clipped ReLU over the concatenated 512, then the pairwise ``>> 7``.

    Pairing follows the Stockfish convention that contract section 4
    describes: the 512 concatenated values are split into four blocks of
    ``L1 // 2``; the two blocks of each perspective are multiplied together,
    yielding ``L1 // 2`` products per perspective and 256 in total.
    """
    half = L1 // 2
    out: list[int] = []
    for acc in (acc_stm, acc_nstm):
        lo = acc[:half]
        hi = acc[half:]
        for j in range(half):
            left = clip(lo[j], 0, FT_ACT_MAX)
            right = clip(hi[j], 0, FT_ACT_MAX)
            out.append((left * right) >> PAIRWISE_SHIFT)
    return out


def _affine_relu(inputs: list[int], weights: np.ndarray,
                 bias: np.ndarray) -> list[int]:
    w = weights.tolist()                      # [in][out]
    out = bias.tolist()
    for j, x in enumerate(inputs):
        if x:
            row = w[j]
            out = [o + x * r for o, r in zip(out, row)]
    return [clip(sdiv(v, WEIGHT_SCALE_HIDDEN), 0, FT_ACT_MAX) for v in out]


def _affine(inputs: list[int], weights: np.ndarray,
            bias: np.ndarray) -> list[int]:
    w = weights.tolist()
    out = bias.tolist()
    for j, x in enumerate(inputs):
        if x:
            row = w[j]
            out = [o + x * r for o, r in zip(out, row)]
    return out


def psqt_value(net: QuantizedNet, stm_rows, nstm_rows, bucket: int) -> int:
    """``(sum psqt[stm] - sum psqt[nstm]) / 2`` for one output bucket (D2)."""
    table = net.psqt
    total = 0
    for row in stm_rows:
        total += int(table[row, bucket])
    for row in nstm_rows:
        total -= int(table[row, bucket])
    return sdiv(total, 2)


def evaluate(net: QuantizedNet, stm_rows, nstm_rows, bucket: int) -> dict:
    """Full inference.  Returns eval_cp / psqt / positional / bucket."""
    if not 0 <= bucket < OUT_BUCKETS:
        raise ValueError(f"output bucket {bucket} outside [0, {OUT_BUCKETS})")
    acc_stm = accumulate(net, stm_rows)
    acc_nstm = accumulate(net, nstm_rows)
    for acc in (acc_stm, acc_nstm):
        worst = max(abs(v) for v in acc)
        if worst > 32767:
            raise OverflowError(f"accumulator {worst} does not fit in i16")
    pair = transform(acc_stm, acc_nstm)
    st = net.stacks[bucket]
    h0 = _affine_relu(pair, st.w0, st.b0)
    h1 = _affine_relu(h0, st.w1, st.b1)
    positional = _affine(h1, st.w2, st.b2)[0]
    psqt = psqt_value(net, stm_rows, nstm_rows, bucket)
    return {
        "eval_cp": sdiv(psqt + positional, FV_SCALE),
        "psqt": psqt,
        "positional": positional,
        "bucket": bucket,
        "acc_max": max(max(abs(v) for v in acc_stm),
                       max(abs(v) for v in acc_nstm)),
    }


def describe() -> str:
    return (f"TNN1 v{VERSION} {DESCRIPTOR}\n"
            f"arch_hash = {ARCH_HASH.hex()}\n"
            f"dims      = kb {KING_BUCKETS}, planes {PLANES}, L1 {L1}, "
            f"ob {OUT_BUCKETS}\n"
            f"scales    = ft {FT_SCALE}, hidden {WEIGHT_SCALE_HIDDEN}, "
            f"output {OUTPUT_SCALE}, fv {FV_SCALE} (nnue2score {NNUE2SCORE})\n"
            f"acc bound = {ACC_BOUND} of 32767 "
            f"(margin {32767 / ACC_BOUND:.3f}x)")


if __name__ == "__main__":
    print(describe())
    for line in DISCREPANCIES:
        print("DISCREPANCY:", line)
