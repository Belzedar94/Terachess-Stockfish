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

#include "engine.h"

#include <algorithm>
#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <deque>
#include <iosfwd>
#include <memory>
#include <ostream>
#include <sstream>
#include <string_view>
#include <utility>
#include <vector>

#include "evaluate.h"
#include "misc.h"
#include "nnue/tera_accumulator.h"
#include "nnue/tera_features.h"
#include "nnue/tera_network.h"
#include "numa.h"
#include "perft.h"
#include "position.h"
#include "search.h"
#include "types.h"
#include "uci.h"
#include "ucioption.h"

namespace Stockfish {

constexpr int MaxHashMB  = Is64Bit ? 33554432 : 2048;
int           MaxThreads = std::max(1024, 4 * int(get_hardware_concurrency()));

#ifdef TERA_EVALFILE_DEFAULT
constexpr const char* DefaultEvalFile = TERA_EVALFILE_DEFAULT;
#else
constexpr const char* DefaultEvalFile = "";
#endif

// The default configuration will attempt to group L3 domains up to 32 threads.
// This size was found to be a good balance between the Elo gain of increased
// history sharing and the speed loss from more cross-cache accesses (see
// PR#6526). The user can always explicitly override this behavior.
constexpr NumaAutoPolicy DefaultNumaPolicy = BundledL3Policy{32};

Engine::Engine(std::optional<std::filesystem::path> path, bool loadDefaultEval) :
    binaryDirectory(path ? CommandLine::get_binary_directory(*path) : std::filesystem::path{}),
    numaContext(NumaConfig::from_system(DefaultNumaPolicy)),
    states(new std::deque<StateInfo>(1)),
    threads() {

    pos.set(StartFEN, &states->back());

    options.add(  //
      "Debug Log File", Option("", [](const Option& o) {
          start_logger(path_from_utf8(std::string(o)));
          return std::nullopt;
      }));

    options.add(  //
      "NumaPolicy", Option("auto", [this](const Option& o) {
          if (!set_numa_config_from_option(o))
              return "NumaPolicy: invalid value '" + std::string(o) + "', keeping previous config.";
          return numa_config_information_as_string() + "\n"
               + thread_allocation_information_as_string();
      }));

    options.add(  //
      "Threads", Option(1, 1, MaxThreads, [this](const Option&) {
          resize_threads();
          return thread_allocation_information_as_string();
      }));

    options.add(  //
      "Hash", Option(16, 1, MaxHashMB, [this](const Option& o) {
          set_tt_size(o);
          return std::nullopt;
      }));

    options.add(  //
      "Clear Hash", Option([this](const Option&) {
          search_clear();
          return std::nullopt;
      }));

    options.add(  //
      "Ponder", Option(false));

    options.add(  //
      "MultiPV", Option(1, 1, MAX_MOVES));

    options.add("Skill Level", Option(20, 0, 20));

    options.add("Move Overhead", Option(10, 0, 5000));

    options.add("nodestime", Option(0, 0, 10000));

    options.add("UCI_LimitStrength", Option(false));

    options.add("UCI_Elo",
                Option(Stockfish::Search::Skill::LowestElo, Stockfish::Search::Skill::LowestElo,
                       Stockfish::Search::Skill::HighestElo));

    options.add("UCI_ShowWDL", Option(false));

    // NNUE "S" (docs/nnue-tera-s.md). EvalFile is a path to a .tnn/.tnn1
    // network. OpenBench EVALFILE builds use a deterministic path relative to
    // the final executable; ordinary builds keep the material fallback.
    options.add(  //
      "EvalFile", Option(DefaultEvalFile, [this](const Option& o) { return set_eval_file(o); }));

    options.add(  //
      "UseNNUE", Option(true, [](const Option& o) {
          TeraNNUE::set_use_nnue(int(o) != 0);
          return std::nullopt;
      }));

    threads.clear();
    resize_threads();

    if (loadDefaultEval && DefaultEvalFile[0])
    {
        TeraNNUE::set_use_nnue(true);
        const auto status = set_eval_file(DefaultEvalFile);
        if (!TeraNNUE::active())
        {
            sync_cout << "info string CRITICAL ERROR: "
                      << status.value_or("default EvalFile did not load") << sync_endl;
            std::exit(EXIT_FAILURE);
        }
        sync_cout << "info string " << status.value_or("EvalFile: loaded") << sync_endl;
    }
}

std::variant<u64, PositionSetError> Engine::perft(const std::string& fen, Depth depth) {
    return Benchmark::perft(fen, depth);
}

void Engine::go(Search::LimitsType& limits) {
    assert(limits.perft == 0);

    threads.start_thinking(options, pos, states, limits);
}
void Engine::stop() { threads.stop = true; }

void Engine::search_clear() {
    wait_for_search_finished();

    tt.clear(threads);
    threads.clear();
}

void Engine::set_on_update_no_moves(std::function<void(const Engine::InfoShort&)>&& f) {
    updateContext.onUpdateNoMoves = std::move(f);
}

void Engine::set_on_update_full(std::function<void(const Engine::InfoFull&)>&& f) {
    updateContext.onUpdateFull = std::move(f);
}

void Engine::set_on_iter(std::function<void(const Engine::InfoIter&)>&& f) {
    updateContext.onIter = std::move(f);
}

void Engine::set_on_bestmove(std::function<void(std::string_view, std::string_view)>&& f) {
    updateContext.onBestmove = std::move(f);
}

void Engine::wait_for_search_finished() { threads.main_thread()->wait_for_search_finished(); }

std::optional<PositionSetError> Engine::set_position(const std::string&              fen,
                                                     const std::vector<std::string>& moves) {
    // Drop the old state and create a new one
    states   = StateListPtr(new std::deque<StateInfo>(1));
    auto err = pos.set(fen, &states->back());
    if (err.has_value())
        return err;

    for (const auto& move : moves)
    {
        auto m = UCIEngine::to_move(pos, move);

        if (m == Move::none())
            return PositionSetError("Illegal move: " + move);

        states->emplace_back();
        pos.do_move(m, states->back());
    }

    return std::nullopt;
}

// modifiers

bool Engine::set_numa_config_from_option(const std::string& o) {
    if (o == "auto" || o == "system")
    {
        numaContext.set_numa_config(NumaConfig::from_system(DefaultNumaPolicy));
    }
    else if (o == "hardware")
    {
        // Don't respect affinity set in the system.
        numaContext.set_numa_config(NumaConfig::from_system(DefaultNumaPolicy, false));
    }
    else if (o == "none")
    {
        numaContext.set_numa_config(NumaConfig{});
    }
    else
    {
        auto parsed = NumaConfig::from_string(o);
        if (!parsed.has_value())
            return false;
        numaContext.set_numa_config(std::move(*parsed));
    }

    // Force reallocation of threads in case affinities need to change.
    resize_threads();
    return true;
}

void Engine::resize_threads() {
    threads.wait_for_search_finished();
    threads.set(numaContext.get_numa_config(), {options, threads, tt, sharedHists}, updateContext);

    // Reallocate the hash with the new threadpool size
    set_tt_size(options["Hash"]);
}

void Engine::set_tt_size(usize mb) {
    wait_for_search_finished();
    tt.resize(mb, threads);
}

void Engine::set_ponderhit(bool b) { threads.main_manager()->ponder = b; }

// utility functions

void Engine::trace_eval() const {
    StateListPtr trace_states(new std::deque<StateInfo>(1));
    Position     p;
    p.set(pos.fen(), &trace_states->back());

    sync_cout << "\n" << Eval::trace(p) << sync_endl;
}

// `features`: active feature rows per perspective (parity gate, section 8).
void Engine::trace_features() const {
    StateListPtr trace_states(new std::deque<StateInfo>(1));
    Position     p;
    p.set(pos.fen(), &trace_states->back());

    sync_cout << TeraNNUE::trace_features(p) << sync_endl;
}

// `nnuecheck`: refresh (oracle) vs incremental update over a random game
// played from the position currently set with `position ...`.
void Engine::nnue_check(int plies, u64 seed) const {
    sync_cout << TeraNNUE::selfcheck_random_game(TeraNNUE::network(), pos.fen(), plies, seed)
              << sync_endl;
}

std::optional<std::string> Engine::set_eval_file(const std::string& path) {

    wait_for_search_finished();

    if (path.empty() || path == "<empty>" || path == "none")
    {
        TeraNNUE::network().unload();
        return std::string("EvalFile: cleared, using the material evaluation");
    }

    std::string error, secondError;

    if (TeraNNUE::network().load(path, error))
        return std::string("EvalFile: loaded '" + path + "' (arch_hash "
                           + TeraNNUE::descriptor_hash_hex() + ", file_sha256 "
                           + TeraNNUE::network().sha256() + ")");

    // Second chance next to the binary, as Stockfish does for its own nets.
    const std::string sideBySide = (binaryDirectory / path_from_utf8(path)).u8string();
    if (sideBySide != path && TeraNNUE::network().load(sideBySide, secondError))
        return std::string("EvalFile: loaded '" + sideBySide + "' (arch_hash "
                           + TeraNNUE::descriptor_hash_hex() + ", file_sha256 "
                           + TeraNNUE::network().sha256() + ")");

    // Fail closed: no adaptation, no partial load, material evaluation stays.
    TeraNNUE::network().unload();
    return std::string("EvalFile: REJECTED '" + path + "': " + error
                       + " -- network NOT loaded, keeping the material evaluation");
}

const OptionsMap& Engine::get_options() const { return options; }
OptionsMap&       Engine::get_options() { return options; }

std::string Engine::fen() const { return pos.fen(); }

std::optional<PositionSetError> Engine::flip() { return pos.flip(); }

std::string Engine::visualize() const {
    std::stringstream ss;
    ss << pos;
    return ss.str();
}

int Engine::get_hashfull(int maxAge) const { return tt.hashfull(maxAge); }

std::vector<std::pair<usize, usize>> Engine::get_bound_thread_count_by_numa_node() const {
    auto                                 counts = threads.get_bound_thread_count_by_numa_node();
    const NumaConfig&                    cfg    = numaContext.get_numa_config();
    std::vector<std::pair<usize, usize>> ratios;
    NumaIndex                            n = 0;
    for (; n < counts.size(); ++n)
        ratios.emplace_back(counts[n], cfg.num_cpus_in_numa_node(n));
    if (!counts.empty())
        for (; n < cfg.num_numa_nodes(); ++n)
            ratios.emplace_back(0, cfg.num_cpus_in_numa_node(n));
    return ratios;
}

std::string Engine::get_numa_config_as_string() const {
    return numaContext.get_numa_config().to_string();
}

std::string Engine::numa_config_information_as_string() const {
    auto cfgStr = get_numa_config_as_string();
    return "Available processors: " + cfgStr;
}

std::string Engine::thread_binding_information_as_string() const {
    auto              boundThreadsByNode = get_bound_thread_count_by_numa_node();
    std::stringstream ss;
    if (boundThreadsByNode.empty())
        return ss.str();

    bool isFirst = true;

    for (auto&& [current, total] : boundThreadsByNode)
    {
        if (!isFirst)
            ss << ":";
        ss << current << "/" << total;
        isFirst = false;
    }

    return ss.str();
}

std::string Engine::thread_allocation_information_as_string() const {
    std::stringstream ss;

    usize threadsSize = threads.size();
    ss << "Using " << threadsSize << (threadsSize > 1 ? " threads" : " thread");

    auto boundThreadsByNodeStr = thread_binding_information_as_string();
    if (boundThreadsByNodeStr.empty())
        return ss.str();

    ss << " with NUMA node thread binding: ";
    ss << boundThreadsByNodeStr;

    return ss.str();
}
}
