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

#include "search.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <iostream>
#include <list>
#include <ratio>
#include <string>
#include <utility>

#ifdef TERA_LMP_TRACE
    #include <optional>
    #include <vector>
#endif

#include "bitboard.h"
#include "evaluate.h"
#include "history.h"
#include "misc.h"
#include "movegen.h"
#include "movepick.h"
#include "nnue/tera_network.h"
#include "position.h"
#include "tera_lmp_shadow.h"
#include "tera_lmp_trace.h"
#include "thread.h"
#include "timeman.h"
#include "tt.h"
#include "types.h"
#include "uci.h"
#include "ucioption.h"

namespace Stockfish {

static constexpr std::array<int, 16> lmrDivisor = {3307, 2930, 2874, 2818, 3215, 3225, 3224, 2782,
                                                   2858, 2919, 3088, 3275, 3180, 2868, 3006, 3599};

using namespace Search;

namespace {

constexpr u64 NODES_LIMIT_OUTPUT = 10'000'000;

constexpr int SEARCHEDLIST_CAPACITY = 32;
using SearchedList                  = ValueList<Move, SEARCHEDLIST_CAPACITY>;

// (*Scalers):
// The values with Scaler asterisks have proven non-linear scaling.
// They are optimized to time controls of 180 + 1.8 and longer,
// so changing them or adding conditions that are similar requires
// tests at these types of time controls.

// (*Scaler) All tuned parameters at time controls shorter than
// optimized for require verifications at longer time controls

int correction_value(const Worker& w, const Position& pos, const Stack* const ss) {
    const Color us     = pos.side_to_move();
    const auto  m      = (ss - 1)->currentMove;
    const auto& shared = w.sharedHistory;
    const int   pcv    = shared.pawn_correction_entry(pos)[us].pawn;
    const int   micv   = shared.minor_piece_correction_entry(pos)[us].minor;
    const int   wnpcv  = shared.nonpawn_correction_entry<WHITE>(pos)[us].nonPawnWhite;
    const int   bnpcv  = shared.nonpawn_correction_entry<BLACK>(pos)[us].nonPawnBlack;
    const int   cntcv =
      m.is_ok()
          ? 8363
            * ((*(ss - 2)->continuationCorrectionHistory)[piece_slot(pos.piece_on(m.to_sq()))]
                                                         [m.to_sq()]
               + (*(ss - 4)->continuationCorrectionHistory)[piece_slot(pos.piece_on(m.to_sq()))]
                                                           [m.to_sq()])
          : 64549;

    return 13345 * pcv + 9280 * micv + 11840 * (wnpcv + bnpcv) + cntcv;
}

// Add correctionHistory value to raw staticEval and guarantee evaluation
// does not hit the tablebase range.
Value to_corrected_static_eval(const Value v, const int cv) {
    return std::clamp(v + cv / 131072, VALUE_TB_LOSS_IN_MAX_PLY + 1, VALUE_TB_WIN_IN_MAX_PLY - 1);
}

void update_correction_history(const Position& pos,
                               Stack* const    ss,
                               Search::Worker& workerThread,
                               const int       bonus) {
    const Move  m  = (ss - 1)->currentMove;
    const Color us = pos.side_to_move();

    constexpr int nonPawnWeight = 186;
    auto&         shared        = workerThread.sharedHistory;

    shared.pawn_correction_entry(pos)[us].pawn << bonus;
    shared.minor_piece_correction_entry(pos)[us].minor << bonus * 152 / 128;
    shared.nonpawn_correction_entry<WHITE>(pos)[us].nonPawnWhite << bonus * nonPawnWeight / 128;
    shared.nonpawn_correction_entry<BLACK>(pos)[us].nonPawnBlack << bonus * nonPawnWeight / 128;

    if (m.is_ok())
    {
        const Square to = m.to_sq();
        const int    ps = piece_slot(pos.piece_on(to));
        (*(ss - 2)->continuationCorrectionHistory)[ps][to] << bonus * 136 / 128;
        (*(ss - 4)->continuationCorrectionHistory)[ps][to] << bonus * 68 / 128;
    }
}

// Add a small random component to draw evaluations to avoid 3-fold blindness
Value value_draw(usize nodes) { return VALUE_DRAW - 1 + Value(nodes & 0x2); }
Value value_to_tt(Value v, int ply);
Value value_from_tt(Value v, int ply, int r50c);
void  update_continuation_histories(Stack* ss, Piece pc, Square to, int bonus);
void  update_quiet_histories(
   const Position& pos, Stack* ss, Search::Worker& workerThread, Move move, int bonus);
void update_all_stats(const Position& pos,
                      Stack*          ss,
                      Search::Worker& workerThread,
                      Move            bestMove,
                      Square          prevSq,
                      SearchedList&   quietsSearched,
                      SearchedList&   capturesSearched,
                      Depth           depth,
                      Move            ttMove,
                      bool            PvNode);

// Detect shuffling moves in order to limit search explosions
// Added in #6447 as non-regression, and so its parameters should not be tuned
bool is_shuffling(Move move, Stack* const ss, const Position& pos) {
    if (pos.capture_stage(move) || pos.rule50_count() < 10)
        return false;
    if (pos.state()->pliesFromNull < 6 || ss->ply < 20)
        return false;
    return move.from_sq() == (ss - 2)->currentMove.to_sq()
        && (ss - 2)->currentMove.from_sq() == (ss - 4)->currentMove.to_sq();
}

}  // namespace

Search::Worker::Worker(SharedState&                    sharedState,
                       std::unique_ptr<ISearchManager> sm,
                       usize                           threadId,
                       usize                           numaThreadId,
                       usize                           numaTotalThreads,
                       NumaReplicatedAccessToken       token) :
    // Unpack the SharedState struct into member variables
    sharedHistory(sharedState.sharedHistories.at(token.get_numa_index())),
    continuationHistory(sharedHistory.continuationHistory),
    threadIdx(threadId),
    numaThreadIdx(numaThreadId),
    numaTotal(numaTotalThreads),
    numaAccessToken(token),
    manager(std::move(sm)),
    options(sharedState.options),
    threads(sharedState.threads),
    tt(sharedState.tt) {
    // Raw new[]: ExtMove/Move are trivially constructible, so the arena
    // pages stay untouched (hence uncommitted) until a ply first uses them
    // Sized for the worst nesting: one picker per ply plus singular
    // re-entries and the ProbCut picker (all LIFO); pages commit on touch
    movesArena.reset(new ExtMove[usize(4) * (MAX_PLY + 10) * MAX_MOVES]);
    movesArenaTop = movesArena.get();
    genScratch.reset(new Move[MAX_MOVES]);
    clear();
}

void Search::Worker::start_searching() {

    // Non-main threads go directly to iterative_deepening()
    if (!is_mainthread())
    {
        iterative_deepening();
        return;
    }

    main_manager()->tm.init(limits, rootPos.side_to_move(), rootPos.game_ply(), options,
                            main_manager()->originalTimeAdjust);
    tt.new_search();

    if (rootMoves.empty())
    {
        rootMoves.emplace_back(Move::none());
        main_manager()->updates.onUpdateNoMoves(
          {0, {rootPos.checkers() ? -VALUE_MATE : VALUE_DRAW, rootPos}});
        main_manager()->updates.onBestmove(UCIEngine::move(Move::none()), "");
        return;
    }

    // Main thread starts non-main threads, and begins own search.
    threads.start_searching();
    bool uciPvSent = iterative_deepening();

    // When we reach the maximum depth, we can arrive here without a raise of
    // threads.stop. However, if we are pondering or in an infinite search,
    // the UCI protocol states that we shouldn't print the best move before the
    // GUI sends a "stop" or "ponderhit" command. We therefore simply wait here
    // until the GUI sends one of those commands.
    while (!threads.stop && (main_manager()->ponder || limits.infinite))
    {}  // Busy wait for a stop or a ponder reset

    // Stop the threads if not already stopped (also raise the stop if
    // "ponderhit" just reset threads.ponder)
    threads.stop = true;

    // Wait until all threads have finished
    threads.wait_for_search_finished();

    // When playing in 'nodes as time' mode, subtract the searched nodes from
    // the available ones before exiting.
    if (limits.npmsec)
        main_manager()->tm.advance_nodes_time(threads.nodes_searched()
                                              - limits.inc[rootPos.side_to_move()]);

    Worker* bestThread = this;
    Skill   skill =
      Skill(options["Skill Level"], options["UCI_LimitStrength"] ? int(options["UCI_Elo"]) : 0);

    if (!limits.depth && !skill.enabled())
        bestThread = threads.get_best_thread()->worker.get();

    main_manager()->bestPreviousScore        = bestThread->rootMoves[0].score;
    main_manager()->bestPreviousAverageScore = bestThread->rootMoves[0].averageScore;

    if (bestThread->rootMoves[0].pv.size() == 1
        && bestThread->rootMoves[0].extract_ponder_from_tt(tt, rootPos))
        uciPvSent = false;

    // Send PV info if it has changed since last output in iterative_deepening().
    if (!uciPvSent || bestThread != this)
        main_manager()->output_pv(*bestThread, threads, tt, bestThread->rootDepth);

    std::string ponder;
    if (bestThread->rootMoves[0].pv.size() > 1)
        ponder = UCIEngine::move(bestThread->rootMoves[0].pv[1]);

    auto bestmove = UCIEngine::move(bestThread->rootMoves[0].pv[0]);
    main_manager()->updates.onBestmove(bestmove, ponder);
}

// Main iterative deepening loop. It calls search()
// repeatedly with increasing depth until the allocated thinking time has been
// consumed, the user stops the search, or the maximum search depth is reached.
bool Search::Worker::iterative_deepening() {

    nnue_start_search();
#ifdef TERA_LMP_TRACE
    TeraLmpTrace::set_root(u64(rootPos.key()), rootPos.fen());
#endif

    SearchManager* mainThread = (is_mainthread() ? main_manager() : nullptr);

    PVMoves pv;

    PVMoves lastBestMovePV;
    Depth   lastBestMoveDepth = 0;
    Value   lastBestMoveScore = -VALUE_INFINITE;

    Value  alpha, beta;
    Value  bestValue     = -VALUE_INFINITE;
    Color  us            = rootPos.side_to_move();
    double timeReduction = 1, totBestMoveChanges = 0;
    int    delta, iterIdx                        = 0;

    // Allocate stack with extra size to allow access from (ss - 7) to (ss + 2):
    // (ss - 7) is needed for update_continuation_histories(ss - 1) which accesses (ss - 6),
    // (ss + 2) is needed for initialization of cutOffCnt.
    Stack  stack[MAX_PLY + 10] = {};
    Stack* ss                  = stack + 7;

    for (int i = 7; i > 0; --i)
    {
        (ss - i)->continuationHistory =
          &continuationHistory[0][0][NO_PIECE][0];  // Use as a sentinel
        (ss - i)->continuationCorrectionHistory = &continuationCorrectionHistory[NO_PIECE][0];
        (ss - i)->staticEval                    = VALUE_NONE;
    }

    for (int i = 0; i <= MAX_PLY + 2; ++i)
        (ss + i)->ply = i;

    ss->pv = &pv;

    if (mainThread)
    {
        if (mainThread->bestPreviousScore == VALUE_INFINITE)
            mainThread->iterValue.fill(VALUE_ZERO);
        else
            mainThread->iterValue.fill(mainThread->bestPreviousScore);
    }

    usize multiPV = usize(options["MultiPV"]);
    Skill skill(options["Skill Level"], options["UCI_LimitStrength"] ? int(options["UCI_Elo"]) : 0);

    // When playing with strength handicap enable MultiPV search that we will
    // use behind-the-scenes to retrieve a set of possible moves.
    if (skill.enabled())
        multiPV = std::max(multiPV, usize(4));

    multiPV = std::min(multiPV, rootMoves.size());

    int  searchAgainCounter = 0;
    bool uciPvSent          = false;

    lowPlyHistory.fill(100);

    for (Color c : {WHITE, BLACK})
        for (int i = 0; i < UINT_16_HISTORY_SIZE; i++)
            mainHistory[c][i] = mainHistory[c][i] * 789 / 1024;

    // Iterative deepening loop until requested to stop or the target depth is reached
    while (rootDepth + 1 < MAX_PLY && !threads.stop
           && !(limits.depth && mainThread && rootDepth >= limits.depth))
    {
        rootDepth++;

        // Age out PV variability metric and signal the start of a new iteration.
        if (mainThread)
        {
            totBestMoveChanges /= 2;
            uciPvSent = false;
        }

        // Save the last iteration's scores before the first PV line is searched and
        // all the move scores except the (new) PV are set to -VALUE_INFINITE.
        for (usize i = 0; i < rootMoves.size(); ++i)
        {
            rootMoves[i].previousScore      = rootMoves[i].score;
            rootMoves[i].previousPV         = rootMoves[i].pv;
            rootMoves[i].previousScoreExact = i < multiPV;
        }

        usize pvFirst = pvLast = 0;

        if (!threads.increaseDepth)
            searchAgainCounter++;

        // MultiPV loop. We perform a full root search for each PV line
        for (pvIdx = 0; pvIdx < multiPV; ++pvIdx)
        {
            if (pvIdx == pvLast)
            {
                pvFirst = pvLast;
                for (pvLast++; pvLast < rootMoves.size(); pvLast++)
                    if (rootMoves[pvLast].tbRank != rootMoves[pvFirst].tbRank)
                        break;
            }

            // Reset UCI info selDepth for each depth and each PV line
            selDepth = 0;

            // Reset aspiration window starting size
            delta = 5 + threadIdx % 8 + std::abs(rootMoves[pvIdx].meanSquaredScore) / 10588;
            Value avg = rootMoves[pvIdx].averageScore;
            alpha     = std::max(avg - delta, -VALUE_INFINITE);
            beta      = std::min(avg + delta, VALUE_INFINITE);

            // Adjust optimism based on root move's averageScore
            optimism[us]  = 137 * avg / (std::abs(avg) + 81);
            optimism[~us] = -optimism[us];

            // Start with a small aspiration window and, in the case of a fail
            // high/low, re-search with a bigger window until we don't fail
            // high/low anymore.
            int failedHighCnt = 0;
            while (true)
            {
                // Adjust the effective depth searched, but ensure at least one
                // effective increment for every four searchAgain steps (see issue #2717).
                Depth adjustedDepth =
                  std::max(1, rootDepth - failedHighCnt - 3 * (searchAgainCounter + 1) / 4);
                rootDelta = beta - alpha;
                bestValue = search<Root>(rootPos, ss, alpha, beta, adjustedDepth, false);

                // Bring the best move to the front. It is critical that sorting
                // is done with a stable algorithm because all the values but the
                // first and eventually the new best one is set to -VALUE_INFINITE
                // and we want to keep the same order for all the moves except the
                // new PV that goes to the front. Note that in the case of MultiPV
                // search the already searched PV lines are preserved.
                std::stable_sort(rootMoves.begin() + pvIdx, rootMoves.begin() + pvLast);

                // If search has been stopped, we break immediately. Sorting is
                // safe because RootMoves is still valid, although it refers to
                // the previous iteration.
                if (threads.stop)
                    break;

                // When failing high/low give some update before a re-search. To avoid
                // excessive output that could hang GUIs like Fritz 19, only start
                // at nodes > 10M (rather than depth N, which can be reached quickly)
                if (mainThread && multiPV == 1 && (bestValue <= alpha || bestValue >= beta)
                    && nodes > NODES_LIMIT_OUTPUT)
                    main_manager()->output_pv(*this, threads, tt, rootDepth);

                // In case of failing low/high increase aspiration window and re-search,
                // otherwise exit the loop.
                if (bestValue <= alpha)
                {
                    beta  = alpha;
                    alpha = std::max(bestValue - delta, -VALUE_INFINITE);

                    failedHighCnt = 0;
                    if (mainThread)
                        mainThread->stopOnPonderhit = false;
                }
                else if (bestValue >= beta)
                {
                    alpha = std::max(beta - delta, alpha);
                    beta  = std::min(bestValue + delta, VALUE_INFINITE);
                    ++failedHighCnt;
                }
                else
                    break;

                delta += 44 * delta / 128;

                assert(alpha >= -VALUE_INFINITE && beta <= VALUE_INFINITE);
            }

            if (threads.stop && pvIdx)
            {
                // In multiPV analysis we do not let aborted searches spoil mated-in/
                // TB loss scores from a completed search in an earlier PV line.
                // Hence we guard against an aborted pvIdx line overtaking pvIdx - 1
                // when pvIdx - 1 is a proven loss.
                // Moreover, we do not trust an exact loss score from an aborted search.
                if ((is_loss(rootMoves[pvIdx - 1].score) && rootMoves[pvIdx] < rootMoves[pvIdx - 1])
                    || rootMoves[pvIdx].score_is_exact_loss())
                {
                    // If previousScore is exact and worse than pvIdx - 1, we can safely use it.
                    // If it is equal, we make sure it cannot overtake pvIdx - 1.
                    if (rootMoves[pvIdx].previousScore != -VALUE_INFINITE
                        && rootMoves[pvIdx].previousScoreExact
                        && rootMoves[pvIdx].previousScore <= rootMoves[pvIdx - 1].score)
                    {
                        rootMoves[pvIdx].score = rootMoves[pvIdx].uciScore =
                          rootMoves[pvIdx].previousScore;
                        rootMoves[pvIdx].previousScore = -VALUE_INFINITE;
                        rootMoves[pvIdx].pv            = rootMoves[pvIdx].previousPV;
                        rootMoves[pvIdx].unset_bound_flags();
                    }

                    // Otherwise, if we can, we cap the score to the best possible, and mark
                    // the score as a bound (also a valid excuse for the incomplete PV.)
                    else
                    {
                        if (is_loss(rootMoves[pvIdx - 1].score))
                        {
                            rootMoves[pvIdx].score = rootMoves[pvIdx].uciScore =
                              rootMoves[pvIdx - 1].score;
                            rootMoves[pvIdx].previousScore = -VALUE_INFINITE;
                            rootMoves[pvIdx].pv.resize(1);
                            rootMoves[pvIdx].scoreUpperbound = true;
                        }
                        else
                            rootMoves[pvIdx].scoreUpperbound = false;

                        rootMoves[pvIdx].scoreLowerbound = !rootMoves[pvIdx].scoreUpperbound;
                    }
                }

                // Finally, we mark all loss scores from partially searched moves as a bound.
                for (usize i = pvIdx + 1; i < multiPV; ++i)
                    if (rootMoves[i].score_is_exact_loss())
                        rootMoves[i].scoreLowerbound = true;
            }

            // Sort the PV lines searched so far and update the GUI
            std::stable_sort(rootMoves.begin() + pvFirst, rootMoves.begin() + pvIdx + 1);

            if (mainThread && !threads.stop && (pvIdx + 1 == multiPV || nodes > NODES_LIMIT_OUTPUT))
            {
                main_manager()->output_pv(*this, threads, tt, rootDepth);
                uciPvSent = (pvIdx + 1 == multiPV);
            }

            if (threads.stop)
                break;
        }

        const bool forgottenMate = lastBestMoveScore != -VALUE_INFINITE
                                && is_mate_or_mated(lastBestMoveScore)
                                && (std::abs(rootMoves[0].score) < std::abs(lastBestMoveScore)
                                    || rootMoves[0].score_is_bound());

        if (!threads.stop)
        {
            if (lastBestMovePV.empty() || lastBestMovePV[0] != rootMoves[0].pv[0])
                lastBestMoveDepth = rootDepth;

            // Do not replace (shorter) mate scores from a previous iteration.
            if (!forgottenMate)
            {
                lastBestMovePV    = rootMoves[0].pv;
                lastBestMoveScore = rootMoves[0].score;
            }
        }

        const bool abortedLossSearch = threads.stop && !pvIdx && rootMoves[0].score_is_exact_loss();

        // An exact mated-in/TB-loss score from an aborted search cannot be trusted: the
        // loss could be delayed or refuted upon exploring the remaining root-moves.
        // Thus here we roll back to the score from the previous iteration.
        // We do the same if a search has failed to recover a mate score that was found
        // in a previous iteration.
        if (abortedLossSearch || (rootMoves[0].score != -VALUE_INFINITE && forgottenMate))
        {
            // Bring the last best move to the front for best thread selection.
            if (!lastBestMovePV.empty())
            {
                Utility::move_to_front(rootMoves, [&lastPV = std::as_const(lastBestMovePV)](
                                                    const auto& rm) { return rm == lastPV[0]; });
                rootMoves[0].score = rootMoves[0].uciScore = lastBestMoveScore;
                rootMoves[0].pv                            = lastBestMovePV;
                rootMoves[0].unset_bound_flags();

                if (mainThread)
                    uciPvSent = false;
            }
            // For an aborted d1 search we label the loss score as a lower bound.
            else if (abortedLossSearch)
                rootMoves[0].scoreLowerbound = true;
        }

        // Have we found a "mate in x" after a completed iteration?
        if (limits.mate && !threads.stop && is_mate_or_mated(rootMoves[0].score)
            && VALUE_MATE - std::abs(rootMoves[0].score) <= 2 * limits.mate)
            threads.stop = true;

        if (!mainThread)
            continue;

        // If the skill level is enabled and time is up, pick a sub-optimal best move
        if (skill.enabled() && skill.time_to_pick(rootDepth))
            skill.pick_best(rootMoves, multiPV);

        // Use part of the gained time from a previous stable move for the current move
        for (auto&& th : threads)
        {
            totBestMoveChanges += th->worker->bestMoveChanges;
            th->worker->bestMoveChanges = 0;
        }

        // Do we have time for the next iteration? Can we stop searching now?
        if (limits.use_time_management() && !threads.stop && !mainThread->stopOnPonderhit)
        {
            u64 nodesEffort = rootMoves[0].effort * 100000 / std::max(u64(1), u64(nodes));

            double fallingEval = (11.87 + 2.21 * (mainThread->bestPreviousAverageScore - bestValue)
                                  + 1.0 * (mainThread->iterValue[iterIdx] - bestValue))
                               / 100.0;
            fallingEval = std::clamp(fallingEval, 0.572, 1.708);

            // If the bestMove is stable over several iterations, reduce time accordingly
            timeReduction =
              std::clamp(interpolate(double(rootDepth - lastBestMoveDepth), 5.0, 18.0, 0.65, 1.55),
                         0.65, 1.55);

            double reduction = (1.48 + mainThread->previousTimeReduction) / (2.157 * timeReduction);

            double bestMoveInstability = 1.096 + 2.29 * totBestMoveChanges / threads.size();

            double highBestMoveEffort = std::clamp(
              interpolate(i64(nodesEffort), i64(79219), i64(101822), 0.924, 0.71), 0.71, 0.924);

            double totalTime = mainThread->tm.optimum() * fallingEval * reduction
                             * bestMoveInstability * highBestMoveEffort;

            if (rootMoves.size() == 1)
                // Cap used time to 0.5s for a better viewer experience
                totalTime = std::min(500.0, totalTime);

            auto elapsedTime = elapsed();

            // Stop the search if we have exceeded totalTime or maximum time,
            // or if we know that there are no better moves in the analysed line(s)
            if (elapsedTime > std::min(totalTime, double(mainThread->tm.maximum()))
                || rootMoves[multiPV - 1].score >= mate_in(3) || rootMoves[0].score == mated_in(2))
            {
                // If we are allowed to ponder do not stop the search now but
                // keep pondering until the GUI sends "ponderhit" or "stop".
                if (mainThread->ponder)
                    mainThread->stopOnPonderhit = true;
                else
                    threads.stop = true;
            }
            else
                threads.increaseDepth = mainThread->ponder || elapsedTime <= totalTime * 0.50;
        }

        mainThread->iterValue[iterIdx] = bestValue;
        iterIdx                        = (iterIdx + 1) & 3;
    }

    if (!mainThread)
        return false;

    mainThread->previousTimeReduction = timeReduction;

    // If the skill level is enabled, swap the best PV line with the sub-optimal one
    if (skill.enabled())
        std::swap(rootMoves[0],
                  *std::find(rootMoves.begin(), rootMoves.end(),
                             skill.best ? skill.best : skill.pick_best(rootMoves, multiPV)));

    return uciPvSent;
}


void Search::Worker::do_move(Position& pos, const Move move, StateInfo& st, Stack* const ss) {
    do_move(pos, move, st, pos.gives_check(move), ss);
}

void Search::Worker::do_move(
  Position& pos, const Move move, StateInfo& st, const bool givesCheck, Stack* const ss) {
    // prefetch_key does not model castling, en passant or promotion keys
    // exactly; for rare moves the prefetch lands on an unused line.
    prefetch(tt.first_entry(pos.prefetch_key(move)));

    bool capture = pos.capture_stage(move);
    ++nodes;

    DirtyPiece dirtyPiece;
    pos.do_move(move, st, givesCheck, dirtyPiece, &tt);

    if (nnueActive)
        accStack->push(TeraNNUE::network(), pos, dirtyPiece);

    if (ss != nullptr)
    {
        const int ps    = piece_slot(dirtyPiece.pc);
        ss->currentMove = move;
        ss->continuationHistory = &continuationHistory[ss->inCheck][capture][ps][move.to_sq()];
        ss->continuationCorrectionHistory = &continuationCorrectionHistory[ps][move.to_sq()];
    }
}

void Search::Worker::do_null_move(Position& pos, StateInfo& st, Stack* const ss) {
    pos.do_null_move(st);
    ss->currentMove                   = Move::null();
    ss->continuationHistory           = &continuationHistory[0][0][NO_PIECE][0];
    ss->continuationCorrectionHistory = &continuationCorrectionHistory[NO_PIECE][0];
}

void Search::Worker::undo_move(Position& pos, const Move move) {
    pos.undo_move(move);

    if (nnueActive)
        accStack->pop();
}

// A null move leaves the board untouched, so the colour-indexed accumulator
// stays valid as is: nothing to push, nothing to pop.
void Search::Worker::undo_null_move(Position& pos) { pos.undo_null_move(); }

// Called once per search, before the root is entered. Decides whether the
// NNUE path is live for this search and seeds the accumulator from the root.
void Search::Worker::nnue_start_search() {

    nnueActive = TeraNNUE::active();

    if (!nnueActive)
        return;

    if (!accStack)
        accStack = std::make_unique<TeraNNUE::AccumulatorStack>();

    accStack->reset(TeraNNUE::network(), rootPos);
}


// Reset histories, usually before a new game
void Search::Worker::clear() {
    mainHistory.fill(-5);
    captureHistory.fill(-699);

    // Each thread is responsible for clearing their part of shared history
    sharedHistory.correctionHistory.clear_range(-6, numaThreadIdx, numaTotal);
    sharedHistory.pawnHistory.clear_range(-1262, numaThreadIdx, numaTotal);

    ttMoveHistory = 0;

    for (auto& to : continuationCorrectionHistory)
        for (auto& h : to)
            h.fill(5);

    for (bool inCheck : {false, true})
        for (StatsType c : {NoCaptures, Captures})
            for (auto& to : continuationHistory[inCheck][c])
                for (auto& h : to)
                    h.fill(-552);

    for (usize i = 1; i < reductions.size(); ++i)
        reductions[i] = int(2834 / 128.0 * std::log(i));
}


// Main search function for both PV and non-PV nodes
template<NodeType nodeType>
Value Search::Worker::search(
  Position& pos, Stack* ss, Value alpha, Value beta, Depth depth, const bool cutNode) {

    constexpr bool PvNode   = nodeType != NonPV;
    constexpr bool rootNode = nodeType == Root;
    const bool     allNode  = !(PvNode || cutNode);

    // Dive into quiescence search when the depth reaches zero
    if (depth <= 0)
        return qsearch<PvNode ? PV : NonPV>(pos, ss, alpha, beta);

    // Limit the depth if extensions made it too large
    depth = std::min(depth, MAX_PLY - 1);

    // Check if we have an upcoming move that draws by repetition
    if (!rootNode && alpha < VALUE_DRAW && pos.upcoming_repetition(ss->ply))
    {
        alpha = value_draw(nodes);
        if (alpha >= beta)
            return alpha;
    }

    assert(-VALUE_INFINITE <= alpha && alpha < beta && beta <= VALUE_INFINITE);
    assert(PvNode || (alpha == beta - 1));
    assert(0 < depth && depth < MAX_PLY);
    assert(!(PvNode && cutNode));

    PVMoves   pv;
    StateInfo st;

    Key   posKey;
    Move  move, excludedMove, bestMove;
    Depth extension, newDepth;
    Value bestValue, value, eval, maxValue, probCutBeta;
    bool  givesCheck, improving, priorCapture, opponentWorsening;
    bool  capture, ttCapture;
    int   priorReduction;
    Piece movedPiece;

    SearchedList capturesSearched;
    SearchedList quietsSearched;

#ifdef TERA_LMP_TRACE
    std::optional<TeraLmpTrace::Record> lmpTraceRecord;
    int                                 lmpQuietPrefixCount = 0;
#endif

    // Step 1. Initialize node
    ss->inCheck   = bool(pos.checkers());
    priorCapture  = pos.captured_piece();
    Color us      = pos.side_to_move();
    ss->moveCount = 0;
    bestValue     = -VALUE_INFINITE;
    maxValue      = VALUE_INFINITE;

    // Check for the available remaining time
    if (is_mainthread()
#ifdef TERA_LMP_TRACE
        && !TeraLmpShadow::suppress_writes()
#endif
    )
        main_manager()->check_time(*this);

    // Used to send selDepth info to GUI (selDepth counts from 1, ply from 0)
    if (PvNode && selDepth < ss->ply + 1)
        selDepth = ss->ply + 1;

    if (!rootNode)
    {
        // Step 2. Check for aborted search and immediate draw
        if (threads.stop.load(std::memory_order_relaxed) || pos.is_draw(ss->ply)
            || ss->ply >= MAX_PLY)
            return (ss->ply >= MAX_PLY && !ss->inCheck) ? evaluate(pos) : value_draw(nodes);

        // Step 3. Mate distance pruning. Even if we mate at the next move our score
        // would be at best mate_in(ss->ply + 1), but if alpha is already bigger because
        // a shorter mate was found upward in the tree then there is no need to search
        // because we will never beat the current alpha. Same logic but with reversed
        // signs apply also in the opposite condition of being mated instead of giving
        // mate. In this case, return a fail-high score.
        alpha = std::max(mated_in(ss->ply), alpha);
        beta  = std::min(mate_in(ss->ply + 1), beta);
        if (alpha >= beta)
            return alpha;
    }

    assert(0 <= ss->ply && ss->ply < MAX_PLY);

    Square prevSq  = ((ss - 1)->currentMove).is_ok() ? ((ss - 1)->currentMove).to_sq() : SQ_NONE;
    bestMove       = Move::none();
    priorReduction = (ss - 1)->reduction;
    (ss - 1)->reduction = 0;
    ss->statScore       = 0;
    (ss + 2)->cutoffCnt = 0;

    const auto correctionValue = correction_value(*this, pos, ss);

    // Step 4. Transposition table lookup
    excludedMove                   = ss->excludedMove;
    posKey                         = pos.key();
    auto [ttHit, ttData, ttWriter] = tt.probe(posKey);
    // Need further processing of the saved data
    ss->ttHit    = ttHit;
    ttData.move  = rootNode ? rootMoves[pvIdx].pv[0] : ttHit ? ttData.move : Move::none();
    ttData.value = ttHit ? value_from_tt(ttData.value, ss->ply, pos.rule50_count()) : VALUE_NONE;
    ss->ttPv     = excludedMove ? ss->ttPv : PvNode || (ttHit && ttData.is_pv);
    ttCapture    = ttData.move && pos.capture_stage(ttData.move);

    // Step 5. Static evaluation of the position
    Value unadjustedStaticEval = VALUE_NONE;

    // Skip early pruning when in check
    if (ss->inCheck)
        ss->staticEval = eval = (ss - 2)->staticEval;
    else if (excludedMove)
        unadjustedStaticEval = eval = ss->staticEval;
    else if (ss->ttHit)
    {
        // Never assume anything about values stored in TT
        unadjustedStaticEval = ttData.eval;
        if (!is_valid(unadjustedStaticEval))
            unadjustedStaticEval = evaluate(pos);

        ss->staticEval = eval = to_corrected_static_eval(unadjustedStaticEval, correctionValue);

        // ttValue can be used as a better position evaluation
        if (is_valid(ttData.value)
            && (ttData.bound & (ttData.value > eval ? BOUND_LOWER : BOUND_UPPER)))
            eval = ttData.value;
    }
    else
    {
        unadjustedStaticEval = evaluate(pos);
        ss->staticEval = eval = to_corrected_static_eval(unadjustedStaticEval, correctionValue);

        // Static evaluation is saved as it was before adjustment by correction history
        ttWriter.write(posKey, VALUE_NONE, ss->ttPv, BOUND_NONE, DEPTH_UNSEARCHED, Move::none(),
                       unadjustedStaticEval, tt.generation());
    }

    // Set up the improving flag, which is true if current static evaluation is
    // bigger than the previous static evaluation at our turn (if we were in
    // check at our previous move we go back until we weren't in check) and is
    // false otherwise. The improving flag is used in various pruning heuristics.
    // Similarly, opponentWorsening is true if our static evaluation is better
    // for us than at the last ply.
    improving         = ss->staticEval > (ss - 2)->staticEval;
    opponentWorsening = ss->staticEval > -(ss - 1)->staticEval;

    // Hindsight adjustment of reductions based on static evaluation difference.
    if (priorReduction >= 3 && !opponentWorsening)
        depth++;
    if (priorReduction >= 2 && depth >= 2 && ss->staticEval + (ss - 1)->staticEval > 173)
        depth--;

    // At non-PV nodes we check for an early TT cutoff
    if (!PvNode && !excludedMove && ttData.depth > depth - (ttData.value <= beta)
        && is_valid(ttData.value)  // Can happen when !ttHit or when access race in probe()
        && (ttData.bound & (ttData.value >= beta ? BOUND_LOWER : BOUND_UPPER))
        && (cutNode == (ttData.value >= beta) || depth > 4))
    {
        // If ttMove is quiet, update move sorting heuristics on TT hit
        if (ttData.move && ttData.value >= beta)
        {
            // Bonus for a quiet ttMove that fails high
            if (!ttCapture)
                update_quiet_histories(pos, ss, *this, ttData.move, std::min(114 * depth, 724));

            // Extra penalty for early quiet moves of the previous ply
            if (prevSq != SQ_NONE && (ss - 1)->moveCount < 4 && !priorCapture)
                update_continuation_histories(ss - 1, pos.piece_on(prevSq), prevSq, -2187);
        }

        // Partial workaround for the graph history interaction problem
        // For high rule50 counts don't produce transposition table cutoffs.
        if (pos.rule50_count() < 96)
        {
            if (depth >= 7 && ttData.move && pos.pseudo_legal(ttData.move) && pos.legal(ttData.move)
                && !is_decisive(ttData.value))
            {
                pos.do_move(ttData.move, st);
                Key nextPosKey                             = pos.key();
                auto [ttHitNext, ttDataNext, ttWriterNext] = tt.probe(nextPosKey);
                pos.undo_move(ttData.move);

                // Check that the ttValue after the tt move would also trigger a cutoff
                if (!is_valid(ttDataNext.value))
                    return ttData.value;

                if ((ttData.value >= beta) == (-ttDataNext.value >= beta))
                    return ttData.value;
            }
            else
                return ttData.value;
        }
    }  // No cutoff, but why? Does the stored inexact value mismatch our aspiration window?
    else if (!PvNode && !excludedMove && ttData.depth > depth - (ttData.value <= beta)
             && is_valid(ttData.value) && ttData.bound != BOUND_EXACT
             && ttData.bound & (ttData.value >= beta ? BOUND_UPPER : BOUND_LOWER) && depth > 5)
    {  // If a window-bound mismatch is the only reason cutoff failed, penalize the now-useless tte
        ttWriter.penalize(1);
    }

    // Step 6. Tablebases probe: removed (Syzygy is out of the Terachess build)

    if (ss->inCheck)
        goto moves_loop;

    // Use static evaluation difference to improve quiet move ordering
    if (((ss - 1)->currentMove).is_ok() && !(ss - 1)->inCheck && !priorCapture)
    {
        int evalDiff = std::clamp(-int((ss - 1)->staticEval + ss->staticEval), -183, 180) + 62;
        mainHistory[~us][((ss - 1)->currentMove).raw() & 0xFFFF] << evalDiff * 10;
        if (!ttHit && type_of(pos.piece_on(prevSq)) != PAWN
            && !((ss - 1)->currentMove).is_promotion())
            sharedHistory.pawn_entry(pos)[pos.piece_on(prevSq)][prevSq] << evalDiff * 13;
    }


    // Step 7. Razoring
    // If eval is really low, skip search entirely and return the qsearch value.
    // For PvNodes, we must have a guard against mates being returned.
    if (!PvNode && eval < alpha - 465 - 300 * depth * depth)
        return qsearch<NonPV>(pos, ss, alpha, beta);

    // Step 8. Futility pruning: child node
    // The depth condition is important for mate finding.
    if (!ss->ttPv && depth < 17 && eval >= beta && (!ttData.move || ttCapture) && !is_loss(beta)
        && !is_win(eval))
    {
        Value futilityMult = std::min(40 + depth * 4, 80);
        futilityMult -= 20 * !ss->ttHit;

        Value futilityMargin = futilityMult * depth
                             - (2934 * improving + 343 * opponentWorsening) * futilityMult / 1024
                             + std::abs(correctionValue) / 182069;

        if (eval - futilityMargin >= beta)
            return (716 * beta + 308 * eval) / 1024;
    }

    // Step 9. Null move search with verification search
    if (cutNode && ss->staticEval >= beta - 14 * depth - 45 * improving + 374 && !excludedMove
        && pos.non_pawn_material(us) && ss->ply >= nmpMinPly && !is_loss(beta))
    {
        assert((ss - 1)->currentMove != Move::null());

        // Null move dynamic reduction based on depth
        Depth R = 7 + depth / 3;
        do_null_move(pos, st, ss);

        Value nullValue = -search<NonPV>(pos, ss + 1, -beta, -beta + 1, depth - R, false);

        undo_null_move(pos);

        // Do not return unproven mate or TB scores
        if (nullValue >= beta && !is_win(nullValue))
        {
            if (nmpMinPly || depth < 16)
                return nullValue;

            assert(!nmpMinPly);  // Recursive verification is not allowed

            // Do verification search at high depths, with null move pruning disabled
            // until ply exceeds nmpMinPly.
            nmpMinPly = ss->ply + 3 * (depth - R) / 4;

            Value v = search<NonPV>(pos, ss, beta - 1, beta, depth - R, false);

            nmpMinPly = 0;

            if (v >= beta)
                return nullValue;
        }
    }

    improving |= ss->staticEval >= beta;

    // Step 10. Internal iterative reductions
    // At sufficient depth, reduce depth for PV/Cut nodes without a TTMove.
    // (*Scaler) Making IIR more aggressive scales poorly.
    if (!allNode && depth >= 6 && !ttData.move)
        depth--;

    // Step 11. ProbCut
    // If we have a good enough capture (or queen promotion) and a reduced search
    // returns a value much above beta, we can (almost) safely prune the previous move.
    probCutBeta = beta + 214 - 59 * improving;
    if (depth >= 3
        && !is_decisive(beta)
        // If value from transposition table is lower than probCutBeta, don't attempt
        // probCut there
        && !(is_valid(ttData.value) && ttData.value < probCutBeta))
    {
        assert(probCutBeta < VALUE_INFINITE && probCutBeta > beta);

        MovePicker mp(pos, ttData.move, probCutBeta - ss->staticEval, &captureHistory, arena_top(),
                      gen_scratch());
        Depth      probCutDepth = depth - 4 - improving;

        while ((move = mp.next_move()) != Move::none())
        {
            assert(move.is_ok());

            if (move == excludedMove || !pos.legal(move))
                continue;

            assert(pos.capture_stage(move));

            do_move(pos, move, st, ss);

            // Perform a preliminary qsearch to verify that the move holds
            value = -qsearch<NonPV>(pos, ss + 1, -probCutBeta, -probCutBeta + 1);

            // If the qsearch held, perform the regular search
            if (value >= probCutBeta && probCutDepth > 0)
                value = -search<NonPV>(pos, ss + 1, -probCutBeta, -probCutBeta + 1, probCutDepth,
                                       !cutNode);

            undo_move(pos, move);

            if (value >= probCutBeta)
            {
                // Save ProbCut data into transposition table
                ttWriter.write(posKey, value_to_tt(value, ss->ply), ss->ttPv, BOUND_LOWER,
                               probCutDepth + 1, move, unadjustedStaticEval, tt.generation());

                if (!is_decisive(value))
                    return value - (probCutBeta - beta);
            }
        }
    }

moves_loop:  // When in check, search starts here

    // Step 12. A small Probcut idea
    probCutBeta = beta + 428;
    if ((ttData.bound & BOUND_LOWER) && ttData.depth >= depth - 4 && ttData.value >= probCutBeta
        && !is_decisive(beta) && is_valid(ttData.value) && !is_decisive(ttData.value))
        return probCutBeta;

    const PieceToHistory* contHist[] = {
      (ss - 1)->continuationHistory, (ss - 2)->continuationHistory, (ss - 3)->continuationHistory,
      (ss - 4)->continuationHistory, (ss - 5)->continuationHistory, (ss - 6)->continuationHistory};

    MovePicker mp(pos, ttData.move, depth, &mainHistory, &lowPlyHistory, &captureHistory, contHist,
                  &sharedHistory, ss->ply, arena_top(), gen_scratch());

    value = bestValue;

    int moveCount = 0;

    // Step 13. Loop through all pseudo-legal moves until no moves remain
    // or a beta cutoff occurs.
    while ((move = mp.next_move()) != Move::none())
    {
        assert(move.is_ok());

        if (move == excludedMove)
            continue;

        // Check for legality
        if (!pos.legal(move))
            continue;

        // At root obey the "searchmoves" option and skip moves not listed in Root
        // Move List. In MultiPV mode we also skip PV moves that have been already
        // searched and those of lower "TB rank" if we are in a TB root position.
        if (rootNode && !std::count(rootMoves.begin() + pvIdx, rootMoves.begin() + pvLast, move))
            continue;

        ss->moveCount = ++moveCount;

        if (rootNode && is_mainthread() && nodes > NODES_LIMIT_OUTPUT)
        {
            main_manager()->updates.onIter({depth, UCIEngine::move(move), moveCount + pvIdx});
        }
        if (PvNode)
            (ss + 1)->pv = nullptr;

        extension  = 0;
        capture    = pos.capture_stage(move);
        movedPiece = pos.moved_piece(move);
        givesCheck = pos.gives_check(move);
#ifdef TERA_LMP_TRACE
        if (!capture && !TeraLmpShadow::suppress_writes())
            ++lmpQuietPrefixCount;
#endif

        // Calculate new depth for this move
        newDepth = depth - 1;

        int delta = beta - alpha;

        int r = reduction(improving, depth, moveCount, delta);

        // Increase reduction for ttPv nodes (*Scaler)
        // Larger values scale well
        if (ss->ttPv)
            r += 1006;

        // Step 14. Pruning at shallow depths.
        // Depth conditions are important for mate finding.
        if (!rootNode && pos.non_pawn_material(us) && !is_loss(bestValue))
        {
            // Skip quiet moves if movecount exceeds our threshold
            const int baselineLmpThreshold = (3 + depth * depth) / (2 - improving);
#ifdef TERA_LMP_TRACE
            // Baseline mode replays from the exact live LMP trigger/state and
            // labels only lenient policies. U3/4 is a separate direction-control
            // trace because its earlier state cannot label baseline tails exactly.
            const int shadowProbeThreshold = TeraLmpTrace::sink().baseline_mode()
                                               ? baselineLmpThreshold
                                               : std::max(1, 3 * baselineLmpThreshold / 4);
            if (moveCount >= shadowProbeThreshold && !TeraLmpShadow::suppress_writes()
                && !ss->inCheck && !lmpTraceRecord)
            {
                const u64 sequence =
                  TeraLmpTrace::sink().claim(threads.size(), int(depth), improving);
                if (sequence)
                {
                    const std::vector<Move> remaining = mp.trace_remaining_moves();
                    struct RankedQuiet {
                        Move move;
                        int  rank;
                    };
                    std::vector<RankedQuiet> quietTail;
                    int                      finalRank = moveCount;

                    for (Move candidate : remaining)
                    {
                        if (candidate == excludedMove || !pos.legal(candidate))
                            continue;
                        ++finalRank;
                        if (!pos.capture_stage(candidate))
                            quietTail.push_back({candidate, finalRank});
                    }

                    if (!quietTail.empty())
                    {
                            TeraLmpTrace::Record record;
                            record.sequence       = sequence;
                            record.rootKey        = TeraLmpTrace::root_key();
                            record.nodeKey        = u64(pos.key());
                            record.rootFen        = TeraLmpTrace::root_fen();
                            record.fen            = pos.fen();
                            record.nodeType       = PvNode ? "pv" : cutNode ? "cut" : "all";
                            record.probeMode      = std::string(TeraLmpTrace::sink().probe_mode());
                            record.ply            = ss->ply;
                            record.depth          = depth;
                            record.pieceCount     = pos.count<ALL_PIECES>();
                            record.alpha          = int(alpha);
                            record.beta           = int(beta);
                            record.bestBefore      = int(bestValue);
                            record.probeTriggerRank = moveCount;
                            record.legalMoveCount   = finalRank;
                            record.quietPrefixCount = lmpQuietPrefixCount;
                            record.tailQuiets       = int(quietTail.size());
                            record.improving        = improving;
                            record.tail.reserve(quietTail.size());

                            // Clone the search stack through ss+2. The shadow uses
                            // the live Position/NNUE accumulator only while each
                            // move is made and always undoes it before returning.
                            std::array<Stack, MAX_PLY + 10> frozenStack{};
                            Stack* const primaryRoot = ss - ss->ply;
                            Stack* const frozenRoot  = frozenStack.data() + 7;
                            for (int offset = -7; offset <= ss->ply + 2; ++offset)
                                frozenRoot[offset] = primaryRoot[offset];
                            for (int offset = ss->ply + 3; offset <= MAX_PLY + 2; ++offset)
                                frozenRoot[offset].ply = offset;

                            const u64      savedNodes       = u64(nodes);
                            const int      savedSelDepth    = selDepth;
                            const int      savedNmpMinPly   = nmpMinPly;
                            ExtMove* const savedArenaTop    = movesArenaTop;

                            {
                                TeraLmpShadow::ScopedReadOnlySearch readOnlyShadow;

                                for (const RankedQuiet& ranked : quietTail)
                                {
                                    // Every candidate is an independent causal
                                    // replay from precisely the same thread state.
                                    nodes     = savedNodes;
                                    selDepth  = savedSelDepth;
                                    nmpMinPly = savedNmpMinPly;

                                    auto   shadowStack = frozenStack;
                                    Stack* shadowSs = shadowStack.data() + 7 + ss->ply;
                                    PVMoves shadowParentPv;
                                    PVMoves shadowChildPv;
                                    shadowSs->pv       = &shadowParentPv;
                                    (shadowSs + 1)->pv = nullptr;

                                    TeraLmpTrace::ShadowMove result;
                                    result.rank       = ranked.rank;
                                    result.move       = UCIEngine::move(ranked.move);
                                    result.givesCheck = pos.gives_check(ranked.move);

                                    const Move  shadowMove       = ranked.move;
                                    const Piece shadowMovedPiece = pos.moved_piece(shadowMove);
                                    Depth       shadowDepth      = depth;
                                    Depth       shadowNewDepth   = shadowDepth - 1;
                                    int         shadowR = reduction(improving, shadowDepth,
                                                                    ranked.rank, beta - alpha);

                                    if (shadowSs->ttPv)
                                        shadowR += 1006;

                                    int shadowLmrDepth = shadowNewDepth - shadowR / 1024;
                                    if (result.givesCheck)
                                    {
                                        const Piece capturedPiece = pos.piece_on(shadowMove.to_sq());
                                        const int captHist =
                                          captureHistory[shadowMovedPiece][shadowMove.to_sq()]
                                                        [type_of(capturedPiece)];

                                        if (shadowLmrDepth < 7)
                                        {
                                            const Value futilityValue =
                                              shadowSs->staticEval + 231 + 232 * shadowLmrDepth
                                              + PieceValue[capturedPiece] + 131 * captHist / 1024;
                                            if (futilityValue <= alpha)
                                                result.prunedByRest = true;
                                        }

                                        const int margin = 175 * shadowDepth + captHist * 34 / 1024;
                                        if (!result.prunedByRest
                                            && (alpha >= VALUE_DRAW
                                                || pos.non_pawn_material(us)
                                                     != PieceValue[shadowMovedPiece])
                                            && !pos.see_ge(shadowMove, -margin))
                                            result.prunedByRest = true;
                                    }
                                    else
                                    {
                                        const int dIndex =
                                          std::min(int(shadowDepth), int(lmrDivisor.size())) - 1;
                                        int history =
                                          (*contHist[0])[piece_slot(shadowMovedPiece)]
                                                        [shadowMove.to_sq()]
                                          + (*contHist[1])[piece_slot(shadowMovedPiece)]
                                                          [shadowMove.to_sq()]
                                          + sharedHistory.pawn_entry(pos)[shadowMovedPiece]
                                                                          [shadowMove.to_sq()];

                                        if (history < -4313 * shadowDepth)
                                            result.prunedByRest = true;

                                        history +=
                                          64 * mainHistory[us][shadowMove.raw() & 0xFFFF] / 32;
                                        shadowLmrDepth += history / lmrDivisor[dIndex];

                                        const Value futilityValue =
                                          shadowSs->staticEval
                                          + (40 + 138 * !bestMove + 117 * shadowLmrDepth
                                             + 90 * (shadowSs->staticEval > alpha));

                                        if (!result.prunedByRest && !shadowSs->inCheck
                                            && shadowLmrDepth < 12 && futilityValue <= alpha)
                                            result.prunedByRest = true;

                                        shadowLmrDepth = std::max(shadowLmrDepth, 0);
                                        if (!result.prunedByRest
                                            && !pos.see_ge(shadowMove,
                                                           -25 * shadowLmrDepth * shadowLmrDepth))
                                            result.prunedByRest = true;
                                    }

                                    if (result.prunedByRest)
                                    {
                                        record.tail.push_back(std::move(result));
                                        continue;
                                    }

                                    int shadowExtension =
                                      result.givesCheck && shadowDepth > 6
                                          && std::abs(shadowSs->staticEval) > 100;
                                    StateInfo shadowState;
                                    const u64 shadowNodesBefore = savedNodes;
                                    do_move(pos, shadowMove, shadowState, result.givesCheck, shadowSs);
                                    shadowNewDepth += shadowExtension;

                                    if (shadowSs->ttPv)
                                        shadowR -= 2766 + PvNode * 1017
                                                 + (ttData.value > alpha) * 838
                                                 + (ttData.depth >= shadowDepth)
                                                     * (923 + cutNode * 955);

                                    shadowR += 714;
                                    shadowR -= std::min(ranked.rank, 40) * 62;
                                    shadowR -= std::abs(correctionValue) / 26131;
                                    if (cutNode)
                                        shadowR += 3995 + 1059 * !ttData.move;
                                    if (ttCapture)
                                        shadowR += 1039;
                                    if ((shadowSs + 1)->cutoffCnt > 1)
                                        shadowR += 236
                                                 + 1079 * ((shadowSs + 1)->cutoffCnt > 2)
                                                 + 1143 * allNode;

                                    shadowSs->statScore =
                                      2 * mainHistory[us][shadowMove.raw() & 0xFFFF]
                                      + (*contHist[0])[piece_slot(shadowMovedPiece)]
                                                      [shadowMove.to_sq()]
                                      + (*contHist[1])[piece_slot(shadowMovedPiece)]
                                                      [shadowMove.to_sq()];
                                    shadowR -= shadowSs->statScore * 445 / 4096;
                                    if (allNode)
                                        shadowR += shadowR * 272 / (256 * shadowDepth + 285);

                                    Value shadowValue;
                                    if (shadowDepth >= 2 && ranked.rank > 1)
                                    {
                                        Depth d =
                                          std::max(1,
                                                   std::min(shadowNewDepth - shadowR / 1024,
                                                            shadowNewDepth + 2))
                                          + PvNode;
                                        shadowSs->reduction = shadowNewDepth - d;
                                        shadowValue =
                                          -search<NonPV>(pos, shadowSs + 1, -(alpha + 1), -alpha,
                                                         d, true);
                                        shadowSs->reduction = 0;

                                        if (shadowValue > alpha)
                                        {
                                            const bool doDeeper =
                                              d < shadowNewDepth && shadowValue > bestValue + 52;
                                            const bool doShallower = shadowValue < bestValue + 9;
                                            shadowNewDepth += doDeeper - doShallower;
                                            if (shadowNewDepth > d)
                                                shadowValue =
                                                  -search<NonPV>(pos, shadowSs + 1, -(alpha + 1),
                                                                 -alpha, shadowNewDepth, !cutNode);
                                            update_continuation_histories(
                                              shadowSs, shadowMovedPiece, shadowMove.to_sq(), 1415);
                                        }
                                    }
                                    else
                                    {
                                        if (!ttData.move)
                                            shadowR += 1085;
                                        shadowValue =
                                          -search<NonPV>(
                                            pos, shadowSs + 1, -(alpha + 1), -alpha,
                                            shadowNewDepth - (shadowR > 5039)
                                              - (shadowR > 5223 && shadowNewDepth > 2),
                                            !cutNode);
                                    }

                                    if constexpr (PvNode)
                                    {
                                        if (ranked.rank == 1 || shadowValue > alpha)
                                        {
                                            (shadowSs + 1)->pv = &shadowChildPv;
                                            shadowChildPv.clear();
                                            shadowValue =
                                              -search<PV>(pos, shadowSs + 1, -beta, -alpha,
                                                          shadowNewDepth, false);
                                        }
                                    }

                                    undo_move(pos, shadowMove);
                                    result.value = shadowValue;
                                    result.nodes = u64(nodes) - shadowNodesBefore;
                                    record.tail.push_back(std::move(result));

                                    if (threads.stop.load(std::memory_order_relaxed))
                                    {
                                        record.stopped = true;
                                        break;
                                    }
                                }
                            }

                            nodes      = savedNodes;
                            selDepth   = savedSelDepth;
                            nmpMinPly  = savedNmpMinPly;
                            if (movesArenaTop != savedArenaTop)
                            {
                                std::cerr << "TERA_LMP_TRACE arena imbalance\n";
                                std::_Exit(2);
                            }
                        lmpTraceRecord = std::move(record);
                    }
                }
            }
#endif
            if (depth > 4 && moveCount >= baselineLmpThreshold)
            {
#ifdef TERA_LMP_TRACE
                if (lmpTraceRecord && !lmpTraceRecord->baselineTriggerRank)
                {
                    lmpTraceRecord->baselineTriggerRank = moveCount;
                    lmpTraceRecord->baselineTriggerDepth = int(depth);
                    const std::vector<Move> baselineRemaining = mp.trace_remaining_moves();
                    for (Move candidate : baselineRemaining)
                        if (candidate != excludedMove && pos.legal(candidate)
                            && !pos.capture_stage(candidate))
                            lmpTraceRecord->baselineSkippedMoves.push_back(
                              UCIEngine::move(candidate));
                }
#endif
                mp.skip_quiet_moves();
            }

            // Reduced depth of the next LMR search
            int lmrDepth = newDepth - r / 1024;

            if (capture || givesCheck)
            {
                Piece capturedPiece = pos.piece_on(move.to_sq());
                int   captHist = captureHistory[movedPiece][move.to_sq()][type_of(capturedPiece)];

                // Futility pruning for captures
                if (!givesCheck && lmrDepth < 7)
                {
                    Value futilityValue = ss->staticEval + 231 + 232 * lmrDepth
                                        + PieceValue[capturedPiece] + 131 * captHist / 1024;

                    if (futilityValue <= alpha)
                        continue;
                }

                // SEE based pruning for captures and checks
                // Avoid pruning sacrifices of our last piece for stalemate
                int margin = 175 * depth + captHist * 34 / 1024;
                if ((alpha >= VALUE_DRAW || pos.non_pawn_material(us) != PieceValue[movedPiece])
                    && !pos.see_ge(move, -margin))
                    continue;
            }
            else
            {
                int dIndex  = std::min(int(depth), int(lmrDivisor.size())) - 1;
                int history = (*contHist[0])[piece_slot(movedPiece)][move.to_sq()]
                            + (*contHist[1])[piece_slot(movedPiece)][move.to_sq()]
                            + sharedHistory.pawn_entry(pos)[movedPiece][move.to_sq()];

                // Continuation history based pruning
                if (history < -4313 * depth)
                    continue;

                history += 64 * mainHistory[us][move.raw() & 0xFFFF] / 32;

                // (*Scaler): Generally, lower divisors scale well
                lmrDepth += history / lmrDivisor[dIndex];

                Value futilityValue =
                  ss->staticEval
                  + (40 + 138 * !bestMove + 117 * lmrDepth + 90 * (ss->staticEval > alpha));

                // Futility pruning: parent node
                // (*Scaler): Generally, more frequent futility pruning
                // scales well
                if (!ss->inCheck && lmrDepth < 12 && futilityValue <= alpha)
                {
                    if (bestValue <= futilityValue && !is_decisive(bestValue)
                        && !is_win(futilityValue))
                        bestValue = futilityValue;
                    continue;
                }

                lmrDepth = std::max(lmrDepth, 0);

                // Prune moves with negative SEE
                if (!pos.see_ge(move, -25 * lmrDepth * lmrDepth))
                    continue;
            }
        }

        // Step 15. Extensions
        // Singular extension search. If all moves but one
        // fail low on a search of (alpha-s, beta-s), and just one fails high on
        // (alpha, beta), then that move is singular and should be extended. To
        // verify this we do a reduced search on the position excluding the ttMove
        // and if the result is lower than ttValue minus a margin, then we will
        // extend the ttMove. Recursive singular search is avoided.

        // (*Scaler) Generally, higher singularBeta (i.e closer to ttValue)
        // and lower extension margins scale well.
        if (!rootNode && move == ttData.move && !excludedMove && depth >= 6 + ss->ttPv
            && is_valid(ttData.value) && !is_decisive(ttData.value) && (ttData.bound & BOUND_LOWER)
            && ttData.depth >= depth - 3 && !is_shuffling(move, ss, pos))
        {
            Value singularBeta  = ttData.value - (60 + 70 * (ss->ttPv && !PvNode)) * depth / 59;
            Depth singularDepth = newDepth / 2;

            ss->excludedMove = move;
            value = search<NonPV>(pos, ss, singularBeta - 1, singularBeta, singularDepth, cutNode);
            ss->excludedMove = Move::none();

            if (value < singularBeta)
            {
                int corrValAdj   = std::abs(correctionValue) / 194822;
                int doubleMargin = -3 + 201 * PvNode - 157 * !ttCapture - corrValAdj
                                 - 1081 * ttMoveHistory / 117824 - (ss->ply > rootDepth) * 41;
                int tripleMargin = 72 + 306 * PvNode - 188 * !ttCapture + 84 * ss->ttPv - corrValAdj
                                 - (ss->ply > rootDepth) * 45;

                extension =
                  1 + (value < singularBeta - doubleMargin) + (value < singularBeta - tripleMargin);

                depth++;
            }

            // Multi-cut pruning
            // Our ttMove is assumed to fail high based on the bound of the TT entry,
            // and if after excluding the ttMove with a reduced search we fail high
            // over the original beta, we assume this expected cut-node is not
            // singular (multiple moves fail high), and we can prune the whole
            // subtree by returning a softbound.
            else if (value >= beta && !is_decisive(value))
            {
                ttMoveHistory << -442 - 108 * depth;
                return value;
            }

            // Negative extensions
            // If other moves failed high over (ttValue - margin) without the
            // ttMove on a reduced search, but we cannot do multi-cut because
            // (ttValue - margin) is lower than the original beta, we do not know
            // if the ttMove is singular or can do a multi-cut, so we reduce the
            // ttMove in favor of other moves based on some conditions:

            // If the ttMove is assumed to fail high over current beta
            else if (ttData.value >= beta)
                extension = -3;

            // If we are on a cutNode but the ttMove is not assumed to fail high
            // over current beta
            else if (cutNode)
                extension = -2;
        }

        // Extension for checks in stable-enough positions
        else if (givesCheck && depth > 6 && std::abs(ss->staticEval) > 100)
            extension = 1;

        u64 nodeCount = rootNode ? u64(nodes) : 0;

        // Step 16. Make the move
        do_move(pos, move, st, givesCheck, ss);

        // Add extension to new depth
        newDepth += extension;

        // Decrease reduction for PvNodes (*Scaler)
        if (ss->ttPv)
            r -= 2766 + PvNode * 1017 + (ttData.value > alpha) * 838
               + (ttData.depth >= depth) * (923 + cutNode * 955);

        r += 714;  // Base reduction offset to compensate for other tweaks
        // The linear term is chess-tuned for moveCount <= ~60. Terachess
        // middlegames generate 150-300 legal moves, where it overwhelms the
        // logarithmic reductions[] and turns reductions into extensions of up
        // to 4.7 plies. Capped at the chess range; see docs/search-audit.md.
        r -= std::min(moveCount, 40) * 62;
        r -= std::abs(correctionValue) / 26131;

        // Increase reduction for cut nodes
        if (cutNode)
            r += 3995 + 1059 * !ttData.move;

        // Increase reduction if ttMove is a capture
        if (ttCapture)
            r += 1039;

        // Increase reduction if next ply has a lot of fail high
        if ((ss + 1)->cutoffCnt > 1)
            r += 236 + 1079 * ((ss + 1)->cutoffCnt > 2) + 1143 * allNode;

        // For first picked move (ttMove) reduce reduction
        else if (move == ttData.move)
            r -= 2016;

        if (capture)
            ss->statScore = 809 * int(PieceValue[pos.captured_piece()]) / 128
                          + captureHistory[movedPiece][move.to_sq()][type_of(pos.captured_piece())];
        else
            ss->statScore = 2 * mainHistory[us][move.raw() & 0xFFFF]
                          + (*contHist[0])[piece_slot(movedPiece)][move.to_sq()]
                          + (*contHist[1])[piece_slot(movedPiece)][move.to_sq()];

        // Decrease/increase reduction for moves with a good/bad history
        r -= ss->statScore * 445 / 4096;

        // Scale up reductions for expected ALL nodes
        if (allNode)
            r += r * 272 / (256 * depth + 285);

        // Step 17. Late moves reduction / extension (LMR)
        if (depth >= 2 && moveCount > 1)
        {
            // In general we want to cap the LMR depth search at newDepth, but when
            // reduction is negative, we allow this move a limited search extension
            // beyond the first move depth.
            // To prevent problems when the max value is less than the min value,
            // std::clamp has been replaced by a more robust implementation.
            Depth d = std::max(1, std::min(newDepth - r / 1024, newDepth + 2)) + PvNode;

            ss->reduction = newDepth - d;
            value         = -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha, d, true);
            ss->reduction = 0;

            // Do a full-depth search when reduced LMR search fails high
            // (*Scaler) Shallower searches here don't scale well
            if (value > alpha)
            {
                // Adjust full-depth search based on LMR results - if the result was
                // good enough search deeper, if it was bad enough search shallower.
                const bool doDeeperSearch    = d < newDepth && value > bestValue + 52;
                const bool doShallowerSearch = value < bestValue + 9;

                newDepth += doDeeperSearch - doShallowerSearch;

                if (newDepth > d)
                    value = -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha, newDepth, !cutNode);

                // Post LMR continuation history updates
                update_continuation_histories(ss, movedPiece, move.to_sq(), 1415);
            }
        }

        // Step 18. Full-depth search when LMR is skipped
        else if (!PvNode || moveCount > 1)
        {
            // Increase reduction if ttMove is not present
            if (!ttData.move)
                r += 1085;

            // Note that if expected reduction is high, we reduce search depth here
            value = -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha,
                                   newDepth - (r > 5039) - (r > 5223 && newDepth > 2), !cutNode);
        }

        // For PV nodes only, do a full PV search on the first move or after a fail high,
        // otherwise let the parent node fail low with value <= alpha and try another move.
        if (PvNode && (moveCount == 1 || value > alpha))
        {
            (ss + 1)->pv = &pv;
            (ss + 1)->pv->clear();

            // Extend move from transposition table if we are about to dive into qsearch.
            // decisive score handling improves mate finding and retrograde analysis.
            if (move == ttData.move
                && ((is_valid(ttData.value) && is_decisive(ttData.value) && ttData.depth > 0)
                    || ttData.depth > 1))
                newDepth = std::max(newDepth, 1);

            value = -search<PV>(pos, ss + 1, -beta, -alpha, newDepth, false);
        }

        // Step 19. Undo move
        undo_move(pos, move);

        assert(value > -VALUE_INFINITE && value < VALUE_INFINITE);

        // Step 20. Check for a new best move
        // Finished searching the move. If a stop occurred, the return value of
        // the search cannot be trusted, and we return immediately without updating
        // best move, principal variation nor transposition table.
        if (threads.stop.load(std::memory_order_relaxed))
            return VALUE_ZERO;

        if (rootNode)
        {
            RootMove& rm = *std::find(rootMoves.begin(), rootMoves.end(), move);

            rm.effort += nodes - nodeCount;

            u64 N      = nodes - nodeCount;
            u64 E_prev = std::max(u64(1), rm.effort - N);

            // Dynamic EMA parameters for root move
            constexpr u64 Scale          = 32;
            constexpr u64 ChiNumerator   = 3;
            constexpr u64 ChiDenominator = 2;   // Chi = 3/2 = 1.5
            constexpr u64 MinWeight      = 12;  // 37.5% minimum weight
            constexpr u64 MaxWeight      = 24;  // 75% maximum weight

            u64 w     = std::clamp((Scale * N * ChiDenominator)
                                     / (N * ChiDenominator + ChiNumerator * E_prev),
                                   MinWeight, MaxWeight);
            u64 w_mss = std::min(w, u64(16));
            i64 v2    = i64(value) * std::abs(value);

            if (rm.averageScore == -VALUE_INFINITE)
                rm.averageScore = value;
            else
                rm.averageScore = Value((value * w + rm.averageScore * (Scale - w)) / Scale);

            if (rm.meanSquaredScore == -VALUE_INFINITE * VALUE_INFINITE)
                rm.meanSquaredScore = value * std::abs(value);
            else
                rm.meanSquaredScore =
                  Value((v2 * w_mss + int64_t(rm.meanSquaredScore) * (Scale - w_mss)) / Scale);

            // PV move or new best move?
            if (moveCount == 1 || value > alpha)
            {
                rm.score = rm.uciScore = value;
                rm.selDepth            = selDepth;
                rm.unset_bound_flags();

                if (value >= beta)
                {
                    rm.scoreLowerbound = true;
                    rm.uciScore        = beta;
                }
                else if (value <= alpha)
                {
                    rm.scoreUpperbound = true;
                    rm.uciScore        = alpha;
                }

                rm.pv.resize(1);

                assert((ss + 1)->pv);

                for (Move pvMove : *(ss + 1)->pv)
                    rm.pv.push_back(pvMove);

                // We record how often the best move has been changed in each iteration.
                // This information is used for time management. In MultiPV mode,
                // we must take care to only do this for the first PV line.
                if (moveCount > 1 && !pvIdx)
                    ++bestMoveChanges;
            }
            else
                // All other moves but the PV, are set to the lowest value: this
                // is not a problem when sorting because the sort is stable and the
                // move position in the list is preserved - just the PV is pushed up.
                rm.score = -VALUE_INFINITE;
        }

        // In case we have an alternative move equal in eval to the current bestmove,
        // promote it to bestmove by pretending it just exceeds alpha (but not beta).
        int inc = (value == bestValue && ss->ply + 2 >= rootDepth && (int(nodes) & 14) == 0
                   && !is_win(std::abs(value) + 1));

        if (value + inc > bestValue)
        {
            bestValue = value;

            if (value + inc > alpha)
            {
                bestMove = move;

                if (PvNode && !rootNode)  // Update pv even in fail-high case
                    ss->pv->update(move, (ss + 1)->pv);

                if (value >= beta)
                {
                    // (*Scaler) Infrequent and small updates scale well
                    ss->cutoffCnt += (extension < 2) || PvNode;
                    assert(value >= beta);  // Fail high
                    break;
                }

                // Reduce other moves if we have found at least one score improvement
                if (depth > 2 && depth < 13 && !is_decisive(value))
                    depth -= 2;

                assert(depth > 0);
                alpha = value;  // Update alpha! Always alpha < beta
            }
        }

        // If the move is worse than some previously searched move,
        // remember it, to update its stats later.
        if (move != bestMove && moveCount <= SEARCHEDLIST_CAPACITY)
        {
            if (capture)
                capturesSearched.push_back(move);
            else
                quietsSearched.push_back(move);
        }
    }

    // Step 21. Check for mate and stalemate
    // All legal moves have been searched and if there are no legal moves, it
    // must be a mate or a stalemate. If we are in a singular extension search then
    // return a fail low score.

    assert(moveCount || !ss->inCheck || excludedMove || !MoveList<LEGAL>(pos).size());

    // Adjust best value for fail high cases
    if (bestValue >= beta && !is_decisive(bestValue) && !is_decisive(alpha))
        bestValue = (bestValue * depth + beta) / (depth + 1);

    if (!moveCount)
        bestValue = excludedMove ? alpha : ss->inCheck ? mated_in(ss->ply) : VALUE_DRAW;

    // If there is a move that produces search value greater than alpha,
    // we update the stats of searched moves.
    else if (bestMove)
    {
        update_all_stats(pos, ss, *this, bestMove, prevSq, quietsSearched, capturesSearched, depth,
                         ttData.move, PvNode);
        if (!PvNode)
            ttMoveHistory << (bestMove == ttData.move ? 792 : -779);
    }

    // Bonus for prior quiet countermove that caused the fail low
    else if (!priorCapture && prevSq != SQ_NONE)
    {
        int bonusScale = -245;
        bonusScale -= (ss - 1)->statScore / 98;
        bonusScale += std::min(59 * depth, 430);
        bonusScale += 191 * ((ss - 1)->moveCount > 8);
        bonusScale += 143 * (!ss->inCheck && bestValue <= ss->staticEval - 103);
        bonusScale += 151 * (!(ss - 1)->inCheck && bestValue <= -(ss - 1)->staticEval - 78);

        bonusScale = std::max(bonusScale, 0);

        // scaledBonus ranges from 0 to roughly 2.3M, overflows happen for multipliers larger than 900
        const int scaledBonus = std::min(141 * depth - 82, 1472) * bonusScale;

        update_continuation_histories(ss - 1, pos.piece_on(prevSq), prevSq,
                                      scaledBonus * 236 / 16384);

        mainHistory[~us][((ss - 1)->currentMove).raw() & 0xFFFF] << scaledBonus * 234 / 32768;

        if (type_of(pos.piece_on(prevSq)) != PAWN && !((ss - 1)->currentMove).is_promotion())
            sharedHistory.pawn_entry(pos)[pos.piece_on(prevSq)][prevSq] << scaledBonus * 322 / 8192;
    }

    // Bonus for prior capture countermove that caused the fail low
    else if (priorCapture && prevSq != SQ_NONE)
    {
        Piece capturedPiece = pos.captured_piece();
        assert(capturedPiece != NO_PIECE);
        captureHistory[pos.piece_on(prevSq)][prevSq][type_of(capturedPiece)] << 901;
    }

    if (PvNode)
        bestValue = std::min(bestValue, maxValue);

    // If no good move is found and the previous position was ttPv, then the previous
    // opponent move is probably good and the new position is added to the search tree.
    if (bestValue <= alpha)
        ss->ttPv = ss->ttPv || (ss - 1)->ttPv;

    // Write gathered information in transposition table. Note that the
    // static evaluation is saved as it was before correction history.
    if (!excludedMove && !(rootNode && pvIdx))
        ttWriter.write(posKey, value_to_tt(bestValue, ss->ply), ss->ttPv,
                       bestValue >= beta    ? BOUND_LOWER
                       : PvNode && bestMove ? BOUND_EXACT
                                            : BOUND_UPPER,
                       moveCount != 0 ? depth : std::min(MAX_PLY - 1, depth + 6), bestMove,
                       unadjustedStaticEval, tt.generation());

    // Adjust correction history if the best move is not a capture
    // and the error direction matches whether we are above/below bounds.
    if (!ss->inCheck && !(bestMove && pos.capture(bestMove))
        && (bestValue > ss->staticEval) == bool(bestMove))
    {
        auto bonus =
          std::clamp(int(bestValue - ss->staticEval) * depth * (bestMove ? 12 : 18) / 128,
                     -CORRECTION_HISTORY_LIMIT / 4, CORRECTION_HISTORY_LIMIT / 4);
        update_correction_history(pos, ss, *this, 1114 * bonus / 1024);
    }

#ifdef TERA_LMP_TRACE
    if (lmpTraceRecord)
    {
        lmpTraceRecord->bestAfter      = int(bestValue);
        lmpTraceRecord->baselineCutoff = bestValue >= beta;
        lmpTraceRecord->baselineBestMove = bestMove ? UCIEngine::move(bestMove) : "";
        TeraLmpTrace::sink().write(*lmpTraceRecord);
    }
#endif

    assert(bestValue > -VALUE_INFINITE && bestValue < VALUE_INFINITE);

    return bestValue;
}


// Quiescence search function, which is called by the main search function with
// depth zero, or recursively with further decreasing depth. With depth <= 0, we
// "should" be using static eval only, but tactical moves may confuse the static eval.
// To fight this horizon effect, we implement this qsearch of tactical moves.
// See https://www.chessprogramming.org/Horizon_Effect
// and https://www.chessprogramming.org/Quiescence_Search
template<NodeType nodeType>
Value Search::Worker::qsearch(Position& pos, Stack* ss, Value alpha, Value beta) {

    static_assert(nodeType != Root);
    constexpr bool PvNode = nodeType == PV;

    assert(alpha >= -VALUE_INFINITE && alpha < beta && beta <= VALUE_INFINITE);
    assert(PvNode || (alpha == beta - 1));

    // Check if we have an upcoming move that draws by repetition
    if (alpha < VALUE_DRAW && pos.upcoming_repetition(ss->ply))
    {
        alpha = value_draw(nodes);
        if (alpha >= beta)
            return alpha;
    }

    PVMoves   pv;
    StateInfo st;

    Key   posKey;
    Move  move, bestMove;
    Value bestValue, value, futilityBase;
    bool  pvHit, givesCheck, capture;
    int   moveCount;

    // Step 1. Initialize node
    if (PvNode)
    {
        (ss + 1)->pv = &pv;
        ss->pv->clear();
    }

    bestMove    = Move::none();
    ss->inCheck = bool(pos.checkers());
    moveCount   = 0;

    // Used to send selDepth info to GUI (selDepth counts from 1, ply from 0)
    if (PvNode && selDepth < ss->ply + 1)
        selDepth = ss->ply + 1;

    // Step 2. Check for an immediate draw or maximum ply reached
    if (pos.is_draw(ss->ply) || ss->ply >= MAX_PLY)
        return (ss->ply >= MAX_PLY && !ss->inCheck) ? evaluate(pos) : VALUE_DRAW;

    assert(0 <= ss->ply && ss->ply < MAX_PLY);

    // Step 3. Transposition table lookup
    posKey                         = pos.key();
    auto [ttHit, ttData, ttWriter] = tt.probe(posKey);
    // Need further processing of the saved data
    ss->ttHit    = ttHit;
    ttData.move  = ttHit ? ttData.move : Move::none();
    ttData.value = ttHit ? value_from_tt(ttData.value, ss->ply, pos.rule50_count()) : VALUE_NONE;
    pvHit        = ttHit && ttData.is_pv;

    // At non-PV nodes we check for an early TT cutoff
    if (!PvNode && ttData.depth >= DEPTH_QS
        && is_valid(ttData.value)  // Can happen when !ttHit or when access race in probe()
        && (ttData.bound & (ttData.value >= beta ? BOUND_LOWER : BOUND_UPPER)))
        return ttData.value;

    // Step 4. Static evaluation of the position
    Value unadjustedStaticEval = VALUE_NONE;
    if (ss->inCheck)
        bestValue = futilityBase = -VALUE_INFINITE;
    else
    {
        const auto correctionValue = correction_value(*this, pos, ss);

        if (ss->ttHit)
        {
            // Never assume anything about values stored in TT
            unadjustedStaticEval = ttData.eval;

            if (!is_valid(unadjustedStaticEval))
                unadjustedStaticEval = evaluate(pos);

            ss->staticEval = bestValue =
              to_corrected_static_eval(unadjustedStaticEval, correctionValue);

            // ttValue can be used as a better position evaluation
            if (is_valid(ttData.value) && !is_decisive(ttData.value)
                && (ttData.bound & (ttData.value > bestValue ? BOUND_LOWER : BOUND_UPPER)))
                bestValue = ttData.value;
        }
        else
        {
            unadjustedStaticEval = evaluate(pos);
            ss->staticEval       = bestValue =
              to_corrected_static_eval(unadjustedStaticEval, correctionValue);
        }

        // Stand pat. Return immediately if static value is at least beta
        if (bestValue >= beta)
        {
            if (!is_decisive(bestValue))
                bestValue = (467 * bestValue + 557 * beta) / 1024;

            if (!ss->ttHit)
                ttWriter.write(posKey, VALUE_NONE, false, BOUND_LOWER, DEPTH_UNSEARCHED,
                               Move::none(), unadjustedStaticEval, tt.generation());
            return bestValue;
        }

        if (bestValue > alpha)
            alpha = bestValue;

        futilityBase = ss->staticEval + 335;
    }

    // Six entries like the main search: score<QUIETS> reads
    // continuationHistory[0..5], and the stack keeps seven sentinel
    // entries below the root, so ss-6 is always valid. The previous
    // single-entry array was a latent out-of-bounds read.
    const PieceToHistory* contHist[] = {
      (ss - 1)->continuationHistory, (ss - 2)->continuationHistory, (ss - 3)->continuationHistory,
      (ss - 4)->continuationHistory, (ss - 5)->continuationHistory, (ss - 6)->continuationHistory};

    Square prevSq = ((ss - 1)->currentMove).is_ok() ? ((ss - 1)->currentMove).to_sq() : SQ_NONE;

    // Initialize a MovePicker object for the current position, and prepare to search
    // the moves. We presently use two stages of move generator in quiescence search:
    // captures, or evasions only when in check.
    MovePicker mp(pos, ttData.move, DEPTH_QS, &mainHistory, &lowPlyHistory, &captureHistory,
                  contHist, &sharedHistory, ss->ply, arena_top(), gen_scratch());

    // Step 5. Loop through all pseudo-legal moves until no moves remain or a beta
    // cutoff occurs.
    while ((move = mp.next_move()) != Move::none())
    {
        assert(move.is_ok());

        if (!pos.legal(move))
            continue;

        givesCheck = pos.gives_check(move);
        capture    = pos.capture_stage(move);

        moveCount++;

        // Step 6. Pruning
        if (!is_loss(bestValue))
        {
            // Futility pruning and moveCount pruning
            if (!givesCheck && move.to_sq() != prevSq && !is_loss(futilityBase)
                && !move.is_promotion())
            {
                if (moveCount > 2)
                    continue;

                Value futilityValue = futilityBase + PieceValue[pos.piece_on(move.to_sq())];

                // If static eval + value of piece we are going to capture is
                // much lower than alpha, we can prune this move.
                if (futilityValue <= alpha)
                {
                    bestValue = std::max(bestValue, futilityValue);
                    continue;
                }

                // If static exchange evaluation is low enough
                // we can prune this move.
                if (!pos.see_ge(move, alpha - futilityBase))
                {
                    bestValue = std::max(bestValue, std::min(alpha, futilityBase));
                    continue;
                }
            }

            // Skip non-captures
            if (!capture)
                continue;

            // Do not search moves with bad enough SEE values
            if (!pos.see_ge(move, -74))
                continue;
        }

        // Step 7. Make and search the move
        do_move(pos, move, st, givesCheck, ss);

        value = -qsearch<nodeType>(pos, ss + 1, -beta, -alpha);
        undo_move(pos, move);

        assert(value > -VALUE_INFINITE && value < VALUE_INFINITE);

        // Step 8. Check for a new best move
        if (value > bestValue)
        {
            bestValue = value;

            if (value > alpha)
            {
                bestMove = move;

                if (PvNode)  // Update pv even in fail-high case
                    ss->pv->update(move, (ss + 1)->pv);

                if (value < beta)  // Update alpha here!
                    alpha = value;
                else
                    break;  // Fail high
            }
        }
    }

    // Step 9. Check for mate and stalemate
    // All legal moves have been searched. A special case: if we are
    // in check and no legal moves were found, it is checkmate.
    if (!moveCount)
    {
        if (ss->inCheck)  // Checkmate!
        {
            assert(!MoveList<LEGAL>(pos).size());
            return mated_in(ss->ply);  // Plies to mate from the root
        }

        // Only check for stalemate under specific conditions
        Color us = pos.side_to_move();
        if (!(pawn_single_push_bb(us, pos.pieces(us, PAWN)) & ~pos.pieces())
            && !pos.non_pawn_material(us) && type_of(pos.captured_piece()) >= KNIGHT
            && !MoveList<LEGAL>(pos).size())
            bestValue = VALUE_DRAW;
    }

    if (!is_decisive(bestValue) && bestValue > beta)
        bestValue = (481 * bestValue + 543 * beta) / 1024;

    // Save gathered info in transposition table. The static evaluation
    // is saved as it was before adjustment by correction history.
    ttWriter.write(posKey, value_to_tt(bestValue, ss->ply), pvHit,
                   bestValue >= beta ? BOUND_LOWER : BOUND_UPPER, DEPTH_QS, bestMove,
                   unadjustedStaticEval, tt.generation());

    assert(bestValue > -VALUE_INFINITE && bestValue < VALUE_INFINITE);

    return bestValue;
}

int Search::Worker::reduction(bool i, Depth d, int mn, int delta) const {
    int reductionScale = reductions[d] * reductions[mn];
    return reductionScale - delta * 617 / rootDelta + !i * reductionScale * 194 / 512 + 1027;
}

// elapsed() returns the time elapsed since the search started. If the
// 'nodestime' option is enabled, it will return the count of nodes searched
// instead. This function is called to check whether the search should be
// stopped based on predefined thresholds like time limits or nodes searched.
TimePoint Search::Worker::elapsed() const {
    return main_manager()->tm.elapsed([this]() { return threads.nodes_searched(); });
}

Value Search::Worker::evaluate(const Position& pos) {

    // Hot path: the accumulator on top of the stack already describes `pos`
    // (it is maintained by do_move/undo_move), so only the forward pass runs.
    if (nnueActive)
        return Eval::clamp_to_eval_range(
          Value(TeraNNUE::evaluate_accumulated(accStack->top(), pos).cp));

    return Eval::evaluate(pos);
}

namespace {
// Adjusts a mate or TB score from "plies to mate from the root" to
// "plies to mate from the current position". Standard scores are unchanged.
// The function is called before storing a value in the transposition table.
Value value_to_tt(Value v, int ply) { return is_win(v) ? v + ply : is_loss(v) ? v - ply : v; }


// Inverse of value_to_tt(): it adjusts a mate or TB score from the transposition
// table (which refers to the plies to mate/be mated from current position) to
// "plies to mate/be mated (TB win/loss) from the root". However, to avoid
// potentially false mate or TB scores related to the 50 moves rule and the
// graph history interaction, we return the highest non-TB score instead.
Value value_from_tt(Value v, int ply, int r50c) {

    if (!is_valid(v))
        return VALUE_NONE;

    // handle TB win or better
    if (is_win(v))
    {
        // Downgrade a potentially false mate score
        if (is_mate(v) && VALUE_MATE - v > 100 - r50c)
            return VALUE_TB_WIN_IN_MAX_PLY - 1;

        // Downgrade a potentially false TB score.
        if (VALUE_TB - v > 100 - r50c)
            return VALUE_TB_WIN_IN_MAX_PLY - 1;

        return v - ply;
    }

    // handle TB loss or worse
    if (is_loss(v))
    {
        // Downgrade a potentially false mate score.
        if (is_mated(v) && VALUE_MATE + v > 100 - r50c)
            return VALUE_TB_LOSS_IN_MAX_PLY + 1;

        // Downgrade a potentially false TB score.
        if (VALUE_TB + v > 100 - r50c)
            return VALUE_TB_LOSS_IN_MAX_PLY + 1;

        return v + ply;
    }

    return v;
}


// Updates stats at the end of search() when a bestMove is found
void update_all_stats(const Position& pos,
                      Stack*          ss,
                      Search::Worker& workerThread,
                      Move            bestMove,
                      Square          prevSq,
                      SearchedList&   quietsSearched,
                      SearchedList&   capturesSearched,
                      Depth           depth,
                      Move            ttMove,
                      bool            PvNode) {

    CapturePieceToHistory& captureHistory = workerThread.captureHistory;
    Piece                  movedPiece     = pos.moved_piece(bestMove);
    PieceType              capturedPiece;

    int bonus =
      std::min(134 * depth - 79, 1572) + 382 * (bestMove == ttMove) + (ss - 1)->statScore / 30;
    int malus = std::min(1005 * depth - 205, 2218);

    if (!PvNode)
        // Important: don't remove the cast to a 64-bit number else the multiplication
        // can overflow on 32-bit platforms which would change the bench signature
        bonus += int(bonus * u64(quietsSearched.size() + capturesSearched.size()) / 256);

    if (!pos.capture_stage(bestMove))
    {
        update_quiet_histories(pos, ss, workerThread, bestMove, bonus * 824 / 1024);

        int actualMalus = malus * 1136 / 1024;
        // Decrease stats for all non-best quiet moves
        for (Move move : quietsSearched)
        {
            actualMalus = actualMalus * 956 / 1024;
            update_quiet_histories(pos, ss, workerThread, move, -actualMalus);
        }
    }
    else
    {
        // Increase stats for the best move in case it was a capture move
        capturedPiece = type_of(pos.piece_on(bestMove.to_sq()));
        captureHistory[movedPiece][bestMove.to_sq()][capturedPiece] << bonus * 1366 / 1024;
    }

    // Extra penalty for a quiet early move that was not a TT move in
    // previous ply when it gets refuted.
    if (prevSq != SQ_NONE && ((ss - 1)->moveCount == 1 + (ss - 1)->ttHit) && !pos.captured_piece())
        update_continuation_histories(ss - 1, pos.piece_on(prevSq), prevSq, -malus * 683 / 1024);

    // Decrease stats for all non-best capture moves
    for (Move move : capturesSearched)
    {
        movedPiece    = pos.moved_piece(move);
        capturedPiece = type_of(pos.piece_on(move.to_sq()));
        captureHistory[movedPiece][move.to_sq()][capturedPiece] << -malus * 1518 / 1024;
    }
}


// Updates the continuation histories for the move pairs formed by
// the current move and the moves played in previous plies.
void update_continuation_histories(Stack* ss, Piece pc, Square to, int bonus) {
    static constexpr std::array<ConthistBonus, 6> conthist_bonuses = {
      {{1, 1040}, {2, 780}, {3, 300}, {4, 537}, {5, 129}, {6, 423}}};

    // Multipliers for positive history consistency
    constexpr int CMHCMultipliers[] = {96, 113, 101, 105, 127, 121, 126};
    int           positiveCount     = 0;

    for (const auto [i, weight] : conthist_bonuses)
    {
        // Only update the first 2 continuation histories if we are in check
        if (ss->inCheck && i > 2)
            break;

        if (((ss - i)->currentMove).is_ok())
        {
            auto& historyEntry = (*(ss - i)->continuationHistory)[piece_slot(pc)][to];
            if (historyEntry > 0)
                positiveCount++;

            int multiplier = CMHCMultipliers[positiveCount];
            historyEntry << (bonus * weight * multiplier / 131072) + 71 * (i < 2);
        }
    }
}

// Updates move sorting heuristics

void update_quiet_histories(
  const Position& pos, Stack* ss, Search::Worker& workerThread, Move move, int bonus) {

    Color us = pos.side_to_move();
    workerThread.mainHistory[us][move.raw() & 0xFFFF]
      << bonus;  // Untuned to prevent duplicate effort

    if (ss->ply < LOW_PLY_HISTORY_SIZE)
        workerThread.lowPlyHistory[ss->ply][move.raw() & 0xFFFF] << bonus * 663 / 1024;

    update_continuation_histories(ss, pos.moved_piece(move), move.to_sq(), bonus * 820 / 1024);

    workerThread.sharedHistory.pawn_entry(pos)[pos.moved_piece(move)][move.to_sq()]
      << bonus * (bonus > -7 ? 1038 : 525) / 1024;
}
}

// When playing with strength handicap, choose the best move among a set of
// RootMoves using a statistical rule dependent on 'level'. Idea by Heinz van Saanen.
Move Skill::pick_best(const RootMoves& rootMoves, usize multiPV) {
    static PRNG rng(now());  // PRNG sequence should be non-deterministic

    // With tablebases at the root, rootMoves are ordered by tbRank rather than by
    // score, so compute the score range explicitly to keep 'delta' non-negative.
    Value topScore = rootMoves[0].score;
    Value minScore = rootMoves[0].score;
    for (usize i = 1; i < multiPV; ++i)
    {
        topScore = std::max(topScore, rootMoves[i].score);
        minScore = std::min(minScore, rootMoves[i].score);
    }
    int    delta    = std::min(topScore - minScore, int(PawnValue));
    int    maxScore = -VALUE_INFINITE;
    double weakness = 120 - 2 * level;

    // Choose best move. For each move score we add two terms, both dependent on
    // weakness. One is deterministic and bigger for weaker levels, and one is
    // random. Then we choose the move with the resulting highest score.
    for (usize i = 0; i < multiPV; ++i)
    {
        // This is our magic formula
        int push = int(weakness * int(topScore - rootMoves[i].score)
                       + delta * (rng.rand<unsigned>() % int(weakness)))
                 / 128;

        if (rootMoves[i].score + push >= maxScore)
        {
            maxScore = rootMoves[i].score + push;
            best     = rootMoves[i].pv[0];
        }
    }

    return best;
}

// Used to print debug info and, more importantly, to detect
// when we are out of available time and thus stop the search.
void SearchManager::check_time(Search::Worker& worker) {
    if (--callsCnt > 0)
        return;

    // When using nodes, ensure checking rate is not lower than 0.1% of nodes
    callsCnt = worker.limits.nodes ? std::min(512, int(worker.limits.nodes / 1024)) : 512;

    static TimePoint lastInfoTime = now();

    TimePoint elapsed = tm.elapsed([&worker]() { return worker.threads.nodes_searched(); });
    TimePoint tick    = worker.limits.startTime + elapsed;

    if (tick - lastInfoTime >= 1000)
    {
        lastInfoTime = tick;
        dbg_print();
    }

    // We should not stop pondering until told so by the GUI
    if (ponder)
        return;

    if ((worker.limits.use_time_management() && (elapsed > tm.maximum() || stopOnPonderhit))
        || (worker.limits.movetime && elapsed >= worker.limits.movetime)
        || (worker.limits.nodes && worker.threads.nodes_searched() >= worker.limits.nodes))
        worker.threads.stop = true;
}

void SearchManager::output_pv(Search::Worker&           worker,
                              const ThreadPool&         threads,
                              const TranspositionTable& tt,
                              Depth                     depth) {

    const auto nodes     = threads.nodes_searched();
    auto&      rootMoves = worker.rootMoves;
    auto&      pos       = worker.rootPos;
    usize      multiPV   = std::min(usize(worker.options["MultiPV"]), rootMoves.size());
    u64        tbHits    = threads.tb_hits();

    for (usize i = 0; i < multiPV; ++i)
    {
        bool usePreviousScore = rootMoves[i].score == -VALUE_INFINITE;

        if (depth == 1 && usePreviousScore && i > 0)
            continue;

        Depth d = usePreviousScore ? std::max(1, depth - 1) : depth;
        Value v = usePreviousScore ? rootMoves[i].previousScore : rootMoves[i].uciScore;

        if (v == -VALUE_INFINITE)
            v = VALUE_ZERO;

        std::string pv;
        for (Move m : usePreviousScore ? rootMoves[i].previousPV : rootMoves[i].pv)
            pv += UCIEngine::move(m) + " ";

        // Remove last whitespace
        if (!pv.empty())
            pv.pop_back();

        auto wdl   = worker.options["UCI_ShowWDL"] ? UCIEngine::wdl(v, pos) : "";
        auto bound = rootMoves[i].scoreLowerbound
                     ? "lowerbound"
                     : (rootMoves[i].scoreUpperbound ? "upperbound" : "");

        InfoFull info;

        info.depth    = d;
        info.selDepth = rootMoves[i].selDepth;
        info.multiPV  = i + 1;
        info.score    = {v, pos};
        info.wdl      = wdl;

        // Previous scores are exact, even though their bound flags may say otherwise.
        if (!usePreviousScore)
            info.bound = bound;

        TimePoint time = std::max(TimePoint(1), tm.elapsed_time());
        info.timeMs    = time;
        info.nodes     = nodes;
        info.nps       = nodes * 1000 / time;
        info.tbHits    = tbHits;
        info.pv        = pv;
        info.hashfull  = tt.hashfull();

        updates.onUpdateFull(info);
    }
}

// Called in case we have no ponder move before exiting the search,
// for instance, in case we stop the search during a fail high at root.
// We try hard to have a ponder move to return to the GUI,
// otherwise in case of 'ponder on' we have nothing to think about.
bool RootMove::extract_ponder_from_tt(const TranspositionTable& tt, Position& pos) {

    assert(pv.size() == 1 && pv[0] != Move::none());

    StateInfo st;
    pos.do_move(pv[0], st, &tt);

    if (!pos.is_draw(1))
    {
        auto [ttHit, ttData, ttWriter] = tt.probe(pos.key());
        if (ttHit && MoveList<LEGAL>(pos).contains(ttData.move))
            pv.push_back(ttData.move);
    }

    pos.undo_move(pv[0]);
    return pv.size() > 1;
}


}  // namespace Stockfish
