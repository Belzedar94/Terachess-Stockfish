#!/usr/bin/env python3
"""F2c: libro de aperturas de Terachess por self-play MultiPV del propio motor.

No existe teoria de aperturas de Terachess ni libros con licencia utilizable:
el libro se genera con el motor. Cada linea son N plies elegidos entre las
mejores jugadas MultiPV (con dispersion controlada) para dar variedad sin
partir de posiciones perdidas.

Salida: un FEN por linea (formato que consume el datagen).

Uso:
  python make_book.py --engine ../src/stockfish.exe \
      --net ../nets/tera-net2.tnn --net-sha256 05162b... \
      --lines 5000 --min-plies 8 --max-plies 16 --nodes 5000 \
      --multipv 6 --out ../books/tera_openings_v1.epd
"""
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Engine:
    def __init__(self, path, multipv, net_path, net_sha256):
        self.p = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        try:
            self._cmd("uci", "uciok")
            self._load_network(net_path, net_sha256)
            self.p.stdin.write(f"setoption name MultiPV value {multipv}\n")
            self.p.stdin.flush()
            self._cmd("isready", "readyok")
        except BaseException:
            self.close()
            raise

    def _cmd(self, send, token):
        self.p.stdin.write(send + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("motor muerto")
            if line.startswith(token):
                return line

    def _load_network(self, path, expected_sha256):
        self.p.stdin.write(f"setoption name EvalFile value {path}\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("motor muerto al cargar la red")
            if line.startswith("info string EvalFile: REJECTED"):
                raise RuntimeError(line.strip())
            if line.startswith("info string EvalFile: loaded"):
                token = f"file_sha256 {expected_sha256})"
                if token not in line:
                    raise RuntimeError(
                        "el motor no confirmó el SHA-256 esperado de la red: "
                        + line.strip()
                    )
                return line.strip()

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
        if self.p.poll() is not None:
            return
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--net", required=True)
    ap.add_argument("--net-sha256", required=True)
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
    ap.add_argument("--receipt", help="default: <out>.receipt.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.lines <= 0:
        ap.error("--lines debe ser positivo")
    if not 0 <= args.min_plies <= args.max_plies:
        ap.error("se exige 0 <= --min-plies <= --max-plies")
    if args.nodes <= 0 or args.multipv <= 0:
        ap.error("--nodes y --multipv deben ser positivos")
    expected_net_sha = args.net_sha256.lower()
    if len(expected_net_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_net_sha):
        ap.error("--net-sha256 debe contener 64 dígitos hexadecimales")

    engine_path = os.path.abspath(args.engine)
    net_path = os.path.abspath(args.net)
    out_path = os.path.abspath(args.out)
    receipt_path = os.path.abspath(args.receipt or (args.out + ".receipt.json"))
    if not os.path.isfile(engine_path):
        ap.error(f"motor inexistente: {engine_path}")
    if not os.path.isfile(net_path):
        ap.error(f"red inexistente: {net_path}")
    actual_net_sha = sha256_file(net_path)
    if actual_net_sha != expected_net_sha:
        ap.error(
            f"SHA-256 de red incorrecto: {actual_net_sha} != {expected_net_sha}"
        )
    for path in (out_path, receipt_path):
        if os.path.exists(path) and not args.force:
            ap.error(f"el destino ya existe (usa --force): {path}")

    from run_fixtures import load_impl
    O = load_impl("a")
    rng = random.Random(args.seed)
    eng = Engine(engine_path, args.multipv, net_path, expected_net_sha)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(os.path.dirname(receipt_path), exist_ok=True)
    seen, kept, discarded = set(), [], 0
    t0 = time.time()

    try:
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
                if mv not in legal:                 # el oráculo manda
                    raise RuntimeError(
                        f"jugada ilegal del motor {mv} en {pos.to_fen()}"
                    )
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
                print(
                    f"  {len(kept)}/{args.lines} líneas ({time.time()-t0:.0f}s, "
                    f"{discarded} descartadas)",
                    flush=True,
                )
    finally:
        eng.close()

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(kept) + "\n")
    raw_sha256 = sha256_file(out_path)
    with open(out_path, "r", encoding="utf-8", newline=None) as source:
        text_sha256 = hashlib.sha256(source.read().encode("utf-8")).hexdigest()
    elapsed = time.time() - t0
    receipt = {
        "schema": "terachess-opening-book-receipt-v1",
        "engine": {
            "name": os.path.basename(engine_path),
            "sha256": sha256_file(engine_path),
        },
        "network": {
            "name": os.path.basename(net_path),
            "sha256": actual_net_sha,
            "bytes": os.path.getsize(net_path),
        },
        "parameters": {
            "lines": args.lines,
            "min_plies": args.min_plies,
            "max_plies": args.max_plies,
            "nodes": args.nodes,
            "multipv": args.multipv,
            "diff_cp": args.diff_cp,
            "eval_limit": args.eval_limit,
            "seed": args.seed,
        },
        "result": {
            "name": os.path.basename(out_path),
            "lines": len(kept),
            "discarded": discarded,
            "bytes": os.path.getsize(out_path),
            "sha256_text": text_sha256,
            "sha256_raw": raw_sha256,
            "elapsed_seconds": round(elapsed, 3),
        },
    }
    with open(receipt_path, "w", encoding="utf-8", newline="\n") as target:
        json.dump(receipt, target, indent=2, sort_keys=True)
        target.write("\n")

    print(f"\nlibro: {len(kept)} líneas -> {out_path}")
    print(f"sha256 texto: {text_sha256}")
    print(f"sha256 raw:   {raw_sha256}")
    print(f"recibo: {receipt_path}")
    print(f"descartadas: {discarded} | {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
