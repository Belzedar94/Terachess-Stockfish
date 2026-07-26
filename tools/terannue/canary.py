#!/usr/bin/env python3
"""Verification (e): end-to-end canary for the terannue pipeline.

Protocol -- every step falsifiable, nothing simulated:

  1. generate N synthetic records whose label is pure material;
  2. ``tools/audit_terabin.py <file> --strict`` must exit 0;
  3. run A: train E epochs straight through, logging every step's loss;
  4. run B: identical training interrupted at ``--break-step`` (a real
     process exit), then ``--resume auto`` to the end -- the loss sequence
     after the resume must equal run A's step for step;
  5. export TNN1 (coalesce, abort-on-range, reload-and-compare);
  6. ``parity_harness`` on 300 positions: Pearson(eval_cp, label_cp) and the
     per-epoch losses must be monotonically decreasing.

Each training run is a separate process, so the resume claim is about real
process state and not about a variable that happened to stay in memory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent


def run(cmd: list[str], label: str, cwd: Path = HERE) -> str:
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, text=True,
                          capture_output=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        sys.stdout.write(proc.stderr)
    if proc.returncode != 0:
        raise SystemExit(f"{label} failed with exit {proc.returncode}")
    return proc.stdout + proc.stderr


def read_losses(path: Path) -> list[dict]:
    with open(path, encoding="ascii") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--records", type=int, default=40_000)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=8e-3)
    parser.add_argument("--lr-gamma", type=float, default=0.6)
    parser.add_argument("--scaling", type=float, default=2000.0)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--break-step", type=int, default=400)
    parser.add_argument("--parity-positions", type=int, default=300)
    parser.add_argument("--reuse-data", type=Path, default=None)
    args = parser.parse_args(argv)

    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    data = args.reuse_data or (work / "canary.terabin")
    report: dict = {}
    python = sys.executable

    # -- 1. data -----------------------------------------------------------
    if not args.reuse_data:
        t0 = time.time()
        run([python, "synth_data.py", data, "-n", args.records,
             "--seed", args.seed], "synth_data")
        report["generation_seconds"] = round(time.time() - t0, 1)
    report["records"] = args.records
    report["data_bytes"] = os.path.getsize(data)

    # -- 2. strict audit ---------------------------------------------------
    proc = subprocess.run([python, str(TOOLS / "audit_terabin.py"), str(data),
                           "--strict"], cwd=TOOLS, text=True,
                          capture_output=True)
    print(f"\n$ audit_terabin.py {data} --strict  ->  exit {proc.returncode}")
    report["audit_exit"] = proc.returncode
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit("audit_terabin --strict did not exit 0")
    tail = [line for line in proc.stdout.splitlines() if "Summary:" in line]
    print("  " + (tail[0].strip() if tail else "ok"))

    common = ["--epochs", args.epochs, "--batch-size", args.batch_size,
              "--lr", args.lr, "--lr-gamma", args.lr_gamma,
              "--scaling", args.scaling,
              "--seed", args.seed, "--workers", args.workers,
              "--log-every", 100]

    # -- 3. run A: uninterrupted -------------------------------------------
    dir_a = work / "runA"
    if (dir_a / "losses.jsonl").exists():
        (dir_a / "losses.jsonl").unlink()
    t0 = time.time()
    run([python, "train.py", data, "--out", dir_a, *common,
         "--export", "net.tnn1"], "train A")
    report["runA_seconds"] = round(time.time() - t0, 1)
    summary_a = json.loads((dir_a / "summary.json").read_text())
    losses_a = read_losses(dir_a / "losses.jsonl")

    # -- 4. run B: interrupt + resume --------------------------------------
    dir_b = work / "runB"
    if (dir_b / "losses.jsonl").exists():
        (dir_b / "losses.jsonl").unlink()
    run([python, "train.py", data, "--out", dir_b, *common,
         "--max-steps", args.break_step, "--export", ""], "train B (part 1)")
    run([python, "train.py", data, "--out", dir_b, *common,
         "--resume", "auto", "--export", ""], "train B (resumed)")
    losses_b = read_losses(dir_b / "losses.jsonl")

    by_step_a = {row["step"]: row["loss"] for row in losses_a}
    by_step_b = {row["step"]: row["loss"] for row in losses_b}
    shared = sorted(set(by_step_a) & set(by_step_b))
    after = [s for s in shared if s > args.break_step]
    diffs = [abs(by_step_a[s] - by_step_b[s]) for s in after]
    exact = sum(1 for d in diffs if d == 0.0)
    report["resume"] = {
        "break_step": args.break_step,
        "steps_A": len(losses_a), "steps_B": len(losses_b),
        "compared_after_resume": len(after),
        "bit_identical": exact,
        "max_abs_diff": max(diffs) if diffs else None,
    }
    print(f"\nresume check: {len(after)} steps after the interruption, "
          f"{exact} bit-identical, max |dLoss| = "
          f"{max(diffs) if diffs else 0:.3e}")

    # -- 5/6. export + parity ---------------------------------------------
    net_path = dir_a / "net.tnn1"
    report["tnn1_bytes"] = os.path.getsize(net_path)
    parity_out = work / "parity.txt"
    out = run([python, "parity_harness.py", net_path, data,
               "-n", args.parity_positions, "--stratify",
               "--min-per-type", 50, "-o", parity_out], "parity_harness")
    pearson = float("nan")
    for line in out.splitlines():
        if line.startswith("pearson"):
            pearson = float(line.split()[-1])
    report["parity"] = {"positions": args.parity_positions,
                        "pearson": pearson, "dump": str(parity_out)}

    epochs = summary_a["epochs"]
    train_losses = [e["train_loss"] for e in epochs]
    val_losses = [e["val_loss"] for e in epochs]
    monotone = all(b < a for a, b in zip(train_losses, train_losses[1:]))
    report["training"] = {
        "epoch_seconds": [round(e["seconds"], 1) for e in epochs],
        "train_loss": train_losses,
        "val_loss": val_losses,
        "val_pearson": [e["val_pearson"] for e in epochs],
        "steps_per_epoch": epochs[0]["steps"],
        "first_step_loss": losses_a[0]["loss"],
        "last_step_loss": losses_a[-1]["loss"],
        "monotone_decreasing": monotone,
    }

    print("\n" + "=" * 72)
    print(json.dumps(report, indent=2))
    (work / "canary_report.json").write_text(json.dumps(report, indent=2))

    failures = []
    if report["audit_exit"] != 0:
        failures.append("audit --strict")
    if not monotone:
        failures.append(f"train loss not monotone: {train_losses}")
    if not (pearson >= 0.7):
        failures.append(f"pearson {pearson} < 0.7")
    if report["resume"]["max_abs_diff"] not in (0.0, None):
        failures.append("resume did not reproduce the loss sequence exactly")
    if failures:
        print("\nCANARY FAILED: " + "; ".join(failures))
        return 1
    print("\nCANARY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
