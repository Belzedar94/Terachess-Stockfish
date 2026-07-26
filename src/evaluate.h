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

#ifndef EVALUATE_H_INCLUDED
#define EVALUATE_H_INCLUDED

#include <string>

#include "types.h"

namespace Stockfish {

class Position;

// Evaluation. With a TNN1 network loaded (UCI option EvalFile, and UseNNUE
// enabled) this is the NNUE "S" of docs/nnue-tera-s.md; otherwise it falls
// back to the F1 material evaluation over the 26 Terachess piece types
// (spec 8 values, stored in PieceTypeValue) plus a 20cp tempo bonus.
namespace Eval {

Value evaluate(const Position& pos);

// Keeps a static evaluation inside the non-decisive score range.
Value clamp_to_eval_range(Value v);

std::string trace(Position& pos);

}  // namespace Eval

}  // namespace Stockfish

#endif  // #ifndef EVALUATE_H_INCLUDED
