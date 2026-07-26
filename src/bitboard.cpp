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

#include "bitboard.h"

#include <bitset>
#include <initializer_list>
#include <utility>

namespace Stockfish {

u8 PopCnt16[1 << 16];

Bitboard Ray[SQUARE_NB][RAY_NB];
Bitboard LineBB[SQUARE_NB][SQUARE_NB];
Bitboard BetweenBB[SQUARE_NB][SQUARE_NB];
Bitboard PseudoAttacks[PIECE_TYPE_NB][SQUARE_NB];
Bitboard PawnAttacks[COLOR_NB][SQUARE_NB];
Bitboard DiagStepBB[SQUARE_NB];
Bitboard OrthStepBB[SQUARE_NB];

namespace {

// (file delta, rank delta) of each ray direction, same order as RayDir
constexpr int RayDelta[RAY_NB][2] = {{0, 1},  {1, 0},  {0, -1},  {-1, 0},
                                     {1, 1},  {1, -1}, {-1, -1}, {-1, 1}};

Square step_to(Square s, int df, int dr) {
    const int f = file_of(s) + df, r = rank_of(s) + dr;
    return f >= 0 && f < FILE_NB && r >= 0 && r < RANK_NB ? make_square(File(f), Rank(r))
                                                          : SQ_NONE;
}

Bitboard steps_bb(Square s, std::initializer_list<std::pair<int, int>> steps) {
    Bitboard b;
    for (const auto& [df, dr] : steps)
        if (Square to = step_to(s, df, dr); to != SQ_NONE)
            b |= square_bb(to);
    return b;
}

}  // namespace


// Bent rider (spec 4.1): one diagonal step, then slides ORTHOGONALLY away
// from the origin along the two components of the diagonal. May stop on the
// diagonal square. Never jumps.
Bitboard eagle_attacks(Square s, Bitboard occupied) {

    // Orthogonal components of NE, SE, SW, NW
    constexpr int Comp[4][2] = {{RAY_N, RAY_E}, {RAY_S, RAY_E}, {RAY_S, RAY_W}, {RAY_N, RAY_W}};

    Bitboard attacks;
    for (int i = 0; i < 4; ++i)
    {
        const int      d   = RAY_NE + i;
        const Bitboard ray = Ray[s][d];
        if (!bool(ray))
            continue;
        const Square s1 = RayPositive[d] ? lsb(ray) : msb(ray);  // nearest square on the ray
        attacks |= square_bb(s1);
        if (!bool(occupied & square_bb(s1)))
            attacks |= ray_attacks(s1, Comp[i][0], occupied) | ray_attacks(s1, Comp[i][1], occupied);
    }
    return attacks;
}

// Symmetric counterpart: one orthogonal step, then slides along the two
// diagonals that contain that orthogonal as a component.
Bitboard rhino_attacks(Square s, Bitboard occupied) {

    // Diagonal components of N, E, S, W
    constexpr int Comp[4][2] = {{RAY_NE, RAY_NW}, {RAY_NE, RAY_SE}, {RAY_SE, RAY_SW}, {RAY_SW, RAY_NW}};

    Bitboard attacks;
    for (int d = RAY_N; d <= RAY_W; ++d)
    {
        const Bitboard ray = Ray[s][d];
        if (!bool(ray))
            continue;
        const Square s1 = RayPositive[d] ? lsb(ray) : msb(ray);
        attacks |= square_bb(s1);
        if (!bool(occupied & square_bb(s1)))
            attacks |= ray_attacks(s1, Comp[d][0], occupied) | ray_attacks(s1, Comp[d][1], occupied);
    }
    return attacks;
}

// Screen captures (spec 4.2): per direction, first blocker B1 (any color) is
// the screen; the capture candidate is the second blocker B2 if it exists.
Bitboard hopper_captures(Square s, Bitboard occupied, int dirs) {

    Bitboard attacks;
    for (int d = 0; d < RAY_NB; ++d)
    {
        if (!((dirs >> d) & 1))
            continue;
        Bitboard b = Ray[s][d] & occupied;
        if (!bool(b))
            continue;
        const Square b1 = RayPositive[d] ? lsb(b) : msb(b);
        b               = Ray[b1][d] & occupied;
        if (bool(b))
            attacks |= square_bb(RayPositive[d] ? lsb(b) : msb(b));
    }
    return attacks;
}


// Returns an ASCII representation of a bitboard suitable
// to be printed to standard output. Useful for debugging.
std::string Bitboards::pretty(Bitboard b) {

    const std::string sep = "+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+\n";
    std::string       s   = sep;

    for (Rank r = RANK_16;; --r)
    {
        for (File f = FILE_A; f <= FILE_P; ++f)
            s += bool(b & make_square(f, r)) ? "| X " : "|   ";

        s += "| " + std::to_string(1 + r) + "\n" + sep;

        if (r == RANK_1)
            break;
    }
    s += "  a   b   c   d   e   f   g   h   i   j   k   l   m   n   o   p\n";

    return s;
}


// Initializes all attack and helper tables at startup.
void Bitboards::init() {

    for (unsigned i = 0; i < (1 << 16); ++i)
        PopCnt16[i] = u8(std::bitset<16>(i).count());

    // Rays
    for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
        for (int d = 0; d < RAY_NB; ++d)
        {
            Bitboard ray;
            Square   t = s;
            while ((t = step_to(t, RayDelta[d][0], RayDelta[d][1])) != SQ_NONE)
                ray |= square_bb(t);
            Ray[s][d] = ray;
        }

    // Leapers and steps (contract atoms: F, W, A=(2,2), D=(2,0), G=(3,3), H=(0,3))
    for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
    {
        const Bitboard knight =
          steps_bb(s, {{1, 2}, {2, 1}, {2, -1}, {1, -2}, {-1, -2}, {-2, -1}, {-2, 1}, {-1, 2}});
        const Bitboard camel =
          steps_bb(s, {{1, 3}, {3, 1}, {3, -1}, {1, -3}, {-1, -3}, {-3, -1}, {-3, 1}, {-1, 3}});
        const Bitboard giraffe =
          steps_bb(s, {{2, 3}, {3, 2}, {3, -2}, {2, -3}, {-2, -3}, {-3, -2}, {-3, 2}, {-2, 3}});
        const Bitboard fstep = steps_bb(s, {{1, 1}, {1, -1}, {-1, -1}, {-1, 1}});
        const Bitboard wstep = steps_bb(s, {{0, 1}, {1, 0}, {0, -1}, {-1, 0}});
        const Bitboard a22   = steps_bb(s, {{2, 2}, {2, -2}, {-2, -2}, {-2, 2}});
        const Bitboard d20   = steps_bb(s, {{0, 2}, {2, 0}, {0, -2}, {-2, 0}});
        const Bitboard g33   = steps_bb(s, {{3, 3}, {3, -3}, {-3, -3}, {-3, 3}});
        const Bitboard h03   = steps_bb(s, {{0, 3}, {3, 0}, {0, -3}, {-3, 0}});
        const Bitboard king  = fstep | wstep;

        Bitboard lion;
        for (int df = -2; df <= 2; ++df)
            for (int dr = -2; dr <= 2; ++dr)
                if (df || dr)
                    lion |= steps_bb(s, {{df, dr}});

        DiagStepBB[s] = fstep;
        OrthStepBB[s] = wstep;

        PseudoAttacks[KNIGHT][s]   = knight;
        PseudoAttacks[CAMEL][s]    = camel;
        PseudoAttacks[GIRAFFE][s]  = giraffe;
        PseudoAttacks[BUFFALO][s]  = knight | camel | giraffe;
        PseudoAttacks[KING][s]     = king;
        PseudoAttacks[PRINCE][s]   = king;
        PseudoAttacks[CENTAUR][s]  = king | knight;
        PseudoAttacks[LION][s]     = lion;
        PseudoAttacks[DUCHESS][s]  = king | a22 | d20 | g33 | h03;
        PseudoAttacks[MACHINE][s]  = wstep | d20;
        PseudoAttacks[ELEPHANT][s] = fstep | a22;
        PseudoAttacks[TROLL][s]    = g33 | h03;  // jumps only; pawn steps live in movegen

        PawnAttacks[WHITE][s] = steps_bb(s, {{-1, 1}, {1, 1}});
        PawnAttacks[BLACK][s] = steps_bb(s, {{-1, -1}, {1, -1}});
    }

    // Empty-board slider masks and compounds (need Ray)
    for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
    {
        const Bitboard rook   = Ray[s][RAY_N] | Ray[s][RAY_E] | Ray[s][RAY_S] | Ray[s][RAY_W];
        const Bitboard bishop = Ray[s][RAY_NE] | Ray[s][RAY_SE] | Ray[s][RAY_SW] | Ray[s][RAY_NW];

        PseudoAttacks[ROOK][s]       = rook;
        PseudoAttacks[BISHOP][s]     = bishop;
        PseudoAttacks[QUEEN][s]      = rook | bishop;
        PseudoAttacks[CANNON][s]     = rook;
        PseudoAttacks[ARCHER][s]     = bishop;
        PseudoAttacks[SORCERESS][s]  = rook | bishop;
        PseudoAttacks[ADMIRAL][s]    = rook | DiagStepBB[s];
        PseudoAttacks[MISSIONARY][s] = bishop | OrthStepBB[s];
        PseudoAttacks[MARSHALL][s]   = rook | PseudoAttacks[KNIGHT][s];
        PseudoAttacks[CARDINAL][s]   = bishop | PseudoAttacks[KNIGHT][s];
        PseudoAttacks[AMAZON][s]     = rook | bishop | PseudoAttacks[KNIGHT][s];
        PseudoAttacks[EAGLE][s]      = eagle_attacks(s, Bitboard());
        PseudoAttacks[RHINO][s]      = rhino_attacks(s, Bitboard());
    }

    // Lines and between (Stockfish master semantics: BetweenBB includes s2)
    for (Square s1 = SQUARE_ZERO; s1 < SQUARE_NB; ++s1)
    {
        for (Square s2 = SQUARE_ZERO; s2 < SQUARE_NB; ++s2)
        {
            LineBB[s1][s2]    = Bitboard();
            BetweenBB[s1][s2] = square_bb(s2);
        }

        for (int d = 0; d < RAY_NB; ++d)
        {
            Bitboard ray = Ray[s1][d];
            while (bool(ray))
            {
                const Square s2   = pop_lsb(ray);
                LineBB[s1][s2]    = Ray[s1][d] | Ray[s1][opposite_ray(d)] | square_bb(s1);
                BetweenBB[s1][s2] = Ray[s1][d] ^ Ray[s2][d];
            }
        }
    }
}

}  // namespace Stockfish


#ifdef BB256_SELFTEST

// Bitboard256 operator and attack-function selftest (stage-1 gate).
// Build:  g++ -std=c++17 -DBB256_SELFTEST -o bb256_selftest bitboard.cpp
// It differentially checks the 256-bit operators against std::bitset<256> /
// byte-wise reference arithmetic, checks a set of precalculated values, and
// checks every attack generator against an independent naive mailbox walker.

    #include <cstdio>
    #include <cstdlib>

namespace {

using namespace Stockfish;

int failures = 0;

void check(bool cond, const char* what) {
    if (!cond)
    {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

std::bitset<256> to_bitset(Bitboard b) {
    std::bitset<256> r;
    for (int i = 0; i < 256; ++i)
        if ((b.w[i >> 6] >> (i & 63)) & 1)
            r.set(i);
    return r;
}

// Reference 256-bit subtraction, bit by bit (school method)
Bitboard ref_sub(Bitboard a, Bitboard b) {
    Bitboard r;
    int      borrow = 0;
    for (int i = 0; i < 256; ++i)
    {
        const int ai = int((a.w[i >> 6] >> (i & 63)) & 1);
        const int bi = int((b.w[i >> 6] >> (i & 63)) & 1);
        const int d  = ai - bi - borrow;
        if (d & 1)
            r.w[i >> 6] |= 1ULL << (i & 63);
        borrow = d < 0;
    }
    return r;
}

Bitboard random_bb(PRNG& rng) {
    // Mix of sparse and dense random boards
    return rng.rand<u64>() % 2 ? Bitboard(rng.rand<u64>(), rng.rand<u64>(), rng.rand<u64>(),
                                          rng.rand<u64>())
                               : Bitboard(rng.sparse_rand<u64>(), rng.sparse_rand<u64>(),
                                          rng.sparse_rand<u64>(), rng.sparse_rand<u64>());
}

// ---- independent naive attack generators (mailbox walking) ----

Square naive_step(Square s, int df, int dr) {
    const int f = (int(s) & 15) + df, r = (int(s) >> 4) + dr;
    return f >= 0 && f < 16 && r >= 0 && r < 16 ? Square((r << 4) | f) : SQ_NONE;
}

bool occ_test(Bitboard occ, Square s) { return (occ.w[int(s) >> 6] >> (int(s) & 63)) & 1; }

Bitboard naive_slide(Square s, Bitboard occ, std::initializer_list<std::pair<int, int>> dirs) {
    Bitboard att;
    for (const auto& [df, dr] : dirs)
    {
        Square t = s;
        while ((t = naive_step(t, df, dr)) != SQ_NONE)
        {
            att |= square_bb(t);
            if (occ_test(occ, t))
                break;
        }
    }
    return att;
}

Bitboard naive_leaper(Square s, std::initializer_list<std::pair<int, int>> steps) {
    Bitboard att;
    for (const auto& [df, dr] : steps)
        if (Square t = naive_step(s, df, dr); t != SQ_NONE)
            att |= square_bb(t);
    return att;
}

Bitboard naive_eagle(Square s, Bitboard occ) {
    Bitboard att;
    for (int df : {-1, 1})
        for (int dr : {-1, 1})
        {
            const Square s1 = naive_step(s, df, dr);
            if (s1 == SQ_NONE)
                continue;
            att |= square_bb(s1);
            if (!occ_test(occ, s1))
                att |= naive_slide(s1, occ, {{df, 0}, {0, dr}});
        }
    return att;
}

Bitboard naive_rhino(Square s, Bitboard occ) {
    Bitboard att;
    const std::pair<int, int> ortho[4] = {{0, 1}, {1, 0}, {0, -1}, {-1, 0}};
    for (const auto& [df, dr] : ortho)
    {
        const Square s1 = naive_step(s, df, dr);
        if (s1 == SQ_NONE)
            continue;
        att |= square_bb(s1);
        if (!occ_test(occ, s1))
        {
            if (df == 0)
                att |= naive_slide(s1, occ, {{1, dr}, {-1, dr}});
            else
                att |= naive_slide(s1, occ, {{df, 1}, {df, -1}});
        }
    }
    return att;
}

Bitboard naive_hopper_quiet(Square s, Bitboard occ, std::initializer_list<std::pair<int, int>> dirs) {
    Bitboard att;
    for (const auto& [df, dr] : dirs)
    {
        Square t = s;
        while ((t = naive_step(t, df, dr)) != SQ_NONE && !occ_test(occ, t))
            att |= square_bb(t);
    }
    return att;
}

Bitboard naive_hopper_captures(Square s, Bitboard occ,
                               std::initializer_list<std::pair<int, int>> dirs) {
    Bitboard att;
    for (const auto& [df, dr] : dirs)
    {
        Square t       = s;
        int    screens = 0;
        while ((t = naive_step(t, df, dr)) != SQ_NONE)
            if (occ_test(occ, t))
            {
                if (++screens == 2)
                {
                    att |= square_bb(t);
                    break;
                }
            }
    }
    return att;
}

const std::initializer_list<std::pair<int, int>> OrthoDirs = {{0, 1}, {1, 0}, {0, -1}, {-1, 0}};
const std::initializer_list<std::pair<int, int>> DiagDirs  = {{1, 1}, {1, -1}, {-1, -1}, {-1, 1}};
const std::initializer_list<std::pair<int, int>> AllDirs   = {{0, 1},  {1, 0},  {0, -1}, {-1, 0},
                                                              {1, 1},  {1, -1}, {-1, -1}, {-1, 1}};

}  // namespace

int main() {

    using namespace Stockfish;

    Bitboards::init();

    // ---- 1. precalculated values (cross-checked against Python big ints) ----

    // carry across the w[0]/w[1] boundary: 1 << 64
    check((Bitboard(1) << 64) == Bitboard(0, 1, 0, 0), "1 << 64");
    check((Bitboard(0x8000000000000000ULL) << 1) == Bitboard(0, 1, 0, 0), "msb(w0) << 1 carry");
    // 1 << 255 = top bit of w[3]
    check((Bitboard(1) << 255) == Bitboard(0, 0, 0, 0x8000000000000000ULL), "1 << 255");
    // (1 << 130) >> 68 = 1 << 62
    check((Bitboard(1) << 130 >> 68) == Bitboard(0x4000000000000000ULL), "(1<<130)>>68");
    // non-word-aligned cross shift: (3 << 63) spans w0/w1
    check((Bitboard(3) << 63) == Bitboard(0x8000000000000000ULL, 1, 0, 0), "3 << 63 carry");
    check((Bitboard(0, 3, 0, 0) >> 1) == Bitboard(0x8000000000000000ULL, 1, 0, 0), "w1 >> 1 carry");
    // shifts >= 256 vanish
    check(!bool(Bitboard(~0ULL, ~0ULL, ~0ULL, ~0ULL) << 256), "shift 256 == empty");

    // subtraction borrow across words: (1 << 128) - 1 = low two words all ones
    check(Bitboard(0, 0, 1, 0) - 1 == Bitboard(~0ULL, ~0ULL, 0, 0), "(1<<128) - 1 borrow");
    check(Bitboard(0, 0, 0, 1) - Bitboard(0, 0, 1, 0) == Bitboard(0, 0, ~0ULL, 0),
          "(1<<192) - (1<<128)");

    // popcount / lsb / msb on multiword values
    check(popcount(AllSquaresBB) == 256, "popcount all");
    check(popcount(file_bb(FILE_A)) == 16 && popcount(rank_bb(RANK_9)) == 16, "file/rank popcount");
    check(lsb(square_bb(Square(130)) | square_bb(Square(7))) == Square(7), "lsb multiword");
    check(msb(square_bb(Square(130)) | square_bb(Square(7))) == Square(130), "msb multiword");
    check(least_significant_square_bb(Bitboard(0, 0, 0x10, 0)) == Bitboard(0, 0, 0x10, 0),
          "b & -b single bit");

    // square_bb layout: sq = (rank << 4) | file, w[0] = squares 0..63
    check(square_bb(make_square(FILE_A, RANK_1)) == Bitboard(1), "a1 = bit 0");
    check(square_bb(make_square(FILE_P, RANK_16)) == Bitboard(0, 0, 0, 0x8000000000000000ULL),
          "p16 = bit 255");
    check(square_bb(make_square(FILE_H, RANK_2)) == Bitboard(1ULL << 23), "h2 = bit 23");

    // shift wrap masking at board edges
    check(!bool(shift<EAST>(square_bb(make_square(FILE_P, RANK_8)))), "EAST wrap masked");
    check(shift<NORTH_WEST>(square_bb(make_square(FILE_B, RANK_1)))
            == square_bb(make_square(FILE_A, RANK_2)),
          "NW shift");
    check(!bool(shift<NORTH>(rank_bb(RANK_16))), "NORTH off the top");

    // ---- 2. randomized differential vs std::bitset<256> ----

    PRNG rng(0x256256256ULL);
    for (int it = 0; it < 10000; ++it)
    {
        const Bitboard a = random_bb(rng), b = random_bb(rng);
        const auto     ra = to_bitset(a), rb = to_bitset(b);
        const int      sh = int(rng.rand<u64>() % 300);  // includes >= 256

        check(to_bitset(a & b) == (ra & rb), "random &");
        check(to_bitset(a | b) == (ra | rb), "random |");
        check(to_bitset(a ^ b) == (ra ^ rb), "random ^");
        check(to_bitset(~a) == ~ra, "random ~");
        check(to_bitset(a << sh) == (ra << sh), "random <<");
        check(to_bitset(a >> sh) == (ra >> sh), "random >>");
        check(popcount(a) == int(ra.count()), "random popcount");
        check((a - b) == ref_sub(a, b), "random subtraction");
        check((-a) == ref_sub(Bitboard(), a), "random unary minus");

        if (bool(a))
        {
            int lo = 0, hi = 255;
            while (!ra[lo])
                ++lo;
            while (!ra[hi])
                --hi;
            check(int(lsb(a)) == lo, "random lsb");
            check(int(msb(a)) == hi, "random msb");
            check(more_than_one(a) == (ra.count() > 1), "random more_than_one");

            // pop_lsb walks every set bit in ascending order
            Bitboard c = a;
            int      prev = -1, n = 0;
            while (bool(c))
            {
                const Square s = pop_lsb(c);
                check(int(s) > prev && ra[s], "pop_lsb order/membership");
                prev = int(s);
                ++n;
            }
            check(n == int(ra.count()), "pop_lsb count");
        }
    }

    // ---- 3. attack generators vs independent naive walker ----

    for (int it = 0; it < 3000; ++it)
    {
        const Bitboard occ = random_bb(rng);
        const Square   s   = Square(int(rng.rand<u64>() & 0xFF));

        check(attacks_bb<ROOK>(s, occ) == naive_slide(s, occ, OrthoDirs), "rook ray-scan");
        check(attacks_bb<BISHOP>(s, occ) == naive_slide(s, occ, DiagDirs), "bishop ray-scan");
        check(attacks_bb<QUEEN>(s, occ) == naive_slide(s, occ, AllDirs), "queen ray-scan");
        check(eagle_attacks(s, occ) == naive_eagle(s, occ), "eagle");
        check(rhino_attacks(s, occ) == naive_rhino(s, occ), "rhino");
        check(hopper_quiet(s, occ, ORTHO_RAYS) == naive_hopper_quiet(s, occ, OrthoDirs),
              "cannon quiet");
        check(hopper_captures(s, occ, ORTHO_RAYS) == naive_hopper_captures(s, occ, OrthoDirs),
              "cannon captures");
        check(hopper_quiet(s, occ, DIAG_RAYS) == naive_hopper_quiet(s, occ, DiagDirs),
              "archer quiet");
        check(hopper_captures(s, occ, DIAG_RAYS) == naive_hopper_captures(s, occ, DiagDirs),
              "archer captures");
        check(hopper_quiet(s, occ, ALL_RAYS) == naive_hopper_quiet(s, occ, AllDirs),
              "sorceress quiet");
        check(hopper_captures(s, occ, ALL_RAYS) == naive_hopper_captures(s, occ, AllDirs),
              "sorceress captures");
    }

    for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
    {
        const Bitboard knight = naive_leaper(
          s, {{1, 2}, {2, 1}, {2, -1}, {1, -2}, {-1, -2}, {-2, -1}, {-2, 1}, {-1, 2}});
        const Bitboard camel = naive_leaper(
          s, {{1, 3}, {3, 1}, {3, -1}, {1, -3}, {-1, -3}, {-3, -1}, {-3, 1}, {-1, 3}});
        const Bitboard giraffe = naive_leaper(
          s, {{2, 3}, {3, 2}, {3, -2}, {2, -3}, {-2, -3}, {-3, -2}, {-3, 2}, {-2, 3}});
        const Bitboard king =
          naive_leaper(s, {{0, 1}, {1, 1}, {1, 0}, {1, -1}, {0, -1}, {-1, -1}, {-1, 0}, {-1, 1}});
        const Bitboard a22 = naive_leaper(s, {{2, 2}, {2, -2}, {-2, -2}, {-2, 2}});
        const Bitboard d20 = naive_leaper(s, {{0, 2}, {2, 0}, {0, -2}, {-2, 0}});
        const Bitboard g33 = naive_leaper(s, {{3, 3}, {3, -3}, {-3, -3}, {-3, 3}});
        const Bitboard h03 = naive_leaper(s, {{0, 3}, {3, 0}, {0, -3}, {-3, 0}});
        const Bitboard fstep = naive_leaper(s, {{1, 1}, {1, -1}, {-1, -1}, {-1, 1}});
        const Bitboard wstep = naive_leaper(s, {{0, 1}, {1, 0}, {0, -1}, {-1, 0}});

        Bitboard lion;
        for (int df = -2; df <= 2; ++df)
            for (int dr = -2; dr <= 2; ++dr)
                if (df || dr)
                    lion |= naive_leaper(s, {{df, dr}});

        check(PseudoAttacks[KNIGHT][s] == knight, "knight table");
        check(PseudoAttacks[CAMEL][s] == camel, "camel table");
        check(PseudoAttacks[GIRAFFE][s] == giraffe, "giraffe table");
        check(PseudoAttacks[BUFFALO][s] == (knight | camel | giraffe), "buffalo table");
        check(PseudoAttacks[KING][s] == king, "king table");
        check(PseudoAttacks[PRINCE][s] == king, "prince table");
        check(PseudoAttacks[CENTAUR][s] == (king | knight), "centaur table");
        check(PseudoAttacks[LION][s] == lion, "lion table");
        check(PseudoAttacks[DUCHESS][s] == (king | a22 | d20 | g33 | h03), "duchess table");
        check(PseudoAttacks[MACHINE][s] == (wstep | d20), "machine table");
        check(PseudoAttacks[ELEPHANT][s] == (fstep | a22), "elephant table");
        check(PseudoAttacks[TROLL][s] == (g33 | h03), "troll jump table");
        check(DiagStepBB[s] == fstep && OrthStepBB[s] == wstep, "step atoms");
        check(PawnAttacks[WHITE][s] == naive_leaper(s, {{-1, 1}, {1, 1}}), "white pawn attacks");
        check(PawnAttacks[BLACK][s] == naive_leaper(s, {{-1, -1}, {1, -1}}), "black pawn attacks");
    }

    // spot check counts at a central square (independent hand counts)
    {
        const Square c = make_square(FILE_H, RANK_8);
        check(popcount(PseudoAttacks[LION][c]) == 24, "lion center 24");
        check(popcount(PseudoAttacks[DUCHESS][c]) == 24, "duchess center 24");
        check(popcount(PseudoAttacks[BUFFALO][c]) == 24, "buffalo center 24");
        check(popcount(PseudoAttacks[KNIGHT][c]) == 8, "knight center 8");
    }

    // line/between sanity: a1-d4 diagonal
    {
        const Square a1 = make_square(FILE_A, RANK_1), d4 = make_square(FILE_D, RANK_4);
        check(bool(line_bb(a1, d4) & make_square(FILE_P, RANK_16)), "line a1-d4 reaches p16");
        check(popcount(line_bb(a1, d4)) == 16, "line a1-d4 length");
        check(popcount(between_bb(a1, d4)) == 3, "between a1-d4 (incl. d4)");
        check(between_bb(a1, make_square(FILE_C, RANK_2))
                == square_bb(make_square(FILE_C, RANK_2)),
              "between non-aligned = to-square");
    }

    if (failures == 0)
        std::printf("Bitboard256 selftest: OK (all checks passed)\n");
    else
        std::printf("Bitboard256 selftest: %d FAILURES\n", failures);

    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}

#endif  // BB256_SELFTEST
