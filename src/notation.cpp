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

#include "notation.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>

#include "bitboard.h"
#include "position.h"

namespace Stockfish::Notation {

std::string square(Square s) {
    return char('a' + file_of(s)) + std::to_string(1 + rank_of(s));
}

std::string move(Move m) {
    if (m == Move::none())
        return "(none)";

    if (m == Move::null())
        return "0000";

    std::string str = square(m.from_sq()) + square(m.to_sq());

    // The promotion suffix is redundant (spec 6.4: forced and deterministic)
    // but always emitted, in lowercase (spec 11)
    if (m.is_promotion())
        str += PieceTypeToChar[m.promotion_type()];

    return str;
}

std::string to_lower(std::string str) {
    std::transform(str.begin(), str.end(), str.begin(),
                   [](unsigned char c) { return char(std::tolower(c)); });

    return str;
}

// Builds a Move from coordinate notation using the position context (piece on
// the origin square, ep square) to select the special-move type and to derive
// or cross-check the forced promotion (spec 11). Geometry and legality are NOT
// fully validated here: the caller must vet the move (stage 4: against the
// generated move list).
Move to_move(const Position& pos, std::string str) {
    str = to_lower(str);

    if (str == "0000")
        return Move::null();

    usize i = 0;

    auto parse_square = [&](Square& out) {
        if (i >= str.size() || str[i] < 'a' || str[i] > 'p')
            return false;
        const File f = File(str[i++] - 'a');
        if (i >= str.size() || !isdigit(static_cast<unsigned char>(str[i])))
            return false;
        int r = str[i++] - '0';
        if (i < str.size() && isdigit(static_cast<unsigned char>(str[i])))
            r = r * 10 + (str[i++] - '0');
        if (r < 1 || r > 16)
            return false;
        out = make_square(f, Rank(r - 1));
        return true;
    };

    Square from, to;
    if (!parse_square(from) || !parse_square(to) || from == to)
        return Move::none();

    PieceType promo = NO_PIECE_TYPE;
    if (i < str.size())
    {
        promo = piece_type_from_char(str[i++]);
        if (promo == NO_PIECE_TYPE || i != str.size())
            return Move::none();
    }

    const Piece pc = pos.piece_on(from);
    if (pc == NO_PIECE || color_of(pc) != pos.side_to_move())
        return Move::none();

    const Color     us = color_of(pc);
    const PieceType pt = type_of(pc);

    // Arrival kinds that matter for the Troll promotion rule (spec 6.4)
    const bool pawnStep =
      int(to) - int(from) == int(pawn_push(us)) || bool(PawnAttacks[us][from] & to);

    // Cross-check an explicit promotion suffix against the forced table
    if (promo != NO_PIECE_TYPE
        && (relative_rank(us, to) != RANK_16 || promo != promoted_piece_type(pt)
            || (pt == TROLL && !pawnStep)))
        return Move::none();

    // En passant: a pawn capture landing on the ep square (spec 6.2)
    if (pt == PAWN && to == pos.ep_square() && bool(PawnAttacks[us][from] & to))
        return Move::make<EN_PASSANT>(from, to);

    // King jump: a distance-2 king move is only reachable that way (spec 6.3,
    // spec 11: distinguished by context)
    const int df = std::abs(file_of(to) - file_of(from));
    const int dr = std::abs(rank_of(to) - rank_of(from));
    if (pt == KING && std::max(df, dr) == 2)
        return Move::make<KING_JUMP>(from, to);

    // Derive the forced promotion when the suffix was omitted
    if (promo == NO_PIECE_TYPE && relative_rank(us, to) == RANK_16 && (pt != TROLL || pawnStep))
        promo = promoted_piece_type(pt);  // NO_PIECE_TYPE for non-promoting pieces

    return Move::make<NORMAL>(from, to, promo);
}

}  // namespace Stockfish::Notation
