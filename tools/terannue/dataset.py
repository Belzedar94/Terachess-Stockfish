#!/usr/bin/env python3
"""Streaming tera-bin dataset for the Terachess NNUE "S" trainer.

Properties the trainer depends on:

  * **streaming by chunks** -- the file is never materialised in RAM;
  * **sparse batches** -- flat index arrays plus CSR-style offsets, exactly
    what ``torch.nn.functional.embedding_bag`` consumes;
  * **labels** -- ``score`` (centipawns from the side to move) and ``result``
    (WDL from the side to move; ``result == 3`` is flagged, never invented);
  * **configurable filters** (``Filters``);
  * **split by game LINEAGE** -- games are detected by ply resets, and the
    train/validation decision is taken per GAME, so no position of a game
    can leak from one side of the split to the other;
  * **shuffle buffer** with a fixed seed;
  * **deterministic** -- the batch sequence is a pure function of
    ``(path, seed, epoch, batch_size, num_workers)``, which is what makes
    ``train.py --resume`` verifiable.

Multi-worker sharding is game-aligned: a worker skips the partial game at the
head of its byte range and runs past its tail until the next ply reset, so a
game is always produced whole and by exactly one worker.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

import features as FT
import terabin

RECORD_SIZE = terabin.RECORD_SIZE
HEADER_SIZE = terabin.HEADER_SIZE


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Filters:
    """Record-level acceptance rules.  Defaults accept everything legal."""

    max_abs_score: int = terabin.SCORE_LIMIT
    min_ply: int = 0
    max_ply: int = 65535
    min_pieces: int = 2
    max_pieces: int = terabin.MAX_PIECES
    max_rule50: int = 100
    require_result: bool = False        # drop result == 3 ("no result")
    drop_ep: bool = False               # drop positions with an e.p. square

    def accepts(self, record: terabin.Record) -> bool:
        if abs(record.score) > self.max_abs_score:
            return False
        if not self.min_ply <= record.ply <= self.max_ply:
            return False
        n = len(record.pieces)
        if not self.min_pieces <= n <= self.max_pieces:
            return False
        if record.rule50 > self.max_rule50:
            return False
        if self.require_result and record.result == 3:
            return False
        if self.drop_ep and record.ep_plus1:
            return False
        return True


# ---------------------------------------------------------------------------
# Deterministic lineage split
# ---------------------------------------------------------------------------


def mix64(value: int) -> int:
    """splitmix64 finaliser -- stable across runs, platforms and versions."""
    value = (value + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    value ^= value >> 30
    value = (value * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    value ^= value >> 27
    value = (value * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return value ^ (value >> 31)


def game_is_validation(game_id: int, seed: int, val_fraction: float) -> bool:
    if val_fraction <= 0.0:
        return False
    return (mix64(game_id ^ (seed * 0x2545F4914F6CDD1D)) % 1_000_000
            < val_fraction * 1_000_000)


# ---------------------------------------------------------------------------
# Raw streaming
# ---------------------------------------------------------------------------


def record_count(path: str | os.PathLike) -> int:
    with open(path, "rb") as handle:
        count, _, _ = terabin.read_header(handle)
        size = os.path.getsize(path)
        if size != HEADER_SIZE + count * RECORD_SIZE:
            raise ValueError(f"tera-bin size mismatch: {size} bytes for "
                             f"{count} records")
        return count


def _read_one(handle, index: int) -> terabin.Record:
    handle.seek(HEADER_SIZE + index * RECORD_SIZE)
    return terabin.unpack(handle.read(RECORD_SIZE))


def _aligned_start(handle, begin: int, count: int) -> int:
    """First index >= begin that starts a game (ply reset)."""
    if begin <= 0:
        return 0
    prev = _read_one(handle, begin - 1).ply
    index = begin
    while index < count:
        ply = _read_one(handle, index).ply
        if ply <= prev:
            return index
        prev = ply
        index += 1
    return count


def stream_games(path: str | os.PathLike, begin: int, end: int,
                 chunk_records: int = 2048):
    """Yield ``(game_id, record)`` for every game that starts in [begin, end).

    ``game_id`` is the absolute record index of the game's first record, so it
    is globally unique and identical for every worker and every epoch.
    """
    count = record_count(path)
    with open(path, "rb") as handle:
        index = _aligned_start(handle, begin, count)
        if index >= count:
            return
        handle.seek(HEADER_SIZE + index * RECORD_SIZE)
        game_id = index
        prev_ply = None
        while index < count:
            blob = handle.read(chunk_records * RECORD_SIZE)
            if not blob:
                return
            for offset in range(0, len(blob), RECORD_SIZE):
                record = terabin.unpack(blob[offset:offset + RECORD_SIZE])
                if prev_ply is not None and record.ply <= prev_ply:
                    game_id = index
                    if index >= end:
                        return
                prev_ply = record.ply
                yield game_id, record
                index += 1


# ---------------------------------------------------------------------------
# Batch assembly
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class Batch:
    stm_idx: torch.Tensor
    stm_off: torch.Tensor
    nstm_idx: torch.Tensor
    nstm_off: torch.Tensor
    buckets: torch.Tensor
    score: torch.Tensor
    result: torch.Tensor        # 0.0 loss / 0.5 draw / 1.0 win, POV stm
    has_result: torch.Tensor    # 0.0 when the record carries result == 3

    def to(self, device, non_blocking: bool = False) -> "Batch":
        return Batch(*[getattr(self, f.name).to(device,
                                                non_blocking=non_blocking)
                       for f in dataclasses.fields(self)])

    def __len__(self) -> int:
        return int(self.buckets.numel())


_RESULT_WDL = {0: 0.0, 1: 0.5, 2: 1.0, 3: 0.5}


def collate(records: list[terabin.Record], factorized: bool = True) -> Batch:
    stm_parts, nstm_parts = [], []
    stm_off = np.zeros(len(records) + 1, dtype=np.int64)
    nstm_off = np.zeros(len(records) + 1, dtype=np.int64)
    buckets = np.zeros(len(records), dtype=np.int64)
    score = np.zeros((len(records), 1), dtype=np.float32)
    result = np.zeros((len(records), 1), dtype=np.float32)
    has_result = np.zeros((len(records), 1), dtype=np.float32)

    for i, record in enumerate(records):
        stm, nstm, bucket = FT.record_features(record, factorized)
        stm_parts.append(stm)
        nstm_parts.append(nstm)
        stm_off[i + 1] = stm_off[i] + stm.size
        nstm_off[i + 1] = nstm_off[i] + nstm.size
        buckets[i] = bucket
        score[i, 0] = record.score
        result[i, 0] = _RESULT_WDL[record.result]
        has_result[i, 0] = 0.0 if record.result == 3 else 1.0

    return Batch(
        torch.from_numpy(np.concatenate(stm_parts)),
        torch.from_numpy(stm_off),
        torch.from_numpy(np.concatenate(nstm_parts)),
        torch.from_numpy(nstm_off),
        torch.from_numpy(buckets),
        torch.from_numpy(score),
        torch.from_numpy(result),
        torch.from_numpy(has_result))


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class TeraBinDataset(IterableDataset):
    """Yields ready-made :class:`Batch` objects (use ``batch_size=None``)."""

    def __init__(self, path: str | os.PathLike, batch_size: int = 8192,
                 split: str = "train", val_fraction: float = 0.05,
                 seed: int = 20260726, filters: Filters | None = None,
                 shuffle_buffer: int = 65536, factorized: bool = True,
                 chunk_records: int = 2048, drop_last: bool = True) -> None:
        if split not in ("train", "val", "all"):
            raise ValueError(f"unknown split {split!r}")
        self.path = Path(path)
        self.batch_size = batch_size
        self.split = split
        self.val_fraction = val_fraction
        self.seed = seed
        self.filters = filters or Filters()
        self.shuffle_buffer = shuffle_buffer
        self.factorized = factorized
        self.chunk_records = chunk_records
        self.drop_last = drop_last
        self.epoch = 0
        self.total_records = record_count(self.path)

    def set_epoch(self, epoch: int) -> None:
        self.epoch = int(epoch)

    def _wants(self, game_id: int) -> bool:
        if self.split == "all":
            return True
        is_val = game_is_validation(game_id, self.seed, self.val_fraction)
        return is_val if self.split == "val" else not is_val

    def _range(self) -> tuple[int, int, int]:
        info = get_worker_info()
        if info is None:
            return 0, self.total_records, 0
        per = -(-self.total_records // info.num_workers)
        begin = info.id * per
        return begin, min(begin + per, self.total_records), info.id

    def __iter__(self):
        begin, end, worker_id = self._range()
        if begin >= end:
            return
        rng = np.random.default_rng(
            [self.seed, self.epoch, worker_id,
             0 if self.split == "train" else 1])
        buffer: list[terabin.Record] = []
        pending: list[terabin.Record] = []
        capacity = max(1, self.shuffle_buffer)

        def flush_batch():
            batch = collate(pending, self.factorized)
            pending.clear()
            return batch

        for game_id, record in stream_games(self.path, begin, end,
                                            self.chunk_records):
            if not self._wants(game_id) or not self.filters.accepts(record):
                continue
            if len(buffer) < capacity:
                buffer.append(record)
                continue
            slot = int(rng.integers(capacity))
            pending.append(buffer[slot])
            buffer[slot] = record
            if len(pending) == self.batch_size:
                yield flush_batch()

        rng.shuffle(buffer)                       # drain deterministically
        for record in buffer:
            pending.append(record)
            if len(pending) == self.batch_size:
                yield flush_batch()
        if pending and not self.drop_last:
            yield flush_batch()


def make_loader(dataset: TeraBinDataset, num_workers: int = 2,
                pin_memory: bool = True, prefetch_factor: int = 4
                ) -> DataLoader:
    kwargs = {}
    if num_workers > 0:
        kwargs["prefetch_factor"] = prefetch_factor
        kwargs["persistent_workers"] = False
    return DataLoader(dataset, batch_size=None, num_workers=num_workers,
                      pin_memory=pin_memory, collate_fn=None, **kwargs)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def self_test(path: str | os.PathLike, verbose: bool = True) -> list[str]:
    log = print if verbose else (lambda *a, **k: None)
    ok = []

    total = record_count(path)
    ids = [gid for gid, _ in stream_games(path, 0, total)]
    games = sorted(set(ids))
    assert len(ids) == total, (len(ids), total)
    log(f"  records {total:,}, games {len(games):,}")
    ok.append("stream")

    # Sharded streaming must reproduce the single-shard game assignment.
    for workers in (2, 3, 5):
        per = -(-total // workers)
        merged = []
        for w in range(workers):
            begin = w * per
            merged.extend(stream_games(path, begin, min(begin + per, total)))
        assert len(merged) == total, (workers, len(merged), total)
        assert [g for g, _ in merged] == ids, f"{workers}-way shard mismatch"
    ok.append("shard-alignment")

    # Lineage split: no game id on both sides.
    train, val = set(), set()
    for gid in games:
        (val if game_is_validation(gid, 20260726, 0.1) else train).add(gid)
    assert not (train & val)
    log(f"  lineage split           : {len(train)} train / {len(val)} val games")
    ok.append("lineage-split")

    return ok


if __name__ == "__main__":
    import sys
    target = sys.argv[1]
    print("dataset self-test OK (%s)" % ", ".join(self_test(target)))
