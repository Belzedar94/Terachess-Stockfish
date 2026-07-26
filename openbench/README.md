# Adopción de OpenBench — Terachess-Stockfish

Servidor permanente: `https://belzedar.duckdns.org` (ver
`openbench-spell/docs/operations.md`). Este directorio contiene lo que hay que
copiar al servidor; el motor cumple ya el contrato de `datagen-mode.md`.

## Checklist de adopción (3 pasos del contrato)

1. **Comando datagen in-engine** — HECHO. Una línea por stdin UCI, acepta
   semilla/count/salida/hilos, deja UN archivo final y sale con código 0:
   ```
   datagen book {BOOK} nodes 12000 count {COUNT} threads {THREADS} seed {SEED} out {OUT}
   ```
   Verificado: tamaño exacto `32 + 144·N`, determinismo por semilla (sha256
   idéntico en 3 corridas), resume idempotente, shards retenidos ante fallo.

2. **Formato y auditoría propios** — HECHO. `tera-bin v1` (`docs/tera-bin-v1.md`),
   espejo Python `tools/terabin.py`, auditor `tools/audit_terabin.py --strict`,
   round-trip doble motor↔python (`tools/tests/test_datagen_roundtrip.py`).
   OpenBench trata cada chunk como blob opaco; la validación de registros,
   deduplicación y merge son responsabilidad nuestra (offline).

3. **Crear el test** en `/newDatagen/` con el preset de abajo. Dimensionado de
   chunk: a ~213 pos/s agregadas medidas con 20 hilos, 20.000 posiciones por
   chunk ≈ 1,5 min por worker; para el objetivo de 20–40 min por chunk del
   contrato usar 10.000–20.000 posiciones **por worker asignado**, ajustando
   `datagen_positions_per_chunk` al `-T` típico de la granja.

## Instalación en el servidor

```bash
scp openbench/Terachess-Stockfish.json <server>:/opt/openbench/Engines/
scp books/tera_openings_v1.epd <server>:/opt/openbench/Books/
ssh <server> systemctl restart openbench
```
(El servidor recalcula SHA-256 y tamaño de todo lo subido; no confía en el
cliente.)

## Parámetros elegidos y por qué

| Parámetro | Valor | Justificación |
|---|---|---|
| `test_bounds` | `[1.00, 6.00]` | Tasa de tablas medida **0/80 partidas** (IC95 <3,7 %) ⇒ cada partida informa ~2× que en ajedrez; fase de brecha grande: los neutros deben morir rápido (política Spell) |
| `test_confidence` | `[0.05, 0.05]` | α=β=0,05 |
| `win_adj` | `movecount=6 score=5000` | Red de seguridad, NO mecanismo necesario: 6/6 partidas del piloto terminaron en mate real. Umbral alto porque la escala cp es Zillions×100 (Amazona=1020) |
| `draw_adj` | `None` | Con ~0 % de tablas, adjudicar tablas solo introduciría ruido |
| STC | `60.0+0.6` | Las partidas duran ~575 plies (~287 jugadas): con 8s+0.08 de ajedrez cada jugada tendría ~28 ms. 60+0.6 da ~200 ms/jugada, presupuesto táctico comparable al STC de Spell |
| LTC | `180.0+1.8` | Escalón ×3 sobre STC |
| `FIXED NODES` | `N=10000` | Reproduce las sondas locales (`tools/parallel_match.py`), útil para comparar builds sin ruido de reloj |
| Hash | 16 MB (STC) | Ojo: cada hilo ya consume ~200 MB en historiales (32 MiB × 4 continuation + resto). No subir Hash sin comprobar la RAM del worker |

**Aviso de RAM**: el motor usa ~200 MB por hilo en tablas de historial (coste
del bucketing de 256 casillas). Un worker con `-T 24` necesita ~5 GB solo para
eso. Medido: 4,2 GB con 20 hilos de datagen.

## Estado

- [x] Comando datagen conforme al contrato
- [x] Formato + auditor + round-trip doble
- [x] Preset JSON escrito
- [ ] Libro `tera_openings_v1.epd` generado y subido
- [ ] Preset instalado en el servidor y primer workload lanzado
- [ ] Bench de referencia del cliente (`bench` a secas) registrado en el commit
