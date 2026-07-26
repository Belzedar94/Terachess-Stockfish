#!/usr/bin/env python3
"""Gate F1: contrasta el motor UCI contra fixtures (listas de movimientos via
go perft 1 divide) y contra perft_refs.json.

Uso: python engine_check.py --engine ../src/stockfish.exe [--depth 3]
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def engine_perft(engine, fen, depth, want_moves=False):
    inp = f"position fen {fen}\ngo perft {depth}\nquit\n"
    out = subprocess.run([engine], input=inp, capture_output=True, text=True,
                         timeout=1200).stdout
    total, moves = None, {}
    for line in out.splitlines():
        m = re.match(r"^([a-p]\d+[a-p]\d+[a-z]?):\s*(\d+)\s*$", line.strip())
        if m:
            moves[m.group(1)] = int(m.group(2))
        if "searched" in line.lower():
            total = int(re.sub(r"\D", "", line.split(":")[-1]))
    return total, moves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--depth", type=int, default=3)
    args = ap.parse_args()
    fails = 0
    t0 = time.time()

    # 1. Fixtures con listas de movimientos exactas
    with open(os.path.join(HERE, "fixtures", "fixtures.json"), encoding="utf-8") as f:
        fixtures = json.load(f)
    n_moves_fx = 0
    for fx in fixtures:
        if "moves" in fx:
            n_moves_fx += 1
            total, moves = engine_perft(args.engine, fx["fen"], 1)
            got = sorted(moves.keys())
            want = sorted(fx["moves"])
            if got != want:
                fails += 1
                print(f"FAIL fixture {fx['name']}:")
                print(f"  faltan en motor: {[m for m in want if m not in got]}")
                print(f"  sobran en motor: {[m for m in got if m not in want]}")
        elif "perft" in fx:
            for d, want in fx["perft"].items():
                total, _ = engine_perft(args.engine, fx["fen"], int(d))
                if total != want:
                    fails += 1
                    print(f"FAIL fixture {fx['name']} perft{d}: want {want} got {total}")
    print(f"fixtures vs motor: {n_moves_fx} con listas + resto perft | fallos acumulados: {fails}")

    # 2. Referencias de perft
    with open(os.path.join(HERE, "perft_refs.json"), encoding="utf-8") as f:
        refs = json.load(f)
    for r in refs:
        for d, want in r["perft"].items():
            if int(d) > args.depth:
                continue
            total, _ = engine_perft(args.engine, r["fen"], int(d))
            status = "OK" if total == want else "FAIL"
            if total != want:
                fails += 1
            print(f"  {status} {r['name']} d={d}: oracle {want} engine {total}")

    print(f"TOTAL fallos: {fails} | {time.time()-t0:.0f}s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
