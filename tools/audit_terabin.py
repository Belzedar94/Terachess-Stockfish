#!/usr/bin/env python3
"""Stream a tera-bin v1 file: structural validation plus distribution report.

Structural failures (bad header, size mismatch, any record that fails the
strict decoder) exit with status 2.  Distribution findings are warnings:
they always print, and ``--strict`` turns them into exit status 1.

Phase buckets (piece-count based, 128 pieces at the start position):
  0 opening (97-128 pieces)   1 middle (65-96)
  2 late    (33-64)           3 endgame (2-32)
``--min-phase-percent X`` warns for every phase below X% of the audited
records (default 0 = disabled, suitable for opening-only pilots).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import terabin


class AuditError(RuntimeError):
    """A structurally invalid tera-bin file."""


# Zillions piece values (TERACHESS_SPEC.md section 8), x100 -> centipawns.
PIECE_VALUE_CP = {
    "p": 50, "z": 170, "m": 180, "e": 200, "n": 200, "w": 220, "i": 230,
    "t": 240, "v": 330, "b": 340, "j": 410, "y": 440, "c": 500, "r": 500,
    "x": 530, "f": 540, "d": 580, "l": 600, "s": 600, "u": 610, "h": 690,
    "o": 820, "q": 830, "g": 840, "a": 1020, "k": 0,
}
CODE_VALUE_CP = [0] * 53
for _code in range(1, 53):
    CODE_VALUE_CP[_code] = PIECE_VALUE_CP[chr(97 + (_code - 1) % 26)]
START_MATERIAL_CP = 46040           # both sides, start position

RESULT_LABELS = {2: "win", 1: "draw", 0: "loss", 3: "no result"}
PHASE_LABELS = ("0 opening (97-128 pieces)", "1 middle (65-96)",
                "2 late (33-64)", "3 endgame (2-32)")

MATERIAL_RANGES = (
    ("0-5999", None, 5999),
    ("6000-11999", 6000, 11999),
    ("12000-17999", 12000, 17999),
    ("18000-23999", 18000, 23999),
    ("24000-29999", 24000, 29999),
    ("30000-35999", 30000, 35999),
    ("36000-41999", 36000, 41999),
    ("42000+", 42000, None),
)

PIECE_COUNT_RANGES = (
    ("2-16", None, 16),
    ("17-32", 17, 32),
    ("33-48", 33, 48),
    ("49-64", 49, 64),
    ("65-80", 65, 80),
    ("81-96", 81, 96),
    ("97-112", 97, 112),
    ("113-128", 113, None),
)

EVAL_RANGES = (
    ("< -10000", None, -10001),
    ("-10000..-2001", -10000, -2001),
    ("-2000..-1001", -2000, -1001),
    ("-1000..-501", -1000, -501),
    ("-500..-201", -500, -201),
    ("-200..-101", -200, -101),
    ("-100..-1", -100, -1),
    ("0", 0, 0),
    ("1..100", 1, 100),
    ("101..200", 101, 200),
    ("201..500", 201, 500),
    ("501..1000", 501, 1000),
    ("1001..2000", 1001, 2000),
    ("2001..10000", 2001, 10000),
    ("> 10000", 10001, None),
)

PLY_RANGES = (
    ("0-4", 0, 4),
    ("5-9", 5, 9),
    ("10-19", 10, 19),
    ("20-39", 20, 39),
    ("40-79", 40, 79),
    ("80-119", 80, 119),
    ("120-199", 120, 199),
    ("200-399", 200, 399),
    ("400+", 400, None),
)

RECORDS_PER_GAME_RANGES = (
    ("1-4", None, 4),
    ("5-9", 5, 9),
    ("10-19", 10, 19),
    ("20-39", 20, 39),
    ("40-79", 40, 79),
    ("80-159", 80, 159),
    ("160+", 160, None),
)


def percent(value: int, total: int) -> float:
    return 100.0 * value / total if total else 0.0


def range_bucket(value: int,
                 ranges: Iterable[tuple[str, int | None, int | None]]) -> str:
    for label, lower, upper in ranges:
        if (lower is None or value >= lower) and (upper is None or value <= upper):
            return label
    raise AssertionError(f"no histogram range for {value}")


def print_counts(counter: Counter, keys: Iterable, total: int,
                 labels: dict | None = None) -> None:
    for key in keys:
        count = counter[key]
        label = labels[key] if labels else str(key)
        print(f"  {label:<26} {count:>12,}  {percent(count, total):>7.3f}%")


def audit(path: Path, limit: int | None, min_phase_percent: float) -> list[str]:
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise AuditError(f"cannot stat {path}: {exc}") from exc

    warnings: list[str] = []
    result_counts: Counter[int] = Counter()
    stm_counts: Counter[int] = Counter()
    phase_counts: Counter[int] = Counter()
    material_counts: Counter[str] = Counter()
    piece_count_counts: Counter[str] = Counter()
    eval_counts: Counter[str] = Counter()
    ply_counts: Counter[str] = Counter()
    game_histogram: Counter[int] = Counter()
    current_game_records = 0
    previous_ply: int | None = None
    min_pieces, max_pieces, piece_total = 256, 0, 0

    try:
        file = path.open("rb")
    except OSError as exc:
        raise AuditError(f"cannot open {path}: {exc}") from exc

    with file:
        try:
            count, source_count, flags = terabin.read_header(file)
        except ValueError as exc:
            raise AuditError(str(exc)) from exc
        expected_size = terabin.HEADER_SIZE + count * terabin.RECORD_SIZE
        if file_size != expected_size:
            raise AuditError(
                f"file size mismatch: header declares {count:,} records "
                f"({expected_size:,} bytes), file has {file_size:,} bytes")
        if count == 0:
            raise AuditError("tera-bin file contains zero records")
        audited = count if limit is None else min(limit, count)

        for index in range(audited):
            raw = file.read(terabin.RECORD_SIZE)
            if len(raw) != terabin.RECORD_SIZE:
                raise AuditError(f"truncated payload at record {index:,}")
            try:
                record = terabin.unpack(raw)
            except ValueError as exc:
                raise AuditError(f"invalid record {index:,}: {exc}") from exc

            result_counts[record.result] += 1
            stm_counts[record.stm] += 1

            pieces = len(record.pieces)
            min_pieces = min(min_pieces, pieces)
            max_pieces = max(max_pieces, pieces)
            piece_total += pieces
            piece_count_counts[range_bucket(pieces, PIECE_COUNT_RANGES)] += 1
            phase_counts[max(0, min(3, (128 - pieces) // 32))] += 1

            material = sum(CODE_VALUE_CP[code] for code in record.pieces)
            material_counts[range_bucket(material, MATERIAL_RANGES)] += 1

            eval_counts[range_bucket(record.score, EVAL_RANGES)] += 1
            ply_counts[range_bucket(record.ply, PLY_RANGES)] += 1

            if previous_ply is not None and record.ply <= previous_ply:
                game_histogram[current_game_records] += 1
                current_game_records = 0
            current_game_records += 1
            previous_ply = record.ply

        if current_game_records:
            game_histogram[current_game_records] += 1

    print(f"tera-bin v1 audit: {path}")
    print("\nHeader")
    print(f"  records                   {count:,}")
    print(f"  source positions          {source_count:,}")
    if source_count:
        print(f"  write rate                {percent(count, source_count):.3f}%")
    else:
        print("  write rate                n/a (source_count=0)")
    print(f"  flags                     {flags}")
    print(f"  file bytes                {file_size:,} "
          f"({terabin.HEADER_SIZE} + {count:,} x {terabin.RECORD_SIZE})")
    if audited < count:
        print(f"  audited records           {audited:,} (--limit; "
              "histograms are partial, size check is complete)")

    print("\nWDL targets (side-to-move perspective)")
    print_counts(result_counts, (2, 1, 0, 3), audited, RESULT_LABELS)

    print("\nSide to move")
    print_counts(stm_counts, (0, 1), audited, {0: "white", 1: "black"})

    print("\nPhase: piece-count buckets")
    print_counts(phase_counts, range(4), audited,
                 dict(enumerate(PHASE_LABELS)))
    if min_phase_percent > 0:
        for phase in range(4):
            value = percent(phase_counts[phase], audited)
            if value < min_phase_percent:
                warnings.append(
                    f"phase {phase} is {value:.3f}% "
                    f"(< {min_phase_percent:.1f}%)")

    print("\nTotal material, both sides "
          f"(Zillions cp, start = {START_MATERIAL_CP:,})")
    print_counts(material_counts, [label for label, _, _ in MATERIAL_RANGES],
                 audited)

    print("\nPiece count")
    print_counts(piece_count_counts,
                 [label for label, _, _ in PIECE_COUNT_RANGES], audited)
    print(f"  min / mean / max          {min_pieces} / "
          f"{piece_total / audited:.2f} / {max_pieces}")

    print("\nEvaluation histogram (cp, side to move; mate scores excluded "
          "by the format)")
    print_counts(eval_counts, [label for label, _, _ in EVAL_RANGES], audited)

    print("\nGame-ply histogram")
    print_counts(ply_counts, [label for label, _, _ in PLY_RANGES], audited)

    games = sum(game_histogram.values())
    print("\nRecords per game (approximate: inferred from ply resets)")
    binned: Counter[str] = Counter()
    for records, n_games in game_histogram.items():
        binned[range_bucket(records, RECORDS_PER_GAME_RANGES)] += n_games
    print_counts(binned, [label for label, _, _ in RECORDS_PER_GAME_RANGES],
                 games)
    if games:
        weighted = sum(r * g for r, g in game_histogram.items())
        print(f"  games                     {games:,}")
        print(f"  mean records/game         {weighted / games:.3f}")
        print(f"  min / max                 {min(game_histogram)} / "
              f"{max(game_histogram)}")

    print("\nWarnings")
    if warnings:
        for warning in warnings:
            print(f"  WARNING: {warning}")
    else:
        print("  none")
    print(f"\nSummary: {audited:,} records audited, {games:,} games inferred, "
          f"{len(warnings)} warning(s)")
    return warnings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a tera-bin v1 (TC01) training-data file")
    parser.add_argument("terabin_file", type=Path, help="tera-bin file to audit")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="audit only the first N records (pilots)")
    parser.add_argument("--min-phase-percent", type=float, default=0.0,
                        metavar="X",
                        help="warn when a phase bucket holds < X%% of the "
                             "records (default 0 = disabled)")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when any warning is emitted")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        print("audit_terabin.py: error: --limit must be positive",
              file=sys.stderr)
        return 2
    try:
        warnings = audit(args.terabin_file, args.limit, args.min_phase_percent)
    except AuditError as exc:
        print(f"audit_terabin.py: error: {exc}", file=sys.stderr)
        return 2
    return 1 if args.strict and warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
