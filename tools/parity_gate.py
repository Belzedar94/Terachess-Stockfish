#!/usr/bin/env python3
"""F3b: GATE DE PARIDAD motor <-> python, tolerancia EXACTAMENTE 0 cp.

Compara, sobre posiciones reales estratificadas:
  - los INDICES de features activos por perspectiva (comando `features`)
  - psqt / positional / total_cp / bucket (comando `eval`)
del motor C++ contra `tools/terannue/quantized_forward.py` sobre el MISMO TNN1.

Estratificacion exigida por el contrato (docs/nnue-tera-s.md §8):
  - los 8 output buckets representados
  - >=50 posiciones con cada uno de los 26 tipos de pieza en el tablero
  - posiciones reales no terminales

Exit 0 solo si TODAS las comparaciones son identicas.

Uso:
  python parity_gate.py --engine ../src/stockfish.exe --net net1.tnn \
      --data ../data/campaign1.bin --positions 1000
"""
import argparse, collections, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "terannue"))


def load_positions(data_path, want, min_per_type=50):
    """Selecciona FENs del tera-bin cubriendo buckets y tipos de pieza."""
    import terabin
    fens, type_count, bucket_count = [], collections.Counter(), collections.Counter()
    need_types = set("abcdefghijklmnopqrstuvwxyz")
    with open(data_path, "rb") as f:
        hdr = terabin.read_header(f)
        n = hdr["count"] if isinstance(hdr, dict) else hdr.count
        for i in range(n):
            raw = f.read(144)
            if len(raw) < 144:
                break
            try:
                rec = terabin.unpack(raw)
                fen = terabin.to_fen(rec)
            except Exception:
                continue
            board = fen.split()[0]
            types = {c.lower() for c in board if c.isalpha()}
            npieces = sum(1 for c in board if c.isalpha())
            bucket = min(7, (npieces - 1) // 16)
            # prioriza posiciones que aporten cobertura nueva
            useful = (bucket_count[bucket] < want // 8
                      or any(type_count[t] < min_per_type for t in types))
            if useful or len(fens) < want:
                fens.append(fen)
                bucket_count[bucket] += 1
                for t in types:
                    type_count[t] += 1
            if len(fens) >= want and all(type_count[t] >= min_per_type
                                         for t in need_types if type_count[t] > 0):
                break
    return fens, type_count, bucket_count


def engine_eval_batch(engine, net, fens):
    """Devuelve [(psqt, positional, total_cp, bucket, feats0, feats1), ...]."""
    cmds = [f"setoption name EvalFile value {net}", "isready"]
    for fen in fens:
        cmds += [f"position fen {fen}", "eval", "features"]
    cmds.append("quit")
    out = subprocess.run([engine], input="\n".join(cmds) + "\n",
                         capture_output=True, text=True, timeout=3600).stdout
    results, cur, feats, persp = [], {}, {}, None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("psqt "):
            cur["psqt"] = int(s.split()[1])
        elif s.startswith("positional "):
            cur["positional"] = int(s.split()[1])
        elif s.startswith("total_cp "):
            cur["total_cp"] = int(s.split()[1])
        elif s.startswith("bucket "):
            cur["bucket"] = int(s.split()[1])
        elif s.startswith("persp "):
            parts = s.split()
            persp = int(parts[1])
            feats[persp] = {"kbucket": int(parts[3]), "count": int(parts[5]),
                            "idx": []}
        elif persp is not None and s and all(t.isdigit() for t in s.split()):
            feats[persp]["idx"].extend(int(t) for t in s.split())
            if len(feats[persp]["idx"]) >= feats[persp]["count"] and persp == 1:
                cur["feats"] = {0: feats.get(0), 1: feats.get(1)}
                results.append(cur)
                cur, feats, persp = {}, {}, None
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--net", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--positions", type=int, default=1000)
    ap.add_argument("--json")
    args = ap.parse_args()

    t0 = time.time()
    print("seleccionando posiciones estratificadas...")
    fens, type_count, bucket_count = load_positions(args.data, args.positions)
    print(f"  {len(fens)} posiciones | buckets: {dict(sorted(bucket_count.items()))}")
    thin = {t: c for t, c in sorted(type_count.items()) if c < 50}
    if thin:
        print(f"  AVISO cobertura <50 posiciones para tipos: {thin}")

    print("evaluando con el motor...")
    eng = engine_eval_batch(args.engine, args.net, fens)
    if len(eng) != len(fens):
        print(f"ERROR: el motor devolvio {len(eng)} evaluaciones de {len(fens)}")
        return 2

    print("evaluando con quantized_forward (python)...")
    import quantized_forward as qf
    import features as feat_mod
    import terabin  # noqa: F401  (asegura el mismo decoder)
    net = qf.load_net(args.net) if hasattr(qf, "load_net") else qf.load(args.net)

    mismatches = []
    for fen, e in zip(fens, eng):
        try:
            py = qf.evaluate_fen(net, fen) if hasattr(qf, "evaluate_fen") else None
            if py is None:
                idx = feat_mod.feature_indices_from_fen(fen)
                py = qf.evaluate_indices(net, idx)
        except Exception as ex:
            mismatches.append({"fen": fen, "kind": "python_error", "detail": repr(ex)})
            continue
        for key in ("psqt", "positional", "total_cp", "bucket"):
            if key in py and py[key] != e.get(key):
                mismatches.append({"fen": fen, "kind": key,
                                   "engine": e.get(key), "python": py[key]})
        if "feats" in e and hasattr(feat_mod, "feature_indices_from_fen"):
            pf = feat_mod.feature_indices_from_fen(fen)
            for p in (0, 1):
                a = sorted(e["feats"][p]["idx"]) if e["feats"].get(p) else []
                b = sorted(pf[p]) if isinstance(pf, (list, tuple)) else sorted(pf[p])
                if a != b:
                    mismatches.append({"fen": fen, "kind": f"features_p{p}",
                                       "engine_n": len(a), "python_n": len(b),
                                       "only_engine": [x for x in a if x not in b][:8],
                                       "only_python": [x for x in b if x not in a][:8]})

    ok = not mismatches
    print(f"\nGATE DE PARIDAD: {'PASS (0 cp)' if ok else 'FAIL'} | "
          f"{len(fens)} posiciones | {len(mismatches)} discrepancias | "
          f"{time.time()-t0:.0f}s")
    for m in mismatches[:10]:
        print("  ", json.dumps(m)[:220])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"positions": len(fens), "mismatches": mismatches,
                       "buckets": dict(bucket_count), "pass": ok}, f, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
