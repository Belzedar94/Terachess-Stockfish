#!/usr/bin/env python3
"""Curva datos -> calidad de red (F4 del plan).

Mide, para cada red, la correlacion y el error absoluto contra la evaluacion
material del motor sobre posiciones FRESCAS (generadas por el oraculo, jamas
vistas en entrenamiento) y sobre posiciones de entrenamiento. La brecha entre
ambas es la medida directa de sobreajuste.

Con un maestro de eval material pura, una red util debe aproximar el material
con error MUY inferior a la senal posicional que pretende anadir; si el error
mediano son varios peones, la red solo puede empeorar la busqueda.

Uso:
  python data_scaling.py --engine ../src/stockfish.exe --data ../data/c2_full.bin \
      --nets 90k=../data/net090.tnn 180k=../data/net180.tnn 370k=../data/netB.tnn
"""
import argparse, os, statistics, subprocess, sys, random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))
import terabin  # noqa: E402


def engine_evals(engine, fens, net):
    cmds = []
    if net:
        cmds += [f"setoption name EvalFile value {net}", "isready"]
    for f in fens:
        cmds += [f"position fen {f}", "eval"]
    cmds.append("quit")
    out = subprocess.run([engine], input="\n".join(cmds) + "\n",
                         capture_output=True, text=True, timeout=3600).stdout
    # total_cp existe en ambos modos y es POV del bando al mover: una por bloque
    return [int(l.split()[1]) for l in out.splitlines()
            if l.strip().lower().startswith("total_cp ")]


def corr(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    cv = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
    return cv / (statistics.pstdev(a) * statistics.pstdev(b) + 1e-9)


def fresh_positions(n, seed=31415):
    from run_fixtures import load_impl
    O = load_impl("a")
    rng = random.Random(seed)
    out = []
    while len(out) < n:
        pos = O.Position.from_fen(O.START_FEN)
        for _ in range(rng.randint(20, 140)):
            mv = pos.legal_moves()
            if not mv:
                break
            pos = pos.apply(rng.choice(mv))
        if pos.legal_moves() and not pos.in_check():
            out.append(pos.to_fen())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--data", required=True, help="tera-bin de entrenamiento")
    ap.add_argument("--nets", nargs="+", required=True, help="etiqueta=ruta.tnn")
    ap.add_argument("--positions", type=int, default=150)
    args = ap.parse_args()

    payload = open(args.data, "rb").read()[32:]
    total = len(payload) // 144
    step = max(1, total // args.positions)
    train = [terabin.to_fen(terabin.unpack(payload[i * 144:(i + 1) * 144]))
             for i in range(0, total, step)][:args.positions]
    fresh = fresh_positions(args.positions)

    mat_train = engine_evals(args.engine, train, None)
    mat_fresh = engine_evals(args.engine, fresh, None)

    print(f"{'red':>8} | {'corr train':>10} {'corr fresh':>10} | "
          f"{'err med train':>13} {'err med fresh':>13} | {'sd red':>7} {'sd mat':>7}")
    print("-" * 84)
    print(f"{'material':>8} | {1.0:10.3f} {1.0:10.3f} | {0:13d} {0:13d} | "
          f"{statistics.pstdev(mat_fresh):7.0f} {statistics.pstdev(mat_fresh):7.0f}")
    rows = []
    for spec in args.nets:
        label, _, path = spec.partition("=")
        nt = engine_evals(args.engine, train, os.path.abspath(path))
        nf = engine_evals(args.engine, fresh, os.path.abspath(path))
        if len(nt) != len(train) or len(nf) != len(fresh):
            print(f"{label:>8} | ERROR de parseo ({len(nt)}/{len(train)}, {len(nf)}/{len(fresh)})")
            continue
        et = statistics.median(abs(a - b) for a, b in zip(mat_train, nt))
        ef = statistics.median(abs(a - b) for a, b in zip(mat_fresh, nf))
        row = (label, corr(mat_train, nt), corr(mat_fresh, nf), et, ef,
               statistics.pstdev(nf), statistics.pstdev(mat_fresh))
        rows.append(row)
        print(f"{label:>8} | {row[1]:10.3f} {row[2]:10.3f} | {et:13.0f} {ef:13.0f} | "
              f"{row[5]:7.0f} {row[6]:7.0f}")
    print("\nLectura: 'corr fresh' es la capacidad real de generalizar; la brecha")
    print("train-fresh mide sobreajuste. El error mediano esta en unidades internas")
    print("(peon = 50): un error de 200 son 4 peones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
