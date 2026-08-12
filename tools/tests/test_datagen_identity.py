#!/usr/bin/env python3
"""Fail-closed integration test for the authenticated Terachess DATAGEN command."""

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def different_hash(value: str) -> str:
    return ("0" if value[0] != "0" else "1") + value[1:]


def run_engine(engine: Path, lines: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [engine],
        input=("\n".join(lines) + "\n").encode("utf-8"),
        cwd=engine.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )


def artifacts(path: Path) -> list[Path]:
    parent = path.parent
    return list(parent.glob(path.name + "*")) if parent.exists() else []


def assert_rejected(engine: Path, command: str, out: Path, label: str,
                    setup: list[str] | None = None) -> None:
    result = run_engine(engine, [*(setup or []), command, "quit"])
    text = result.stdout.decode("utf-8", "replace")
    if result.returncode == 0 or artifacts(out):
        raise AssertionError(
            f"{label}: expected nonzero exit and no artifacts, got exit "
            f"{result.returncode}, artifacts={artifacts(out)}, stdout={text!r}"
        )
    print(f"negative {label}: exit {result.returncode}, 0 artifacts")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-dirty", action="store_true")
    args = parser.parse_args()

    engine = args.engine.resolve()
    network = args.network.resolve()
    producer_sha = sha256(engine)
    network_sha = sha256(network)

    with tempfile.TemporaryDirectory(prefix="tera datagen identity ") as temporary:
        root = Path(temporary)

        def command(out: Path, *, producer: str = producer_sha,
                    net_hash: str = network_sha, book: str = "NONE",
                    book_hash: str = "NONE", resume: bool = False) -> str:
            value = (
                f'datagen out "{out}" count 4 nodes 1 threads 1 seed 12082026 '
                f'book "{book}" book_sha256 {book_hash} '
                f'network "{network}" network_sha256 {net_hash} '
                f'producer_sha256 {producer} random_move_count 2 '
                'random_move_min_ply 1 random_move_max_ply 4 random_multi_pv 2 '
                'random_multi_pv_diff 100 write_min_ply 1 eval_limit 10000 '
                'filter_captures 0 filter_checks 0 --debug-sample 4'
            )
            return value + (" --resume" if resume else "")

        missing = root / "missing contract" / "chunk.bin"
        assert_rejected(
            engine,
            f'datagen out "{missing}" count 4 nodes 1 threads 1 seed 1',
            missing,
            "missing identity",
        )

        bad_producer = root / "bad producer" / "chunk.bin"
        assert_rejected(
            engine,
            command(bad_producer, producer=different_hash(producer_sha)),
            bad_producer,
            "producer SHA-256 mismatch",
        )

        bad_network = root / "bad network" / "chunk.bin"
        assert_rejected(
            engine,
            command(bad_network, net_hash=different_hash(network_sha)),
            bad_network,
            "network SHA-256 mismatch",
        )

        material = root / "material fallback" / "chunk.bin"
        assert_rejected(
            engine,
            command(material),
            material,
            "material fallback",
            ["setoption name UseNNUE value false"],
        )

        fake_book = root / "book with spaces.epd"
        fake_book.write_bytes(b"not reached because its hash is wrong\n")
        bad_book = root / "bad book" / "chunk.bin"
        assert_rejected(
            engine,
            command(bad_book, book=str(fake_book), book_hash="0" * 64),
            bad_book,
            "book SHA-256 mismatch",
        )

        positive = root / "positive path with spaces" / "chunk.bin"
        result = run_engine(engine, [command(positive), "quit"])
        stdout = result.stdout.decode("utf-8", "replace")
        expected = [positive, Path(str(positive) + ".meta.json"),
                    Path(str(positive) + ".debug.txt"), Path(str(positive) + ".resume")]
        if result.returncode or not all(path.is_file() for path in expected):
            raise AssertionError(
                f"positive generation failed: exit={result.returncode}, stdout={stdout!r}"
            )
        if stdout.count("EvalFile: loaded") != 1:
            raise AssertionError("expected exactly one network load for the process")
        raw = positive.read_bytes()
        if len(raw) != 32 + 4 * 144 or raw[:4] != b"TC01":
            raise AssertionError("positive output does not satisfy tera-bin v1 size/magic")
        version, record_size = struct.unpack_from("<HH", raw, 4)
        records = struct.unpack_from("<Q", raw, 8)[0]
        if (version, record_size, records) != (1, 144, 4):
            raise AssertionError("positive output has an invalid tera-bin v1 header")

        metadata = json.loads(Path(str(positive) + ".meta.json").read_text(encoding="utf-8"))
        wanted = {
            "provenance_schema": "terachess-datagen-provenance-v1",
            "source_commit": args.source_commit,
            "source_dirty": args.source_dirty,
            "producer_sha256": producer_sha,
            "network_sha256": network_sha,
            "book_sha256": "none",
            "records": 4,
        }
        for key, value in wanted.items():
            if metadata.get(key) != value:
                raise AssertionError(f"metadata {key}: {metadata.get(key)!r} != {value!r}")

        tools = Path(__file__).resolve().parents[1]
        audit = subprocess.run(
            [sys.executable, tools / "audit_terabin.py", positive, "--strict"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        roundtrip = subprocess.run(
            [sys.executable, Path(__file__).with_name("test_datagen_roundtrip.py"),
             positive, "--limit", "4"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if audit.returncode or roundtrip.returncode:
            raise AssertionError(
                "positive output failed strict audit or double round-trip: "
                f"audit={audit.stdout!r}/{audit.stderr!r}, "
                f"roundtrip={roundtrip.stdout!r}/{roundtrip.stderr!r}"
            )

        resumed = run_engine(engine, [command(positive, resume=True), "quit"])
        if resumed.returncode or b"authenticated sidecars" not in resumed.stdout:
            raise AssertionError("idempotent authenticated resume failed")

        resume_mismatch = run_engine(
            engine,
            [command(positive, producer=different_hash(producer_sha), resume=True), "quit"],
        )
        if resume_mismatch.returncode == 0:
            raise AssertionError("completed resume accepted a mismatched producer identity")

        print(
            "positive: 4/4 records, strict audit 0 warnings, round-trip 4/4, "
            "one network load, provenance exact; resume exact PASS, "
            "resume mismatch rejected"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
