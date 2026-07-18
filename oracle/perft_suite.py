#!/usr/bin/env python3
"""Gate F0/F1: suite de perft de referencia.

Modos:
  generate  — calcula perft con impl A, verifica contra impl B, escribe refs
  check     — re-verifica una impl contra refs congeladas
  engine    — compara un motor UCI externo (go perft) contra refs [F1]

Uso: python perft_suite.py generate [--depth 3] [--quick]
     python perft_suite.py check --impl a|b
Exit 0 si todo coincide.
"""
import argparse, json, os, subprocess, sys, time

from run_fixtures import load_impl

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, "perft_refs.json")
POSITIONS = os.path.join(HERE, "perft_positions.json")


def load_positions():
    with open(POSITIONS, encoding="utf-8") as f:
        return json.load(f)


def compute(mod, fen, depth):
    return mod.perft(mod.Position.from_fen(fen), depth)


def cmd_generate(args):
    A, B = load_impl("a"), load_impl("b")
    positions = load_positions()
    refs, fails = [], []
    t0 = time.time()
    for p in positions:
        maxd = min(args.depth, p.get("max_depth", args.depth))
        if args.quick:
            maxd = min(maxd, 2)
        entry = {"name": p["name"], "fen": p["fen"], "perft": {}}
        for d in range(1, maxd + 1):
            na = compute(A, p["fen"], d)
            nb = compute(B, p["fen"], d)
            if na != nb:
                fails.append({"name": p["name"], "depth": d, "a": na, "b": nb})
                break
            entry["perft"][str(d)] = na
        refs.append(entry)
        print(f"  {p['name']}: {entry['perft']} ({time.time()-t0:.0f}s)")
    if fails:
        print("DISCREPANCIAS A vs B — refs NO escritas:")
        for f in fails:
            print(json.dumps(f))
        return 1
    with open(REFS, "w", encoding="utf-8") as f:
        json.dump(refs, f, indent=1)
    print(f"OK: {len(refs)} posiciones, refs escritas en {REFS} ({time.time()-t0:.1f}s)")
    return 0


def cmd_check(args):
    mod = load_impl(args.impl)
    with open(REFS, encoding="utf-8") as f:
        refs = json.load(f)
    fails = 0
    for r in refs:
        for d, want in r["perft"].items():
            got = compute(mod, r["fen"], int(d))
            if got != want:
                print(f"FAIL {r['name']} d={d}: want {want} got {got}")
                fails += 1
    print(f"check impl {args.impl}: {'OK' if not fails else str(fails)+' fallos'}")
    return 1 if fails else 0


def cmd_engine(args):
    """Compara un motor UCI (que soporte 'go perft N') contra refs."""
    with open(REFS, encoding="utf-8") as f:
        refs = json.load(f)
    fails = 0
    for r in refs:
        for d, want in r["perft"].items():
            if int(d) > args.depth:
                continue
            inp = f"position fen {r['fen']}\ngo perft {d}\nquit\n"
            out = subprocess.run([args.engine], input=inp, capture_output=True,
                                 text=True, timeout=600).stdout
            got = None
            for line in out.splitlines():
                if "searched" in line.lower() or line.startswith("Nodes"):
                    got = int("".join(ch for ch in line.split(":")[-1] if ch.isdigit()))
            if got != want:
                print(f"FAIL {r['name']} d={d}: want {want} engine {got}")
                fails += 1
    print(f"engine check: {'OK' if not fails else str(fails)+' fallos'}")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("--depth", type=int, default=3)
    g.add_argument("--quick", action="store_true")
    c = sub.add_parser("check"); c.add_argument("--impl", required=True, choices=["a", "b"])
    e = sub.add_parser("engine"); e.add_argument("--engine", required=True)
    e.add_argument("--depth", type=int, default=3)
    args = ap.parse_args()
    return {"generate": cmd_generate, "check": cmd_check, "engine": cmd_engine}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
