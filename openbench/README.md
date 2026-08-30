# Adopción de OpenBench — Terachess-Stockfish

Servidor permanente: `https://belzedar.duckdns.org` (ver
`openbench-spell/docs/operations.md`). Este directorio contiene lo que hay que
copiar al servidor; el motor cumple ya el contrato de `datagen-mode.md`.

## Checklist de adopción (3 pasos del contrato)

1. **Comando datagen in-engine** — HECHO. Una línea por stdin UCI, autentica
   productor/red/libro, deja UN archivo final y sale con código 0:
   ```
   datagen book {BOOK} book_sha256 {BOOK_SHA256} network {NETWORK} network_sha256 {NETWORK_SHA256} producer_sha256 {PRODUCER_SHA256} nodes 8000 count {COUNT} threads {THREADS} seed {SEED} out {OUT} write_min_ply 6
   ```
   Verificado: los cinco negativos de identidad fallan antes de crear artefactos;
   tamaño exacto `32 + 144·N`, determinismo por semilla (sha256 idéntico en 3
   corridas), resume autenticado idempotente, shards retenidos ante fallo.

2. **Formato y auditoría propios** — HECHO. `tera-bin v1` (`docs/tera-bin-v1.md`),
   espejo Python `tools/terabin.py`, auditor `tools/audit_terabin.py --strict`,
   round-trip doble motor↔python (`tools/tests/test_datagen_roundtrip.py`).
   OpenBench trata cada chunk como blob opaco; la validación de registros,
   deduplicación y merge son responsabilidad nuestra (offline).

3. **Crear el test** en `/newDatagen/` con protocolo de publicación **41** y
   `{PRODUCER_SHA256}`. Con net-2 se parte de **88,6 pos/s agregadas a T24 y
   8.000 nodos**: chunks de 100.000 posiciones estiman ~18,8 min en la torre.
   El canary inicial puede ser menor; la campaña se dimensiona a 20–40 min por
   chunk usando la velocidad observada por OpenBench, no el bench UCI.

## Instalación en el servidor

```bash
scp openbench/Terachess-Stockfish.json <server>:/opt/openbench/Engines/
ssh <server> systemctl restart openbench
```
(El servidor recalcula SHA-256 y tamaño de todo lo subido; no confía en el
cliente.)

## Parámetros elegidos y por qué

| Parámetro | Valor | Justificación |
|---|---|---|
| `test_bounds` | `[0.00, 10.00]` | Metodología general OpenBench adoptada por el propietario el 2026-08-28; sustituye los bounds locales `[1,6]` para todo test futuro |
| `test_confidence` | `[0.05, 0.05]` | α=β=0,05 |
| `win_adj` | `movecount=6 score=5000` | Red de seguridad, NO mecanismo necesario: 6/6 partidas del piloto terminaron en mate real. Umbral alto porque la escala cp es Zillions×100 (Amazona=1020) |
| `draw_adj` | `None` | Con ~0 % de tablas, adjudicar tablas solo introduciría ruido |
| STC | `10.0+0.10`, `Threads=1 Hash=32`, workload 32 | Metodología OpenBench vigente; presupuesto nominal máximo ~135 ms/jugada con 287,5 jugadas por bando |
| LTC | `30.0+0.30`, `Threads=1 Hash=128`, workload 8 | Confirmación obligatoria solo después de H1 en STC; ~404 ms/jugada nominales |
| `FIXED NODES` | `N=10000` | Reproduce las sondas locales (`tools/parallel_match.py`), útil para comparar builds sin ruido de reloj |
| Cap | 1.200 plies | La media medida es 575; un cap corto fabrica tablas falsas |

**Aviso de RAM**: el motor usa ~200 MB por hilo en tablas de historial (coste
del bucketing de 256 casillas). Un worker con `-T 24` necesita ~5 GB solo para
eso. Medido: 4,2 GB con 20 hilos de datagen.

## Estado

- [x] Comando datagen conforme al contrato
- [x] Build/bench público con net-2: **32.541** nodos; ausencia de red → exit 1
- [x] Identidad v41 dentro del chunk (productor/red/libro + resume v2)
- [x] Formato + auditor + round-trip doble
- [x] Preset JSON escrito
- [x] Libro `TERACHESS_openings_v1.epd` generado, autenticado y registrado
- [x] Preset DATAGEN instalado y campaña #361 creada
- [ ] Presets STC/LTC de este fichero desplegados en producción
- [x] STC correctos creados por CLI: OpenBench #415/#416
- [x] Bench de referencia del cliente (`bench` a secas) registrado
