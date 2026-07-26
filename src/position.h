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

#ifndef POSITION_H_INCLUDED
#define POSITION_H_INCLUDED

#include <array>
#include <cassert>
#include <deque>
#include <iosfwd>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

#include "bitboard.h"
#include "types.h"

// Terachess-Stockfish 256-square position layer. Representation contract:
// docs/port-256-design.md (frozen). Rules authority: TERACHESS_SPEC.md.

namespace Stockfish {

class TranspositionTable;

// StateInfo struct stores information needed to restore a Position object to
// its previous state when we retract a move. Whenever a move is made on the
// board (by calling Position::do_move), a StateInfo object must be passed.

struct StateInfo {

    // Copied when making a move
    Key    materialKey;
    Key    pawnKey;
    Key    minorPieceKey;
    Key    nonPawnKey[COLOR_NB];
    Value  nonPawnMaterial[COLOR_NB];
    int    kingJumpRights;  // KingJumpRights bits (spec 6.3); replaces castling
    int    rule50;
    int    pliesFromNull;
    Square epSquare;

    // Not copied when making a move (will be recomputed anyhow)
    Key        key;
    Bitboard   checkersBB;
    StateInfo* previous;
    Piece      capturedPiece;
    // Piece that made the last move, saved because the origin of a forced
    // promotion is not recoverable from the promoted type alone (e.g. BUFFALO
    // may come from KNIGHT, CAMEL or GIRAFFE; spec 6.4).
    Piece movedPiece;
    int   repetition;
};


// A list to keep track of the position states along the setup moves (from the
// start position to the position just before the search starts). Needed by
// 'draw by repetition' detection. Use a std::deque because pointers to
// elements are not invalidated upon list resizing.
using StateListPtr = std::unique_ptr<std::deque<StateInfo>>;

// This error should be used whenever a position is suspected to be unsupported
// by the engine. In particular positions that may cause hard errors like segmentation fault.
struct PositionSetError: std::runtime_error {
    using std::runtime_error::runtime_error;
};

// Position class stores information regarding the board representation as
// pieces, side to move, hash keys, king-jump rights, etc. Important methods are
// do_move() and undo_move(), used by the search to update node info when
// traversing the search tree.
class Position {
   public:
    static void init();

    Position()                           = default;
    Position(const Position&)            = delete;
    Position& operator=(const Position&) = delete;

    // FEN string input/output (FEN-TSF grammar, spec 3.2)
    std::optional<PositionSetError> set(const std::string& fenStr, StateInfo* si);
    std::string                     fen() const;

    // Position representation
    Bitboard pieces() const;  // All pieces
    template<typename... PieceTypes>
    Bitboard pieces(PieceTypes... pts) const;
    Bitboard pieces(Color c) const;
    template<typename... PieceTypes>
    Bitboard                            pieces(Color c, PieceTypes... pts) const;
    Piece                               piece_on(Square s) const;
    const std::array<Piece, SQUARE_NB>& piece_array() const;
    Square                              ep_square() const;
    bool                                empty(Square s) const;
    template<PieceType Pt>
    int count(Color c) const;
    template<PieceType Pt>
    int count() const;
    template<PieceType Pt>
    Square square(Color c) const;

    // King initial-jump right (spec 6.3)
    int  king_jump_rights() const;
    bool can_king_jump(Color c) const;

    // Checking
    Bitboard checkers() const;

    // Attacks to/from a given square
    Bitboard attackers_to(Square s) const;
    Bitboard attackers_to(Square s, Bitboard occupied) const;
    bool     attackers_to_exist(Square s, Bitboard occupied, Color c) const;
    template<PieceType Pt>
    Bitboard attacks_by(Color c) const;

    // Properties of moves
    bool  legal(Move m) const;
    bool  pseudo_legal(const Move m) const;
    // King initial-jump conditions (spec 6.3) short of the final "own king not
    // attacked afterwards" check, which legal() covers. Shared with movegen.
    bool  king_jump_pseudo_legal(Square from, Square to) const;
    bool  capture(Move m) const;
    bool  capture_stage(Move m) const;
    bool  gives_check(Move m) const;
    Piece moved_piece(Move m) const;
    Piece captured_piece() const;

    // Doing and undoing moves
    void do_move(Move m, StateInfo& newSt, const TranspositionTable* tt);
    void
    do_move(Move m, StateInfo& newSt, bool givesCheck, DirtyPiece& dp, const TranspositionTable* tt);
    void undo_move(Move m);
    void do_null_move(StateInfo& newSt);
    void undo_null_move();

    // Static Exchange Evaluation
    bool see_ge(Move m, int threshold = 0) const;

    // Accessing hash keys
    Key key() const;
    Key prefetch_key(Move m) const;
    Key material_key() const;
    Key pawn_key() const;
    Key minor_piece_key() const;
    Key non_pawn_key(Color c) const;

    // Other properties of the position
    Color side_to_move() const;
    int   game_ply() const;
    bool  is_draw(int ply) const;
    bool  is_repetition(int ply) const;
    bool  upcoming_repetition(int ply) const;
    bool  has_repeated() const;
    int   rule50_count() const;
    Value non_pawn_material(Color c) const;
    Value non_pawn_material() const;

    // Position consistency check, for debugging
    bool                            pos_is_ok() const;
    bool                            material_key_is_ok() const;
    std::optional<PositionSetError> flip();

    StateInfo* state() const;

    void put_piece(Piece pc, Square s);
    void remove_piece(Square s);

   private:
    // Initialization helpers (used while setting up a position)
    Key  compute_material_key() const;
    void set_state() const;

    // Other helpers
    void move_piece(Square from, Square to);
    template<bool AfterMove = false>
    Key adjust_key50(Key k) const;

    // Data members
    std::array<Piece, SQUARE_NB>        board;
    std::array<Bitboard, PIECE_TYPE_NB> byTypeBB;
    std::array<Bitboard, COLOR_NB>      byColorBB;

    int        pieceCount[PIECE_NB];
    StateInfo* st;
    int        gamePly;
    Color      sideToMove;
    DirtyPiece scratch_dp;
};

std::ostream& operator<<(std::ostream& os, const Position& pos);

inline Color Position::side_to_move() const { return sideToMove; }

inline Piece Position::piece_on(Square s) const {
    assert(is_ok(s));
    return board[s];
}

inline const std::array<Piece, SQUARE_NB>& Position::piece_array() const { return board; }

inline bool Position::empty(Square s) const { return piece_on(s) == NO_PIECE; }

inline Piece Position::moved_piece(Move m) const { return piece_on(m.from_sq()); }

inline Bitboard Position::pieces() const { return byTypeBB[ALL_PIECES]; }

template<typename... PieceTypes>
inline Bitboard Position::pieces(PieceTypes... pts) const {
    return (byTypeBB[pts] | ...);
}

inline Bitboard Position::pieces(Color c) const { return byColorBB[c]; }

template<typename... PieceTypes>
inline Bitboard Position::pieces(Color c, PieceTypes... pts) const {
    return pieces(c) & pieces(pts...);
}

template<PieceType Pt>
inline int Position::count(Color c) const {
    return pieceCount[make_piece(c, Pt)];
}

template<PieceType Pt>
inline int Position::count() const {
    return count<Pt>(WHITE) + count<Pt>(BLACK);
}

template<PieceType Pt>
inline Square Position::square(Color c) const {
    assert(count<Pt>(c) == 1);
    return lsb(pieces(c, Pt));
}

inline Square Position::ep_square() const { return st->epSquare; }

inline int Position::king_jump_rights() const { return st->kingJumpRights; }

inline bool Position::can_king_jump(Color c) const {
    return st->kingJumpRights & king_jump_right(c);
}

inline Bitboard Position::attackers_to(Square s) const { return attackers_to(s, pieces()); }

// Hoppers (CANNON/ARCHER/SORCERESS) are not supported by attacks_bb and thus
// not by this template either; their capture and move sets differ.
template<PieceType Pt>
inline Bitboard Position::attacks_by(Color c) const {

    if constexpr (Pt == PAWN)
        return c == WHITE ? pawn_attacks_bb<WHITE>(pieces(WHITE, PAWN))
                          : pawn_attacks_bb<BLACK>(pieces(BLACK, PAWN));
    else
    {
        Bitboard threats;
        Bitboard attackers = pieces(c, Pt);
        while (attackers)
            threats |= attacks_bb<Pt>(pop_lsb(attackers), pieces());
        return threats;
    }
}

inline Bitboard Position::checkers() const { return st->checkersBB; }

inline Key Position::key() const { return adjust_key50(st->key); }

template<bool AfterMove>
inline Key Position::adjust_key50(Key k) const {
    return st->rule50 < 14 - AfterMove ? k : k ^ make_key((st->rule50 - (14 - AfterMove)) / 8);
}

inline Key Position::pawn_key() const { return st->pawnKey; }

inline Key Position::material_key() const { return st->materialKey; }

inline Key Position::minor_piece_key() const { return st->minorPieceKey; }

inline Key Position::non_pawn_key(Color c) const { return st->nonPawnKey[c]; }

inline Value Position::non_pawn_material(Color c) const { return st->nonPawnMaterial[c]; }

inline Value Position::non_pawn_material() const {
    return non_pawn_material(WHITE) + non_pawn_material(BLACK);
}

inline int Position::game_ply() const { return gamePly; }

inline int Position::rule50_count() const { return st->rule50; }

inline bool Position::capture(Move m) const {
    assert(m.is_ok());

    const MoveType mt = m.type_of();

    if (mt == NORMAL)  // includes promotions (they travel in the promo field)
        return !empty(m.to_sq());

    return mt == EN_PASSANT;  // the king jump is never a capture (spec 6.3)
}

// Returns true if a move is generated from the capture stage, having also
// promotions covered, i.e. consistency with the capture stage move generation
// is needed to avoid the generation of duplicate moves.
inline bool Position::capture_stage(Move m) const {
    assert(m.is_ok());
    return capture(m) || m.is_promotion();
}

inline Piece Position::captured_piece() const { return st->capturedPiece; }

inline void Position::put_piece(Piece pc, Square s) {
    board[s] = pc;
    byTypeBB[ALL_PIECES] |= byTypeBB[type_of(pc)] |= s;
    byColorBB[color_of(pc)] |= s;
    pieceCount[pc]++;
    pieceCount[make_piece(color_of(pc), ALL_PIECES)]++;
}

inline void Position::remove_piece(Square s) {
    Piece pc = board[s];

    byTypeBB[ALL_PIECES] ^= s;
    byTypeBB[type_of(pc)] ^= s;
    byColorBB[color_of(pc)] ^= s;
    board[s] = NO_PIECE;
    pieceCount[pc]--;
    pieceCount[make_piece(color_of(pc), ALL_PIECES)]--;
}

inline void Position::move_piece(Square from, Square to) {
    Piece    pc     = board[from];
    Bitboard fromTo = from | to;

    byTypeBB[ALL_PIECES] ^= fromTo;
    byTypeBB[type_of(pc)] ^= fromTo;
    byColorBB[color_of(pc)] ^= fromTo;
    board[from] = NO_PIECE;
    board[to]   = pc;
}

inline void Position::do_move(Move m, StateInfo& newSt, const TranspositionTable* tt = nullptr) {
    do_move(m, newSt, gives_check(m), scratch_dp, tt);
}

inline StateInfo* Position::state() const { return st; }

}  // namespace Stockfish

#endif  // #ifndef POSITION_H_INCLUDED
