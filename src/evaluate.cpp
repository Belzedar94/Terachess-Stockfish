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

#include "evaluate.h"

#include <algorithm>
#include <cassert>
#include <sstream>

#include "nnue/tera_features.h"
#include "nnue/tera_network.h"
#include "position.h"
#include "types.h"

namespace Stockfish {

namespace {

constexpr Value Tempo = 20;

// Material for all 26 piece types (spec 8 values). Position maintains the
// non-pawn material sums incrementally (KING counts as 0 there), so only the
// pawn term is added by hand.
Value material_imbalance(const Position& pos) {
    return pos.non_pawn_material(WHITE) - pos.non_pawn_material(BLACK)
         + PawnValue * (pos.count<PAWN>(WHITE) - pos.count<PAWN>(BLACK));
}

Value material_eval(const Position& pos) {
    Value mat = material_imbalance(pos);
    return (pos.side_to_move() == WHITE ? mat : -mat) + Tempo;
}

}  // namespace

// Keep the evaluation strictly inside the non-decisive score range.
Value Eval::clamp_to_eval_range(Value v) {
    return std::clamp(v, VALUE_TB_LOSS_IN_MAX_PLY + 1, VALUE_TB_WIN_IN_MAX_PLY - 1);
}

// Static evaluation, from the point of view of the side to move. With a
// network loaded this is the NNUE "S" output; the accumulator is rebuilt from
// scratch here, so this entry point stays valid for any caller. The search
// hot path uses its own incremental accumulator (Search::Worker::evaluate).
Value Eval::evaluate(const Position& pos) {

    assert(!pos.checkers());

    if (TeraNNUE::active())
        return clamp_to_eval_range(Value(TeraNNUE::evaluate_position(pos).cp));

    return clamp_to_eval_range(material_eval(pos));
}

// Debug helper for the 'eval' UCI command. The four canonical lines
// (psqt / positional / total_cp / bucket) are the parity-gate contract of
// docs/nnue-tera-s.md section 8 and are always emitted, network or not.
// All of them are exact integers from the side-to-move point of view; the
// engine clamps total_cp into the non-decisive range only inside search().
std::string Eval::trace(Position& pos) {

    std::ostringstream ss;

    if (TeraNNUE::active())
    {
        const auto e = TeraNNUE::evaluate_position(pos);

        ss << "nnue " << TeraNNUE::network().file() << '\n';
        ss << "psqt " << e.psqt << '\n';
        ss << "positional " << e.positional << '\n';
        ss << "total_cp " << e.cp << '\n';
        ss << "bucket " << e.bucket;
        return ss.str();
    }

    const Value mat = material_imbalance(pos);

    ss << "nnue none\n";
    ss << "material " << mat << "  (white side, internal units)\n";
    ss << "tempo " << Tempo << '\n';
    ss << "psqt 0\n";
    ss << "positional 0\n";
    ss << "total_cp " << material_eval(pos) << '\n';
    ss << "bucket " << TeraNNUE::output_bucket(pos);

    return ss.str();
}

}  // namespace Stockfish
