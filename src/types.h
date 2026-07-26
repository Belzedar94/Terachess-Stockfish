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

#ifndef TYPES_H_INCLUDED
    #define TYPES_H_INCLUDED

// Terachess-Stockfish 256-square board layer. Representation contract:
// docs/port-256-design.md (frozen). Rules authority: TERACHESS_SPEC.md.
//
// When compiling with provided Makefile (e.g. for Linux and OSX), configuration
// is done automatically. To get started type 'make help'.
//
// When Makefile is not used (e.g. with Microsoft Visual Studio) some switches
// need to be set manually:
//
// -DNDEBUG      | Disable debugging mode. Always use this for release.
//
// -DNO_PREFETCH | Disable use of prefetch asm-instruction. You may need this to
//               | run on some very old machines.
//
// -DUSE_POPCNT  | Add runtime support for use of popcnt asm-instruction. Works
//               | only in 64-bit mode and requires hardware with popcnt support.

    #include <cassert>
    #include <cstddef>
    #include <cstdint>
    #include <type_traits>
    #include "misc.h"

    #if defined(_MSC_VER)
        // Disable some silly and noisy warnings from MSVC compiler
        #pragma warning(disable: 4127)  // Conditional expression is constant
        #pragma warning(disable: 4146)  // Unary minus operator applied to unsigned type
        #pragma warning(disable: 4800)  // Forcing value to bool 'true' or 'false'
    #endif

// Predefined macros hell:
//
// __GNUC__                Compiler is GCC, Clang or ICX
// __clang__               Compiler is Clang or ICX
// __INTEL_LLVM_COMPILER   Compiler is ICX
// _MSC_VER                Compiler is MSVC
// _WIN32                  Building on Windows (any)
// _WIN64                  Building on Windows 64 bit

    // Enforce minimum GCC version
    #if defined(__GNUC__) && !defined(__clang__) \
      && (__GNUC__ < 9 || (__GNUC__ == 9 && __GNUC_MINOR__ < 3))
        #error "Stockfish requires GCC 9.3 or later for correct compilation"
    #endif

    // Enforce minimum Clang version
    #if defined(__clang__) && (__clang_major__ < 11)
        #error "Stockfish requires Clang 11.0 or later for correct compilation"
    #endif

    #define ASSERT_ALIGNED(ptr, alignment) assert(reinterpret_cast<uintptr_t>(ptr) % alignment == 0)

    #if defined(_WIN64) && defined(_MSC_VER)  // No Makefile used
        #include <intrin.h>                   // Microsoft header for _BitScanForward64()
        #define IS_64BIT
    #endif

    #if defined(USE_POPCNT) && defined(_MSC_VER)
        #include <nmmintrin.h>  // Microsoft header for _mm_popcnt_u64()
    #endif

    #if !defined(NO_PREFETCH) && defined(_MSC_VER)
        #include <xmmintrin.h>  // Microsoft header for _mm_prefetch()
    #endif

namespace Stockfish {

    #ifdef USE_POPCNT
constexpr bool HasPopCnt = true;
    #else
constexpr bool HasPopCnt = false;
    #endif

    #ifdef IS_64BIT
constexpr bool Is64Bit = true;
    #else
constexpr bool Is64Bit = false;
    #endif

using Key = u64;

// A 256-bit bitboard for the 16x16 board: w[0] holds squares 0-63,
// w[1] squares 64-127, w[2] squares 128-191, w[3] squares 192-255.
// Pattern adapted from Fairy-Stockfish-VLB types.h (same owner, GPLv3),
// with the word order reversed so that w[0] is the LOW word.
struct Bitboard {
    u64 w[4];

    constexpr Bitboard() :
        w{0, 0, 0, 0} {}
    constexpr Bitboard(u64 lo) :
        w{lo, 0, 0, 0} {}
    constexpr Bitboard(u64 w0, u64 w1, u64 w2, u64 w3) :
        w{w0, w1, w2, w3} {}

    constexpr explicit operator bool() const { return w[0] || w[1] || w[2] || w[3]; }

    constexpr bool operator==(const Bitboard& x) const {
        return w[0] == x.w[0] && w[1] == x.w[1] && w[2] == x.w[2] && w[3] == x.w[3];
    }
    constexpr bool operator!=(const Bitboard& x) const { return !(*this == x); }

    constexpr Bitboard operator~() const { return Bitboard(~w[0], ~w[1], ~w[2], ~w[3]); }

    constexpr Bitboard operator&(const Bitboard& x) const {
        return Bitboard(w[0] & x.w[0], w[1] & x.w[1], w[2] & x.w[2], w[3] & x.w[3]);
    }
    constexpr Bitboard operator|(const Bitboard& x) const {
        return Bitboard(w[0] | x.w[0], w[1] | x.w[1], w[2] | x.w[2], w[3] | x.w[3]);
    }
    constexpr Bitboard operator^(const Bitboard& x) const {
        return Bitboard(w[0] ^ x.w[0], w[1] ^ x.w[1], w[2] ^ x.w[2], w[3] ^ x.w[3]);
    }

    constexpr Bitboard& operator&=(const Bitboard& x) { return *this = *this & x; }
    constexpr Bitboard& operator|=(const Bitboard& x) { return *this = *this | x; }
    constexpr Bitboard& operator^=(const Bitboard& x) { return *this = *this ^ x; }

    constexpr Bitboard operator<<(unsigned bits) const {
        if (bits == 0)
            return *this;
        if (bits >= 256)
            return Bitboard();
        const unsigned q = bits >> 6, r = bits & 63;
        u64            nw[4] = {0, 0, 0, 0};
        for (unsigned i = q; i < 4; ++i)
        {
            nw[i] = w[i - q] << r;
            if (r && i > q)
                nw[i] |= w[i - q - 1] >> (64 - r);
        }
        return Bitboard(nw[0], nw[1], nw[2], nw[3]);
    }

    constexpr Bitboard operator>>(unsigned bits) const {
        if (bits == 0)
            return *this;
        if (bits >= 256)
            return Bitboard();
        const unsigned q = bits >> 6, r = bits & 63;
        u64            nw[4] = {0, 0, 0, 0};
        for (unsigned i = 0; i + q < 4; ++i)
        {
            nw[i] = w[i + q] >> r;
            if (r && i + q + 1 < 4)
                nw[i] |= w[i + q + 1] << (64 - r);
        }
        return Bitboard(nw[0], nw[1], nw[2], nw[3]);
    }

    constexpr Bitboard operator<<(int bits) const { return *this << unsigned(bits); }
    constexpr Bitboard operator>>(int bits) const { return *this >> unsigned(bits); }

    constexpr Bitboard& operator<<=(int bits) { return *this = *this << bits; }
    constexpr Bitboard& operator>>=(int bits) { return *this = *this >> bits; }

    // 256-bit subtraction with borrow propagation (needed by b & (b - 1) idioms)
    constexpr Bitboard operator-(const Bitboard& x) const {
        u64  nw[4]  = {0, 0, 0, 0};
        bool borrow = false;
        for (int i = 0; i < 4; ++i)
        {
            const u64 xi         = x.w[i] + u64(borrow);
            const bool wrapped   = borrow && x.w[i] == ~0ULL;  // x.w[i] + 1 overflowed
            nw[i]                = w[i] - xi;
            borrow               = wrapped || w[i] < xi;
        }
        return Bitboard(nw[0], nw[1], nw[2], nw[3]);
    }

    constexpr Bitboard operator-(int x) const { return *this - Bitboard(u64(x)); }

    // Two's complement (needed by b & -b)
    constexpr Bitboard operator-() const { return Bitboard() - *this; }

    constexpr Bitboard& operator-=(const Bitboard& x) { return *this = *this - x; }
};

// Terachess: 64 pieces per side on 256 squares. Empirical upper bound for a
// single position's move count; re-measure in F1d (contract).
constexpr int MAX_MOVES = 2048;
constexpr int MAX_PLY   = 246;

enum Color : u8 {
    WHITE,
    BLACK,
    COLOR_NB = 2
};

// King's initial-jump rights (Metamachy rule, spec 6.3). Replaces castling.
enum KingJumpRights : u8 {
    NO_KING_JUMP = 0,
    WHITE_JUMP   = 1,
    BLACK_JUMP   = 2,
    KING_JUMP_RIGHTS_NB = 4
};

constexpr KingJumpRights king_jump_right(Color c) { return c == WHITE ? WHITE_JUMP : BLACK_JUMP; }

constexpr KingJumpRights operator&(Color c, KingJumpRights kjr) {
    return KingJumpRights(king_jump_right(c) & kjr);
}

enum Bound : u8 {
    BOUND_NONE,
    BOUND_UPPER,
    BOUND_LOWER,
    BOUND_EXACT = BOUND_UPPER | BOUND_LOWER
};

// Value is used as an alias for int, this is done to differentiate between a search
// value and any other integer value. The values used in search are always supposed
// to be in the range (-VALUE_NONE, VALUE_NONE] and should not exceed this range.
using Value = int;

constexpr Value VALUE_ZERO     = 0;
constexpr Value VALUE_DRAW     = 0;
constexpr Value VALUE_NONE     = 32002;
constexpr Value VALUE_INFINITE = 32001;

constexpr Value VALUE_MATE             = 32000;
constexpr Value VALUE_MATE_IN_MAX_PLY  = VALUE_MATE - MAX_PLY;
constexpr Value VALUE_MATED_IN_MAX_PLY = -VALUE_MATE_IN_MAX_PLY;

constexpr Value VALUE_TB                 = VALUE_MATE_IN_MAX_PLY - 1;
constexpr Value VALUE_TB_WIN_IN_MAX_PLY  = VALUE_TB - MAX_PLY;
constexpr Value VALUE_TB_LOSS_IN_MAX_PLY = -VALUE_TB_WIN_IN_MAX_PLY;


constexpr bool is_valid(Value value) { return value != VALUE_NONE; }

constexpr bool is_win(Value value) {
    assert(is_valid(value));
    return value >= VALUE_TB_WIN_IN_MAX_PLY;
}

constexpr bool is_loss(Value value) {
    assert(is_valid(value));
    return value <= VALUE_TB_LOSS_IN_MAX_PLY;
}

constexpr bool is_decisive(Value value) { return is_win(value) || is_loss(value); }

constexpr bool is_mate(Value value) {
    assert(is_valid(value));
    return value >= VALUE_MATE_IN_MAX_PLY;
}

constexpr bool is_mated(Value value) {
    assert(is_valid(value));
    return value <= VALUE_MATED_IN_MAX_PLY;
}

constexpr bool is_mate_or_mated(Value value) { return is_mate(value) || is_mated(value); }

constexpr Value mate_in(int ply) { return VALUE_MATE - ply; }

constexpr Value mated_in(int ply) { return -VALUE_MATE + ply; }

// The 26 Terachess piece types, engine-optimized order (frozen contract).
// The alphabetical tera-bin encoding lives ONLY in the datagen (F2), not here.
// clang-format off
enum PieceType : int {
    NO_PIECE_TYPE = 0,
    PAWN = 1, KNIGHT, BISHOP, ROOK, QUEEN, CAMEL, GIRAFFE,
    ELEPHANT, MACHINE, PRINCE, TROLL, ARCHER, CANNON, CENTAUR, MISSIONARY,
    ADMIRAL, CARDINAL, MARSHALL, BUFFALO, DUCHESS, LION, RHINO, SORCERESS,
    EAGLE, AMAZON, KING = 26,

    ALL_PIECES    = 0,
    PIECE_TYPE_NB = 27
};

// piece = (color << 5) | type
enum Piece : int {
    NO_PIECE = 0,

    W_PAWN = PAWN,   W_KNIGHT, W_BISHOP, W_ROOK, W_QUEEN, W_CAMEL, W_GIRAFFE,
    W_ELEPHANT, W_MACHINE, W_PRINCE, W_TROLL, W_ARCHER, W_CANNON, W_CENTAUR,
    W_MISSIONARY, W_ADMIRAL, W_CARDINAL, W_MARSHALL, W_BUFFALO, W_DUCHESS,
    W_LION, W_RHINO, W_SORCERESS, W_EAGLE, W_AMAZON, W_KING = 26,

    B_PAWN = PAWN + 32, B_KNIGHT, B_BISHOP, B_ROOK, B_QUEEN, B_CAMEL, B_GIRAFFE,
    B_ELEPHANT, B_MACHINE, B_PRINCE, B_TROLL, B_ARCHER, B_CANNON, B_CENTAUR,
    B_MISSIONARY, B_ADMIRAL, B_CARDINAL, B_MARSHALL, B_BUFFALO, B_DUCHESS,
    B_LION, B_RHINO, B_SORCERESS, B_EAGLE, B_AMAZON, B_KING = KING + 32,

    PIECE_NB = 64
};
// clang-format on

// FEN-TSF letters (spec 3.1), indexed by PieceType; uppercase = white.
// clang-format off
constexpr char PieceTypeToChar[PIECE_TYPE_NB] = {
    ' ',
    'p',  // PAWN
    'n',  // KNIGHT
    'b',  // BISHOP
    'r',  // ROOK
    'q',  // QUEEN
    'm',  // CAMEL
    'z',  // GIRAFFE
    'e',  // ELEPHANT
    'w',  // MACHINE
    'i',  // PRINCE
    't',  // TROLL
    'v',  // ARCHER
    'c',  // CANNON
    'j',  // CENTAUR
    'y',  // MISSIONARY
    's',  // ADMIRAL
    'x',  // CARDINAL
    'h',  // MARSHALL
    'f',  // BUFFALO
    'd',  // DUCHESS
    'l',  // LION
    'u',  // RHINO
    'o',  // SORCERESS
    'g',  // EAGLE
    'a',  // AMAZON
    'k'   // KING
};
// clang-format on

constexpr PieceType piece_type_from_char(char lower) {
    for (int pt = PAWN; pt < PIECE_TYPE_NB; ++pt)
        if (PieceTypeToChar[pt] == lower)
            return PieceType(pt);
    return NO_PIECE_TYPE;
}

// Piece values: Zillions x100 (spec 8, frozen contract). KING = 0.
// clang-format off
constexpr Value PieceTypeValue[PIECE_TYPE_NB] = {
    VALUE_ZERO,
    50,    // PAWN
    200,   // KNIGHT
    340,   // BISHOP
    500,   // ROOK
    830,   // QUEEN
    180,   // CAMEL
    170,   // GIRAFFE
    200,   // ELEPHANT
    220,   // MACHINE
    230,   // PRINCE
    240,   // TROLL
    330,   // ARCHER
    500,   // CANNON
    410,   // CENTAUR
    440,   // MISSIONARY
    600,   // ADMIRAL
    530,   // CARDINAL
    690,   // MARSHALL
    540,   // BUFFALO
    580,   // DUCHESS
    600,   // LION
    610,   // RHINO
    820,   // SORCERESS
    840,   // EAGLE
    1020,  // AMAZON
    VALUE_ZERO  // KING
};
// clang-format on

constexpr Value PawnValue   = PieceTypeValue[PAWN];
constexpr Value KnightValue = PieceTypeValue[KNIGHT];
constexpr Value BishopValue = PieceTypeValue[BISHOP];
constexpr Value RookValue   = PieceTypeValue[ROOK];
constexpr Value QueenValue  = PieceTypeValue[QUEEN];

constexpr auto PieceValue = []() constexpr {
    std::array<Value, PIECE_NB> v{};
    for (int pt = NO_PIECE_TYPE; pt < PIECE_TYPE_NB; ++pt)
    {
        v[pt]      = PieceTypeValue[pt];
        v[pt + 32] = PieceTypeValue[pt];
    }
    return v;
}();

using Depth = int;

// The following DEPTH_ constants are used for transposition table entries
// and quiescence search move generation stages. In regular search, the
// depth stored in the transposition table is literal: the search depth
// (effort) used to make the corresponding transposition table value. In
// quiescence search, however, the transposition table entries only store
// the current quiescence move generation stage (which should thus compare
// lower than any regular search depth).
constexpr Depth DEPTH_QS = 0;
// For transposition table entries where no searching at all was done
// (whether regular or qsearch) we use DEPTH_UNSEARCHED, which should thus
// compare lower than any quiescence or regular depth. DEPTH_NONE is used
// for the transposition table entry occupancy check (see tt.cpp), and
// should thus be lower than DEPTH_UNSEARCHED.
constexpr Depth DEPTH_UNSEARCHED = -2;
constexpr Depth DEPTH_NONE       = -3;

// sq = (rank << 4) | file, a1 = 0, p1 = 15, a16 = 240, p16 = 255.
// Stored as int (16 bits needed); NEVER pack a Square into a u8.
enum Square : int {
    SQUARE_ZERO = 0,
    SQ_NONE     = 256,
    SQUARE_NB   = 256
};

enum Direction : int {
    NORTH = 16,
    EAST  = 1,
    SOUTH = -NORTH,
    WEST  = -EAST,

    NORTH_EAST = NORTH + EAST,
    SOUTH_EAST = SOUTH + EAST,
    SOUTH_WEST = SOUTH + WEST,
    NORTH_WEST = NORTH + WEST
};

// clang-format off
enum File : int {
    FILE_A, FILE_B, FILE_C, FILE_D, FILE_E, FILE_F, FILE_G, FILE_H,
    FILE_I, FILE_J, FILE_K, FILE_L, FILE_M, FILE_N, FILE_O, FILE_P,
    FILE_NB = 16
};

enum Rank : int {
    RANK_1, RANK_2,  RANK_3,  RANK_4,  RANK_5,  RANK_6,  RANK_7,  RANK_8,
    RANK_9, RANK_10, RANK_11, RANK_12, RANK_13, RANK_14, RANK_15, RANK_16,
    RANK_NB = 16
};
// clang-format on

// Keep track of what a move changes on the board (used by NNUE)
// T256-TODO: re-audit for the F3 NNUE reintroduction (threat features were
// removed: their packed 8-bit square layout cannot hold 256 squares + SQ_NONE).
struct DirtyPiece {
    Piece  pc;        // this is never allowed to be NO_PIECE
    Square from, to;  // to should be SQ_NONE for promotions

    // if {add,remove}_sq is SQ_NONE, {add,remove}_pc is allowed to be
    // uninitialized
    Square remove_sq, add_sq;
    Piece  remove_pc, add_pc;
};

    #define ENABLE_INCR_OPERATORS_ON(T) \
        constexpr T& operator++(T& d) { return d = T(int(d) + 1); } \
        constexpr T& operator--(T& d) { return d = T(int(d) - 1); }

ENABLE_INCR_OPERATORS_ON(PieceType)
ENABLE_INCR_OPERATORS_ON(Square)
ENABLE_INCR_OPERATORS_ON(File)
ENABLE_INCR_OPERATORS_ON(Rank)

    #undef ENABLE_INCR_OPERATORS_ON

constexpr Direction operator+(Direction d1, Direction d2) { return Direction(int(d1) + int(d2)); }
constexpr Direction operator*(int i, Direction d) { return Direction(i * int(d)); }

// Additional operators to add a Direction to a Square
constexpr Square  operator+(Square s, Direction d) { return Square(int(s) + int(d)); }
constexpr Square  operator-(Square s, Direction d) { return Square(int(s) - int(d)); }
constexpr Square& operator+=(Square& s, Direction d) { return s = s + d; }
constexpr Square& operator-=(Square& s, Direction d) { return s = s - d; }

// Toggle color
constexpr Color operator~(Color c) { return Color(c ^ BLACK); }

// Swap rank 1 <-> rank 16
constexpr Square flip_rank(Square s) { return Square(s ^ 0xF0); }

// Swap file a <-> file p
constexpr Square flip_file(Square s) { return Square(s ^ 0x0F); }

// Swap color of piece B_KNIGHT <-> W_KNIGHT
constexpr Piece operator~(Piece pc) { return Piece(pc ^ 32); }

constexpr Square make_square(File f, Rank r) { return Square((r << 4) | f); }

constexpr Piece make_piece(Color c, PieceType pt) { return Piece((c << 5) | pt); }

constexpr PieceType type_of(Piece pc) { return PieceType(pc & 31); }

constexpr Color color_of(Piece pc) {
    assert(pc != NO_PIECE);
    return Color(pc >> 5);
}

constexpr bool is_ok(Square s) { return s >= SQUARE_ZERO && s < SQUARE_NB; }

constexpr File file_of(Square s) { return File(s & 15); }

constexpr Rank rank_of(Square s) { return Rank(s >> 4); }

constexpr Square relative_square(Color c, Square s) { return c == WHITE ? s : flip_rank(s); }

constexpr Rank relative_rank(Color c, Rank r) { return Rank(r ^ (c * 15)); }

constexpr Rank relative_rank(Color c, Square s) { return relative_rank(c, rank_of(s)); }

constexpr Direction pawn_push(Color c) { return c == WHITE ? NORTH : SOUTH; }

constexpr char piece_to_char(Piece pc) {
    const char lower = PieceTypeToChar[type_of(pc)];
    return color_of(pc) == WHITE ? char(lower - 'a' + 'A') : lower;
}

constexpr Piece piece_from_char(char c) {
    const bool      white = c >= 'A' && c <= 'Z';
    const PieceType pt    = piece_type_from_char(white ? char(c - 'A' + 'a') : c);
    return pt == NO_PIECE_TYPE ? NO_PIECE : make_piece(white ? WHITE : BLACK, pt);
}

// Forced promotion table (spec 6.4). Returns NO_PIECE_TYPE for pieces that
// never promote. The TROLL entry applies only when the Troll reaches the last
// rank with a pawn step (1 forward or diagonal-forward capture); that arrival
// condition depends on the move and is the caller's responsibility.
constexpr PieceType promoted_piece_type(PieceType pt) {
    return pt == PAWN                                        ? QUEEN
         : pt == PRINCE                                      ? AMAZON
         : pt == KNIGHT || pt == CAMEL || pt == GIRAFFE      ? BUFFALO
         : pt == ELEPHANT || pt == MACHINE || pt == CENTAUR  ? LION
         : pt == TROLL                                       ? QUEEN
                                                             : NO_PIECE_TYPE;
}


// Based on a congruential pseudo-random number generator
constexpr Key make_key(u64 seed) { return seed * 6364136223846793005ULL + 1442695040888963407ULL; }


// Special-move flag stored in Move bits 21-22 (frozen contract).
// Promotion is NOT a special type: it travels in the promo field (bits 16-20).
enum MoveType : u32 {
    NORMAL     = 0,
    EN_PASSANT = 1 << 21,
    KING_JUMP  = 2 << 21
};

// A move is stored in 32 bits (frozen contract):
//
// bit  0- 7: destination square (0-255)
// bit  8-15: origin square (0-255)
// bit 16-20: resulting promotion PieceType (0 = no promotion). Promotion is
//            forced and deterministic (spec 6.4) but the move carries it
//            explicitly for cheap cross-validation.
// bit 21-22: special move flag: 0 = normal, 1 = en passant, 2 = king jump
// bit 23-31: always 0
//
// Move::none() = 0. Move::null() sets the unused special-type value 3 in
// bits 21-22 (plus from=b1/to=c1 so from != to): no generated move can carry
// type 3, so the null move can never collide with a real move. DEVIATION from
// the frozen contract's (1 << 8) | 2, which collided with the normal b1-c1
// move and would have corrupted TT/search move comparisons (stage-3 handoff
// trap 1; architect sign-off pending).

class Move {
   public:
    Move() = default;
    constexpr explicit Move(u32 d) :
        data(d) {}

    constexpr Move(Square from, Square to) :
        data((u32(from) << 8) | u32(to)) {}

    template<MoveType T>
    static constexpr Move make(Square from, Square to, PieceType promo = NO_PIECE_TYPE) {
        return Move(T | (u32(promo) << 16) | (u32(from) << 8) | u32(to));
    }

    constexpr Square from_sq() const {
        assert(is_ok());
        return Square((data >> 8) & 0xFF);
    }

    constexpr Square to_sq() const {
        assert(is_ok());
        return Square(data & 0xFF);
    }

    constexpr MoveType type_of() const { return MoveType(data & (3u << 21)); }

    constexpr PieceType promotion_type() const { return PieceType((data >> 16) & 0x1F); }

    constexpr bool is_promotion() const { return promotion_type() != NO_PIECE_TYPE; }

    constexpr bool is_ok() const { return none().data != data && null().data != data; }

    static constexpr Move null() { return Move((3u << 21) | (1 << 8) | 2); }
    static constexpr Move none() { return Move(0); }

    constexpr bool operator==(const Move& m) const { return data == m.data; }
    constexpr bool operator!=(const Move& m) const { return data != m.data; }

    constexpr explicit operator bool() const { return data != 0; }

    constexpr u32 raw() const { return data; }

    struct MoveHash {
        usize operator()(const Move& m) const { return make_key(m.data); }
    };

    static constexpr int FromSqShift = 8;
    static constexpr int ToSqShift   = 0;

   protected:
    u32 data;
};

template<typename T, typename... Ts>
struct is_all_same {
    static constexpr bool value = (std::is_same_v<T, Ts> && ...);
};

template<typename... Ts>
constexpr auto is_all_same_v = is_all_same<Ts...>::value;

}  // namespace Stockfish

#endif  // #ifndef TYPES_H_INCLUDED

#include "tune.h"  // Global visibility to tuning setup
