#!/usr/bin/env python3
"""Gate F0: round-trip FEN sobre posiciones aleatorias del oraculo.

parse(dump(parse(x))) == parse(x) sobre N posiciones de random-walk.
Uso: python test_roundtrip.py [--positions 10000] [--impl a|b] [--seed S]
"""
import argparse, random, sys, time

from run_fixtures import load_impl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", type=int, default=10000)
    ap.add_argument("--impl", default="a", choices=["a", "b"])
    ap.add_argument("--seed", type=int, default=20260718)
    ap.add_argument("--max-plies", type=int, default=140)
    args = ap.parse_args()
    mod = load_impl(args.impl)
    rng = random.Random(args.seed)
    done, fails = 0, 0
    t0 = time.time()
    while done < args.positions:
        pos = mod.Position.from_fen(mod.START_FEN)
        for _ in range(args.max_plies):
            fen1 = pos.to_fen()
            fen2 = mod.Position.from_fen(fen1).to_fen()
            done += 1
            if fen1 != fen2:
                fails += 1
                print(f"FAIL: {fen1!r} -> {fen2!r}")
                if fails >= 10:
                    print("(cortado a 10)")
                    done = args.positions
                    break
            if done >= args.positions:
                break
            mvs = pos.legal_moves()
            if not mvs:
                break
            pos = pos.apply(rng.choice(mvs))
    print(f"round-trip: {done} posiciones, {fails} fallos, {time.time()-t0:.1f}s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
