/*
  Terachess-Stockfish NNUE "S" — feature indexing.

  NORMATIVE SOURCE: docs/nnue-tera-s.md (frozen contract v1), sections 1-4.
  Every constant and every line of arithmetic here traces back to that
  document; the Python reference is tools/terannue/features.py and it must
  agree bit for bit (parity gate, contract section 8).

  Contract recap:
    * Two perspectives. Index 0 = side to move, index 1 = the other side.
    * View:   vsq = (c == WHITE) ? sq : sq ^ 0xF0
    * Mirror: conditioned on the perspective's OWN king. With vksq the view
              of that king's square, if file(vksq) < 8 then x ^= 0x0F is
              applied to EVERY square of that perspective, vksq included.
    * King buckets (post-mirror, r = vksq >> 4, f = vksq & 15):
              band_r = r < 2 ? 0 : r < 4 ? 1 : r < 8 ? 2 : 3
              band_f = f < 12 ? 0 : 1
              bucket = band_r * 2 + band_f
    * 51 planes: 0..24 own non-king pieces in engine type order
      (plane = type - PAWN), 25..49 the same classes of the other side,
      50 both kings.
    * feature = KING_BUCKET * 13056 + plane * 256 + vsq_mirrored
    * MaxActiveDimensions = 128.
    * Output bucket = min(7, (pieceCount - 1) / 16).
*/

#ifndef TERA_FEATURES_H_INCLUDED
#define TERA_FEATURES_H_INCLUDED

#include <cstdint>
#include <string>

#include "../types.h"

namespace Stockfish {

class Position;

namespace TeraNNUE {

// ---------------------------------------------------------------------------
// Frozen dimensions (contract sections 3, 4 and 7)
// ---------------------------------------------------------------------------

constexpr int KingBuckets       = 8;
constexpr int Planes            = 51;
constexpr int NumSquares        = 256;
constexpr int L1                = 256;   // accumulator width per perspective
constexpr int OutputBuckets     = 8;
constexpr int FeaturesPerBucket = Planes * NumSquares;              // 13056
constexpr int NumFeatures       = KingBuckets * FeaturesPerBucket;  // 104448
constexpr int MaxActive         = 128;
constexpr int KingPlane         = 50;

static_assert(FeaturesPerBucket == 13056, "contract section 3");
static_assert(NumFeatures == 104448, "contract section 3");

// Quantisation (contract section 5). Every division below truncates toward
// zero, which in C++ is exactly what operator/ does on signed integers.
constexpr int FtScale       = 128;
constexpr int FtActMax      = 127;   // clamp(acc, 0, 127)
constexpr int FtWeightMax   = 127;
constexpr int FtBiasMax     = 8191;
constexpr int PairwiseShift = 7;
constexpr int HiddenScale   = 64;    // i8 stack weights represent w/64
constexpr int FvScale       = 16;    // contract section 4 (2026-07-19 amendment)

// Stack shapes. The pairwise stage reduces 512 -> 256, so FC0_IN = 256 and
// fc0 is i8[256x16] (contract section 4, 2026-07-19 normative amendment).
constexpr int Fc0In  = L1;   // 256 pairwise products
constexpr int Fc0Out = 16;
constexpr int Fc1In  = Fc0Out;
constexpr int Fc1Out = 32;
constexpr int Fc2In  = Fc1Out;
constexpr int Fc2Out = 1;

// ---------------------------------------------------------------------------
// Section 1 — orientation
// ---------------------------------------------------------------------------

// Rank flip for Black so that the perspective's own side always "advances up".
constexpr int view_sq(Color persp, Square s) {
    return persp == WHITE ? int(s) : (int(s) ^ 0xF0);
}

// The mirror is decided by the perspective's own king only.
constexpr bool mirror_for(Color persp, Square kingSq) {
    return (view_sq(persp, kingSq) & 15) < 8;
}

constexpr int orient(Color persp, Square s, bool mirror) {
    const int v = view_sq(persp, s);
    return mirror ? (v ^ 0x0F) : v;
}

// ---------------------------------------------------------------------------
// Section 2 — king buckets
// ---------------------------------------------------------------------------

// vksq MUST already be viewed and mirrored (file in [8, 15]).
constexpr int king_bucket_of_view(int vksq) {
    const int r     = vksq >> 4;
    const int f     = vksq & 15;
    const int bandR = r < 2 ? 0 : r < 4 ? 1 : r < 8 ? 2 : 3;
    const int bandF = f < 12 ? 0 : 1;
    return bandR * 2 + bandF;
}

constexpr int king_bucket(Color persp, Square kingSq) {
    return king_bucket_of_view(orient(persp, kingSq, mirror_for(persp, kingSq)));
}

// ---------------------------------------------------------------------------
// Section 3 — planes and the feature index
// ---------------------------------------------------------------------------

constexpr int plane_of(Color persp, Piece pc) {
    const PieceType pt = type_of(pc);
    if (pt == KING)
        return KingPlane;  // both kings share plane 50; the square tells them apart
    return (Color(pc >> 5) == persp ? 0 : 25) + (int(pt) - int(PAWN));
}

// Fast path: the caller already knows the perspective's bucket and mirror bit.
constexpr int feature_index(Color persp, Piece pc, Square s, int kingBucket, bool mirror) {
    return kingBucket * FeaturesPerBucket + plane_of(persp, pc) * NumSquares
         + orient(persp, s, mirror);
}

// Contract signature: kingSq is the RAW (unoriented) square of the
// perspective's own king.
constexpr int feature_index(Color persp, Piece pc, Square s, Square kingSq) {
    const bool m = mirror_for(persp, kingSq);
    return feature_index(persp, pc, s, king_bucket_of_view(orient(persp, kingSq, m)), m);
}

// ---------------------------------------------------------------------------
// Section 4 — output buckets
// ---------------------------------------------------------------------------

constexpr int output_bucket(int pieceCount) {
    const int b = (pieceCount - 1) / 16;
    return b < OutputBuckets - 1 ? b : OutputBuckets - 1;
}

int output_bucket(const Position& pos);

// ---------------------------------------------------------------------------
// Active feature list of a Position
// ---------------------------------------------------------------------------

// Everything one perspective needs, derived from its own king square.
struct PerspectiveKey {
    int  bucket;
    bool mirror;
};

PerspectiveKey perspective_key(const Position& pos, Color persp);

struct FeatureList {
    int count = 0;
    int index[MaxActive];

    const int* begin() const { return index; }
    const int* end() const { return index + count; }
};

// Active rows of one perspective, emitted in ASCENDING SQUARE order — the
// same order as tools/terannue/features.py real_indices(), so the engine's
// `features` command diffs line-for-line against parity_harness.py.
void active_features(const Position& pos, Color persp, FeatureList& out);

// Debug dump for the parity gate (`features` UCI command).
std::string trace_features(const Position& pos);

}  // namespace TeraNNUE
}  // namespace Stockfish

#endif  // #ifndef TERA_FEATURES_H_INCLUDED
