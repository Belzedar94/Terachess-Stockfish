#!/usr/bin/env python3
"""Mandatory verifications (a)-(d) of the terannue trainer.

  a  feature indexing, including three indices computed BY HAND from the
     contract and the 128-active-rows invariant of the start position;
  b  STE coherence: ``model(quant=True)`` and ``quantized_forward`` return the
     SAME integers on >= 100 positions -- the heart of the ==0 cp gate;
  c  overflow: 128 active features with every weight at the +-127 limit and
     the bias at +-8191 must stay inside i16, and we report the number reached;
  d  dataset: deterministic DataLoader over two epochs and first-batch
     features identical to ``features.py`` on the same records.

Verification (e), the end-to-end canary, lives in ``canary.py`` because it
needs a GPU and several minutes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch

import dataset as DS
import features as FT
import model as M
import quantized_forward as QF
import serialize
import terabin


def banner(text: str) -> None:
    print(f"\n=== {text} " + "=" * max(0, 66 - len(text)))


# ---------------------------------------------------------------------------
# (a) features
# ---------------------------------------------------------------------------


def test_features() -> dict:
    banner("(a) feature indexing")
    names = FT.self_test(verbose=True)
    view = FT.PositionView.from_fen(terabin.START_FEN)
    counts = {p: int(FT.real_indices(view, p).size) for p in (0, 1)}
    print(f"  checks passed                     : {', '.join(names)}")
    return {"active_stm": counts[0], "active_nstm": counts[1],
            "checks": names}


# ---------------------------------------------------------------------------
# (b) STE coherence
# ---------------------------------------------------------------------------


def _quantized_random_weights(net: M.TeraNNUE, seed: int) -> None:
    """Random weights that already sit exactly on the inference grid."""
    gen = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        w = net.ft.weight
        w[:, :M.L1] = torch.randint(-QF.FT_WEIGHT_MAX, QF.FT_WEIGHT_MAX + 1,
                                    w[:, :M.L1].shape, generator=gen,
                                    dtype=torch.float32) / QF.FT_SCALE
        w[:, M.L1:] = torch.randint(-4000, 4001, w[:, M.L1:].shape,
                                    generator=gen,
                                    dtype=torch.float32) / QF.OUTPUT_SCALE
        net.ft_bias.copy_(torch.randint(-QF.FT_BIAS_MAX, QF.FT_BIAS_MAX + 1,
                                        net.ft_bias.shape, generator=gen,
                                        dtype=torch.float32) / QF.FT_SCALE)
        for layer in (net.fc0, net.fc1, net.fc2):
            layer.weight.copy_(
                torch.randint(-QF.HIDDEN_WEIGHT_MAX, QF.HIDDEN_WEIGHT_MAX + 1,
                              layer.weight.shape, generator=gen,
                              dtype=torch.float32) / QF.WEIGHT_SCALE_HIDDEN)
            layer.bias.copy_(torch.randint(-8192, 8193, layer.bias.shape,
                                           generator=gen, dtype=torch.float32)
                             / QF.OUTPUT_SCALE)


def _off_grid_random_weights(net: M.TeraNNUE, seed: int) -> None:
    """Random weights that are NOT on the grid, inside the clip budget."""
    gen = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in net.parameters():
            p.uniform_(-1.0, 1.0, generator=gen)
        net.ft.weight[:, M.L1:] *= 0.5
    net.clip_weights_()


def _compare(net: M.TeraNNUE, qnet: QF.QuantizedNet,
             records: list[terabin.Record], label: str) -> dict:
    views = [FT.PositionView.from_record(r) for r in records]
    stm = [FT.real_indices(v, 0) for v in views]
    nstm = [FT.real_indices(v, 1) for v in views]
    buckets = [v.bucket for v in views]

    stm_off = np.concatenate(([0], np.cumsum([a.size for a in stm])))
    nstm_off = np.concatenate(([0], np.cumsum([a.size for a in nstm])))
    args = (torch.from_numpy(np.concatenate(stm)),
            torch.from_numpy(stm_off.astype(np.int64)),
            torch.from_numpy(np.concatenate(nstm)),
            torch.from_numpy(nstm_off.astype(np.int64)),
            torch.tensor(buckets, dtype=torch.int64))
    t0 = time.time()
    with torch.no_grad():
        eval_cp, psqt, positional = net(*args, quant=True)
    torch_seconds = time.time() - t0

    t0 = time.time()
    mismatches = []
    acc_max = 0
    for i, view in enumerate(views):
        ref = QF.evaluate(qnet, stm[i].tolist(), nstm[i].tolist(), view.bucket)
        acc_max = max(acc_max, ref["acc_max"])
        got = (int(eval_cp[i, 0]), int(psqt[i, 0]), int(positional[i, 0]))
        want = (ref["eval_cp"], ref["psqt"], ref["positional"])
        if got != want:
            mismatches.append((i, got, want))
    ref_seconds = time.time() - t0

    print(f"  {label}: {len(records)} positions, "
          f"{len(mismatches)} mismatch(es)")
    print(f"    torch fake-quant {torch_seconds:.2f}s | "
          f"pure-int reference {ref_seconds:.2f}s | "
          f"max |acc| {acc_max}")
    if mismatches:
        for i, got, want in mismatches[:5]:
            print(f"    position {i}: torch {got} != reference {want}")
        raise AssertionError(f"{label}: {len(mismatches)} STE mismatches")
    return {"positions": len(records), "mismatches": 0, "acc_max": acc_max,
            "torch_seconds": torch_seconds, "ref_seconds": ref_seconds}


def test_ste(data: Path, tmpdir: Path, positions: int = 120) -> dict:
    banner("(b) STE coherence: model(quant=True) == quantized_forward")
    records = list(terabin.iter_records(data, limit=positions))
    assert len(records) >= 100, f"need >= 100 positions, got {len(records)}"

    out = {}
    # b1 -- weights already on the quantisation grid, no factorizer.
    net = M.TeraNNUE(factorized=False)
    _quantized_random_weights(net, 1234)
    serialize.save_model(net, tmpdir / "grid.tnn1", verbose=False)
    out["on_grid"] = _compare(net, serialize.load_tnn1(tmpdir / "grid.tnn1"),
                              records, "on-grid weights")

    # b2 -- off-grid weights through the full factorizer + coalescence path.
    net2 = M.TeraNNUE(factorized=True)
    _off_grid_random_weights(net2, 99)
    net2.coalesce_factors_()
    serialize.save_model(net2, tmpdir / "coalesced.tnn1", verbose=False)
    out["coalesced"] = _compare(net2,
                                serialize.load_tnn1(tmpdir / "coalesced.tnn1"),
                                records, "off-grid + coalesced factors")
    return out


# ---------------------------------------------------------------------------
# (c) overflow
# ---------------------------------------------------------------------------


def test_overflow() -> dict:
    banner("(c) accumulator overflow bound")
    view = FT.PositionView.from_fen(terabin.START_FEN)
    rows = FT.real_indices(view, 0).tolist()
    assert len(rows) == QF.MAX_ACTIVE == 128

    results = {}
    for sign in (+1, -1):
        net = QF.QuantizedNet.zeros()
        net.ft_weights[:] = sign * QF.FT_WEIGHT_MAX
        net.ft_bias[:] = sign * QF.FT_BIAS_MAX
        acc = QF.accumulate(net, rows)
        worst = max(abs(v) for v in acc)
        expected = QF.MAX_ACTIVE * QF.FT_WEIGHT_MAX + QF.FT_BIAS_MAX
        assert worst == expected, (worst, expected)
        assert worst < 32768, "i16 overflow"
        assert all(v == sign * expected for v in acc)
        results["pos" if sign > 0 else "neg"] = worst
        print(f"  all weights {sign * QF.FT_WEIGHT_MAX:+4d}, bias "
              f"{sign * QF.FT_BIAS_MAX:+6d} -> |acc| = {worst} "
              f"(bound {QF.ACC_BOUND}, i16 max 32767, "
              f"margin {32767 / worst:.3f}x)")

    # Same thing through the torch fake-quant path, on a real 128-piece batch.
    net_t = M.TeraNNUE(factorized=False)
    with torch.no_grad():
        net_t.ft.weight[:, :M.L1] = QF.FT_WEIGHT_MAX / QF.FT_SCALE
        net_t.ft_bias.fill_(QF.FT_BIAS_MAX / QF.FT_SCALE)
    idx = torch.tensor(rows, dtype=torch.int64)
    off = torch.tensor([0, len(rows)], dtype=torch.int64)
    torch_worst = net_t.max_accumulator(idx, off)
    assert torch_worst == results["pos"], (torch_worst, results["pos"])
    print(f"  torch fake-quant path             : |acc| = {torch_worst}")

    # The pairwise product must fit in u8 (contract section 5).
    pair_max = (QF.FT_ACT_MAX * QF.FT_ACT_MAX) >> QF.PAIRWISE_SHIFT
    assert pair_max == 126 and pair_max <= 255
    print(f"  pairwise (127*127)>>7             : {pair_max} (u8 ok)")
    results["pairwise_max"] = pair_max
    results["i16_margin"] = 32767 / results["pos"]
    return results


# ---------------------------------------------------------------------------
# (d) dataset
# ---------------------------------------------------------------------------


def _batches(ds: DS.TeraBinDataset, epoch: int, workers: int, limit: int):
    ds.set_epoch(epoch)
    loader = DS.make_loader(ds, num_workers=workers, pin_memory=False)
    out = []
    for i, batch in enumerate(loader):
        if i >= limit:
            break
        out.append(batch)
    return out


def _fingerprint(batch: DS.Batch) -> tuple:
    return (int(batch.stm_idx.sum()), int(batch.stm_idx.numel()),
            int(batch.nstm_idx.sum()), int(batch.buckets.sum()),
            float(batch.score.sum()), float(batch.result.sum()),
            int(torch.tensor([hash(tuple(batch.stm_off.tolist()))])[0]))


def test_dataset(data: Path, workers: int = 2) -> dict:
    banner("(d) dataset determinism and feature agreement")
    total = DS.record_count(data)
    ds = DS.TeraBinDataset(data, batch_size=256, split="all",
                           val_fraction=0.0, seed=4242, shuffle_buffer=1024,
                           factorized=True)

    runs = []
    for attempt in range(2):
        per_epoch = []
        for epoch in (0, 1):
            batches = _batches(ds, epoch, workers, limit=6)
            per_epoch.append([_fingerprint(b) for b in batches])
        runs.append(per_epoch)
    assert runs[0] == runs[1], "DataLoader is not deterministic across runs"
    assert runs[0][0] != runs[0][1], "epoch 1 reproduced epoch 0 exactly"
    print(f"  {total:,} records, 2 epochs x 6 batches x 2 runs: "
          "byte-identical across runs, different across epochs")

    # First batch of a single-worker pass must match features.py exactly.
    ds_single = DS.TeraBinDataset(data, batch_size=64, split="all",
                                  val_fraction=0.0, seed=4242,
                                  shuffle_buffer=1, factorized=True)
    ds_single.set_epoch(0)
    first = next(iter(DS.make_loader(ds_single, num_workers=0,
                                     pin_memory=False)))
    records = list(terabin.iter_records(data, limit=len(first)))
    expect = DS.collate(records, factorized=True)
    assert torch.equal(first.stm_idx, expect.stm_idx)
    assert torch.equal(first.stm_off, expect.stm_off)
    assert torch.equal(first.nstm_idx, expect.nstm_idx)
    assert torch.equal(first.nstm_off, expect.nstm_off)
    assert torch.equal(first.buckets, expect.buckets)
    assert torch.equal(first.score, expect.score)

    checked = 0
    for i, record in enumerate(records):
        stm, nstm, bucket = FT.record_features(record, factorized=True)
        lo, hi = int(first.stm_off[i]), int(first.stm_off[i + 1])
        assert np.array_equal(first.stm_idx[lo:hi].numpy(), stm)
        lo, hi = int(first.nstm_off[i]), int(first.nstm_off[i + 1])
        assert np.array_equal(first.nstm_idx[lo:hi].numpy(), nstm)
        assert int(first.buckets[i]) == bucket
        checked += 1
    print(f"  first batch vs features.py        : {checked} records identical")

    # Lineage split really is disjoint.
    train = DS.TeraBinDataset(data, batch_size=128, split="train",
                              val_fraction=0.2, seed=7, shuffle_buffer=1)
    val = DS.TeraBinDataset(data, batch_size=128, split="val",
                            val_fraction=0.2, seed=7, shuffle_buffer=1)
    games = {gid for gid, _ in DS.stream_games(data, 0, total)}
    tr = {g for g in games if not DS.game_is_validation(g, 7, 0.2)}
    va = games - tr
    assert not (tr & va) and va
    n_tr = sum(len(b) for b in _batches(train, 0, 0, limit=10**9))
    n_va = sum(len(b) for b in _batches(val, 0, 0, limit=10**9))
    print(f"  lineage split                     : {len(tr)} train games "
          f"({n_tr:,} pos) / {len(va)} val games ({n_va:,} pos), disjoint")
    return {"records": total, "batch_records_checked": checked,
            "train_games": len(tr), "val_games": len(va),
            "train_positions": n_tr, "val_positions": n_va}


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data", type=Path, help="small tera-bin file")
    parser.add_argument("--tmp", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--skip", nargs="*", default=[])
    args = parser.parse_args(argv)
    args.tmp.mkdir(parents=True, exist_ok=True)

    report = {}
    if "a" not in args.skip:
        report["a"] = test_features()
    if "b" not in args.skip:
        report["b"] = test_ste(args.data, args.tmp)
    if "c" not in args.skip:
        report["c"] = test_overflow()
    if "d" not in args.skip:
        report["d"] = test_dataset(args.data, args.workers)

    banner("summary")
    for key, value in report.items():
        print(f"  {key}: {value}")
    print("\nALL SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
