#!/usr/bin/env python3
"""Deterministic offline harness for the Terachess P1/LMP family.

The trace build keeps the primary search on the frozen baseline.  At sampled
LMP triggers it replays the remaining quiet candidates from a copied stack
while suppressing TT/history writes.  This driver freezes independent roots,
collects authenticated traces, verifies non-interference controls, and scores
the predeclared policies without changing the official six-SPRT budget.

Nothing in this file starts an OpenBench workload.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "oracle"))

import terabin
from run_fixtures import load_impl


SCHEMA_ROOTS = "tera-lmp-shadow-roots-v1"
SCHEMA_TRACE = "tera-lmp-shadow-v1"
SCHEMA_COLLECTION = "tera-lmp-shadow-collection-v1"
SCHEMA_ANALYSIS = "tera-lmp-shadow-analysis-v1"
SCHEMA_CONTROLS = "tera-lmp-shadow-controls-v1"
NET2_SHA256 = "05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861"
BOOK_SHA256 = "1f117b0ed03049afad62481494fff9e3232774d188433a99ffff1454d84babe7"
ROOTS_SHA256 = "099d9eec8ef58f8608cefca4f7011546e8211de6e6b5b02f461741807fd0c661"
SOURCE_SHA256 = "24671a8ca66eb241eb71d79c1cd3023410088dfdf0ba9ebfb75e79cf593627ff"
EXPECTED_BENCH = 32541
FROZEN_ROOTS_PER_SPLIT = 128
FROZEN_NODES_PER_ROOT = 100000
FROZEN_TRACE_EVERY = 1
FROZEN_MAX_RECORDS = 24000
FROZEN_MIN_EXPOSED = 20000
FROZEN_MIN_PER_STRATUM = 1000
FROZEN_ORACLE_SAMPLE = 512
BASELINE_POLICIES = (
    "baseline",
    "U2",
    "D4",
    "k1.5",
    "k2",
    "k3",
    "k4",
    "legal-half",
    "no-LMP",
)
U34_POLICIES = ("baseline", "U3/4")
TRACE_ENV = (
    "TERA_LMP_TRACE_PATH",
    "TERA_LMP_TRACE_EVERY",
    "TERA_LMP_TRACE_MAX",
    "TERA_LMP_TRACE_MODE",
)


class HarnessError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files_byte_identical(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve() or first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_block = left.read(1024 * 1024)
            right_block = right.read(1024 * 1024)
            if left_block != right_block:
                return False
            if not left_block:
                return True


def canonical_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def require_file(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise HarnessError(f"{label} not found: {path}")
    return path


def book_fens(path: Path) -> set[str]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != BOOK_SHA256:
        raise HarnessError("opening book SHA-256 differs from the frozen v1 artifact")
    lines = payload.decode("ascii").splitlines()
    if len(lines) != 5000 or len(set(lines)) != 5000:
        raise HarnessError("opening book must contain exactly 5,000 unique FENs")
    return set(lines)


def make_roots(args: argparse.Namespace) -> int:
    source = require_file(Path(args.source), "tera-bin source")
    book = require_file(Path(args.book), "opening book")
    if args.count != 256 or args.dev_count != 128:
        raise HarnessError("P1 contract freezes 256 roots split 128 development/128 holdout")
    if args.min_record_gap < 1201:
        raise HarnessError("min-record-gap must exceed the 1,200-ply game cap")

    started = time.time()
    source_sha = sha256_file(source)
    excluded = book_fens(book)
    oracle_a = load_impl("a")
    oracle_b = load_impl("b")

    with source.open("rb") as stream:
        count, source_count, flags = terabin.read_header(stream)
        expected_size = terabin.HEADER_SIZE + count * terabin.RECORD_SIZE
        if source.stat().st_size != expected_size:
            raise HarnessError(
                f"tera-bin size mismatch: {source.stat().st_size} != {expected_size}"
            )

        rng = random.Random(args.seed)
        accepted: list[dict] = []
        accepted_indices: list[int] = []
        accepted_fens: set[str] = set()
        attempts = 0
        rejection_counts: collections.Counter[str] = collections.Counter()

        while len(accepted) < args.count:
            attempts += 1
            if attempts > args.max_attempts:
                raise HarnessError(
                    f"only accepted {len(accepted)} roots after {attempts} attempts"
                )
            index = rng.randrange(count)
            if any(abs(index - other) < args.min_record_gap for other in accepted_indices):
                rejection_counts["record_gap"] += 1
                continue
            stream.seek(terabin.HEADER_SIZE + index * terabin.RECORD_SIZE)
            raw = stream.read(terabin.RECORD_SIZE)
            if len(raw) != terabin.RECORD_SIZE:
                raise HarnessError(f"truncated record at index {index}")
            try:
                record = terabin.unpack(raw)
                fen = terabin.to_fen(record)
            except ValueError:
                rejection_counts["tera_bin_decode"] += 1
                continue
            if not args.min_ply <= record.ply <= args.max_ply:
                rejection_counts["ply_range"] += 1
                continue
            if fen in excluded:
                rejection_counts["book_overlap"] += 1
                continue
            if fen in accepted_fens:
                rejection_counts["duplicate_fen"] += 1
                continue
            try:
                pos_a = oracle_a.Position.from_fen(fen)
                pos_b = oracle_b.Position.from_fen(fen)
                if pos_a.to_fen() != fen or pos_b.to_fen() != fen:
                    rejection_counts["noncanonical_fen"] += 1
                    continue
                legal_a = sorted(pos_a.legal_moves())
                legal_b = sorted(pos_b.legal_moves())
            except Exception:
                rejection_counts["oracle_error"] += 1
                continue
            if legal_a != legal_b:
                rejection_counts["oracle_move_mismatch"] += 1
                continue
            if not legal_a:
                rejection_counts["terminal"] += 1
                continue

            identity = hashlib.sha256(
                f"{source_sha}:{index}:{fen}".encode("ascii")
            ).hexdigest()
            accepted.append(
                {
                    "id": identity[:16],
                    "split_key": identity,
                    "record_index": index,
                    "ply": record.ply,
                    "piece_count": len(record.pieces),
                    "legal_moves": len(legal_a),
                    "side_to_move": "black" if record.stm else "white",
                    "score_cp": record.score,
                    "result_code": record.result,
                    "fen": fen,
                    "fen_sha256": hashlib.sha256(fen.encode("ascii")).hexdigest(),
                }
            )
            accepted_indices.append(index)
            accepted_fens.add(fen)

    accepted.sort(key=lambda item: item["split_key"])
    for ordinal, item in enumerate(accepted):
        item["ordinal"] = ordinal
        item["split"] = "development" if ordinal < args.dev_count else "holdout"
        del item["split_key"]

    ply_histogram = collections.Counter(str(item["ply"] // 100 * 100) for item in accepted)
    manifest = {
        "schema": SCHEMA_ROOTS,
        "source": {
            "path": os.path.relpath(source, ROOT),
            "bytes": source.stat().st_size,
            "sha256": source_sha,
            "records": count,
            "source_records": source_count,
            "flags": flags,
        },
        "exclusion_book": {
            "path": os.path.relpath(book, ROOT),
            "bytes": book.stat().st_size,
            "sha256": BOOK_SHA256,
            "positions": len(excluded),
            "overlap": 0,
        },
        "sampling": {
            "seed": args.seed,
            "count": args.count,
            "development": args.dev_count,
            "holdout": args.count - args.dev_count,
            "min_ply": args.min_ply,
            "max_ply": args.max_ply,
            "min_record_gap": args.min_record_gap,
            "attempts": attempts,
            "rejections": dict(sorted(rejection_counts.items())),
            "dual_oracle_positions": args.count,
            "dual_oracle_failures": 0,
            "ply_century_histogram": dict(sorted(ply_histogram.items(), key=lambda x: int(x[0]))),
        },
        "elapsed_seconds": round(time.time() - started, 3),
        "roots": accepted,
    }
    output = Path(args.out).resolve()
    canonical_json(output, manifest)
    print(
        f"roots PASS: {args.count} unique, 128/128, dual-oracle 0 failures, "
        f"book overlap 0; {time.time() - started:.1f}s"
    )
    print(f"source SHA-256 {source_sha}; manifest {output}")
    return 0


def roots_from_manifest(path: Path, split: str, limit: int | None = None) -> tuple[dict, list[dict]]:
    if sha256_file(path) != ROOTS_SHA256:
        raise HarnessError(f"root manifest SHA-256 differs from frozen P1 roots {ROOTS_SHA256}")
    manifest = load_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_ROOTS:
        raise HarnessError("root manifest has the wrong schema")
    roots = manifest.get("roots")
    if not isinstance(roots, list) or len(roots) != 256:
        raise HarnessError("root manifest must contain exactly 256 roots")
    if manifest.get("source", {}).get("sha256") != SOURCE_SHA256:
        raise HarnessError("root manifest source SHA-256 differs from frozen c3_final")
    if manifest.get("exclusion_book", {}).get("sha256") != BOOK_SHA256:
        raise HarnessError("root manifest opening-book SHA-256 differs from frozen v1 book")
    if [root.get("ordinal") for root in roots] != list(range(256)):
        raise HarnessError("root manifest ordinals are not exactly 0..255")
    if len({root.get("id") for root in roots}) != 256:
        raise HarnessError("root manifest IDs are not unique")
    if len({root.get("fen") for root in roots}) != 256:
        raise HarnessError("root manifest FENs are not unique")
    for root in roots:
        fen = root.get("fen")
        if not isinstance(fen, str) or root.get("fen_sha256") != hashlib.sha256(
            fen.encode("ascii")
        ).hexdigest():
            raise HarnessError("root manifest contains an invalid FEN identity")
    split_counts = collections.Counter(root.get("split") for root in roots)
    if split_counts != {"development": 128, "holdout": 128}:
        raise HarnessError(f"root manifest split counts are invalid: {dict(split_counts)}")
    selected = roots if split == "all" else [root for root in roots if root["split"] == split]
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise HarnessError(f"root selection is empty for split={split}")
    return manifest, selected


def uci_script(roots: list[dict], net: Path, nodes: int, threads: int = 1) -> str:
    commands = [
        "uci",
        f"setoption name Threads value {threads}",
        "setoption name Hash value 16",
        f"setoption name EvalFile value {net}",
        "isready",
    ]
    for root in roots:
        commands.extend(
            [
                "ucinewgame",
                "isready",
                f"position fen {root['fen']}",
                f"go nodes {nodes}",
            ]
        )
    # `go` is asynchronous. The next root's `ucinewgame` waits for the prior
    # search, but a final `quit` would set stop=true and truncate the last root.
    # search_clear(), reached through `ucinewgame`, is the synchronous barrier.
    commands.extend(["ucinewgame", "quit"])
    return "\n".join(commands) + "\n"


def parse_searches(output: str) -> list[dict]:
    searches: list[dict] = []
    current: dict = {}
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("info "):
            for name, pattern in (
                ("depth", r"\bdepth (\d+)"),
                ("seldepth", r"\bseldepth (\d+)"),
                ("nodes", r"\bnodes (\d+)"),
                ("time_ms", r"\btime (\d+)"),
            ):
                match = re.search(pattern, line)
                if match:
                    current[name] = int(match.group(1))
            score = re.search(r"\bscore (cp|mate) (-?\d+)", line)
            if score:
                current["score_kind"] = score.group(1)
                current["score"] = int(score.group(2))
        elif line.startswith("bestmove "):
            fields = line.split()
            current["bestmove"] = fields[1]
            searches.append(current)
            current = {}
    return searches


def run_search_batch(
    engine: Path,
    net: Path,
    roots: list[dict],
    nodes: int,
    timeout: int,
    trace_path: Path | None = None,
    every: int = 128,
    maximum: int = 20000,
    threads: int = 1,
    mode: str = "baseline",
) -> tuple[str, list[dict], float]:
    env = os.environ.copy()
    for name in TRACE_ENV:
        env.pop(name, None)
    if trace_path is not None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        if trace_path.exists():
            trace_path.unlink()
        env.update(
            {
                "TERA_LMP_TRACE_PATH": str(trace_path.resolve()),
                "TERA_LMP_TRACE_EVERY": str(every),
                "TERA_LMP_TRACE_MAX": str(maximum),
                "TERA_LMP_TRACE_MODE": mode,
            }
        )
    started = time.time()
    completed = subprocess.run(
        [str(engine)],
        input=uci_script(roots, net, nodes, threads),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    elapsed = time.time() - started
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-30:])
        raise HarnessError(f"engine exited {completed.returncode}\n{tail}")
    if "EvalFile: loaded" not in completed.stdout:
        raise HarnessError("engine did not confirm EvalFile loading")
    searches = parse_searches(completed.stdout)
    if len(searches) != len(roots):
        raise HarnessError(f"engine returned {len(searches)} searches for {len(roots)} roots")
    for index, search in enumerate(searches):
        if "nodes" not in search or "bestmove" not in search:
            raise HarnessError(f"search {index} lacks nodes or bestmove")
        if search["nodes"] < nodes:
            raise HarnessError(
                f"search {index} stopped at {search['nodes']} nodes before requested {nodes}"
            )
    return completed.stdout, searches, elapsed


def iter_trace(path: Path):
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise HarnessError(f"invalid trace JSON at line {number}: {error}") from error
            if record.get("schema") != SCHEMA_TRACE:
                raise HarnessError(f"wrong trace schema at line {number}")
            yield record


def read_trace(path: Path) -> list[dict]:
    """Materialize a trace only for deliberately tiny control runs."""
    return list(iter_trace(path))


def validate_collection_contract(
    collection: object,
    collection_path: Path,
    trace_path: Path,
    trace_records: int | list[dict],
    roots_path: Path,
    root_fens: set[str],
    split: str,
    mode: str,
) -> list[str]:
    """Authenticate one full frozen collection receipt without trusting its labels."""
    prefix = collection_path.name
    errors: list[str] = []
    if not isinstance(collection, dict) or collection.get("schema") != SCHEMA_COLLECTION:
        return [f"{prefix}: wrong collection schema"]

    engine = collection.get("engine", {})
    engine_path = Path(engine.get("path", ""))
    if not engine_path.is_file():
        errors.append(f"{prefix}: recorded trace engine is unavailable")
    elif (
        engine.get("bytes") != engine_path.stat().st_size
        or engine.get("sha256") != sha256_file(engine_path)
    ):
        errors.append(f"{prefix}: trace engine bytes/hash differ from receipt")

    network = collection.get("network", {})
    network_path = Path(network.get("path", ""))
    if network.get("sha256") != NET2_SHA256:
        errors.append(f"{prefix}: receipt does not pin frozen net-2")
    if not network_path.is_file():
        errors.append(f"{prefix}: recorded network is unavailable")
    elif (
        network.get("bytes") != network_path.stat().st_size
        or network.get("sha256") != sha256_file(network_path)
    ):
        errors.append(f"{prefix}: network bytes/hash differ from receipt")

    roots = collection.get("roots", {})
    if roots != {
        "path": str(roots_path.resolve()),
        "sha256": ROOTS_SHA256,
        "source_sha256": SOURCE_SHA256,
        "split": split,
        "count": FROZEN_ROOTS_PER_SPLIT,
    }:
        errors.append(f"{prefix}: roots receipt differs from frozen full-split contract")

    search = collection.get("search", {})
    for key, expected in {
        "nodes_per_root": FROZEN_NODES_PER_ROOT,
        "threads": 1,
        "hash_mb": 16,
        "ucinewgame_per_root": True,
        "final_ucinewgame_barrier": True,
    }.items():
        if search.get(key) != expected:
            errors.append(f"{prefix}: search.{key}={search.get(key)!r}, expected {expected!r}")

    trace = collection.get("trace", {})
    trace_record_count = trace_records if isinstance(trace_records, int) else len(trace_records)
    trace_expected = {
        "sha256": sha256_file(trace_path),
        "bytes": trace_path.stat().st_size,
        "records": trace_record_count,
        "every": FROZEN_TRACE_EVERY,
        "max_records": FROZEN_MAX_RECORDS,
        "mode": mode,
    }
    for key, expected in trace_expected.items():
        if trace.get(key) != expected:
            errors.append(f"{prefix}: trace.{key}={trace.get(key)!r}, expected {expected!r}")
    if Path(trace.get("path", "")).resolve() != trace_path.resolve():
        errors.append(f"{prefix}: trace path differs from analyzed input")

    transcript = collection.get("transcript", {})
    transcript_path = Path(transcript.get("path", ""))
    if not transcript_path.is_file():
        errors.append(f"{prefix}: recorded UCI transcript is unavailable")
    elif (
        transcript.get("bytes") != transcript_path.stat().st_size
        or transcript.get("sha256") != sha256_file(transcript_path)
    ):
        errors.append(f"{prefix}: UCI transcript bytes/hash differ from receipt")

    results = collection.get("results", [])
    result_fens = [item.get("fen") for item in results if isinstance(item, dict)]
    if len(results) != FROZEN_ROOTS_PER_SPLIT or set(result_fens) != root_fens:
        errors.append(f"{prefix}: primary results do not cover the frozen split exactly")
    if len(result_fens) != len(set(result_fens)):
        errors.append(f"{prefix}: primary results contain duplicate FENs")
    if any(item.get("split") != split for item in results if isinstance(item, dict)):
        errors.append(f"{prefix}: primary result split label mismatch")
    if any(
        not isinstance(item.get("nodes"), int)
        or item.get("nodes") < FROZEN_NODES_PER_ROOT
        for item in results
        if isinstance(item, dict)
    ):
        errors.append(f"{prefix}: primary results contain truncated node counts")
    return errors


def collect(args: argparse.Namespace) -> int:
    engine = require_file(Path(args.engine), "trace engine")
    net = require_file(Path(args.net), "network")
    roots_path = require_file(Path(args.roots), "root manifest")
    net_sha = sha256_file(net)
    if net_sha != NET2_SHA256:
        raise HarnessError(f"net SHA-256 {net_sha} != frozen net-2 {NET2_SHA256}")
    manifest, roots = roots_from_manifest(roots_path, args.split, args.limit)
    trace_path = Path(args.trace).resolve()
    output_path = Path(args.out).resolve()
    transcript_path = Path(args.transcript).resolve() if args.transcript else output_path.with_suffix(".uci.log")
    for path in (trace_path, output_path, transcript_path):
        if path.exists() and not args.overwrite:
            raise HarnessError(f"refusing to overwrite {path}; pass --overwrite")

    stdout, searches, elapsed = run_search_batch(
        engine,
        net,
        roots,
        args.nodes,
        args.timeout,
        trace_path,
        args.every,
        args.max_records,
        mode=args.mode,
    )
    if not trace_path.is_file():
        raise HarnessError("trace build produced no trace file")
    trace_record_count = 0
    stopped = False
    for record in iter_trace(trace_path):
        trace_record_count += 1
        stopped = stopped or bool(record.get("stopped"))
    if not trace_record_count:
        raise HarnessError("trace file contains zero records")
    if stopped:
        raise HarnessError("trace contains a stopped/incomplete shadow replay")

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(stdout, encoding="utf-8", newline="\n")
    results = []
    for root, search in zip(roots, searches):
        results.append(
            {
                "root_id": root["id"],
                "split": root["split"],
                "fen": root["fen"],
                **search,
            }
        )
    receipt = {
        "schema": SCHEMA_COLLECTION,
        "engine": {
            "path": str(engine),
            "bytes": engine.stat().st_size,
            "sha256": sha256_file(engine),
        },
        "network": {"path": str(net), "bytes": net.stat().st_size, "sha256": net_sha},
        "roots": {
            "path": str(roots_path.resolve()),
            "sha256": sha256_file(roots_path),
            "source_sha256": manifest["source"]["sha256"],
            "split": args.split,
            "count": len(roots),
        },
        "search": {
            "nodes_per_root": args.nodes,
            "threads": 1,
            "hash_mb": 16,
            "ucinewgame_per_root": True,
            "final_ucinewgame_barrier": True,
            "elapsed_seconds": round(elapsed, 3),
        },
        "trace": {
            "path": str(trace_path),
            "bytes": trace_path.stat().st_size,
            "sha256": sha256_file(trace_path),
            "records": trace_record_count,
            "every": args.every,
            "max_records": args.max_records,
            "mode": args.mode,
        },
        "transcript": {
            "path": str(transcript_path),
            "bytes": transcript_path.stat().st_size,
            "sha256": sha256_file(transcript_path),
        },
        "results": results,
    }
    canonical_json(output_path, receipt)
    print(
        f"collection PASS: {len(roots)} roots, {trace_record_count} records, "
        f"{elapsed:.1f}s; trace SHA-256 {receipt['trace']['sha256']}"
    )
    return 0


def run_bench(engine: Path, net: Path, timeout: int) -> tuple[int, str]:
    env = os.environ.copy()
    for name in TRACE_ENV:
        env.pop(name, None)
    completed = subprocess.run(
        [str(engine)],
        input=(
            "uci\nsetoption name Threads value 1\n"
            f"setoption name EvalFile value {net}\nisready\nbench\nquit\n"
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if completed.returncode:
        raise HarnessError(f"bench engine exited {completed.returncode}")
    matches = re.findall(r"Nodes searched\s*:\s*([0-9,]+)", completed.stdout)
    if not matches:
        raise HarnessError("bench output contains no 'Nodes searched' value")
    return int(matches[-1].replace(",", "")), completed.stdout


def stable_search_projection(searches: list[dict]) -> list[dict]:
    return [
        {key: item.get(key) for key in ("bestmove", "nodes", "depth", "score_kind", "score")}
        for item in searches
    ]


def validate_trace_record(record: dict, index: int, sequences: set[int]) -> list[str]:
    errors: list[str] = []
    prefix = f"record {index}"
    sequence = record.get("sequence")
    if not isinstance(sequence, int) or sequence <= 0 or sequence in sequences:
        errors.append(f"{prefix}: invalid/duplicate sequence")
    sequences.add(sequence)
    tail = record.get("tail")
    if not isinstance(tail, list) or len(tail) != record.get("tail_quiets"):
        errors.append(f"{prefix}: tail length mismatch")
        return errors
    moves = [move.get("move") for move in tail]
    ranks = [move.get("rank") for move in tail]
    if len(set(moves)) != len(moves) or ranks != sorted(set(ranks)):
        errors.append(f"{prefix}: duplicate/non-monotonic tail")
    depth = record.get("depth")
    improving = bool(record.get("improving"))
    if not isinstance(depth, int) or depth <= 0:
        errors.append(f"{prefix}: invalid depth")
        return errors
    t0 = (3 + depth * depth) // (2 - int(improving))
    u34 = max(1, 3 * t0 // 4)
    mode = record.get("probe_mode")
    if mode not in ("baseline", "u34"):
        errors.append(f"{prefix}: invalid/missing probe mode")
        return errors
    expected_probe = t0 if mode == "baseline" else u34
    probe_trigger = record.get("probe_trigger_rank")
    if not isinstance(probe_trigger, int) or probe_trigger < expected_probe:
        errors.append(
            f"{prefix}: {mode} probe trigger precedes its frozen threshold "
            f"{expected_probe}"
        )
    trigger = record.get("baseline_trigger_rank")
    trigger_depth = record.get("baseline_trigger_depth")
    skipped = record.get("baseline_skipped_moves")
    if not isinstance(skipped, list) or len(set(skipped)) != len(skipped):
        errors.append(f"{prefix}: invalid baseline skipped list")
        return errors
    if trigger:
        if not isinstance(trigger_depth, int) or trigger_depth <= 0:
            errors.append(f"{prefix}: missing effective baseline trigger depth")
            return errors
        effective_t0 = (3 + trigger_depth * trigger_depth) // (2 - int(improving))
        if trigger < effective_t0:
            errors.append(
                f"{prefix}: baseline trigger {trigger} precedes effective T0 "
                f"{effective_t0} at depth {trigger_depth}"
            )
        if mode == "baseline" and trigger != probe_trigger:
            errors.append(f"{prefix}: baseline probe and live trigger ranks differ")
        if mode == "baseline" and trigger_depth != depth:
            errors.append(f"{prefix}: baseline probe and live trigger depths differ")
        expected = [move["move"] for move in tail if move["rank"] > trigger]
        if expected != skipped:
            errors.append(f"{prefix}: rank simulation differs from live MovePicker skip")
    else:
        if skipped:
            errors.append(f"{prefix}: skipped moves without a baseline trigger")
        if trigger_depth:
            errors.append(f"{prefix}: trigger depth without a baseline trigger")
        if mode == "baseline":
            errors.append(f"{prefix}: baseline probe has no live baseline trigger")
    for move in tail:
        if move.get("pruned_by_rest"):
            if move.get("value") is not None or move.get("nodes") != 0:
                errors.append(f"{prefix}: downstream-pruned move has value/nodes")
        elif not isinstance(move.get("value"), int) or not isinstance(move.get("nodes"), int):
            errors.append(f"{prefix}: searched move lacks integer value/nodes")
    return errors


def validate_trace_structure(records: list[dict]) -> list[str]:
    errors: list[str] = []
    sequences: set[int] = set()
    for index, record in enumerate(records):
        errors.extend(validate_trace_record(record, index, sequences))
    return errors


def validate_trace_oracles(records: list[dict], sample_count: int) -> tuple[int, list[str]]:
    if sample_count <= 0 or not records:
        return 0, []
    oracle_a = load_impl("a")
    oracle_b = load_impl("b")
    ranked = sorted(
        records,
        key=lambda record: hashlib.sha256(
            f"{record.get('node_key')}:{record.get('fen')}".encode("ascii")
        ).digest(),
    )
    sample = ranked[: min(sample_count, len(ranked))]
    errors: list[str] = []
    for index, record in enumerate(sample):
        prefix = f"oracle sample {index}"
        fen = record.get("fen")
        try:
            pos_a = oracle_a.Position.from_fen(fen)
            pos_b = oracle_b.Position.from_fen(fen)
            if pos_a.to_fen() != fen or pos_b.to_fen() != fen:
                errors.append(f"{prefix}: noncanonical node FEN")
                continue
            legal_a = set(pos_a.legal_moves())
            legal_b = set(pos_b.legal_moves())
        except Exception as error:
            errors.append(f"{prefix}: parse/movegen error {error!r}")
            continue
        if legal_a != legal_b:
            errors.append(f"{prefix}: independent legal move sets differ")
            continue
        traced = {move["move"] for move in record["tail"]}
        if not traced <= legal_a:
            errors.append(
                f"{prefix}: {len(traced - legal_a)} traced tail moves are not oracle-legal"
            )
    return len(sample), errors


def verify_controls(args: argparse.Namespace) -> int:
    normal = require_file(Path(args.normal_engine), "normal engine")
    trace_engine = require_file(Path(args.trace_engine), "trace engine")
    net = require_file(Path(args.net), "network")
    roots_path = require_file(Path(args.roots), "root manifest")
    if sha256_file(net) != NET2_SHA256:
        raise HarnessError("control run requires the frozen net-2 bytes")
    _, roots = roots_from_manifest(roots_path, args.split, args.root_count)

    normal_bench, _ = run_bench(normal, net, args.timeout)
    dormant_bench, _ = run_bench(trace_engine, net, args.timeout)
    with tempfile.TemporaryDirectory(prefix="tera-lmp-controls-") as directory:
        temp = Path(directory)
        _, normal_searches, normal_elapsed = run_search_batch(
            normal, net, roots, args.nodes, args.timeout
        )
        _, dormant_searches, dormant_elapsed = run_search_batch(
            trace_engine, net, roots, args.nodes, args.timeout
        )
        if any(temp.iterdir()):
            raise HarnessError("dormant trace build unexpectedly created an artifact")
        trace_a = temp / "active-a.jsonl"
        trace_b = temp / "active-b.jsonl"
        _, active_a, active_a_elapsed = run_search_batch(
            trace_engine,
            net,
            roots,
            args.nodes,
            args.timeout,
            trace_a,
            1,
            args.max_records,
            mode="baseline",
        )
        _, active_b, active_b_elapsed = run_search_batch(
            trace_engine,
            net,
            roots,
            args.nodes,
            args.timeout,
            trace_b,
            1,
            args.max_records,
            mode="baseline",
        )
        records = read_trace(trace_a)
        errors = validate_trace_structure(records)
        exact_repeat = trace_a.read_bytes() == trace_b.read_bytes()

        projections = {
            "normal": stable_search_projection(normal_searches),
            "trace_dormant": stable_search_projection(dormant_searches),
            "trace_active_a": stable_search_projection(active_a),
            "trace_active_b": stable_search_projection(active_b),
        }
        search_equal = len({json.dumps(value, sort_keys=True) for value in projections.values()}) == 1
        pass_gate = (
            normal_bench == EXPECTED_BENCH
            and dormant_bench == EXPECTED_BENCH
            and bool(records)
            and not errors
            and exact_repeat
            and search_equal
        )
        receipt = {
            "schema": SCHEMA_CONTROLS,
            "pass": pass_gate,
            "normal_engine": {"sha256": sha256_file(normal), "bench": normal_bench},
            "trace_engine": {"sha256": sha256_file(trace_engine), "bench": dormant_bench},
            "network_sha256": sha256_file(net),
            "roots_sha256": sha256_file(roots_path),
            "root_ids": [root["id"] for root in roots],
            "nodes_per_root": args.nodes,
            "trace_records": len(records),
            "trace_sha256": sha256_file(trace_a),
            "repeat_byte_identical": exact_repeat,
            "primary_search_identical": search_equal,
            "structural_errors": errors,
            "projections": projections,
            "elapsed_seconds": {
                "normal": round(normal_elapsed, 3),
                "trace_dormant": round(dormant_elapsed, 3),
                "trace_active_a": round(active_a_elapsed, 3),
                "trace_active_b": round(active_b_elapsed, 3),
            },
        }
        canonical_json(Path(args.out).resolve(), receipt)
    print(
        f"controls {'PASS' if pass_gate else 'FAIL'}: bench {normal_bench}/{dormant_bench}, "
        f"records {receipt['trace_records']}, repeat={exact_repeat}, primary={search_equal}"
    )
    return 0 if pass_gate else 1


def depth_stratum(depth: int) -> str | None:
    if 4 <= depth <= 5:
        return "4-5"
    if 6 <= depth <= 8:
        return "6-8"
    if 9 <= depth <= 12:
        return "9-12"
    return None


def policy_thresholds(record: dict) -> dict[str, int | None]:
    mode = record.get("probe_mode")
    improving = int(bool(record["improving"]))
    probe_trigger = record.get("probe_trigger_rank", 1)
    trigger = record.get("baseline_trigger_rank", 0)
    trigger_depth = record.get("baseline_trigger_depth", 0)

    if mode == "u34":
        return {"baseline": trigger or None, "U3/4": probe_trigger}
    if mode != "baseline":
        raise HarnessError(f"unsupported probe mode {mode!r}")
    if not trigger:
        return {policy: None for policy in BASELINE_POLICIES}

    numerator = 3 + trigger_depth * trigger_depth
    t0 = numerator // (2 - improving)

    def reachable(threshold: int) -> int:
        return max(threshold, probe_trigger)

    return {
        "baseline": reachable(t0),
        "U2": reachable(2 * t0),
        "D4": None if trigger_depth <= 4 else trigger,
        "k1.5": reachable(3 * numerator // 2),
        "k2": reachable(2 * numerator),
        "k3": reachable(3 * numerator),
        "k4": reachable(4 * numerator),
        "legal-half": reachable(max(t0, math.ceil(record["legal_move_count"] / 2))),
        "no-LMP": None,
    }


def retained(
    policy: str,
    move: dict,
    thresholds: dict[str, int | None],
    baseline_skipped: set[str],
) -> bool:
    if policy == "baseline":
        return move["move"] not in baseline_skipped
    threshold = thresholds[policy]
    return threshold is None or move["rank"] <= threshold


def analysis_parameters_frozen(args: argparse.Namespace, root_count: int) -> bool:
    return (
        args.limit is None
        and args.min_exposed == FROZEN_MIN_EXPOSED
        and args.min_per_stratum == FROZEN_MIN_PER_STRATUM
        and args.oracle_sample == FROZEN_ORACLE_SAMPLE
        and root_count == FROZEN_ROOTS_PER_SPLIT
    )


def scan_trace_for_analysis(
    trace_path: Path, root_fens: set[str], oracle_sample_count: int
) -> dict:
    """Validate and summarize a full trace with bounded memory.

    Only the frozen oracle sample is retained.  All other records are released
    after their structural and stratum checks, so a valid 24k-record trace does
    not expand into several gigabytes of Python objects.
    """
    record_count = 0
    exposed_count = 0
    modes: set[str | None] = set()
    strata: collections.Counter = collections.Counter()
    structure_errors: list[str] = []
    sequences: set[int] = set()
    oracle_heap: list[tuple[int, int, dict]] = []

    for record in iter_trace(trace_path):
        if record.get("root_fen") not in root_fens:
            continue
        index = record_count
        record_count += 1
        structure_errors.extend(validate_trace_record(record, index, sequences))
        modes.add(record.get("probe_mode"))

        if record.get("baseline_trigger_rank") and record.get("baseline_skipped_moves"):
            exposed_count += 1
            bucket = depth_stratum(record["depth"])
            if bucket:
                strata[
                    f"{bucket}|improving={str(bool(record['improving'])).lower()}"
                ] += 1

        if oracle_sample_count > 0:
            rank = int.from_bytes(
                hashlib.sha256(
                    f"{record.get('node_key')}:{record.get('fen')}".encode("ascii")
                ).digest(),
                "big",
            )
            # A min-heap over negative rank/index keeps the worst selected
            # record at slot zero.  The index preserves sorted()'s stable tie
            # behavior without ever comparing the record dictionaries.
            candidate = (-rank, -index, record)
            if len(oracle_heap) < oracle_sample_count:
                heapq.heappush(oracle_heap, candidate)
            elif candidate > oracle_heap[0]:
                heapq.heapreplace(oracle_heap, candidate)

    oracle_sample = [
        entry[2]
        for entry in sorted(oracle_heap, key=lambda entry: (-entry[0], -entry[1]))
    ]
    return {
        "records": record_count,
        "exposed": exposed_count,
        "modes": modes,
        "strata": strata,
        "structural_errors": structure_errors,
        "oracle_sample": oracle_sample,
    }


def score_trace_stream(
    trace_path: Path,
    root_fens: set[str],
    policies: list[str],
    total_primary_nodes: int,
) -> dict[str, dict]:
    """Score all policies in one streaming pass over the authenticated trace."""
    counters_by_policy = {policy: collections.Counter() for policy in policies}
    per_root: dict[str, dict[str, collections.Counter]] = collections.defaultdict(
        lambda: {policy: collections.Counter() for policy in policies}
    )

    for record in iter_trace(trace_path):
        if record.get("root_fen") not in root_fens:
            continue
        thresholds = policy_thresholds(record)
        baseline_skipped = set(record["baseline_skipped_moves"])
        for move in record["tail"]:
            baseline_keep = retained("baseline", move, thresholds, baseline_skipped)
            critical = (
                not record["baseline_cutoff"]
                and not move["pruned_by_rest"]
                and isinstance(move["value"], int)
                and move["value"] > record["best_after"]
            )
            for policy in policies:
                keep = retained(policy, move, thresholds, baseline_skipped)
                counters = counters_by_policy[policy]
                counters["moves"] += 1
                counters["retained"] += int(keep)
                counters["critical"] += int(critical)
                counters["critical_retained"] += int(critical and keep)
                if not move["pruned_by_rest"]:
                    counters["shadow_nodes_retained"] += move["nodes"] * int(keep)
                    counters["signed_nodes_vs_baseline"] += move["nodes"] * (
                        int(keep) - int(baseline_keep)
                    )
                root_counter = per_root[record["root_fen"]][policy]
                root_counter["moves"] += 1
                root_counter["retained"] += int(keep)
                root_counter["critical"] += int(critical)
                root_counter["critical_retained"] += int(critical and keep)

    metrics: dict[str, dict] = {}
    for policy in policies:
        counters = counters_by_policy[policy]
        root_retention = []
        root_recall = []
        for root_metrics in per_root.values():
            counter = root_metrics[policy]
            if counter["moves"]:
                root_retention.append(counter["retained"] / counter["moves"])
            if counter["critical"]:
                root_recall.append(counter["critical_retained"] / counter["critical"])
        metrics[policy] = {
            **dict(counters),
            "class_retained_node_weighted": (
                counters["retained"] / counters["moves"] if counters["moves"] else None
            ),
            "critical_recall_node_weighted": (
                counters["critical_retained"] / counters["critical"]
                if counters["critical"]
                else None
            ),
            "class_retained_root_weighted": (
                sum(root_retention) / len(root_retention) if root_retention else None
            ),
            "critical_recall_root_weighted": (
                sum(root_recall) / len(root_recall) if root_recall else None
            ),
            "signed_shadow_work_vs_baseline_over_primary": (
                counters["signed_nodes_vs_baseline"] / total_primary_nodes
                if total_primary_nodes
                else None
            ),
        }
    return metrics


def analyze(args: argparse.Namespace) -> int:
    trace_path = require_file(Path(args.trace), "trace")
    roots_path = require_file(Path(args.roots), "root manifest")
    collection_path = require_file(Path(args.collection), "collection receipt")
    repeat_path = require_file(Path(args.repeat_trace), "repeat trace") if args.repeat_trace else None
    repeat_collection_path = (
        require_file(Path(args.repeat_collection), "repeat collection receipt")
        if args.repeat_collection
        else None
    )
    _, roots = roots_from_manifest(roots_path, args.split, args.limit)
    root_fens = {root["fen"] for root in roots}
    trace_summary = scan_trace_for_analysis(trace_path, root_fens, args.oracle_sample)
    record_count = trace_summary["records"]
    exposed_count = trace_summary["exposed"]
    record_modes = trace_summary["modes"]
    strata = trace_summary["strata"]
    structure_errors = trace_summary["structural_errors"]
    if not record_count:
        structure_errors.append("selected split has zero trace records")
    probe_mode = next(iter(record_modes)) if len(record_modes) == 1 else None
    if probe_mode not in ("baseline", "u34"):
        structure_errors.append(
            f"trace must contain exactly one supported probe mode, got {sorted(map(str, record_modes))}"
        )
    oracle_checked, oracle_errors = validate_trace_oracles(
        trace_summary["oracle_sample"], args.oracle_sample
    )
    structure_errors.extend(oracle_errors)

    full_contract = analysis_parameters_frozen(args, len(roots))
    if not full_contract:
        structure_errors.append("analysis parameters differ from the frozen full-split gate")

    repeat_identical = (
        repeat_path is not None and files_byte_identical(trace_path, repeat_path)
    )
    if repeat_path is None or repeat_path.resolve() == trace_path.resolve():
        structure_errors.append("repeat trace must be a distinct file")
    repeat_record_count = record_count if repeat_identical else 0
    collection = load_json(collection_path)
    structure_errors.extend(
        validate_collection_contract(
            collection,
            collection_path,
            trace_path,
            record_count,
            roots_path,
            root_fens,
            args.split,
            probe_mode,
        )
    )
    if not isinstance(collection, dict):
        collection = {}
    repeat_collection = (
        load_json(repeat_collection_path) if repeat_collection_path is not None else None
    )
    if repeat_collection_path is None or repeat_collection_path.resolve() == collection_path.resolve():
        structure_errors.append("repeat collection receipt must be a distinct file")
    elif repeat_path is not None:
        structure_errors.extend(
            validate_collection_contract(
                repeat_collection,
                repeat_collection_path,
                repeat_path,
                repeat_record_count,
                roots_path,
                root_fens,
                args.split,
                probe_mode,
            )
        )
    if isinstance(collection, dict) and isinstance(repeat_collection, dict):
        if collection.get("engine", {}).get("sha256") != repeat_collection.get("engine", {}).get(
            "sha256"
        ):
            structure_errors.append("primary and repeat receipts use different trace engines")
        first_transcript = Path(collection.get("transcript", {}).get("path", "")).resolve()
        second_transcript = Path(repeat_collection.get("transcript", {}).get("path", "")).resolve()
        if first_transcript == second_transcript:
            structure_errors.append("primary and repeat receipts reuse one UCI transcript")
    primary_nodes = {
        item["fen"]: item["nodes"]
        for item in collection.get("results", [])
        if item.get("fen") in root_fens
    }

    policies = list(BASELINE_POLICIES if probe_mode == "baseline" else U34_POLICIES)
    if probe_mode not in ("baseline", "u34"):
        policies = []
    total_primary_nodes = sum(primary_nodes.values())
    if len(primary_nodes) != len(roots) or total_primary_nodes <= 0:
        structure_errors.append(
            f"primary-node receipt covers {len(primary_nodes)}/{len(roots)} selected roots"
        )
    metrics = score_trace_stream(trace_path, root_fens, policies, total_primary_nodes)

    pareto = []
    eligible = [
        policy
        for policy in policies
        if metrics[policy]["critical_recall_node_weighted"] is not None
        and metrics[policy]["signed_shadow_work_vs_baseline_over_primary"] is not None
    ]
    for policy in eligible:
        recall = metrics[policy]["critical_recall_node_weighted"]
        work = metrics[policy]["signed_shadow_work_vs_baseline_over_primary"]
        dominated = False
        for other in eligible:
            if other == policy:
                continue
            other_recall = metrics[other]["critical_recall_node_weighted"]
            other_work = metrics[other]["signed_shadow_work_vs_baseline_over_primary"]
            if other_recall >= recall and other_work <= work and (
                other_recall > recall or other_work < work
            ):
                dominated = True
                break
        if not dominated:
            pareto.append(policy)

    baseline_exact = not any("rank simulation" in error for error in structure_errors)
    no_lmp_exact = (
        metrics.get("no-LMP", {}).get("class_retained_node_weighted") == 1.0
        if probe_mode == "baseline"
        else None
    )
    stratum_gate = all(
        strata[f"{depth}|improving={value}"] >= args.min_per_stratum
        for depth in ("4-5", "6-8", "9-12")
        for value in ("false", "true")
    )
    gate_pass = (
        not structure_errors
        and repeat_identical
        and exposed_count >= args.min_exposed
        and stratum_gate
        and baseline_exact
        and (no_lmp_exact is True if probe_mode == "baseline" else True)
    )
    report = {
        "schema": SCHEMA_ANALYSIS,
        "split": args.split,
        "probe_mode": probe_mode,
        "pass": gate_pass,
        "inputs": {
            "trace_sha256": sha256_file(trace_path),
            "repeat_trace_sha256": sha256_file(repeat_path) if repeat_path else None,
            "roots_sha256": sha256_file(roots_path),
            "collection_sha256": sha256_file(collection_path),
            "repeat_collection_sha256": (
                sha256_file(repeat_collection_path) if repeat_collection_path else None
            ),
        },
        "counts": {
            "records": record_count,
            "baseline_exposed": exposed_count,
            "dual_oracle_records": oracle_checked,
            "roots_with_primary_nodes": len(primary_nodes),
            "primary_nodes": total_primary_nodes,
            "strata": dict(sorted(strata.items())),
        },
        "gates": {
            "structural_errors": structure_errors,
            "full_frozen_contract": full_contract,
            "repeat_byte_identical": repeat_identical,
            "baseline_skip_simulation_exact": baseline_exact,
            "no_lmp_retains_class": no_lmp_exact,
            "min_exposed": args.min_exposed,
            "min_per_stratum": args.min_per_stratum,
            "strata_pass": stratum_gate,
        },
        "critical_definition": (
            "shadow move survives downstream pruning, baseline node did not cut off, "
            "and shadow value exceeds the baseline node's final value"
        ),
        "metrics": metrics,
        "pareto_policies": pareto,
        "official_sprt_matrix_unchanged": ["U2", "D4", "U3/4"],
    }
    canonical_json(Path(args.out).resolve(), report)
    print(
        f"analysis {'PASS' if gate_pass else 'STOP'}: records {record_count}, "
        f"baseline-exposed {exposed_count}, repeat={repeat_identical}, "
        f"strata={dict(sorted(strata.items()))}"
    )
    print(f"Pareto screen: {', '.join(pareto) if pareto else 'none'}")
    return 0 if gate_pass else 1


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest="command", required=True)

    roots = sub.add_parser("make-roots", help="freeze the 128/128 dual-oracle root manifest")
    roots.add_argument("--source", default=str(ROOT / "data" / "c3_final.bin"))
    roots.add_argument("--book", default=str(ROOT / "books" / "tera_openings_v1.epd"))
    roots.add_argument("--out", default=str(HERE / "lmp_shadow_roots_v1.json"))
    roots.add_argument("--seed", type=int, default=2026081201)
    roots.add_argument("--count", type=int, default=256)
    roots.add_argument("--dev-count", type=int, default=128)
    roots.add_argument("--min-ply", type=int, default=12)
    roots.add_argument("--max-ply", type=int, default=700)
    roots.add_argument("--min-record-gap", type=int, default=1201)
    roots.add_argument("--max-attempts", type=int, default=100000)
    roots.set_defaults(func=make_roots)

    collection = sub.add_parser("collect", help="collect an authenticated trace")
    collection.add_argument("--engine", required=True)
    collection.add_argument("--net", default=str(ROOT / "data" / "tera-net2.tnn"))
    collection.add_argument("--roots", default=str(HERE / "lmp_shadow_roots_v1.json"))
    collection.add_argument("--split", choices=("development", "holdout", "all"), required=True)
    collection.add_argument("--mode", choices=("baseline", "u34"), default="baseline")
    collection.add_argument("--nodes", type=int, required=True)
    collection.add_argument("--every", type=int, default=128)
    collection.add_argument("--max-records", type=int, default=20000)
    collection.add_argument("--limit", type=int)
    collection.add_argument("--trace", required=True)
    collection.add_argument("--out", required=True)
    collection.add_argument("--transcript")
    collection.add_argument("--timeout", type=int, default=43200)
    collection.add_argument("--overwrite", action="store_true")
    collection.set_defaults(func=collect)

    controls = sub.add_parser("verify-controls", help="prove trace non-interference")
    controls.add_argument("--normal-engine", required=True)
    controls.add_argument("--trace-engine", required=True)
    controls.add_argument("--net", default=str(ROOT / "data" / "tera-net2.tnn"))
    controls.add_argument("--roots", default=str(HERE / "lmp_shadow_roots_v1.json"))
    controls.add_argument("--split", choices=("development", "holdout"), default="development")
    controls.add_argument("--root-count", type=int, default=2)
    controls.add_argument("--nodes", type=int, default=20000)
    controls.add_argument("--max-records", type=int, default=12)
    controls.add_argument("--timeout", type=int, default=3600)
    controls.add_argument("--out", required=True)
    controls.set_defaults(func=verify_controls)

    analysis = sub.add_parser("analyze", help="score frozen policies and enforce holdout gates")
    analysis.add_argument("--trace", required=True)
    analysis.add_argument("--repeat-trace", required=True)
    analysis.add_argument("--collection", required=True)
    analysis.add_argument("--repeat-collection", required=True)
    analysis.add_argument("--roots", default=str(HERE / "lmp_shadow_roots_v1.json"))
    analysis.add_argument("--split", choices=("development", "holdout"), required=True)
    analysis.add_argument("--limit", type=int, help="pilot only; frozen gates use the full split")
    analysis.add_argument("--min-exposed", type=int, default=FROZEN_MIN_EXPOSED)
    analysis.add_argument("--min-per-stratum", type=int, default=FROZEN_MIN_PER_STRATUM)
    analysis.add_argument("--oracle-sample", type=int, default=FROZEN_ORACLE_SAMPLE)
    analysis.add_argument("--out", required=True)
    analysis.set_defaults(func=analyze)
    return command


def main() -> int:
    try:
        args = parser().parse_args()
        return args.func(args)
    except (HarnessError, OSError, subprocess.TimeoutExpired) as error:
        print(f"LMP HARNESS ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
