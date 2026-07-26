#!/usr/bin/env python3
"""Gate F1d: perft masivo motor vs oraculo sobre posiciones de random-walk.

Un solo proceso de motor para todas las posiciones (rapido). El oraculo A
genera las posiciones y calcula la referencia.

Uso: python mass_perft.py --engine ../src/stockfish.exe --positions 1000 --depth 2
"""
import argparse, random, subprocess, sys, time

from run_fixtures import load_impl


def engine_batch(engine, fens, depth):
    """Lanza un unico proceso y devuelve la lista de nodos por FEN."""
    cmds = []
    for fen in fens:
        cmds.append(f"position fen {fen}")
        cmds.append(f"go perft {depth}")
    cmds.append("quit")
    out = subprocess.run([engine], input="\n".join(cmds) + "\n",
                         capture_output=True, text=True, timeout=7200).stdout
    totals = []
    for line in out.splitlines():
        if "searched" in line.lower():
            totals.append(int("".join(c for c in line.split(":")[-1] if c.isdigit())))
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--positions", type=int, default=1000)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--seed", type=int, default=31337)
    ap.add_argument("--max-plies", type=int, default=200)
    args = ap.parse_args()

    A = load_impl("a")
    rng = random.Random(args.seed)
    t0 = time.time()

    fens, refs = [], []
    pos = A.Position.from_fen(A.START_FEN)
    plies = 0
    while len(fens) < args.positions:
        mvs = pos.legal_moves()
        if not mvs or plies >= args.max_plies:
            pos, plies = A.Position.from_fen(A.START_FEN), 0
            continue
        pos = pos.apply(rng.choice(mvs))
        plies += 1
        if plies >= 4:                      # evita la ventana simetrica inicial
            fens.append(pos.to_fen())
    print(f"posiciones generadas: {len(fens)} ({time.time()-t0:.0f}s)")

    t1 = time.time()
    for i, fen in enumerate(fens):
        refs.append(A.perft(A.Position.from_fen(fen), args.depth))
        if (i + 1) % 200 == 0:
            print(f"  oraculo {i+1}/{len(fens)} ({time.time()-t1:.0f}s)")
    print(f"oraculo listo ({time.time()-t1:.0f}s)")

    t2 = time.time()
    got = engine_batch(args.engine, fens, args.depth)
    print(f"motor listo ({time.time()-t2:.0f}s)")

    if len(got) != len(refs):
        print(f"ERROR: motor devolvio {len(got)} resultados, esperados {len(refs)}")
        return 2
    fails = 0
    for fen, want, have in zip(fens, refs, got):
        if want != have:
            fails += 1
            print(f"FAIL perft{args.depth} oraculo={want} motor={have}\n  {fen}")
            if fails >= 15:
                print("(cortado a 15)")
                break
    total_nodes = sum(refs)
    print(f"perft masivo: {len(fens)} posiciones d={args.depth} | "
          f"{total_nodes} nodos hoja | discrepancias: {fails} | {time.time()-t0:.0f}s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
