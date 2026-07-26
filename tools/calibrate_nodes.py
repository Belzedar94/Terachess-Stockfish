#!/usr/bin/env python3
"""F2b: calibracion depth<->nodos y blunder-rate ANTES de fijar el datagen.

Regla predeclarada (PLAN.md F2b): los nodos del datagen son el minimo N con
blunder-rate < 15% y depth media >= 5.
blunder(pos, N) := la jugada elegida a N nodos es refutada por >150 cp por una
re-busqueda a 4N nodos (evaluada como: score_4N(mejor_4N) - score_4N(mejor_N)).

Uso:
  python calibrate_nodes.py --engine ../src/stockfish.exe --positions 60 \
      --nodes 10000,20000,40000,80000 [--threads 1]
"""
import argparse, json, os, random, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))


def uci_batch(engine, script, threads=1, timeout=36000, net=None):
    pre = f"setoption name Threads value {threads}\n"
    if net:
        pre += f"setoption name EvalFile value {net}\n"
    pre += "uci\nisready\n"
    out = subprocess.run([engine], input=pre + script + "\nquit\n",
                         capture_output=True, text=True, timeout=timeout).stdout
    return out


def parse_searches(out):
    """Devuelve [(depth, score_cp, bestmove, nodes), ...] una por 'go'."""
    res, cur = [], {}
    for line in out.splitlines():
        if line.startswith("info depth"):
            d = re.search(r"^info depth (\d+)", line)
            s = re.search(r"score cp (-?\d+)", line)
            n = re.search(r" nodes (\d+)", line)
            mate = re.search(r"score mate (-?\d+)", line)
            if d:
                cur["depth"] = int(d.group(1))
            if n:
                cur["nodes"] = int(n.group(1))
            if s:
                cur["score"] = int(s.group(1))
            elif mate:
                cur["score"] = 30000 if int(mate.group(1)) > 0 else -30000
        elif line.startswith("bestmove"):
            cur["best"] = line.split()[1]
            res.append(cur)
            cur = {}
    return res


def gen_positions(n, seed, min_ply=8, max_ply=160):
    from run_fixtures import load_impl
    A = load_impl("a")
    rng = random.Random(seed)
    fens = []
    while len(fens) < n:
        pos = A.Position.from_fen(A.START_FEN)
        target = rng.randint(min_ply, max_ply)
        ok = True
        for _ in range(target):
            mvs = pos.legal_moves()
            if not mvs:
                ok = False
                break
            pos = pos.apply(rng.choice(mvs))
        if ok and pos.legal_moves():
            fens.append(pos.to_fen())
    return fens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--positions", type=int, default=60)
    ap.add_argument("--nodes", default="10000,20000,40000,80000")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--seed", type=int, default=99)
    ap.add_argument("--net", help="TNN1 a cargar (calibracion con red)")
    ap.add_argument("--out", default=os.path.join(HERE, "calibration.json"))
    args = ap.parse_args()

    node_levels = [int(x) for x in args.nodes.split(",")]
    print(f"generando {args.positions} posiciones...")
    fens = gen_positions(args.positions, args.seed)

    report = {"positions": len(fens), "threads": args.threads, "levels": {}}
    for N in node_levels:
        t0 = time.time()
        # 1) busqueda a N nodos
        script = "\n".join(f"position fen {f}\ngo nodes {N}" for f in fens)
        base = parse_searches(uci_batch(args.engine, script, args.threads, net=args.net))
        # 2) arbitro a 4N nodos: score del arbitro para SU mejor jugada
        script4 = "\n".join(f"position fen {f}\ngo nodes {4*N}" for f in fens)
        ref = parse_searches(uci_batch(args.engine, script4, args.threads, net=args.net))
        # 3) score del arbitro para la jugada del nivel N (searchmoves)
        script_sm = "\n".join(
            f"position fen {f}\ngo nodes {4*N} searchmoves {b['best']}"
            for f, b in zip(fens, base) if b.get("best") not in (None, "(none)"))
        forced = parse_searches(uci_batch(args.engine, script_sm, args.threads, net=args.net))

        depths = [b.get("depth", 0) for b in base]
        blunders, deltas = 0, []
        for r, f in zip(ref, forced):
            if "score" not in r or "score" not in f:
                continue
            delta = r["score"] - f["score"]
            deltas.append(delta)
            if delta > 150:
                blunders += 1
        n_eval = len(deltas)
        lvl = {
            "nodes": N,
            "depth_mean": round(sum(depths) / max(1, len(depths)), 2),
            "depth_min": min(depths) if depths else 0,
            "depth_max": max(depths) if depths else 0,
            "blunder_rate_pct": round(100.0 * blunders / max(1, n_eval), 1),
            "delta_mean_cp": round(sum(deltas) / max(1, n_eval), 1),
            "evaluated": n_eval,
            "seconds": round(time.time() - t0, 1),
        }
        report["levels"][str(N)] = lvl
        print(f"  nodes={N:>7}: depth media {lvl['depth_mean']:>5} "
              f"[{lvl['depth_min']}-{lvl['depth_max']}] | "
              f"blunder {lvl['blunder_rate_pct']:>5}% | "
              f"delta medio {lvl['delta_mean_cp']:>7} cp | {lvl['seconds']}s")

    # regla predeclarada
    chosen = None
    for N in node_levels:
        lvl = report["levels"][str(N)]
        if lvl["blunder_rate_pct"] < 15.0 and lvl["depth_mean"] >= 5.0:
            chosen = N
            break
    report["chosen_nodes"] = chosen
    report["rule"] = "min N con blunder_rate<15% y depth_mean>=5"
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print(f"\nREGLA -> nodos de datagen = {chosen}")
    print(f"informe: {args.out}")
    return 0 if chosen else 1


if __name__ == "__main__":
    sys.exit(main())
