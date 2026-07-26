/*
  Terachess-Stockfish NNUE "S" — accumulator (implementation).
  Contract: docs/nnue-tera-s.md sections 4, 5 and 9.
*/

#include "tera_accumulator.h"

#include <cassert>
#include <cstring>
#include <deque>
#include <sstream>
#include <vector>

#include "../bitboard.h"
#include "../misc.h"
#include "../movegen.h"
#include "../notation.h"
#include "../position.h"
#include "tera_network.h"

namespace Stockfish {
namespace TeraNNUE {

namespace {

// One (piece, square) toggle of the sparse input.
struct Delta {
    Piece  pc;
    Square sq;
    int    sign;  // +1 add, -1 remove
};

// A move touches at most 3 (piece, square) pairs: the mover leaves `from`,
// the victim leaves `capsq`, and either the mover lands on `to` or the
// promoted piece appears there (contract section 9: "<= 3 deltas").
int collect_deltas(const DirtyPiece& dp, Delta* out) {
    int n = 0;

    out[n++] = {dp.pc, dp.from, -1};

    if (dp.to != SQ_NONE)
        out[n++] = {dp.pc, dp.to, +1};

    if (dp.remove_sq != SQ_NONE)
        out[n++] = {dp.remove_pc, dp.remove_sq, -1};

    if (dp.add_sq != SQ_NONE)
        out[n++] = {dp.add_pc, dp.add_sq, +1};

    assert(n <= 3);
    return n;
}

void add_row(const Network& net, Accumulator& a, Color c, int feature) {
    const std::int16_t* w = net.ft_row(feature);
    for (int k = 0; k < L1; ++k)
        a.acc[c][k] = std::int16_t(a.acc[c][k] + w[k]);

    const std::int32_t* p = net.psqt_row(feature);
    for (int b = 0; b < OutputBuckets; ++b)
        a.psqt[c][b] += p[b];
}

void sub_row(const Network& net, Accumulator& a, Color c, int feature) {
    const std::int16_t* w = net.ft_row(feature);
    for (int k = 0; k < L1; ++k)
        a.acc[c][k] = std::int16_t(a.acc[c][k] - w[k]);

    const std::int32_t* p = net.psqt_row(feature);
    for (int b = 0; b < OutputBuckets; ++b)
        a.psqt[c][b] -= p[b];
}

}  // namespace

bool Accumulator::operator==(const Accumulator& o) const {
    for (Color c : {WHITE, BLACK})
    {
        if (kingBucket[c] != o.kingBucket[c] || mirror[c] != o.mirror[c])
            return false;
        if (std::memcmp(acc[c], o.acc[c], sizeof(acc[c])) != 0)
            return false;
        if (std::memcmp(psqt[c], o.psqt[c], sizeof(psqt[c])) != 0)
            return false;
    }
    return true;
}

// The ORACLE: rebuild one perspective from the position alone.
void refresh_side(const Network& net, const Position& pos, Accumulator& a, Color persp) {

    const PerspectiveKey key = perspective_key(pos, persp);

    a.kingBucket[persp] = key.bucket;
    a.mirror[persp]     = key.mirror;

    const std::int16_t* bias = net.ft_bias();
    for (int k = 0; k < L1; ++k)
        a.acc[persp][k] = bias[k];
    for (int b = 0; b < OutputBuckets; ++b)
        a.psqt[persp][b] = 0;

    Bitboard occupied = pos.pieces();
    while (occupied)
    {
        const Square s = pop_lsb(occupied);
        add_row(net, a, persp,
                feature_index(persp, pos.piece_on(s), s, key.bucket, key.mirror));
    }
}

void refresh(const Network& net, const Position& pos, Accumulator& a) {
    refresh_side(net, pos, a, WHITE);
    refresh_side(net, pos, a, BLACK);
}

int update(const Network&     net,
           const Accumulator& prev,
           Accumulator&       next,
           const Position&    posAfter,
           const DirtyPiece&  dp) {

    Delta     deltas[4];
    const int n           = collect_deltas(dp, deltas);
    int       refreshCount = 0;

    for (Color c : {WHITE, BLACK})
    {
        const PerspectiveKey key = perspective_key(posAfter, c);

        // A king that leaves its bucket (or crosses the mirror axis) rewrites
        // every feature of its own perspective: refresh instead of patching.
        if (key.bucket != prev.kingBucket[c] || key.mirror != prev.mirror[c])
        {
            refresh_side(net, posAfter, next, c);
            ++refreshCount;
            continue;
        }

        next.kingBucket[c] = key.bucket;
        next.mirror[c]     = key.mirror;
        std::memcpy(next.acc[c], prev.acc[c], sizeof(next.acc[c]));
        std::memcpy(next.psqt[c], prev.psqt[c], sizeof(next.psqt[c]));

        for (int i = 0; i < n; ++i)
        {
            const int f =
              feature_index(c, deltas[i].pc, deltas[i].sq, key.bucket, key.mirror);
            if (deltas[i].sign > 0)
                add_row(net, next, c, f);
            else
                sub_row(net, next, c, f);
        }
    }

    return refreshCount;
}

// ---------------------------------------------------------------------------
// Accumulator stack
// ---------------------------------------------------------------------------

AccumulatorStack::AccumulatorStack() :
    entries(new Accumulator[Capacity]) {}

void AccumulatorStack::reset(const Network& net, const Position& pos) {
    cursor = 0;
    refresh(net, pos, entries[0]);
}

int AccumulatorStack::push(const Network& net, const Position& posAfter, const DirtyPiece& dp) {
    assert(cursor + 1 < Capacity);
    if (cursor + 1 >= Capacity)
    {
        // Defensive: never write out of bounds. Recompute in place instead.
        refresh(net, posAfter, entries[cursor]);
        return 2;
    }
    const int forced = update(net, entries[cursor], entries[cursor + 1], posAfter, dp);
    ++cursor;
    return forced;
}

void AccumulatorStack::pop() {
    assert(cursor > 0);
    if (cursor > 0)
        --cursor;
}

// ---------------------------------------------------------------------------
// Self-check: refresh (oracle) vs update (incremental) over a random game
// ---------------------------------------------------------------------------

std::string
selfcheck_random_game(const Network& net, const std::string& fen, int plies, u64 seed) {

    std::ostringstream ss;

    if (!net.loaded())
        return "nnuecheck: no network loaded (set EvalFile first)";

    if (plies < 1)
        plies = 1;

    std::deque<StateInfo> states(1);
    Position              pos;

    if (pos.set(fen.empty() ? std::string(StartFEN) : fen, &states.back()))
        return "nnuecheck: could not set the position";

    // The whole chain lives on the heap (an Accumulator is ~1 KiB, and a game
    // is not bounded by MAX_PLY the way the search stack is).
    std::vector<Accumulator>     chain(usize(plies) + 1);
    std::unique_ptr<Accumulator> oracle(new Accumulator);
    refresh(net, pos, chain[0]);

    // In parallel, drive the very AccumulatorStack the search uses, for as
    // long as its search-sized capacity allows.
    AccumulatorStack stack;
    stack.reset(net, pos);
    bool stackLive = true;

    PRNG              rng(seed ? seed : 1);
    std::vector<Move> played;
    int done = 0, diffs = 0, forced = 0, stackPlies = 0, stackDiffs = 0, popDiffs = 0;

    for (int i = 0; i < plies; ++i)
    {
        MoveList<LEGAL> legal(pos);
        if (legal.size() == 0)
            break;

        const Move m = *(legal.begin() + rng.rand<u64>() % legal.size());

        states.emplace_back();
        DirtyPiece dp;
        pos.do_move(m, states.back(), pos.gives_check(m), dp, nullptr);
        played.push_back(m);

        forced += update(net, chain[i], chain[i + 1], pos, dp);
        refresh(net, pos, *oracle);
        if (chain[i + 1] != *oracle)
            ++diffs;

        if (stackLive && stack.depth() + 2 < AccumulatorStack::Capacity)
        {
            stack.push(net, pos, dp);
            ++stackPlies;
            if (stack.top() != *oracle)
                ++stackDiffs;
        }
        else
            stackLive = false;

        ++done;
    }

    // Unwind the search stack: pop() must expose exactly the accumulator of
    // the position we return to, so the stack is a faithful LIFO and not just
    // a running sum.
    for (int i = stackPlies; i-- > 0;)
    {
        stack.pop();
        if (stack.top() != chain[i])
            ++popDiffs;
    }

    const bool pass = diffs == 0 && stackDiffs == 0 && popDiffs == 0;

    ss << "plies " << done << '\n'
       << "diffs " << diffs << '\n'
       << "stack_plies " << stackPlies << '\n'
       << "stack_diffs " << stackDiffs << '\n'
       << "undo_diffs " << popDiffs << '\n'
       << "forced_refreshes " << forced << '\n'
       << "result " << (pass ? "PASS" : "FAIL");

    return ss.str();
}

}  // namespace TeraNNUE
}  // namespace Stockfish
