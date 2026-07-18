#!/usr/bin/env python3
"""Gate F0: ejecuta fixtures.json contra una implementacion del oraculo.

Uso: python run_fixtures.py [--impl a|b|both] [--only NAME] [--json out.json]
Exit 0 si 0 discrepancias; 1 si hay fallos; 2 error estructural.
"""
import argparse, importlib.util, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_impl(tag):
    path = os.path.join(HERE, "terachess.py" if tag == "a" else "terachess_b.py")
    spec = importlib.util.spec_from_file_location(f"oracle_{tag}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(tag, fixtures, only=None):
    mod = load_impl(tag)
    fails = []
    for fx in fixtures:
        if only and fx["name"] != only:
            continue
        name, fen = fx["name"], fx["fen"]
        try:
            pos = mod.Position.from_fen(fen)
        except Exception as e:
            fails.append({"name": name, "impl": tag, "kind": "parse_error", "detail": repr(e)})
            continue
        try:
            rt = mod.Position.from_fen(pos.to_fen()).to_fen()
            if rt != pos.to_fen():
                fails.append({"name": name, "impl": tag, "kind": "fen_roundtrip", "detail": rt})
        except Exception as e:
            fails.append({"name": name, "impl": tag, "kind": "fen_error", "detail": repr(e)})
        if "moves" in fx:
            try:
                got = sorted(pos.legal_moves())
            except Exception as e:
                fails.append({"name": name, "impl": tag, "kind": "movegen_error", "detail": repr(e)})
                continue
            want = sorted(fx["moves"])
            if got != want:
                missing = [m for m in want if m not in got]
                extra = [m for m in got if m not in want]
                fails.append({"name": name, "impl": tag, "kind": "moves_mismatch",
                              "missing": missing, "extra": extra,
                              "n_want": len(want), "n_got": len(got)})
        if "perft" in fx:
            for d, want in fx["perft"].items():
                try:
                    got = mod.perft(pos, int(d))
                except Exception as e:
                    fails.append({"name": name, "impl": tag, "kind": "perft_error", "detail": repr(e)})
                    continue
                if got != want:
                    fails.append({"name": name, "impl": tag, "kind": "perft_mismatch",
                                  "depth": int(d), "want": want, "got": got})
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", default="both", choices=["a", "b", "both"])
    ap.add_argument("--only")
    ap.add_argument("--json")
    args = ap.parse_args()
    with open(os.path.join(HERE, "fixtures", "fixtures.json"), encoding="utf-8") as f:
        fixtures = json.load(f)
    names = [fx["name"] for fx in fixtures]
    if len(set(names)) != len(names):
        print("ERROR: nombres de fixture duplicados", file=sys.stderr)
        return 2
    t0 = time.time()
    tags = ["a", "b"] if args.impl == "both" else [args.impl]
    all_fails = []
    for tag in tags:
        all_fails += run(tag, fixtures, args.only)
    n = len(fixtures) if not args.only else 1
    print(f"fixtures: {n} | impls: {','.join(tags)} | fallos: {len(all_fails)} | {time.time()-t0:.1f}s")
    for f in all_fails:
        print(json.dumps(f, ensure_ascii=False))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(all_fails, fh, indent=1, ensure_ascii=False)
    return 1 if all_fails else 0


if __name__ == "__main__":
    sys.exit(main())
