/*
  Terachess-Stockfish NNUE "S" — TNN1 loader and integer forward pass.
  Contract: docs/nnue-tera-s.md sections 4, 5 and 7.
*/

#include "tera_network.h"

#include <cstring>
#include <fstream>
#include <sstream>
#include <vector>

#include "../memory.h"
#include "../position.h"

namespace Stockfish {
namespace TeraNNUE {

namespace {

// ---------------------------------------------------------------------------
// SHA-256 (FIPS 180-4). Only used on the ~110-byte descriptor at load time.
// ---------------------------------------------------------------------------

constexpr std::uint32_t Sha256K[64] = {
  0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u,
  0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu,
  0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu,
  0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
  0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
  0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
  0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u,
  0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
  0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u,
  0xc67178f2u};

constexpr std::uint32_t rotr32(std::uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

void sha256(const unsigned char* data, usize len, unsigned char out[32]) {

    std::uint32_t h[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                          0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};

    // Pad the whole message up front: this routine only ever sees the ~110
    // byte descriptor, so a single buffer is both simplest and fastest.
    std::vector<unsigned char> msg(data, data + len);
    msg.push_back(0x80);
    while (msg.size() % 64 != 56)
        msg.push_back(0);
    const std::uint64_t bits = std::uint64_t(len) * 8;
    for (int i = 7; i >= 0; --i)
        msg.push_back(static_cast<unsigned char>((bits >> (8 * i)) & 0xFF));

    for (usize off = 0; off < msg.size(); off += 64)
    {
        const unsigned char* block = msg.data() + off;

        std::uint32_t w[64];
        for (int i = 0; i < 16; ++i)
            w[i] = (std::uint32_t(block[4 * i]) << 24) | (std::uint32_t(block[4 * i + 1]) << 16)
                 | (std::uint32_t(block[4 * i + 2]) << 8) | std::uint32_t(block[4 * i + 3]);
        for (int i = 16; i < 64; ++i)
        {
            const std::uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const std::uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i]                   = w[i - 16] + s0 + w[i - 7] + s1;
        }

        std::uint32_t a = h[0], b = h[1], c = h[2], d = h[3];
        std::uint32_t e = h[4], f = h[5], g = h[6], hh = h[7];

        for (int i = 0; i < 64; ++i)
        {
            const std::uint32_t S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
            const std::uint32_t ch = (e & f) ^ (~e & g);
            const std::uint32_t t1 = hh + S1 + ch + Sha256K[i] + w[i];
            const std::uint32_t S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
            const std::uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t t2 = S0 + mj;
            hh = g; g = f; f = e; e = d + t1;
            d = c;  c = b; b = a; a = t1 + t2;
        }

        h[0] += a; h[1] += b; h[2] += c; h[3] += d;
        h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }

    for (int i = 0; i < 8; ++i)
    {
        out[4 * i]     = static_cast<unsigned char>(h[i] >> 24);
        out[4 * i + 1] = static_cast<unsigned char>(h[i] >> 16);
        out[4 * i + 2] = static_cast<unsigned char>(h[i] >> 8);
        out[4 * i + 3] = static_cast<unsigned char>(h[i]);
    }
}

void descriptor_hash(unsigned char out[32]) {
    // sizeof(Descriptor) - 1 drops the terminating NUL: the hashed text is
    // exactly the string of contract section 7.
    sha256(reinterpret_cast<const unsigned char*>(Descriptor), sizeof(Descriptor) - 1, out);
}

std::string hex(const unsigned char* p, usize n) {
    static const char* digits = "0123456789abcdef";
    std::string        s;
    s.reserve(2 * n);
    for (usize i = 0; i < n; ++i)
    {
        s += digits[p[i] >> 4];
        s += digits[p[i] & 15];
    }
    return s;
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
    unsigned char digest[32];
    descriptor_hash(digest);
    return hex(digest, 32);
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

    unsigned char header[TnnHeaderSize];
    in.read(reinterpret_cast<char*>(header), std::streamsize(TnnHeaderSize));
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
        error = "arch_hash mismatch: file " + hex(header + 6, 32) + " != build "
              + hex(expected, 32);
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

    in.read(reinterpret_cast<char*>(ftBuf.get()), std::streamsize(ftCount * 2));
    in.read(reinterpret_cast<char*>(biasBuf), std::streamsize(sizeof(biasBuf)));
    in.read(reinterpret_cast<char*>(psqtBuf.get()), std::streamsize(psqtCount * 4));
    for (int b = 0; b < OutputBuckets; ++b)
    {
        in.read(reinterpret_cast<char*>(stackBuf[b].w0), sizeof(stackBuf[b].w0));
        in.read(reinterpret_cast<char*>(stackBuf[b].b0), sizeof(stackBuf[b].b0));
        in.read(reinterpret_cast<char*>(stackBuf[b].w1), sizeof(stackBuf[b].w1));
        in.read(reinterpret_cast<char*>(stackBuf[b].b1), sizeof(stackBuf[b].b1));
        in.read(reinterpret_cast<char*>(stackBuf[b].w2), sizeof(stackBuf[b].w2));
        in.read(reinterpret_cast<char*>(stackBuf[b].b2), sizeof(stackBuf[b].b2));
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
    filePath = path;
    isLoaded = true;
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
