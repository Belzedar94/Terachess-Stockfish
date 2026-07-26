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

// Terachess-Stockfish 256-square position layer. Representation contract:
// docs/port-256-design.md (frozen). Rules authority: TERACHESS_SPEC.md.

#include "position.h"

#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>

#include "bitboard.h"
#include "misc.h"
#include "movegen.h"
#include "notation.h"
#include "tt.h"

using std::string;

namespace Stockfish {

namespace Zobrist {

Key psq[PIECE_NB][SQUARE_NB];
// Indexed by SQUARE, not by file: the double step happens from any rank
// (spec 6.1), so the crossed square must enter the key whole (aliasing, spec 5).
// Slot [SQ_NONE] stays zero and is never xored in.
Key ep[SQUARE_NB + 1];
Key kingJump[KING_JUMP_RIGHTS_NB];
Key side, noPawns;

}

namespace {

// Bucket used for StateInfo::minorPieceKey (search correction histories).
// F1 choice, to be re-evaluated with data in F2: every non-pawn, non-king
// piece worth less than a Rook.
constexpr bool is_minor(PieceType pt) {
    return pt != PAWN && pt != KING && PieceTypeValue[pt] < RookValue;
}

}  // namespace


// King initial-jump legality up to (but excluding) the final "own king not
// attacked afterwards" condition, which do-and-check legal() covers (spec 6.3).
// Member (not file-local) because movegen shares the intermediate-square
// geometry when generating KING_JUMP moves.
bool Position::king_jump_pseudo_legal(Square from, Square to) const {

    Color us = side_to_move(), them = ~us;

    if (!can_king_jump(us) || bool(checkers()) || !empty(to))
        return false;

    const int df = file_of(to) - file_of(from), dr = rank_of(to) - rank_of(from);
    if (std::max(std::abs(df), std::abs(dr)) != 2)
        return false;

    auto threatened = [&](Square s) { return attackers_to_exist(s, pieces(), them); };

    // (2,0)/(0,2)/(2,2): the midpoint may be occupied (it is jumped over) but
    // must not be threatened.
    if (std::abs(df) != 1 && std::abs(dr) != 1)
        return !threatened(make_square(File(file_of(from) + df / 2), Rank(rank_of(from) + dr / 2)));

    // Knight-type jump: two intermediate squares, at least one must be free of
    // threat; their occupation is irrelevant (spec 6.3.4).
    Square m1, m2;
    if (std::abs(df) == 2)  // (+-2, +-1)
    {
        m1 = make_square(File(file_of(from) + df / 2), rank_of(from));
        m2 = make_square(File(file_of(from) + df / 2), Rank(rank_of(from) + dr));
    }
    else  // (+-1, +-2)
    {
        m1 = make_square(file_of(from), Rank(rank_of(from) + dr / 2));
        m2 = make_square(File(file_of(from) + df), Rank(rank_of(from) + dr / 2));
    }
    return !threatened(m1) || !threatened(m2);
}


// Returns an ASCII representation of the position
std::ostream& operator<<(std::ostream& os, const Position& pos) {

    string sep = "\n    +";
    for (int i = 0; i < FILE_NB; ++i)
        sep += "---+";
    sep += "\n";

    os << sep;

    for (Rank r = RANK_16;; --r)
    {
        os << " " << std::setw(2) << (1 + r) << " |";
        for (File f = FILE_A; f <= FILE_P; ++f)
        {
            Piece pc = pos.piece_on(make_square(f, r));
            os << " " << (pc == NO_PIECE ? ' ' : piece_to_char(pc)) << " |";
        }
        os << sep;

        if (r == RANK_1)
            break;
    }

    os << "    ";
    for (File f = FILE_A; f <= FILE_P; ++f)
        os << "  " << char('a' + f) << " ";

    os << "\n\nFen: " << pos.fen() << "\nKey: " << std::hex << std::uppercase << std::setfill('0')
       << std::setw(16) << pos.key() << std::setfill(' ') << std::dec << "\nCheckers: ";

    for (Bitboard b = pos.checkers(); b;)
        os << Notation::square(pop_lsb(b)) << " ";

    return os;
}


// Initializes at startup the various arrays used to compute hash keys
void Position::init() {

    PRNG rng(1070372);

    for (Color c : {WHITE, BLACK})
        for (PieceType pt = PAWN; pt <= KING; ++pt)
            for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
                Zobrist::psq[make_piece(c, pt)][s] = rng.rand<Key>();

    for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
        Zobrist::ep[s] = rng.rand<Key>();
    Zobrist::ep[SQ_NONE] = 0;

    for (int kjr = 0; kjr < KING_JUMP_RIGHTS_NB; ++kjr)
        Zobrist::kingJump[kjr] = rng.rand<Key>();

    Zobrist::side    = rng.rand<Key>();
    Zobrist::noPawns = rng.rand<Key>();

    // Cuckoo tables (upcoming_repetition) are disabled in F1 (ADR): the
    // master algorithm enumerates reversible moves per piece pair, which
    // needs re-derivation for 26 piece types on 256 squares. Re-evaluate in
    // F2 with an enlarged table.
}


// Initializes the position object with the given FEN-TSF string (spec 3.2).
// The FEN string is validated; if it is invalid or inconsistent, a
// PositionSetError describing the problem is returned, otherwise std::nullopt.
//
//   <pieces> <turn> <king-jump-rights> <ep-square> <halfmove> <fullmove>
//
// - pieces: 16 ranks from 16 down to 1 separated by '/'; empty runs as
//   integers, consecutive digits parsed greedily as ONE number 1-16 (so a
//   run may be split around pieces, e.g. "3pP11", but "88" is rejected).
// - turn: 'w' | 'b'.
// - king-jump-rights: 'K' white king may still make its initial jump
//   (spec 6.3), 'k' idem black, '-' none.
// - ep-square: square crossed by the last double step (spec 6.2) in algebraic
//   notation, or '-'. Accepted unconditionally, kept only if an enemy pawn
//   could actually capture en passant (canonical state).
std::optional<PositionSetError> Position::set(const string& fenStr, StateInfo* si) {

    unsigned char      token;
    std::istringstream ss(fenStr);

    std::memset(static_cast<void*>(this), 0, sizeof(Position));
    std::memset(static_cast<void*>(si), 0, sizeof(StateInfo));
    st           = si;
    st->epSquare = SQ_NONE;  // memset left 0 == a1

    ss >> std::noskipws;

    int file = FILE_A;
    int rank = RANK_16;

    // 1. Piece placement
    for (;;)
    {
        if (!(ss >> token))
            return PositionSetError("Invalid FEN. Unexpected end of stream.");

        if (isspace(token))
            break;

        if (isdigit(token))
        {
            int run = token - '0';
            while (ss.peek() != EOF && isdigit(ss.peek()))
            {
                ss >> token;
                run = run * 10 + (token - '0');
                if (run > 16)
                    return PositionSetError("Invalid FEN. Empty-square run larger than 16.");
            }
            if (run < 1)
                return PositionSetError("Invalid FEN. Empty-square run of zero.");

            file += run;
            if (file > FILE_NB)
                return PositionSetError("Invalid FEN. Invalid file reached.");
        }
        else if (token == '/')
        {
            if (file != FILE_NB)
                return PositionSetError(
                  "Invalid FEN. Trying to end rank when not at the end of it.");

            --rank;
            file = FILE_A;

            if (rank < RANK_1)
                return PositionSetError("Invalid FEN. Invalid rank reached.");
        }
        else
        {
            if (file >= FILE_NB)
                return PositionSetError("Invalid FEN. Invalid file reached.");

            const Piece pc = piece_from_char(char(token));
            if (pc == NO_PIECE)
                return PositionSetError(std::string("Invalid FEN. Invalid piece: ")
                                        + std::string(1, char(token)));

            put_piece(pc, make_square(File(file), Rank(rank)));
            ++file;
        }
    }

    if (rank != RANK_1 || file != FILE_NB)
        return PositionSetError("Invalid FEN. Board state encoding ended but cursor not at end.");

    if (count<KING>(WHITE) != 1 || count<KING>(BLACK) != 1)
        return PositionSetError("Unsupported position. Incorrect number of kings.");

    if (PseudoAttacks[KING][square<KING>(WHITE)] & pieces(BLACK, KING))
        return PositionSetError("Unsupported position. Kings are adjacent.");

    // A pawn on its last rank would have promoted (spec 6.4). A Troll may sit
    // there (jump arrivals do not promote).
    if ((pieces(WHITE, PAWN) & Rank16BB) || (pieces(BLACK, PAWN) & Rank1BB))
        return PositionSetError("Unsupported position. Pawns on their last rank.");

    // 2. Active color
    if (!(ss >> token))
        return PositionSetError("Invalid FEN. Unexpected end of stream.");
    if (token != 'w' && token != 'b')
        return PositionSetError(std::string("Invalid FEN. Invalid side to move: ")
                                + std::string(1, char(token)));
    sideToMove = (token == 'w' ? WHITE : BLACK);
    if (!(ss >> token) || !isspace(token) || ss.eof())
        return PositionSetError("Invalid FEN. Expected whitespace after side to move.");

    // 3. King-jump rights (spec 6.3): 'K', 'k', canonical order "Kk", or '-'.
    // A right only claims "this king has not moved yet"; it cannot be
    // cross-checked against the board, so it is accepted as given.
    int kjr = 0;
    for (;;)
    {
        if (!(ss >> token))
            return PositionSetError("Invalid FEN. Unexpected end of stream.");

        if (isspace(token))
            break;

        if (token == 'K')
            kjr |= WHITE_JUMP;
        else if (token == 'k')
            kjr |= BLACK_JUMP;
        else if (token == '-' && kjr == 0)
            continue;  // next char must be the field separator
        else
            return PositionSetError(std::string("Invalid FEN. Expected king-jump rights. Got: ")
                                    + std::string(1, char(token)));
    }
    st->kingJumpRights = kjr;

    // 4. En passant square: '-' or file letter plus 1-2 digit rank.
    unsigned char col;
    if (!(ss >> col))
        return PositionSetError("Invalid FEN. Unexpected end of stream.");
    if (col != '-')
    {
        if (col < 'a' || col > 'p')
            return PositionSetError("Invalid FEN. Invalid en-passant square.");

        unsigned char row;
        if (!(ss >> row) || !isdigit(row))
            return PositionSetError("Invalid FEN. Invalid en-passant square.");
        int r = row - '0';
        if (ss.peek() != EOF && isdigit(ss.peek()))
        {
            ss >> row;
            r = r * 10 + (row - '0');
        }
        if (r < 1 || r > 16)
            return PositionSetError("Invalid FEN. Invalid en-passant square.");

        const Square eps  = make_square(File(col - 'a'), Rank(r - 1));
        const Color  them = ~sideToMove;

        // Keep the square only if the double step that produced it is
        // plausible (the stepped Pawn or Prince sits one push beyond, both
        // crossed squares are empty) and one of our pawns can capture
        // (spec 6.2; parsers accept the unconditional square, the state and
        // the canonical emitter demand a capturer).
        const int toDbl   = int(eps) + int(pawn_push(them));
        const int fromDbl = int(eps) - int(pawn_push(them));

        const bool valid = is_ok(Square(toDbl)) && is_ok(Square(fromDbl)) && empty(eps)
                        && empty(Square(fromDbl))
                        && (pieces(them, PAWN, PRINCE) & Square(toDbl))
                        && (PawnAttacks[them][eps] & pieces(sideToMove, PAWN));

        st->epSquare = valid ? eps : SQ_NONE;
    }

    // 5-6. Halfmove clock and fullmove number
    ss >> std::skipws >> st->rule50 >> gamePly;

    if (st->rule50 < 0 || st->rule50 > 32767)
        return PositionSetError("Unsupported position. Rule50 counter out of range.");

    if (gamePly < 0 || gamePly > 100000)
        return PositionSetError("Unsupported position. Game ply out of range.");

    // Convert from fullmove starting from 1 to gamePly starting from 0,
    // handle also common incorrect FEN with fullmove = 0.
    gamePly = std::max(2 * (gamePly - 1), 0) + (sideToMove == BLACK);

    set_state();

    // The side not to move must not be in check (the side to move could
    // otherwise capture the king).
    if (attackers_to(square<KING>(~sideToMove)) & pieces(sideToMove))
        return PositionSetError("Unsupported position. Side not to move is in check.");

    assert(pos_is_ok());

    return std::nullopt;
}


// Computes the hash keys of the position, and other
// data that once computed is updated incrementally as moves are made.
// The function is only used when a new position is set up.
void Position::set_state() const {

    st->key               = 0;
    st->minorPieceKey     = 0;
    st->nonPawnKey[WHITE] = st->nonPawnKey[BLACK] = 0;
    st->pawnKey                                   = Zobrist::noPawns;
    st->nonPawnMaterial[WHITE] = st->nonPawnMaterial[BLACK] = VALUE_ZERO;
    st->checkersBB = attackers_to(square<KING>(sideToMove)) & pieces(~sideToMove);

    for (Bitboard b = pieces(); b;)
    {
        Square s  = pop_lsb(b);
        Piece  pc = piece_on(s);
        st->key ^= Zobrist::psq[pc][s];

        if (type_of(pc) == PAWN)
            st->pawnKey ^= Zobrist::psq[pc][s];

        else
        {
            st->nonPawnKey[color_of(pc)] ^= Zobrist::psq[pc][s];

            if (type_of(pc) != KING)
            {
                st->nonPawnMaterial[color_of(pc)] += PieceValue[pc];

                if (is_minor(type_of(pc)))
                    st->minorPieceKey ^= Zobrist::psq[pc][s];
            }
        }
    }

    if (st->epSquare != SQ_NONE)
        st->key ^= Zobrist::ep[st->epSquare];

    if (sideToMove == BLACK)
        st->key ^= Zobrist::side;

    st->key ^= Zobrist::kingJump[st->kingJumpRights];

    st->materialKey = compute_material_key();
}

Key Position::compute_material_key() const {
    Key k = 0;
    for (Color c : {WHITE, BLACK})
        for (PieceType pt = PAWN; pt <= KING; ++pt)
        {
            Piece pc = make_piece(c, pt);
            for (int cnt = 0; cnt < pieceCount[pc]; ++cnt)
                k ^= Zobrist::psq[pc][cnt];
        }
    return k;
}


// Returns a FEN-TSF representation of the position (spec 3.2). Canonical
// emitter: maximal empty runs, rights in "Kk" order, ep square only when a
// capturer exists (guaranteed by construction of st->epSquare).
string Position::fen() const {

    int                emptyCnt;
    std::ostringstream ss;

    for (Rank r = RANK_16;; --r)
    {
        for (File f = FILE_A; f <= FILE_P; ++f)
        {
            for (emptyCnt = 0; f <= FILE_P && empty(make_square(f, r)); ++f)
                ++emptyCnt;

            if (emptyCnt)
                ss << emptyCnt;

            if (f <= FILE_P)
                ss << piece_to_char(piece_on(make_square(f, r)));
        }

        if (r == RANK_1)
            break;
        ss << '/';
    }

    ss << (sideToMove == WHITE ? " w " : " b ");

    if (st->kingJumpRights & WHITE_JUMP)
        ss << 'K';
    if (st->kingJumpRights & BLACK_JUMP)
        ss << 'k';
    if (!st->kingJumpRights)
        ss << '-';

    ss << (ep_square() == SQ_NONE ? " - " : " " + Notation::square(ep_square()) + " ")
       << st->rule50 << " " << 1 + (gamePly - (sideToMove == BLACK)) / 2;

    return ss.str();
}


// Computes a bitboard of all pieces of both colors which attack a given
// square, i.e. could pseudo-legally capture there (spec 5): screen captures
// and the pawn-type captures of Pawn and Troll included; move-only actions
// (double steps, king jump, hopper slides) excluded. Slider, hopper and bent
// rider attacks use the given occupancy. All patterns except the bent riders
// are symmetric, so they are matched from the target square; the Eagles and
// Rhinos (at most 2 per side) are iterated explicitly.
Bitboard Position::attackers_to(Square s, Bitboard occupied) const {

    Bitboard b =
      (ray_attacks_mask(s, ORTHO_RAYS, occupied) & pieces(ROOK, QUEEN, ADMIRAL, MARSHALL, AMAZON))
      | (ray_attacks_mask(s, DIAG_RAYS, occupied)
         & pieces(BISHOP, QUEEN, MISSIONARY, CARDINAL, AMAZON))
      | (PseudoAttacks[KNIGHT][s] & pieces(KNIGHT, MARSHALL, CARDINAL, AMAZON))
      | (PseudoAttacks[CAMEL][s] & pieces(CAMEL)) | (PseudoAttacks[GIRAFFE][s] & pieces(GIRAFFE))
      | (PseudoAttacks[BUFFALO][s] & pieces(BUFFALO))
      | (PseudoAttacks[ELEPHANT][s] & pieces(ELEPHANT))
      | (PseudoAttacks[MACHINE][s] & pieces(MACHINE)) | (PseudoAttacks[LION][s] & pieces(LION))
      | (PseudoAttacks[DUCHESS][s] & pieces(DUCHESS))
      | (PseudoAttacks[CENTAUR][s] & pieces(CENTAUR))
      | (PseudoAttacks[TROLL][s] & pieces(TROLL))       // (3,3)/(0,3) jumps
      | (PseudoAttacks[KING][s] & pieces(KING, PRINCE))  // Prince attacks as a King
      | (DiagStepBB[s] & pieces(ADMIRAL)) | (OrthStepBB[s] & pieces(MISSIONARY))
      // Diagonal-forward captures of Pawn and Troll (fcF)
      | (PawnAttacks[BLACK][s] & pieces(WHITE) & pieces(PAWN, TROLL))
      | (PawnAttacks[WHITE][s] & pieces(BLACK) & pieces(PAWN, TROLL))
      // Screen captures (spec 4.2): a hopper on the second blocker from s
      // attacks s (same screen square in both scan directions)
      | (hopper_captures(s, occupied, ORTHO_RAYS) & pieces(CANNON, SORCERESS))
      | (hopper_captures(s, occupied, DIAG_RAYS) & pieces(ARCHER, SORCERESS));

    // Bent riders. The empty-board pattern is symmetric (it only depends on
    // |df|,|dr|), so PseudoAttacks[s] prefilters the candidates; the real
    // occupancy-aware set is then checked per piece.
    Bitboard bent =
      (pieces(EAGLE) & PseudoAttacks[EAGLE][s]) | (pieces(RHINO) & PseudoAttacks[RHINO][s]);
    while (bent)
    {
        Square e = pop_lsb(bent);
        if (attacks_bb(type_of(piece_on(e)), e, occupied) & s)
            b |= square_bb(e);
    }

    return b;
}

// Checks if at least one piece of a given color attacks a given square.
// F1 correctness-first wrapper. // T256-TODO: early-exit version in F2.
bool Position::attackers_to_exist(Square s, Bitboard occupied, Color c) const {
    return bool(attackers_to(s, occupied) & pieces(c));
}

// Tests whether a pseudo-legal move is legal: after making it, the own King
// must not be attacked (spec 5). F1 do-and-check on the real board (frozen
// contract: no pin machinery, screens make it tricky); the mutation is fully
// reverted before returning.
bool Position::legal(Move m) const {

    assert(m.is_ok());

    Position& pos = const_cast<Position&>(*this);

    Color  us   = sideToMove;
    Color  them = ~us;
    Square from = m.from_sq();
    Square to   = m.to_sq();

    assert(color_of(moved_piece(m)) == us);

    Square capsq    = m.type_of() == EN_PASSANT ? to - pawn_push(us) : to;
    Piece  captured = board[capsq];

    assert(captured == NO_PIECE || color_of(captured) == them);

    if (captured)
        pos.remove_piece(capsq);
    pos.move_piece(from, to);

    // The promotion type is irrelevant here: only occupancy affects whether
    // our king is attacked, never the type of our own piece.
    Square ksq = type_of(board[to]) == KING ? to : square<KING>(us);
    bool   ok  = !attackers_to_exist(ksq, pieces(), them);

    pos.move_piece(to, from);
    if (captured)
        pos.put_piece(captured, capsq);

    return ok;
}


// Takes a random move and tests whether the move is pseudo-legal. It is used
// to validate moves from TT that can be corrupted due to SMP concurrent
// access or hash position key aliasing.
bool Position::pseudo_legal(const Move m) const {

    Color  us   = sideToMove;
    Color  them = ~us;
    Square from = m.from_sq();
    Square to   = m.to_sq();
    Piece  pc   = moved_piece(m);

    // Bits 23-31 of a move are always zero: a raced or corrupted TT entry
    // carrying payload there is rejected outright.
    if (m.raw() & ~0x7FFFFFu)
        return false;

    if (pc == NO_PIECE || color_of(pc) != us || from == to)
        return false;

    const PieceType pt       = type_of(pc);
    const PieceType promo    = m.promotion_type();
    const bool      lastRank = relative_rank(us, to) == RANK_16;
    const Bitboard  occ      = pieces();

    if (m.type_of() == KING_JUMP)
        return pt == KING && promo == NO_PIECE_TYPE && king_jump_pseudo_legal(from, to);

    // The destination square cannot be occupied by a friendly piece
    if (pieces(us) & to)
        return false;

    if (m.type_of() == EN_PASSANT)
        return pt == PAWN && promo == NO_PIECE_TYPE && st->epSquare != SQ_NONE
            && to == st->epSquare && bool(PawnAttacks[us][from] & to);

    // NORMAL. The promo field must match the forced table (spec 6.4) exactly
    // when the piece reaches its last rank, and be empty otherwise.
    switch (pt)
    {
    case PAWN : {
        const bool okMove = (bool(PawnAttacks[us][from] & to) && bool(pieces(them) & to))
                         || (to == from + pawn_push(us) && empty(to))
                         || (int(to) - int(from) == 2 * int(pawn_push(us)) && empty(to)
                             && empty(from + pawn_push(us)));
        return okMove && promo == (lastRank ? QUEEN : NO_PIECE_TYPE);
    }
    case PRINCE : {
        const bool okMove = bool(PseudoAttacks[KING][from] & to)
                         || (int(to) - int(from) == 2 * int(pawn_push(us)) && empty(to)
                             && empty(from + pawn_push(us)));
        return okMove && promo == (lastRank ? AMAZON : NO_PIECE_TYPE);
    }
    case TROLL :
        // Jump arrival: move-or-capture, never promotes (spec 6.4)
        if (PseudoAttacks[TROLL][from] & to)
            return promo == NO_PIECE_TYPE;
        // Pawn-step arrival: promotes on the last rank
        return ((to == from + pawn_push(us) && empty(to))
                || (bool(PawnAttacks[us][from] & to) && bool(pieces(them) & to)))
            && promo == (lastRank ? QUEEN : NO_PIECE_TYPE);
    case CANNON :
    case ARCHER :
    case SORCERESS : {
        const int dirs = pt == CANNON ? ORTHO_RAYS : pt == ARCHER ? DIAG_RAYS : ALL_RAYS;
        return promo == NO_PIECE_TYPE
            && (empty(to) ? bool(hopper_quiet(from, occ, dirs) & to)
                          : bool(hopper_captures(from, occ, dirs) & to));
    }
    case KNIGHT :
    case CAMEL :
    case GIRAFFE :
        return bool(PseudoAttacks[pt][from] & to)
            && promo == (lastRank ? BUFFALO : NO_PIECE_TYPE);
    case ELEPHANT :
    case MACHINE :
    case CENTAUR :
        return bool(PseudoAttacks[pt][from] & to) && promo == (lastRank ? LION : NO_PIECE_TYPE);
    default :
        return bool(attacks_bb(pt, from, occ) & to) && promo == NO_PIECE_TYPE;
    }
}


// Tests whether a pseudo-legal move gives check. F1 direct version (frozen
// contract): apply the board part of the move, ask attackers_to, revert.
// // T256-TODO: incremental version in F2.
bool Position::gives_check(Move m) const {

    assert(m.is_ok());
    assert(color_of(moved_piece(m)) == sideToMove);

    Position& pos = const_cast<Position&>(*this);

    Color  us   = sideToMove;
    Color  them = ~us;
    Square from = m.from_sq();
    Square to   = m.to_sq();

    Square capsq    = m.type_of() == EN_PASSANT ? to - pawn_push(us) : to;
    Piece  captured = board[capsq];
    Piece  pc       = board[from];

    if (captured)
        pos.remove_piece(capsq);
    pos.move_piece(from, to);
    if (m.is_promotion())
    {
        pos.remove_piece(to);
        pos.put_piece(make_piece(us, m.promotion_type()), to);
    }

    bool check = attackers_to_exist(square<KING>(them), pieces(), us);

    if (m.is_promotion())
    {
        pos.remove_piece(to);
        pos.put_piece(pc, to);
    }
    pos.move_piece(to, from);
    if (captured)
        pos.put_piece(captured, capsq);

    return check;
}


// Makes a move, and saves all information necessary to a StateInfo object.
// The move is assumed to be legal. Pseudo-legal moves should be filtered out
// before this function is called. If a pointer to the TT table is passed,
// the entry for the new position will be prefetched.
void Position::do_move(
  Move m, StateInfo& newSt, bool givesCheck, DirtyPiece& dp, const TranspositionTable* tt) {

    assert(m.is_ok());
    assert(&newSt != st);

    Key k = st->key ^ Zobrist::side;

    // Copy some fields of the old state to our new StateInfo object except the
    // ones which are going to be recalculated from scratch anyway and then switch
    // our state pointer to point to the new (ready to be updated) state.
    // StateInfo is no longer trivially copyable (Bitboard has user-provided
    // constructors) but is still standard-layout plain data; byte copy is fine.
    std::memcpy(static_cast<void*>(&newSt), static_cast<const void*>(st),
                offsetof(StateInfo, key));
    newSt.previous = st;
    st             = &newSt;

    // Increment ply counters. In particular, rule50 will be reset to zero later on
    // in case of a capture or a pawn move.
    ++gamePly;
    ++st->rule50;
    ++st->pliesFromNull;

    Color  us       = sideToMove;
    Color  them     = ~us;
    Square from     = m.from_sq();
    Square to       = m.to_sq();
    Piece  pc       = piece_on(from);
    Square capsq    = m.type_of() == EN_PASSANT ? to - pawn_push(us) : to;
    Piece  captured = piece_on(capsq);

    assert(color_of(pc) == us);
    assert(captured == NO_PIECE || color_of(captured) == them);
    assert(type_of(captured) != KING);
    assert(m.type_of() != KING_JUMP || (type_of(pc) == KING && captured == NO_PIECE));
    // The en-passant victim can be a Pawn or a Prince (spec 6.2)
    assert(m.type_of() != EN_PASSANT
           || (type_of(pc) == PAWN && to == st->epSquare
               && (type_of(captured) == PAWN || type_of(captured) == PRINCE)));

    dp.pc        = pc;
    dp.from      = from;
    dp.to        = to;
    dp.add_sq    = SQ_NONE;
    dp.remove_sq = SQ_NONE;

    if (captured)
    {
        // If the captured piece is a pawn, update pawn hash key, otherwise
        // update non-pawn material.
        if (type_of(captured) == PAWN)
            st->pawnKey ^= Zobrist::psq[captured][capsq];
        else
        {
            st->nonPawnMaterial[them] -= PieceValue[captured];
            st->nonPawnKey[them] ^= Zobrist::psq[captured][capsq];

            if (is_minor(type_of(captured)))
                st->minorPieceKey ^= Zobrist::psq[captured][capsq];
        }

        dp.remove_pc = captured;
        dp.remove_sq = capsq;

        k ^= Zobrist::psq[captured][capsq];
        st->materialKey ^= Zobrist::psq[captured][pieceCount[captured] - 1];

        // Reset rule 50 counter
        st->rule50 = 0;
    }

    // Update hash key
    k ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];

    // Reset en passant square
    if (st->epSquare != SQ_NONE)
    {
        k ^= Zobrist::ep[st->epSquare];
        st->epSquare = SQ_NONE;
    }

    // Any king move (including the jump itself) permanently loses the
    // initial-jump right (spec 6.3.5)
    if (type_of(pc) == KING && (st->kingJumpRights & king_jump_right(us)))
    {
        k ^= Zobrist::kingJump[st->kingJumpRights];
        st->kingJumpRights &= ~king_jump_right(us);
        k ^= Zobrist::kingJump[st->kingJumpRights];
    }

    if (type_of(pc) == PAWN)
    {
        st->pawnKey ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];

        // Only true Pawn moves reset the 50-move counter; the pawn-like steps
        // of Troll and Prince do not (spec 7.4)
        st->rule50 = 0;
    }
    else
    {
        st->nonPawnKey[us] ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];

        if (is_minor(type_of(pc)))
            st->minorPieceKey ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];
    }

    // Double step of Pawn or Prince, from any rank (spec 6.1): mark the
    // crossed square as en-passant square if an enemy pawn could capture there
    if ((type_of(pc) == PAWN || type_of(pc) == PRINCE)
        && int(to) - int(from) == 2 * int(pawn_push(us))
        && (PawnAttacks[us][from + pawn_push(us)] & pieces(them, PAWN)))
    {
        st->epSquare = from + pawn_push(us);
        k ^= Zobrist::ep[st->epSquare];
    }

    // Forced promotion (spec 6.4). The move carries the resulting type; for
    // the Troll the generator only sets it on pawn-step arrivals.
    Piece promoted = NO_PIECE;
    if (m.is_promotion())
    {
        promoted = make_piece(us, m.promotion_type());

        assert(relative_rank(us, to) == RANK_16);
        assert(m.promotion_type() == promoted_piece_type(type_of(pc)));

        dp.add_pc = promoted;
        dp.add_sq = to;
        dp.to     = SQ_NONE;

        k ^= Zobrist::psq[pc][to] ^ Zobrist::psq[promoted][to];
        st->materialKey ^=
          Zobrist::psq[pc][pieceCount[pc] - 1] ^ Zobrist::psq[promoted][pieceCount[promoted]];

        if (type_of(pc) == PAWN)
            st->pawnKey ^= Zobrist::psq[pc][to];
        else
        {
            st->nonPawnKey[us] ^= Zobrist::psq[pc][to];
            st->nonPawnMaterial[us] -= PieceValue[pc];
            if (is_minor(type_of(pc)))
                st->minorPieceKey ^= Zobrist::psq[pc][to];
        }

        st->nonPawnKey[us] ^= Zobrist::psq[promoted][to];
        st->nonPawnMaterial[us] += PieceValue[promoted];
        if (is_minor(m.promotion_type()))  // never true with the 6.4 table
            st->minorPieceKey ^= Zobrist::psq[promoted][to];
    }

    if (tt)
        prefetch(tt->first_entry(adjust_key50(k)));

    // Update the key with the final value
    st->key = k;

    // Update the board (all key updates above used the pre-move board)
    if (captured)
        remove_piece(capsq);
    move_piece(from, to);
    if (promoted)
    {
        remove_piece(to);
        put_piece(promoted, to);
    }

    st->capturedPiece = captured;
    st->movedPiece    = pc;

    sideToMove = ~sideToMove;

    // Update the checkers bitboard
    st->checkersBB =
      givesCheck ? attackers_to(square<KING>(sideToMove)) & pieces(~sideToMove) : Bitboard();
    assert(!givesCheck || bool(st->checkersBB));

    // Calculate the repetition info. It is the ply distance from the previous
    // occurrence of the same position, negative in the 3-fold case, or zero
    // if the position was not repeated.
    st->repetition = 0;
    int end        = std::min(st->rule50, st->pliesFromNull);
    if (end >= 4)
    {
        StateInfo* stp = st->previous->previous;
        for (int i = 4; i <= end; i += 2)
        {
            stp = stp->previous->previous;
            if (stp->key == st->key)
            {
                st->repetition = stp->repetition ? -i : i;
                break;
            }
        }
    }

    assert(pos_is_ok());
}


// Unmakes a move. When it returns, the position should
// be restored to exactly the same state as before the move was made.
void Position::undo_move(Move m) {

    assert(m.is_ok());

    sideToMove = ~sideToMove;

    Color  us   = sideToMove;
    Square from = m.from_sq();
    Square to   = m.to_sq();

    assert(empty(from));
    assert(type_of(st->capturedPiece) != KING);

    if (m.is_promotion())
    {
        assert(relative_rank(us, to) == RANK_16);
        assert(type_of(piece_on(to)) == m.promotion_type());

        remove_piece(to);
        put_piece(st->movedPiece, to);
    }

    move_piece(to, from);  // Put the piece back at the source square

    if (st->capturedPiece)
    {
        Square capsq = m.type_of() == EN_PASSANT ? to - pawn_push(us) : to;
        put_piece(st->capturedPiece, capsq);  // Restore the captured piece
    }

    // Finally point our state pointer back to the previous state
    st = st->previous;
    --gamePly;

    assert(pos_is_ok());
}


Key Position::prefetch_key(Move m) const {
    Square from     = m.from_sq();
    Square to       = m.to_sq();
    Piece  pc       = piece_on(from);
    Piece  captured = piece_on(to);  // Zobrist::psq[NO_PIECE][*] is zero
    Key    k        = st->key ^ Zobrist::side;

    k ^= Zobrist::psq[captured][to] ^ Zobrist::psq[pc][to] ^ Zobrist::psq[pc][from];

    if (captured || type_of(pc) == PAWN)
        return k;

    return adjust_key50<true>(k);
}


// Used to do a "null move": it flips
// the side to move without executing any move on the board.
void Position::do_null_move(StateInfo& newSt) {

    assert(!checkers());
    assert(&newSt != st);

    std::memcpy(static_cast<void*>(&newSt), static_cast<const void*>(st), sizeof(StateInfo));

    newSt.previous = st;
    st             = &newSt;

    if (st->epSquare != SQ_NONE)
    {
        st->key ^= Zobrist::ep[st->epSquare];
        st->epSquare = SQ_NONE;
    }

    st->key ^= Zobrist::side;

    st->pliesFromNull = 0;

    st->capturedPiece = NO_PIECE;

    sideToMove = ~sideToMove;

    st->repetition = 0;

    assert(pos_is_ok());
}


// Must be used to undo a "null move"
void Position::undo_null_move() {

    st         = st->previous;
    sideToMove = ~sideToMove;
}


// Tests if the SEE (Static Exchange Evaluation) value of the move is greater
// or equal to the given threshold, with an algorithm similar to alpha-beta
// pruning with a null window. F1 correctness-first: attackers (including
// hoppers and bent riders, whose attack sets change with every removal in
// non-x-ray ways) are recomputed from scratch every iteration.
// // T256-TODO: optimize in F2 (incremental x-ray updates where valid).
bool Position::see_ge(Move m, int threshold) const {

    assert(m.is_ok());

    // Only deal with normal moves, assume others pass a simple SEE
    if (m.type_of() != NORMAL)
        return VALUE_ZERO >= threshold;

    Square from = m.from_sq(), to = m.to_sq();

    assert(piece_on(from) != NO_PIECE);

    int swap = PieceValue[piece_on(to)] - threshold;
    if (swap < 0)
        return false;

    swap = PieceValue[piece_on(from)] - swap;
    if (swap <= 0)
        return true;

    assert(color_of(piece_on(from)) == sideToMove);
    Bitboard occupied = pieces() ^ from ^ to;
    Color    stm      = sideToMove;
    int      res      = 1;

    while (true)
    {
        stm = ~stm;

        Bitboard stmAttackers = attackers_to(to, occupied) & occupied & pieces(stm);

        // If stm has no more attackers then give up: stm loses
        if (!stmAttackers)
            break;

        res ^= 1;

        // Locate the least valuable attacker; the king only if nothing else
        Square bestSq  = SQ_NONE;
        int    bestVal = 0;
        for (Bitboard b = stmAttackers; b;)
        {
            Square x = pop_lsb(b);
            int    v = type_of(piece_on(x)) == KING ? VALUE_INFINITE : PieceValue[piece_on(x)];
            if (bestSq == SQ_NONE || v < bestVal)
            {
                bestSq  = x;
                bestVal = v;
            }
        }

        if (type_of(piece_on(bestSq)) == KING)
            // If we "capture" with the king but the opponent still has
            // attackers, reverse the result.
            return bool(attackers_to(to, occupied) & occupied & pieces(~stm)) ? bool(res ^ 1)
                                                                              : bool(res);

        if ((swap = PieceValue[piece_on(bestSq)] - swap) < res)
            break;

        occupied ^= square_bb(bestSq);
    }

    return bool(res);
}

// Tests whether the position is drawn by 50-move rule or by repetition.
// It does not detect stalemates.
bool Position::is_draw(int ply) const {

    // A mate delivered exactly on the 100th irreversible ply takes precedence
    // over the 50-move rule ([SUPUESTO] FIDE-analogous, spec 7.4).
    if (st->rule50 > 99 && (!checkers() || MoveList<LEGAL>(*this).size()))
        return true;

    return is_repetition(ply);
}

// Return a draw score if a position repeats once earlier but strictly
// after the root, or repeats twice before or at the root.
bool Position::is_repetition(int ply) const { return st->repetition && st->repetition < ply; }

// Tests whether there has been at least one repetition
// of positions since the last capture or pawn move.
bool Position::has_repeated() const {

    StateInfo* stc = st;
    int        end = std::min(st->rule50, st->pliesFromNull);
    while (end-- >= 4)
    {
        if (stc->repetition)
            return true;

        stc = stc->previous;
    }
    return false;
}


// Cuckoo-based upcoming-repetition detection is disabled in F1 (ADR, frozen
// contract): always report no upcoming repetition. Re-evaluate in F2 with an
// enlarged table and the 26-piece reversible-move enumeration.
bool Position::upcoming_repetition(int) const { return false; }


// Flips position with the white and black sides reversed. This
// is only useful for debugging e.g. for finding evaluation symmetry bugs.
std::optional<PositionSetError> Position::flip() {

    string            f, token;
    std::stringstream ss(fen());

    for (Rank r = RANK_16;; --r)  // Piece placement
    {
        std::getline(ss, token, r > RANK_1 ? '/' : ' ');
        f.insert(0, token + (f.empty() ? " " : "/"));

        if (r == RANK_1)
            break;
    }

    ss >> token;                        // Active color
    f += (token == "w" ? "B " : "W ");  // Will be lowercased later

    ss >> token;  // King-jump rights
    f += token + " ";

    std::transform(f.begin(), f.end(), f.begin(),
                   [](char c) { return char(islower(c) ? toupper(c) : tolower(c)); });

    ss >> token;  // En passant square: mirror the rank
    if (token == "-")
        f += token;
    else
    {
        const File file = File(token[0] - 'a');
        const Rank rank = Rank(std::stoi(token.substr(1)) - 1);
        f += Notation::square(flip_rank(make_square(file, rank)));
    }

    std::getline(ss, token);  // Half and full moves
    f += token;

    return set(f, st);
}


bool Position::material_key_is_ok() const { return compute_material_key() == st->materialKey; }


// Performs some consistency checks for the position object
// and raise an assert if something wrong is detected.
// This is meant to be helpful when debugging.
bool Position::pos_is_ok() const {

    if (sideToMove != WHITE && sideToMove != BLACK)
        assert(0 && "pos_is_ok: Default");

    if (ep_square() != SQ_NONE)
    {
        // The double-stepped enemy Pawn or Prince must sit one push beyond
        const Square stepped = ep_square() + pawn_push(~sideToMove);
        if (!is_ok(stepped) || !empty(ep_square())
            || !(pieces(~sideToMove, PAWN, PRINCE) & stepped))
            assert(0 && "pos_is_ok: Ep square");
    }

    if (count<KING>(WHITE) != 1 || count<KING>(BLACK) != 1
        || piece_on(square<KING>(WHITE)) != W_KING || piece_on(square<KING>(BLACK)) != B_KING
        || bool(attackers_to(square<KING>(~sideToMove)) & pieces(sideToMove)))
        assert(0 && "pos_is_ok: Kings");

    if ((pieces(WHITE, PAWN) & Rank16BB) || (pieces(BLACK, PAWN) & Rank1BB))
        assert(0 && "pos_is_ok: Pawns");

    if ((pieces(WHITE) & pieces(BLACK)) || (pieces(WHITE) | pieces(BLACK)) != pieces()
        || popcount(pieces(WHITE)) > 64 || popcount(pieces(BLACK)) > 64)
        assert(0 && "pos_is_ok: Bitboards");

    for (PieceType p1 = PAWN; p1 <= KING; ++p1)
        for (PieceType p2 = PAWN; p2 <= KING; ++p2)
            if (p1 != p2 && bool(pieces(p1) & pieces(p2)))
                assert(0 && "pos_is_ok: Bitboards");

    for (Color c : {WHITE, BLACK})
        for (PieceType pt = PAWN; pt <= KING; ++pt)
        {
            const Piece pc = make_piece(c, pt);
            if (pieceCount[pc] != popcount(pieces(c, pt))
                || pieceCount[pc] != std::count(board.begin(), board.end(), pc))
                assert(0 && "pos_is_ok: Pieces");
        }

    assert(material_key_is_ok() && "pos_is_ok: materialKey");

    return true;
}

}  // namespace Stockfish
