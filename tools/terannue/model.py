#!/usr/bin/env python3
"""PyTorch model for the Terachess NNUE "S" network.

Implements contract sections 4 (architecture), 5 (quantisation) and 6
(train-only factorisation) of ``docs/nnue-tera-s.md``.  The integer grid is
defined once, in ``quantized_forward.py``; this module only mirrors it.

Two forward paths share the same parameters:

  * ``forward(..., quant=False)``  -- plain float, used for training.
  * ``forward(..., quant=True)``   -- fake quantisation: every weight is
    rounded and clamped onto the inference grid with a straight-through
    estimator and the arithmetic is the exact integer pipeline of
    ``quantized_forward`` emulated in float64.  It returns integer-valued
    tensors, so the ==0 cp gate can be checked directly against the pure
    integer reference.  Requires a coalesced (non-factorised) model.

Float <-> integer correspondence (all scales are powers of two):

    ft weight   w    <-> W  = round(w * 128),  |W| <= 127
    ft bias     b    <-> B  = round(b * 128),  |B| <= 8191
    activation  a    <-> A  = clamp(acc, 0, 127),  a = A / 128
    stack weight v   <-> V  = round(v * 64),   |V| <= 127
    stack bias  c    <-> C  = round(c * 8192)
    psqt        p    <-> P  = round(p * 8192)
    model output y   <-> eval_cp = trunc((psqt + positional) / 16),
                         so cp ~= y * 512  (NNUE2SCORE)
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

import features as FT
import quantized_forward as QF

# TF32 silently truncates mantissas; the fake-quant path must stay exact.
torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

L1 = QF.L1
OUT_BUCKETS = QF.OUT_BUCKETS
PAIRWISE_OUT = QF.PAIRWISE_OUT
FC0_OUT, FC1_OUT = QF.FC0_OUT, QF.FC1_OUT

ACT_MAX = QF.FT_ACT_MAX / QF.FT_SCALE                 # 127/128
FT_WEIGHT_LIMIT = QF.FT_WEIGHT_MAX / QF.FT_SCALE      # 127/128
FT_BIAS_LIMIT = QF.FT_BIAS_MAX / QF.FT_SCALE          # 8191/128
HIDDEN_WEIGHT_LIMIT = QF.HIDDEN_WEIGHT_MAX / QF.WEIGHT_SCALE_HIDDEN   # 127/64
HIDDEN_BIAS_LIMIT = 32.0                              # engineering guard
PSQT_LIMIT = 8.0                                      # +-4096 cp per row


@dataclasses.dataclass(frozen=True)
class FactorBudget:
    """Share of the +-127/128 FT range reserved for each summand.

    ``clip_weights_`` keeps every component inside ``share * 127/128``, which
    satisfies contract section 5 for each individual weight AND guarantees
    that the coalesced sum written by ``serialize.py`` is still inside the
    i8-scaled range -- so the mandatory export ABORT never has to fire.
    """

    real: float = 0.55
    piece_square: float = 0.20
    piece_type: float = 0.10
    royal: float = 0.15

    def __post_init__(self) -> None:
        total = self.real + self.piece_square + self.piece_type + self.royal
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"factor budget must sum to 1.0, got {total}")


# ---------------------------------------------------------------------------
# Straight-through estimators
# ---------------------------------------------------------------------------


def ste_round(x: torch.Tensor) -> torch.Tensor:
    return x + (torch.round(x) - x).detach()


def ste_clamp(x: torch.Tensor, lo: float, hi: float) -> torch.Tensor:
    return x + (torch.clamp(x, lo, hi) - x).detach()


def ste_trunc(x: torch.Tensor) -> torch.Tensor:
    return x + (torch.trunc(x) - x).detach()


# ---------------------------------------------------------------------------
# Royal-relative coalescing matrices (contract section 6 / discrepancy D3)
# ---------------------------------------------------------------------------


def royal_coalesce_matrices() -> np.ndarray:
    """``A[kb, vsq, offset]``: bucket-uniform mean weights for the royal factor.

    The royal factor is indexed by the exact king square, a real feature row
    only by the king BUCKET, so the coalescence of contract section 6 is not
    exact.  We fold the arithmetic mean of the factor over every post-mirror
    king square that lands in the bucket -- deterministic, reproducible and
    exact in the limit of a uniform king distribution inside the bucket.
    """
    matrices = np.zeros((QF.KING_BUCKETS, FT.NUM_SQ, FT.ROYAL_OFFSETS),
                        dtype=np.float64)
    members = [[] for _ in range(QF.KING_BUCKETS)]
    for vksq in range(FT.NUM_SQ):
        if (vksq & 15) < 8:                 # unreachable after the mirror
            continue
        members[FT.king_bucket(vksq)].append(vksq)
    for kb, kings in enumerate(members):
        if not kings:
            raise AssertionError(f"king bucket {kb} has no post-mirror square")
        share = 1.0 / len(kings)
        for vksq in kings:
            for vsq in range(FT.NUM_SQ):
                matrices[kb, vsq, FT.royal_offset(vsq, vksq)] += share
    return matrices


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class TeraNNUE(nn.Module):
    """FT (104448 -> 256 per perspective) + 8 stacks (pairwise, 16-32-1) + PSQT."""

    def __init__(self, factorized: bool = True,
                 budget: FactorBudget | None = None,
                 psqt_init: str = "zero") -> None:
        super().__init__()
        self.factorized = factorized
        self.budget = budget or FactorBudget()
        self.num_inputs = (FT.NUM_TOTAL_FEATURES if factorized
                           else FT.NUM_REAL_FEATURES)

        # One table: columns [0:L1] are the accumulator, [L1:] the PSQT buckets.
        self.ft = nn.Embedding(self.num_inputs, L1 + OUT_BUCKETS)
        self.ft_bias = nn.Parameter(torch.zeros(L1))
        self.fc0 = nn.Linear(PAIRWISE_OUT, FC0_OUT * OUT_BUCKETS)
        self.fc1 = nn.Linear(FC0_OUT, FC1_OUT * OUT_BUCKETS)
        self.fc2 = nn.Linear(FC1_OUT, 1 * OUT_BUCKETS)

        self._init_weights(psqt_init)
        self.register_buffer("_royal_matrices", torch.empty(0), persistent=False)

    # -- initialisation -----------------------------------------------------

    def _init_weights(self, psqt_init: str) -> None:
        with torch.no_grad():
            self.ft.weight.zero_()
            sigma = 1.0 / math.sqrt(QF.MAX_ACTIVE)      # keep |acc| small
            real = self.ft.weight[:FT.NUM_REAL_FEATURES, :L1]
            real.normal_(0.0, sigma * FT_WEIGHT_LIMIT)
            real.clamp_(-FT_WEIGHT_LIMIT * self.budget.real,
                        FT_WEIGHT_LIMIT * self.budget.real)
            self.ft_bias.zero_()
            for layer, fan_in in ((self.fc0, PAIRWISE_OUT),
                                  (self.fc1, FC0_OUT), (self.fc2, FC1_OUT)):
                bound = 1.0 / math.sqrt(fan_in)
                layer.weight.uniform_(-bound, bound)
                layer.bias.zero_()
            if psqt_init == "material":
                self._init_psqt_material()
            elif psqt_init != "zero":
                raise ValueError(f"unknown psqt_init {psqt_init!r}")

    def _init_psqt_material(self) -> None:
        """Seed the piece-TYPE factor's PSQT with the Zillions piece values.

        Only meaningful for a factorised model; the resulting net evaluates
        exact material at step 0.  Off by default so that a synthetic-data
        canary has to actually learn something.
        """
        if not self.factorized:
            raise ValueError("material psqt init needs the type factor")
        with torch.no_grad():
            for code in range(1, 53):
                value = FT.CODE_VALUE_CP[code] / QF.NNUE2SCORE
                for persp in (FT.WHITE, FT.BLACK):
                    plane = int(FT.PLANE[persp, code])
                    if plane == FT.KING_PLANE:
                        continue
                    own = plane < 25
                    row = FT.FACTOR_TYPE_OFFSET + plane
                    self.ft.weight[row, L1:] = value if own else -value

    # -- contract section 5: clipping ---------------------------------------

    @torch.no_grad()
    def clip_weights_(self) -> None:
        """Apply every range constraint of the contract.  Call after each step."""
        w = self.ft.weight
        if self.factorized:
            spans = [
                (0, FT.NUM_REAL_FEATURES, self.budget.real),
                (FT.FACTOR_A_OFFSET, FT.FACTOR_TYPE_OFFSET,
                 self.budget.piece_square),
                (FT.FACTOR_TYPE_OFFSET, FT.FACTOR_ROYAL_OFFSET,
                 self.budget.piece_type),
                (FT.FACTOR_ROYAL_OFFSET, FT.NUM_TOTAL_FEATURES,
                 self.budget.royal),
            ]
        else:
            spans = [(0, FT.NUM_REAL_FEATURES, 1.0)]
        for lo, hi, share in spans:
            limit = FT_WEIGHT_LIMIT * share
            w[lo:hi, :L1].clamp_(-limit, limit)
            w[lo:hi, L1:].clamp_(-PSQT_LIMIT, PSQT_LIMIT)
        self.ft_bias.clamp_(-FT_BIAS_LIMIT, FT_BIAS_LIMIT)
        for layer in (self.fc0, self.fc1, self.fc2):
            layer.weight.clamp_(-HIDDEN_WEIGHT_LIMIT, HIDDEN_WEIGHT_LIMIT)
            layer.bias.clamp_(-HIDDEN_BIAS_LIMIT, HIDDEN_BIAS_LIMIT)

    # -- contract section 6: coalescence ------------------------------------

    def royal_matrices(self) -> torch.Tensor:
        if self._royal_matrices.numel() == 0:
            mats = torch.from_numpy(royal_coalesce_matrices())
            self._royal_matrices = mats.to(self.ft.weight.device)
        return self._royal_matrices

    def coalesced_table(self) -> torch.Tensor:
        """The 104448 real rows with every train-only factor folded in."""
        w = self.ft.weight
        real = w[:FT.NUM_REAL_FEATURES].to(torch.float64).clone()
        if not self.factorized:
            return real
        dim = real.shape[1]
        n_ps = FT.NUM_PLANES * FT.NUM_SQ
        a = w[FT.FACTOR_A_OFFSET:FT.FACTOR_TYPE_OFFSET].to(torch.float64)
        t = w[FT.FACTOR_TYPE_OFFSET:FT.FACTOR_ROYAL_OFFSET].to(torch.float64)
        royal = w[FT.FACTOR_ROYAL_OFFSET:].to(torch.float64)
        royal = royal.view(FT.NUM_PLANES, FT.ROYAL_OFFSETS, dim)

        # piece-square: identical for the 8 buckets.
        real += a.repeat(QF.KING_BUCKETS, 1)
        # piece-type: broadcast over the 256 squares and the 8 buckets.
        real += t.repeat_interleave(FT.NUM_SQ, dim=0).repeat(QF.KING_BUCKETS, 1)
        # royal-relative: bucket-uniform mean (discrepancy D3).
        mats = self.royal_matrices().to(torch.float64)
        flat = royal.permute(1, 0, 2).reshape(FT.ROYAL_OFFSETS,
                                              FT.NUM_PLANES * dim)
        for kb in range(QF.KING_BUCKETS):
            block = (mats[kb] @ flat).view(FT.NUM_SQ, FT.NUM_PLANES, dim)
            block = block.permute(1, 0, 2).reshape(n_ps, dim)
            real[kb * n_ps:(kb + 1) * n_ps] += block
        return real

    @torch.no_grad()
    def coalesce_factors_(self) -> None:
        """Fold the factors into the real rows in place and drop the virtual
        table.  After this the model is deployment-shaped and ``quant=True``
        can be used."""
        table = self.coalesced_table().to(self.ft.weight.dtype)
        new = nn.Embedding(FT.NUM_REAL_FEATURES, L1 + OUT_BUCKETS)
        new.weight.copy_(table)
        self.ft = new.to(table.device)
        self.num_inputs = FT.NUM_REAL_FEATURES
        self.factorized = False

    # -- quantisation --------------------------------------------------------

    def _quantized_stacks(self) -> dict:
        """Integer-valued float64 tensors for the bias and the 8 stacks."""
        ftb = ste_clamp(ste_round(self.ft_bias.to(torch.float64) * QF.FT_SCALE),
                        -QF.FT_BIAS_MAX, QF.FT_BIAS_MAX)
        out = {"ft_bias": ftb}
        for name, layer, n_out in (("fc0", self.fc0, FC0_OUT),
                                   ("fc1", self.fc1, FC1_OUT),
                                   ("fc2", self.fc2, 1)):
            w = layer.weight.to(torch.float64).view(OUT_BUCKETS, n_out, -1)
            b = layer.bias.to(torch.float64).view(OUT_BUCKETS, n_out)
            out[name + "_w"] = ste_clamp(
                ste_round(w * QF.WEIGHT_SCALE_HIDDEN),
                -QF.HIDDEN_WEIGHT_MAX, QF.HIDDEN_WEIGHT_MAX)
            out[name + "_b"] = ste_round(b * QF.OUTPUT_SCALE)
        return out

    # -- forward -------------------------------------------------------------

    def forward(self, stm_idx, stm_off, nstm_idx, nstm_off, buckets,
                quant: bool = False):
        if quant:
            return self._forward_quant(stm_idx, stm_off, nstm_idx, nstm_off,
                                       buckets)
        return self._forward_float(stm_idx, stm_off, nstm_idx, nstm_off,
                                   buckets)

    def _select(self, flat: torch.Tensor, buckets: torch.Tensor,
                n_out: int) -> torch.Tensor:
        view = flat.view(-1, OUT_BUCKETS, n_out)
        idx = buckets.view(-1, 1, 1).expand(-1, 1, n_out)
        return view.gather(1, idx).squeeze(1)

    def _forward_float(self, stm_idx, stm_off, nstm_idx, nstm_off, buckets):
        w = self.ft.weight
        acc_stm = F.embedding_bag(stm_idx, w, stm_off, mode="sum",
                                  include_last_offset=True)
        acc_nstm = F.embedding_bag(nstm_idx, w, nstm_off, mode="sum",
                                   include_last_offset=True)
        psqt = ((acc_stm[:, L1:] - acc_nstm[:, L1:]) * 0.5
                ).gather(1, buckets.view(-1, 1))

        x = torch.cat((acc_stm[:, :L1] + self.ft_bias,
                       acc_nstm[:, :L1] + self.ft_bias), dim=1)
        x = torch.clamp(x, 0.0, ACT_MAX)
        c = x.split(L1 // 2, dim=1)
        pair = torch.cat((c[0] * c[1], c[2] * c[3]), dim=1)

        h0 = torch.clamp(self._select(self.fc0(pair), buckets, FC0_OUT),
                         0.0, ACT_MAX)
        h1 = torch.clamp(self._select(self.fc1(h0), buckets, FC1_OUT),
                         0.0, ACT_MAX)
        positional = self._select(self.fc2(h1), buckets, 1)
        return positional + psqt

    def _quantized_bag(self, indices, offsets):
        """Segment sum of the QUANTISED rows only (quantise-then-gather is
        identical to gather-then-quantise because the map is element-wise)."""
        rows = self.ft.weight.index_select(0, indices).to(torch.float64)
        ftw = ste_clamp(ste_round(rows[:, :L1] * QF.FT_SCALE),
                        -QF.FT_WEIGHT_MAX, QF.FT_WEIGHT_MAX)
        psqt = ste_round(rows[:, L1:] * QF.OUTPUT_SCALE)
        table = torch.cat((ftw, psqt), dim=1)
        counts = offsets[1:] - offsets[:-1]
        sample = torch.repeat_interleave(
            torch.arange(counts.numel(), device=table.device), counts)
        out = torch.zeros(counts.numel(), table.shape[1],
                          dtype=table.dtype, device=table.device)
        return out.index_add_(0, sample, table)

    def _forward_quant(self, stm_idx, stm_off, nstm_idx, nstm_off, buckets):
        """Exact integer emulation.  Returns (eval_cp, psqt, positional)."""
        if self.factorized:
            raise RuntimeError("call coalesce_factors_() before quant=True")
        q = self._quantized_stacks()
        acc_stm = self._quantized_bag(stm_idx, stm_off)
        acc_nstm = self._quantized_bag(nstm_idx, nstm_off)

        psqt_all = ste_trunc((acc_stm[:, L1:] - acc_nstm[:, L1:]) / 2.0)
        psqt = psqt_all.gather(1, buckets.view(-1, 1))

        ftb = q["ft_bias"]
        x = torch.cat((acc_stm[:, :L1] + ftb, acc_nstm[:, :L1] + ftb), dim=1)
        x = ste_clamp(x, 0.0, float(QF.FT_ACT_MAX))
        c = x.split(L1 // 2, dim=1)
        pair = ste_trunc(torch.cat((c[0] * c[1], c[2] * c[3]), dim=1)
                         / float(1 << QF.PAIRWISE_SHIFT))

        def stack(inp, name, n_out, relu):
            w = q[name + "_w"].index_select(0, buckets)        # (B, out, in)
            b = q[name + "_b"].index_select(0, buckets)        # (B, out)
            s = torch.einsum("boi,bi->bo", w, inp) + b
            if not relu:
                return s
            return ste_clamp(ste_trunc(s / float(QF.WEIGHT_SCALE_HIDDEN)),
                             0.0, float(QF.FT_ACT_MAX))

        h0 = stack(pair, "fc0", FC0_OUT, True)
        h1 = stack(h0, "fc1", FC1_OUT, True)
        positional = stack(h1, "fc2", 1, False)
        eval_cp = ste_trunc((psqt + positional) / float(QF.FV_SCALE))
        return eval_cp, psqt, positional

    # -- diagnostics ---------------------------------------------------------

    @torch.no_grad()
    def max_accumulator(self, indices, offsets) -> int:
        acc = (self._quantized_bag(indices, offsets)[:, :L1]
               + self._quantized_stacks()["ft_bias"])
        return int(acc.abs().max().item())


def count_parameters(model: TeraNNUE) -> dict:
    return {name: int(p.numel()) for name, p in model.named_parameters()}


if __name__ == "__main__":
    net = TeraNNUE(factorized=True)
    total = sum(p.numel() for p in net.parameters())
    print(QF.describe())
    print(f"trainable parameters: {total:,}")
    for name, n in count_parameters(net).items():
        print(f"  {name:<12} {n:>12,}")
