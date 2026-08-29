/*
  Offline-only LMP shadow trace. Enable explicitly with
  EXTRACXXFLAGS=-DTERA_LMP_TRACE and TERA_LMP_TRACE_PATH=<jsonl>.

  Production/OpenBench builds do not define TERA_LMP_TRACE, so this entire
  implementation is absent from their object code.
*/

#ifndef TERA_LMP_TRACE_H_INCLUDED
#define TERA_LMP_TRACE_H_INCLUDED

#ifdef TERA_LMP_TRACE

    #include <array>
    #include <atomic>
    #include <cstdint>
    #include <cstdio>
    #include <cstdlib>
    #include <fstream>
    #include <mutex>
    #include <string>
    #include <string_view>
    #include <utility>
    #include <vector>

    #include "types.h"

namespace Stockfish::TeraLmpTrace {

inline thread_local u64         currentRootKey = 0;
inline thread_local std::string currentRootFen;

inline void set_root(u64 key, std::string fen) {
    currentRootKey = key;
    currentRootFen = std::move(fen);
}

inline u64 root_key() { return currentRootKey; }
inline const std::string& root_fen() { return currentRootFen; }

struct ShadowMove {
    int         rank         = 0;
    std::string move;
    bool        givesCheck   = false;
    bool        prunedByRest = false;
    Value       value        = VALUE_NONE;
    u64         nodes        = 0;
};

struct Record {
    u64         sequence = 0;
    u64         rootKey  = 0;
    u64         nodeKey  = 0;
    std::string rootFen;
    std::string fen;
    std::string nodeType;
    std::string probeMode;
    int         ply             = 0;
    int         depth           = 0;
    int         pieceCount      = 0;
    int         alpha           = 0;
    int         beta            = 0;
    int         bestBefore      = 0;
    int         bestAfter       = 0;
    int         probeTriggerRank    = 0;
    int         baselineTriggerRank = 0;
    int         baselineTriggerDepth = 0;
    int         legalMoveCount      = 0;
    int         quietPrefixCount    = 0;
    int         tailQuiets          = 0;
    bool        improving       = false;
    bool        baselineCutoff  = false;
    bool        stopped         = false;
    std::string baselineBestMove;
    std::vector<std::string> baselineSkippedMoves;
    std::vector<ShadowMove> tail;
};

inline std::string json_escape(std::string_view input) {
    std::string out;
    out.reserve(input.size() + 8);
    for (unsigned char c : input)
    {
        switch (c)
        {
        case '\\' : out += "\\\\"; break;
        case '"' : out += "\\\""; break;
        case '\b' : out += "\\b"; break;
        case '\f' : out += "\\f"; break;
        case '\n' : out += "\\n"; break;
        case '\r' : out += "\\r"; break;
        case '\t' : out += "\\t"; break;
        default :
            if (c < 0x20)
            {
                char escaped[7];
                std::snprintf(escaped, sizeof(escaped), "\\u%04x", unsigned(c));
                out += escaped;
            }
            else
                out += char(c);
        }
    }
    return out;
}

inline u64 parse_positive_env(const char* name, u64 fallback) {
    const char* raw = std::getenv(name);
    if (!raw || !*raw)
        return fallback;
    char*              end = nullptr;
    const unsigned long long value = std::strtoull(raw, &end, 10);
    if (!end || *end || value == 0)
    {
        std::fprintf(stderr, "invalid %s=%s (positive integer required)\n", name, raw);
        std::fflush(stderr);
        std::_Exit(2);
    }
    return u64(value);
}

class Sink {
   public:
    Sink() : every(parse_positive_env("TERA_LMP_TRACE_EVERY", 128)),
             maximum(parse_positive_env("TERA_LMP_TRACE_MAX", 20000)) {
        for (auto& counter : exposureCounters)
            counter.store(0, std::memory_order_relaxed);
        for (auto& counter : recordCounters)
            counter.store(0, std::memory_order_relaxed);

        const char* rawMode = std::getenv("TERA_LMP_TRACE_MODE");
        if (rawMode && *rawMode)
        {
            if (std::string_view(rawMode) == "baseline")
                mode = ProbeMode::Baseline;
            else if (std::string_view(rawMode) == "u34")
                mode = ProbeMode::U34;
            else
            {
                std::fprintf(stderr,
                             "invalid TERA_LMP_TRACE_MODE=%s (baseline or u34 required)\n",
                             rawMode);
                std::fflush(stderr);
                std::_Exit(2);
            }
        }

        const char* rawPath = std::getenv("TERA_LMP_TRACE_PATH");
        if (!rawPath || !*rawPath)
            return;
        path = rawPath;
        output.open(path, std::ios::out | std::ios::trunc);
        if (!output)
        {
            std::fprintf(stderr, "cannot open TERA_LMP_TRACE_PATH=%s\n", rawPath);
            std::fflush(stderr);
            std::_Exit(2);
        }
        enabledFlag = true;
    }

    bool enabled() const { return enabledFlag; }
    bool baseline_mode() const { return mode == ProbeMode::Baseline; }
    std::string_view probe_mode() const {
        return baseline_mode() ? std::string_view("baseline") : std::string_view("u34");
    }

    u64 claim(usize threadCount, int depth, bool improving) {
        if (!enabledFlag)
            return 0;
        if (threadCount != 1)
        {
            std::fprintf(stderr, "TERA_LMP_TRACE requires Threads=1, got %zu\n", threadCount);
            std::fflush(stderr);
            std::_Exit(2);
        }
        // The P1 gate is explicitly stratified by these six cells. Sampling
        // and caps are independent per cell, so abundant depth-4 nodes cannot
        // consume the entire receipt before a rarer depth-12/improving cell.
        int depthBucket = depth >= 4 && depth <= 5  ? 0
                        : depth >= 6 && depth <= 8  ? 1
                        : depth >= 9 && depth <= 12 ? 2
                                                    : -1;
        if (depthBucket < 0)
            return 0;
        const usize bucket = usize(2 * depthBucket + improving);
        const u64 seen = exposureCounters[bucket].fetch_add(1, std::memory_order_relaxed) + 1;
        if ((seen - 1) % every)
            return 0;
        const u64 slot = recordCounters[bucket].fetch_add(1, std::memory_order_relaxed) + 1;
        const u64 bucketLimit = maximum / recordCounters.size()
                              + (bucket < maximum % recordCounters.size());
        return slot <= bucketLimit
               ? sequenceCounter.fetch_add(1, std::memory_order_relaxed) + 1
               : 0;
    }

    void write(const Record& record) {
        if (!enabledFlag)
            return;
        std::lock_guard<std::mutex> lock(outputMutex);
        output << "{\"schema\":\"tera-lmp-shadow-v1\""
               << ",\"sequence\":" << record.sequence
               << ",\"root_key\":" << record.rootKey
               << ",\"node_key\":" << record.nodeKey
               << ",\"root_fen\":\"" << json_escape(record.rootFen) << '"'
               << ",\"fen\":\"" << json_escape(record.fen) << '"'
               << ",\"node_type\":\"" << record.nodeType << '"'
               << ",\"probe_mode\":\"" << record.probeMode << '"'
               << ",\"ply\":" << record.ply
               << ",\"depth\":" << record.depth
               << ",\"piece_count\":" << record.pieceCount
               << ",\"improving\":" << (record.improving ? "true" : "false")
               << ",\"alpha\":" << record.alpha
               << ",\"beta\":" << record.beta
               << ",\"best_before\":" << record.bestBefore
               << ",\"best_after\":" << record.bestAfter
               << ",\"baseline_cutoff\":" << (record.baselineCutoff ? "true" : "false")
               << ",\"baseline_bestmove\":\"" << json_escape(record.baselineBestMove) << '"'
               << ",\"probe_trigger_rank\":" << record.probeTriggerRank
               << ",\"baseline_trigger_rank\":" << record.baselineTriggerRank
               << ",\"baseline_trigger_depth\":" << record.baselineTriggerDepth
               << ",\"legal_move_count\":" << record.legalMoveCount
               << ",\"quiet_prefix_count\":" << record.quietPrefixCount
               << ",\"tail_quiets\":" << record.tailQuiets
               << ",\"stopped\":" << (record.stopped ? "true" : "false")
               << ",\"baseline_skipped_moves\":[";
        for (usize i = 0; i < record.baselineSkippedMoves.size(); ++i)
        {
            if (i)
                output << ',';
            output << '"' << json_escape(record.baselineSkippedMoves[i]) << '"';
        }
        output << ']'
               << ",\"tail\":[";
        for (usize i = 0; i < record.tail.size(); ++i)
        {
            const ShadowMove& move = record.tail[i];
            if (i)
                output << ',';
            output << "{\"rank\":" << move.rank << ",\"move\":\""
                   << json_escape(move.move) << "\",\"gives_check\":"
                   << (move.givesCheck ? "true" : "false")
                   << ",\"pruned_by_rest\":" << (move.prunedByRest ? "true" : "false")
                   << ",\"value\":";
            if (move.prunedByRest)
                output << "null";
            else
                output << int(move.value);
            output << ",\"nodes\":" << move.nodes << '}';
        }
        output << "]}\n";
        output.flush();
        if (!output)
        {
            std::fprintf(stderr, "write failed for TERA_LMP_TRACE_PATH=%s\n", path.c_str());
            std::fflush(stderr);
            std::_Exit(2);
        }
    }

   private:
    enum class ProbeMode { Baseline, U34 };

    bool             enabledFlag = false;
    ProbeMode        mode        = ProbeMode::Baseline;
    u64              every;
    u64              maximum;
    std::string      path;
    std::ofstream    output;
    std::mutex       outputMutex;
    std::array<std::atomic<u64>, 6> exposureCounters{};
    std::array<std::atomic<u64>, 6> recordCounters{};
    std::atomic<u64>                sequenceCounter{0};
};

inline Sink& sink() {
    static Sink instance;
    return instance;
}

}  // namespace Stockfish::TeraLmpTrace

#endif  // TERA_LMP_TRACE

#endif  // TERA_LMP_TRACE_H_INCLUDED
