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

#ifndef NOTATION_H_INCLUDED
#define NOTATION_H_INCLUDED

#include <string>

#include "types.h"

namespace Stockfish {

class Position;

// Terachess II start position (TERACHESS_SPEC.md 3.2)
constexpr auto StartFEN =
  "sjyhxfdoodfxhyjs/cmztuvlkalvutzmc/ernbwigqqgiwbnre/pppppppppppppppp/16/16/16/16/16/16/16/16/"
  "PPPPPPPPPPPPPPPP/ERNBWIGQQGIWBNRE/CMZTUVLKALVUTZMC/SJYHXFDOODFXHYJS w Kk - 0 1";

// Coordinate notation (spec 11), shared by the UCI front end and the rules
// layer (Position::fen). This TU has no dependency on the engine/search/NNUE
// closure, and none on movegen either: to_move() builds the move from the
// position context; the caller is responsible for legality validation.
namespace Notation {

std::string square(Square s);
std::string move(Move m);
std::string to_lower(std::string str);
Move        to_move(const Position& pos, std::string str);

}  // namespace Notation

}  // namespace Stockfish

#endif  // #ifndef NOTATION_H_INCLUDED
