# Terachess-Stockfish 1.0

*2026-07-19*

Terachess-Stockfish is a dedicated engine for **Terachess II**, the 16×16
chess variant designed by Jean-Louis Cazaux: 256 squares, 26 piece types and
64 pieces per side. It is derived from Stockfish, with the board
representation, move generation, evaluation and data pipeline rewritten for
the variant.

This is the first release, and to our knowledge the first engine of any kind
for Terachess. No published perft results existed for the variant either; the
reference counts shipped in this repository are original work.

---

## Highlights

- **Full Terachess II rules**, verified against two independently written
  reference implementations: 1,000 random positions and 37,371,980 leaf nodes
  compared at perft depth 2 with zero discrepancies, plus 87 hand-derived
  rule fixtures.
- **NNUE evaluation** trained from scratch on 2.87 million self-play
  positions. The network is worth roughly **+500 Elo** over the material
  evaluation it replaces, measured in two steps (see *Strength*).
- **Embedded data generation** conforming to the OpenBench distributed
  datagen contract, with a versioned binary record format and byte-exact
  round-trip verification between engine and trainer.
- **Verification harness** shipped with the engine: rule oracle, perft suite,
  parity gate, unit-scale gate and an SPRT runner.

## Strength

All matches were played at fixed nodes, with the rule oracle acting as
arbiter (every move validated; illegal moves are an immediate loss and are
reported). Openings are randomised and colours alternate.

| Comparison | Result | Elo |
|---|---|---|
| `tera-net1` vs material evaluation | +87 −13 =0 (100 games, 10k nodes) | **+330 ± 52** |
| `tera-net2` vs `tera-net1` | +268 −91 =1 (360 games, 8k nodes) | **+187**, SPRT PASS, LLR +3.30, bounds [1, 6] |

There is no other engine to compare against, so these figures describe
progress against our own baseline rather than a position in any ranking.

## Contents

| File | Description |
|---|---|
| `terachess-stockfish-1.0-x86-64-bmi2.exe` | Engine binary (Windows, AVX2/BMI2) |
| `tera-net2.tnn` | NNUE network, 56.9 MB — load with `setoption name EvalFile value <path>` |
| `tera-net1.tnn` | Previous network, kept for regression testing |
| Source archive | Complete sources, oracle, tooling and documentation |

The network is **not** embedded in the binary. Without it the engine falls
back to a material evaluation and says so on stderr; it will play legally but
far weaker.

## Requirements

- x86-64 CPU with AVX2 and BMI2 (Zen 2+ / Haswell+).
- **RAM: roughly 200 MB per search thread**, on top of the hash table and the
  57 MB network. This is unusually high and is a direct consequence of the
  256-square board: the history tables are indexed by square. A 24-thread run
  needs about 5 GB. Budget accordingly before raising `Threads`.

## Getting started

```
uci
setoption name EvalFile value /path/to/tera-net2.tnn
setoption name Threads value 4
setoption name Hash value 256
position startpos
go movetime 10000
```

The engine speaks UCI. Squares use two-digit ranks (`a1`–`p16`); promotions
always carry the resulting piece letter as a suffix (`a15a16q`). `d` prints
the 16×16 board, `eval` dumps the evaluation breakdown, and `go perft N`
performs a leaf-node count.

## Known limitations

We would rather state these than have you discover them.

1. **Search heuristics are still chess-tuned.** Late move pruning discards
   between 18 % and 89 % of quiet moves depending on depth, because its
   threshold was fitted for positions with ~35 legal moves and Terachess
   middlegames have 150–300. A/B comparisons between builds of this engine are
   valid; the *absolute* playing strength is not representative of what the
   search can reach. Sweeping this family is the first item of the improvement
   programme.
2. **Two rules are assumptions, not sources.** Cazaux does not specify a
   repetition rule or a fifty-move rule for Terachess; we adopted the FIDE
   ones and marked them as assumptions in the specification. A ruling from the
   author would change the engine's behaviour in those positions.
3. **Rules verified internally, not externally.** Correctness is established
   against two implementations written independently from the written
   specification, which catches implementation bugs but not a shared
   misreading of the rules. Cross-checks against Ai Ai, Jocly and the author's
   ZRF are planned and not yet done.
4. **Training data is below plan.** 2.87 M positions were generated against a
   target of 20–30 M. The measured data-quality curve shows the network was
   still improving with more data when the release was cut.
5. **The `wdl` output is meaningless.** The upstream win-rate model is fitted
   for chess and assigns zero material value to every Terachess-specific
   piece. It is neutralised (`to_cp` is the identity), so `score cp` is honest,
   but win/draw/loss percentages should be ignored until a Terachess model is
   fitted.
6. **Endgame tablebases and opening books are absent**, and no `Chess960`,
   `MultiPV`-tuned or ponder support has been validated for the variant.

## Notes on the variant

Measurements that may be of interest to anyone else working on Terachess:

- **Games are long and decisive.** Self-play games average **575 plies** and
  reach 842. Across 606 games recorded during development there was **one
  draw**. Any ply cap below 1,000 manufactures artificial draws — an early
  probe of ours capped at 300 and reported 100 % draws.
- **Branching factor** is 54 at the initial position and 98–300 in the
  middlegame.
- **Reference perft**, initial position: 54 / 2,916 / 175,508 / 10,562,564 for
  depths 1–4. Fifteen further positions are published in
  `oracle/perft_refs.json`.

## Verification

Every claim above is reproducible from the repository:

```bash
cd oracle
python run_fixtures.py --impl both                     # 87 fixtures, 0 failures
python differential.py --games 50                      # oracle A vs B
python engine_check.py --engine ../src/stockfish.exe   # engine vs oracle
python mass_perft.py --engine ../src/stockfish.exe --positions 1000

cd ../tools
python check_label_units.py --engine ../src/stockfish.exe --data <data.bin>
python parity_gate.py --engine ../src/stockfish.exe --net ../nets/tera-net2.tnn --ref <ref.jsonl>
```

The parity gate requires the engine and the Python trainer to agree to
**exactly 0 centipawns** on every position, including the active feature
indices of both perspectives. Network provenance receipts are in
`nets/PROVENANCE.md`; the development ledger, including the failures and their
autopsies, is in `AUDIT.md`.

## Bench

```
Bench: 21519
```
(`bench 16 1 5`, material evaluation, no network — the signature is
deterministic and is checked on every commit.)

## Licence and credits

GPL v3, as required by Stockfish. Terachess-Stockfish is a derivative of
Stockfish by the Stockfish developers; the 256-bit bitboard operators were
adapted from the very-large-boards branch of Fairy-Stockfish. The rules of
Terachess are the work of **Jean-Louis Cazaux**, and the Betza notation used
as the machine-readable source of the piece definitions comes from H. G.
Muller's Interactive Diagram. Neither is affiliated with this project.

See `AUTHORS` and `Copying.txt`.
