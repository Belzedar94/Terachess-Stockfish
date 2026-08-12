/*
  Terachess-Stockfish, a Terachess II engine derived from Stockfish
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

#include "datagen.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <fstream>
#include <iomanip>
#include <ios>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "engine.h"
#include "misc.h"
#include "movegen.h"
#include "nnue/tera_network.h"
#include "notation.h"
#include "position.h"
#include "score.h"
#include "search.h"
#include "sha256.h"
#include "types.h"
#include "uci.h"
#include "ucioption.h"

#ifndef TERA_SOURCE_COMMIT
    #define TERA_SOURCE_COMMIT "0000000000000000000000000000000000000000"
#endif

#ifndef TERA_SOURCE_DIRTY
    #define TERA_SOURCE_DIRTY 1
#endif

namespace Stockfish::Datagen {

namespace {

// --------------------------------------------------------------------------
// tera-bin v1 constants (docs/tera-bin-v1.md; mirrored by tools/terabin.py)
// --------------------------------------------------------------------------

constexpr usize TeraHeaderSize = 32;
constexpr usize TeraRecordSize = 144;
constexpr int   TeraMetaBits   = 101;
constexpr int   TeraResultBit  = 99;  // 1+2+9+7+16+16+32+16
constexpr int   TeraMaxPieces  = 128;
constexpr int   ScoreLimit     = 32000;

// Result codes are POV of the side to move: 0 loss, 1 draw, 2 win, 3 unknown.
constexpr u64 ResultUnknown = 3;

constexpr u64 ResumeMetaVersion = 2;

// A game longer than this is adjudicated as a draw (contract: hard cap).
// Measured self-play length is ~575 plies on average with individual games
// reaching 850 (docs/search-audit.md §3bis); a 600-ply cap truncated ~29% of
// games into artificial draws and poisoned the WDL labels.
constexpr int MaxGamePlies = 1400;

using TeraRecord = std::array<u8, TeraRecordSize>;

// --------------------------------------------------------------------------
// Parameters
// --------------------------------------------------------------------------

struct Params {
    std::filesystem::path out;
    std::filesystem::path book;              // empty == NONE (start position)
    std::filesystem::path network;
    u64                   count             = 0;
    u64                   nodes             = 25000;
    usize                 threads           = 1;
    u64                   seed              = 1;
    int                   randomMoveCount   = 8;
    int                   randomMoveMinPly  = 1;
    int                   randomMoveMaxPly  = 20;
    int                   randomMultiPv     = 4;
    int                   randomMultiPvDiff = 100;
    int                   writeMinPly       = 4;
    int                   evalLimit         = 10000;
    bool                  filterCaptures    = true;
    bool                  filterChecks      = true;
    usize                 debugSample       = 0;
    bool                  resume            = false;
    u64                   resumeNumber      = 0;
    std::string           networkSha256;
    std::string           producerSha256;
    std::string           bookSha256;
    std::string           sourceCommit = TERA_SOURCE_COMMIT;
    bool                  sourceDirty  = TERA_SOURCE_DIRTY != 0;
    u64                   networkSize  = 0;
    u64                   producerSize = 0;
    u64                   bookSize     = 0;
};

struct ResumeMetadata {
    u64         metaVersion = ResumeMetaVersion;
    std::string command;      // generation identity (threads/resume excluded)
    std::string lastCommand;  // full command of the most recent session
    std::string format        = "tera-bin";
    u64         formatVersion = 1;
    u64         recordSize    = TeraRecordSize;
    std::string bookPath;
    u64         bookSize = 0;
    std::string bookHash;
    std::string networkPath;
    u64         networkSize = 0;
    std::string networkHash;
    u64         producerSize = 0;
    std::string producerHash;
    std::string sourceCommit;
    bool        sourceDirty = true;
    u64         resumeCount = 0;
};

struct BufferedRecord {
    TeraRecord  record{};
    std::string fen;
    int         score = 0;
    int         stm   = 0;
};

struct Candidate {
    Move move  = Move::none();
    int  score = 0;
    bool mate  = false;
    bool valid = false;
};

struct WorkerStats {
    usize              shardId         = 0;
    u64                target          = 0;
    u64                records         = 0;
    u64                sourcePositions = 0;
    u64                games           = 0;
    u64                whiteWins       = 0;
    u64                blackWins       = 0;
    u64                draws           = 0;
    u64                seed            = 0;
    usize              debugTarget     = 0;
    double             seconds         = 0.0;
    std::map<u64, u64> recordsPerGame;
    std::string        error;
};

struct ShardInfo {
    usize                 id = 0;
    std::filesystem::path path;
    u64                   records         = 0;
    u64                   sourcePositions = 0;
};

// --------------------------------------------------------------------------
// Bit plumbing (LSB-first, exactly as tools/terabin.py)
// --------------------------------------------------------------------------

struct BitWriter {
    explicit BitWriter(u8* bytes) :
        data(bytes) {}

    void put(u64 value, int bits) {
        for (int i = 0; i < bits; ++i, ++cursor)
            if (value & (u64(1) << i))
                data[cursor >> 3] |= u8(1u << (cursor & 7));
    }

    void put_signed(int value, int bits) { put(u64(value) & ((u64(1) << bits) - 1), bits); }

    u8* data;
    int cursor = 0;
};

void overwrite_bits(u8* data, int cursor, u64 value, int bits) {
    for (int i = 0; i < bits; ++i, ++cursor)
    {
        const u8 mask = u8(1u << (cursor & 7));
        data[cursor >> 3] &= u8(~mask);
        if (value & (u64(1) << i))
            data[cursor >> 3] |= mask;
    }
}

void put_le(u8* destination, u64 value, usize bytes) {
    for (usize i = 0; i < bytes; ++i)
        destination[i] = u8(value >> (8 * i));
}

u64 get_le(const u8* source, usize bytes) {
    u64 value = 0;
    for (usize i = 0; i < bytes; ++i)
        value |= u64(source[i]) << (8 * i);
    return value;
}

void write_header(std::ostream& file, u64 count, u64 sourceCount, u64 flags = 0) {
    std::array<u8, TeraHeaderSize> header{};
    header[0] = 'T';
    header[1] = 'C';
    header[2] = '0';
    header[3] = '1';
    put_le(header.data() + 4, 1, 2);               // version
    put_le(header.data() + 6, TeraRecordSize, 2);  // record_size
    put_le(header.data() + 8, count, 8);
    put_le(header.data() + 16, sourceCount, 8);
    put_le(header.data() + 24, flags, 8);
    file.write(reinterpret_cast<const char*>(header.data()), std::streamsize(header.size()));
}

bool read_header(std::istream& file, u64& count, u64& sourceCount, u64& flags, std::string& error) {
    std::array<u8, TeraHeaderSize> header{};
    file.read(reinterpret_cast<char*>(header.data()), std::streamsize(header.size()));
    if (file.gcount() != std::streamsize(header.size()))
    {
        error = "truncated tera-bin header";
        return false;
    }
    if (header[0] != 'T' || header[1] != 'C' || header[2] != '0' || header[3] != '1'
        || get_le(header.data() + 4, 2) != 1 || get_le(header.data() + 6, 2) != TeraRecordSize)
    {
        error = "unsupported tera-bin header";
        return false;
    }
    count       = get_le(header.data() + 8, 8);
    sourceCount = get_le(header.data() + 16, 8);
    flags       = get_le(header.data() + 24, 8);
    return true;
}

// --------------------------------------------------------------------------
// Record packing
// --------------------------------------------------------------------------

// 6-bit piece code: 1 + type_idx (white) / 27 + type_idx (black), with
// type_idx the alphabetical index of the FEN-TSF letter (a=0 ... z=25).
int piece_code(Piece pc) {
    const int idx = PieceTypeToChar[type_of(pc)] - 'a';
    assert(idx >= 0 && idx < 26);
    return color_of(pc) == WHITE ? 1 + idx : 27 + idx;
}

// tera-bin move field: dest 0-7, origin 8-15, promotion piece code 16-21,
// type 22-23 (0 normal, 1 e.p., 2 king jump), 24-31 zero.  The engine's Move
// keeps the promotion as a PieceType in bits 16-20 and the type in 21-22, so
// both fields need translating.
u64 tera_move(const Position& pos, Move m) {
    if (!m)
        return 0;
    const u64 dest   = u64(m.to_sq());
    const u64 origin = u64(m.from_sq());
    const u64 promo =
      m.is_promotion() ? u64(piece_code(make_piece(pos.side_to_move(), m.promotion_type()))) : 0;
    const u64 kind = m.type_of() == EN_PASSANT ? 1 : m.type_of() == KING_JUMP ? 2 : 0;
    return dest | (origin << 8) | (promo << 16) | (kind << 22);
}

TeraRecord pack_record(const Position& pos, int score, Move move, int ply) {
    TeraRecord record{};

    // Occupancy (bytes 0-31) and 6-bit piece codes (bytes 32-127), ascending
    // square order.  Byte s >> 3 / bit s & 7 is exactly the little-endian bit
    // s & 63 of occupancy word s >> 6.
    BitWriter pieces(record.data() + 32);
    int       occupied = 0;
    for (Square s = SQUARE_ZERO; s < SQUARE_NB; ++s)
    {
        const Piece pc = pos.piece_on(s);
        if (pc == NO_PIECE)
            continue;
        record[usize(s) >> 3] |= u8(1u << (int(s) & 7));
        pieces.put(u64(piece_code(pc)), 6);
        ++occupied;
    }
    assert(occupied <= TeraMaxPieces);
    (void) occupied;

    BitWriter bits(record.data() + 128);
    bits.put(pos.side_to_move() == BLACK, 1);
    bits.put(u64(pos.king_jump_rights() & (WHITE_JUMP | BLACK_JUMP)), 2);
    bits.put(pos.ep_square() == SQ_NONE ? 0 : u64(int(pos.ep_square()) + 1), 9);
    bits.put(u64(std::min(pos.rule50_count(), 100)), 7);
    bits.put(u64(1 + (pos.game_ply() - (pos.side_to_move() == BLACK)) / 2), 16);
    bits.put_signed(std::clamp(score, -ScoreLimit, ScoreLimit), 16);
    bits.put(tera_move(pos, move), 32);
    bits.put(u64(ply), 16);
    bits.put(ResultUnknown, 2);  // patched once the game has a result

    assert(bits.cursor == TeraMetaBits);
    return record;
}

// POV of the side to move: 0 loss, 1 draw, 2 win.
u64 stm_result(int whiteResult, int stm) {
    if (whiteResult == 0)
        return 1;
    const bool whiteWon = whiteResult > 0;
    const bool stmWhite = stm == 0;
    return whiteWon == stmWhite ? 2 : 0;
}

void set_result(TeraRecord& record, u64 result) {
    overwrite_bits(record.data() + 128, TeraResultBit, result, 2);
}

// --------------------------------------------------------------------------
// Search capture
// --------------------------------------------------------------------------

int score_to_cp(const Score& score, bool& mate) {
    mate = false;
    if (score.is<Score::InternalUnits>())
        return std::clamp(score.get<Score::InternalUnits>().value, -ScoreLimit, ScoreLimit);
    if (score.is<Score::Mate>())
    {
        mate = true;
        return score.get<Score::Mate>().plies >= 0 ? ScoreLimit : -ScoreLimit;
    }
    const auto tb = score.get<Score::Tablebase>();
    return tb.win ? 20000 - std::abs(tb.plies) : -20000 + std::abs(tb.plies);
}

struct SearchCapture {
    explicit SearchCapture(usize multiPv) :
        lines(multiPv) {}

    void reset(const Position& p) {
        pos = &p;
        std::fill(lines.begin(), lines.end(), Candidate{});
    }

    void update(const Engine::InfoFull& info) {
        if (!pos || info.multiPV == 0 || info.multiPV > lines.size())
            return;

        std::istringstream pv{std::string(info.pv)};
        std::string        moveText;
        pv >> moveText;
        if (moveText.empty())
            return;

        Candidate& candidate = lines[info.multiPV - 1];
        candidate.move       = UCIEngine::to_move(*pos, moveText);
        candidate.score      = score_to_cp(info.score, candidate.mate);
        candidate.valid      = bool(candidate.move);
    }

    const Position*        pos = nullptr;
    std::vector<Candidate> lines;
};

// --------------------------------------------------------------------------
// Random streams
// --------------------------------------------------------------------------

u64 bounded_rand(PRNG& rng, u64 bound) {
    assert(bound > 0);
    const u64 threshold = u64(-bound) % bound;
    u64       value;
    do
        value = rng.rand<u64>();
    while (value < threshold);
    return value % bound;
}

// Disjoint streams: (resumeNumber, threadId) is a unique 64-bit stream id, so
// no worker of any session ever replays another worker's sequence.
u64 splitmix_seed(u64 seed, u64 resumeNumber, usize threadId) {
    assert(resumeNumber <= std::numeric_limits<std::uint32_t>::max());
    assert(u64(threadId) <= std::numeric_limits<std::uint32_t>::max());
    const u64 stream = (resumeNumber << 32) | u64(threadId);
    u64       z      = seed + 0x9E3779B97F4A7C15ULL * (stream + 1);
    z                = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z                = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    z ^= z >> 31;
    return z ? z : 1;
}

std::vector<u8> random_move_flags(const Params& params, PRNG& rng) {
    std::vector<int> candidates;
    candidates.reserve(usize(params.randomMoveMaxPly - params.randomMoveMinPly + 1));
    for (int ply = std::max(0, params.randomMoveMinPly - 1); ply < params.randomMoveMaxPly; ++ply)
        candidates.push_back(ply);

    const int selected = std::min(params.randomMoveCount, int(candidates.size()));
    for (int i = 0; i < selected; ++i)
    {
        const int j = i + int(bounded_rand(rng, u64(candidates.size() - usize(i))));
        std::swap(candidates[i], candidates[j]);
    }

    std::vector<u8> flags(usize(params.randomMoveMaxPly), 0);
    for (int i = 0; i < selected; ++i)
        flags[usize(candidates[i])] = 1;
    return flags;
}

// --------------------------------------------------------------------------
// Paths, book and resume metadata
// --------------------------------------------------------------------------

std::filesystem::path with_suffix(std::filesystem::path path, const std::string& suffix) {
    path += suffix;
    return path;
}

std::string trim(std::string value) {
    const auto notSpace = [](unsigned char c) { return !std::isspace(c); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), notSpace));
    value.erase(std::find_if(value.rbegin(), value.rend(), notSpace).base(), value.end());
    return value;
}

std::filesystem::path normalized_path(const std::filesystem::path& path) {
    std::error_code ec;
    auto            normalized = std::filesystem::weakly_canonical(path, ec);
    if (!ec)
        return normalized;
    normalized = std::filesystem::absolute(path, ec);
    return ec ? path.lexically_normal() : normalized.lexically_normal();
}

std::string portable_path(const std::filesystem::path& path) { return path.generic_string(); }

bool authenticate_file(const std::filesystem::path& path,
                       const std::string&           expectedSha256,
                       std::string_view             label,
                       u64&                         size,
                       std::string&                 error) {
    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec) || ec)
    {
        error = std::string(label) + " is not a regular file: " + path.string();
        return false;
    }
    size = std::filesystem::file_size(path, ec);
    if (ec)
    {
        error = "cannot read " + std::string(label) + " size: " + ec.message();
        return false;
    }

    std::string observed;
    if (!Integrity::sha256_file(path, observed, error))
        return false;
    if (observed != expectedSha256)
    {
        error = std::string(label) + " SHA-256 mismatch: expected " + expectedSha256 + ", got "
              + observed;
        return false;
    }
    return true;
}

// Generation identity: everything that changes the bytes produced.  Thread
// count and the --resume flag are deliberately excluded so that a resumed
// session may use a different worker count.
std::string identity_command(const Params&                params,
                             const std::filesystem::path& book,
                             const std::filesystem::path& network,
                             const std::filesystem::path& out) {
    std::ostringstream command;
    command << "datagen out " << std::quoted(portable_path(out)) << " count " << params.count
            << " nodes " << params.nodes << " seed " << params.seed << " book "
            << std::quoted(params.book.empty() ? std::string("NONE") : portable_path(book))
            << " book_sha256 " << params.bookSha256 << " network "
            << std::quoted(portable_path(network)) << " network_sha256 " << params.networkSha256
            << " producer_sha256 " << params.producerSha256 << " source_commit "
            << params.sourceCommit << " source_dirty " << int(params.sourceDirty)
            << " random_move_count " << params.randomMoveCount << " random_move_min_ply "
            << params.randomMoveMinPly << " random_move_max_ply " << params.randomMoveMaxPly
            << " random_multi_pv " << params.randomMultiPv << " random_multi_pv_diff "
            << params.randomMultiPvDiff << " write_min_ply " << params.writeMinPly << " eval_limit "
            << params.evalLimit << " filter_captures " << int(params.filterCaptures)
            << " filter_checks " << int(params.filterChecks) << " --debug-sample "
            << params.debugSample;
    return command.str();
}

std::string full_command(const Params&                params,
                         const std::filesystem::path& book,
                         const std::filesystem::path& network,
                         const std::filesystem::path& out) {
    std::ostringstream command;
    command << identity_command(params, book, network, out) << " threads " << params.threads;
    if (params.resume)
        command << " --resume";
    return command.str();
}

bool write_resume_metadata(const std::filesystem::path& out,
                           const ResumeMetadata&        metadata,
                           std::string&                 error) {
    const auto path      = with_suffix(out, ".resume");
    const auto temporary = with_suffix(out, ".resume.tmp");
    const auto previous  = with_suffix(out, ".resume.prev");

    std::ofstream file(temporary, std::ios::trunc);
    if (!file)
    {
        error = "cannot write resume metadata " + temporary.string();
        return false;
    }
    file << "schema " << std::quoted("terachess-datagen-resume") << '\n'
         << "meta_version " << metadata.metaVersion << '\n'
         << "command " << std::quoted(metadata.command) << '\n'
         << "last_command " << std::quoted(metadata.lastCommand) << '\n'
         << "format " << std::quoted(metadata.format) << '\n'
         << "format_version " << metadata.formatVersion << '\n'
         << "record_size " << metadata.recordSize << '\n'
         << "book_path " << std::quoted(metadata.bookPath) << '\n'
         << "book_size " << metadata.bookSize << '\n'
         << "book_hash " << std::quoted(metadata.bookHash) << '\n'
         << "network_path " << std::quoted(metadata.networkPath) << '\n'
         << "network_size " << metadata.networkSize << '\n'
         << "network_hash " << std::quoted(metadata.networkHash) << '\n'
         << "producer_size " << metadata.producerSize << '\n'
         << "producer_hash " << std::quoted(metadata.producerHash) << '\n'
         << "source_commit " << std::quoted(metadata.sourceCommit) << '\n'
         << "source_dirty " << int(metadata.sourceDirty) << '\n'
         << "resume_count " << metadata.resumeCount << '\n';
    file.close();
    if (!file)
    {
        error = "failed while writing resume metadata " + temporary.string();
        return false;
    }

    std::error_code ec;
    std::filesystem::remove(previous, ec);
    ec.clear();
    const bool hadCurrent = std::filesystem::exists(path, ec);
    if (ec)
    {
        error = "cannot inspect resume metadata: " + ec.message();
        return false;
    }
    if (hadCurrent)
    {
        std::filesystem::rename(path, previous, ec);
        if (ec)
        {
            error = "cannot rotate resume metadata: " + ec.message();
            return false;
        }
    }
    std::filesystem::rename(temporary, path, ec);
    if (ec)
    {
        if (hadCurrent)
        {
            std::error_code restoreError;
            std::filesystem::rename(previous, path, restoreError);
        }
        error = "cannot publish resume metadata: " + ec.message();
        return false;
    }
    std::filesystem::remove(previous, ec);
    return true;
}

bool parse_resume_metadata_file(const std::filesystem::path& path,
                                ResumeMetadata&              metadata,
                                std::string&                 error) {
    std::ifstream file(path);
    if (!file)
    {
        error = "cannot open " + path.string();
        return false;
    }

    std::set<std::string> seen;
    std::string           line;
    usize                 lineNumber = 0;
    while (std::getline(file, line))
    {
        ++lineNumber;
        std::istringstream input(line);
        std::string        key;
        if (!(input >> key))
            continue;
        if (!seen.insert(key).second)
        {
            error = "duplicate resume metadata field '" + key + "'";
            return false;
        }

        bool ok = true;
        if (key == "schema")
        {
            std::string schema;
            ok = bool(input >> std::quoted(schema)) && schema == "terachess-datagen-resume";
        }
        else if (key == "meta_version")
            ok = bool(input >> metadata.metaVersion);
        else if (key == "command")
            ok = bool(input >> std::quoted(metadata.command));
        else if (key == "last_command")
            ok = bool(input >> std::quoted(metadata.lastCommand));
        else if (key == "format")
            ok = bool(input >> std::quoted(metadata.format));
        else if (key == "format_version")
            ok = bool(input >> metadata.formatVersion);
        else if (key == "record_size")
            ok = bool(input >> metadata.recordSize);
        else if (key == "book_path")
            ok = bool(input >> std::quoted(metadata.bookPath));
        else if (key == "book_size")
            ok = bool(input >> metadata.bookSize);
        else if (key == "book_hash")
            ok = bool(input >> std::quoted(metadata.bookHash));
        else if (key == "network_path")
            ok = bool(input >> std::quoted(metadata.networkPath));
        else if (key == "network_size")
            ok = bool(input >> metadata.networkSize);
        else if (key == "network_hash")
            ok = bool(input >> std::quoted(metadata.networkHash));
        else if (key == "producer_size")
            ok = bool(input >> metadata.producerSize);
        else if (key == "producer_hash")
            ok = bool(input >> std::quoted(metadata.producerHash));
        else if (key == "source_commit")
            ok = bool(input >> std::quoted(metadata.sourceCommit));
        else if (key == "source_dirty")
        {
            int dirty = -1;
            ok        = bool(input >> dirty) && (dirty == 0 || dirty == 1);
            if (ok)
                metadata.sourceDirty = dirty != 0;
        }
        else if (key == "resume_count")
            ok = bool(input >> metadata.resumeCount);
        else
        {
            error = "unknown resume metadata field '" + key + "'";
            return false;
        }

        input >> std::ws;
        if (!ok || !input.eof())
        {
            error =
              "invalid resume metadata field '" + key + "' on line " + std::to_string(lineNumber);
            return false;
        }
    }
    if (!file.eof())
    {
        error = "failed while reading " + path.string();
        return false;
    }

    static const std::array<const char*, 18> Required = {
      "schema",       "meta_version",  "command",       "last_command",  "format",
      "format_version", "record_size", "book_path",     "book_size",     "book_hash",
      "network_path", "network_size",  "network_hash",  "producer_size", "producer_hash",
      "source_commit", "source_dirty", "resume_count"};
    for (const char* key : Required)
        if (!seen.count(key))
        {
            error = "resume metadata is missing field '" + std::string(key) + "'";
            return false;
        }
    return true;
}

bool load_resume_metadata(const std::filesystem::path& out,
                          ResumeMetadata&              metadata,
                          std::string&                 error) {
    const auto       path       = with_suffix(out, ".resume");
    const auto       temporary  = with_suffix(out, ".resume.tmp");
    const auto       previous   = with_suffix(out, ".resume.prev");
    const std::array candidates = {path, temporary, previous};
    std::string      lastError;
    for (const auto& candidate : candidates)
    {
        std::error_code ec;
        if (!std::filesystem::exists(candidate, ec) || ec)
            continue;
        ResumeMetadata parsed;
        if (!parse_resume_metadata_file(candidate, parsed, lastError))
            continue;

        if (candidate != path)
        {
            std::filesystem::remove(path, ec);
            ec.clear();
            std::filesystem::rename(candidate, path, ec);
            if (ec)
            {
                error = "cannot recover resume metadata: " + ec.message();
                return false;
            }
        }
        metadata = std::move(parsed);
        std::filesystem::remove(temporary, ec);
        std::filesystem::remove(previous, ec);
        return true;
    }
    error = "cannot read valid resume metadata " + path.string();
    if (!lastError.empty())
        error += ": " + lastError;
    return false;
}

bool validate_resume_metadata(const ResumeMetadata& metadata,
                              const std::string&    identity,
                              const Params&         params,
                              std::string&          error) {
    if (metadata.metaVersion != ResumeMetaVersion || metadata.format != "tera-bin"
        || metadata.formatVersion != 1 || metadata.recordSize != TeraRecordSize)
    {
        error = "resume metadata uses an unsupported format/version";
        return false;
    }
    if (metadata.command != identity)
    {
        error = "resume metadata mismatch: requested [" + identity + "], stored ["
              + metadata.command + "]";
        return false;
    }
    if (params.bookSize != metadata.bookSize || params.bookSha256 != metadata.bookHash)
    {
        error = "resume metadata mismatch for book contents: requested " + params.bookSha256 + " ("
              + std::to_string(params.bookSize) + " bytes), stored " + metadata.bookHash + " ("
              + std::to_string(metadata.bookSize) + " bytes)";
        return false;
    }
    if (params.networkSize != metadata.networkSize || params.networkSha256 != metadata.networkHash
        || params.producerSize != metadata.producerSize
        || params.producerSha256 != metadata.producerHash
        || params.sourceCommit != metadata.sourceCommit || params.sourceDirty != metadata.sourceDirty)
    {
        error = "resume metadata mismatch for authenticated producer/network/source identity";
        return false;
    }
    if (metadata.resumeCount >= std::numeric_limits<std::uint32_t>::max())
    {
        error = "resume counter exhausted";
        return false;
    }
    return true;
}

bool load_book(const std::filesystem::path& path,
               std::vector<std::string>&    positions,
               std::string&                 error) {
    std::ifstream file(path);
    if (!file)
    {
        error = "cannot open book " + path.string();
        return false;
    }

    std::string line;
    while (std::getline(file, line))
    {
        if (positions.empty() && line.size() >= 3 && u8(line[0]) == 0xEF && u8(line[1]) == 0xBB
            && u8(line[2]) == 0xBF)
            line.erase(0, 3);
        line = trim(std::move(line));
        if (!line.empty() && line[0] != '#')
            positions.push_back(std::move(line));
    }

    if (positions.empty())
    {
        error = "book contains no positions";
        return false;
    }
    return true;
}

// --------------------------------------------------------------------------
// Argument parsing
// --------------------------------------------------------------------------

bool read_path(std::istream& args, std::filesystem::path& value, bool allowNone) {
    std::string text;
    args >> std::ws;
    if (args.peek() == '"')
    {
        args.get();
        if (!std::getline(args, text, '"'))
            return false;
    }
    else if (!(args >> text))
        return false;

    // std::quoted treats every backslash as an escape. That silently turns a
    // quoted Windows path such as C:\Users\... into C:Users... and can collapse
    // the whole path into one overlong filename. A double quote is not legal
    // inside a Windows filename, so the literal-delimiter parser above is both
    // lossless for worker paths and intentionally simple.
    if (allowNone && (text == "NONE" || text == "none"))
    {
        value.clear();
        return true;
    }
    value = path_from_utf8(text);
    return !value.empty();
}

template<typename T>
bool read_value(std::istream& args, T& value) {
    return bool(args >> value);
}

bool parse_params(std::istream& args, Params& params, std::string& error) {
    bool sawOut = false, sawCount = false, sawBook = false, sawBookSha256 = false;
    bool sawNetwork = false, sawNetworkSha256 = false, sawProducerSha256 = false;

    const auto first = [&](bool& seen, const std::string& name) {
        if (seen)
        {
            error = "duplicate option '" + name + "'";
            return false;
        }
        seen = true;
        return true;
    };

    std::string token;
    while (args >> token)
    {
        bool ok = true;
        if (token == "out")
            ok = first(sawOut, token) && read_path(args, params.out, false);
        else if (token == "count")
            ok = first(sawCount, token) && read_value(args, params.count);
        else if (token == "nodes")
            ok = read_value(args, params.nodes);
        else if (token == "threads")
            ok = read_value(args, params.threads);
        else if (token == "seed")
            ok = read_value(args, params.seed);
        else if (token == "book")
            ok = first(sawBook, token) && read_path(args, params.book, true);
        else if (token == "book_sha256")
            ok = first(sawBookSha256, token) && read_value(args, params.bookSha256);
        else if (token == "network")
            ok = first(sawNetwork, token) && read_path(args, params.network, true);
        else if (token == "network_sha256")
            ok = first(sawNetworkSha256, token) && read_value(args, params.networkSha256);
        else if (token == "producer_sha256")
            ok = first(sawProducerSha256, token) && read_value(args, params.producerSha256);
        else if (token == "random_move_count")
            ok = read_value(args, params.randomMoveCount);
        else if (token == "random_move_min_ply")
            ok = read_value(args, params.randomMoveMinPly);
        else if (token == "random_move_max_ply")
            ok = read_value(args, params.randomMoveMaxPly);
        else if (token == "random_multi_pv")
            ok = read_value(args, params.randomMultiPv);
        else if (token == "random_multi_pv_diff")
            ok = read_value(args, params.randomMultiPvDiff);
        else if (token == "write_min_ply")
            ok = read_value(args, params.writeMinPly);
        else if (token == "eval_limit")
            ok = read_value(args, params.evalLimit);
        else if (token == "filter_captures" || token == "filter_checks")
        {
            int flag = -1;
            ok       = read_value(args, flag) && (flag == 0 || flag == 1);
            if (ok && token == "filter_captures")
                params.filterCaptures = flag;
            else if (ok)
                params.filterChecks = flag;
        }
        else if (token == "--debug-sample")
            ok = read_value(args, params.debugSample);
        else if (token == "--resume")
            params.resume = true;
        else
        {
            error = "unknown option '" + token + "'";
            return false;
        }

        if (!ok)
        {
            if (error.empty())
                error = "invalid or missing value for '" + token + "'";
            return false;
        }
    }

    params.bookSha256     = Integrity::normalize_sha256(std::move(params.bookSha256));
    params.networkSha256  = Integrity::normalize_sha256(std::move(params.networkSha256));
    params.producerSha256 = Integrity::normalize_sha256(std::move(params.producerSha256));

    const bool sourceCommitValid =
      params.sourceCommit.size() == 40
      && std::all_of(params.sourceCommit.begin(), params.sourceCommit.end(), [](unsigned char c) {
             return std::isxdigit(c) != 0;
         })
      && params.sourceCommit != "0000000000000000000000000000000000000000";

    if (!sawOut || params.out.empty())
        error = "out is required";
    else if (!sawCount)
        error = "count is required";
    else if (!sawBook || !sawBookSha256 || !sawNetwork || !sawNetworkSha256
             || !sawProducerSha256)
        error = "authenticated datagen requires book, book_sha256, network, network_sha256, and "
                "producer_sha256";
    else if ((params.book.empty()) != (params.bookSha256 == "none")
             || (!params.book.empty() && !Integrity::is_sha256(params.bookSha256)))
        error = "book and book_sha256 must be NONE together or identify one authenticated file";
    else if (params.network.empty() || !Integrity::is_sha256(params.networkSha256))
        error = "network must identify an authenticated file and network_sha256 must be 64 hex "
                "digits";
    else if (!Integrity::is_sha256(params.producerSha256))
        error = "producer_sha256 must contain 64 hex digits";
    else if (!sourceCommitValid)
        error = "build lacks an authenticated 40-hex source commit";
    else if (!params.count)
        error = "count must be greater than zero";
    else if (!params.nodes)
        error = "nodes must be greater than zero";
    else if (params.count > (std::numeric_limits<u64>::max() - TeraHeaderSize) / TeraRecordSize)
        error = "count is too large for the tera-bin file size";
    else if (!params.threads)
        error = "threads must be greater than zero";
    else if (u64(params.threads) > std::numeric_limits<std::uint32_t>::max())
        error = "threads exceed the resume stream-id limit";
    else if (params.randomMultiPv < 1 || params.randomMultiPv > MAX_MOVES)
        error = "random_multi_pv must be in [1, MAX_MOVES]";
    else if (params.randomMultiPvDiff < 0)
        error = "random_multi_pv_diff must be non-negative";
    else if (params.randomMoveCount < 0 || params.randomMoveMinPly < 1
             || params.randomMoveMaxPly < params.randomMoveMinPly)
        error = "invalid random move count/range";
    else if (params.randomMoveMaxPly > MaxGamePlies)
        error = "random_move_max_ply exceeds the hard game-length cap";
    else if (params.writeMinPly < 0)
        error = "write_min_ply must be non-negative";
    else if (params.evalLimit < 1 || params.evalLimit > ScoreLimit)
        error = "eval_limit must be in [1, 32000]";
    else if (u64(params.debugSample) > params.count)
        error = "--debug-sample cannot exceed count";
    else if (!params.seed)
        error = "seed must be non-zero";

    return error.empty();
}

// --------------------------------------------------------------------------
// Shards
// --------------------------------------------------------------------------

bool parse_shard_id(const std::string& filename, const std::string& prefix, usize& id) {
    if (filename.size() <= prefix.size() || filename.compare(0, prefix.size(), prefix) != 0)
        return false;
    const std::string_view suffix(filename.data() + prefix.size(), filename.size() - prefix.size());
    u64                    value = 0;
    for (char c : suffix)
    {
        if (c < '0' || c > '9')
            return false;
        const u64 digit = u64(c - '0');
        if (value > (std::numeric_limits<u64>::max() - digit) / 10)
            return false;
        value = value * 10 + digit;
    }
    if (value > std::numeric_limits<usize>::max())
        return false;
    id = usize(value);
    return true;
}

bool inspect_shard(ShardInfo& shard, bool sanitize, std::string& error) {
    std::error_code ec;
    u64             size = std::filesystem::file_size(shard.path, ec);
    if (ec)
    {
        error = "cannot size shard " + shard.path.string();
        return false;
    }
    if (size < TeraHeaderSize)
    {
        if (!sanitize)
        {
            error = "truncated shard header in " + shard.path.string();
            return false;
        }
        std::ofstream reset(shard.path, std::ios::binary | std::ios::trunc);
        write_header(reset, 0, 0);
        reset.close();
        if (!reset)
        {
            error = "cannot reset header-truncated shard " + shard.path.string();
            return false;
        }
        shard.records         = 0;
        shard.sourcePositions = 0;
        sync_cout << "info string datagen resume: reset header-truncated shard "
                  << shard.path.string() << " (" << size << " byte(s)) as empty" << sync_endl;
        return true;
    }

    std::ifstream input(shard.path, std::ios::binary);
    if (!input)
    {
        error = "cannot read shard " + shard.path.string();
        return false;
    }
    u64         declaredCount = 0;
    u64         flags         = 0;
    std::string headerError;
    if (!read_header(input, declaredCount, shard.sourcePositions, flags, headerError))
    {
        error = "invalid shard " + shard.path.string() + ": " + headerError;
        return false;
    }
    if (flags)
    {
        error = "invalid shard " + shard.path.string() + ": unsupported non-zero flags";
        return false;
    }
    input.close();

    u64       payload = size - TeraHeaderSize;
    const u64 tail    = payload % TeraRecordSize;
    if (tail)
    {
        if (!sanitize)
        {
            error = "truncated shard " + shard.path.string() + " has " + std::to_string(tail)
                  + " trailing byte(s)";
            return false;
        }
        std::filesystem::resize_file(shard.path, size - tail, ec);
        if (ec)
        {
            error = "cannot truncate shard " + shard.path.string() + ": " + ec.message();
            return false;
        }
        payload -= tail;
        sync_cout << "info string datagen resume: truncated shard " << shard.path.string() << " by "
                  << tail << " byte(s) to the last complete 144-byte record" << sync_endl;
    }
    shard.records = payload / TeraRecordSize;

    const bool countMismatch = declaredCount != shard.records;
    const bool sourceMissing = shard.sourcePositions < shard.records;
    if (countMismatch || sourceMissing)
    {
        if (!sanitize)
        {
            error = "shard header count/source mismatch in " + shard.path.string();
            return false;
        }
        if (sourceMissing)
            shard.sourcePositions = shard.records;
        std::fstream update(shard.path, std::ios::binary | std::ios::in | std::ios::out);
        if (!update)
        {
            error = "cannot repair shard header " + shard.path.string();
            return false;
        }
        write_header(update, shard.records, shard.sourcePositions);
        update.close();
        if (!update)
        {
            error = "failed while repairing shard header " + shard.path.string();
            return false;
        }
        if (countMismatch)
            sync_cout << "info string datagen resume: repaired shard " << shard.path.string()
                      << " header count from " << declaredCount << " to " << shard.records
                      << sync_endl;
    }
    return true;
}

bool discover_shards(const std::filesystem::path& out,
                     bool                         sanitize,
                     std::vector<ShardInfo>&      shards,
                     std::string&                 error) {
    shards.clear();
    auto parent = out.parent_path();
    if (parent.empty())
        parent = ".";
    const std::string prefix = out.filename().string() + ".";

    std::error_code ec;
    if (!std::filesystem::exists(parent, ec))
        return !ec;
    std::filesystem::directory_iterator iterator(parent, ec);
    if (ec)
    {
        error = "cannot enumerate output shards: " + ec.message();
        return false;
    }
    for (const auto& entry : iterator)
    {
        if (!entry.is_regular_file(ec))
        {
            if (ec)
            {
                error = "cannot inspect output shard directory: " + ec.message();
                return false;
            }
            continue;
        }
        usize id = 0;
        if (parse_shard_id(entry.path().filename().string(), prefix, id))
            shards.push_back({id, entry.path(), 0, 0});
    }
    std::sort(shards.begin(), shards.end(),
              [](const auto& left, const auto& right) { return left.id < right.id; });
    for (usize i = 1; i < shards.size(); ++i)
        if (shards[i - 1].id == shards[i].id)
        {
            error = "duplicate numeric shard id " + std::to_string(shards[i].id);
            return false;
        }
    for (auto& shard : shards)
        if (!inspect_shard(shard, sanitize, error))
            return false;
    return true;
}

// A debug line is "<FEN> | <score> | <result>" with result in [0, 3].
bool valid_debug_record_line(const std::string& line) {
    const auto first = line.find(" | ");
    if (first == std::string::npos || first == 0)
        return false;
    const auto second = line.find(" | ", first + 3);
    if (second == std::string::npos)
        return false;

    int                score  = 0;
    int                result = 0;
    std::string        extra;
    std::istringstream scoreText(line.substr(first + 3, second - first - 3));
    std::istringstream resultText(line.substr(second + 3));
    return bool(scoreText >> score) && !(scoreText >> extra) && bool(resultText >> result)
        && !(resultText >> extra) && result >= 0 && result <= 3;
}

// Usable debug lines of one shard: the merge only ever consumes a prefix of
// at most `records` lines, so anything beyond that is already ignored.
u64 count_shard_debug_records(const ShardInfo& shard) {
    std::ifstream debug(with_suffix(shard.path, ".debug"));
    std::string   line;
    u64           lines = 0;
    while (lines < shard.records && std::getline(debug, line))
        if (!line.empty() && valid_debug_record_line(line))
            ++lines;
    return lines;
}

bool verify_merged_output(const std::filesystem::path& path,
                          u64                          count,
                          u64                          sourcePositions,
                          std::string&                 error) {
    std::ifstream input(path, std::ios::binary);
    if (!input)
    {
        error = "cannot verify merged output " + path.string();
        return false;
    }
    u64 headerCount  = 0;
    u64 headerSource = 0;
    u64 flags        = 0;
    if (!read_header(input, headerCount, headerSource, flags, error))
    {
        error = "cannot verify merged output: " + error;
        return false;
    }
    input.close();
    std::error_code ec;
    const u64       size     = std::filesystem::file_size(path, ec);
    const u64       expected = TeraHeaderSize + count * TeraRecordSize;
    if (ec || headerCount != count || headerSource != sourcePositions || flags || size != expected)
    {
        error = "merged output verification failed (header/size mismatch)";
        return false;
    }
    return true;
}

// --------------------------------------------------------------------------
// Sidecars
// --------------------------------------------------------------------------

bool write_metadata(const Params&                   params,
                    const std::vector<WorkerStats>& stats,
                    u64                             sourcePositions,
                    u64                             games,
                    u64                             survivorRecords,
                    double                          seconds,
                    std::string&                    error) {
    const auto    path      = with_suffix(params.out, ".meta.json");
    const auto    temporary = with_suffix(params.out, ".meta.json.tmp");
    std::ofstream file(temporary, std::ios::trunc);
    if (!file)
    {
        error = "cannot write metadata " + temporary.string();
        return false;
    }

    u64 sessionRecords = 0;
    u64 whiteWins = 0, blackWins = 0, draws = 0;
    for (const auto& worker : stats)
    {
        sessionRecords += worker.records;
        whiteWins += worker.whiteWins;
        blackWins += worker.blackWins;
        draws += worker.draws;
    }
    const bool   exactGameStats     = survivorRecords == 0;
    const double positionsPerSecond = double(sessionRecords) / std::max(seconds, 1e-9);
    const double perThread          = positionsPerSecond / double(params.threads);

    std::map<u64, u64> recordsPerGame;
    for (const auto& worker : stats)
        for (const auto& [records, gameCount] : worker.recordsPerGame)
            recordsPerGame[records] += gameCount;
    const u64 zeroRecordGames = recordsPerGame.count(0) ? recordsPerGame.at(0) : 0;

    file << std::fixed << std::setprecision(6) << "{\n"
         << "  \"format\": \"tera-bin\",\n"
         << "  \"version\": 1,\n"
         << "  \"provenance_schema\": \"terachess-datagen-provenance-v1\",\n"
         << "  \"source_commit\": \"" << params.sourceCommit << "\",\n"
         << "  \"source_dirty\": " << (params.sourceDirty ? "true" : "false") << ",\n"
         << "  \"producer_sha256\": \"" << params.producerSha256 << "\",\n"
         << "  \"producer_bytes\": " << params.producerSize << ",\n"
         << "  \"network_sha256\": \"" << params.networkSha256 << "\",\n"
         << "  \"network_bytes\": " << params.networkSize << ",\n"
         << "  \"network_arch_hash\": \"" << TeraNNUE::descriptor_hash_hex() << "\",\n"
         << "  \"book_sha256\": \"" << params.bookSha256 << "\",\n"
         << "  \"book_bytes\": " << params.bookSize << ",\n"
         << "  \"record_size\": " << TeraRecordSize << ",\n"
         << "  \"records\": " << params.count << ",\n"
         << "  \"source_positions\": " << sourcePositions << ",\n"
         << "  \"resume_count\": " << params.resumeNumber << ",\n"
         << "  \"survivor_records\": " << survivorRecords << ",\n"
         << "  \"session_records\": " << sessionRecords << ",\n"
         << "  \"games\": " << games << ",\n"
         << "  \"game_results\": {\"white_win\": " << whiteWins << ", \"black_win\": " << blackWins
         << ", \"draw\": " << draws << "},\n"
         << "  \"games_with_records\": " << (games - zeroRecordGames) << ",\n"
         << "  \"zero_record_games\": " << zeroRecordGames << ",\n"
         << "  \"records_per_game_mean\": "
         << (games ? double(sessionRecords) / double(games) : 0.0) << ",\n"
         << "  \"seconds\": " << seconds << ",\n"
         << "  \"threads\": " << params.threads << ",\n"
         << "  \"nodes\": " << params.nodes << ",\n"
         << "  \"seed\": " << params.seed << ",\n"
         << "  \"positions_per_second\": " << positionsPerSecond << ",\n"
         << "  \"positions_per_second_per_thread\": " << perThread << ",\n"
         << "  \"debug_sample\": " << params.debugSample << ",\n"
         << "  \"exact_game_stats\": " << (exactGameStats ? "true" : "false") << ",\n"
         << "  \"shard_policy\": \"temporary numbered tera-bin shards; resume sessions use new "
            "ids; removed only after a verified merge\",\n"
         << "  \"records_per_game_histogram\": {";
    usize histogramIndex = 0;
    for (const auto& [records, gameCount] : recordsPerGame)
        file << (histogramIndex++ ? ", " : "") << '"' << records << "\": " << gameCount;
    file << "},\n"
         << "  \"workers\": [\n";
    for (usize i = 0; i < stats.size(); ++i)
    {
        const auto& worker = stats[i];
        file << "    {\"id\": " << worker.shardId << ", \"seed\": " << worker.seed
             << ", \"records\": " << worker.records
             << ", \"source_positions\": " << worker.sourcePositions
             << ", \"games\": " << worker.games << ", \"seconds\": " << worker.seconds << "}"
             << (i + 1 == stats.size() ? "\n" : ",\n");
    }
    file << "  ]\n}\n";
    file.close();
    if (!file)
    {
        error = "failed while writing metadata " + temporary.string();
        return false;
    }

    std::error_code ec;
    std::filesystem::remove(path, ec);
    ec.clear();
    std::filesystem::rename(temporary, path, ec);
    if (ec)
    {
        error = "cannot publish metadata: " + ec.message();
        return false;
    }
    return true;
}

bool merge_shards(const Params&                 params,
                  const std::vector<ShardInfo>& shards,
                  u64                           sourcePositions,
                  std::string&                  error) {
    u64 records = 0;
    for (const auto& shard : shards)
        records += shard.records;
    if (records != params.count)
    {
        error = "cannot merge " + std::to_string(records) + " records; target is "
              + std::to_string(params.count);
        return false;
    }

    const auto    temporary = with_suffix(params.out, ".tmp");
    std::ofstream merged(temporary, std::ios::binary | std::ios::trunc);
    if (!merged)
    {
        error = "cannot create merged output " + temporary.string();
        return false;
    }
    write_header(merged, params.count, sourcePositions);

    std::vector<char> buffer(1024 * 1024);
    for (const auto& info : shards)
    {
        std::ifstream shard(info.path, std::ios::binary);
        if (!shard)
        {
            error = "cannot read shard " + info.path.string();
            return false;
        }
        shard.seekg(std::streamoff(TeraHeaderSize));
        u64 remaining = info.records * TeraRecordSize;
        while (remaining)
        {
            const usize chunk = usize(std::min<u64>(remaining, buffer.size()));
            shard.read(buffer.data(), std::streamsize(chunk));
            if (shard.gcount() != std::streamsize(chunk))
            {
                error = "truncated shard " + info.path.string();
                return false;
            }
            merged.write(buffer.data(), std::streamsize(chunk));
            remaining -= chunk;
        }
    }
    merged.close();
    if (!merged)
    {
        error = "failed while writing merged output";
        return false;
    }
    if (!verify_merged_output(temporary, params.count, sourcePositions, error))
        return false;

    std::optional<std::filesystem::path> debugTemporary;
    std::optional<std::filesystem::path> debugFinal;
    if (params.debugSample)
    {
        debugTemporary = with_suffix(params.out, ".debug.txt.tmp");
        debugFinal     = with_suffix(params.out, ".debug.txt");
        std::ofstream debug(*debugTemporary, std::ios::trunc);
        if (!debug)
        {
            error = "cannot create debug sidecar";
            return false;
        }

        usize debugRecords = 0;
        for (const auto& info : shards)
        {
            std::ifstream input(with_suffix(info.path, ".debug"));
            std::string   line;
            u64           shardRecords = 0;
            while (std::getline(input, line))
                if (shardRecords < info.records && valid_debug_record_line(line))
                {
                    ++shardRecords;
                    if (debugRecords < params.debugSample)
                    {
                        debug << line << '\n';
                        ++debugRecords;
                    }
                }
        }
        debug.close();
        if (debugRecords != params.debugSample || !debug)
        {
            error = "debug shards did not contain the requested sample";
            return false;
        }
    }

    std::error_code ec;
    std::filesystem::rename(temporary, params.out, ec);
    if (ec)
    {
        error = "cannot publish merged output: " + ec.message();
        return false;
    }
    if (debugTemporary)
    {
        std::filesystem::rename(*debugTemporary, *debugFinal, ec);
        if (ec)
        {
            std::error_code rollbackError;
            std::filesystem::remove(params.out, rollbackError);
            error = "cannot publish debug sidecar: " + ec.message();
            return false;
        }
    }
    if (!verify_merged_output(params.out, params.count, sourcePositions, error))
        return false;

    // Shards survive every failure path above. Only a verified, published
    // output (and its requested debug sidecar) makes them safe to reclaim.
    for (const auto& info : shards)
    {
        std::filesystem::remove(info.path, ec);
        std::filesystem::remove(with_suffix(info.path, ".debug"), ec);
    }
    return true;
}

// --------------------------------------------------------------------------
// Worker
// --------------------------------------------------------------------------

void configure_engine(Engine& engine, int multiPv) {
    // Every Engine callback must be armed: search.cpp invokes them
    // unconditionally and a default-constructed std::function would throw.
    engine.set_on_iter([](const Engine::InfoIter&) {});
    engine.set_on_update_no_moves([](const Engine::InfoShort&) {});
    engine.set_on_bestmove([](std::string_view, std::string_view) {});
    engine.set_on_update_full([](const Engine::InfoFull&) {});
    std::istringstream multi("name MultiPV value " + std::to_string(multiPv));
    engine.get_options().setoption(multi);
}

void generate_worker(const Params&                   params,
                     const std::vector<std::string>& book,
                     Engine&                         engine,
                     WorkerStats&                    stats,
                     std::atomic_bool&               abort,
                     std::atomic<u64>&               globalRecords,
                     std::atomic<TimePoint>&         lastReport,
                     TimePoint                       globalStart,
                     std::mutex&                     reportMutex) {
    const auto    shardPath = with_suffix(params.out, "." + std::to_string(stats.shardId));
    std::ofstream shard(shardPath, std::ios::binary | std::ios::trunc);
    if (!shard)
    {
        stats.error = "cannot create shard " + shardPath.string();
        abort       = true;
        return;
    }
    write_header(shard, stats.target, 0);
    shard.flush();
    if (!shard)
    {
        stats.error = "cannot initialize shard " + shardPath.string();
        abort       = true;
        return;
    }

    std::ofstream debug;
    if (stats.debugTarget)
    {
        debug.open(with_suffix(shardPath, ".debug"), std::ios::trunc);
        if (!debug)
        {
            stats.error = "cannot create debug shard";
            abort       = true;
            return;
        }
    }

    PRNG          rng(stats.seed);
    SearchCapture capture(usize(params.randomMultiPv));
    engine.set_on_update_full([&capture](const Engine::InfoFull& info) { capture.update(info); });

    const TimePoint workerStart = now();
    usize           debugLines  = 0;

    while (stats.records < stats.target && !abort.load(std::memory_order_relaxed))
    {
        Position     pos;
        StateListPtr states(new std::deque<StateInfo>(1));
        const auto&  opening = book[usize(bounded_rand(rng, book.size()))];
        if (auto setError = pos.set(opening, &states->back()))
        {
            stats.error = "invalid book position: " + std::string(setError->what());
            abort       = true;
            break;
        }

        const auto                  randomFlags = random_move_flags(params, rng);
        std::vector<BufferedRecord> game;
        int                         whiteResult = 0;

        for (int ply = 0; !abort.load(std::memory_order_relaxed); ++ply)
        {
            MoveList<LEGAL> legal(pos);
            if (!legal.size())
            {
                // Checkmate or stalemate under the engine's own rules.
                whiteResult = pos.checkers() ? (pos.side_to_move() == WHITE ? -1 : 1) : 0;
                break;
            }
            if (pos.is_draw(ply) || ply >= MaxGamePlies)
            {
                whiteResult = 0;
                break;
            }

            ++stats.sourcePositions;
            const bool uniformRandom = usize(ply) < randomFlags.size() && randomFlags[usize(ply)];
            capture.reset(pos);
            if (auto setError = engine.set_position(pos.fen(), {}))
            {
                stats.error = "search position rejected: " + std::string(setError->what());
                abort       = true;
                break;
            }

            Search::LimitsType limits;
            limits.nodes = params.nodes;
            engine.go(limits);
            engine.wait_for_search_finished();

            if (!capture.lines[0].valid)
            {
                stats.error = "fixed-node search returned no scored PV in a legal position";
                abort       = true;
                break;
            }

            const Move best      = capture.lines[0].move;
            const int  bestScore = capture.lines[0].score;
            const bool bestMate  = capture.lines[0].mate;

            // Adjudication: a decided position ends the game and is NOT
            // recorded (the record would carry a near-mate or mate score).
            if (bestMate || std::abs(bestScore) >= params.evalLimit)
            {
                const bool stmWins = bestScore > 0;
                whiteResult        = (pos.side_to_move() == WHITE) == stmWins ? 1 : -1;
                break;
            }

            const bool write = ply >= params.writeMinPly && std::abs(bestScore) <= params.evalLimit
                            && !(params.filterChecks && bool(pos.checkers()))
                            && !(params.filterCaptures && pos.capture(best));

            if (write)
                game.push_back({pack_record(pos, bestScore, best, ply),
                                stats.debugTarget ? pos.fen() : std::string(), bestScore,
                                pos.side_to_move() == BLACK});

            Move played = Move::none();
            if (uniformRandom)
                played = legal.begin()[bounded_rand(rng, legal.size())];
            else
            {
                usize eligible = 1;
                while (eligible < capture.lines.size() && capture.lines[eligible].valid
                       && !capture.lines[eligible].mate
                       && capture.lines[0].score - capture.lines[eligible].score
                            <= params.randomMultiPvDiff)
                    ++eligible;
                played = capture.lines[usize(bounded_rand(rng, eligible))].move;
            }

            if (!played || !pos.legal(played))
            {
                stats.error = "selected move is not legal";
                abort       = true;
                break;
            }

            states->emplace_back();
            pos.do_move(played, states->back());
        }

        if (abort.load(std::memory_order_relaxed))
            break;

        u64 writtenThisGame = 0;
        for (auto& item : game)
        {
            if (stats.records >= stats.target)
                break;
            const u64 result = stm_result(whiteResult, item.stm);
            set_result(item.record, result);
            shard.write(reinterpret_cast<const char*>(item.record.data()),
                        std::streamsize(item.record.size()));
            if (debugLines < stats.debugTarget)
            {
                debug << item.fen << " | " << item.score << " | " << result << '\n';
                ++debugLines;
            }
            ++stats.records;
            ++writtenThisGame;
        }
        shard.flush();
        if (stats.debugTarget)
            debug.flush();
        if (!shard || (stats.debugTarget && !debug))
        {
            stats.error = "failed while flushing shard " + shardPath.string();
            abort       = true;
            break;
        }
        ++stats.games;
        if (whiteResult > 0)
            ++stats.whiteWins;
        else if (whiteResult < 0)
            ++stats.blackWins;
        else
            ++stats.draws;
        ++stats.recordsPerGame[writtenThisGame];

        const u64 done =
          globalRecords.fetch_add(writtenThisGame, std::memory_order_relaxed) + writtenThisGame;
        const auto current  = now();
        auto       previous = lastReport.load(std::memory_order_relaxed);
        if (current - previous >= 5000
            && lastReport.compare_exchange_strong(previous, current, std::memory_order_relaxed))
        {
            std::lock_guard<std::mutex> lock(reportMutex);
            const double                elapsed = double(current - globalStart) / 1000.0;
            sync_cout << "info string datagen " << std::min(done, params.count) << '/'
                      << params.count << " positions, " << u64(double(done) / std::max(elapsed, 1e-9))
                      << " pos/s" << sync_endl;
        }
    }

    stats.seconds = double(now() - workerStart) / 1000.0;
    shard.seekp(0);
    write_header(shard, stats.records, stats.sourcePositions);
    shard.close();
    debug.close();
    if (!stats.error.empty())
        return;
    if (!shard || (stats.debugTarget && !debug))
    {
        stats.error = "failed while writing shard " + shardPath.string();
        abort       = true;
    }
}

}  // namespace

// --------------------------------------------------------------------------
// Entry point
// --------------------------------------------------------------------------

bool run(std::istream&                               args,
         const std::optional<std::filesystem::path>& binaryPath,
         std::string&                                error) {
    Params params;
    if (!parse_params(args, params, error))
        return false;

    std::error_code ec;
    const auto      metaJson = with_suffix(params.out, ".meta.json");
    const auto      debugTxt = with_suffix(params.out, ".debug.txt");

    const bool haveOut      = std::filesystem::exists(params.out, ec);
    const bool haveMeta     = std::filesystem::exists(metaJson, ec);
    const bool haveDebug    = std::filesystem::exists(debugTxt, ec);
    const bool haveComplete = haveOut && haveMeta && (!params.debugSample || haveDebug);

    if (!(params.resume && haveComplete) && (haveOut || haveMeta || haveDebug))
    {
        error = "output or one of its final sidecars already exists; generation is already "
                "complete or was interrupted after publication";
        return false;
    }

    if (!binaryPath)
    {
        error = "cannot authenticate producer without the executable path";
        return false;
    }

    // Authenticate every external input before creating a directory, resume
    // sidecar, or shard. OpenBench v41 independently freezes the same values;
    // this check makes the generated chunk self-defending as well.
    const auto normalizedProducer = normalized_path(*binaryPath);
    const auto normalizedNetwork  = normalized_path(params.network);
    if (!authenticate_file(normalizedProducer, params.producerSha256, "producer",
                           params.producerSize, error)
        || !authenticate_file(normalizedNetwork, params.networkSha256, "network",
                              params.networkSize, error))
        return false;

    if (!TeraNNUE::active())
    {
        error = "authenticated network is not active; material fallback is forbidden";
        return false;
    }
    if (TeraNNUE::network().sha256() != params.networkSha256)
    {
        error = "active in-memory network SHA-256 mismatch: expected " + params.networkSha256
              + ", loaded " + TeraNNUE::network().sha256();
        return false;
    }

    // Book: authenticated FEN lines, or explicit NONE for builtin startpos.
    std::vector<std::string> book;
    std::filesystem::path    normalizedBook;
    if (params.book.empty())
        book.emplace_back(StartFEN);
    else
    {
        normalizedBook = normalized_path(params.book);
        if (!authenticate_file(normalizedBook, params.bookSha256, "book", params.bookSize, error)
            || !load_book(normalizedBook, book, error))
            return false;
    }

    const auto parent = params.out.parent_path();
    if (!parent.empty())
    {
        std::filesystem::create_directories(parent, ec);
        if (ec)
        {
            error = "cannot create output directory: " + ec.message();
            return false;
        }
    }

    const auto        normalizedOut = normalized_path(params.out);
    const std::string identity =
      identity_command(params, normalizedBook, normalizedNetwork, normalizedOut);

    if (params.resume && haveComplete)
    {
        ResumeMetadata completeMetadata;
        if (!load_resume_metadata(params.out, completeMetadata, error)
            || !validate_resume_metadata(completeMetadata, identity, params, error))
            return false;
        sync_cout << "info string datagen resume: " << params.out.string()
                  << " and its authenticated sidecars already exist; generation is complete"
                  << sync_endl;
        return true;
    }

    ResumeMetadata         resumeMetadata;
    std::vector<ShardInfo> existingShards;
    u64                    survivorRecords = 0;
    if (params.resume)
    {
        if (!load_resume_metadata(params.out, resumeMetadata, error)
            || !validate_resume_metadata(resumeMetadata, identity, params, error))
            return false;
        if (!discover_shards(params.out, true, existingShards, error))
            return false;
        for (const auto& shard : existingShards)
        {
            if (survivorRecords > params.count || shard.records > params.count - survivorRecords)
            {
                error = "surviving shards exceed the target record count";
                return false;
            }
            survivorRecords += shard.records;
        }

        params.resumeNumber        = resumeMetadata.resumeCount + 1;
        resumeMetadata.resumeCount = params.resumeNumber;
        resumeMetadata.lastCommand =
          full_command(params, normalizedBook, normalizedNetwork, normalizedOut);
        if (!write_resume_metadata(params.out, resumeMetadata, error))
            return false;

        for (const char* suffix : {".tmp", ".debug.txt.tmp", ".meta.json.tmp"})
        {
            const auto stale = with_suffix(params.out, suffix);
            if (std::filesystem::exists(stale, ec))
            {
                std::filesystem::remove(stale, ec);
                if (ec)
                {
                    error = "cannot remove stale resume temporary " + stale.string() + ": "
                          + ec.message();
                    return false;
                }
                sync_cout << "info string datagen resume: removed stale temporary "
                          << stale.string() << sync_endl;
            }
        }
    }
    else
    {
        for (const char* suffix :
             {".resume", ".resume.tmp", ".resume.prev", ".tmp", ".debug.txt.tmp", ".meta.json.tmp"})
            if (std::filesystem::exists(with_suffix(params.out, suffix), ec))
            {
                error = "output resume metadata or a temporary sidecar already exists; use "
                        "--resume";
                return false;
            }
        if (!discover_shards(params.out, false, existingShards, error))
            return false;
        if (!existingShards.empty())
        {
            error = "an output shard from an earlier run already exists; use --resume";
            return false;
        }
        resumeMetadata.command     = identity;
        resumeMetadata.lastCommand =
          full_command(params, normalizedBook, normalizedNetwork, normalizedOut);
        resumeMetadata.bookPath = params.book.empty() ? "NONE" : portable_path(normalizedBook);
        resumeMetadata.bookSize = params.bookSize;
        resumeMetadata.bookHash = params.bookSha256;
        resumeMetadata.networkPath = portable_path(normalizedNetwork);
        resumeMetadata.networkSize = params.networkSize;
        resumeMetadata.networkHash = params.networkSha256;
        resumeMetadata.producerSize = params.producerSize;
        resumeMetadata.producerHash = params.producerSha256;
        resumeMetadata.sourceCommit = params.sourceCommit;
        resumeMetadata.sourceDirty = params.sourceDirty;
        if (!write_resume_metadata(params.out, resumeMetadata, error))
            return false;
    }

    const u64 remaining    = params.count - survivorRecords;
    usize     firstShardId = 0;
    if (!existingShards.empty())
    {
        if (existingShards.back().id == std::numeric_limits<usize>::max())
        {
            error = "numeric shard id space exhausted";
            return false;
        }
        firstShardId = existingShards.back().id + 1;
    }
    if (params.threads - 1 > std::numeric_limits<usize>::max() - firstShardId)
    {
        error = "numeric shard id space exhausted";
        return false;
    }

    // The debug sidecar must be exactly merged records 0..debugSample-1, and
    // the merge order is the numeric shard id (survivors first, then this
    // session's shards).  Each shard's debug quota is therefore fixed by its
    // offset in the merged file; a survivor that is short of its quota cannot
    // be realigned and fails closed.
    const u64 debugSample = u64(params.debugSample);
    u64       debugOffset = 0;
    const auto debug_quota = [&](u64 records) {
        const u64 quota = std::min<u64>(records, debugOffset < debugSample ? debugSample - debugOffset : 0);
        debugOffset += records;
        return quota;
    };
    for (const auto& shard : existingShards)
    {
        const u64 want = debug_quota(shard.records);
        if (want && count_shard_debug_records(shard) < want)
        {
            error = "resume cannot rebuild an aligned debug sample: shard " + shard.path.string()
                  + " holds fewer than " + std::to_string(want) + " debug line(s)";
            return false;
        }
    }

    std::vector<WorkerStats>             stats(params.threads);
    std::vector<std::unique_ptr<Engine>> engines(params.threads);
    for (usize id = 0; id < params.threads; ++id)
    {
        stats[id].shardId = firstShardId + id;
        stats[id].target  = remaining / params.threads + (id < remaining % params.threads);
        stats[id].seed    = splitmix_seed(params.seed, params.resumeNumber, id);
        stats[id].debugTarget = usize(debug_quota(stats[id].target));
        if (stats[id].target)
        {
            const auto shardPath = with_suffix(params.out, "." + std::to_string(stats[id].shardId));
            if (std::filesystem::exists(shardPath, ec))
            {
                error = "new shard path already exists: " + shardPath.string();
                return false;
            }
            // The UCI owner loaded and authenticated the compiled default once
            // before dispatching this command. TeraNNUE is process-global, so
            // workers share that immutable network instead of re-reading it
            // once per thread.
            engines[id] = std::make_unique<Engine>(binaryPath, false);
            configure_engine(*engines[id], params.randomMultiPv);
        }
    }
    if (debugOffset < debugSample)
    {
        error = "the requested debug sample exceeds the planned record count";
        return false;
    }

    sync_cout << "info string datagen tera-bin v1" << (params.resume ? " resume" : "") << ": "
              << params.count << " target positions, " << survivorRecords << " surviving, "
              << remaining << " remaining, " << params.threads << " independent engines, "
              << params.nodes << " nodes, " << book.size() << " book line(s), base seed "
              << params.seed << ", session " << params.resumeNumber << sync_endl;

    std::atomic_bool         abort{false};
    std::atomic<u64>         globalRecords{survivorRecords};
    const TimePoint          start = now();
    std::atomic<TimePoint>   lastReport{start};
    std::mutex               reportMutex;
    std::vector<std::thread> workers;
    workers.reserve(params.threads);
    for (usize id = 0; id < params.threads; ++id)
    {
        if (!stats[id].target)
            continue;
        workers.emplace_back(generate_worker, std::cref(params), std::cref(book),
                             std::ref(*engines[id]), std::ref(stats[id]), std::ref(abort),
                             std::ref(globalRecords), std::ref(lastReport), start,
                             std::ref(reportMutex));
    }
    for (auto& worker : workers)
        worker.join();

    for (const auto& worker : stats)
        if (!worker.error.empty())
        {
            error = worker.error;
            return false;
        }
    for (const auto& worker : stats)
        if (worker.records != worker.target)
        {
            error = "generation stopped before every shard reached its target";
            return false;
        }

    u64 games = 0;
    for (const auto& worker : stats)
        games += worker.games;

    // Free the per-worker engines (and their transposition tables) before the
    // merge: the merge only needs file handles.
    engines.clear();

    std::vector<ShardInfo> allShards;
    if (!discover_shards(params.out, false, allShards, error))
        return false;
    u64 totalRecords    = 0;
    u64 sourcePositions = 0;
    for (const auto& shard : allShards)
    {
        totalRecords += shard.records;
        sourcePositions += shard.sourcePositions;
    }
    if (totalRecords != params.count)
    {
        error = "generation produced " + std::to_string(totalRecords) + " records; target is "
              + std::to_string(params.count);
        return false;
    }

    if (!merge_shards(params, allShards, sourcePositions, error))
        return false;

    const double seconds = double(now() - start) / 1000.0;
    if (!write_metadata(params, stats, sourcePositions, games, survivorRecords, seconds, error))
        return false;

    const double positionsPerSecond = double(remaining) / std::max(seconds, 1e-9);
    sync_cout << "info string datagen finished: " << params.count << " total positions, "
              << remaining << " generated this session, " << games << " session games, "
              << std::fixed << std::setprecision(2) << positionsPerSecond << " pos/s, "
              << positionsPerSecond / double(params.threads) << " pos/s/thread -> "
              << params.out.string() << sync_endl;
    return true;
}

}  // namespace Stockfish::Datagen
