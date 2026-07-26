#!/usr/bin/env python3
"""Synthetic tera-bin v1 generator -- valid training data without an engine.

Positions come from the Python oracle (``oracle/terachess.py``), so every
record is a genuinely legal Terachess position; the label is pure Zillions
material from the side to move (TERACHESS_SPEC.md section 8).  That makes the
file useful for two things and nothing else:

  * unit tests of the dataset / feature / parity plumbing, and
  * the end-to-end canary -- a network trained on material must learn
    material, which is a falsifiable statement about the whole pipeline.

Games start either from the initial position or from a *thinned* start
position (a random subset of the non-king pieces removed) so that all eight
output buckets, and the whole piece-count range, are represented.  Thinned
starts are re-drawn until the oracle agrees the position is legal.

Output passes ``tools/audit_terabin.py --strict`` unchanged.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from multiprocessing import Pool
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _p in (str(_ROOT / "tools"), str(_ROOT / "oracle")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import terabin                              # noqa: E402
import terachess                            # noqa: E402
from audit_terabin import PIECE_VALUE_CP    # noqa: E402

START_FEN = terabin.START_FEN
ADJUDICATE_CP = 500          # |material| above which a capped game is decided

# oracle move kind -> tera-bin move kind (docs/tera-bin-v1.md)
KIND_MAP = {0: 0, 1: 0, 2: 1, 3: 2}


def material_cp(board: list[str]) -> int:
    """Zillions material from White's point of view."""
    total = 0
    for piece in board:
        if piece == ".":
            continue
        value = PIECE_VALUE_CP[piece.lower()]
        total += value if piece.isupper() else -value
    return total


def thinned_start(rng: random.Random, keep_min: int = 4) -> str | None:
    """Start position minus a random subset of the non-king pieces."""
    board = terachess.Position.from_fen(START_FEN).board[:]
    movable = [sq for sq, p in enumerate(board) if p not in (".", "K", "k")]
    drop_fraction = rng.random() ** 0.7          # skew toward heavy thinning
    n_drop = int(len(movable) * drop_fraction)
    n_drop = min(n_drop, len(movable) - keep_min)
    for sq in rng.sample(movable, max(0, n_drop)):
        board[sq] = "."
    fen = "%s w Kk - 0 1" % terachess._board_rows(board)
    pos = terachess.Position.from_fen(fen)
    if terachess.attacked(pos.board, pos.bk, True):
        return None                              # side not to move in check
    if not pos.legal_moves():
        return None
    return fen


def play_game(rng: random.Random, max_plies: int, full_start_prob: float
              ) -> list[bytes]:
    """One random game; returns packed tera-bin records, ply starting at 0."""
    if rng.random() < full_start_prob:
        fen = START_FEN
    else:
        fen = None
        for _ in range(40):
            fen = thinned_start(rng)
            if fen:
                break
        if not fen:
            fen = START_FEN
    pos = terachess.Position.from_fen(fen)

    frames = []                 # (record fields without result, stm)
    outcome = None
    for ply in range(max_plies):
        legal = pos._legal()
        if not legal:
            outcome = ("0-1" if pos.white_to_move else "1-0") \
                if pos.in_check() else "1/2-1/2"
            break
        uci, mv = legal[rng.randrange(len(legal))]
        frm, to, promo, kind = mv
        white = pos.white_to_move
        promo_code = 0
        if promo:
            promo_code = terabin.letter_to_code(
                promo.upper() if white else promo)
        move = terabin.pack_move(frm, to, promo_code, KIND_MAP[kind])
        score = material_cp(pos.board)
        if not white:
            score = -score
        frames.append((pos.to_fen(), score, move, ply, 0 if white else 1))
        pos = pos._apply(mv)
        if pos.halfmove >= 100:
            outcome = "1/2-1/2"
            break
    if outcome is None:
        final = material_cp(pos.board)
        outcome = ("1-0" if final >= ADJUDICATE_CP else
                   "0-1" if final <= -ADJUDICATE_CP else "1/2-1/2")

    white_result = {"1-0": 2, "1/2-1/2": 1, "0-1": 0}[outcome]
    out = []
    for fen_s, score, move, ply, stm in frames:
        result = white_result if stm == 0 else 2 - white_result
        record = terabin.from_fen(fen_s, score=score, move=move, ply=ply,
                                  result=result)
        out.append(terabin.pack(record))
    return out


def _shard(args) -> tuple[str, int, int]:
    seed, target, max_plies, full_start_prob, path = args
    rng = random.Random(seed)
    written = games = 0
    with open(path, "wb") as handle:
        while written < target:
            records = play_game(rng, max_plies, full_start_prob)
            if not records:
                continue
            del records[max(1, target - written):]
            handle.write(b"".join(records))
            written += len(records)
            games += 1
    return path, written, games


def generate(out_path: Path, count: int, seed: int, max_plies: int,
             full_start_prob: float, workers: int) -> dict:
    start = time.time()
    workers = max(1, workers)
    per = [count // workers] * workers
    for i in range(count % workers):
        per[i] += 1
    shard_paths = [f"{out_path}.shard{i}" for i in range(workers)]
    jobs = [(seed * 1_000_003 + i, per[i], max_plies, full_start_prob,
             shard_paths[i]) for i in range(workers)]

    if workers == 1:
        results = [_shard(jobs[0])]
    else:
        with Pool(workers) as pool:
            results = pool.map(_shard, jobs)

    total = sum(r[1] for r in results)
    games = sum(r[2] for r in results)
    tmp = f"{out_path}.tmp"
    with open(tmp, "wb") as out:
        terabin.write_header(out, count=total, source_count=total)
        for path, _, _ in results:
            with open(path, "rb") as shard:
                while True:
                    chunk = shard.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, out_path)
    for path in shard_paths:
        os.unlink(path)

    seconds = time.time() - start
    size = os.path.getsize(out_path)
    assert size == terabin.HEADER_SIZE + total * terabin.RECORD_SIZE
    return {"records": total, "games": games, "seconds": seconds,
            "bytes": size, "records_per_second": total / max(seconds, 1e-9)}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="output tera-bin file")
    parser.add_argument("-n", "--count", type=int, default=40_000)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--max-plies", type=int, default=60)
    parser.add_argument("--full-start-prob", type=float, default=0.15,
                        help="share of games that start from the initial "
                             "position instead of a thinned one")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    info = generate(args.out, args.count, args.seed, args.max_plies,
                    args.full_start_prob, args.workers)
    print(f"wrote {info['records']:,} records from {info['games']:,} games "
          f"in {info['seconds']:.1f}s "
          f"({info['records_per_second']:,.0f} rec/s, "
          f"{info['bytes']:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
