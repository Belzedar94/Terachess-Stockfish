#!/usr/bin/env python3
"""Fail-closed audit for verbose OpenBench Terachess PGN evidence.

The tool authenticates the book/PGN bytes, checks variant and clock evidence,
finds complete colour-swapped pairs despite OpenBench concurrency overshoot,
and can replay every move through both independent rule oracles.
"""

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path


COMMENT = re.compile(
    r"^(book|([+-]?M?\d+(?:\.\d+)?)\s+(\d+)/(\d+)\s+(\d+)\s+(\d+))$"
)
MOVE_COMMENT = re.compile(
    r"\s*(?:\d+\.\s+)?([a-zA-Z0-9+=#@,-]+)\s+\{\s*([^}]*)\s*\}"
)
RESULT = re.compile(r"(1-0|0-1|1/2-1/2|\*)\s*$")


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def nearest_rank(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def parse_tc(value):
    base, increment = value.split("+", 1)
    return round(float(base) * 1000), round(float(increment) * 1000)


def parse_headers(text):
    return dict(re.findall(r'^\[([^ ]+) "(.*)"\]$', text, re.MULTILINE))


def parse_games(pgn_text, book, expected_variant, canonical_tc):
    canonical_base, canonical_increment = parse_tc(canonical_tc)
    blocks = [
        block for block in re.split(r"(?=^\[Event )", pgn_text, flags=re.MULTILINE)
        if block.strip()
    ]
    games = []
    all_times = []

    for index, block in enumerate(blocks, start=1):
        try:
            header_text, move_text = block.split("\n\n", 1)
        except ValueError as error:
            raise ValueError("game %d has no header/movetext separator" % index) from error
        headers = parse_headers(header_text)
        required = {
            "White", "Black", "Result", "FEN", "TimeControl", "Variant",
            "ScaleFactor",
        }
        missing = sorted(required - headers.keys())
        if missing:
            raise ValueError("game %d lacks headers: %s" % (index, ", ".join(missing)))

        parsed_moves = MOVE_COMMENT.findall(move_text)
        if not parsed_moves:
            raise ValueError("game %d contains no commented moves" % index)
        moves = []
        scores = []
        times = []
        timed_by_color = {"w": [], "b": []}
        side = headers["FEN"].split()[1]
        current = side
        malformed = []
        for move, raw_comment in parsed_moves:
            comment = raw_comment.strip()
            match = COMMENT.fullmatch(comment)
            if not match:
                malformed.append(comment)
            elif match.group(1) != "book":
                scores.append(match.group(2))
                milliseconds = int(match.group(5))
                times.append(milliseconds)
                timed_by_color[current].append(milliseconds)
            moves.append(move)
            current = "b" if current == "w" else "w"
        all_times.extend(times)

        actual_base, actual_increment = parse_tc(headers["TimeControl"])
        remaining = {}
        for color in ("w", "b"):
            count = len(timed_by_color[color])
            spent = sum(timed_by_color[color])
            remaining[color] = {
                "moves": count,
                "spent_ms": spent,
                "canonical_lower_bound_ms": (
                    canonical_base + canonical_increment * count - spent - count
                ),
                "scaled_header_lower_bound_ms": (
                    actual_base + actual_increment * count - spent - count
                ),
            }

        trailing = RESULT.search(move_text)
        games.append({
            "index": index,
            "round": headers.get("Round"),
            "white": headers["White"],
            "black": headers["Black"],
            "result": headers["Result"],
            "trailing_result": trailing.group(1) if trailing else None,
            "fen": headers["FEN"],
            "fen_in_book": headers["FEN"] in book,
            "variant": headers["Variant"],
            "variant_matches": headers["Variant"] == expected_variant,
            "time_control": headers["TimeControl"],
            "scale_factor": headers["ScaleFactor"],
            "game_end_time": headers.get("GameEndTime"),
            "termination": headers.get("Termination"),
            "plies": len(moves),
            "timed_comments": len(times),
            "malformed_comments": malformed,
            "max_move_ms": max(times),
            "last_scores": scores[-8:],
            "remaining": remaining,
            "_moves": moves,
            "_times": times,
        })

    return games, all_times


def find_pairs(games):
    groups = defaultdict(list)
    for game in games:
        groups[game["fen"]].append(game)
    pairs = []
    for fen, grouped in groups.items():
        for left_index in range(len(grouped)):
            for right_index in range(left_index + 1, len(grouped)):
                left = grouped[left_index]
                right = grouped[right_index]
                if left["white"] == right["black"] and left["black"] == right["white"]:
                    pairs.append({
                        "fen_sha256": sha256(fen.encode("ascii")),
                        "game_indexes": [left["index"], right["index"]],
                        "rounds": [left["round"], right["round"]],
                        "results": [left["result"], right["result"]],
                    })
    return pairs


def clock_summary(games, times):
    if not games or not times:
        return {
            "samples": len(times),
            "p50_ms_nearest_rank": None,
            "p95_ms_nearest_rank": None,
            "p99_ms_nearest_rank": None,
            "max_ms": None,
            "min_canonical_lower_bound_ms": None,
            "min_scaled_header_lower_bound_ms": None,
        }
    return {
        "samples": len(times),
        "p50_ms_nearest_rank": nearest_rank(times, 0.50),
        "p95_ms_nearest_rank": nearest_rank(times, 0.95),
        "p99_ms_nearest_rank": nearest_rank(times, 0.99),
        "max_ms": max(times),
        "min_canonical_lower_bound_ms": min(
            values["canonical_lower_bound_ms"]
            for game in games for values in game["remaining"].values()
        ),
        "min_scaled_header_lower_bound_ms": min(
            values["scaled_header_lower_bound_ms"]
            for game in games for values in game["remaining"].values()
        ),
    }


def replay(games, oracle_dir, allow_mate_adjudication):
    sys.path.insert(0, str(oracle_dir))
    import terachess as oracle_a
    import terachess_b as oracle_b

    started = time.monotonic()
    evidence = {
        "move_checks": 0,
        "legal_set_checks": 0,
        "terminal_games": 0,
        "adjudicated_nonterminal_games": 0,
        "illegal_moves": 0,
        "oracle_mismatches": 0,
        "games": [],
    }
    for game in games:
        a = oracle_a.Position.from_fen(game["fen"])
        b = oracle_b.Position.from_fen(game["fen"])
        if a.to_fen() != game["fen"] or b.to_fen() != game["fen"]:
            raise RuntimeError("noncanonical opening FEN in game %d" % game["index"])
        for ply, move in enumerate(game["_moves"], start=1):
            legal_a = set(a.legal_moves())
            legal_b = set(b.legal_moves())
            evidence["legal_set_checks"] += 1
            if legal_a != legal_b:
                evidence["oracle_mismatches"] += 1
                raise RuntimeError(
                    "oracle legal-set mismatch game=%d ply=%d" % (game["index"], ply)
                )
            if move not in legal_a:
                evidence["illegal_moves"] += 1
                raise RuntimeError(
                    "illegal move game=%d ply=%d move=%s"
                    % (game["index"], ply, move)
                )
            a = a.apply(move)
            b = b.apply(move)
            evidence["move_checks"] += 1
        if a.to_fen() != b.to_fen() or a.result() != b.result():
            evidence["oracle_mismatches"] += 1
            raise RuntimeError("final oracle mismatch in game %d" % game["index"])
        oracle_result = a.result()
        if oracle_result == "*":
            evidence["adjudicated_nonterminal_games"] += 1
            classification = "win-adjudication"
            if not allow_mate_adjudication or not any(
                "M" in score for score in game["last_scores"]
            ):
                raise RuntimeError(
                    "nonterminal game %d lacks allowed mate adjudication"
                    % game["index"]
                )
        else:
            evidence["terminal_games"] += 1
            classification = "oracle-terminal"
            if oracle_result != game["result"]:
                raise RuntimeError("terminal result mismatch in game %d" % game["index"])
        evidence["games"].append({
            "index": game["index"],
            "classification": classification,
            "oracle_result": oracle_result,
        })
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return evidence


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", required=True, type=Path)
    parser.add_argument("--book", required=True, type=Path)
    parser.add_argument("--variant", default="terachess")
    parser.add_argument("--canonical-tc", required=True)
    parser.add_argument("--max-plies", type=int, default=1200)
    parser.add_argument("--min-complete-pairs", type=int, default=1)
    parser.add_argument("--replay-oracles", action="store_true")
    parser.add_argument("--oracle-dir", type=Path)
    parser.add_argument("--allow-mate-adjudication", action="store_true")
    parser.add_argument("--include-games", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    pgn_bytes = args.pgn.read_bytes()
    book_bytes = args.book.read_bytes()
    book_lines = args.book.read_text(encoding="utf-8").splitlines()
    games, times = parse_games(
        pgn_bytes.decode("utf-8"), set(book_lines), args.variant, args.canonical_tc
    )
    pairs = find_pairs(games)
    pair_indexes = {index for pair in pairs for index in pair["game_indexes"]}
    pair_games = [game for game in games if game["index"] in pair_indexes]
    pair_times = [value for game in pair_games for value in game["_times"]]

    checks = {
        "games_present": bool(games),
        "all_fens_in_book": all(game["fen_in_book"] for game in games),
        "all_variants_match": all(game["variant_matches"] for game in games),
        "all_results_match": all(
            game["result"] == game["trailing_result"] for game in games
        ),
        "all_comments_verbose": all(
            not game["malformed_comments"] for game in games
        ),
        "all_below_max_plies": all(game["plies"] < args.max_plies for game in games),
        "complete_pairs": len(pairs) >= args.min_complete_pairs,
    }
    clock = clock_summary(games, times)
    pair_clock = clock_summary(pair_games, pair_times)
    checks["canonical_clocks_positive"] = (
        clock["min_canonical_lower_bound_ms"] is not None
        and clock["min_canonical_lower_bound_ms"] > 0
    )
    checks["scaled_clocks_positive"] = (
        clock["min_scaled_header_lower_bound_ms"] is not None
        and clock["min_scaled_header_lower_bound_ms"] > 0
    )

    oracle = None
    if args.replay_oracles:
        oracle_dir = args.oracle_dir or Path(__file__).resolve().parents[1] / "oracle"
        oracle = replay(games, oracle_dir, args.allow_mate_adjudication)
        checks["oracle_replay"] = (
            oracle["illegal_moves"] == 0 and oracle["oracle_mismatches"] == 0
        )

    report = {
        "schema": "terachess-openbench-pgn-audit-v1",
        "inputs": {
            "pgn": str(args.pgn.resolve()),
            "pgn_bytes": len(pgn_bytes),
            "pgn_sha256": sha256(pgn_bytes),
            "book": str(args.book.resolve()),
            "book_bytes": len(book_bytes),
            "book_sha256": sha256(book_bytes),
            "book_lines": len(book_lines),
            "book_unique_lines": len(set(book_lines)),
            "expected_variant": args.variant,
            "canonical_tc": args.canonical_tc,
            "max_plies": args.max_plies,
        },
        "games": len(games),
        "max_observed_plies": max(game["plies"] for game in games),
        "termination_headers": sum(game["termination"] is not None for game in games),
        "complete_pairs": pairs,
        "clock": clock,
        "complete_pair_clock": pair_clock,
        "oracle": oracle,
        "checks": checks,
        "pass": all(checks.values()),
    }
    if args.include_games:
        report["game_details"] = [
            {key: value for key, value in game.items() if not key.startswith("_")}
            for game in games
        ]
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
