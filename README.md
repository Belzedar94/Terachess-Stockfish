# Terachess-Stockfish

Motor de **Terachess II** (Jean-Louis Cazaux, 2020): tablero 16×16 = 256
casillas, 26 tipos de pieza, 64 piezas por bando. Derivado de Stockfish master,
con datagen embebido y trainer NNUE propios.

No existía ningún motor de Terachess ni referencias públicas de perft para la
variante: las de este repositorio (`oracle/perft_refs.json`) son originales.

## Estado

| Fase | Contenido | Gate |
|---|---|---|
| **F0** | Espec normativa + doble oráculo Python + 87 fixtures + suite perft + round-trip FEN | **PASS** — 30.000 posiciones diferenciales, 0 discrepancias |
| **F1** | Port a 256 casillas (Bitboard256, Move u32, ray-scan, 26 piezas) | **PASS** — 1.000 posiciones / 37,4 M nodos hoja sin discrepancias |
| **F2** | Auditoría de búsqueda (bug de LMR corregido) + datagen embebido tera-bin v1 | **PASS** — 88,6 pos/s agregadas con 24 hilos |
| **F3** | NNUE red S: trainer, lado motor, paridad ==0 cp y net-1 | **PASS** — paridad 0 cp / 1.200 posiciones; **net-1 +330,2 ± 51,7 Elo** vs material (umbral +100) |
| **F4** | Calibración estadística propia + net-2 | **PASS** — SPRT [1,6]: net-2 vs net-1 **+268 −91 =1, LLR +3,30 (~+187 Elo)** |
| **F5** | Programa de mejora continua con presupuestos declarados | documentado (`docs/staging-program.md`) |

**Red actual**: `tera-net2.tnn` (56,9 MB, sha256 `05162b61…`), 8 king-buckets,
L1=256, 8 output buckets, entrenada con 2.868.384 posiciones de self-play.
Supera a `tera-net1.tnn` por ~187 Elo (SPRT PASS) y a la evaluación material por
más de 400 Elo acumulados.

## Estructura

```
src/            motor C++ (Stockfish master portado) + datagen embebido
oracle/         doble oráculo Python (mailbox y bitboards) + fixtures + perft
tools/          terabin (formato), auditor, calibración, matches, SPRT
tools/terannue/ trainer NNUE PyTorch (red S)
docs/           contratos congelados: espec, tera-bin v1, NNUE red S, auditoría
openbench/      preset y runbook para el servidor de tests distribuido
AUDIT.md        ledger de todas las iteraciones, incluidas las descartadas
BENCH_LOG.md    firmas de bench por commit
```

## Reproducir desde cero

```bash
# 1. Motor
cd src && make -j build ARCH=x86-64-bmi2 COMP=mingw     # nunca profile-build
echo -e "position startpos\ngo perft 3\nquit" | ./stockfish.exe   # 175508

# 2. Oráculo y gates de reglas
cd ../oracle
python run_fixtures.py --impl both          # 87 fixtures, 0 fallos
python differential.py --games 50           # A vs B, 0 discrepancias
python test_aliasing.py --impl a            # 12/12
python test_roundtrip.py --positions 10000  # 0 fallos
python engine_check.py --engine ../src/stockfish.exe   # motor vs oráculo
python mass_perft.py --engine ../src/stockfish.exe --positions 1000

# 3. Datos
../src/stockfish.exe <<< "datagen out data.bin count 100000 nodes 12000 threads 8 seed 1"
python ../tools/audit_terabin.py data.bin --strict

# 4. Entrenamiento
python ../tools/terannue/train.py data.bin --epochs 6 --batch-size 512 --out ckpt

# 5. Gate de paridad (obligatorio antes de cualquier SPRT)
python ../tools/terannue/parity_harness.py net.tnn data.bin -n 1000 --stratify --features -o ref.jsonl
python ../tools/parity_gate.py --engine ../src/stockfish.exe --net net.tnn --ref ref.jsonl
```

## Reglas del proyecto (heredadas del playbook y de Spell-Stockfish)

1. **Corrección antes que fuerza.** Ninguna medición de Elo justifica una regla
   mal implementada.
2. **Los gates fallan cerrados.** Un gate fallido bloquea lo que va detrás; jamás
   se relaja un umbral después de ver el resultado.
3. **Bench determinista en cada commit** (`Bench: <nodos>` en el mensaje) y
   NPS-check contra `BENCH_LOG.md` en todo cambio de toolchain.
4. **Todo va al ledger**, incluidos los intentos descartados y las mediciones
   que no concluyen.
5. **Un knob por experimento**; las ideas con umbral o dosis pasan por arnés
   offline antes de gastar partidas.
6. **Los contratos se congelan antes de implementar** (formato de datos,
   arquitectura de red) y cualquier cambio de bytes crea una versión nueva.

## Advertencias conocidas

- La búsqueda conserva constantes afinadas para ajedrez (LMP poda entre el 18 %
  y el 89 % de los movimientos quietos con branching 150-300). Las mediciones
  A/B entre builds propios son válidas; **la fuerza absoluta no es
  representativa** hasta barrer esa familia. Ver `docs/search-audit.md`.
- Cap de partida: **≥1.000 plies**. Las partidas duran ~575 plies de media y un
  cap menor fabrica tablas artificiales.
- Cada hilo del motor consume ~200 MB en historiales (coste del bucketing de
  256 casillas): 24 hilos ≈ 5 GB.
- La regla de repetición y la de 50 movimientos son **suposiciones** (el autor
  no las especifica); marcadas como tales en `TERACHESS_SPEC.md`.
