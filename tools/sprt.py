#!/usr/bin/env python3
"""F4: SPRT con bounds en Elo crudo y parada secuencial, sobre el runner de
partidas a nodos fijos (el oraculo es el arbitro de reglas).

Modelo trinomial (W/L/D) con LLR de Wald. Los bounds se declaran ANTES de
lanzar (politica del playbook: nunca relajar un umbral tras ver el resultado).

Sub-comandos:
  calibrate  — piloto de N partidas para medir tasa de tablas, longitud media y
               percentil 95 del |eval| en posiciones ganadas => fija adjudicacion
  run        — SPRT A vs B con bounds [e0, e1], alpha, beta

Uso:
  python sprt.py run --a cand.exe --b base.exe --nodes 20000 \
      --elo0 1 --elo1 6 --alpha 0.05 --beta 0.05 --max-games 8000
"""
import argparse, json, math, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def elo_to_score(e):
    return 1.0 / (1.0 + 10 ** (-e / 400.0))


def llr_trinomial(w, l, d, elo0, elo1, drawelo=None):
    """LLR con el modelo de Wald sobre proporciones observadas (BayesElo-free).

    Aproximacion estandar: se usa la varianza empirica del score y se compara
    la hipotesis H0: elo=elo0 contra H1: elo=elo1.
    """
    n = w + l + d
    if n < 2:
        return 0.0
    score = (w + 0.5 * d) / n
    # varianza empirica por partida
    var = (w * (1 - score) ** 2 + l * (0 - score) ** 2 + d * (0.5 - score) ** 2) / n
    if var <= 1e-12:
        var = 1e-12
    s0, s1 = elo_to_score(elo0), elo_to_score(elo1)
    return n * (s1 - s0) * (score - (s0 + s1) / 2) / var


def bounds(alpha, beta):
    return math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--a", required=True)
    r.add_argument("--b", required=True)
    r.add_argument("--nodes", type=int, default=20000)
    r.add_argument("--elo0", type=float, default=1.0)
    r.add_argument("--elo1", type=float, default=6.0)
    r.add_argument("--alpha", type=float, default=0.05)
    r.add_argument("--beta", type=float, default=0.05)
    r.add_argument("--max-games", type=int, default=8000)
    r.add_argument("--batch", type=int, default=20, help="partidas por lote")
    r.add_argument("--max-plies", type=int, default=300)
    r.add_argument("--seed", type=int, default=4242)
    r.add_argument("--json")

    c = sub.add_parser("calibrate")
    c.add_argument("--engine", required=True)
    c.add_argument("--games", type=int, default=200)
    c.add_argument("--nodes", type=int, default=20000)
    c.add_argument("--max-plies", type=int, default=300)
    c.add_argument("--json")

    args = ap.parse_args()
    lower, upper = bounds(args.alpha, args.beta) if args.cmd == "run" else (0, 0)

    if args.cmd == "calibrate":
        cmd = [sys.executable, os.path.join(HERE, "fixed_nodes_match.py"),
               "--a", args.engine, "--b", args.engine,
               "--games", str(args.games), "--nodes", str(args.nodes),
               "--max-plies", str(args.max_plies), "--json", "/tmp/calib_match.json"]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        print(out[-1500:])
        try:
            with open("/tmp/calib_match.json", encoding="utf-8") as f:
                m = json.load(f)
        except OSError:
            print("no se pudo leer el json del match")
            return 1
        draw = m["draw_rate_pct"]
        report = {
            "draw_rate_pct": draw,
            "mean_plies": m["mean_plies"],
            "games": m["games"],
            "nodes": args.nodes,
            # politica predeclarada del plan
            "recommended_bounds": [1.0, 6.0] if draw < 20 else [0.0, 4.0],
            "draw_adjudication": "ninguna" if draw < 20 else "revisar",
            "note": ("Con pocas tablas cada partida informa mas: 1 nElo ~ 2 Elo. "
                     "Bounds en Elo crudo [1,6] mientras la brecha sea grande."),
        }
        print(json.dumps(report, indent=1, ensure_ascii=False))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1)
        return 0

    # --- run ---
    print(f"SPRT [{args.elo0}, {args.elo1}] alpha={args.alpha} beta={args.beta} "
          f"| LLR bounds [{lower:.2f}, {upper:.2f}] | nodos {args.nodes}")
    w = l = d = 0
    played = 0
    t0 = time.time()
    verdict = "INCONCLUSO"
    seed = args.seed
    while played < args.max_games:
        n = min(args.batch, args.max_games - played)
        tmp = os.path.join(HERE, f".sprt_batch_{seed}.json")
        cmd = [sys.executable, os.path.join(HERE, "fixed_nodes_match.py"),
               "--a", args.a, "--b", args.b, "--games", str(n),
               "--nodes", str(args.nodes), "--max-plies", str(args.max_plies),
               "--seed", str(seed), "--json", tmp]
        subprocess.run(cmd, capture_output=True, text=True)
        try:
            with open(tmp, encoding="utf-8") as f:
                m = json.load(f)
            os.remove(tmp)
        except OSError:
            print("lote fallido; abortando")
            break
        w += m["w"]; l += m["l"]; d += m["d"]
        played += m["games"]
        seed += 1
        if m["anomalies"]:
            print(f"  !! {len(m['anomalies'])} anomalias en el lote "
                  f"(se cuentan como derrota del motor que fallo)")
        llr = llr_trinomial(w, l, d, args.elo0, args.elo1)
        score = (w + 0.5 * d) / max(1, w + l + d)
        e = -400 * math.log10(1 / score - 1) if 0 < score < 1 else float("nan")
        print(f"  {played} partidas: +{w} -{l} ={d} | Elo {e:+.1f} | "
              f"LLR {llr:+.2f} | {time.time()-t0:.0f}s", flush=True)
        if llr >= upper:
            verdict = "PASS"
            break
        if llr <= lower:
            verdict = "FAIL"
            break

    llr = llr_trinomial(w, l, d, args.elo0, args.elo1)
    out = {"verdict": verdict, "games": played, "w": w, "l": l, "d": d,
           "llr": round(llr, 3), "bounds_elo": [args.elo0, args.elo1],
           "alpha": args.alpha, "beta": args.beta, "nodes": args.nodes,
           "seconds": round(time.time() - t0, 1)}
    print(f"\nVEREDICTO: {verdict} | +{w} -{l} ={d} en {played} partidas | LLR {llr:+.2f}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
