#!/usr/bin/env python3
"""Sonda A/B a nodos fijos con el ORACULO como arbitro de reglas.

El oraculo mantiene la partida: valida cada jugada de los motores (una jugada
ilegal = perdida inmediata y se reporta), decide mate/ahogado/50/repeticion.
Colores alternos, aperturas aleatorias compartidas por el par (juego emparejado).

Lecciones Spell incorporadas: ucinewgame entre partidas (aislamiento), toda
perdida se reporta (crash, ilegal, timeout), y esto es una SONDA RELATIVA entre
builds propios, no un sustituto del SPRT del harness (F4).

Uso:
  python fixed_nodes_match.py --a ../src/stockfish.exe --b ../baseline.exe \
      --games 100 --nodes 20000
"""
import argparse, json, math, os, random, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))


class Engine:
    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.crashed = False
        self.send("uci")
        self.wait("uciok")

    def send(self, s):
        try:
            self.p.stdin.write(s + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            self.crashed = True

    def wait(self, token, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.p.stdout.readline()
            if not line:
                self.crashed = True
                return None
            if line.startswith(token):
                return line.strip()
        self.crashed = True
        return None

    def newgame(self):
        self.send("ucinewgame")
        self.send("isready")
        self.wait("readyok")

    def bestmove(self, start_fen, moves, nodes):
        """Devuelve (move, score_cp_pov_stm). score None si no se pudo leer."""
        cmd = f"position fen {start_fen}"
        if moves:
            cmd += " moves " + " ".join(moves)
        self.send(cmd)
        self.send(f"go nodes {nodes}")
        score = None
        t0 = time.time()
        while time.time() - t0 < 180:
            line = self.p.stdout.readline()
            if not line:
                self.crashed = True
                return None, None
            if line.startswith("info ") and " score " in line:
                parts = line.split()
                try:
                    if "cp" in parts:
                        score = int(parts[parts.index("cp") + 1])
                    elif "mate" in parts:
                        m = int(parts[parts.index("mate") + 1])
                        score = 30000 - abs(m) if m > 0 else -(30000 - abs(m))
                except (ValueError, IndexError):
                    pass
            elif line.startswith("bestmove"):
                parts = line.split()
                return (parts[1] if len(parts) > 1 else None), score
        self.crashed = True
        return None, None

    def close(self):
        self.send("quit")
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="motor A (candidato)")
    ap.add_argument("--b", required=True, help="motor B (baseline)")
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--max-plies", type=int, default=400)
    ap.add_argument("--open-plies", type=int, default=8)
    ap.add_argument("--adj-cp", type=int, default=0,
                    help="adjudicar victoria si |eval| >= cp durante adj-moves jugadas (0 = off)")
    ap.add_argument("--adj-moves", type=int, default=4)
    ap.add_argument("--rule50", type=int, default=0,
                    help="tablas si el contador de 50 llega a N jugadas (0 = off)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--json")
    args = ap.parse_args()

    from run_fixtures import load_impl
    O = load_impl("a")
    rng = random.Random(args.seed)

    A = Engine(args.a, "A")
    B = Engine(args.b, "B")
    w = l = d = 0
    anomalies = []
    plies_total = 0
    t0 = time.time()
    saved_opening = []

    for g in range(args.games):
        if g % 2 == 0:
            saved_opening, pos = [], O.Position.from_fen(O.START_FEN)
            for _ in range(args.open_plies):
                mvs = pos.legal_moves()
                if not mvs:
                    break
                mv = rng.choice(mvs)
                saved_opening.append(mv)
                pos = pos.apply(mv)

        a_is_white = (g % 2 == 0)
        A.newgame()
        B.newgame()
        pos = O.Position.from_fen(O.START_FEN)
        for mv in saved_opening:
            pos = pos.apply(mv)
        moves = list(saved_opening)
        result, winner = None, None
        adj_streak_white = adj_streak_black = 0

        for ply in range(args.max_plies):
            legal = pos.legal_moves()
            if not legal:
                result = pos.result()
                break
            fen_parts = pos.to_fen().split()
            white_to_move = fen_parts[1] == "w"
            if args.rule50 and int(fen_parts[4]) >= 2 * args.rule50:
                result = "1/2-1/2"
                break
            eng = A if (white_to_move == a_is_white) else B
            mv, score = eng.bestmove(O.START_FEN, moves, args.nodes)
            if eng.crashed or mv is None or mv not in legal:
                anomalies.append({"game": g, "engine": eng.name, "ply": ply,
                                  "move": mv, "crashed": eng.crashed,
                                  "fen": pos.to_fen()})
                winner = "B" if eng is A else "A"
                result = "anomaly"
                break
            # adjudicacion por evaluacion (win_adj movecount/score del plan)
            if args.adj_cp and score is not None:
                white_score = score if white_to_move else -score
                if white_score >= args.adj_cp:
                    adj_streak_white += 1
                    adj_streak_black = 0
                elif white_score <= -args.adj_cp:
                    adj_streak_black += 1
                    adj_streak_white = 0
                else:
                    adj_streak_white = adj_streak_black = 0
                if adj_streak_white >= args.adj_moves:
                    result, adjudicated = "1-0", True
                    moves.append(mv); plies_total += 1
                    break
                if adj_streak_black >= args.adj_moves:
                    result, adjudicated = "0-1", True
                    moves.append(mv); plies_total += 1
                    break
            moves.append(mv)
            pos = pos.apply(mv)
            plies_total += 1
            r = pos.result()
            if r != "*":
                result = r
                break
        else:
            result = "1/2-1/2"

        if result == "anomaly":
            pass
        elif result in ("1/2-1/2", "*"):
            winner = None
        elif result == "1-0":
            winner = "A" if a_is_white else "B"
        elif result == "0-1":
            winner = "B" if a_is_white else "A"

        if winner == "A":
            w += 1
        elif winner == "B":
            l += 1
        else:
            d += 1

        if (g + 1) % 10 == 0:
            e, se = elo(w, l, d)
            print(f"  {g+1}/{args.games}: +{w} -{l} ={d}  Elo {e:+.1f} +/- {se:.1f} "
                  f"({time.time()-t0:.0f}s)", flush=True)

    A.close()
    B.close()
    e, se = elo(w, l, d)
    n = w + l + d
    out = {"a": args.a, "b": args.b, "nodes": args.nodes, "games": n,
           "w": w, "l": l, "d": d, "draw_rate_pct": round(100.0 * d / max(1, n), 1),
           "mean_plies": round(plies_total / max(1, n), 1),
           "elo": round(e, 1), "elo_err": round(se, 1),
           "anomalies": anomalies, "seconds": round(time.time() - t0, 1)}
    print(f"\nRESULTADO: +{w} -{l} ={d} en {n} partidas | Elo {e:+.1f} +/- {se:.1f} | "
          f"tablas {out['draw_rate_pct']}% | plies medios {out['mean_plies']} | "
          f"anomalias: {len(anomalies)} | {out['seconds']}s")
    for a in anomalies[:5]:
        print("  ANOMALIA:", json.dumps(a)[:200])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
