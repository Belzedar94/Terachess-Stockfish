/*
  Terachess-Stockfish NNUE "S" — feature-transformer accumulator.

  Contract: docs/nnue-tera-s.md sections 4, 5 and 9.

    * i16[2][256] per position, one row per perspective COLOR (the ordering
      "0 = side to move" is applied at forward time, not here: the stored
      accumulator is colour-indexed so a null move needs no work at all).
    * PSQT rides along as i32[2][8] straight out of the FT (contract 4).
    * refresh() is the ORACLE: it rebuilds a perspective from the position.
    * update() is the incremental path: <= 3 deltas per move, with a full
      refresh of one perspective only when its own king changed king bucket
      or changed side of the mirror (contract 9).
*/

#ifndef TERA_ACCUMULATOR_H_INCLUDED
#define TERA_ACCUMULATOR_H_INCLUDED

#include <cstdint>
#include <memory>
#include <string>

#include "../types.h"
#include "tera_features.h"

namespace Stockfish {

class Position;

namespace TeraNNUE {

class Network;

struct alignas(64) Accumulator {
    std::int16_t acc[COLOR_NB][L1];
    std::int32_t psqt[COLOR_NB][OutputBuckets];
    int          kingBucket[COLOR_NB];
    bool         mirror[COLOR_NB];

    bool operator==(const Accumulator& o) const;
    bool operator!=(const Accumulator& o) const { return !(*this == o); }
};

// Rebuild one perspective (or both) from scratch. This is the reference the
// incremental path is validated against.
void refresh_side(const Network& net, const Position& pos, Accumulator& a, Color persp);
void refresh(const Network& net, const Position& pos, Accumulator& a);

// Incremental update. `posAfter` is the position AFTER the move, `dp` the
// DirtyPiece do_move() produced for it. `prev` and `next` must be distinct
// objects. Returns how many perspectives had to fall back to a full refresh
// (0, 1 or 2).
int update(const Network&    net,
           const Accumulator& prev,
           Accumulator&       next,
           const Position&    posAfter,
           const DirtyPiece&  dp);

// Per-thread stack of accumulators, pushed/popped alongside do_move/undo_move.
class AccumulatorStack {
   public:
    static constexpr int Capacity = MAX_PLY + 64;

    AccumulatorStack();

    void reset(const Network& net, const Position& pos);
    // Returns how many perspectives needed a full refresh (0, 1 or 2).
    int  push(const Network& net, const Position& posAfter, const DirtyPiece& dp);
    void pop();

    const Accumulator& top() const { return entries[cursor]; }
    int                depth() const { return cursor; }

   private:
    std::unique_ptr<Accumulator[]> entries;
    int                            cursor = 0;
};

// Self-check for the incremental path (UCI `nnuecheck` command): plays a
// random legal game from `fen` (empty = the start position) and compares
// update() against refresh() after every ply.
std::string
selfcheck_random_game(const Network& net, const std::string& fen, int plies, u64 seed);

}  // namespace TeraNNUE
}  // namespace Stockfish

#endif  // #ifndef TERA_ACCUMULATOR_H_INCLUDED
