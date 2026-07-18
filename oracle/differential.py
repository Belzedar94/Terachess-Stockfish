#!/usr/bin/env python3
"""Gate F0: test diferencial impl A (mailbox) vs impl B (bitboards).

Random-walk desde startpos con A; en cada posicion compara legal_moves()
de A y B, y el FEN tras aplicar el movimiento elegido en ambas.

Uso: python differential.py [--games N] [--max-plies M] [--seed S]
Exit 0 si 0 discrepancias.
"""
import argparse, json, random, sys, time

from run_fixtures import load_impl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--max-plies", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20260718)
    args = ap.parse_args()
    A, B = load_impl("a"), load_impl("b")
    rng = random.Random(args.seed)
    fails, positions = [], 0
    t0 = time.time()
    for g in range(args.games):
        pa = A.Position.from_fen(A.START_FEN)
        pb = B.Position.from_fen(B.START_FEN)
        for ply in range(args.max_plies):
            fa, fb = pa.to_fen(), pb.to_fen()
            if fa != fb:
                fails.append({"game": g, "ply": ply, "kind": "fen_divergence", "a": fa, "b": fb})
                break
            ma, mb = sorted(pa.legal_moves()), sorted(pb.legal_moves())
            positions += 1
            if ma != mb:
                fails.append({"game": g, "ply": ply, "fen": fa, "kind": "moves_divergence",
                              "a_only": [m for m in ma if m not in mb],
                              "b_only": [m for m in mb if m not in ma]})
                break
            if not ma:
                break
            mv = rng.choice(ma)
            try:
                pa, pb = pa.apply(mv), pb.apply(mv)
            except Exception as e:
                fails.append({"game": g, "ply": ply, "fen": fa, "move": mv,
                              "kind": "apply_error", "detail": repr(e)})
                break
        if len(fails) >= 20:
            print("(cortado a 20 fallos)")
            break
    print(f"partidas: {args.games} | posiciones comparadas: {positions} | "
          f"discrepancias: {len(fails)} | {time.time()-t0:.1f}s")
    for f in fails:
        print(json.dumps(f, ensure_ascii=False)[:600])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
