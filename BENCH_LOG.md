# BENCH_LOG.md — firmas de bench deterministas

Disciplina heredada de Spell-Stockfish: **cada commit lleva su firma de bench**
(`Bench: <nodos>` en el mensaje). Toda mejora de velocidad se compara contra la
línea anterior de esta tabla; todo cambio de toolchain o receta de build exige
un NPS-check contra la última entrada ANTES de creerse ninguna medición
posterior (lección PGO-g++15.2: 3,3× más lento con firma idéntica).

**Máquina de referencia**: Windows 10, 25 hilos lógicos, MSYS2 mingw64 g++ 15.2.0.
**Receta oficial**: `make -j build ARCH=x86-64-bmi2 COMP=mingw` (SIN PGO —
profile-build está prohibido en esta máquina hasta que se demuestre lo contrario).
**Comando de firma**: `bench 16 1 5` (12 posiciones Terachess, depth 5, 1 hilo).

| Fecha | Commit | Comando | Nodos (firma) | NPS | Notas |
|---|---|---|---|---|---|
| 2026-07-19 | 72bfb25 | `bench` (ajedrez, depth default) | 2.692.515 | ~1.380.000 | Chasis SF master limpio, eval material fallback. Firma de AJEDREZ, no comparable con las siguientes |
| 2026-07-19 | 94fab4f | `bench 16 1 5` | **22.723** | — | Primera firma de Terachess. Port 256 completo, eval material 26 tipos. Determinista ×3 |

## Otras mediciones de referencia (mismo commit 94fab4f)

| Medición | Valor |
|---|---|
| `go perft 3` startpos | 175.508 nodos, 259 ms (incl. arranque) |
| `go perft 4` startpos | 10.562.564 nodos, ~4,6 s ≈ **2,3 Mnps** (bulk counting) |
| `go movetime 2000` 4 hilos | depth 15, 2.279.208 nodos ≈ **1,14 Mnps** |
| Branching medido (perft 1) | 54 (inicial) — 98–300 (mediojuego) |

Nota sobre el NPS: los ~1,1 Mnps de búsqueda con eval material están MUY por
encima del rango planificado (150–300 knps), porque la eval material es
trivial. El número que manda para el datagen es **pos/s agregadas con los 24
hilos escribiendo a disco** (piloto de F2), y el que mandará en F3 es el NPS
con red S activa (peaje esperado −30/−50 %).
