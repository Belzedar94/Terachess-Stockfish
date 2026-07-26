#!/usr/bin/env python3
"""Double round-trip engine<->python over a real datagen output.

The engine's `--debug-sample N` sidecar (`<out>.debug.txt`) holds one
``FEN | score | result`` line per record, in merged-file order, for the first
N records of `<out>`.  For every such line this test asserts, against the
Python format authority (``tools/terabin.py``, docs/tera-bin-v1.md):

  1. ``to_fen(unpack(raw)) == `` the FEN the engine printed (exact string),
  2. the record's score and POV-stm result equal the sidecar's,
  3. ``pack(unpack(raw)) == raw`` byte for byte,
  4. the header is well formed and ``filesize == 32 + 144 * count``.

Usage: ``python tools/tests/test_datagen_roundtrip.py <out.bin> [--limit N]``
Exit 0 on success, 1 on any mismatch, 2 on a structural/setup failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import terabin

FIELD_NAMES = ("board", "turn", "king-jump rights", "ep", "halfmove",
               "fullmove")


def parse_debug_line(line: str) -> tuple[str, int, int]:
    """Split ``FEN | score | result``; the FEN itself never contains '|'."""
    parts = line.split(" | ")
    if len(parts) != 3:
        raise ValueError(f"malformed debug line: {line!r}")
    return parts[0], int(parts[1]), int(parts[2])


def check(path: Path, limit: int | None) -> int:
    debug_path = path.with_name(path.name + ".debug.txt")
    if not debug_path.is_file():
        print(f"error: missing debug sidecar {debug_path}", file=sys.stderr)
        return 2

    lines = [line for line in debug_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if not lines:
        print(f"error: empty debug sidecar {debug_path}", file=sys.stderr)
        return 2
    if limit is not None:
        lines = lines[:limit]

    file_size = path.stat().st_size
    errors: list[str] = []
    with path.open("rb") as handle:
        try:
            count, source_count, flags = terabin.read_header(handle)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        expected = terabin.HEADER_SIZE + count * terabin.RECORD_SIZE
        if file_size != expected:
            print(f"error: file size {file_size:,} != {expected:,} "
                  f"(32 + 144 x {count:,})", file=sys.stderr)
            return 2
        if flags:
            print(f"error: non-zero header flags {flags}", file=sys.stderr)
            return 2
        if len(lines) > count:
            print(f"error: {len(lines):,} debug lines but only {count:,} "
                  "records", file=sys.stderr)
            return 2

        for index, line in enumerate(lines):
            try:
                fen, score, result = parse_debug_line(line)
            except ValueError as exc:
                errors.append(f"record {index}: {exc}")
                continue

            raw = handle.read(terabin.RECORD_SIZE)
            if len(raw) != terabin.RECORD_SIZE:
                errors.append(f"record {index}: truncated payload")
                break

            try:
                record = terabin.unpack(raw)
            except ValueError as exc:
                errors.append(f"record {index}: strict decoder rejected: {exc}")
                continue

            if terabin.pack(record) != raw:
                errors.append(f"record {index}: pack(unpack(raw)) != raw")

            got_fen = terabin.to_fen(record)
            if got_fen != fen:
                engine_fields = fen.split(" ")
                python_fields = got_fen.split(" ")
                if len(engine_fields) != len(python_fields):
                    errors.append(f"record {index}: FEN field count "
                                  f"{len(engine_fields)} vs {len(python_fields)}")
                for name, x, y in zip(FIELD_NAMES, engine_fields, python_fields):
                    if x != y:
                        errors.append(f"record {index}: field {name}: "
                                      f"engine={x!r} terabin={y!r}")

            if record.score != score:
                errors.append(f"record {index}: score engine={score} "
                              f"terabin={record.score}")
            if record.result != result:
                errors.append(f"record {index}: result engine={result} "
                              f"terabin={record.result}")
            if not 0 <= record.result <= 2:
                errors.append(f"record {index}: unpatched result "
                              f"{record.result} (placeholder survived)")
            if abs(record.score) >= 32000:
                errors.append(f"record {index}: mate-magnitude score "
                              f"{record.score} must never be written")

    if errors:
        for error in errors[:40]:
            print(f"FAIL {error}")
        if len(errors) > 40:
            print(f"... and {len(errors) - 40} more")
        print(f"datagen round-trip FAILED: {len(errors)} error(s) over "
              f"{len(lines)} record(s)")
        return 1

    print(f"datagen round-trip OK: {len(lines)}/{len(lines)} records "
          f"(engine FEN == terabin to_fen, score/result equal, "
          f"pack(unpack(raw)) == raw); header {count:,} records / "
          f"{source_count:,} source positions, {file_size:,} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Double round-trip a datagen tera-bin file against "
                    "tools/terabin.py using its --debug-sample sidecar")
    parser.add_argument("terabin_file", type=Path)
    parser.add_argument("--limit", type=int, metavar="N",
                        help="check only the first N debug lines")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        print("error: --limit must be positive", file=sys.stderr)
        return 2
    if not args.terabin_file.is_file():
        print(f"error: no such file {args.terabin_file}", file=sys.stderr)
        return 2
    return check(args.terabin_file, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
