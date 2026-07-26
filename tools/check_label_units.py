#!/usr/bin/env python3
"""GATE de unidades: el label grabado debe vivir en el MISMO espacio que la
evaluacion del motor (ADR docs/eval-units.md).

Un fallo de este gate costo una red entera: los labels estaban en cp
normalizados por el modelo WDL de ajedrez y la evaluacion en unidades internas,
factor ~4x. La red aprendio el espacio equivocado y perdio 511 Elo.

Se ejecuta sobre TODA campana ANTES de entrenar.

Uso:
  python check_label_units.py --engine ../src/stockfish.exe --data campaign.bin \
      [--positions 300] [--min-abs 150]
Exit 0 si pendiente en [0.8, 1.25] y correlacion > 0.9.
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import terabin  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--positions", type=int, default=300)
    ap.add_argument("--min-abs", type=int, default=150,
                    help="solo posiciones con |label| mayor, para medir pendiente")
    ap.add_argument("--slope-min", type=float, default=0.8)
    ap.add_argument("--slope-max", type=float, default=1.25)
    ap.add_argument("--corr-min", type=float, default=0.9)
    args = ap.parse_args()

    payload = open(args.data, "rb").read()[32:]
    total = len(payload) // 144
    step = max(1, total // (args.positions * 6))
    picks = []
    for i in range(0, total, step):
        rec = terabin.unpack(payload[i * 144:(i + 1) * 144])
        if abs(rec.score) >= args.min_abs:
            picks.append((terabin.to_fen(rec), rec.score))
        if len(picks) >= args.positions:
            break
    if len(picks) < 20:
        print(f"muestras insuficientes con |label| >= {args.min_abs}: {len(picks)}")
        return 2

    cmds = []
    for fen, _ in picks:
        cmds += [f"position fen {fen}", "eval"]
    cmds.append("quit")
    out = subprocess.run([args.engine], input="\n".join(cmds) + "\n",
                         capture_output=True, text=True, timeout=1800).stdout

    # Una sola magnitud por posicion: con red se usa total_cp (POV stm), sin red
    # 'material' (POV blancas). `eval` imprime varias lineas por bloque, asi que
    # se toma la primera util de cada bloque y se marca su POV.
    static, pov_white, seen_block = [], [], False
    for line in out.splitlines():
        s = line.strip().lower()
        if s.startswith("nnue "):
            seen_block = False
        if not seen_block and s.startswith("total_cp "):
            static.append(int(s.split()[1]))
            pov_white.append(False)            # total_cp ya es POV stm
            seen_block = True
        elif not seen_block and s.startswith("material "):
            static.append(int(s.split()[1]))
            pov_white.append(True)
            seen_block = True
    if len(static) != len(picks):
        print(f"ERROR: {len(static)} evaluaciones para {len(picks)} posiciones")
        return 2

    # el label es POV del bando al mover; 'material' se imprime POV blancas
    xs, ys = [], []
    for (fen, label), st, is_white_pov in zip(picks, static, pov_white):
        stm_white = fen.split()[1] == "w"
        ys.append(label)
        xs.append((st if stm_white else -st) if is_white_pov else st)

    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx if sxx else 0.0
    corr = sxy / ((sxx * syy) ** 0.5) if sxx and syy else 0.0

    ok = args.slope_min <= slope <= args.slope_max and corr >= args.corr_min
    print(f"posiciones {n} | pendiente label/eval {slope:.3f} "
          f"(admitida [{args.slope_min}, {args.slope_max}]) | correlacion {corr:.3f}")
    print(f"GATE DE UNIDADES: {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("  Los labels NO viven en el espacio de Eval::evaluate(). "
              "Ver docs/eval-units.md antes de entrenar nada con estos datos.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
