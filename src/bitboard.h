/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef BITBOARD_H_INCLUDED
#define BITBOARD_H_INCLUDED

#include <algorithm>
#include <cassert>
#include <cstring>
#include <string>

#include "types.h"
#include "misc.h"

// 256-square board layer (16x16). Sliders use ray-scan over precomputed rays
// (frozen contract, docs/port-256-design.md); magic bitboards are gone.

namespace Stockfish {

namespace Bitboards {

void        init();
std::string pretty(Bitboard b);

}  // namespace Stockfish::Bitboards

constexpr Bitboard AllSquaresBB = Bitboard(~0ULL, ~0ULL, ~0ULL, ~0ULL);

constexpr Bitboard square_bb(Square s) {
    assert(is_ok(s));
    u64 nw[4] = {0, 0, 0, 0};
    nw[s >> 6] = 1ULL << (s & 63);
    return Bitboard(nw[0], nw[1], nw[2], nw[3]);
}

// rank_bb() and file_bb() return a bitboard representing all the squares on
// the given file or rank. Each 64-bit word holds 4 ranks of 16 squares; a
// file occupies bit positions f, f+16, f+32, f+48 of every word.

constexpr Bitboard rank_bb(Rank r) {
    u64 nw[4]  = {0, 0, 0, 0};
    nw[r >> 2] = 0xFFFFULL << ((r & 3) * 16);
    return Bitboard(nw[0], nw[1], nw[2], nw[3]);
}

constexpr Bitboard rank_bb(Square s) { return rank_bb(rank_of(s)); }

constexpr Bitboard file_bb(File f) {
    const u64 col = 0x0001000100010001ULL << f;
    return Bitboard(col, col, col, col);
}

constexpr Bitboard file_bb(Square s) { return file_bb(file_of(s)); }

constexpr Bitboard FileABB  = file_bb(FILE_A);
constexpr Bitboard FilePBB  = file_bb(FILE_P);
constexpr Bitboard Rank1BB  = rank_bb(RANK_1);
constexpr Bitboard Rank16BB = rank_bb(RANK_16);


// Overloads of bitwise operators between a Bitboard and a Square for testing
// whether a given bit is set in a bitboard, and for setting and clearing bits.

constexpr Bitboard  operator&(Bitboard b, Square s) { return b & square_bb(s); }
constexpr Bitboard  operator|(Bitboard b, Square s) { return b | square_bb(s); }
constexpr Bitboard  operator^(Bitboard b, Square s) { return b ^ square_bb(s); }
constexpr Bitboard& operator|=(Bitboard& b, Square s) { return b |= square_bb(s); }
constexpr Bitboard& operator^=(Bitboard& b, Square s) { return b ^= square_bb(s); }

constexpr Bitboard operator&(Square s, Bitboard b) { return b & s; }
constexpr Bitboard operator|(Square s, Bitboard b) { return b | s; }
constexpr Bitboard operator^(Square s, Bitboard b) { return b ^ s; }

constexpr Bitboard operator|(Square s1, Square s2) { return square_bb(s1) | s2; }

constexpr bool more_than_one(Bitboard b) { return bool(b & (b - 1)); }


// Moves a bitboard one or two steps as specified by the direction D
template<Direction D>
constexpr Bitboard shift(Bitboard b) {
    return D == NORTH         ? b << 16
         : D == SOUTH         ? b >> 16
         : D == NORTH + NORTH ? b << 32
         : D == SOUTH + SOUTH ? b >> 32
         : D == EAST          ? (b & ~FilePBB) << 1
         : D == WEST          ? (b & ~FileABB) >> 1
         : D == NORTH_EAST    ? (b & ~FilePBB) << 17
         : D == NORTH_WEST    ? (b & ~FileABB) << 15
         : D == SOUTH_EAST    ? (b & ~FilePBB) >> 15
         : D == SOUTH_WEST    ? (b & ~FileABB) >> 17
                              : Bitboard();
}


// Returns the squares attacked by pawns of the given color
// from the squares in the given bitboard.
template<Color C>
constexpr Bitboard pawn_attacks_bb(Bitboard b) {
    return C == WHITE ? shift<NORTH_WEST>(b) | shift<NORTH_EAST>(b)
                      : shift<SOUTH_WEST>(b) | shift<SOUTH_EAST>(b);
}

constexpr Bitboard pawn_single_push_bb(Color c, Bitboard b) {
    return c == WHITE ? shift<NORTH>(b) : shift<SOUTH>(b);
}

inline int edge_distance(File f) { return std::min(int(f), int(FILE_P - f)); }


// Per-word scan helpers

inline int popcount64(u64 v) {
#ifndef USE_POPCNT
    extern u8 PopCnt16[1 << 16];  // defined in bitboard.cpp
    u16       quarters[4];
    std::memcpy(quarters, &v, sizeof(v));
    return PopCnt16[quarters[0]] + PopCnt16[quarters[1]] + PopCnt16[quarters[2]]
         + PopCnt16[quarters[3]];
#elif defined(_MSC_VER)
    return int(_mm_popcnt_u64(v));
#else
    return __builtin_popcountll(v);
#endif
}

inline int lsb64(u64 v) {
    assert(v);
#if defined(__GNUC__)
    return __builtin_ctzll(v);
#elif defined(_MSC_VER) && defined(_WIN64)
    unsigned long idx;
    _BitScanForward64(&idx, v);
    return int(idx);
#else
    #error "Compiler not supported."
#endif
}

inline int msb64(u64 v) {
    assert(v);
#if defined(__GNUC__)
    return 63 ^ __builtin_clzll(v);
#elif defined(_MSC_VER) && defined(_WIN64)
    unsigned long idx;
    _BitScanReverse64(&idx, v);
    return int(idx);
#else
    #error "Compiler not supported."
#endif
}

// Counts the number of non-zero bits in a bitboard.
inline int popcount(Bitboard b) {
    return popcount64(b.w[0]) + popcount64(b.w[1]) + popcount64(b.w[2]) + popcount64(b.w[3]);
}

// Returns the least significant bit in a non-zero bitboard.
inline Square lsb(Bitboard b) {
    assert(bool(b));
    for (int i = 0; i < 3; ++i)
        if (b.w[i])
            return Square(64 * i + lsb64(b.w[i]));
    return Square(192 + lsb64(b.w[3]));
}

// Returns the most significant bit in a non-zero bitboard.
inline Square msb(Bitboard b) {
    assert(bool(b));
    for (int i = 3; i > 0; --i)
        if (b.w[i])
            return Square(64 * i + msb64(b.w[i]));
    return Square(msb64(b.w[0]));
}

// Returns the bitboard of the least significant
// square of a non-zero bitboard. It is equivalent to square_bb(lsb(bb)).
inline Bitboard least_significant_square_bb(Bitboard b) {
    assert(bool(b));
    return b & -b;
}

// Finds and clears the least significant bit in a non-zero bitboard.
inline Square pop_lsb(Bitboard& b) {
    assert(bool(b));
    for (int i = 0; i < 4; ++i)
        if (b.w[i])
        {
            const int bit = lsb64(b.w[i]);
            b.w[i] &= b.w[i] - 1;
            return Square(64 * i + bit);
        }
    return SQ_NONE;  // unreachable
}


// The 8 ray directions. Order matters: 0-3 orthogonal, 4-7 diagonal, and
// opposite(d) = d ^ 2 within each group.
enum RayDir : int {
    RAY_N  = 0,
    RAY_E  = 1,
    RAY_S  = 2,
    RAY_W  = 3,
    RAY_NE = 4,
    RAY_SE = 5,
    RAY_SW = 6,
    RAY_NW = 7,
    RAY_NB = 8
};

constexpr Direction RayDirection[RAY_NB] = {NORTH,      EAST,       SOUTH,      WEST,
                                            NORTH_EAST, SOUTH_EAST, SOUTH_WEST, NORTH_WEST};

// True if the direction has a positive square delta (scan with lsb; else msb)
constexpr bool RayPositive[RAY_NB] = {true, true, false, false, true, false, false, true};

constexpr int opposite_ray(int d) { return d ^ 2; }

// Direction-set masks for hoppers (bit d set = ray direction d included)
constexpr int ORTHO_RAYS = 0x0F;
constexpr int DIAG_RAYS  = 0xF0;
constexpr int ALL_RAYS   = 0xFF;

// Ray[s][d]: squares strictly beyond s in ray direction d, up to the edge
extern Bitboard Ray[SQUARE_NB][RAY_NB];

// LineBB[s1][s2]: full line (rank/file/diagonal) through aligned s1, s2,
// both included; empty if not aligned.
// BetweenBB[s1][s2]: squares strictly between aligned s1 and s2, plus s2;
// just s2 if not aligned (Stockfish master semantics).
extern Bitboard LineBB[SQUARE_NB][SQUARE_NB];
extern Bitboard BetweenBB[SQUARE_NB][SQUARE_NB];

// Empty-board attack sets: full leaper/step patterns, and slider masks for
// ray pieces. TROLL holds only its (3,3)/(0,3) jumps (the pawn-step part is
// generated in movegen); PRINCE holds its king-step pattern (double push in
// movegen); KING holds the plain 8-step pattern (initial jump in movegen).
// CANNON/ARCHER/SORCERESS hold the corresponding empty-board slider mask
// (candidate superset for both quiet slides and screen captures).
extern Bitboard PseudoAttacks[PIECE_TYPE_NB][SQUARE_NB];
extern Bitboard PawnAttacks[COLOR_NB][SQUARE_NB];

// Single-step atoms used by compound pieces: F-step (diagonal) and W-step
// (orthogonal) king steps.
extern Bitboard DiagStepBB[SQUARE_NB];
extern Bitboard OrthStepBB[SQUARE_NB];

inline Bitboard line_bb(Square s1, Square s2) {
    assert(is_ok(s1) && is_ok(s2));
    return LineBB[s1][s2];
}

inline Bitboard between_bb(Square s1, Square s2) {
    assert(is_ok(s1) && is_ok(s2));
    return BetweenBB[s1][s2];
}

inline bool aligned(Square s1, Square s2, Square s3) { return bool(line_bb(s1, s2) & s3); }


// Slider attacks along one ray by ray-scan: first blocker cuts the ray.
inline Bitboard ray_attacks(Square s, int d, Bitboard occupied) {
    Bitboard       attacks  = Ray[s][d];
    const Bitboard blockers = attacks & occupied;
    if (bool(blockers))
        attacks ^= Ray[RayPositive[d] ? lsb(blockers) : msb(blockers)][d];
    return attacks;
}

inline Bitboard ray_attacks_mask(Square s, int dirs, Bitboard occupied) {
    Bitboard attacks;
    for (int d = 0; d < RAY_NB; ++d)
        if ((dirs >> d) & 1)
            attacks |= ray_attacks(s, d, occupied);
    return attacks;
}

// Bent riders (spec 4.1). Returned set has standard move-or-capture
// semantics; the caller masks out own pieces.
Bitboard eagle_attacks(Square s, Bitboard occupied);
Bitboard rhino_attacks(Square s, Bitboard occupied);

// Screen pieces (spec 4.2). hopper_quiet: normal sliding, no captures
// (stops BEFORE the first blocker). hopper_captures: per direction, the
// second blocker B2 if it exists (caller checks that B2 is an enemy).
inline Bitboard hopper_quiet(Square s, Bitboard occupied, int dirs) {
    return ray_attacks_mask(s, dirs, occupied) & ~occupied;
}

Bitboard hopper_captures(Square s, Bitboard occupied, int dirs);


// Returns the attacks by the given piece type with the given occupancy.
// Hoppers (CANNON/ARCHER/SORCERESS) are excluded: their move and capture
// sets differ, use hopper_quiet()/hopper_captures(). PAWN uses PawnAttacks.
template<PieceType Pt>
inline Bitboard attacks_bb(Square s, Bitboard occupied) {
    static_assert(Pt != PAWN && Pt != CANNON && Pt != ARCHER && Pt != SORCERESS
                    && Pt != NO_PIECE_TYPE,
                  "attacks_bb: unsupported piece type");
    assert(is_ok(s));

    switch (Pt)
    {
    case ROOK :
        return ray_attacks_mask(s, ORTHO_RAYS, occupied);
    case BISHOP :
        return ray_attacks_mask(s, DIAG_RAYS, occupied);
    case QUEEN :
        return ray_attacks_mask(s, ALL_RAYS, occupied);
    case ADMIRAL :
        return ray_attacks_mask(s, ORTHO_RAYS, occupied) | DiagStepBB[s];
    case MISSIONARY :
        return ray_attacks_mask(s, DIAG_RAYS, occupied) | OrthStepBB[s];
    case MARSHALL :
        return ray_attacks_mask(s, ORTHO_RAYS, occupied) | PseudoAttacks[KNIGHT][s];
    case CARDINAL :
        return ray_attacks_mask(s, DIAG_RAYS, occupied) | PseudoAttacks[KNIGHT][s];
    case AMAZON :
        return ray_attacks_mask(s, ALL_RAYS, occupied) | PseudoAttacks[KNIGHT][s];
    case EAGLE :
        return eagle_attacks(s, occupied);
    case RHINO :
        return rhino_attacks(s, occupied);
    default :  // pure leapers/steppers ignore occupancy
        return PseudoAttacks[Pt][s];
    }
}

// Runtime-dispatch version of the above
inline Bitboard attacks_bb(PieceType pt, Square s, Bitboard occupied) {
    assert(pt != PAWN && pt != NO_PIECE_TYPE && is_ok(s));

    switch (pt)
    {
    case ROOK :
        return attacks_bb<ROOK>(s, occupied);
    case BISHOP :
        return attacks_bb<BISHOP>(s, occupied);
    case QUEEN :
        return attacks_bb<QUEEN>(s, occupied);
    case ADMIRAL :
        return attacks_bb<ADMIRAL>(s, occupied);
    case MISSIONARY :
        return attacks_bb<MISSIONARY>(s, occupied);
    case MARSHALL :
        return attacks_bb<MARSHALL>(s, occupied);
    case CARDINAL :
        return attacks_bb<CARDINAL>(s, occupied);
    case AMAZON :
        return attacks_bb<AMAZON>(s, occupied);
    case EAGLE :
        return eagle_attacks(s, occupied);
    case RHINO :
        return rhino_attacks(s, occupied);
    case CANNON :
    case ARCHER :
    case SORCERESS :
        assert(false && "hoppers: use hopper_quiet()/hopper_captures()");
        return Bitboard();
    default :
        return PseudoAttacks[pt][s];
    }
}

}  // namespace Stockfish

#endif  // #ifndef BITBOARD_H_INCLUDED
