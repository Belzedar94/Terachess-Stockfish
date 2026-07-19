#!/usr/bin/env python3
"""tera-bin v1 ("TC01") fixed-size training records for Terachess-Stockfish.

Normative byte contract: docs/tera-bin-v1.md.  FEN-TSF grammar and piece
letters: TERACHESS_SPEC.md section 3.  Python 3.12 stdlib only; deliberately
independent of the oracle and of the engine.

File layout:

  header  <4sHHQQQ>   magic "TC01", version 1, record_size 144, count,
                      source_count, flags                     (32 bytes)
  record  <4Q96s16s>  occupancy, 6-bit piece codes, metadata  (144 bytes)

Occupancy: word i covers squares 64*i .. 64*i+63; square = rank*16 + file
(a1 = bit 0 of word 0, p16 = bit 63 of word 3).  Piece codes: one 6-bit code
per occupied square in ascending square order, LSB-first bitstream with zero
padding.  Code = 1 + type_idx for white, 27 + type_idx for black, where
type_idx is the alphabetical index of the FEN-TSF letter (a=0 Amazon ...
z=25 Giraffe).  0 is invalid; 53-63 are reserved.

Metadata bitstream (LSB-first, 101 bits; the remaining bits of the 16 bytes
MUST be zero -- non-zero padding marks a corrupt record and is rejected):

  stm:1  king_jump:2  ep_plus1:9  rule50:7  fullmove:16  score:i16
  move:u32  ply:16  result:2

move packing: bits 0-7 destination, 8-15 origin, 16-21 promotion piece code
(0 = no promotion), 22-23 type (0 normal, 1 en passant, 2 king leap,
3 reserved), 24-31 reserved zero.

e.p. emission policy (aligned with the oracle's canonical emitter,
TERACHESS_SPEC.md 3.2): the record stores the raw e.p. square (ep_plus1),
but ``to_fen`` emits it only when the double-stepper is still on its landing
square and a side-to-move Pawn stands ready to capture onto the e.p. square;
otherwise "-".  ``from_fen`` stores whatever square the FEN carries, so
oracle-canonical FENs round-trip exactly.  Per the contract, ``from_fen``
saturates the halfmove clock at 100 and clamps the score to +-32000.
"""

from __future__ import annotations

import dataclasses
import os
import re
import struct
from collections.abc import Iterator

MAGIC = b"TC01"
VERSION = 1
HEADER = struct.Struct("<4sHHQQQ")
RECORD = struct.Struct("<4Q96s16s")
HEADER_SIZE = HEADER.size
RECORD_SIZE = RECORD.size
META_BITS = 101
MAX_PIECES = 128
SCORE_LIMIT = 32000

assert HEADER_SIZE == 32 and RECORD_SIZE == 144

FILES = "abcdefghijklmnop"
SQ_RE = re.compile(r"^([a-p])(\d{1,2})$")

W_KING, B_KING = 11, 37     # 'k' has type_idx 10 -> 1+10 / 27+10
W_PAWN, B_PAWN = 16, 42     # 'p' has type_idx 15

START_FEN = (
    "sjyhxfdoodfxhyjs/cmztuvlkalvutzmc/ernbwigqqgiwbnre/pppppppppppppppp/"
    "16/16/16/16/16/16/16/16/PPPPPPPPPPPPPPPP/ERNBWIGQQGIWBNRE/"
    "CMZTUVLKALVUTZMC/SJYHXFDOODFXHYJS w Kk - 0 1"
)


# ---------------------------------------------------------------------------
# Squares and piece codes
# ---------------------------------------------------------------------------


def sq_name(sq: int) -> str:
    if not 0 <= sq < 256:
        raise ValueError(f"square index {sq} outside [0, 255]")
    return FILES[sq & 15] + str((sq >> 4) + 1)


def parse_sq(name: str) -> int:
    m = SQ_RE.match(name)
    if not m:
        raise ValueError(f"bad square {name!r}")
    rank = int(m.group(2)) - 1
    if not 0 <= rank <= 15:
        raise ValueError(f"bad square rank in {name!r}")
    return rank * 16 + FILES.index(m.group(1))


def letter_to_code(letter: str) -> int:
    lower = letter.lower()
    if len(letter) != 1 or not "a" <= lower <= "z":
        raise ValueError(f"bad piece letter {letter!r}")
    idx = ord(lower) - 97
    return (1 + idx) if letter.isupper() else (27 + idx)


def code_to_letter(code: int) -> str:
    if not 1 <= code <= 52:
        raise ValueError(f"piece code {code} outside [1, 52]")
    letter = chr(97 + (code - 1) % 26)
    return letter.upper() if code <= 26 else letter


def pack_move(origin: int, dest: int, promo_code: int = 0, kind: int = 0) -> int:
    """Build the 32-bit move field (contract: dest 0-7, origin 8-15,
    promotion code 16-21, type 22-23, reserved 24-31 zero)."""
    if not 0 <= origin < 256 or not 0 <= dest < 256:
        raise ValueError("move origin/destination outside [0, 255]")
    if promo_code != 0 and not 1 <= promo_code <= 52:
        raise ValueError(f"promotion code {promo_code} outside {{0}} u [1, 52]")
    if kind not in (0, 1, 2):
        raise ValueError(f"move type {kind} invalid (3 is reserved)")
    return dest | (origin << 8) | (promo_code << 16) | (kind << 22)


def unpack_move(move: int) -> tuple[int, int, int, int]:
    """Return (origin, dest, promo_code, kind) from a 32-bit move field."""
    if not 0 <= move < (1 << 32) or move >> 24:
        raise ValueError(f"move field {move:#x} has non-zero reserved bits")
    return (move >> 8) & 255, move & 255, (move >> 16) & 63, (move >> 22) & 3


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class Record:
    """Decoded tera-bin position and training target.

    ``occupancy`` is a 4-tuple of u64 words; ``pieces`` holds one 6-bit code
    per occupied square in ascending square order.  ``stm`` is 0 for White,
    1 for Black; ``king_jump`` bit0/bit1 = white/black keeps the initial
    king leap; ``ep_plus1`` is 0 for no e.p. square, else square + 1;
    ``score`` is centipawns from the side to move; ``result`` is from the
    side to move: 0 loss, 1 draw, 2 win, 3 no result.
    """

    occupancy: tuple[int, int, int, int]
    pieces: tuple[int, ...]
    stm: int
    king_jump: int
    ep_plus1: int
    rule50: int
    fullmove: int
    score: int
    move: int
    ply: int
    result: int

    def __post_init__(self) -> None:
        occ = self.occupancy
        if len(occ) != 4 or any(not 0 <= w < (1 << 64) for w in occ):
            raise ValueError("occupancy must be four u64 words")
        n = sum(w.bit_count() for w in occ)
        if n > MAX_PIECES:
            raise ValueError(f"{n} occupied squares exceed the {MAX_PIECES} limit")
        if len(self.pieces) != n:
            raise ValueError(
                f"{len(self.pieces)} piece codes but popcount(occupancy) == {n}")
        wk = bk = 0
        for code in self.pieces:
            if not 1 <= code <= 52:
                raise ValueError(f"piece code {code} outside [1, 52]")
            wk += code == W_KING
            bk += code == B_KING
        if wk != 1 or bk != 1:
            raise ValueError(f"need exactly one king per color, got {wk}W/{bk}B")
        if self.stm not in (0, 1):
            raise ValueError(f"stm {self.stm} invalid")
        if not 0 <= self.king_jump <= 3:
            raise ValueError(f"king_jump {self.king_jump} outside [0, 3]")
        if not 0 <= self.ep_plus1 <= 256:
            raise ValueError(f"ep_plus1 {self.ep_plus1} outside {{0}} u [1, 256]")
        if not 0 <= self.rule50 <= 100:
            raise ValueError(f"rule50 {self.rule50} outside [0, 100]")
        if not 0 <= self.fullmove < (1 << 16):
            raise ValueError(f"fullmove {self.fullmove} does not fit in u16")
        if not -SCORE_LIMIT <= self.score <= SCORE_LIMIT:
            raise ValueError(f"score {self.score} outside +-{SCORE_LIMIT}")
        if not 0 <= self.move < (1 << 32):
            raise ValueError("move does not fit in u32")
        if self.move >> 24:
            raise ValueError("move reserved bits 24-31 must be zero")
        if (self.move >> 22) & 3 == 3:
            raise ValueError("move type 3 is reserved")
        if (self.move >> 16) & 63 > 52:
            raise ValueError("move promotion code outside {0} u [1, 52]")
        if not 0 <= self.ply < (1 << 16):
            raise ValueError(f"ply {self.ply} does not fit in u16")
        if self.result not in (0, 1, 2, 3):
            raise ValueError(f"result {self.result} outside [0, 3]")

    def board(self) -> list[int]:
        """256-element list of piece codes, 0 for empty squares."""
        bd = [0] * 256
        i = 0
        for word in range(4):
            bits = self.occupancy[word]
            base = word * 64
            while bits:
                lsb = bits & -bits
                bd[base + lsb.bit_length() - 1] = self.pieces[i]
                i += 1
                bits ^= lsb
        return bd


def split_board(bd: list[int]) -> tuple[tuple[int, int, int, int], tuple[int, ...]]:
    """(occupancy, pieces) from a 256-element code list."""
    if len(bd) != 256:
        raise ValueError("board must contain 256 squares")
    words = [0, 0, 0, 0]
    pieces = []
    for sq, code in enumerate(bd):
        if code:
            words[sq >> 6] |= 1 << (sq & 63)
            pieces.append(code)
    return (words[0], words[1], words[2], words[3]), tuple(pieces)


# ---------------------------------------------------------------------------
# LSB-first bitstreams
# ---------------------------------------------------------------------------


class _BitWriter:
    def __init__(self) -> None:
        self.value = 0
        self.cursor = 0

    def put(self, value: int, bits: int) -> None:
        if value < 0 or value >= (1 << bits):
            raise ValueError(f"{value} does not fit in {bits} bits")
        self.value |= value << self.cursor
        self.cursor += bits

    def signed(self, value: int, bits: int) -> None:
        self.put(value & ((1 << bits) - 1), bits)


class _BitReader:
    def __init__(self, value: int) -> None:
        self.value = value
        self.cursor = 0

    def get(self, bits: int) -> int:
        value = (self.value >> self.cursor) & ((1 << bits) - 1)
        self.cursor += bits
        return value

    def signed(self, bits: int) -> int:
        value = self.get(bits)
        return value - (1 << bits) if value & (1 << (bits - 1)) else value


# ---------------------------------------------------------------------------
# pack / unpack
# ---------------------------------------------------------------------------


def pack(record: Record) -> bytes:
    """144 exact bytes for a validated Record."""
    stream = 0
    for i, code in enumerate(record.pieces):
        stream |= code << (6 * i)
    b = _BitWriter()
    b.put(record.stm, 1)
    b.put(record.king_jump, 2)
    b.put(record.ep_plus1, 9)
    b.put(record.rule50, 7)
    b.put(record.fullmove, 16)
    b.signed(record.score, 16)
    b.put(record.move, 32)
    b.put(record.ply, 16)
    b.put(record.result, 2)
    assert b.cursor == META_BITS
    return RECORD.pack(*record.occupancy,
                       stream.to_bytes(96, "little"),
                       b.value.to_bytes(16, "little"))


def unpack(data: bytes | memoryview) -> Record:
    """Strict decoder: any contract violation raises ValueError."""
    if len(data) != RECORD_SIZE:
        raise ValueError(f"tera-bin record must be {RECORD_SIZE} bytes")
    o0, o1, o2, o3, pieces_raw, meta_raw = RECORD.unpack(data)
    occupancy = (o0, o1, o2, o3)
    n = sum(w.bit_count() for w in occupancy)
    if n > MAX_PIECES:
        raise ValueError(f"{n} occupied squares exceed the {MAX_PIECES} limit")
    stream = int.from_bytes(pieces_raw, "little")
    pieces = tuple((stream >> (6 * i)) & 63 for i in range(n))
    if stream >> (6 * n):
        raise ValueError("non-zero tera-bin piece padding")
    meta = int.from_bytes(meta_raw, "little")
    if meta >> META_BITS:
        raise ValueError("non-zero tera-bin metadata padding")
    b = _BitReader(meta)
    stm = b.get(1)
    king_jump = b.get(2)
    ep_plus1 = b.get(9)
    rule50 = b.get(7)
    fullmove = b.get(16)
    score = b.signed(16)
    move = b.get(32)
    ply = b.get(16)
    result = b.get(2)
    return Record(occupancy, pieces, stm, king_jump, ep_plus1, rule50,
                  fullmove, score, move, ply, result)


# ---------------------------------------------------------------------------
# File header and streaming
# ---------------------------------------------------------------------------


def write_header(file, count: int, source_count: int = 0, flags: int = 0) -> None:
    file.write(HEADER.pack(MAGIC, VERSION, RECORD_SIZE, count, source_count, flags))


def read_header(file) -> tuple[int, int, int]:
    """(count, source_count, flags); fails clean on foreign formats."""
    raw = file.read(HEADER_SIZE)
    if len(raw) != HEADER_SIZE:
        raise ValueError("truncated tera-bin header")
    magic, version, size, count, source_count, flags = HEADER.unpack(raw)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r} (expected {MAGIC!r})")
    if version != VERSION:
        raise ValueError(f"unsupported tera-bin version {version}")
    if size != RECORD_SIZE:
        raise ValueError(f"unsupported record size {size} (expected {RECORD_SIZE})")
    return count, source_count, flags


def iter_records(path: os.PathLike[str] | str,
                 limit: int | None = None) -> Iterator[Record]:
    with open(path, "rb") as file:
        count, _, _ = read_header(file)
        if limit is not None:
            count = min(count, limit)
        for index in range(count):
            raw = file.read(RECORD_SIZE)
            if len(raw) != RECORD_SIZE:
                raise ValueError(f"truncated payload at record {index}")
            yield unpack(raw)


# ---------------------------------------------------------------------------
# FEN-TSF (TERACHESS_SPEC.md section 3)
# ---------------------------------------------------------------------------


def _parse_board(board_s: str) -> list[int]:
    rows = board_s.split("/")
    if len(rows) != 16:
        raise ValueError("FEN board must have 16 ranks")
    bd = [0] * 256
    for i, row in enumerate(rows):
        r = 15 - i
        f = 0
        k = 0
        while k < len(row):
            ch = row[k]
            if ch.isdigit():
                # Greedy 2-digit read for 10..16 when it fits the rank.
                if ch == "1" and k + 1 < len(row) and row[k + 1].isdigit():
                    v2 = int(row[k:k + 2])
                    if v2 <= 16 and f + v2 <= 16:
                        f += v2
                        k += 2
                        continue
                v = int(ch)
                if v == 0 or f + v > 16:
                    raise ValueError(f"bad empty run in rank {r + 1}")
                f += v
                k += 1
            else:
                if f >= 16:
                    raise ValueError(f"rank {r + 1} overflow")
                bd[r * 16 + f] = letter_to_code(ch)
                f += 1
                k += 1
        if f != 16:
            raise ValueError(f"rank {r + 1} has width {f}")
    return bd


def _board_rows(bd: list[int]) -> str:
    rows = []
    for r in range(15, -1, -1):
        row = []
        run = 0
        base = r * 16
        for f in range(16):
            code = bd[base + f]
            if code == 0:
                run += 1
            else:
                if run:
                    row.append(str(run))
                    run = 0
                row.append(code_to_letter(code))
        if run:
            row.append(str(run))
        rows.append("".join(row))
    return "/".join(rows)


def _ep_capturable(bd: list[int], ep: int, stm: int) -> bool:
    """Canonical-emitter filter (spec 3.2, mirrors oracle _ep_capturable):
    emit the e.p. square only if a side-to-move Pawn stands ready to capture
    onto it and the double-stepper is still there to be taken."""
    white = stm == 0
    fw = 16 if white else -16
    base = ep - fw                      # victim square; capturers at base +- 1
    if not 0 <= base < 256:
        return False
    victim = bd[base]
    if victim == 0 or (victim <= 26) == white:
        return False                    # missing, or not an enemy piece
    pawn = W_PAWN if white else B_PAWN
    f = ep & 15
    if f > 0 and bd[base - 1] == pawn:
        return True
    if f < 15 and bd[base + 1] == pawn:
        return True
    return False


def to_fen(record: Record) -> str:
    bd = record.board()
    rights = (("K" if record.king_jump & 1 else "")
              + ("k" if record.king_jump & 2 else "")) or "-"
    if record.ep_plus1 and _ep_capturable(bd, record.ep_plus1 - 1, record.stm):
        ep_s = sq_name(record.ep_plus1 - 1)
    else:
        ep_s = "-"
    return "%s %s %s %s %d %d" % (
        _board_rows(bd),
        "b" if record.stm else "w",
        rights,
        ep_s,
        record.rule50,
        record.fullmove,
    )


def from_fen(fen: str, score: int = 0, move: int = 0, ply: int = 0,
             result: int = 3) -> Record:
    parts = fen.split()
    if len(parts) != 6:
        raise ValueError(f"FEN must have 6 fields, got {len(parts)}")
    board_s, turn, rights, ep_s, hm_s, fm_s = parts
    bd = _parse_board(board_s)
    if turn not in ("w", "b"):
        raise ValueError(f"bad turn {turn!r}")
    if rights == "-":
        king_jump = 0
    else:
        if not set(rights) <= {"K", "k"} or len(set(rights)) != len(rights):
            raise ValueError(f"bad king-leap rights {rights!r}")
        king_jump = ("K" in rights) | (("k" in rights) << 1)
    ep_plus1 = 0 if ep_s == "-" else parse_sq(ep_s) + 1
    halfmove = int(hm_s)
    if halfmove < 0:
        raise ValueError(f"negative halfmove clock {halfmove}")
    fullmove = int(fm_s)
    if not 1 <= fullmove < (1 << 16):
        raise ValueError(f"fullmove {fullmove} outside [1, 65535]")
    occupancy, pieces = split_board(bd)
    return Record(occupancy, pieces,
                  1 if turn == "b" else 0,
                  king_jump, ep_plus1,
                  min(halfmove, 100),                       # contract: saturated
                  fullmove,
                  max(-SCORE_LIMIT, min(SCORE_LIMIT, int(score))),
                  move, ply, result)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _raw(occupancy, codes, stm=0, king_jump=0, ep_plus1=0, rule50=0,
         fullmove=1, score=0, move=0, ply=0, result=3,
         pieces_extra=0, meta_extra=0) -> bytes:
    """Build raw record bytes without Record validation (for error cases)."""
    stream = 0
    for i, code in enumerate(codes):
        stream |= (code & 63) << (6 * i)
    stream |= pieces_extra
    b = _BitWriter()
    b.put(stm, 1)
    b.put(king_jump, 2)
    b.put(ep_plus1, 9)
    b.put(rule50, 7)
    b.put(fullmove, 16)
    b.signed(score, 16)
    b.put(move, 32)
    b.put(ply, 16)
    b.put(result, 2)
    return RECORD.pack(*occupancy, stream.to_bytes(96, "little"),
                       (b.value | meta_extra).to_bytes(16, "little"))


def _expect_error(fn, label: str) -> None:
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(f"{label}: expected ValueError")


def self_test() -> list[str]:
    ok = []

    # 1. Start position: 128 pieces, exact FEN and byte round-trips.
    rec0 = from_fen(START_FEN)
    assert sum(w.bit_count() for w in rec0.occupancy) == 128
    assert rec0.pieces.count(W_KING) == 1 and rec0.pieces.count(B_KING) == 1
    assert rec0.king_jump == 3 and rec0.stm == 0 and rec0.ep_plus1 == 0
    assert to_fen(rec0) == START_FEN, "startpos FEN round-trip failed"
    raw0 = pack(rec0)
    assert len(raw0) == RECORD_SIZE
    assert unpack(raw0) == rec0
    assert pack(unpack(raw0)) == raw0
    ok.append("startpos")

    # 2. Synthetic sparse records round-trip, including extreme meta values.
    move = pack_move(parse_sq("h2"), parse_sq("j3"), 0, 2)     # king leap
    bd = [0] * 256
    bd[parse_sq("h2")] = W_KING
    bd[parse_sq("h15")] = B_KING
    bd[parse_sq("d9")] = B_PAWN
    bd[parse_sq("e9")] = W_PAWN
    occupancy, pieces = split_board(bd)
    samples = [
        Record(occupancy, pieces, 0, 3, parse_sq("d10") + 1, 100, 65535,
               -32000, move, 65535, 0),
        Record(occupancy, pieces, 1, 0, 0, 0, 1, 32000,
               pack_move(0, 255, 52, 1), 0, 3),
        Record(occupancy, pieces, 1, 2, 256, 7, 412, -321, 0, 8191, 2),
    ]
    for i, rec in enumerate(samples):
        raw = pack(rec)
        assert unpack(raw) == rec, f"sample {i} record round-trip"
        assert pack(unpack(raw)) == raw, f"sample {i} byte round-trip"
    assert unpack_move(move) == (parse_sq("h2"), parse_sq("j3"), 0, 2)
    ok.append("sparse-round-trip")

    # 3. e.p. emission: capturable -> square, else "-" (oracle policy).
    cap = samples[0]
    assert " d10 " in to_fen(cap), "capturable e.p. must be emitted"
    bd2 = list(bd)
    bd2[parse_sq("e9")] = 0                     # no white pawn -> no emission
    occ2, pieces2 = split_board(bd2)
    quiet = Record(occ2, pieces2, 0, 3, parse_sq("d10") + 1, 3, 12, 0, 0, 4, 3)
    assert " - " in to_fen(quiet).replace(" w ", " "), "uncapturable e.p."
    assert from_fen(to_fen(cap)).ep_plus1 == parse_sq("d10") + 1
    # from_fen accepts the unconditional form and stores the raw square.
    assert from_fen(to_fen(quiet).replace(" - 3 ", " d10 3 ")).ep_plus1 \
        == parse_sq("d10") + 1
    ok.append("ep-policy")

    # 4. from_fen producer-side saturation and clamping (contract).
    sat = from_fen(to_fen(quiet).replace(" 3 12", " 250 12"), score=99999)
    assert sat.rule50 == 100 and sat.score == SCORE_LIMIT
    ok.append("saturation")

    # 5. Structural error cases: strict unpack must reject.
    kings = (11, 37)
    occ_k = (0b11, 0, 0, 0)
    assert unpack(_raw(occ_k, kings)).pieces == kings       # baseline is valid
    _expect_error(lambda: unpack(_raw((0b111, 0, 0, 0), (11, 11, 37))),
                  "two white kings")
    _expect_error(lambda: unpack(_raw((0b111, 0, 0, 0), (11, 37, 0))),
                  "piece code 0")
    _expect_error(lambda: unpack(_raw((0b111, 0, 0, 0), (11, 37, 53))),
                  "reserved piece code 53")
    _expect_error(lambda: unpack(_raw(occ_k, kings, pieces_extra=1 << 20)),
                  "dirty piece padding")
    _expect_error(lambda: unpack(_raw(occ_k, kings, meta_extra=1 << META_BITS)),
                  "dirty meta padding")
    _expect_error(lambda: unpack(_raw(occ_k, kings, ep_plus1=400)),
                  "ep_plus1 out of range")
    _expect_error(lambda: unpack(_raw(occ_k, kings, rule50=121)),
                  "rule50 out of range")
    _expect_error(lambda: unpack(_raw(occ_k, kings, result=3, move=3 << 22)),
                  "reserved move type 3")
    _expect_error(lambda: unpack(_raw(occ_k, kings, move=1 << 24)),
                  "move reserved bits")
    _expect_error(
        lambda: unpack(_raw(((1 << 64) - 1, (1 << 64) - 1, 1, 0), ())),
        "popcount 129")
    _expect_error(lambda: unpack(b"\x00" * 10), "short record")
    corrupt = bytearray(raw0)
    corrupt[143] = 0xFF                         # meta padding byte
    _expect_error(lambda: unpack(bytes(corrupt)), "corrupt startpos meta pad")
    _expect_error(lambda: Record(occ_k, (11, 11), 0, 0, 0, 0, 1, 0, 0, 0, 3),
                  "Record without black king")
    ok.append("error-cases")

    # 6. Header round-trip and foreign-format rejection.
    import io
    buf = io.BytesIO()
    write_header(buf, count=5, source_count=9, flags=0)
    assert buf.tell() == HEADER_SIZE
    buf.seek(0)
    assert read_header(buf) == (5, 9, 0)
    _expect_error(lambda: read_header(io.BytesIO(b"XX01" + b"\x00" * 28)),
                  "bad magic")
    _expect_error(
        lambda: read_header(io.BytesIO(HEADER.pack(MAGIC, 2, 144, 0, 0, 0))),
        "bad version")
    _expect_error(
        lambda: read_header(io.BytesIO(HEADER.pack(MAGIC, 1, 44, 0, 0, 0))),
        "bad record size")
    _expect_error(lambda: read_header(io.BytesIO(b"TC0")), "short header")
    ok.append("header")

    # 7. iter_records streaming.
    buf = io.BytesIO()
    write_header(buf, count=2, source_count=2)
    buf.write(pack(rec0))
    buf.write(pack(samples[0]))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.terabin")
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        assert list(iter_records(path)) == [rec0, samples[0]]
        assert list(iter_records(path, limit=1)) == [rec0]
    ok.append("iter-records")

    return ok


if __name__ == "__main__":
    names = self_test()
    print("terabin self-test OK (%s); header=%d, record=%d"
          % (", ".join(names), HEADER_SIZE, RECORD_SIZE))
