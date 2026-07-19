#!/usr/bin/env python3
"""Round-trip terabin against the Terachess oracle over a random game.

For every position of a deterministic 40-ply random game from the start
position: oracle FEN -> from_fen -> pack -> unpack -> to_fen must equal the
oracle's canonical FEN field by field, and pack(unpack(x)) == x.

e.p. emission policy: the oracle's canonical emitter (TERACHESS_SPEC.md 3.2)
prints the e.p. square only when a side-to-move Pawn can actually capture on
it; terabin.to_fen() applies the same filter over the raw stored square, so
the comparison below is exact string equality with no normalization step.
Directed cases at the end pin that alignment (pawn double step capturable,
prince double step not pawn-capturable, king-leap rights loss, promotion).

Runs with plain python; exit 0 on success, 1 on any mismatch.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(REPO_ROOT / "oracle"))

import terabin
import terachess

FIELD_NAMES = ("board", "turn", "king-jump rights", "ep", "halfmove",
               "fullmove")
# oracle move kind -> tera-bin move type (double step is a normal move).
KIND_TO_TERA = {0: 0, 1: 0, 2: 1, 3: 2}


def encode_oracle_move(white_to_move: bool, mv) -> int:
    frm, to, promo, kind = mv
    promo_code = 0
    if promo:
        idx = ord(promo) - 97
        promo_code = (1 + idx) if white_to_move else (27 + idx)
    return terabin.pack_move(frm, to, promo_code, KIND_TO_TERA[kind])


def check_position(pos, ply: int, move_field: int, score: int, result: int,
                   errors: list[str], tag: str) -> None:
    fen = pos.to_fen()
    record = terabin.from_fen(fen, score=score, move=move_field, ply=ply,
                              result=result)
    raw = terabin.pack(record)
    record2 = terabin.unpack(raw)
    if record2 != record:
        errors.append(f"{tag}: unpack(pack(record)) != record")
    if terabin.pack(record2) != raw:
        errors.append(f"{tag}: pack(unpack(bytes)) != bytes")
    got = (record2.score, record2.move, record2.ply, record2.result)
    if got != (score, move_field, ply, result):
        errors.append(f"{tag}: meta targets did not survive: {got}")
    fen2 = terabin.to_fen(record2)
    if fen2 != fen:
        oracle_fields = fen.split(" ")
        terabin_fields = fen2.split(" ")
        for name, x, y in zip(FIELD_NAMES, oracle_fields, terabin_fields):
            if x != y:
                errors.append(f"{tag}: field {name}: oracle={x!r} "
                              f"terabin={y!r}")
        if len(oracle_fields) != len(terabin_fields):
            errors.append(f"{tag}: field count {len(oracle_fields)} vs "
                          f"{len(terabin_fields)}")


def random_game(errors: list[str], plies: int = 40) -> int:
    rng = random.Random(0x7E4AC7E5)
    pos = terachess.Position.from_fen(terachess.START_FEN)
    checked = 0
    for ply in range(plies + 1):
        legal = pos._legal()
        if legal and ply < plies:
            uci, mv = legal[rng.randrange(len(legal))]
            move_field = encode_oracle_move(pos.white_to_move, mv)
        else:
            uci, move_field = None, 0
        score = rng.randint(-800, 800)
        result = rng.randrange(4)
        check_position(pos, ply, move_field, score, result, errors,
                       f"ply {ply}")
        checked += 1
        if uci is None:
            break
        pos = pos.apply(uci)
    return checked


def directed_cases(errors: list[str]) -> int:
    checked = 0

    # Pawn double step: capturable e.p. square must round-trip as "d9".
    pa = terachess.Position.from_fen(terachess._mk_fen(
        {"d8": "P", "e10": "p", "h2": "K", "h15": "k"}, hm=7))
    pa1 = pa.apply("d8d10")
    if " d9 " not in pa1.to_fen():
        errors.append("directed: oracle did not emit capturable e.p.")
    check_position(pa1, 1, 0, 15, 3, errors, "directed pawn-ep")
    checked += 1
    # ... and the e.p. capture itself.
    check_position(pa1.apply("e10d9"), 2,
                   terabin.pack_move(terachess.parse_sq("e10"),
                                     terachess.parse_sq("d9"), 0, 1),
                   -25, 3, errors, "directed after-ep-capture")
    checked += 1

    # Prince double step with only an enemy prince nearby: the oracle omits
    # the e.p. square (no pawn can capture); terabin must agree.
    pj = terachess.Position.from_fen(terachess._mk_fen(
        {"d8": "P", "e10": "i", "h2": "K", "h15": "k"}))
    pj1 = pj.apply("d8d10")
    if pj1.ep is None:
        errors.append("directed: oracle lost the raw e.p. state")
    check_position(pj1, 1, 0, 0, 3, errors, "directed prince-no-ep")
    checked += 1

    # King leap: rights Kk -> k after h2j3; both sides round-trip.
    pk = terachess.Position.from_fen(terachess._mk_fen(
        {"h2": "K", "h15": "k"}, rights="Kk"))
    check_position(pk, 0,
                   terabin.pack_move(terachess.parse_sq("h2"),
                                     terachess.parse_sq("j3"), 0, 2),
                   0, 1, errors, "directed king-leap-before")
    checked += 1
    check_position(pk.apply("h2j3"), 1, 0, 0, 1, errors,
                   "directed king-leap-after")
    checked += 1

    # Promotion: knight to Buffalo, with the promotion code in the move.
    pn = terachess.Position.from_fen(terachess._mk_fen(
        {"g14": "N", "a1": "K", "p1": "k"}))
    promo_code = terabin.letter_to_code("F")
    check_position(pn, 10,
                   terabin.pack_move(terachess.parse_sq("g14"),
                                     terachess.parse_sq("h16"), promo_code, 0),
                   120, 2, errors, "directed promotion-before")
    checked += 1
    check_position(pn.apply("g14h16f"), 11, 0, -120, 0, errors,
                   "directed promotion-after")
    checked += 1

    return checked


def main() -> int:
    errors: list[str] = []
    game_positions = random_game(errors)
    directed = directed_cases(errors)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"round-trip FAILED: {len(errors)} error(s) over "
              f"{game_positions + directed} positions")
        return 1
    print(f"round-trip OK: {game_positions} random-game positions + "
          f"{directed} directed cases, oracle FEN == terabin FEN field by "
          "field")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
