/*
  Terachess-Stockfish NNUE "S" — TNN1 loader and integer forward pass.
  Contract: docs/nnue-tera-s.md sections 4, 5 and 7.
*/

#include "tera_network.h"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <sstream>

#include "../memory.h"
#include "../position.h"
#include "../sha256.h"

namespace Stockfish {
namespace TeraNNUE {

namespace {

void descriptor_hash(unsigned char out[32]) {
    // sizeof(Descriptor) - 1 drops the terminating NUL: the hashed text is
    // exactly the string of contract section 7.
    Integrity::Sha256 hasher;
    hasher.update(Descriptor, sizeof(Descriptor) - 1);
    const auto digest = hasher.digest();
    std::copy(digest.begin(), digest.end(), out);
}

std::uint16_t read_u16(const unsigned char* p) {
    return std::uint16_t(std::uint16_t(p[0]) | (std::uint16_t(p[1]) << 8));
}

std::uint32_t read_u32(const unsigned char* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) | (std::uint32_t(p[2]) << 16)
         | (std::uint32_t(p[3]) << 24);
}

constexpr int clip127(int v) { return v < 0 ? 0 : (v > FtActMax ? FtActMax : v); }

// Truncation toward zero: plain signed division, never a shift.
constexpr int trunc_div(int v, int d) { return v / d; }

bool          gUseNnue = true;

}  // namespace

void PageFree::operator()(std::int16_t* p) const { aligned_large_pages_free(p); }
void PageFree::operator()(std::int32_t* p) const { aligned_large_pages_free(p); }

std::string descriptor_hash_hex() {
    Integrity::Sha256 hasher;
    hasher.update(Descriptor, sizeof(Descriptor) - 1);
    return Integrity::sha256_hex(hasher.digest());
}

Network& network() {
    static Network instance;
    return instance;
}

bool use_nnue() { return gUseNnue; }
void set_use_nnue(bool v) { gUseNnue = v; }
bool active() { return gUseNnue && network().loaded(); }

// ---------------------------------------------------------------------------
// Loading — FAILS CLOSED
// ---------------------------------------------------------------------------

void Network::unload() {
    ftWeights.reset();
    psqtTable.reset();
    filePath.clear();
    fileSha256.clear();
    isLoaded = false;
}

bool Network::load(const std::string& path, std::string& error) {

    error.clear();

    std::ifstream in(path_from_utf8(path), std::ios::binary);
    if (!in)
    {
        error = "cannot open '" + path + "'";
        return false;
    }

    in.seekg(0, std::ios::end);
    const std::streamoff size = in.tellg();
    in.seekg(0, std::ios::beg);

    if (size < 0 || usize(size) != TnnFileSize)
    {
        std::ostringstream ss;
        ss << "size " << size << " != " << TnnFileSize << " (expected TNN1 file size)";
        error = ss.str();
        return false;
    }

    Integrity::Sha256 fileHasher;
    const auto read_and_hash = [&](void* destination, usize bytes) {
        in.read(static_cast<char*>(destination), std::streamsize(bytes));
        const auto count = in.gcount();
        if (count > 0)
            fileHasher.update(destination, usize(count));
    };

    unsigned char header[TnnHeaderSize];
    read_and_hash(header, TnnHeaderSize);
    if (!in)
    {
        error = "truncated TNN1 header";
        return false;
    }

    if (std::memcmp(header, "TNN1", 4) != 0)
    {
        error = "bad magic (expected 'TNN1')";
        return false;
    }

    const std::uint16_t version = read_u16(header + 4);
    if (version != TnnVersion)
    {
        std::ostringstream ss;
        ss << "unsupported TNN1 version " << version << " (expected " << TnnVersion << ")";
        error = ss.str();
        return false;
    }

    unsigned char expected[32];
    descriptor_hash(expected);
    if (std::memcmp(header + 6, expected, 32) != 0)
    {
        Integrity::Sha256Digest fileDigest{}, expectedDigest{};
        std::copy_n(header + 6, fileDigest.size(), fileDigest.begin());
        std::copy_n(expected, expectedDigest.size(), expectedDigest.begin());
        error = "arch_hash mismatch: file " + Integrity::sha256_hex(fileDigest) + " != build "
              + Integrity::sha256_hex(expectedDigest);
        return false;
    }

    const std::uint32_t dims[4] = {read_u32(header + 38), read_u32(header + 42),
                                   read_u32(header + 46), read_u32(header + 50)};
    const std::uint32_t want[4] = {KingBuckets, Planes, L1, OutputBuckets};
    for (int i = 0; i < 4; ++i)
        if (dims[i] != want[i])
        {
            std::ostringstream ss;
            ss << "dims (" << dims[0] << ", " << dims[1] << ", " << dims[2] << ", " << dims[3]
               << ") != (" << want[0] << ", " << want[1] << ", " << want[2] << ", " << want[3]
               << ")";
            error = ss.str();
            return false;
        }

    // Allocate into scratch buffers so a failed load never damages the
    // network currently in use.
    const usize ftCount   = usize(NumFeatures) * L1;
    const usize psqtCount = usize(NumFeatures) * OutputBuckets;

    std::unique_ptr<std::int16_t[], PageFree> ftBuf(
      static_cast<std::int16_t*>(aligned_large_pages_alloc(ftCount * sizeof(std::int16_t))));
    std::unique_ptr<std::int32_t[], PageFree> psqtBuf(
      static_cast<std::int32_t*>(aligned_large_pages_alloc(psqtCount * sizeof(std::int32_t))));

    if (!ftBuf || !psqtBuf)
    {
        error = "out of memory allocating the feature transformer";
        return false;
    }

    std::int16_t biasBuf[L1];
    StackWeights stackBuf[OutputBuckets];

    // The on-disk encoding is little-endian, densely packed. x86/ARM64 hosts
    // are little-endian, so the blocks are read straight into place.
    static_assert(sizeof(std::int16_t) == 2 && sizeof(std::int32_t) == 4, "LE raw read");

    read_and_hash(ftBuf.get(), ftCount * 2);
    read_and_hash(biasBuf, sizeof(biasBuf));
    read_and_hash(psqtBuf.get(), psqtCount * 4);
    for (int b = 0; b < OutputBuckets; ++b)
    {
        read_and_hash(stackBuf[b].w0, sizeof(stackBuf[b].w0));
        read_and_hash(stackBuf[b].b0, sizeof(stackBuf[b].b0));
        read_and_hash(stackBuf[b].w1, sizeof(stackBuf[b].w1));
        read_and_hash(stackBuf[b].b1, sizeof(stackBuf[b].b1));
        read_and_hash(stackBuf[b].w2, sizeof(stackBuf[b].w2));
        read_and_hash(stackBuf[b].b2, sizeof(stackBuf[b].b2));
    }

    if (!in)
    {
        error = "unexpected end of file while reading the weight blocks";
        return false;
    }

    // Contract section 5: the anti-overflow guarantee is a property of the
    // FILE, not of the trainer's good behaviour. Verify it here too.
    for (usize i = 0; i < ftCount; ++i)
        if (ftBuf[i] > FtWeightMax || ftBuf[i] < -FtWeightMax)
        {
            std::ostringstream ss;
            ss << "ft weight " << ftBuf[i] << " at row " << (i / L1) << " outside +-"
               << FtWeightMax;
            error = ss.str();
            return false;
        }
    for (int k = 0; k < L1; ++k)
        if (biasBuf[k] > FtBiasMax || biasBuf[k] < -FtBiasMax)
        {
            std::ostringstream ss;
            ss << "ft bias " << biasBuf[k] << " outside +-" << FtBiasMax;
            error = ss.str();
            return false;
        }

    ftWeights = std::move(ftBuf);
    psqtTable = std::move(psqtBuf);
    std::memcpy(ftBias, biasBuf, sizeof(ftBias));
    std::memcpy(stacks, stackBuf, sizeof(stacks));
    filePath   = path;
    fileSha256 = Integrity::sha256_hex(fileHasher.digest());
    isLoaded   = true;
    return true;
}

// ---------------------------------------------------------------------------
// Forward pass (contract sections 4 and 5)
// ---------------------------------------------------------------------------

EvalBreakdown Network::forward(const Accumulator& a, Color stm, int bucket) const {

    EvalBreakdown out;
    out.bucket = bucket;

    if (!isLoaded || bucket < 0 || bucket >= OutputBuckets)
        return out;

    const Color persp[2] = {stm, ~stm};

    // clipped ReLU over the concatenated 512, then the pairwise (l * r) >> 7.
    // The 512 values split into four blocks of 128: the two blocks of each
    // perspective multiply together, giving 128 products per perspective.
    constexpr int Half = L1 / 2;
    int           pair[Fc0In];
    for (int p = 0; p < 2; ++p)
    {
        const std::int16_t* v = a.acc[persp[p]];
        for (int j = 0; j < Half; ++j)
        {
            const int l = clip127(v[j]);
            const int r = clip127(v[j + Half]);
            pair[p * Half + j] = (l * r) >> PairwiseShift;
        }
    }

    const StackWeights& st = stacks[bucket];

    int h0[Fc0Out];
    for (int o = 0; o < Fc0Out; ++o)
        h0[o] = st.b0[o];
    for (int j = 0; j < Fc0In; ++j)
        if (pair[j])
            for (int o = 0; o < Fc0Out; ++o)
                h0[o] += pair[j] * st.w0[j][o];
    for (int o = 0; o < Fc0Out; ++o)
        h0[o] = clip127(trunc_div(h0[o], HiddenScale));

    int h1[Fc1Out];
    for (int o = 0; o < Fc1Out; ++o)
        h1[o] = st.b1[o];
    for (int j = 0; j < Fc1In; ++j)
        if (h0[j])
            for (int o = 0; o < Fc1Out; ++o)
                h1[o] += h0[j] * st.w1[j][o];
    for (int o = 0; o < Fc1Out; ++o)
        h1[o] = clip127(trunc_div(h1[o], HiddenScale));

    int positional = st.b2[0];
    for (int j = 0; j < Fc2In; ++j)
        positional += h1[j] * st.w2[j][0];

    // Contract section 4: psqt = trunc((psqt_stm - psqt_nstm) / 2)
    const int psqt =
      trunc_div(a.psqt[persp[0]][bucket] - a.psqt[persp[1]][bucket], 2);

    out.psqt       = psqt;
    out.positional = positional;
    out.cp         = trunc_div(psqt + positional, FvScale);
    return out;
}

EvalBreakdown evaluate_accumulated(const Accumulator& a, const Position& pos) {
    return network().forward(a, pos.side_to_move(), output_bucket(pos));
}

EvalBreakdown evaluate_position(const Position& pos) {
    // Heap, not stack: an Accumulator is ~1 KiB and this file is compiled
    // with -Wstack-usage.
    std::unique_ptr<Accumulator> a(new Accumulator);
    refresh(network(), pos, *a);
    return evaluate_accumulated(*a, pos);
}

}  // namespace TeraNNUE
}  // namespace Stockfish
