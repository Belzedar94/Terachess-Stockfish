#!/usr/bin/env python3
"""Trainer for the Terachess NNUE "S" network.

  * AdamW + exponential LR decay per epoch (optional linear warmup);
  * loss = |sigmoid(pred) - target|^p with the standard NNUE blend
    ``target = lambda * sigmoid(score/scaling) + (1-lambda) * WDL``
    (``--lambda 1.0`` by default: pure evaluation target);
  * ``model.clip_weights_()`` after EVERY optimizer step (contract section 5);
  * per-epoch validation on the lineage-disjoint split;
  * ATOMIC checkpoints holding model + optimizer + scheduler + every RNG
    state + the data cursor, and a ``--resume`` that reproduces the
    uninterrupted loss sequence step for step;
  * fixed seeds and deterministic kernels.

Every step's loss is appended to ``<out>/losses.jsonl``, which is what makes
the resume claim falsifiable rather than decorative.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch

import dataset as DS
import model as M
import quantized_forward as QF
import serialize

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------


def nnue_loss(pred: torch.Tensor, batch: DS.Batch, lam: float,
              scaling: float, power: float = 2.6) -> torch.Tensor:
    """NNUE evaluation/WDL blend.  ``pred`` is the model's float output."""
    q = pred * (QF.NNUE2SCORE / scaling)
    p = batch.score / scaling
    wdl_pred = torch.sigmoid(q)
    wdl_score = torch.sigmoid(p)
    # Records without a game result (result == 3) fall back to the score.
    wdl_result = batch.has_result * batch.result \
        + (1.0 - batch.has_result) * wdl_score
    target = lam * wdl_score + (1.0 - lam) * wdl_result
    return ((wdl_pred - target).abs() ** power).mean()


# ---------------------------------------------------------------------------
# Determinism and RNG bookkeeping
# ---------------------------------------------------------------------------


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    with contextlib.suppress(Exception):
        torch.use_deterministic_algorithms(True, warn_only=True)


def rng_state() -> dict:
    state = {"python": random.getstate(),
             "numpy": np.random.get_state(),
             "torch": torch.get_rng_state()}
    if torch.cuda.is_available():
        state["cuda"] = torch.cuda.get_rng_state_all()
    return state


def restore_rng(state: dict) -> None:
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"])
    if "cuda" in state and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state["cuda"])


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


def save_checkpoint(path: Path, payload: dict) -> None:
    """Atomic: write to a sibling temp file, fsync, then rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        torch.save(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load_checkpoint(path: Path) -> dict:
    return torch.load(path, map_location="cpu", weights_only=False)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def build_loaders(args) -> tuple[DS.TeraBinDataset, DS.TeraBinDataset]:
    filters = DS.Filters(max_abs_score=args.max_abs_score,
                         min_ply=args.min_ply,
                         require_result=args.require_result)
    common = dict(batch_size=args.batch_size, val_fraction=args.val_fraction,
                  seed=args.seed, filters=filters,
                  shuffle_buffer=args.shuffle_buffer, factorized=not args.no_factorizer)
    train = DS.TeraBinDataset(args.data, split="train", **common)
    val_path = args.val_data or args.data
    val = DS.TeraBinDataset(val_path, split="all" if args.val_data else "val",
                            **common)
    return train, val


@torch.no_grad()
def validate(net: M.TeraNNUE, loader, device, lam, scaling) -> dict:
    net.eval()
    total = weight = 0.0
    preds, labels = [], []
    for batch in loader:
        batch = batch.to(device, non_blocking=True)
        pred = net(batch.stm_idx, batch.stm_off, batch.nstm_idx,
                   batch.nstm_off, batch.buckets)
        loss = nnue_loss(pred, batch, lam, scaling)
        n = len(batch)
        total += float(loss.item()) * n
        weight += n
        preds.append((pred * QF.NNUE2SCORE).flatten().float().cpu())
        labels.append(batch.score.flatten().float().cpu())
    net.train()
    if weight == 0.0:
        return {"loss": float("nan"), "pearson": float("nan"), "n": 0}
    p = torch.cat(preds).numpy()
    y = torch.cat(labels).numpy()
    corr = float(np.corrcoef(p, y)[0, 1]) if p.std() > 0 and y.std() > 0 \
        else float("nan")
    return {"loss": total / weight, "pearson": corr, "n": int(weight)}


def train(args) -> dict:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device if args.device else
                          ("cuda" if torch.cuda.is_available() else "cpu"))
    seed_everything(args.seed)

    net = M.TeraNNUE(factorized=not args.no_factorizer,
                     psqt_init=args.psqt_init).to(device)
    net.clip_weights_()
    optimizer = torch.optim.AdamW(net.parameters(), lr=args.lr,
                                  weight_decay=args.weight_decay,
                                  betas=(0.9, 0.999), eps=1e-8)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer,
                                                       gamma=args.lr_gamma)
    train_ds, val_ds = build_loaders(args)

    start_epoch = 0
    start_step_in_epoch = 0
    global_step = 0
    history: list[dict] = []
    ckpt_path = out_dir / "checkpoint.pt"

    if args.resume:
        resume_path = Path(args.resume) if args.resume != "auto" else ckpt_path
        state = load_checkpoint(resume_path)
        net.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        restore_rng(state["rng"])
        start_epoch = state["epoch"]
        start_step_in_epoch = state["step_in_epoch"]
        global_step = state["global_step"]
        history = state.get("history", [])
        print(f"resumed from {resume_path}: epoch {start_epoch}, "
              f"step_in_epoch {start_step_in_epoch}, "
              f"global_step {global_step}")

    loss_log = open(out_dir / "losses.jsonl", "a", encoding="ascii")
    summary = {"epochs": [], "device": str(device)}
    stop = False

    for epoch in range(start_epoch, args.epochs):
        if stop:
            break
        train_ds.set_epoch(epoch)
        loader = DS.make_loader(train_ds, num_workers=args.workers,
                                pin_memory=(device.type == "cuda"))
        skip = start_step_in_epoch if epoch == start_epoch else 0
        epoch_start = time.time()
        seen = running = 0.0
        step_in_epoch = 0

        for batch in loader:
            if step_in_epoch < skip:
                step_in_epoch += 1
                continue
            batch = batch.to(device, non_blocking=True)
            pred = net(batch.stm_idx, batch.stm_off, batch.nstm_idx,
                       batch.nstm_off, batch.buckets)
            loss = nnue_loss(pred, batch, args.lambda_, args.scaling)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            if args.grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(net.parameters(), args.grad_clip)
            optimizer.step()
            net.clip_weights_()            # contract section 5, every step

            value = float(loss.item())
            running += value * len(batch)
            seen += len(batch)
            step_in_epoch += 1
            global_step += 1
            loss_log.write(json.dumps({"epoch": epoch, "step": global_step,
                                       "loss": value}) + "\n")
            if global_step % args.log_every == 0:
                loss_log.flush()
                print(f"  epoch {epoch} step {global_step:>6} "
                      f"loss {running / max(seen, 1):.6f} "
                      f"lr {optimizer.param_groups[0]['lr']:.3e}")
            if args.max_steps and global_step >= args.max_steps:
                stop = True
                break

        epoch_seconds = time.time() - epoch_start
        train_loss = running / max(seen, 1)
        if stop:
            save_checkpoint(ckpt_path, {
                "model": net.state_dict(), "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(), "rng": rng_state(),
                "epoch": epoch, "step_in_epoch": step_in_epoch,
                "global_step": global_step, "history": history,
                "args": vars(args)})
            print(f"stopped at global_step {global_step}; checkpoint written")
            break

        scheduler.step()
        val_ds.set_epoch(epoch)
        val_loader = DS.make_loader(val_ds, num_workers=args.workers,
                                    pin_memory=(device.type == "cuda"))
        stats = validate(net, val_loader, device, args.lambda_, args.scaling)
        record = {"epoch": epoch, "train_loss": train_loss,
                  "val_loss": stats["loss"], "val_pearson": stats["pearson"],
                  "val_positions": stats["n"], "seconds": epoch_seconds,
                  "positions": int(seen), "steps": step_in_epoch}
        history.append(record)
        summary["epochs"].append(record)
        print(f"epoch {epoch}: train {train_loss:.6f} | val {stats['loss']:.6f} "
              f"| val pearson {stats['pearson']:.4f} | {epoch_seconds:.1f}s "
              f"| {int(seen):,} positions")

        save_checkpoint(ckpt_path, {
            "model": net.state_dict(), "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(), "rng": rng_state(),
            "epoch": epoch + 1, "step_in_epoch": 0,
            "global_step": global_step, "history": history,
            "args": vars(args)})
        save_checkpoint(out_dir / f"epoch{epoch:03d}.pt",
                        {"model": net.state_dict(), "epoch": epoch + 1})
        start_step_in_epoch = 0

    loss_log.close()
    if args.export and not stop:
        net_cpu = net.to("cpu")
        if net_cpu.factorized:
            net_cpu.coalesce_factors_()
        _, size = serialize.save_model(net_cpu, out_dir / args.export)
        summary["tnn1_bytes"] = size
    summary["history"] = history
    with open(out_dir / "summary.json", "w", encoding="ascii") as handle:
        json.dump(summary, handle, indent=2)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data", type=Path)
    parser.add_argument("--val-data", type=Path, default=None,
                        help="separate validation file; otherwise the lineage "
                             "split of --data is used")
    parser.add_argument("--out", type=Path, default=Path("runs/dev"))
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=8192)
    parser.add_argument("--lr", type=float, default=8.75e-4)
    parser.add_argument("--lr-gamma", type=float, default=0.99)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--grad-clip", type=float, default=0.0)
    parser.add_argument("--lambda", dest="lambda_", type=float, default=1.0,
                        help="1.0 = pure score target, 0.0 = pure WDL")
    parser.add_argument("--scaling", type=float, default=1000.0,
                        help="centipawns per sigmoid unit")
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--device", type=str, default=None)
    parser.add_argument("--shuffle-buffer", type=int, default=65536)
    parser.add_argument("--val-fraction", type=float, default=0.05)
    parser.add_argument("--max-abs-score", type=int, default=32000)
    parser.add_argument("--min-ply", type=int, default=0)
    parser.add_argument("--require-result", action="store_true")
    parser.add_argument("--no-factorizer", action="store_true")
    parser.add_argument("--psqt-init", choices=("zero", "material"),
                        default="zero")
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--max-steps", type=int, default=0,
                        help="stop and checkpoint after N global steps")
    parser.add_argument("--resume", type=str, default=None,
                        help="'auto' for <out>/checkpoint.pt, or a path")
    parser.add_argument("--export", type=str, default="net.tnn1",
                        help="TNN1 filename written after the last epoch")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    summary = train(args)
    print(json.dumps({k: v for k, v in summary.items() if k != "history"},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
