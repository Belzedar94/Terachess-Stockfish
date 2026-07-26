/*
  Terachess-Stockfish NNUE "S" — feature indexing (implementation).
  Contract: docs/nnue-tera-s.md sections 1-4.
*/

#include "tera_features.h"

#include <cassert>
#include <sstream>

#include "../bitboard.h"
#include "../position.h"

namespace Stockfish {
namespace TeraNNUE {

int output_bucket(const Position& pos) { return output_bucket(pos.count<ALL_PIECES>()); }

PerspectiveKey perspective_key(const Position& pos, Color persp) {
    const Square ksq    = pos.square<KING>(persp);
    const bool   mirror = mirror_for(persp, ksq);
    return {king_bucket_of_view(orient(persp, ksq, mirror)), mirror};
}

void active_features(const Position& pos, Color persp, FeatureList& out) {

    const PerspectiveKey key = perspective_key(pos, persp);

    out.count = 0;

    // pop_lsb() walks the board in ascending square order, which is exactly
    // the order tools/terannue/features.py emits.
    Bitboard occupied = pos.pieces();
    while (occupied)
    {
        const Square s = pop_lsb(occupied);
        assert(out.count < MaxActive);
        out.index[out.count++] = feature_index(persp, pos.piece_on(s), s, key.bucket, key.mirror);
    }
}

std::string trace_features(const Position& pos) {

    std::ostringstream ss;
    FeatureList        list;

    // Perspective 0 = side to move, perspective 1 = the other side.
    for (int p = 0; p < 2; ++p)
    {
        const Color c = p == 0 ? pos.side_to_move() : ~pos.side_to_move();
        const auto  k = perspective_key(pos, c);

        active_features(pos, c, list);

        ss << "persp " << p << " kbucket " << k.bucket << " count " << list.count << '\n';
        for (int i = 0; i < list.count; ++i)
            ss << (i ? " " : "") << list.index[i];
        ss << '\n';
    }

    std::string out = ss.str();
    if (!out.empty() && out.back() == '\n')
        out.pop_back();  // the caller appends its own line break
    return out;
}

}  // namespace TeraNNUE
}  // namespace Stockfish
