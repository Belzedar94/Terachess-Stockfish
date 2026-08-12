# TRANSFER.md — Documento de traspaso de Terachess-Stockfish

**Fecha**: 2026-07-26 · **De**: el arquitecto/ejecutor de las fases 0–4 y el
release 1.0 · **Para**: el agente que continúa el proyecto.

Este documento es la entrada al proyecto. Orden de lectura recomendado:
TRANSFER.md (esto) → `RESULTS.md` → `AUDIT.md` (el ledger completo, con las
autopsias de los fallos) → `PLAN.md` (el plan original aprobado) → los
contratos de `docs/`.

---

## 1. Qué es esto y dónde está

Motor de **Terachess II** (Cazaux 2020; 16×16, 26 tipos, 64 piezas/bando)
derivado de Stockfish master, con datagen embebido, trainer NNUE propio y un
arnés de verificación completo. Primer motor conocido de la variante.

- **Repo público**: https://github.com/Belzedar94/Terachess-Stockfish
  (remoto `origin` ya configurado; cuenta `gh` autenticada: Belzedar94).
- **Release publicado**: v1.0 con binario, `tera-net1.tnn`, `tera-net2.tnn` y
  `SHA256SUMS.txt` — https://github.com/Belzedar94/Terachess-Stockfish/releases/tag/v1.0
- **Working tree local**:
  `C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Terachess-Stockfish\`

## 2. Estado: qué está HECHO (con su evidencia)

Las 5 fases del plan tienen gate superado. Resumen con los números que
importan (detalle y comandos exactos en `RESULTS.md` y `AUDIT.md`):

| Hito | Evidencia |
|---|---|
| Espec normativa (`TERACHESS_SPEC.md`) | 26 piezas con semántica operativa, 5 contradicciones entre fuentes resueltas, suposiciones `[SUPUESTO]` marcadas |
| Doble oráculo Python (`oracle/`) | 30.000 posiciones diferenciales A↔B, 87 fixtures a mano, 13.000 round-trips FEN, 12/12 aliasing — todo a 0 fallos |
| Port a 256 casillas (`src/`) | Motor vs oráculo: 87 fixtures con listas exactas + 1.000 posiciones / 37,4 M nodos hoja, 0 discrepancias. Perft startpos 54 / 2.916 / 175.508 / 10.562.564 |
| Auditoría de búsqueda (`docs/search-audit.md`) | Bug de LMR corregido (`min(moveCount,40)*62`); LMP documentado SIN tocar (familia F5) |
| Datagen embebido (`src/datagen.cpp`) | 88,6 pos/s con 24 hilos; determinismo sha256 ×3; resume probado con kill real; round-trip doble 200/200 |
| ADR-001 unidades (`docs/eval-units.md`) | `to_cp` = identidad; gate `check_label_units.py` (pendiente 1,00 en datos buenos, 0,26 en los contaminados) |
| NNUE motor+trainer (`src/nnue/`, `tools/terannue/`) | Gate de paridad **0 cp exactos** en 1.200/800/300 posiciones; acumulador incremental = refresh en 6.000 plies |
| net-1 | +330,2 ± 51,7 Elo vs eval material (gate exigía ≥+100) |
| net-2 (actual) | SPRT [1,6] vs net-1: **+268 −91 =1, LLR +3,30 (~+187 Elo)** |
| Calibración estadística (`docs/statistics.md`) | 1 tabla en 606 partidas; 575 plies de media; bounds [1,6] justificados con coste medido |
| Release 1.0 | Identidad del motor, GPLv3 (`Copying.txt`, `AUTHORS`), notas con limitaciones declaradas |

## 3. Activos SOLO LOCALES (no están en git — no los pierdas)

`data/` pesa ~12 GB y está en `.gitignore`. Lo irreemplazable:

| Archivo | Qué es |
|---|---|
| `data/c3_final.bin` (413 MB) | **El dataset de net-2**: 2.868.384 registros tera-bin v1, auditado `--strict`, unidades OK. Regenerable en ~4 h de torre, pero con OTRA semilla ya no reproduce net-2 |
| `data/campaign3.bin.*` | Shards originales de la campaña 3 (el merge es c3_final) |
| `data/c2_*.bin`, `data/c3_15/28.bin` | Escalera de la curva datos→calidad (90 k → 2,8 M) |
| `nets/tera-net1.tnn`, `nets/tera-net2.tnn` | Las redes (también en el release de GitHub — ahí están a salvo) |
| `data/net090/180/A/B/15/1pre.tnn` | Redes de la escalera y las archivadas como evidencia de fallo |
| `data/parity_ref*.jsonl`, `data/pref_n2.jsonl` | Volcados de referencia del gate de paridad |
| `data/*_ckpt/` | Checkpoints de entrenamiento (resume auténtico verificado) |

Recomendación pendiente: aplicar la regla 3-2-1 a `c3_final.bin` y a los
checkpoints (riesgo #15 del plan; nunca se hizo backup externo).

Los JSON de resultados de gates están duplicados en `evidence/` (versionado).

## 4. Cómo se opera esto (lo que no está en ningún manual)

- **Build**: `export PATH=/c/msys64/mingw64/bin:/c/msys64/usr/bin:$PATH && cd src && make -j build ARCH=x86-64-bmi2 COMP=mingw TMP='C:\Users\djime\AppData\Local\Temp' TEMP='C:\Users\djime\AppData\Local\Temp'`.
  Las variables TMP/TEMP son necesarias bajo el harness (make de MSYS2 las
  pierde). **PGO PROHIBIDO** en esta máquina (g++ 15.2: binario 3,3× más lento
  con firma idéntica — incidente documentado en Spell).
- **Firma de bench**: `bench 16 1 5` → **21519** en el commit actual
  (`0668431`). Cada commit lleva `Bench: <nodos>`; histórico en `BENCH_LOG.md`.
- **Máquina**: Windows 10, 25 hilos lógicos, RTX 3080 (CUDA OK, torch 2.7.1).
  **~200 MB de RAM por hilo del motor** (historiales); 20 hilos de datagen ≈
  4,2 GB. **No entrenes en GPU con el datagen de 20+ hilos activo**: el paging
  file se agota y mata el DataLoader (pasó; el retrain con `--workers 2` y la
  máquina libre funcionó).
- **Procesos**: en Windows es `taskkill //F //IM <img>`, NO `pkill` (un `pkill`
  fallido dejó un SPRT viejo vivo midiendo el motor contra sí mismo — ver §6).
- **Matches**: SIEMPRE contra COPIAS estables del binario (recompilar bajo un
  match rompe el enlace del .exe y el runner se cuelga). El runner usa el
  oráculo Python como árbitro (jugada ilegal = derrota reportada).
- **Caps de partida ≥1.000 plies** en toda sonda/SPRT/datagen (media real 575;
  un cap de 300 fabricó 100 % de tablas falsas; el datagen ya usa 1.400).
- Ahora mismo NO hay ningún proceso del proyecto corriendo. (Si ves
  `Horde-Stockfish-*` en tasklist: es OTRO proyecto del propietario, no lo
  toques.)

## 5. Qué FALTA, por prioridad (el backlog real)

1. **Campaña de régimen con bootstrap** — la más valiosa. Generar 10–30 M
   registros CON net-2 cargada como evaluación (`setoption EvalFile` no aplica
   al datagen: hay que añadir una opción `net <path>` al comando datagen o
   cargarla vía UCI antes — verifica cómo quedó integrado; si el datagen no
   carga red aún, esa integración es el primer paso). Antes de lanzar:
   `check_label_units.py` sobre un piloto CON red (las etiquetas cambian de
   distribución). Después: net-3, gates de paridad+unidades, SPRT vs net-2.
2. **Confirmación LTC** — deuda metodológica declarada: el SPRT de net-2 fue a
   nodos fijos (8 k) en un solo "TC". La política del proyecto exige
   confirmación a TC largo antes de dar nada por definitivo ("methodology
   needs both", sign-flip de Spell). Además la gestión de tiempo real
   (wtime/btime) está **sin ejercitar en partidas** — todo fue a nodos fijos.
   Valida time management antes del primer match con reloj.
3. **Familia P1 del programa F5: LMP** (`docs/staging-program.md`) — la mayor
   distorsión restante (poda 18–89 % de los quiets con constantes de ajedrez).
   Arnés offline PRIMERO (recall/class-size), matriz, 2-3 puntos Pareto a
   SPRT. Presupuesto declarado: 6 SPRT. Luego P2 (escalas de futility) y P3
   (dosis del tope de LMR).
4. **Libro de aperturas** — `tools/make_book.py` existe pero
   `tera_openings_v1.epd` nunca se generó (las campañas usaron startpos +
   random plies). Generarlo con net-2 y registrar su SHA-256.
5. **OpenBench** — `openbench/Terachess-Stockfish.json` y el runbook están
   escritos pero NO instalados en el servidor (belzedar.duckdns.org). Con el
   preset instalado, las campañas de régimen pueden ir a la granja.
6. **Verificación externa de reglas** — cotejo con Jocly (Terachess I,
   subconjunto), Ai Ai (única implementación integral de II) y el ZRF de
   Cazaux (`cazauxchess.zip` — nunca descargado/verificado); consulta al autor
   sobre repetición/regla de 50 y las 3 lagunas `[SUPUESTO]` de la espec;
   publicación de las refs de perft en talkchess. Cualquier divergencia
   dispara el protocolo dominó de AUDIT.md.
7. **Fusión de output buckets 0-1-2** (al 0,2 % y 3 % del dataset, bajo el 5 %
   del contrato) — desviación declarada en `docs/nnue-tera-s.md`; requiere
   `arch_hash` nuevo y re-pasar el gate de paridad. Junto con la variante
   L1=128 y el factor royal-relative, forma la familia P6.
8. **Kindergarten sliders** — el motor sigue con ray-scan (correcto y
   suficiente hasta ahora); el upgrade kindergarten con gate de identidad
   quedó planificado y sin hacer. Solo si el perfil lo justifica.
9. **Cuckoo/upcoming_repetition** — desactivado por ADR (tabla de 8192 no
   escala); re-evaluar con tabla 2^17 si la detección temprana de repetición
   importa en LTC.
10. **Modelo WDL propio** (P7) — `wdl` sigue sin sentido; ajustar con
    resultados reales cuando haya volumen.
11. **Release 1.1** cuando net-3 pase sus gates.

## 6. Los errores que ya cometimos para que tú no los repitas

Cada uno tiene autopsia completa en `AUDIT.md`:

1. **Unidades**: el campo `InternalUnits` de Stockfish contiene cp YA
   convertidos por el modelo WDL de ajedrez (que da 0 a las piezas fairy).
   Costó una red entera (−511 Elo). El gate de unidades existe por esto;
   córrelo sobre TODA campaña antes de entrenar.
2. **Cap de plies**: 300 plies → 100 % tablas falsas. Media real: 575.
3. **Sondas defectuosas, tres en un día**: pipe con `quit` que aborta la
   búsqueda (0 nodos, parecía motor roto); filtro que capturaba 2 líneas por
   posición (correlación 0,202 donde había 0,975); SPRT midiendo el motor
   contra sí mismo 160 partidas (lo delató la simetría +20−20/+40−40).
   Regla: **valida toda sonda nueva contra una medición ya conocida, y
   desconfía de los resultados demasiado limpios.**
4. **Gate que pasaba vacío**: parity_gate daba PASS con 0 posiciones.
   Corregido (`--min-positions`); mantén el patrón "falla cerrado".
5. **Proxy no monótona**: el error de aproximación al material predecía
   4,5–36 M posiciones necesarias; la realidad fue una transición de fase
   (−545 → +330 Elo entre 370 k y 1,5 M). Las proxies diagnostican; la fuerza
   se decide con partidas.

## 7. Reglas de la casa (innegociables)

Las del `README.md` §Reglas del proyecto: gates que fallan cerrados y NUNCA se
relajan tras ver un resultado; bench firmado en cada commit; un knob por
experimento; arnés offline antes que granja; contratos congelados (cambio de
bytes = versión nueva); TODO al ledger, incluidos tus propios errores con
números; LTC confirma antes de merge definitivo.

## 8. Verificación de entorno para el traspaso

Antes de cambiar NADA, deja estos gates en verde (≈10 min) y anota los
resultados como tu primera entrada de AUDIT ("traspaso verificado"):

```bash
cd src && make -j build ARCH=x86-64-bmi2 COMP=mingw   # (con TMP/TEMP, ver §4)
printf 'bench 16 1 5\nquit\n' | ./stockfish.exe       # => Nodes searched: 21519
cd ../oracle
python run_fixtures.py --impl both                    # 87 fixtures, 0 fallos
python engine_check.py --engine ../src/stockfish.exe  # 0 fallos
cd ../tools
python parity_gate.py --engine ../src/stockfish.exe \
    --net ../nets/tera-net2.tnn --ref ../data/pref_n2.jsonl   # PASS (0 cp)
python check_label_units.py --engine ../src/stockfish.exe \
    --net ../nets/tera-net2.tnn \
    --data ../data/c3_final.bin --positions 150               # PASS, NNUE activa, pendiente ~1.0
```

Si algo de esto NO está verde, no construyas encima: diagnostica primero.
