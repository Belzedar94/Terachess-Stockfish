/*
  Terachess-Stockfish NNUE "S" — TNN1 loader and integer forward pass.

  Contract: docs/nnue-tera-s.md sections 4, 5 and 7.  The Python authority of
  the ==0 cp parity gate is tools/terannue/quantized_forward.py; this file
  reproduces it operation for operation.

  Integer pipeline:

      acc[p][k]  = ft_bias[k] + sum(ft_weights[row][k] for row in active[p])
      x          = [clip(acc[stm], 0, 127) | clip(acc[nstm], 0, 127)]   (512)
      pair[j]    = (x[a] * x[a + 128]) >> 7                             (256)
      fc0        = trunc((b0 + sum(w0 * pair)) / 64), clipped 0..127     (16)
      fc1        = trunc((b1 + sum(w1 * fc0 )) / 64), clipped 0..127     (32)
      positional = b2 + sum(w2 * fc1)                                   (i32)
      psqt       = trunc((sum psqt[stm] - sum psqt[nstm]) / 2)          (i32)
      eval_cp    = trunc((psqt + positional) / FV_SCALE)

  Every division truncates toward zero.  Signed integer division in C++ does
  exactly that, so plain `/` is used and shifts are deliberately avoided on
  possibly-negative values.

  Two points the contract clarified on 2026-07-19 (normative amendment to
  sections 4 and 7, matching tools/terannue/quantized_forward.py):

    * The pairwise stage reduces 512 -> 256, so FC0_IN = 256 and fc0 is
      i8[256x16] (the old `i8[512x16]` of section 7 was inconsistent with
      section 4 and no longer applies).
    * psqt = (psqt_stm[bucket] - psqt_nstm[bucket]) / 2 and
      eval_cp = (psqt + positional[bucket]) / FV_SCALE with FV_SCALE = 16,
      both truncating toward zero.

  The loader FAILS CLOSED: magic, version, arch_hash (sha256 of the canonical
  descriptor compiled into the binary), the four dims and the exact file size
  are all checked, and any mismatch rejects the file with a message.  Nothing
  is ever adapted.
*/

#ifndef TERA_NETWORK_H_INCLUDED
#define TERA_NETWORK_H_INCLUDED

#include <cstdint>
#include <memory>
#include <string>

#include "../misc.h"
#include "../types.h"
#include "tera_accumulator.h"
#include "tera_features.h"

namespace Stockfish {

class Position;

namespace TeraNNUE {

// Text that is hashed into arch_hash (contract section 7, byte-exact).
constexpr char Descriptor[] = "terachess-nnue-S;kb=8;planes=51;sq=256;L1=256;ob=8;"
                              "ftscale=128;act=clip0_127;pairwise=shr7;stack=16-32-1";

constexpr int  TnnVersion    = 1;
constexpr usize TnnHeaderSize = 4 + 2 + 32 + 16;  // 54, densely packed LE
constexpr usize TnnFileSize =
  TnnHeaderSize + usize(NumFeatures) * L1 * 2 + usize(L1) * 2
  + usize(NumFeatures) * OutputBuckets * 4
  + usize(OutputBuckets)
      * (Fc0In * Fc0Out + Fc0Out * 4 + Fc1In * Fc1Out + Fc1Out * 4 + Fc2In * Fc2Out + Fc2Out * 4);

// One output bucket's fc0/fc1/fc2 block, stored [in][out] exactly as on disk.
struct StackWeights {
    std::int8_t  w0[Fc0In][Fc0Out];
    std::int32_t b0[Fc0Out];
    std::int8_t  w1[Fc1In][Fc1Out];
    std::int32_t b1[Fc1Out];
    std::int8_t  w2[Fc2In][Fc2Out];
    std::int32_t b2[Fc2Out];
};

struct EvalBreakdown {
    int psqt       = 0;
    int positional = 0;
    int cp         = 0;
    int bucket     = 0;
};

// Deleter for the two large page-aligned weight blocks.
struct PageFree {
    void operator()(std::int16_t* p) const;
    void operator()(std::int32_t* p) const;
};

class Network {
   public:
    Network() = default;
    Network(const Network&)            = delete;
    Network& operator=(const Network&) = delete;

    // Fails closed. Returns false and fills `error` on any mismatch; the
    // previously loaded network (if any) is left untouched on failure.
    bool load(const std::string& path, std::string& error);
    void unload();

    bool               loaded() const { return isLoaded; }
    const std::string& file() const { return filePath; }
    const std::string& sha256() const { return fileSha256; }

    const std::int16_t* ft_row(int f) const { return ftWeights.get() + usize(f) * L1; }
    const std::int16_t* ft_bias() const { return ftBias; }
    const std::int32_t* psqt_row(int f) const { return psqtTable.get() + usize(f) * OutputBuckets; }

    // Contract sections 4/5. `bucket` is the output bucket.
    EvalBreakdown forward(const Accumulator& a, Color stm, int bucket) const;

   private:
    std::unique_ptr<std::int16_t[], PageFree> ftWeights;
    std::unique_ptr<std::int32_t[], PageFree> psqtTable;
    std::int16_t                              ftBias[L1] = {};
    StackWeights                              stacks[OutputBuckets] = {};
    std::string                               filePath;
    std::string                               fileSha256;
    bool                                      isLoaded = false;
};

// Process-wide network (read-only during search, so shared by all threads).
Network& network();

bool use_nnue();
void set_use_nnue(bool v);
bool active();  // network().loaded() && use_nnue()

// sha256 of `Descriptor`, as a lowercase hex string (debug output).
std::string descriptor_hash_hex();

// Stateless evaluation used outside the search (refresh + forward).
EvalBreakdown evaluate_position(const Position& pos);

// Evaluation from an already-maintained accumulator (search hot path).
EvalBreakdown evaluate_accumulated(const Accumulator& a, const Position& pos);

}  // namespace TeraNNUE
}  // namespace Stockfish

#endif  // #ifndef TERA_NETWORK_H_INCLUDED
