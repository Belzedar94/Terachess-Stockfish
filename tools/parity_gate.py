#!/usr/bin/env python3
"""F3b: GATE DE PARIDAD motor <-> python, tolerancia EXACTAMENTE 0 cp.

Compara el volcado de referencia de `terannue/parity_harness.py` (autoridad,
enteros puros sin torch) contra los comandos `eval` y `features` del motor C++
sobre las MISMAS posiciones y la MISMA red TNN1.

Formato del volcado de referencia (parity_harness --features):
    <FEN> | <eval_cp> | <psqt> | <positional> | <bucket>
      stm  <indices...>
      nstm <indices...>

Formato esperado del motor (comandos de depuracion):
    eval     -> lineas "psqt <int>", "positional <int>", "total_cp <int>", "bucket <int>"
    features -> "persp <0|1> kbucket <n> count <n>" y luego los indices

Exit 0 SOLO si todas las comparaciones son identicas.

Uso:
  python parity_gate.py --engine ../src/stockfish.exe --net ../data/net1pre.tnn \
      --ref ../data/parity_ref.jsonl [--json out.json]
"""
import argparse, json, os, subprocess, sys, time


def load_ref(path):
    """Devuelve [(fen, eval, psqt, positional, bucket, {0:[...],1:[...]}), ...].

    stm = perspectiva 0, nstm = perspectiva 1 (convenio del contrato).
    """
    items, cur = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            if not s.strip():
                continue
            if s.startswith("  stm") or s.startswith("  nstm"):
                key = 0 if s.strip().startswith("stm") else 1
                cur["feats"][key] = [int(t) for t in s.split()[1:]]
            else:
                if cur:
                    items.append(cur)
                parts = [p.strip() for p in s.split("|")]
                cur = {"fen": parts[0], "eval": int(parts[1]), "psqt": int(parts[2]),
                       "positional": int(parts[3]), "bucket": int(parts[4]),
                       "feats": {}}
    if cur:
        items.append(cur)
    return items


def engine_dump(engine, net, fens, want_features=True):
    cmds = [f"setoption name EvalFile value {net}", "isready"]
    for fen in fens:
        cmds += [f"position fen {fen}", "eval"]
        if want_features:
            cmds.append("features")
    cmds.append("quit")
    out = subprocess.run([engine], input="\n".join(cmds) + "\n",
                         capture_output=True, text=True, timeout=3600)
    return out.stdout, out.stderr


def parse_engine(out, n_expected):
    """Trocea la salida en n_expected bloques de eval(+features)."""
    blocks, cur, persp = [], {}, None
    for line in out.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("psqt "):
            if cur.get("psqt") is not None:
                blocks.append(cur); cur, persp = {}, None
            cur["psqt"] = int(s.split()[1])
        elif low.startswith("positional "):
            cur["positional"] = int(s.split()[1])
        elif low.startswith("total_cp "):
            cur["total_cp"] = int(s.split()[1])
        elif low.startswith("bucket "):
            cur["bucket"] = int(s.split()[1])
        elif low.startswith("persp "):
            p = s.split()
            persp = int(p[1])
            cur.setdefault("feats", {})[persp] = []
        elif persp is not None and s and all(t.lstrip("-").isdigit() for t in s.split()):
            cur["feats"][persp].extend(int(t) for t in s.split())
    if cur:
        blocks.append(cur)
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--net", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--json")
    ap.add_argument("--no-features", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    ref = load_ref(args.ref)
    print(f"referencia: {len(ref)} posiciones | buckets "
          f"{sorted({r['bucket'] for r in ref})}")
    fens = [r["fen"] for r in ref]

    out, err = engine_dump(args.engine, os.path.abspath(args.net), fens,
                           not args.no_features)
    blocks = parse_engine(out, len(ref))
    print(f"motor: {len(blocks)} bloques de evaluacion")
    if len(blocks) != len(ref):
        print("ERROR: el motor no devolvio un bloque por posicion.")
        print("Primeras lineas de su salida:")
        for line in out.splitlines()[:15]:
            print("   ", line[:120])
        if err.strip():
            print("stderr:", err[:300])
        return 2

    mis = []
    for r, b in zip(ref, blocks):
        for k_ref, k_eng in (("psqt", "psqt"), ("positional", "positional"),
                             ("eval", "total_cp"), ("bucket", "bucket")):
            if k_eng not in b:
                mis.append({"fen": r["fen"], "kind": f"missing_{k_eng}"})
            elif r[k_ref] != b[k_eng]:
                mis.append({"fen": r["fen"], "kind": k_ref,
                            "python": r[k_ref], "engine": b[k_eng],
                            "delta": b[k_eng] - r[k_ref]})
        if not args.no_features and r["feats"]:
            for p in (0, 1):
                a = sorted(r["feats"].get(p, []))
                c = sorted(b.get("feats", {}).get(p, []))
                if a != c:
                    mis.append({"fen": r["fen"], "kind": f"features_p{p}",
                                "n_python": len(a), "n_engine": len(c),
                                "solo_python": [x for x in a if x not in c][:6],
                                "solo_motor": [x for x in c if x not in a][:6]})

    ok = not mis
    print(f"\nGATE DE PARIDAD: {'PASS (0 cp)' if ok else 'FAIL'} | "
          f"{len(ref)} posiciones | {len(mis)} discrepancias | {time.time()-t0:.0f}s")
    for m in mis[:12]:
        print("  ", json.dumps(m, ensure_ascii=False)[:230])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"positions": len(ref), "pass": ok, "mismatches": mis}, f,
                      indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
