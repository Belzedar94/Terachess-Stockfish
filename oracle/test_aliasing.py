#!/usr/bin/env python3
"""Gate F0: pares de aliasing semantico (playbook doc 02, TERACHESS_SPEC §5).

Cada par: dos FEN con el mismo material/casillas pero distinto estado no-tablero.
Si el conjunto de movimientos legales NO difiere donde debe, hay aliasing.

Uso: python test_aliasing.py [--impl a|b]   (exit 0 = sin aliasing)
"""
import argparse, sys

from run_fixtures import load_impl

K_ONLY = "16/7k8/16/16/16/16/16/16/16/16/16/16/16/16/7K8/16"


def moves(mod, fen):
    return set(mod.Position.from_fen(fen).legal_moves())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", default="a", choices=["a", "b"])
    args = ap.parse_args()
    mod = load_impl(args.impl)
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'OK ' if cond else 'FAIL'} {name} {detail if not cond else ''}")
        if not cond:
            fails.append(name)

    # Par 1: derecho de salto del Rey debe cambiar los movimientos legales.
    a = moves(mod, f"{K_ONLY} w Kk - 0 1")
    b = moves(mod, f"{K_ONLY} w - - 0 1")
    jumps = a - b
    expected_jumps = {"h2" + d for d in
                      ["f1", "f2", "f3", "f4", "g4", "h4", "i4", "j1", "j2", "j3", "j4"]}
    check("king-jump-right", jumps == expected_jumps,
          f"esperados {sorted(expected_jumps)} obtenidos {sorted(jumps)}")

    # Par 2: casilla e.p. tras doble paso de PRINCIPE — el peon captura al paso.
    board = "16/7k8/16/16/16/16/16/16/16/16/16/16/5Pi9/16/7K8/16"
    a = moves(mod, f"{board} w - g5 0 3")
    b = moves(mod, f"{board} w - - 0 3")
    check("ep-square-prince", (a - b) == {"f4g5"},
          f"diff {sorted(a-b)}")

    # Par 3: la casilla e.p. codifica LA CASILLA (dos e.p. posibles distintos).
    board = "16/7k8/16/16/16/16/16/16/16/16/16/16/3PpPp9/16/7K8/16"
    a = moves(mod, f"{board} w - e5 0 3")
    b = moves(mod, f"{board} w - g5 0 3")
    check("ep-square-location",
          ("d4e5" in a and "f4e5" in a and "f4g5" not in a
           and "f4g5" in b and "d4e5" not in b and "f4e5" not in b),
          f"a={sorted(m for m in a if 'e5' in m or 'g5' in m)} b={sorted(m for m in b if 'e5' in m or 'g5' in m)}")

    # Par 4: Troll en ultima fila NO promociona (llego por salto): sigue siendo Troll.
    a = moves(mod, f"T15/7k8/16/16/16/16/16/16/16/16/16/16/16/16/7K8/16 w - - 0 1")
    troll_moves = {m for m in a if m.startswith("a16")}
    check("troll-last-rank-no-promo",
          troll_moves == {"a16a13", "a16d13", "a16d16"},
          f"got {sorted(troll_moves)}")

    # Par 5: contadores preservados en round-trip.
    fen = f"{K_ONLY} b Kk - 37 88"
    rt = mod.Position.from_fen(fen).to_fen()
    check("counters-roundtrip", rt.endswith(" 37 88"), rt)

    # Par 6: turno cambia el bando que mueve.
    a = moves(mod, f"{K_ONLY} w - - 0 1")
    b = moves(mod, f"{K_ONLY} b - - 0 1")
    check("side-to-move", all(m[:2] == "h2" for m in a) and all(m[:3] == "h15" for m in b),
          f"a={sorted(a)[:3]} b={sorted(b)[:3]}")

    print(f"aliasing: {len(fails)} fallos")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
