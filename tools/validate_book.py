#!/usr/bin/env python3
"""Fail-closed validator for Terachess EPD opening books.

Every non-empty line must be a unique, canonical, non-terminal Terachess FEN.
Both independent rule implementations must agree on its result and complete
legal move set.  When a make_book receipt is supplied, its frozen parameters
and output hashes are checked against the actual file.
"""

import argparse
import hashlib
import json
import os
import sys
import time


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def opening_ply(fen):
    """Infer the ply represented by a canonical six-field FEN."""
    fields = fen.split()
    if len(fields) != 6:
        raise ValueError(f"FEN needs 6 fields, got {len(fields)}")
    fullmove = int(fields[5])
    if fullmove < 1:
        raise ValueError(f"fullmove must be >= 1, got {fullmove}")
    return 2 * (fullmove - 1) + (fields[1] == "b")


def validate_receipt(receipt, book_path, payload, lines, plies):
    errors = []
    if receipt.get("schema") != "terachess-opening-book-receipt-v1":
        errors.append("receipt schema is not terachess-opening-book-receipt-v1")

    params = receipt.get("parameters", {})
    result = receipt.get("result", {})
    actual_raw_sha = sha256_bytes(payload)
    actual_text_sha = sha256_bytes(payload.decode("utf-8").replace("\r\n", "\n").encode("utf-8"))
    expected = {
        "result.name": (result.get("name"), os.path.basename(book_path)),
        "result.lines": (result.get("lines"), len(lines)),
        "result.bytes": (result.get("bytes"), len(payload)),
        "result.sha256_raw": (result.get("sha256_raw"), actual_raw_sha),
        "result.sha256_text": (result.get("sha256_text"), actual_text_sha),
        "parameters.lines": (params.get("lines"), len(lines)),
    }
    for label, (got, want) in expected.items():
        if got != want:
            errors.append(f"{label}: receipt={got!r}, actual={want!r}")

    min_plies = params.get("min_plies")
    max_plies = params.get("max_plies")
    if not isinstance(min_plies, int) or not isinstance(max_plies, int):
        errors.append("receipt min_plies/max_plies must be integers")
    elif plies and (min(plies) < min_plies or max(plies) > max_plies):
        errors.append(
            f"opening ply range {min(plies)}..{max(plies)} is outside receipt "
            f"range {min_plies}..{max_plies}"
        )
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", required=True)
    parser.add_argument("--receipt")
    parser.add_argument("--expected-lines", type=int)
    parser.add_argument("--expected-raw-sha256")
    parser.add_argument("--json", help="optional machine-readable validation receipt")
    args = parser.parse_args()

    from run_fixtures import load_impl

    book_path = os.path.abspath(args.book)
    if not os.path.isfile(book_path):
        parser.error(f"book does not exist: {book_path}")

    payload = open(book_path, "rb").read()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"ERROR: book is not UTF-8: {exc}", file=sys.stderr)
        return 2

    raw_lines = text.splitlines()
    lines = [line for line in raw_lines if line]
    errors = []
    if len(lines) != len(raw_lines):
        errors.append("book contains blank lines")
    if args.expected_lines is not None and len(lines) != args.expected_lines:
        errors.append(f"line count {len(lines)} != expected {args.expected_lines}")

    actual_raw_sha = sha256_bytes(payload)
    if args.expected_raw_sha256 and actual_raw_sha != args.expected_raw_sha256.lower():
        errors.append(
            f"raw SHA-256 {actual_raw_sha} != expected {args.expected_raw_sha256.lower()}"
        )

    duplicates = len(lines) - len(set(lines))
    if duplicates:
        errors.append(f"book contains {duplicates} duplicate FEN lines")

    impl_a = load_impl("a")
    impl_b = load_impl("b")
    plies = []
    t0 = time.time()
    for number, fen in enumerate(lines, 1):
        try:
            pos_a = impl_a.Position.from_fen(fen)
            pos_b = impl_b.Position.from_fen(fen)
            canonical_a = pos_a.to_fen()
            canonical_b = pos_b.to_fen()
            if canonical_a != fen:
                errors.append(f"line {number}: oracle A non-canonical round-trip")
            if canonical_b != fen:
                errors.append(f"line {number}: oracle B non-canonical round-trip")
            result_a = pos_a.result()
            result_b = pos_b.result()
            if result_a != "*" or result_b != "*":
                errors.append(
                    f"line {number}: terminal position A={result_a}, B={result_b}"
                )
            moves_a = pos_a.legal_moves()
            moves_b = pos_b.legal_moves()
            if moves_a != moves_b:
                missing = sorted(set(moves_a) - set(moves_b))[:8]
                extra = sorted(set(moves_b) - set(moves_a))[:8]
                errors.append(
                    f"line {number}: legal move mismatch A={len(moves_a)}, "
                    f"B={len(moves_b)}, A-only={missing}, B-only={extra}"
                )
            if not moves_a:
                errors.append(f"line {number}: no legal moves")
            plies.append(opening_ply(fen))
        except Exception as exc:
            errors.append(f"line {number}: {type(exc).__name__}: {exc}")

    receipt_path = os.path.abspath(args.receipt) if args.receipt else None
    if receipt_path:
        try:
            with open(receipt_path, encoding="utf-8") as source:
                receipt = json.load(source)
            errors.extend(validate_receipt(receipt, book_path, payload, lines, plies))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"receipt error: {type(exc).__name__}: {exc}")

    elapsed = time.time() - t0
    report = {
        "schema": "terachess-opening-book-validation-v1",
        "book": os.path.basename(book_path),
        "bytes": len(payload),
        "lines": len(lines),
        "unique_lines": len(set(lines)),
        "opening_ply_min": min(plies) if plies else None,
        "opening_ply_max": max(plies) if plies else None,
        "sha256_raw": actual_raw_sha,
        "oracles": ["a", "b"],
        "errors": errors,
        "elapsed_seconds": round(elapsed, 3),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as target:
            json.dump(report, target, indent=2, sort_keys=True)
            target.write("\n")

    print(
        f"book: {len(lines)} lines | unique: {len(set(lines))} | "
        f"plies: {report['opening_ply_min']}..{report['opening_ply_max']} | "
        f"errors: {len(errors)} | {elapsed:.1f}s"
    )
    print(f"sha256 raw: {actual_raw_sha}")
    for error in errors[:50]:
        print(f"ERROR: {error}", file=sys.stderr)
    if len(errors) > 50:
        print(f"ERROR: {len(errors) - 50} additional errors omitted", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
