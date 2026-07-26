# Procedencia de las redes

Cada red debe poder responder: qué código la generó, qué datos vio, con qué
objetivo se entrenó, y con qué binario se verificó su carga y su juego
(principio 3 del playbook). Los `.tnn` no se versionan en git (56,9 MB cada uno);
lo que se versiona es este recibo.

## tera-net1.tnn

| Campo | Valor |
|---|---|
| sha256 | `6bb5cd483f5a514e970eac129999f4a261e2a700f2d300cf808afe4fb71d6d3d` |
| Tamaño | 56.858.966 B |
| Arquitectura | red S: 8 king-buckets × 51 planos × 256 casillas, L1=256, 8 output buckets, FT_SCALE=128 |
| `arch_hash` | `92935a4de67fb1804e3fab2e529157e7bd6732b00bae867e74c9e6a824f0dd26` |
| Datos | 1.522.654 registros, 4.183 partidas (campaña 3, instantánea) |
| Comando de datagen | `datagen out campaign3.bin count 6000000 nodes 8000 threads 24 seed 777 write_min_ply 6` |
| Motor generador | commit `038b723` (post ADR-001: unidades correctas) |
| Auditoría de datos | `audit_terabin --strict` exit 0, 0 avisos; gate de unidades pendiente 1,004 |
| Entrenamiento | 6 épocas, batch 1024, λ=1,0, scaling 1000, factorización activa |
| Loss | train 1,25e-3 → 1,03e-4; val 8,72e-4 → **6,81e-4** (monótona) |
| Gate de paridad | **0 cp** en 600 posiciones estratificadas |
| Fuerza | **+330,2 ± 51,7 Elo** vs evaluación material (100 partidas, 10k nodos) |
| Calidad | corr con material en posiciones frescas 0,937; error mediano 133 cp |

## tera-net2.tnn (actual)

| Campo | Valor |
|---|---|
| sha256 | `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861` |
| Tamaño | 56.858.966 B |
| Arquitectura | idéntica a net-1 (mismo `arch_hash`) |
| Datos | 2.868.384 registros (campaña 3 completa) |
| Motor generador | commit `038b723` |
| Auditoría de datos | `audit_terabin --strict` exit 0; gate de unidades pendiente 1,019 |
| Entrenamiento | 6 épocas, batch 1024, 2 workers, λ=1,0 |
| Loss | train 7,98e-4 → 1,13e-4; val 5,97e-4 → **4,16e-4** (monótona) |
| Gate de paridad | **0 cp** en 300 posiciones |
| Fuerza | **SPRT PASS** vs net-1: +268 −91 =1 en 360 partidas, LLR +3,30, ~+187 Elo, bounds [1, 6] declarados antes |
| Calidad | corr 0,942; error mediano **100 cp** |

## Redes archivadas como evidencia de fallo

- **net1pre** (sha256 `6c6e91b6e40c…`): 408 k registros con unidades erróneas
  (pre ADR-001). −511,5 ± 63,0 Elo. Conservada como evidencia del bug de escala.
- **netA**: 220 k registros, unidades ya correctas. −544,7 ± 79,4 Elo. Evidencia
  de que el arreglo de unidades era necesario pero no suficiente.
- **net090 / net180 / netB**: escalera de 90 k / 180 k / 370 k registros usada
  para levantar la curva datos→calidad (`docs/net1-postmortem.md`).

## Cómo reproducir la verificación de una red

```bash
python tools/terannue/parity_harness.py nets/tera-net2.tnn data.bin \
    -n 600 --stratify --features -o ref.jsonl
python tools/parity_gate.py --engine src/stockfish.exe \
    --net nets/tera-net2.tnn --ref ref.jsonl          # exige 0 cp
python tools/parallel_match.py --a src/stockfish.exe --b src/stockfish.exe \
    --a-opt "EvalFile=$(pwd)/nets/tera-net2.tnn" \
    --b-opt "EvalFile=$(pwd)/nets/tera-net1.tnn" \
    --games 360 --nodes 8000 --concurrency 12 --max-plies 1000
```
