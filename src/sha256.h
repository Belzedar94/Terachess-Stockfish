/*
  Terachess-Stockfish, a Terachess II engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef SHA256_H_INCLUDED
#define SHA256_H_INCLUDED

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>

namespace Stockfish::Integrity {

using Sha256Digest = std::array<std::uint8_t, 32>;

class Sha256 {
   public:
    Sha256();

    void         update(const void* data, std::size_t size);
    Sha256Digest digest() const;

   private:
    void transform(const std::uint8_t* block);

    std::array<std::uint32_t, 8> state;
    std::array<std::uint8_t, 64> buffer{};
    std::size_t                  bufferSize = 0;
    std::uint64_t                totalBytes = 0;
};

std::string sha256_hex(const Sha256Digest& digest, bool uppercase = false);
bool        is_sha256(std::string_view text);
std::string normalize_sha256(std::string text);
bool        sha256_file(const std::filesystem::path& path, std::string& digest,
                        std::string& error);

}  // namespace Stockfish::Integrity

#endif  // SHA256_H_INCLUDED
