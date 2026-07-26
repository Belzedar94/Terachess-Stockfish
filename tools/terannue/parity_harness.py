#!/usr/bin/env python3
"""Reference dump for the ==0 cp parity gate (contract section 8).

Reads a TNN1 network and a tera-bin file and prints one line per position::

    <FEN> | <eval_cp> | <psqt> | <positional> | <bucket>

The numbers come from ``quantized_forward`` -- pure Python integers -- which
the contract designates as the AUTHORITY.  In F3b the engine's ``eval``
command must reproduce all three integers exactly, on every line, for at
least 1000 stratified positions.  A single centipawn of disagreement blocks
the pipeline; there is no tolerance knob here on purpose.

``--stratify`` selects positions the way the gate demands: every output
bucket represented and at least ``--min-per-type`` positions containing each
of the 26 piece types.  ``--features`` additionally dumps the active feature
rows per perspective, which is what the engine's ``features`` command has to
match when the eval disagrees.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

import features as FT
import quantized_forward as QF
import serialize
import terabin


def stratified_indices(path: Path, wanted: int, min_per_type: int,
                       limit: int | None) -> list[int]:
    """Two-pass selection, exactly what contract section 8 asks for:

    pass 1 covers the RARE piece types first (>= ``min_per_type`` positions
    holding each of the 26 types), pass 2 fills the remaining slots by
    round-robin over the output buckets so all 8 are represented.
    """
    by_bucket: dict[int, list[int]] = {b: [] for b in range(QF.OUT_BUCKETS)}
    by_type: dict[int, list[int]] = {t: [] for t in range(FT.PAWN, FT.KING + 1)}
    info: dict[int, tuple[int, frozenset[int]]] = {}
    for index, record in enumerate(terabin.iter_records(path, limit)):
        bucket = FT.output_bucket(len(record.pieces))
        types = frozenset(int(FT.CODE_TYPE[c]) for c in record.pieces)
        info[index] = (bucket, types)
        by_bucket[bucket].append(index)
        for t in types:
            by_type[t].append(index)

    chosen: set[int] = set()
    order: list[int] = []
    type_have: Counter[int] = Counter()
    bucket_have: Counter[int] = Counter()

    def take(index: int) -> None:
        chosen.add(index)
        order.append(index)
        bucket, types = info[index]
        bucket_have[bucket] += 1
        for t in types:
            type_have[t] += 1

    for t in sorted(by_type, key=lambda x: len(by_type[x])):
        for index in by_type[t]:
            if type_have[t] >= min_per_type or len(order) >= wanted:
                break
            if index not in chosen:
                take(index)

    cursors = {b: 0 for b in range(QF.OUT_BUCKETS)}
    while len(order) < wanted:
        progressed = False
        for bucket in sorted(range(QF.OUT_BUCKETS),
                             key=lambda b: bucket_have[b]):
            pool = by_bucket[bucket]
            while cursors[bucket] < len(pool) and pool[cursors[bucket]] in chosen:
                cursors[bucket] += 1
            if cursors[bucket] >= len(pool):
                continue
            take(pool[cursors[bucket]])
            progressed = True
            if len(order) >= wanted:
                break
        if not progressed:
            break

    missing_types = [t for t in range(FT.PAWN, FT.KING + 1)
                     if type_have[t] < min_per_type]
    if missing_types:
        names = ", ".join(f"{FT.PIECE_TYPE_ORDER[t - FT.PAWN]}"
                          f"({type_have[t]})" for t in missing_types)
        print(f"parity_harness: WARNING: under {min_per_type} positions for: "
              f"{names}", file=sys.stderr)
    absent = sorted(b for b in range(QF.OUT_BUCKETS)
                    if not bucket_have[b] and by_bucket[b])
    if absent:
        print(f"parity_harness: WARNING: output buckets absent: {absent}",
              file=sys.stderr)
    empty = sorted(b for b in range(QF.OUT_BUCKETS) if not by_bucket[b])
    if empty:
        print(f"parity_harness: WARNING: no data at all for buckets {empty}",
              file=sys.stderr)
    return sorted(order)


def evaluate_record(net: QF.QuantizedNet, record: terabin.Record) -> dict:
    view = FT.PositionView.from_record(record)
    stm = FT.real_indices(view, 0).tolist()
    nstm = FT.real_indices(view, 1).tolist()
    out = QF.evaluate(net, stm, nstm, view.bucket)
    out["stm_rows"] = stm
    out["nstm_rows"] = nstm
    return out


def run(net_path: Path, data_path: Path, count: int, stratify: bool,
        min_per_type: int, scan_limit: int | None, dump_features: bool,
        out_stream=sys.stdout) -> list[dict]:
    net = serialize.load_tnn1(net_path)
    if stratify:
        wanted = set(stratified_indices(data_path, count, min_per_type,
                                        scan_limit))
    else:
        wanted = set(range(count))
    rows: list[dict] = []
    for index, record in enumerate(terabin.iter_records(data_path)):
        if index not in wanted:
            continue
        result = evaluate_record(net, record)
        fen = terabin.to_fen(record)
        print(f"{fen} | {result['eval_cp']} | {result['psqt']} | "
              f"{result['positional']} | {result['bucket']}", file=out_stream)
        if dump_features:
            print("  stm  " + " ".join(map(str, result["stm_rows"])),
                  file=out_stream)
            print("  nstm " + " ".join(map(str, result["nstm_rows"])),
                  file=out_stream)
        result["fen"] = fen
        result["label_cp"] = record.score
        rows.append(result)
        if len(rows) >= count:
            break
    return rows


def summarise(rows: list[dict], out_stream=sys.stderr) -> dict:
    evals = np.array([r["eval_cp"] for r in rows], dtype=np.float64)
    labels = np.array([r["label_cp"] for r in rows], dtype=np.float64)
    acc_max = max(r["acc_max"] for r in rows)
    buckets = Counter(r["bucket"] for r in rows)
    corr = float("nan")
    if evals.size > 1 and evals.std() > 0 and labels.std() > 0:
        corr = float(np.corrcoef(evals, labels)[0, 1])
    print(f"positions          {len(rows)}", file=out_stream)
    print(f"eval_cp  min/mean/max  {evals.min():.0f} / {evals.mean():.1f} / "
          f"{evals.max():.0f}", file=out_stream)
    print(f"label_cp min/mean/max  {labels.min():.0f} / {labels.mean():.1f} / "
          f"{labels.max():.0f}", file=out_stream)
    print(f"pearson(eval, label)   {corr:.4f}", file=out_stream)
    print(f"max |accumulator|      {acc_max} of 32767 "
          f"(bound {QF.ACC_BOUND})", file=out_stream)
    print("buckets                "
          + ", ".join(f"{b}:{buckets[b]}" for b in sorted(buckets)),
          file=out_stream)
    return {"n": len(rows), "pearson": corr, "acc_max": acc_max,
            "buckets": dict(buckets)}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("net", type=Path, help="TNN1 network")
    parser.add_argument("data", type=Path, help="tera-bin positions")
    parser.add_argument("-n", "--count", type=int, default=1000)
    parser.add_argument("--stratify", action="store_true",
                        help="cover all 8 output buckets and all 26 types")
    parser.add_argument("--min-per-type", type=int, default=50)
    parser.add_argument("--scan-limit", type=int, default=None,
                        help="records scanned while stratifying")
    parser.add_argument("--features", action="store_true",
                        help="also dump the active rows per perspective")
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    stream = open(args.out, "w", encoding="ascii") if args.out else sys.stdout
    try:
        rows = run(args.net, args.data, args.count, args.stratify,
                   args.min_per_type, args.scan_limit, args.features, stream)
    finally:
        if args.out:
            stream.close()
    if not rows:
        print("parity_harness: no positions evaluated", file=sys.stderr)
        return 2
    if not args.quiet:
        summarise(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
