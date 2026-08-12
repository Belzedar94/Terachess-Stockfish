#!/usr/bin/env python3
"""Check the canonical byte round-trip of every record in a tera-bin file.

Unlike ``tools/tests/test_datagen_roundtrip.py``, this probe does not need the
engine debug sidecar.  It is therefore suitable for the opaque ``.bz2`` blobs
published by OpenBench after decompression.  It checks the complete header and
file size, decodes every record strictly, and requires
``pack(unpack(raw)) == raw`` byte for byte.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import terabin


class RoundTripError(RuntimeError):
    """A structural or canonicalization failure in a tera-bin file."""


def check(path: Path, expected_records: int | None) -> tuple[int, int, int]:
    try:
        file_size = path.stat().st_size
    except OSError as error:
        raise RoundTripError(f"cannot stat {path}: {error}") from error

    try:
        handle = path.open("rb")
    except OSError as error:
        raise RoundTripError(f"cannot open {path}: {error}") from error

    with handle:
        try:
            count, source_count, flags = terabin.read_header(handle)
        except ValueError as error:
            raise RoundTripError(str(error)) from error

        if expected_records is not None and count != expected_records:
            raise RoundTripError(
                f"header declares {count:,} records; expected "
                f"{expected_records:,}"
            )
        if flags != 0:
            raise RoundTripError(f"non-zero header flags {flags}")

        expected_size = terabin.HEADER_SIZE + count * terabin.RECORD_SIZE
        if file_size != expected_size:
            raise RoundTripError(
                f"file size {file_size:,} != {expected_size:,} "
                f"({terabin.HEADER_SIZE} + {count:,} x "
                f"{terabin.RECORD_SIZE})"
            )

        for index in range(count):
            raw = handle.read(terabin.RECORD_SIZE)
            if len(raw) != terabin.RECORD_SIZE:
                raise RoundTripError(f"truncated payload at record {index:,}")
            try:
                record = terabin.unpack(raw)
                repacked = terabin.pack(record)
            except (TypeError, ValueError) as error:
                raise RoundTripError(
                    f"record {index:,} rejected: {error}"
                ) from error
            if repacked != raw:
                raise RoundTripError(
                    f"record {index:,}: pack(unpack(raw)) != raw"
                )

        if handle.read(1):
            raise RoundTripError("trailing bytes after declared records")

    return count, source_count, file_size


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check pack(unpack(raw)) == raw for a complete tera-bin"
    )
    parser.add_argument("terabin_file", type=Path)
    parser.add_argument(
        "--expected-records",
        type=int,
        metavar="N",
        help="fail unless the header declares exactly N records",
    )
    args = parser.parse_args(argv)
    if args.expected_records is not None and args.expected_records <= 0:
        parser.error("--expected-records must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        count, source_count, file_size = check(
            args.terabin_file, args.expected_records
        )
    except RoundTripError as error:
        print(f"tera-bin byte round-trip FAILED: {error}", file=sys.stderr)
        return 2

    print(
        f"tera-bin byte round-trip OK: {count:,}/{count:,} records; "
        f"header source_count={source_count:,}, bytes={file_size:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
