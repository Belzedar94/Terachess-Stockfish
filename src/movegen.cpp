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

#include "movegen.h"

#include <cassert>

#include "bitboard.h"
#include "position.h"

// Terachess-Stockfish move generation for the 26 piece types (rules authority:
// TERACHESS_SPEC.md; representation: docs/port-256-design.md, frozen).
//
// Stage semantics (consistent with Position::capture_stage): every promotion
// belongs to the CAPTURES stage, whether or not it captures, and QUIETS never
// contains promotions. Promotion is forced (spec 6.4), so any arrival of a
// promotable piece on its last rank carries the promo field.

namespace Stockfish {

namespace {

inline Move* splat_moves(Move* moveList, Square from, Bitboard b) {
    while (b)
        *moveList++ = Move(from, pop_lsb(b));
    return moveList;
}

inline Move* splat_promotions(Move* moveList, Square from, Bitboard b, PieceType promo) {
    while (b)
        *moveList++ = Move::make<NORMAL>(from, pop_lsb(b), promo);
    return moveList;
}

template<Direction Offset>
inline Move* splat_step_moves(Move* moveList, Bitboard b) {
    while (b)
    {
        Square to   = pop_lsb(b);
        *moveList++ = Move(to - Offset, to);
    }
    return moveList;
}

template<Direction Offset>
inline Move* splat_step_promotions(Move* moveList, Bitboard b, PieceType promo) {
    while (b)
    {
        Square to   = pop_lsb(b);
        *moveList++ = Move::make<NORMAL>(to - Offset, to, promo);
    }
    return moveList;
}

// Emits the moves of one piece whose arrival on the last rank forces a
// promotion (spec 6.4). att is the move-or-capture destination set.
template<GenType Type>
Move* emit_promotable(const Position& pos,
                      Move*           moveList,
                      Square          from,
                      Bitboard        att,
                      Bitboard        target,
                      Bitboard        lastRank,
                      PieceType       promo) {

    Bitboard b = att & target;

    if (Type == CAPTURES)
        b |= att & ~pos.pieces() & lastRank;  // quiet promotions join the capture stage
    else if (Type == QUIETS)
        b &= ~lastRank;  // promotions were already emitted by the capture stage

    moveList = splat_promotions(moveList, from, b & lastRank, promo);
    return splat_moves(moveList, from, b & ~lastRank);
}


template<Color Us, GenType Type>
Move* generate_pawn_moves(const Position& pos, Move* moveList) {

    constexpr Color     Them     = ~Us;
    constexpr Direction Up       = pawn_push(Us);
    constexpr Direction UpRight  = Us == WHITE ? NORTH_EAST : SOUTH_WEST;
    constexpr Direction UpLeft   = Us == WHITE ? NORTH_WEST : SOUTH_EAST;
    constexpr Bitboard  LastRank = Us == WHITE ? Rank16BB : Rank1BB;

    const Bitboard emptySquares = ~pos.pieces();
    const Bitboard enemies      = pos.pieces(Them);
    const Bitboard pawns        = pos.pieces(Us, PAWN);

    // Single and double pushes from any square (spec 6.1); a push landing on
    // the last rank promotes to Queen (spec 6.4).
    Bitboard b1 = shift<Up>(pawns) & emptySquares;
    Bitboard b2 = shift<Up>(b1) & emptySquares;

    if constexpr (Type != CAPTURES)
    {
        moveList = splat_step_moves<Up>(moveList, b1 & ~LastRank);
        moveList = splat_step_moves<Up + Up>(moveList, b2 & ~LastRank);
    }

    if constexpr (Type != QUIETS)
    {
        moveList = splat_step_promotions<Up>(moveList, b1 & LastRank, QUEEN);
        moveList = splat_step_promotions<Up + Up>(moveList, b2 & LastRank, QUEEN);

        // Diagonal-forward captures
        Bitboard c1 = shift<UpRight>(pawns) & enemies;
        Bitboard c2 = shift<UpLeft>(pawns) & enemies;

        moveList = splat_step_promotions<UpRight>(moveList, c1 & LastRank, QUEEN);
        moveList = splat_step_moves<UpRight>(moveList, c1 & ~LastRank);
        moveList = splat_step_promotions<UpLeft>(moveList, c2 & LastRank, QUEEN);
        moveList = splat_step_moves<UpLeft>(moveList, c2 & ~LastRank);

        // En passant (spec 6.2): only a Pawn captures; the removed piece may
        // be a Pawn or a Prince (do_move handles it). The ep square is never
        // on the capturer's last rank (the double step would have had to start
        // beyond the board), so no promotion here.
        if (pos.ep_square() != SQ_NONE)
        {
            Bitboard capturers = pawns & PawnAttacks[Them][pos.ep_square()];
            while (capturers)
                *moveList++ = Move::make<EN_PASSANT>(pop_lsb(capturers), pos.ep_square());
        }
    }

    return moveList;
}


template<Color Us, GenType Type>
Move* generate_prince_moves(const Position& pos, Move* moveList, Bitboard target) {

    constexpr Direction Up       = pawn_push(Us);
    constexpr Bitboard  LastRank = Us == WHITE ? Rank16BB : Rank1BB;

    const Bitboard princes = pos.pieces(Us, PRINCE);

    // Double forward push, no capture, both squares empty (spec 6.1);
    // generates an ep square in do_move. Promotes to Amazon on the last rank.
    const Bitboard emptySquares = ~pos.pieces();
    Bitboard       b2           = shift<Up>(shift<Up>(princes) & emptySquares) & emptySquares;

    if constexpr (Type != CAPTURES)
        moveList = splat_step_moves<Up + Up>(moveList, b2 & ~LastRank);
    if constexpr (Type != QUIETS)
        moveList = splat_step_promotions<Up + Up>(moveList, b2 & LastRank, AMAZON);

    // King-step move-or-capture (not royal, spec 4 #24)
    Bitboard bb = princes;
    while (bb)
    {
        Square from = pop_lsb(bb);
        moveList    = emit_promotable<Type>(pos, moveList, from, PseudoAttacks[KING][from], target,
                                            LastRank, AMAZON);
    }

    return moveList;
}


template<Color Us, GenType Type>
Move* generate_troll_moves(const Position& pos, Move* moveList, Bitboard target) {

    constexpr Direction Up       = pawn_push(Us);
    constexpr Direction UpRight  = Us == WHITE ? NORTH_EAST : SOUTH_WEST;
    constexpr Direction UpLeft   = Us == WHITE ? NORTH_WEST : SOUTH_EAST;
    constexpr Bitboard  LastRank = Us == WHITE ? Rank16BB : Rank1BB;

    const Bitboard trolls = pos.pieces(Us, TROLL);

    // Pawn-step part (fmW + fcF): no double step, no ep (spec 4 #26); a
    // pawn-step arrival on the last rank promotes to Queen (spec 6.4).
    Bitboard f = shift<Up>(trolls) & ~pos.pieces();

    if constexpr (Type != CAPTURES)
        moveList = splat_step_moves<Up>(moveList, f & ~LastRank);

    if constexpr (Type != QUIETS)
    {
        moveList = splat_step_promotions<Up>(moveList, f & LastRank, QUEEN);

        const Bitboard enemies = pos.pieces(~Us);
        Bitboard       c1      = shift<UpRight>(trolls) & enemies;
        Bitboard       c2      = shift<UpLeft>(trolls) & enemies;

        moveList = splat_step_promotions<UpRight>(moveList, c1 & LastRank, QUEEN);
        moveList = splat_step_moves<UpRight>(moveList, c1 & ~LastRank);
        moveList = splat_step_promotions<UpLeft>(moveList, c2 & LastRank, QUEEN);
        moveList = splat_step_moves<UpLeft>(moveList, c2 & ~LastRank);
    }

    // (3,3)/(0,3) jumps: move-or-capture, NEVER promote (spec 6.4)
    Bitboard bb = trolls;
    while (bb)
    {
        Square from = pop_lsb(bb);
        moveList    = splat_moves(moveList, from, PseudoAttacks[TROLL][from] & target);
    }

    return moveList;
}


// Screen pieces (spec 4.2): quiet slides and screen captures are distinct sets.
template<Color Us, GenType Type>
Move* generate_hopper_moves(const Position& pos, Move* moveList, PieceType pt, int dirs) {

    const Bitboard occ = pos.pieces();
    Bitboard       bb  = pos.pieces(Us, pt);

    while (bb)
    {
        Square from = pop_lsb(bb);
        if constexpr (Type != CAPTURES)
            moveList = splat_moves(moveList, from, hopper_quiet(from, occ, dirs));
        if constexpr (Type != QUIETS)
            moveList =
              splat_moves(moveList, from, hopper_captures(from, occ, dirs) & pos.pieces(~Us));
    }

    return moveList;
}


// Leapers, sliders, compounds and bent riders: one pattern for move and
// capture. The three leaper groups that promote (spec 6.4) carry the promo.
template<Color Us, PieceType Pt, GenType Type>
Move* generate_piece_moves(const Position& pos, Move* moveList, Bitboard target) {

    static_assert(Pt != KING && Pt != PAWN && Pt != PRINCE && Pt != TROLL && Pt != CANNON
                    && Pt != ARCHER && Pt != SORCERESS,
                  "generate_piece_moves: piece has a dedicated generator");

    constexpr PieceType Promo    = promoted_piece_type(Pt);
    constexpr Bitboard  LastRank = Us == WHITE ? Rank16BB : Rank1BB;

    Bitboard bb = pos.pieces(Us, Pt);

    while (bb)
    {
        Square   from = pop_lsb(bb);
        Bitboard att  = attacks_bb<Pt>(from, pos.pieces());

        if constexpr (Promo == NO_PIECE_TYPE)
            moveList = splat_moves(moveList, from, att & target);
        else
            moveList = emit_promotable<Type>(pos, moveList, from, att, target, LastRank, Promo);
    }

    return moveList;
}


template<Color Us, GenType Type>
Move* generate_king_moves(const Position& pos, Move* moveList, Bitboard target) {

    const Square ksq = pos.square<KING>(Us);

    moveList = splat_moves(moveList, ksq, PseudoAttacks[KING][ksq] & target);

    // Initial jump (spec 6.3): quiet only (destination must be empty).
    // Candidates = the Chebyshev-distance-2 ring = Lion pattern minus K-step.
    if (Type != CAPTURES && pos.can_king_jump(Us) && !pos.checkers())
    {
        Bitboard cand = PseudoAttacks[LION][ksq] & ~PseudoAttacks[KING][ksq] & ~pos.pieces();
        while (cand)
        {
            Square to = pop_lsb(cand);
            if (pos.king_jump_pseudo_legal(ksq, to))
                *moveList++ = Move::make<KING_JUMP>(ksq, to);
        }
    }

    return moveList;
}


template<Color Us, GenType Type>
Move* generate_all(const Position& pos, Move* moveList) {

    static_assert(Type == CAPTURES || Type == QUIETS || Type == NON_EVASIONS,
                  "Unsupported type in generate_all()");

    const Bitboard target = Type == CAPTURES ? pos.pieces(~Us)
                          : Type == QUIETS   ? ~pos.pieces()
                                             : ~pos.pieces(Us);  // NON_EVASIONS

    moveList = generate_pawn_moves<Us, Type>(pos, moveList);
    moveList = generate_prince_moves<Us, Type>(pos, moveList, target);
    moveList = generate_troll_moves<Us, Type>(pos, moveList, target);

    // Promoting leapers (spec 6.4)
    moveList = generate_piece_moves<Us, KNIGHT, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, CAMEL, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, GIRAFFE, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, ELEPHANT, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, MACHINE, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, CENTAUR, Type>(pos, moveList, target);

    // Non-promoting leapers/steppers
    moveList = generate_piece_moves<Us, BUFFALO, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, LION, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, DUCHESS, Type>(pos, moveList, target);

    // Sliders and compounds
    moveList = generate_piece_moves<Us, BISHOP, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, ROOK, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, QUEEN, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, ADMIRAL, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, MISSIONARY, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, CARDINAL, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, MARSHALL, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, AMAZON, Type>(pos, moveList, target);

    // Bent riders (spec 4.1)
    moveList = generate_piece_moves<Us, EAGLE, Type>(pos, moveList, target);
    moveList = generate_piece_moves<Us, RHINO, Type>(pos, moveList, target);

    // Screen pieces (spec 4.2)
    moveList = generate_hopper_moves<Us, Type>(pos, moveList, CANNON, ORTHO_RAYS);
    moveList = generate_hopper_moves<Us, Type>(pos, moveList, ARCHER, DIAG_RAYS);
    moveList = generate_hopper_moves<Us, Type>(pos, moveList, SORCERESS, ALL_RAYS);

    return generate_king_moves<Us, Type>(pos, moveList, target);
}

}  // namespace


// <CAPTURES>     Generates all pseudo-legal captures plus all promotions
// <QUIETS>       Generates all pseudo-legal non-captures except promotions
// <NON_EVASIONS> Generates all pseudo-legal captures and non-captures
//
// Returns a pointer to the end of the move list.
template<GenType Type>
Move* generate(const Position& pos, Move* moveList) {

    static_assert(Type == CAPTURES || Type == QUIETS || Type == NON_EVASIONS,
                  "Unsupported type in generate()");

    Color us = pos.side_to_move();

    return us == WHITE ? generate_all<WHITE, Type>(pos, moveList)
                       : generate_all<BLACK, Type>(pos, moveList);
}

// Explicit template instantiations
template Move* generate<CAPTURES>(const Position&, Move*);
template Move* generate<QUIETS>(const Position&, Move*);
template Move* generate<NON_EVASIONS>(const Position&, Move*);

// generate<EVASIONS> generates the check evasions. F1 correctness-first per
// the frozen contract: blocking a screen check or a bent-rider check does not
// follow master's between-squares logic, so the evasions are NON_EVASIONS
// filtered with legal() (the result is exactly the legal moves).
// T256-TODO: dedicated (faster, pseudo-legal superset) evasion generator in F2.
template<>
Move* generate<EVASIONS>(const Position& pos, Move* moveList) {

    assert(pos.checkers());

    Move* cur  = moveList;
    Move* last = generate<NON_EVASIONS>(pos, moveList);

    while (cur != last)
        if (!pos.legal(*cur))
            *cur = *(--last);
        else
            ++cur;

    return last;
}

// generate<LEGAL> generates all the legal moves in the given position.
// No pin machinery in F1 (frozen contract): every move is vetted with legal().
template<>
Move* generate<LEGAL>(const Position& pos, Move* moveList) {

    Move* cur  = moveList;
    Move* last = generate<NON_EVASIONS>(pos, moveList);

    while (cur != last)
        if (!pos.legal(*cur))
            *cur = *(--last);
        else
            ++cur;

    return last;
}

}  // namespace Stockfish
