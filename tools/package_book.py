#!/usr/bin/env python3
"""Create a deterministic single-member ZIP for an OpenBench EPD book."""

import argparse
import hashlib
import json
import os
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--expected-lines", required=True, type=int)
    parser.add_argument("--expected-raw-sha256", required=True)
    args = parser.parse_args()

    source = os.path.abspath(args.source)
    output = os.path.abspath(args.output)
    if not os.path.isfile(source):
        parser.error(f"source does not exist: {source}")
    if os.path.exists(output):
        parser.error(f"refusing to overwrite: {output}")
    if os.path.basename(args.member) != args.member or not args.member.endswith(".epd"):
        parser.error("--member must be a plain .epd filename")

    payload = open(source, "rb").read()
    actual_sha = sha256(payload)
    if actual_sha != args.expected_raw_sha256.lower():
        parser.error(
            f"source SHA-256 {actual_sha} != expected {args.expected_raw_sha256.lower()}"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        parser.error(f"source is not UTF-8: {exc}")
    if b"\r" in payload:
        parser.error("source must use LF line endings")
    lines = text.splitlines()
    if len(lines) != args.expected_lines or any(not line for line in lines):
        parser.error(
            f"source line contract failed: lines={len(lines)}, "
            f"expected={args.expected_lines}, blank={any(not line for line in lines)}"
        )

    os.makedirs(os.path.dirname(output), exist_ok=True)
    info = ZipInfo(args.member, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    with ZipFile(output, mode="x") as archive:
        archive.writestr(info, payload, compress_type=ZIP_DEFLATED, compresslevel=9)

    archive_payload = open(output, "rb").read()
    with ZipFile(output) as archive:
        names = archive.namelist()
        extracted = archive.read(args.member)
    if names != [args.member] or extracted != payload:
        print("ERROR: archive round-trip mismatch", file=sys.stderr)
        return 1

    report = {
        "schema": "terachess-opening-book-package-v1",
        "source": os.path.basename(source),
        "member": args.member,
        "lines": len(lines),
        "payload_bytes": len(payload),
        "payload_sha256": actual_sha,
        "archive_bytes": len(archive_payload),
        "archive_sha256": sha256(archive_payload),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
