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

#ifndef DATAGEN_H_INCLUDED
#define DATAGEN_H_INCLUDED

#include <filesystem>
#include <iosfwd>
#include <optional>
#include <string>

// Embedded self-play generator writing tera-bin v1 ("TC01") records.
//
// Byte contract (NORMATIVE): docs/tera-bin-v1.md.  Python mirror and format
// authority: tools/terabin.py -- the bytes produced here must decode with the
// strict Python decoder unchanged.  FEN-TSF grammar and piece letters:
// TERACHESS_SPEC.md section 3.  Distributed-worker contract (single-line UCI
// command, one final file at `out`, exit code 0): openbench-spell
// docs/datagen-mode.md.

namespace Stockfish::Datagen {

// Runs the generator described by the remainder of a single-line UCI
// `datagen ...` command.  Returns false and fills `error` on any deterministic
// failure; the caller must then terminate the process with a non-zero status.
// A successful return (including the idempotent "already complete" resume
// path) guarantees that a single merged file exists at the requested `out`.
bool run(std::istream&                               args,
         const std::optional<std::filesystem::path>& binaryPath,
         std::string&                                error);

}  // namespace Stockfish::Datagen

#endif  // #ifndef DATAGEN_H_INCLUDED
