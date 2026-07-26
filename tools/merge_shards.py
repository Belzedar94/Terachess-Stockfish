#!/usr/bin/env python3
"""Une shards de datagen (completos o en vuelo) en un tera-bin v1 valido.

Util para (a) instantaneas de una campana en curso y (b) fusionar campanas.
Solo copia registros COMPLETOS; verifica cada uno con terabin.unpack y descarta
(contandolos) los que no decodifiquen.

Uso: python merge_shards.py --out merged.bin data/campaign1.bin.*
"""
import argparse, glob, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import terabin  # noqa: E402

REC = 144
HDR = 32


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--validate", action="store_true",
                    help="decodifica cada registro (lento pero seguro)")
    ap.add_argument("shards", nargs="+")
    args = ap.parse_args()

    paths = []
    for pat in args.shards:
        paths.extend(sorted(glob.glob(pat)))
    paths = [p for p in paths if not p.endswith((".debug", ".json", ".meta"))]
    if not paths:
        print("sin shards")
        return 1

    total, bad = 0, 0
    tmp = args.out + ".tmp"
    with open(tmp, "wb") as out:
        out.write(b"\x00" * HDR)          # cabecera provisional
        for p in paths:
            data = open(p, "rb").read()
            payload = data[HDR:]
            n = len(payload) // REC
            kept = 0
            for i in range(n):
                rec = payload[i * REC:(i + 1) * REC]
                if args.validate:
                    try:
                        terabin.unpack(rec)
                    except Exception:
                        bad += 1
                        continue
                out.write(rec)
                kept += 1
            total += kept
            print(f"  {os.path.basename(p)}: {kept} registros")
    with open(tmp, "r+b") as out:
        out.seek(0)
        out.write(struct.pack("<4sHHQQQ", b"TC01", 1, REC, total, total, 0)
                  .ljust(HDR, b"\x00")[:HDR])
    os.replace(tmp, args.out)
    size = os.path.getsize(args.out)
    print(f"\n{args.out}: {total} registros, {size} bytes "
          f"(esperado {HDR + REC*total}) | descartados {bad}")
    return 0 if size == HDR + REC * total else 1


if __name__ == "__main__":
    sys.exit(main())
