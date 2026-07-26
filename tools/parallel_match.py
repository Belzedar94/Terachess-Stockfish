#!/usr/bin/env python3
"""Envoltorio paralelo de fixed_nodes_match.py: N procesos con semillas
disjuntas, agregando W/L/D. El runner secuencial hace ~23 s por partida; con
20 procesos un SPRT de 5.000 partidas baja de ~32 h a ~1,6 h.

Uso:
  python parallel_match.py --a cand.exe --b base.exe --games 400 --nodes 10000 \
      --concurrency 20 [--json out.json]
"""
import argparse, json, math, os, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "fixed_nodes_match.py")


def elo(w, l, d):
    n = w + l + d
    if n == 0:
        return 0.0, 0.0
    score = (w + 0.5 * d) / n
    if score <= 0 or score >= 1:
        return (999.0 if score > 0.5 else -999.0), 0.0
    e = -400 * math.log10(1 / score - 1)
    var = (w * (1 - score) ** 2 + l * score ** 2 + d * (0.5 - score) ** 2) / n
    se = math.sqrt(var / n)
    denom = score * (1 - score) * math.log(10) / 400
    return e, (se / denom if denom else 0.0)


def worker(args, idx, games, seed):
    tmp = os.path.join(tempfile.gettempdir(), f"pm_{os.getpid()}_{idx}.json")
    cmd = [sys.executable, RUNNER, "--a", args.a, "--b", args.b,
           "--games", str(games), "--nodes", str(args.nodes),
           "--max-plies", str(args.max_plies), "--seed", str(seed),
           "--json", tmp]
    if args.adj_cp:
        cmd += ["--adj-cp", str(args.adj_cp), "--adj-moves", str(args.adj_moves)]
    if args.rule50:
        cmd += ["--rule50", str(args.rule50)]
    for o in args.a_opt:
        cmd += ["--a-opt", o]
    for o in args.b_opt:
        cmd += ["--b-opt", o]
    subprocess.run(cmd, capture_output=True, text=True)
    try:
        with open(tmp, encoding="utf-8") as f:
            r = json.load(f)
        os.remove(tmp)
        return r
    except OSError:
        return {"w": 0, "l": 0, "d": 0, "games": 0, "anomalies": [], "mean_plies": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--nodes", type=int, default=10000)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--max-plies", type=int, default=1000)
    ap.add_argument("--adj-cp", type=int, default=5000)
    ap.add_argument("--adj-moves", type=int, default=6)
    ap.add_argument("--rule50", type=int, default=100)
    ap.add_argument("--seed", type=int, default=9000)
    ap.add_argument("--a-opt", action="append", default=[])
    ap.add_argument("--b-opt", action="append", default=[])
    ap.add_argument("--json")
    args = ap.parse_args()

    k = args.concurrency
    per = [args.games // k + (1 if i < args.games % k else 0) for i in range(k)]
    per = [p for p in per if p > 0]
    print(f"{args.games} partidas en {len(per)} procesos ({per[0]} por proceso) "
          f"@ {args.nodes} nodos")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(per)) as ex:
        futs = [ex.submit(worker, args, i, g, args.seed + 1000 * i)
                for i, g in enumerate(per)]
        results = [f.result() for f in futs]

    w = sum(r["w"] for r in results)
    l = sum(r["l"] for r in results)
    d = sum(r["d"] for r in results)
    anomalies = [a for r in results for a in r.get("anomalies", [])]
    plies = [r.get("mean_plies", 0) * r.get("games", 0) for r in results]
    n = w + l + d
    e, se = elo(w, l, d)
    out = {"a": args.a, "b": args.b, "nodes": args.nodes, "games": n,
           "w": w, "l": l, "d": d,
           "draw_rate_pct": round(100.0 * d / max(1, n), 1),
           "mean_plies": round(sum(plies) / max(1, n), 1),
           "elo": round(e, 1), "elo_err": round(se, 1),
           "anomalies": len(anomalies), "seconds": round(time.time() - t0, 1)}
    print(f"\nRESULTADO: +{w} -{l} ={d} en {n} partidas | Elo {e:+.1f} +/- {se:.1f} | "
          f"tablas {out['draw_rate_pct']}% | plies {out['mean_plies']} | "
          f"anomalias {len(anomalies)} | {out['seconds']}s "
          f"({n/max(1,out['seconds']):.2f} partidas/s)")
    for a in anomalies[:5]:
        print("  ANOMALIA:", json.dumps(a)[:200])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
