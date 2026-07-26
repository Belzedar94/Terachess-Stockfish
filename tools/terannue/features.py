#!/usr/bin/env python3
"""Feature indexing for the Terachess NNUE "S" network.

NORMATIVE SOURCE: ``docs/nnue-tera-s.md`` (frozen contract v1).  This module
implements sections 1-3 of that document and nothing else; every constant here
traces back to a line of the contract.  ``tools/terabin.py`` is imported, never
duplicated.

Contract recap (verbatim semantics):

  * Two perspectives.  Index 0 = side to move, index 1 = the other side.
  * View: ``vsq = (c == WHITE) ? sq : sq ^ 0xF0``.
  * Horizontal mirror conditioned on the perspective's OWN king: let ``vksq``
    be the view of that king's square; if ``file(vksq) < 8`` then ``x ^ 0x0F``
    is applied to every square of that perspective, ``vksq`` included.  After
    the mirror ``file(vksq) in [8, 15]``.
  * King buckets, with ``r = vksq >> 4`` and ``f = vksq & 15`` post-mirror::

        band_r = r < 2 ? 0 : r < 4 ? 1 : r < 8 ? 2 : 3
        band_f = f < 12 ? 0 : 1
        bucket = band_r * 2 + band_f

  * 51 planes: 0..24 non-king pieces of the perspective's side in engine type
    order (``plane = type - PAWN``), 25..49 the same 25 classes of the other
    side, 50 both kings.
  * ``feature = KING_BUCKET[vksq] * 13056 + plane * 256 + vsq_mirrored``.
  * ``MaxActiveDimensions = 128`` (one feature per piece per perspective).
  * Output bucket: ``min(7, (pieceCount - 1) / 16)``.

Train-only virtual factors (contract section 6) live in the index space right
after the 104448 real rows; ``serialize.py`` coalesces them away on export so
the engine only ever sees real rows.
"""

from __future__ import annotations

import os
import sys

import numpy as np

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import terabin  # noqa: E402  (tools/terabin.py, already verified; do not fork)

# ---------------------------------------------------------------------------
# Engine piece types (docs/port-256-design.md) and FEN-TSF letters (spec 3.1)
# ---------------------------------------------------------------------------

WHITE, BLACK = 0, 1

PIECE_TYPE_ORDER = (
    "PAWN", "KNIGHT", "BISHOP", "ROOK", "QUEEN", "CAMEL", "GIRAFFE",
    "ELEPHANT", "MACHINE", "PRINCE", "TROLL", "ARCHER", "CANNON", "CENTAUR",
    "MISSIONARY", "ADMIRAL", "CARDINAL", "MARSHALL", "BUFFALO", "DUCHESS",
    "LION", "RHINO", "SORCERESS", "EAGLE", "AMAZON", "KING",
)
PAWN = 1
KING = 26
PIECE_TYPE = {name: PAWN + i for i, name in enumerate(PIECE_TYPE_ORDER)}
assert PIECE_TYPE["KING"] == KING and len(PIECE_TYPE_ORDER) == 26

# FEN-TSF letter -> engine PieceType (TERACHESS_SPEC.md 3.1).
LETTER_TO_TYPE = {
    "a": PIECE_TYPE["AMAZON"],     "b": PIECE_TYPE["BISHOP"],
    "c": PIECE_TYPE["CANNON"],     "d": PIECE_TYPE["DUCHESS"],
    "e": PIECE_TYPE["ELEPHANT"],   "f": PIECE_TYPE["BUFFALO"],
    "g": PIECE_TYPE["EAGLE"],      "h": PIECE_TYPE["MARSHALL"],
    "i": PIECE_TYPE["PRINCE"],     "j": PIECE_TYPE["CENTAUR"],
    "k": PIECE_TYPE["KING"],       "l": PIECE_TYPE["LION"],
    "m": PIECE_TYPE["CAMEL"],      "n": PIECE_TYPE["KNIGHT"],
    "o": PIECE_TYPE["SORCERESS"],  "p": PIECE_TYPE["PAWN"],
    "q": PIECE_TYPE["QUEEN"],      "r": PIECE_TYPE["ROOK"],
    "s": PIECE_TYPE["ADMIRAL"],    "t": PIECE_TYPE["TROLL"],
    "u": PIECE_TYPE["RHINO"],      "v": PIECE_TYPE["ARCHER"],
    "w": PIECE_TYPE["MACHINE"],    "x": PIECE_TYPE["CARDINAL"],
    "y": PIECE_TYPE["MISSIONARY"], "z": PIECE_TYPE["GIRAFFE"],
}
assert len(set(LETTER_TO_TYPE.values())) == 26

# ---------------------------------------------------------------------------
# Contract dimensions
# ---------------------------------------------------------------------------

NUM_SQ = 256
NUM_PLANES = 51
KING_PLANE = 50
NUM_KING_BUCKETS = 8
FEATURES_PER_BUCKET = NUM_PLANES * NUM_SQ            # 13056
NUM_REAL_FEATURES = NUM_KING_BUCKETS * FEATURES_PER_BUCKET   # 104448
MAX_ACTIVE = 128
NUM_OUTPUT_BUCKETS = 8

assert FEATURES_PER_BUCKET == 13056 and NUM_REAL_FEATURES == 104448

# Train-only virtual factors (contract section 6), appended after the real rows.
FACTOR_A_OFFSET = NUM_REAL_FEATURES                  # piece-square, 51 x 256
FACTOR_A_SIZE = NUM_PLANES * NUM_SQ                  # 13056
FACTOR_TYPE_OFFSET = FACTOR_A_OFFSET + FACTOR_A_SIZE
FACTOR_TYPE_SIZE = NUM_PLANES                        # 51
ROYAL_SPAN = 15                                      # clamp(delta, +-7) -> 15
ROYAL_OFFSETS = ROYAL_SPAN * ROYAL_SPAN              # 225
FACTOR_ROYAL_OFFSET = FACTOR_TYPE_OFFSET + FACTOR_TYPE_SIZE
FACTOR_ROYAL_SIZE = NUM_PLANES * ROYAL_OFFSETS       # 11475
NUM_VIRTUAL_FEATURES = FACTOR_A_SIZE + FACTOR_TYPE_SIZE + FACTOR_ROYAL_SIZE
NUM_TOTAL_FEATURES = NUM_REAL_FEATURES + NUM_VIRTUAL_FEATURES   # 129030

assert FACTOR_A_SIZE == 13056 and FACTOR_ROYAL_SIZE == 11475
assert NUM_TOTAL_FEATURES == 129030

# ---------------------------------------------------------------------------
# tera-bin piece code tables (terabin: 1 + type_idx white, 27 + type_idx black,
# type_idx = alphabetical index of the FEN-TSF letter)
# ---------------------------------------------------------------------------

CODE_COLOR = np.full(53, -1, dtype=np.int8)
CODE_TYPE = np.zeros(53, dtype=np.int8)
for _code in range(1, 53):
    _letter = chr(97 + (_code - 1) % 26)
    CODE_COLOR[_code] = WHITE if _code <= 26 else BLACK
    CODE_TYPE[_code] = LETTER_TO_TYPE[_letter]

# PLANE[perspective_color][code] -> plane index 0..50.
PLANE = np.zeros((2, 53), dtype=np.int16)
for _persp in (WHITE, BLACK):
    for _code in range(1, 53):
        _t = int(CODE_TYPE[_code])
        if _t == KING:
            PLANE[_persp, _code] = KING_PLANE
        else:
            _own = int(CODE_COLOR[_code]) == _persp
            PLANE[_persp, _code] = (_t - PAWN) if _own else 25 + (_t - PAWN)

W_KING_CODE = terabin.W_KING          # 11
B_KING_CODE = terabin.B_KING          # 37
assert int(CODE_TYPE[W_KING_CODE]) == KING and int(CODE_TYPE[B_KING_CODE]) == KING

# Zillions piece values x100 (TERACHESS_SPEC.md section 8), indexed by code.
from audit_terabin import CODE_VALUE_CP  # noqa: E402


# ---------------------------------------------------------------------------
# Scalar reference implementation (the readable authority)
# ---------------------------------------------------------------------------


def orient(color: int, sq: int, mirror: bool) -> int:
    """Contract section 1: rank flip for Black, then the conditional mirror."""
    v = sq if color == WHITE else sq ^ 0xF0
    return (v ^ 0x0F) if mirror else v


def mirror_needed(color: int, king_sq: int) -> bool:
    """True when ``file(view(king)) < 8`` and the perspective must be mirrored."""
    vk = king_sq if color == WHITE else king_sq ^ 0xF0
    return (vk & 15) < 8


def king_bucket(vksq: int) -> int:
    """Contract section 2.  ``vksq`` MUST already be viewed and mirrored."""
    r = vksq >> 4
    f = vksq & 15
    band_r = 0 if r < 2 else 1 if r < 4 else 2 if r < 8 else 3
    band_f = 0 if f < 12 else 1
    return band_r * 2 + band_f


def plane_of(persp_color: int, code: int) -> int:
    """Plane 0..50 for a tera-bin piece ``code`` seen from ``persp_color``."""
    return int(PLANE[persp_color, code])


def feature_index(persp_color: int, king_sq: int, code: int, sq: int) -> int:
    """``KING_BUCKET[vksq] * 13056 + plane * 256 + vsq_mirrored``.

    ``king_sq`` is the raw (unoriented) square of the perspective's OWN king.
    """
    mirror = mirror_needed(persp_color, king_sq)
    vksq = orient(persp_color, king_sq, mirror)
    vsq = orient(persp_color, sq, mirror)
    return (king_bucket(vksq) * FEATURES_PER_BUCKET
            + plane_of(persp_color, code) * NUM_SQ
            + vsq)


def _clamp7(d: int) -> int:
    return -7 if d < -7 else 7 if d > 7 else d


def royal_offset(vsq: int, vksq: int) -> int:
    """``clamp(dfile, +-7) x clamp(drank, +-7)`` packed file-major into 0..224."""
    df = _clamp7((vsq & 15) - (vksq & 15))
    dr = _clamp7((vsq >> 4) - (vksq >> 4))
    return (df + 7) * ROYAL_SPAN + (dr + 7)


def virtual_indices(persp_color: int, king_sq: int, code: int, sq: int
                    ) -> tuple[int, int, int]:
    """The three train-only factor rows for one (piece, square) observation."""
    mirror = mirror_needed(persp_color, king_sq)
    vksq = orient(persp_color, king_sq, mirror)
    vsq = orient(persp_color, sq, mirror)
    plane = plane_of(persp_color, code)
    return (FACTOR_A_OFFSET + plane * NUM_SQ + vsq,
            FACTOR_TYPE_OFFSET + plane,
            FACTOR_ROYAL_OFFSET + plane * ROYAL_OFFSETS
            + royal_offset(vsq, vksq))


def output_bucket(piece_count: int) -> int:
    """Contract section 4: ``min(7, (pieceCount - 1) / 16)``."""
    return min(NUM_OUTPUT_BUCKETS - 1, (piece_count - 1) // 16)


# ---------------------------------------------------------------------------
# Position view over a terabin.Record
# ---------------------------------------------------------------------------


def occupied_squares(record: terabin.Record) -> np.ndarray:
    """Ascending occupied squares, aligned with ``record.pieces`` by contract."""
    raw = b"".join(w.to_bytes(8, "little") for w in record.occupancy)
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")
    return np.flatnonzero(bits).astype(np.int32)


class PositionView:
    """Everything the feature indexer needs, decoded once per record."""

    __slots__ = ("squares", "codes", "stm", "king_sq", "piece_count", "bucket")

    def __init__(self, squares: np.ndarray, codes: np.ndarray, stm: int) -> None:
        self.squares = squares
        self.codes = codes
        self.stm = stm
        wk = squares[codes == W_KING_CODE]
        bk = squares[codes == B_KING_CODE]
        if wk.size != 1 or bk.size != 1:
            raise ValueError("position needs exactly one king per colour")
        self.king_sq = (int(wk[0]), int(bk[0]))     # indexed by colour
        self.piece_count = int(squares.size)
        self.bucket = output_bucket(self.piece_count)

    @classmethod
    def from_record(cls, record: terabin.Record) -> "PositionView":
        return cls(occupied_squares(record),
                   np.asarray(record.pieces, dtype=np.int16),
                   record.stm)

    @classmethod
    def from_fen(cls, fen: str) -> "PositionView":
        return cls.from_record(terabin.from_fen(fen))

    def perspective_color(self, perspective: int) -> int:
        """0 -> side to move, 1 -> the other side (contract section 1)."""
        return self.stm if perspective == 0 else 1 - self.stm


# ---------------------------------------------------------------------------
# Vectorised indexing (the path the DataLoader uses)
# ---------------------------------------------------------------------------


def _oriented(view: PositionView, color: int) -> tuple[np.ndarray, int]:
    """(mirrored view squares of every piece, mirrored view of the own king)."""
    king_sq = view.king_sq[color]
    mirror = mirror_needed(color, king_sq)
    vsq = view.squares.astype(np.int32)
    vk = king_sq
    if color == BLACK:
        vsq = vsq ^ 0xF0
        vk = vk ^ 0xF0
    if mirror:
        vsq = vsq ^ 0x0F
        vk = vk ^ 0x0F
    return vsq, vk


def real_indices(view: PositionView, perspective: int) -> np.ndarray:
    """Active real feature rows for one perspective (one per piece)."""
    color = view.perspective_color(perspective)
    vsq, vk = _oriented(view, color)
    planes = PLANE[color][view.codes].astype(np.int32)
    return (king_bucket(vk) * FEATURES_PER_BUCKET
            + planes * NUM_SQ + vsq).astype(np.int64)


def all_indices(view: PositionView, perspective: int, factorized: bool = True
                ) -> np.ndarray:
    """Active rows for one perspective; with ``factorized`` the three
    train-only factor rows per piece are appended (contract section 6)."""
    color = view.perspective_color(perspective)
    vsq, vk = _oriented(view, color)
    planes = PLANE[color][view.codes].astype(np.int32)
    plane_sq = planes * NUM_SQ + vsq
    real = king_bucket(vk) * FEATURES_PER_BUCKET + plane_sq
    if not factorized:
        return real.astype(np.int64)
    df = np.clip((vsq & 15) - (vk & 15), -7, 7)
    dr = np.clip((vsq >> 4) - (vk >> 4), -7, 7)
    royal = (FACTOR_ROYAL_OFFSET + planes * ROYAL_OFFSETS
             + (df + 7) * ROYAL_SPAN + (dr + 7))
    return np.concatenate((real,
                           FACTOR_A_OFFSET + plane_sq,
                           FACTOR_TYPE_OFFSET + planes,
                           royal)).astype(np.int64)


def record_features(record: terabin.Record, factorized: bool = True
                    ) -> tuple[np.ndarray, np.ndarray, int]:
    """(stm rows, nstm rows, output bucket) for one tera-bin record."""
    view = PositionView.from_record(record)
    return (all_indices(view, 0, factorized),
            all_indices(view, 1, factorized),
            view.bucket)


def material_cp(record: terabin.Record) -> int:
    """Zillions material from the side to move (spec section 8)."""
    total = 0
    for code in record.pieces:
        value = CODE_VALUE_CP[code]
        white = code <= 26
        total += value if white else -value
    return total if record.stm == 0 else -total


# ---------------------------------------------------------------------------
# Self-test (verification (a) of the trainer brief)
# ---------------------------------------------------------------------------


def self_test(verbose: bool = True) -> list[str]:
    ok: list[str] = []
    log = print if verbose else (lambda *a, **k: None)

    view = PositionView.from_fen(terabin.START_FEN)
    assert view.piece_count == 128, view.piece_count
    assert view.stm == WHITE

    # -- king squares --------------------------------------------------------
    wk, bk = view.king_sq
    assert wk == terabin.parse_sq("h2") == 23, wk
    assert bk == terabin.parse_sq("h15") == 231, bk

    # -- hand-computed indices ----------------------------------------------
    # White king h2 -> sq 23; White view keeps it; file(23) = 7 < 8 so the
    # whole White perspective is mirrored: vksq = 23 ^ 0x0F = 24 (r=1, f=8).
    # band_r = 0 (r < 2), band_f = 0 (f < 12)  ->  king bucket 0.
    assert mirror_needed(WHITE, wk) is True
    assert orient(WHITE, wk, True) == 24
    assert king_bucket(24) == 0

    # (1) White Pawn on f4 from the White perspective.
    #     sq(f4) = 3*16 + 5 = 53; vsq = 53; mirrored = 53 ^ 0x0F = 58.
    #     Own Pawn -> plane = PAWN - PAWN = 0.
    #     feature = 0*13056 + 0*256 + 58 = 58.
    f4 = terabin.parse_sq("f4")
    assert f4 == 53, f4
    hand_pawn = 0 * FEATURES_PER_BUCKET + 0 * NUM_SQ + 58
    assert hand_pawn == 58
    got = feature_index(WHITE, wk, terabin.letter_to_code("P"), f4)
    assert got == hand_pawn, (got, hand_pawn)
    log(f"  white Pawn f4 / White perspective : hand {hand_pawn:6d} == {got:6d}")

    # (2) Black Amazon (i15, sq 232) from the White perspective.
    #     vsq = 232; mirrored = 232 ^ 0x0F = 231.  Opponent Amazon ->
    #     plane = 25 + (AMAZON - PAWN) = 25 + 24 = 49.
    #     feature = 0*13056 + 49*256 + 231 = 12775.
    i15 = terabin.parse_sq("i15")
    assert i15 == 232, i15
    hand_amazon_w = 0 * FEATURES_PER_BUCKET + 49 * NUM_SQ + 231
    assert hand_amazon_w == 12775
    got = feature_index(WHITE, wk, terabin.letter_to_code("a"), i15)
    assert got == hand_amazon_w, (got, hand_amazon_w)
    log(f"  black Amazon i15 / White persp.   : hand {hand_amazon_w:6d} == {got:6d}")

    # (3) The same Black Amazon from the Black perspective.
    #     Black view: vsq = 232 ^ 0xF0 = 24; own king h15 -> 231 ^ 0xF0 = 23,
    #     file 7 < 8 so mirror: vksq = 24 (bucket 0), vsq = 24 ^ 0x0F = 23.
    #     Own Amazon -> plane = AMAZON - PAWN = 24.
    #     feature = 0*13056 + 24*256 + 23 = 6167.
    assert mirror_needed(BLACK, bk) is True
    assert orient(BLACK, bk, True) == 24
    hand_amazon_b = 0 * FEATURES_PER_BUCKET + 24 * NUM_SQ + 23
    assert hand_amazon_b == 6167
    got = feature_index(BLACK, bk, terabin.letter_to_code("a"), i15)
    assert got == hand_amazon_b, (got, hand_amazon_b)
    log(f"  black Amazon i15 / Black persp.   : hand {hand_amazon_b:6d} == {got:6d}")
    ok.append("hand-indices")

    # -- 128 active rows per perspective, all distinct and in range ----------
    for persp in (0, 1):
        rows = real_indices(view, persp)
        assert rows.size == 128, rows.size
        assert len(set(rows.tolist())) == 128, "duplicate feature row"
        assert rows.min() >= 0 and rows.max() < NUM_REAL_FEATURES
    log(f"  active rows per perspective       : {real_indices(view, 0).size}")
    ok.append("startpos-128")

    # -- vectorised path agrees with the scalar reference, everywhere --------
    rng = np.random.default_rng(20260726)
    fens = [terabin.START_FEN]
    for _ in range(40):
        bd = [0] * 256
        squares = rng.choice(256, size=int(rng.integers(2, 60)), replace=False)
        bd[int(squares[0])] = W_KING_CODE
        bd[int(squares[1])] = B_KING_CODE
        for sq in squares[2:]:
            code = int(rng.integers(1, 53))
            while code in (W_KING_CODE, B_KING_CODE):
                code = int(rng.integers(1, 53))
            bd[int(sq)] = code
        occ, pieces = terabin.split_board(bd)
        rec = terabin.Record(occ, pieces, int(rng.integers(0, 2)), 0, 0, 0, 1,
                             0, 0, 0, 3)
        fens.append(terabin.to_fen(rec))
    checked = 0
    for fen in fens:
        v = PositionView.from_fen(fen)
        for persp in (0, 1):
            color = v.perspective_color(persp)
            ksq = v.king_sq[color]
            vec = all_indices(v, persp, factorized=True)
            n = v.piece_count
            for i in range(n):
                code = int(v.codes[i])
                sq = int(v.squares[i])
                assert vec[i] == feature_index(color, ksq, code, sq)
                va, vt, vr = virtual_indices(color, ksq, code, sq)
                assert vec[n + i] == va and vec[2 * n + i] == vt
                assert vec[3 * n + i] == vr
                checked += 1
            assert vec.min() >= 0 and vec.max() < NUM_TOTAL_FEATURES
    log(f"  scalar vs vectorised observations : {checked}")
    ok.append("scalar-vs-vector")

    # -- output buckets ------------------------------------------------------
    assert output_bucket(128) == 7 and output_bucket(2) == 0
    assert output_bucket(17) == 1 and output_bucket(16) == 0
    assert output_bucket(113) == 7 and output_bucket(112) == 6
    ok.append("output-buckets")

    # -- mirror invariant: post-mirror own king always lands on files i..p ---
    for color in (WHITE, BLACK):
        for sq in range(256):
            vk = orient(color, sq, mirror_needed(color, sq))
            assert 8 <= (vk & 15) <= 15, (color, sq, vk)
            assert 0 <= king_bucket(vk) < NUM_KING_BUCKETS
    ok.append("mirror-invariant")

    # -- every king bucket is reachable and the plane table is a bijection ---
    buckets = {king_bucket(orient(WHITE, sq, mirror_needed(WHITE, sq)))
               for sq in range(256)}
    assert buckets == set(range(8)), buckets
    for persp in (WHITE, BLACK):
        planes = sorted(int(PLANE[persp, c]) for c in range(1, 53))
        assert planes == sorted(list(range(50)) + [50, 50]), planes[:5]
    ok.append("plane-table")

    return ok


if __name__ == "__main__":
    names = self_test()
    print("features self-test OK (%s); real=%d, virtual=%d, total=%d"
          % (", ".join(names), NUM_REAL_FEATURES, NUM_VIRTUAL_FEATURES,
             NUM_TOTAL_FEATURES))
