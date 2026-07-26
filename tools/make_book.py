#!/usr/bin/env python3
"""F2c: libro de aperturas de Terachess por self-play MultiPV del propio motor.

No existe teoria de aperturas de Terachess ni libros con licencia utilizable:
el libro se genera con el motor. Cada linea son N plies elegidos entre las
mejores jugadas MultiPV (con dispersion controlada) para dar variedad sin
partir de posiciones perdidas.

Salida: un FEN por linea (formato que consume el datagen).

Uso:
  python make_book.py --engine ../src/stockfish.exe --lines 5000 \
      --min-plies 8 --max-plies 16 --nodes 5000 --multipv 6 --out ../books/tera_v1.epd
"""
import argparse, hashlib, os, random, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))


class Engine:
    def __init__(self, path, multipv):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._cmd("uci", "uciok")
        self.p.stdin.write(f"setoption name MultiPV value {multipv}\n")
        self.p.stdin.flush()
        self._cmd("isready", "readyok")

    def _cmd(self, send, token):
        self.p.stdin.write(send + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("motor muerto")
            if line.startswith(token):
                return line

    def multipv_moves(self, start_fen, moves, nodes):
        """Devuelve [(score_cp, move), ...] de la ultima iteracion."""
        cmd = f"position fen {start_fen}"
        if moves:
            cmd += " moves " + " ".join(moves)
        self.p.stdin.write(cmd + f"\ngo nodes {nodes}\n")
        self.p.stdin.flush()
        best = {}
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("motor muerto")
            if line.startswith("info ") and " multipv " in line and " pv " in line:
                parts = line.split()
                try:
                    idx = int(parts[parts.index("multipv") + 1])
                    if "cp" in parts:
                        sc = int(parts[parts.index("cp") + 1])
                    elif "mate" in parts:
                        m = int(parts[parts.index("mate") + 1])
                        sc = 30000 if m > 0 else -30000
                    else:
                        continue
                    mv = parts[parts.index("pv") + 1]
                    best[idx] = (sc, mv)
                except (ValueError, IndexError):
                    continue
            elif line.startswith("bestmove"):
                break
        return [best[k] for k in sorted(best)]

    def close(self):
        self.p.stdin.write("quit\n")
        self.p.stdin.flush()
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--lines", type=int, default=5000)
    ap.add_argument("--min-plies", type=int, default=8)
    ap.add_argument("--max-plies", type=int, default=16)
    ap.add_argument("--nodes", type=int, default=5000)
    ap.add_argument("--multipv", type=int, default=6)
    ap.add_argument("--diff-cp", type=int, default=150,
                    help="solo se eligen jugadas a <= diff del mejor")
    ap.add_argument("--eval-limit", type=int, default=800,
                    help="descarta lineas que acaben con |eval| mayor")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from run_fixtures import load_impl
    O = load_impl("a")
    rng = random.Random(args.seed)
    eng = Engine(args.engine, args.multipv)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    seen, kept, discarded = set(), [], 0
    t0 = time.time()

    while len(kept) < args.lines:
        pos = O.Position.from_fen(O.START_FEN)
        moves = []
        target = rng.randint(args.min_plies, args.max_plies)
        ok = True
        last_score = 0
        for _ in range(target):
            cand = eng.multipv_moves(O.START_FEN, moves, args.nodes)
            if not cand:
                ok = False
                break
            top = cand[0][0]
            pool = [(s, m) for (s, m) in cand if top - s <= args.diff_cp]
            last_score, mv = rng.choice(pool)
            legal = pos.legal_moves()
            if mv not in legal:                     # el arbitro manda
                print(f"  AVISO: jugada ilegal del motor {mv} en {pos.to_fen()}")
                ok = False
                break
            moves.append(mv)
            pos = pos.apply(mv)
            if pos.result() != "*":
                ok = False
                break
        if not ok or abs(last_score) > args.eval_limit:
            discarded += 1
            continue
        fen = pos.to_fen()
        h = hashlib.sha256(fen.encode()).hexdigest()
        if h in seen:
            discarded += 1
            continue
        seen.add(h)
        kept.append(fen)
        if len(kept) % 100 == 0:
            print(f"  {len(kept)}/{args.lines} lineas ({time.time()-t0:.0f}s, "
                  f"{discarded} descartadas)", flush=True)

    eng.close()
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    data = open(args.out, "rb").read()
    print(f"\nlibro: {len(kept)} lineas -> {args.out}")
    print(f"sha256: {hashlib.sha256(data).hexdigest()}")
    print(f"descartadas: {discarded} | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
