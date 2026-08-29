# AUDIT.md — Terachess-Stockfish

Running log of all iterations. Every implementation/iteration/fix (including
discarded attempts) gets an entry: hypothesis → files changed → validation →
decision → learnings. Columna adicional obligatoria en familias de experimentos:
"espacio explorado / presupuesto usado" (lección retroactive-review E4 de Spell).

Convenciones heredadas de Spell-Stockfish\AUDIT.md:
- Bench determinista por commit (firma = nodos totales) registrado en BENCH_LOG.md;
  disciplina OpenBench `Bench: <nodos>` en el mensaje de commit.
- Gates fallan cerrados: un gate fallido bloquea downstream y se registra con
  evidencia exacta; nunca se relaja un umbral después de ver el resultado.
- Divergencias de reglas: protocolo dominó (fixture nuevo → refs perft
  regeneradas → rebase de ramas → rebuild del baseline congelado).

---

## 2026-07-18 — ADR-0: elección de base (decisión del propietario)

**Contexto**: plan de implementación completo elaborado y aprobado
([PLAN.md](PLAN.md)). El análisis comparado (4 opciones, con red-team
adversarial verificando números contra código real) recomendaba la opción C
(extender Fairy-Stockfish-VLB, 256 casillas ya operativas) por menor tiempo
hasta la primera red (11–18 semanas vs 15–24) y menor riesgo de reglas (Betza
batido en cientos de variantes).

**Decisión del propietario**: **opción A — motor especializado partiendo de
Stockfish master, portado a 256 casillas.** Razón textual: "El overhead de
tomar como base un motor generalista es muy peligroso. Prefiero que empecemos
de uno especializado y que hagamos tantos cambios y mejoras como necesitemos."

**Consecuencias asumidas** (detalle en [docs/adr/ADR-000-base-especializada.md](docs/adr/ADR-000-base-especializada.md)):
- El riesgo dominante pasa a ser la corrección de reglas (movegen a mano de 26
  tipos sin perft de referencia) → mitigado con triple oráculo (Python desde la
  espec + Jocly + FSF-VLB opcional) y presupuesto de verificación reforzado.
- FSF-VLB queda relegado a referencia de código (Bitboard256 del propietario) y
  tercer oráculo opcional de perft; nunca base.
- Punto de partida concreto: chasis Spell-Stockfish (SF master 2026 con Move32/
  TT12B/butterfly-64k ya migrados), extirpando la capa spell; fallback SF master
  virgen si la extirpación no es limpia en ≤1 semana (micro-gate F1a).

**Estado**: plan aprobado; versión objetivo Terachess II (2020) confirmada por
el propietario. Siguiente paso: Fase 0 (TERACHESS_SPEC.md + oráculo Python +
suite perft + round-trip FEN — todo antes de una línea de búsqueda).

---

## 2026-07-18/19 — F0 parcial + micro-gate F1a: PASS

**Hipótesis**: el chasis Spell-Stockfish puede volver a SF master puro en ≤1
semana de trabajo (micro-gate del ADR-0); la espec de Terachess II es
transcribible del Interactive Diagram sin ambigüedades bloqueantes.

**Hecho**:
- `TERACHESS_SPEC.md` v1.0: 26 piezas con semántica operativa (bent riders
  §4.1, pantallas §4.2, salto de Rey §6.3, e.p. §6.2, promoción §6.4),
  suposiciones FIDE marcadas [SUPUESTO], 5 contradicciones entre fuentes
  resueltas con criterio documentado.
- Arnés F0: run_fixtures / differential A↔B / perft_suite / test_roundtrip /
  test_aliasing (6 pares). Contrato tera-bin v1 congelado (docs/tera-bin-v1.md).
- **F1a (commit 72bfb25)**: capa spell extirpada por completo (16 archivos
  eliminados, ~25 modificados). Validación: perft startpos d5=4.865.609,
  d6=119.060.324; kiwipete d4=4.085.603; pos3 d5=674.624; pos4 d4=422.333;
  pos5 d4=2.103.487; pos6 d4=3.894.594 — TODOS exactos contra las referencias
  públicas de ajedrez. Bench determinista 2.692.515 nodos en 3 corridas
  (~1,38 Mnps, 1 hilo, eval material fallback, sin red). Mate-en-1, ahogado y
  mate-en-raíz correctos. Conservado: Move u32 (bits altos libres), TT 12 B,
  MAX_MOVES 32768, arena MovePicker.
- Conteo a mano del arquitecto para el gate de movegen 256: **perft(1)
  startpos Terachess II = 54** (32 peones + 4 caballos + 4 camellos +
  4 jirafas + 2 máquinas + 2 elefantes + 6 trolls; filas 1–4 llenas ⇒ Lion/
  Buffalo/Duchess/hoppers/bent-riders/Rey/Príncipe = 0). Pendiente de
  verificación independiente por el oráculo.

**Decisión**: gate F1a PASS → la base del port es el chasis extirpado (no hizo
falta el fallback a SF virgen). Port 256 lanzado en 4 etapas
(docs/port-256-design.md como contrato congelado de representación).

**Learnings**: (1) el make de MSYS2 pierde TMP/TEMP bajo el harness — pasarlas
como variables de make; documentado en el contrato de build. (2) Los node
counts de búsqueda del chasis no son byte-idénticos a SF oficial (constantes
SPSA del fork heredadas) — irrelevante: F2 re-deriva todas las constantes para
branching 150–250 de todos modos. (3) Interrupción por límite de sesión a
mitad de cirugía deja estado parcial en disco — verificar SIEMPRE el estado
real con ls/git status antes de reanudar agentes.

---

## 2026-07-19 — GATE FASE 0: PASS (commit 5c4de4e)

**Hipótesis**: dos implementaciones Python del oráculo escritas de forma
independiente (mailbox vs bitboards-int, agentes distintos, sin ver el código
del otro) + fixtures derivados a mano por un tercer agente convergen a 0
discrepancias, dando un oráculo congelado fiable de Terachess II.

**Evidencia** (toda ejecutada, exit 0):
- `run_fixtures.py --impl both`: **87 fixtures, 0 fallos** (a la primera).
- `differential.py`: **30.000 posiciones** en 200 partidas aleatorias
  (6.000 + 24.000), **0 discrepancias** A↔B.
- `test_roundtrip.py`: 10.000 (A) + 3.000 (B) posiciones, 0 fallos.
- `test_aliasing.py`: 12/12 OK en ambas (salto de rey = 11 destinos exactos
  del ejemplo del autor; ep-sobre-Príncipe; ep-por-casilla; troll última
  fila; contadores; turno).
- `perft_suite.py generate`: 15 posiciones con A computando y B verificando:
  startpos **54 / 2.916 / 175.508** (54 = conteo a mano del arquitecto,
  3 derivaciones independientes coinciden) + 14 random-walks (branching
  medido 98–300 — confirma el supuesto 150–300 del plan).
- Espec ampliada con 3 resoluciones nuevas [SUPUESTO]: doble-paso-a-última-fila
  (promociona Y fija ep; el ep retira la pieza promocionada), movimiento
  dentro de la última fila (promociona), simetría completa del Rhino en s1.

**Decisión**: oráculo A congelado como primario (tag pendiente al cerrar F1),
B como diferencial. Gate F0 PASS → F1 (port 256) desbloqueado como camino
crítico (ya en marcha).

**Pendiente honesto (no bloquea el gate interno, sí la canonicidad plena)**:
cross-check externo con Jocly (subconjunto Terachess I) y FSF-VLB (subconjunto
Betza); verificación del ZRF cazauxchess.zip; consulta a Cazaux (repetición/50,
lagunas [SUPUESTO]); publicación de las refs de perft en talkchess. Programado
como verificación paralela, no como precondición de F1.

---

## 2026-07-19 — GATE FASE 1: PASS (commit 94fab4f)

**Hipótesis**: el chasis SF master puede portarse a 256 casillas / 26 tipos
conservando corrección demostrable, y una tercera implementación independiente
(C++) convergerá con las dos del oráculo.

**Evidencia**:
- **Cross-check motor↔oráculo**: 87 fixtures con listas exactas de movimientos
  legales + 15 posiciones de perft (d1–d2) → **0 fallos** (`engine_check.py`).
- **Perft masivo**: 1.000 posiciones de random-walk, perft(2), **37.371.980
  nodos hoja**, **0 discrepancias** motor vs oráculo (`mass_perft.py`, 396 s).
- Perft startpos: 54 / 2.916 / 175.508 / 10.562.564 (d4 en ~4,6 s ≈ 2,3 Mnps).
- **Bench determinista**: `bench 16 1 5` = **22.723** nodos, idéntico en 3
  corridas. Búsqueda real depth 15 @ 2 s / 4 hilos sin crash.
- Tres implementaciones independientes (Python mailbox, Python bitboards-int,
  C++ bitboards-256) coinciden en todo lo medido.

**Decisión**: gate F1 PASS. Representación congelada (Bitboard256 4×u64, Square
u16, Move u32, ray-scan sin magics, historiales bucketed a 8 clases).

**Learnings**: (1) el conteo a mano de perft(1)=54 acertó y sirvió de gate
barato para la etapa de movegen. (2) `Move::null()` colisionaba con b1c1 en el
layout nuevo: se movió a los bits de tipo especial (desviación del contrato,
documentada en el código). (3) `DirtyThreat` empaquetaba casillas en 8 bits —
incompatible con SQ_NONE=256; eliminado (lo reintroducirá F3 si el NNUE lo
necesita, con ancho correcto). Confirma el riesgo #2 del plan (centinelas de
8 bits) como real y sistémico.

---

## 2026-07-19 — F2a: auditoría de búsqueda — 1 bug confirmado, 1 métrica invalidada

**Hipótesis**: las constantes chess-tuned del chasis están fuera de rango con
branching 150–300 y contaminan cualquier medición (lección LMR de Spell).

**Confirmado — bug de LMR** (`docs/search-audit.md` §1): el término lineal
`r -= moveCount * 62` desborda al logarítmico `reductions[d]·reductions[mn]`.
A depth 8 las reducciones se vuelven **extensiones a partir de moveCount 93**:
mn=180 → **extiende 4,68 plies**; mn=300 → extiende 11,41. Todos los nodos de
mediojuego de Terachess están en la zona perversa. Corregido con
`std::min(moveCount, 40) * 62`; firma de bench 22.723 → 21.519 (árbol distinto,
como debe ser). Sonda A/B a nodos fijos ejecutada con el oráculo como árbitro.

**Documentado sin tocar — LMP** (§2): `(3 + depth²)/(2 − improving)` poda entre
el 18 % y el 89 % de los movimientos quietos según profundidad (en ajedrez no
poda casi nada). Es familia con umbral y dosis ⇒ arnés offline + matriz en F5
con presupuesto declarado (6 SPRT), NO one-shot (lección C1 de Spell).

**Invalidado honestamente — métrica de blunder** (§3): la calibración midió
0,0 % / 2,5 % / 0,0 % de blunder a 10/20/40 k nodos y depth media 8,4–10,7.
**No es creíble**: con eval material el árbitro a 4N usa la MISMA función ⇒ mide
autoconsistencia, no calidad; y la depth está inflada por el LMP anterior
(profundidad hueca en el sentido exacto de Spell). La regla predeclarada del
plan no puede resolverse sin red. Sustituida por: nodos de datagen = 25.000 por
presupuesto de tiempo y rendimientos decrecientes del delta; la regla original
se re-ejecuta en F3b con la red S cargada. **Deuda técnica declarada, no gate
superado.**

**Learnings**: la disciplina del playbook (auditar ANTES de medir) se pagó sola
en la primera hora: sin ella habríamos calibrado el datagen entero sobre una
métrica que medía el eco de su propia evaluación.

---

## 2026-07-19 — F2a bis: longitud de partida (dato fundacional de la variante)

**Hipótesis**: Terachess con eval material es demasiado tablero para producir
partidas decisivas; habrá que adjudicar por evaluación.

**Refutada por la medida.** Piloto de 6 partidas a 10 k nodos con cap de 1.200
plies: **media 575 plies, 6/6 decisivas por MATE real, 0 tablas**, ~18 s por
partida. La tabla completa está en `docs/search-audit.md` §3bis.

El error estaba en mi propia sonda: con cap de 300 plies medí 100 % de tablas y
estuve a punto de tomarlo por una propiedad del juego. Era un artefacto del cap.
Regla operativa nueva: **cap ≥1.000 plies en toda sonda, SPRT y datagen**.

Consecuencias: bounds SPRT [1, 6] confirmados (tasa de tablas baja ⇒ cada
partida informa ~2× que una de ajedrez); ~1 h por SPRT de 5.000 partidas con 24
hilos; la adjudicación queda como red de seguridad (|eval| ≥5.000 cp × 6), no
como mecanismo necesario.

---

## 2026-07-19 — GATE FASE 2 (datagen): PASS (commit b78036e)

**Hipótesis**: el patrón run7 (shards + merge verificado + resume idempotente)
es clonable sobre el formato tera-bin v1 y alcanza el umbral de viabilidad de
≥60 pos/s agregadas con 24 hilos.

**Evidencia**:
- Comando UCI de una línea conforme al contrato OpenBench; exit 0 y un único
  archivo final.
- Tamaño exacto `32 + 144·N`; `audit_terabin.py --strict` exit 0 (tasa de
  escritura 52,3 %, WDL 41,2/19,9/39,0, 0 registros sin resultado).
- **Round-trip doble 200/200**: `to_fen(unpack(raw))` idéntico al FEN del motor
  y `pack(unpack(raw)) == raw` byte a byte.
- **Determinismo**: sha256 idéntico en 3 corridas y 2 recompilaciones.
- **Resume real** (kill + relaunch): 4 shards reparados, 2.804 registros
  supervivientes adoptados, streams splitmix disjuntos, merge verificado,
  auditoría estricta y round-trip 150/150 tras la reanudación.
- **Piloto**: 24 hilos, 25 k nodos → **88,57 pos/s agregadas** (3,69 por hilo).
  Gate ≥60 → **PASS**.

**Corrección aplicada tras el piloto**: `MaxGamePlies` 600 → 1.400. Con la
media medida de 575 plies, el cap de 600 truncaba ~29 % de las partidas en
tablas artificiales y envenenaba las etiquetas WDL — el mismo error que cometí
en mi sonda, detectado aquí por triangulación entre dos mediciones
independientes.

**Decisión**: campaña 1 lanzada con 12 k nodos y 20 hilos (2 M registros,
~174 pos/s reales). Es una campaña **de escala piloto**: su objetivo es cerrar
la cadena datagen→trainer→red→gate de principio a fin, no producir la red
definitiva. La campaña de régimen (20–30 M) queda para cuando la cadena esté
verificada.

---

## 2026-07-19 — SONDA #1 lmr-cap-40: INCONCLUSA (se conserva por corrección)

| id | knob | partidas | W/L/D | Elo | veredicto | espacio/presupuesto |
|---|---|---|---|---|---|---|
| #1 | `r -= min(moveCount,40)*62` vs `moveCount*62` | 80 @10k nodos | +41 −39 =0 | +8,7 ± 38,9 | **INCONCLUSO** | 1 punto / familia abierta, presupuesto 4 SPRT |

**Autopsia**: la barra de error es 4× el efecto. La sonda descarta una regresión
grande, no demuestra ganancia. **Se conserva el cambio igualmente** porque su
justificación no es empírica sino matemática: la fórmula original extendía 4,7
plies los movimientos tardíos (ver `docs/search-audit.md` §1), lo cual es
indefendible en cualquier árbol. Resolver ±10 Elo exige miles de partidas.
Pendiente para F4/F5: SPRT formal del toggle + barrido de la dosis del tope
(20/40/60/dinámico según legales).

**Subproducto valioso**: **0 tablas en 80 partidas** (media 464,7 plies, 0
anomalías, 0 jugadas ilegales en ~37.000 plies arbitrados por el oráculo).
Con 0/80, el intervalo de confianza al 95 % sitúa la tasa de tablas por debajo
del 3,7 % — aún mejor que el 4 % de Spell. Confirma bounds [1, 6] y hace el
SPRT muy eficiente en esta variante.

**Limitación operativa detectada**: el runner de partidas es secuencial (23 s
por partida) ⇒ un SPRT de 5.000 partidas serían ~32 h. Necesita paralelismo
antes de F4; anotado como tarea. **Resuelto el mismo día**:
`tools/parallel_match.py` (N procesos con semillas disjuntas) — 5.000 partidas
bajan a ~1,6 h con 20 procesos.

---

## 2026-07-19 — CADENA COMPLETA VALIDADA CON DATOS REALES

**Hipótesis**: los componentes verificados por separado (datagen, formato,
auditor, trainer) componen sin sorpresas cuando los datos son reales y no
sintéticos.

**Evidencia** (campaña 1 en curso, instantánea al 20 %):
- Muestra de un shard vivo: **16.607/16.607 registros válidos**, 0 corruptos,
  todos `pack(unpack(raw)) == raw`. Resultados parcheados: 0 registros con
  "sin resultado". Distribución WDL 40/21/40, |score| ≤1.691 (respeta el
  eval_limit), media 17,9 cp.
- `merge_shards.py` (nuevo): instantánea de los 20 shards →
  **407.839 registros, 1.160 partidas, 341,8 registros/partida**,
  `audit_terabin.py --strict` **exit 0, 0 avisos**, tamaño exacto.
- **Trainer sobre datos REALES** (no sintéticos): entrena en CUDA, loss
  descendente (9,27e-4 → 5,32e-4 en 60 pasos), checkpoint escrito.
- **Export TNN1 desde datos reales**: 56.858.966 B (56,86 MB), recarga
  verificada **bit-exacta**, pesos dentro de rango (|bias FT| máx 3 de 8.191;
  |psqt| máx 746) — la garantía anti-overflow del contrato se cumple con
  margen amplio en datos reales.

**Decisión**: la cadena datagen→formato→auditor→trainer→TNN1 queda cerrada.
Falta el último eslabón (motor↔red) y su gate de paridad ==0 cp, en curso.
Entrenando en paralelo una red preliminar sobre los 408 k registros para tener
artefacto real con el que ejecutar el gate en cuanto el motor lo soporte.

**Learning**: haber congelado el formato y el contrato de red ANTES de escribir
nada permitió que tres implementaciones independientes (datagen C++, terabin
Python, trainer PyTorch) compusieran a la primera. El único choque —la
contradicción fc0 512 vs 256— lo detectó la verificación cruzada del trainer
contra el propio contrato, antes de entrenar ninguna red real.

---

## 2026-07-19 — F3b GATE DE PARIDAD: PASS (0 cp) — commit 038b723

**Evidencia**: 1.200 posiciones reales estratificadas (los 8 output buckets
representados: 144/144/144/144/143/143/145/193), red **real** entrenada sobre
datos **reales**, comparando motor C++ contra `quantized_forward.py`:
**0 discrepancias** en índices de features de ambas perspectivas, `psqt`,
`positional`, `total_cp` y `bucket`. Tolerancia exigida y obtenida: exactamente
0 cp.

Verificaciones del lado motor: acumulador incremental vs refresh completo en
**6.000 plies** de 20 partidas (0 diferencias, incluidas las de deshacer y los
867 refrescos forzados por cambio de bucket/espejo); cargador que rechaza las 5
corrupciones probadas (magic, versión, arch_hash, dims, tamaño) con causa
exacta y cae a material sin crash; `bench 16 1 5` = 21.519 **exacto** sin red
(la integración no altera el árbol); `perft 3` = 175.508; peaje de nps 14,2 %.

---

## 2026-07-19 — F3 GATE DE FUERZA: **FAIL** (−511 Elo) → causa raíz ADR-001

| id | red | partidas | W/L/D | Elo | veredicto |
|---|---|---|---|---|---|
| net1pre | S, 8 buckets, 408 k registros, 6 épocas | 160 @10k nodos | +8 −152 =0 | **−511,5 ± 63,0** | **FAIL** (gate exigía ≥+100) |

**Autopsia** (el gate hizo exactamente su trabajo): con la paridad en 0 cp, la
fontanería estaba bien; el fallo tenía que estar en la red o en los datos.
Diagnóstico por medición, no por conjetura:

1. Distribución de datos: descartada como causa. El desequilibrio material tenía
   desviación 1.408 cp y el 36,8 % de las posiciones superaba los 1.000 cp — hay
   señal de sobra para aprender el valor de las piezas.
2. Comparación directa red vs material sobre las mismas 120 posiciones:
   correlación **0,992** pero desviación **499 frente a 2.043** y ratio mediano
   **0,242**. La red era una copia fiel del material **a 1/4 de escala**.
3. Origen: los labels ya venían comprimidos. `score.cpp:34` guarda en un campo
   llamado `InternalUnits` el valor **ya convertido** por `to_cp`, y `to_cp`
   dividía por el `a` del modelo WDL **de ajedrez**, que cuenta
   `P+3N+3B+5R+9Q` (toda pieza propia de Terachess vale cero) y satura el
   material a [17,78] cuando la inicial tiene 128 piezas.

Con la evaluación comprimida 4×, todos los márgenes de poda —calibrados en la
escala del material— se volvieron efectivamente 4× más agresivos. De ahí los
−511 Elo.

**Decisión (ADR-001, `docs/eval-units.md`)**: `to_cp` pasa a ser la identidad —
en Terachess la unidad interna ES el centipeón reportado. Bench sin cambios
(21.519): el árbol no se toca, solo las unidades. Los 720.684 registros
generados se **descartan**: su escala depende del material a través de `a`, así
que no son recuperables con un factor constante.

**Gate nuevo para que no se repita**: `tools/check_label_units.py` exige que la
pendiente label/eval esté en [0,8, 1,25] con correlación >0,9, sobre toda
campaña y **antes** de entrenar. Validado contra los datos viejos: da FAIL con
pendiente 0,263.

### Apéndice: falsa alarma de "búsqueda rota" (sonda mal construida)

Durante el diagnóstico, una prueba táctica rápida (`position … | go nodes 20000
| quit` por tubería) devolvió **0 nodos y PV vacía en todas las posiciones,
incluida la inicial**, y con todos los binarios, incluidos los que acababan de
jugar partidas de 464 plies. Parecía un fallo gravísimo del motor.

No lo era: al llegar todo el stdin de golpe, el `quit` aborta la búsqueda antes
de que produzca nada. Las mediciones anteriores eran válidas porque el runner de
partidas lee de forma **síncrona** hasta `bestmove`, y en los lotes de
calibración cada `position` siguiente espera a que termine la búsqueda anterior
(solo la última quedaba truncada).

Repetida la prueba de forma síncrona, el motor **captura correctamente** la
torre y la dama colgadas y encuentra el mate, tanto con eval material como con
red. Registrado por la lección que confirma (Spell #6): *validar siempre el
mecanismo de la sonda antes de creer su resultado*. Coste: 15 minutos; sin la
comprobación habría sido una "corrección" de un bug inexistente sobre código
correcto.

---

## 2026-07-19 — **GATE FASE 4: PASS** — SPRT #2 net-2 > net-1

| id | candidato | base | partidas | W/L/D | Elo | LLR | bounds | veredicto |
|---|---|---|---|---|---|---|---|---|
| #2 | tera-net2 (2.868.384 registros) | tera-net1 (1.522.654) | 360 @8k nodos | +268 −91 =1 | **+187,0** | **+3,30** | [1, 6] α=β=0,05 | **PASS** |

Bounds declarados **antes** de lanzar, conforme a la política. Cruce del umbral
(+2,94) a las 360 partidas en 571 s con 12 procesos — coherente con el coste
estimado en `docs/statistics.md`. Tasa de tablas 1/360 = 0,3 %, confirmando de
nuevo el carácter decisivo de la variante.

Calidad medida de net-2 frente a net-1 (posiciones frescas del oráculo):
correlación con material 0,942 vs 0,937; error mediano **100 cp vs 133**;
dispersión 528 vs 489 (material: 548). Paridad motor↔python **0 cp** en 300
posiciones. sha256 `05162b618577fd28…`.

**Incidente de instrumentación (registrado por instructivo)**: los primeros 160
partidas de este SPRT dieron **exactamente** +20−20, +40−40, +60−60, +80−80.
Esa simetría perfecta es imposible entre motores distintos: `pkill` no había
matado el proceso lanzado antes de que `sprt.py` soportara opciones UCI, de modo
que se estaba comparando el motor **consigo mismo** sin red. Detectado por
implausibilidad estadística del propio resultado, no por un error visible.
Tercera sonda defectuosa de la sesión; refuerza la regla: **un resultado
demasiado limpio es sospechoso**.

---

## 2026-07-19 — **GATE FASE 3: PASS** — net-1 gana +330 Elo al material

| red | datos | partidas | W/L/D | Elo | paridad | veredicto |
|---|---|---|---|---|---|---|
| net1pre | 408 k (unidades malas) | 160 | +8 −152 =0 | −511,5 ± 63,0 | 0 cp | FAIL |
| netA | 220 k | 120 | +5 −115 =0 | −544,7 ± 79,4 | 0 cp | FAIL |
| **tera-net1** | **1.522.654** | **100** | **+87 −13 =0** | **+330,2 ± 51,7** | **0 cp** | **PASS** |

Umbral del gate: ≥+100 Elo. Obtenido: **+330**, tres veces el listón, con
0 tablas y 0 anomalías en 100 partidas arbitradas por el oráculo.

**Artefacto**: `tera-net1.tnn`, 56.858.966 B,
sha256 `6bb5cd483f5a514e970eac129999f4a261e2a700f2d300cf808afe4fb71d6d3d`.
Procedencia: campaña 3 (`datagen … nodes 8000 threads 24 seed 777
write_min_ply 6`), 1.522.654 registros de 4.183 partidas, auditados con
`--strict` sin avisos, gate de unidades con pendiente 1,004; entrenamiento
6 épocas, batch 1024, λ=1,0, val loss 8,72e-4 → 6,81e-4 (monótona).

**Calidad medida de la red** (`tools/data_scaling.py`, posiciones frescas del
oráculo jamás vistas): correlación con el material **0,937** (era 0,796 con
370 k), error mediano **133 cp** (era 170), dispersión 489 frente a 548 del
material, brecha entrenamiento↔frescas reducida de 0,15 a 0,055.

**Corrección de mi propio razonamiento, registrada por instructiva**: en la
iteración 2 argumenté que la red necesitaba reproducir el material *casi
exactamente* para empatar, y extrapolé 4,5 M (luego 36 M con el punto nuevo)
posiciones para bajar de 1 peón de error. **La premisa era falsa**: con 2,7
peones de error mediano la red ya gana 330 Elo, porque el conocimiento
posicional destilado de la búsqueda domina el ruido de aproximación mucho antes
de lo supuesto. Entre 370 k y 1,52 M hay una **transición de fase**
(−545 → +330 Elo), no una rampa. La extrapolación log-lineal describía bien el
error de aproximación pero era **irrelevante como predictor de fuerza**.

**Learning transferible**: al elegir la métrica proxy de un gate caro, verificar
que la proxy y el gate se mueven juntos. "Error de aproximación al maestro" era
medible y barato, pero no monótono con el Elo en el rango que importaba.

---

## 2026-07-19 — F3 GATE DE FUERZA, iteración 2: **FAIL** (−545 Elo) — causa medida

Con unidades ya correctas (gate de unidades PASS, pendiente 1,012) y paridad
0 cp sobre 800 posiciones, la red netA (220 k registros) vuelve a perder:
**+5 −115 =0, −544,7 ± 79,4 Elo** en 120 partidas.

**Diagnóstico cuantitativo** (`docs/net1-postmortem.md`, herramienta
`tools/data_scaling.py`): se entrenaron cuatro redes y se midió su acuerdo con
la evaluación material sobre posiciones **frescas generadas por el oráculo**,
jamás vistas en entrenamiento:

| Datos | corr entrenamiento | corr frescas | error mediano frescas |
|---|---|---|---|
| 90 k | 0,754 | 0,368 | 244 cp |
| 180 k | 0,882 | 0,579 | 226 cp |
| 220 k | 0,934 | 0,692 | 210 cp |
| 370 k | 0,979 | 0,796 | 170 cp |

Mejora monótona con los datos, con la factorización activa en todas. Brecha
entrenamiento↔frescas de 0,18 a 370 k: **sobreajuste medido** (26,7 M pesos
frente a 370 k posiciones).

**Por qué son −545 y no −20**: el maestro es material **exacto**. La red no
tiene otra fuente de conocimiento que superar, así que para empatar debe
reproducirlo casi perfectamente. Con 170 cp (3,4 peones) de error mediano en
posiciones nuevas, el ruido de la aproximación aplasta cualquier señal
posicional destilada por la búsqueda.

**Extrapolación falsable**: `err ≈ 839 − 119·log10(N)` ⇒ **~4,5 M posiciones**
para bajar de 1 peón de error, ~10 M para que el ruido deje de dominar
(6–14 h de torre a 200 pos/s). Coincide en orden de magnitud con los 20–30 M
que el propio plan fijó para net-1.

**Decisión (regla predeclarada del plan)**: la auditoría de la cadena
índices→forward exigida por el criterio de abandono **está hecha y sale
limpia**, así que no hay código que arreglar. Se detiene el entrenamiento a
escala piloto y se lanza campaña de régimen (6 M posiciones, 24 hilos, 8 k
nodos). **El gate de +100 Elo no se relaja**: cambia el insumo, no el listón.

**Aviso metodológico**: dos veces en esta sesión una sonda mal construida casi
produce una conclusión falsa (el `quit` que abortaba la búsqueda; un filtro que
capturaba dos líneas por posición y desalineaba los pares, dando correlación
0,202 donde había 0,975). Ambas se detectaron por incoherencia con mediciones
previas. Regla reforzada: **toda sonda nueva se contrasta contra una medición
ya validada antes de creer su resultado**.

**Learnings**: (1) un nombre de campo mentiroso heredado del upstream
(`InternalUnits` conteniendo cp convertidos) costó una red y ~4 horas de CPU;
(2) el gate de paridad ==0 cp verifica que dos implementaciones **coinciden**,
no que la magnitud sea la **correcta** — hacía falta un gate de unidades
separado, y ahora existe; (3) importar un modelo estadístico ajustado a ajedrez
(WDL) a una variante con 128 piezas y 26 tipos es exactamente la clase de
supuesto que el playbook manda auditar: estaba en la lista de "constantes
chess-tuned" y aun así se coló por venir en una ruta de "solo presentación".

---

## 2026-08-12 — Traspaso verificado: **PASS** (commit `ebaba5e`)

**Hipótesis**: el árbol publicado conserva en esta máquina la identidad de
búsqueda, reglas, inferencia de net-2 y unidades de los datos declaradas en
`TRANSFER.md` §8. Ningún trabajo del backlog se habilita si uno de esos gates
falla.

**Cambios**: ninguno en código, contratos, datos o red. Se recompiló el motor
material-only desde limpio; la única edición durable de esta iteración es esta
entrada del ledger. El worker oficial de OpenBench se observó conectado a
`https://belzedar.duckdns.org` con PID 27540 y `-T 24`; no se detuvo ni se
modificó.

**Validación** (todos los comandos terminaron con exit 0):

- Build limpio, sin PGO, MSYS2 mingw64 GCC **16.1.0**, Python **3.12.0**:
  `make -j2 build ARCH=x86-64-bmi2 COMP=mingw TMP='C:\Users\djime\AppData\Local\Temp' TEMP='C:\Users\djime\AppData\Local\Temp'`.
  Se usaron 2 jobs, no `-j` ilimitado, para no competir con el worker T24.
- `bench 16 1 5`: **21.519 nodos en 4/4 corridas**. NPS observados:
  165.530, 193.864, 199.250 y 192.133; inferiores a los 331–414 kn/s de
  `BENCH_LOG.md`, pero no comparables como perf por la carga concurrente de la
  flota. La identidad falsable del árbol sí coincide exactamente.
- `python oracle/run_fixtures.py --impl both`: **87 fixtures**, implementaciones
  A y B, **0 fallos**.
- `python oracle/engine_check.py --engine ../src/stockfish.exe`: **87 listas
  exactas + 31 comprobaciones perft, 0 fallos**; startpos
  **54 / 2.916 / 175.508**.
- `python tools/parity_gate.py --engine ../src/stockfish.exe --net
  ../nets/tera-net2.tnn --ref ../data/pref_n2.jsonl`: **300 posiciones**, los
  buckets 0–7 presentes, **0 discrepancias, PASS (0 cp)**. SHA-256 de net-2:
  `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861`.
- `python tools/check_label_units.py --engine ../src/stockfish.exe --data
  ../data/c3_final.bin --positions 150`: **150 posiciones**, pendiente
  label/eval **1,016**, correlación **0,989**, **PASS**.

**Incidentes y diagnóstico**:

1. La primera invocación perdió las barras inversas de `TMP/TEMP` por quoting y
   GCC intentó crear `src\C:UsersdjimeAppDataLocalTemp\`: fallo de la sonda, no
   del proyecto. Repetir con comillas simples preservó la ruta.
2. La segunda invocación encontró objetos LTO antiguos de GCC 15.2
   (`LTO version 15.1`) al enlazar con GCC 16.1 (`expected 16.0`). `make clean`
   seguido de build completo eliminó la mezcla de toolchains y produjo el
   binario válido (SHA-256
   `142664df885bffe8e06c9112a16bfd9ec41cd24052977a15c7db9dbf1250d625`).

**Decisión**: traspaso **PASS**; el backlog queda desbloqueado. Por decisión del
propietario, toda ciencia posterior —SPRT STC/LTC, partidas fijas y DATAGEN— se
creará, aprobará y ejecutará en el OpenBench oficial antes de arrancar; estos
cinco gates locales son el preflight determinista exigido por sus contratos de
build/bench, no un sustituto de OpenBench.

**Learnings**: (1) una actualización de GCC exige `make clean` antes de creer
ningún enlace LTO; (2) bajo flota T24 la firma de nodos sigue siendo válida pero
el NPS local no permite atribuir una regresión; (3) registrar también errores de
invocación evita convertir una sonda rota en un diagnóstico del motor.

---

## 2026-08-12 — Bootstrap de build OpenBench con net-2: **PASS local**

**Hipótesis**: el contrato público de OpenBench —`make -j EXE=...`
`GIT_SHA_FULL=... EVALFILE=...`, seguido de `bench` sin argumentos— debe
producir un binario nativo que cargue net-2 desde `Engines/../Networks/`, use
esa misma red en el datagen embebido y falle cerrado si el archivo no existe.
No se habilita ningún piloto distribuido mientras una de esas propiedades sea
implícita o dependa de un `setoption` que el DATAGEN de OpenBench no envía.

**Cambios**:

- `src/Makefile` acepta la identidad completa del commit y hornea solamente
  `../Networks/<basename>` como `EvalFile` por defecto; el path absoluto del
  worker no entra en el ejecutable. El `make` desnudo despacha el build nativo
  sin PGO y se añadió el detector portable `scripts/get_native_properties.sh`
  que ya usa el checkout operativo de Spell/OpenBench.
- `Engine` carga el default compilado una sola vez al arrancar y termina con
  código distinto de cero si no puede activarlo. Los engines del datagen
  comparten la red global inmutable ya cargada en vez de releerla por hilo.
- `bench` sin argumentos equivale al contrato firmado `bench 16 1 5`; los
  argumentos explícitos no cambian.

**Validación local previa a OpenBench**:

- Build exacto del worker, sin target/`ARCH`/`COMP`, bajo MSYS2 GCC **16.1.0**:
  `make -j2 EXE=terachess-ob-bare.exe GIT_SHA_FULL=2b255e050b4356d3e183a9a8b4f8f4b4a41094ab EVALFILE=<Networks/05162b61>`.
  Exit **0**; detector: `ARCH=native` → **x86-64-bmi2**. SHA-256 del primer
  binario validado: `c8310d2e22f88d925e671b9a91428802f3bc627bc069370dd5169e190421c1bb`;
  tras integrar el sharing de red y el parser Windows, SHA-256 final
  `e52716bcbd1f2c8c2348a38f8b3018fee1aa02990d9da7d143d481ceb1bb1bc7`.
- En una réplica `Engines/../Networks/`, el arranque confirmó `EvalFile:
  loaded '../Networks/05162b61'`, `arch_hash`
  `92935a4de67fb1804e3fab2e529157e7bd6732b00bae867e74c9e6a824f0dd26`.
  `bench` con net-2: **32.541 nodos en 4/4**; NPS **94.595, 133.364,
  104.633 y 121.876** bajo el worker T24 concurrente. La sonda material previa
  conservó **21.519 en 4/4**. El NPS no se usa como comparación de rendimiento
  por la carga compartida; las firmas de nodos sí son deterministas.
- Recinto negativo creado desde cero, con ausencia de
  `Networks/05162b61` verificada antes de ejecutar: mensaje `CRITICAL ERROR`,
  red `REJECTED`, proceso **exit 1**. No hubo fallback silencioso a material.
- Sobre el binario público con net-2:
  `python oracle/run_fixtures.py --impl both` → **87 fixtures, 0 fallos**;
  `python oracle/engine_check.py --engine <binario>` → **87 listas exactas +
  31 perft, 0 fallos**, startpos **54 / 2.916 / 175.508**.
- `parity_gate.py --min-positions 300` sobre el binario final y net-2 →
  **300 posiciones, buckets 0–7, 0 cp, 0 discrepancias, PASS**.
  `check_label_units.py --net tera-net2.tnn` sobre 150 muestras de `c3_final`
  confirmó **NNUE 150/150**, pendiente **1,044**, correlación **0,991**, PASS.
  La sonda negativa `--net none` detectó NNUE **0/20** y falló cerrado con
  exit **2**. El gate ahora acepta `--net`, comprueba el return code del motor
  y rechaza explícitamente cualquier fallback material cuando se exige NNUE.
- Smoke del comando de una línea con ruta absoluta Windows de **145 caracteres**,
  net-2 y dos workers: **16/16 registros**, exit **0**, un solo mensaje de
  carga de red, **6,88 pos/s**. SHA-256 del `tera-bin`:
  `19d9a4b566b29d3f1d8a6e20fbbf4f10148b6cfadfe76686541868342e4d58c7`.
  `audit_terabin.py --strict` → **16 registros, 0 warnings**;
  doble round-trip → **8/8 exactos**.

**Incidentes y diagnóstico**:

1. El primer uso de `$(notdir $(EVALFILE))` partió una ruta Windows con
   espacios y horneó un basename falso; una compilación fallida lo detectó.
   Se sustituyó por `basename -- "$(EVALFILE)"` y se inspeccionó la macro real
   del compilador antes de ejecutar el binario.
2. Una primera macro llevaba barras de escape de más y no compiló; corregir a
   una única cita escapada produjo el literal esperado. Un intento posterior
   desde PowerShell encontró `make` fuera de `PATH` (exit **1**, sin compilar);
   repetir mediante el bash MSYS2 prescrito reprodujo el entorno del worker.
3. La primera sonda de red ausente era inválida: el recinto reutilizado aún
   contenía la red y devolvió exit **0**. El resultado se rechazó y la sonda
   limpia, con precondición `Test-Path=False`, devolvió exit **1**. Es la misma
   clase de error de arnés que obliga a desconfiar de resultados demasiado
   limpios.
4. El primer smoke envió BOM desde `StandardInput` de PowerShell: el motor vio
   `﻿datagen`, lo rechazó y luego salió 0 al procesar `quit`; exigir además los
   tres artefactos evitó el falso positivo. Python envió UTF-8 sin BOM. Esa
   repetición destapó un bug real: `std::quoted` consumía las barras inversas
   de paths Windows entre comillas, colapsando el path absoluto en un nombre y
   produciendo `Filename too long` con solo 145 caracteres. Un parser literal
   de delimitador preserva `\`; la misma ruta pasó después y protege también
   `{BOOK}`.
5. Se pidió revisión asesora a Oracle mediante la sesión ChatGPT Pro: el modelo
   `gpt-5-pro` quedó verificado, pero Chrome dejó de ser alcanzable tras 20 min
   sin respuesta; el segundo intento recibió `ECONNREFUSED`. No hubo respuesta
   asesora ni fallback a API, por lo que ninguna decisión se atribuye a Oracle.

**Decisión**: bootstrap de build **PASS local**. Esto es un preflight
determinista, no un test científico: el piloto de generación sigue bloqueado
hasta congelar dentro del comando la identidad SHA-256 de productor, red y
libro, validar unidades con la red y publicar/aprobar el workload en el
OpenBench oficial `https://belzedar.duckdns.org`.

**Learnings**: (1) el `EVALFILE` de OpenBench es un input de build y no una
orden UCI para DATAGEN; debe convertirse explícitamente en default verificable;
(2) un singleton de red permite compartir pesos, pero solo después de una
carga temprana que falle cerrado; (3) una sonda negativa necesita demostrar
su precondición —no basta con que el nombre del directorio diga “missing”.

---

## 2026-08-12 — Contrato autenticado de DATAGEN/OpenBench v41: **PASS local**

**Hipótesis**: un chunk publicable no puede confiar solo en que OpenBench haya
descargado los archivos correctos. El propio productor debe rechazar, antes de
crear directorios o shards, cualquier ausencia o deriva de productor, red o
libro; debe demostrar que los pesos en memoria corresponden a esa red; y un
resume debe congelar la misma identidad incluso cuando el output ya está
completo. El formato de registros `TC01` no puede cambiar.

**Cambios**:

- El comando `datagen` exige `book`/`book_sha256`, `network`/
  `network_sha256` y `producer_sha256`; acepta `NONE`/`NONE` solo para el libro
  builtin y prohíbe red material. SHA-256 se normaliza y valida como 64 hex.
- Se centralizó SHA-256 FIPS 180-4 en un hasher incremental. El loader TNN1
  calcula el digest sobre el **mismo stream** que convierte en pesos y conserva
  ese hash con la red en memoria. El arranque publica `arch_hash` y
  `file_sha256`; DATAGEN compara ambos archivos externos y la identidad de los
  pesos activos.
- `GIT_SHA_FULL` produce `TERA_SOURCE_COMMIT`; el metadata final añade schema
  `terachess-datagen-provenance-v1`, commit/dirty bit, hashes y tamaños de
  productor/red/libro, y `network_arch_hash`. Resume sube a metadata versión
  **2** y congela los mismos campos. Son sidecars: magic, versión, header y
  registros de `tera-bin v1` permanecen byte-contractualmente intactos.
- Se añadió `tools/tests/test_datagen_identity.py` como arnés positivo/negativo
  reproducible y se actualizaron el contrato/documentación operativa.

**Validación local previa a OpenBench**:

- Build público sin PGO, mismo comando de worker y GCC **16.1.0**, exit **0**.
  Binario precommit: **4.326.374 B**, SHA-256
  `dc82b6ccf8541d420740ac17e7fb96f869ec7ecf2edadabbc2d3146137894b99`.
  La carga reportó net-2 SHA-256 completo
  `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861`
  y `arch_hash` `92935a4de67fb1804e3fab2e529157e7bd6732b00bae867e74c9e6a824f0dd26`.
- Arnés de identidad: contrato ausente, SHA de productor incorrecto, SHA de
  red incorrecto, NNUE desactivada y SHA de libro incorrecto → **5/5 exit 1,
  cero artefactos**. Positivo → **4/4 registros**, una sola carga de red,
  metadata exacta, `audit_terabin --strict` **0 warnings**, round-trip **4/4**.
  Resume exacto PASS; productor distinto sobre output completo rechazado.
- Reglas: **87 fixtures, 0 fallos**; motor vs oráculo: **87 listas exactas +
  31 perft, 0 fallos**, startpos **54 / 2.916 / 175.508**.
- Paridad net-2: **300 posiciones**, buckets **0–7**, **0 cp**, 0 discrepancias.
  Unidades: **NNUE 150/150**, pendiente **1,044**, correlación **0,991**, PASS.
- Bench net-2: **32.541 nodos en 4/4**, NPS 103.304, 123.261, 81.556 y
  106.691 bajo T24. Tras descargar la red explícitamente, material:
  **21.519 nodos en 4/4**, NPS 170.785, 173.540, 130.418 y 166.813. La
  ausencia verificada de `Networks/05162b61` conservó el fail-closed: exit **1**.

**Incidentes y diagnóstico**:

1. La primera compilación no produjo binario (exit **2**): al centralizar el
   hasher faltaba incluir la API pública TeraNNUE en `datagen.cpp` y quedaba
   una referencia al helper hexadecimal retirado en un mensaje de error. La
   corrección explícita compiló; la carga de net-2 prueba además que el hash de
   descriptor no cambió.
2. Una sonda material volvió a usar un pipe PowerShell con BOM: el motor
   rechazó `﻿setoption`, dejó net-2 activa y midió 32.541; ese resultado se
   descartó. Un intento de framing manual quedó esperando stdin y se terminó
   sin dejar proceso hijo. Python envió bytes UTF-8 sin BOM y reprodujo
   material 21.519 ×4. Relearning evitado: exit 0 no autentica el primer
   comando de un stream UCI.

**Decisión**: contrato del productor **PASS local**. Se puede publicar el
commit y usarlo como pin del engine/preset. Aún **no** se autoriza la campaña:
faltan alta/deploy en el OpenBench oficial, red net-2 registrada y un canary
v41 aprobado cuyo chunk descargado pase auditoría y gate de unidades con NNUE.

**Learnings**: (1) hashear de nuevo un path no demuestra qué bytes se cargaron
en memoria; el digest pertenece al stream del loader; (2) el resume idempotente
también es una frontera de autenticación, no un atajo anterior a los hashes;
(3) añadir procedencia a sidecars no versiona `tera-bin`, pero cambiar cualquier
byte de header/registro sí exigiría `tera-bin v2`.

---

## 2026-08-12 — Alta en OpenBench oficial y canary net-2: **EN CURSO**

**Hipótesis**: el primer workload distribuido solo es admisible si producción
expone previamente el motor y la red, la creación v41 congela las identidades
completas y el chunk permanece en `PENDING` hasta ser visible y revisado. El
canary debe pasar auditoría `tera-bin v1` y unidades con NNUE antes de crear la
campaña de 10 M.

**Cambios**:

- `fde58c6823b1d97e77a70b9827433aab55a8b143` añadió autenticación fail-closed
  de productor/red/libro; build limpio desde `git archive`: 4.326.374 B,
  SHA-256 `6a6f31f992ef2b46bc48b664d4b46adf7981e693c5bb2166fe7fdcb504e88ec0`,
  `source_dirty=false`, contrato positivo y 5/5 negativos PASS, bench
  **32.541 ×4** (NPS 131.744, 127.113, 130.164, 132.280).
- `d38dc5de24196851c10a6857829330623e723ffb` congeló los presets v41: canary
  20.000 y campaña 10.000.000, ambos a 8.000 nodos, startpos, net-2 y el commit
  productor anterior. El alta central quedó canonizada en OpenBench
  `ee320a30` (`Bench: 32541`).
- Producción tenía antes del alta **67** entradas sucias: 5 ficheros
  versionados con 774 inserciones/6 borrados de trabajo AtomicDB y artefactos
  no versionados. El deploy canónico, que ejecuta `reset --hard`, se dejó
  fallar cerrado. Se aplicó solo el delta recuperable documentado: dos ficheros
  con hashes verificados, backup
  `Config/config.json.bak-terachess-20260812-ee320a30`, `manage.py check` 0,
  `makemigrations --check --dry-run` 0, reinicio únicamente de `openbench`,
  servicio activo y HTTP local/público 200. El worker conservó PID **27540** y
  **T24**.
- net-2 se registró como `tera-net2.tnn`, ID OpenBench `05162B61`, default,
  **56.858.966 B**, SHA-256 completo
  `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861`.
- Canary oficial **#352**: 20.000 posiciones en 1 chunk, seed
  202608120000000, prioridad 100, protocolo 41, campaña
  `terachess-net2-regime-20260812`, rol `canary`, cohort
  `net2-n8000-startpos-v1`. El contrato publicado congeló red/bytes exactos,
  commit `fde58c68…`, bench 32.541, productor requerido y hashes de contrato:
  publicación `0fcda0aedb5f873d0250ae6f371cf469aa6793d97a591243ff503f63551fcec2`,
  productor `2e92f4afcb63dc5da336a40f354697d3cbf35a569620ef3c61571033116aa853`
  y entorno `310d337236145781f7ebf75437014f9eda5e359dcc99a8323d8f607f316e3e36`.

**Incidentes y diagnóstico**:

1. Se anunció sin medir un tamaño de red de 318.572 B; era falso. La medición
   previa a toda transmisión dio **56.858.966 B** y el hash contractual exacto.
   La cifra se corrigió antes del alta.
2. Chrome bloqueó `setFiles` antes de transmitir por no tener acceso a URLs de
   fichero. Se usó staging SSH + hash/tamaño + alta transaccional en el mismo
   modelo `Network`; la UI autenticada marcó después el objeto exacto como
   default.
3. Una sonda anónima al endpoint de descarga guardó **64 B** de JSON de error,
   no la red. Se rechazó por tamaño/hash; la comprobación válida es el objeto
   de producción `Media/05162B61`, medido directamente.
4. La creación por un usuario approver dejó #352 con `approved=true`
   automáticamente aunque la vista aún mostraba “Approve”. No se pulsó una
   segunda aprobación. La fila fue visible y su contrato se auditó con estado
   `PENDING`, 0/1 chunks, antes de que el worker quedara libre.
5. El ZIP fuente v41 nombra `fdd71548…`, distinto del commit a primera vista.
   `git show` demostró que es exactamente el **tree SHA** de `fde58c68…`;
   OpenBench entrega ese árbol y pasa el commit por `GIT_SHA_FULL`, por lo que
   no hay deriva.
6. Al terminar de forma natural Horde #348, el selector eligió Spell #351:
   su prioridad era **303**, superior a 100 del canary. No se detuvo ni
   preemptó el trabajo ajeno. Un intento autenticado de elevar #352 a 1.000 se
   reflejó inicialmente en la UI, pero la comprobación final mostró **301** y
   la DB confirmó cuatro eventos `MODIFY`; el resultado 1.000 se retiró. No fue
   un clamp del scheduler: el código admite cualquier entero y selecciona el
   máximo. Antes de ningún resultado, una transacción fail-closed exigió
   prioridad 301, `PENDING`, intentos 0 y máquina nula; cambió solo a **304** y
   creó `LogEvent` **2326**, `Admin`, `MODIFY priority 301->304 (canary queue)`.
   Tras la indicación del propietario, el evento autenticado **2327** de
   `belzedar` dejó #352 en **302**, por debajo de #351 (**303**) y por encima
   de la cola ordinaria (**301**). No se harán más cambios de prioridad. #351
   quedó intacto ejecutando 1.536 partidas con concurrencia 12 y TC efectivo
   65,07+0,65; #352 conservó `PENDING`, intentos 0.

**Decisión provisional**: #352 queda en cola oficial tras #351, con prioridad
302, por debajo de #351 y sin preemptar el lease vigente. La campaña de 10 M
permanece
**BLOQUEADA** hasta descargar el chunk, verificar recibo/manifiesto,
`audit_terabin --strict`, round-trip byte a byte y
`check_label_units --net`. La equivalencia motor↔Python con sidecar ya pasó en
el preflight; OpenBench v41 publica el blob único y no conserva sidecars del
worker. Gate congelado antes de ver datos: header **20.000/20.000**, auditoría
estructural completa sin warnings, `pack(unpack(raw)) == raw` **20.000/20.000**
y unidades con `--positions 300 --min-abs 150`, exigiendo además muestra exacta
300/300, NNUE 300/300, pendiente en **[0,8; 1,25]** y correlación **≥0,9**.

**Learnings provisionales**: (1) medir todo activo antes de anunciar bytes;
(2) el hash corto de OpenBench solo localiza el objeto, el contrato v41 debe
congelar además SHA-256 completo y tamaño; (3) una cuenta approver puede saltar
el estado visual “sin aprobar”, así que la verdad operativa se consulta en DB;
(4) un árbol remoto sucio no autoriza a destruir trabajo ajeno para desplegar
un JSON independiente; (5) toda mutación operativa de prioridad necesita
readback independiente de DB y evento explícito, igual que una sonda científica.

### 2026-08-12 — Autopsia del intento 1: build Linux AVX-512 **FAIL CERRADO**

**Hipótesis**: el canary #352 debe construir el mismo commit público en toda
máquina admitida por OpenBench; un fallo anterior a DATAGEN debe reencolar el
chunk sin datos y bloquear el workload hasta causa raíz.

**Evidencia y cambios**:

- Spell #351 terminó primero en el worker T24 por bound alto: **1.078**
  partidas, **+687 −348 =43**, LLR **2,94924** sobre **2,94444**. Otro worker
  Linux, máquina OpenBench **16** (`UnholyCrusade`), no compatible con ese
  workload Spell, pudo seleccionar #352 mientras #351 cerraba: la prioridad se
  aplica después de filtrar compatibilidad por máquina, no como barrera global.
  El intento no produjo datos.
- `event2328.log`: **33.028 B**, SHA-256
  `bf0014bde52efeff121383ce2c1638534d60696abe944557939d8a7b4a6d8f1c`.
  GCC 16 seleccionó `x86-64-avx512icl`; `movepick.cpp` falló desde la primera
  declaración `__m512i` y todos los intrínsecos `_mm512_*` por faltar su header.
  DB tras el fallo: chunk `PENDING`, intentos **1**, máquina nula, 0/20.000
  posiciones. #352 se detuvo mediante CAS sobre ese estado exacto y el mismo
  error; evento **2330**, 0 registros, prioridad sin modificar (**302**).
- Cambio mínimo: `movepick.cpp` incluye `<immintrin.h>` solamente cuando
  `USE_AVX512` está definido. No cambia reglas, búsqueda, formato ni red.

**Validación previa al nuevo workload**:

- La primera réplica MinGW fue inválida: PATH incompleto, compilador ausente,
  exit **127**; se rechazó. Con PATH correcto, el árbol prepatch compiló
  `movepick.cpp` en MinGW, demostrando que Windows ocultaba la deuda mediante
  includes transitivos; la orquestación expiró durante el build y no se contó
  como PASS Linux.
- Árbol limpio `fde58c6` + parche, GCC **16.1.0**, sin PGO, `-j2`, target
  `x86-64-avx512icl`: build/enlace exit **0**, 0 líneas de error, binario
  **4.341.467 B**. Ejecutarlo en el Ryzen 9 5950X terminó 4/4 con
  `0xC000001D` antes de buscar: sonda rechazada, porque el detector clasifica
  esta CPU como `x86-64-bmi2` y no soporta ICL.
- Mismo árbol parcheado, target soportado `x86-64-bmi2`, net-2 exacta: build
  exit **0**, 0 líneas de error, binario **4.326.374 B**, SHA-256
  `118ee1f3de9a2a71454e5d8f259eec104ff27228d6438c6ddde723aaa4702a47`.
  Carga 4/4 del SHA-256 completo de net-2 y bench **32.541 ×4**; NPS **110.683,
  43.272, 60.710, 69.236** bajo el worker T24 y otro build concurrentes.

**Decisión**: #352 queda cerrado y no se reutiliza: su contrato v41 fija el
commit fallido. Se publica el fix con bench firmado y se crea un canary nuevo,
con identidad/commit nuevos y exactamente los mismos parámetros/gates. La
validación decisiva del include será el build Linux de OpenBench; la campaña de
10 M sigue bloqueada.

**Learning**: un build `native` validado solo en Windows no cubre headers SIMD
en GNU/Linux. Toda arquitectura que OpenBench pueda seleccionar necesita al
menos un build real en ese sistema antes del primer workload científico.

### 2026-08-12 — Deploy r2 y canary oficial #353: **PASS datos / PENDIENTE Linux**

**Hipótesis**: el reemplazo de #352 debe ser un workload nuevo cuyo contrato
v41 fije el commit con el arreglo AVX-512. Debe conservar prioridad **302**,
sin modificar ni interrumpir otra carga, y solo se podrá abrir la campaña de
10 M después de que su único chunk pase todos los gates ya congelados.

**Cambios y validación operativa**:

- `711177d601f5e16341277e81a141c63d0e61ef52` publicó el include condicional
  de `<immintrin.h>` con `Bench: 32541`. El preset r2 fija ese commit,
  campaña `terachess-net2-regime-20260812-r2` e IDs externos
  `canary-20k-r2`/`train-10m-r2`; el JSON mide **2.652 B** y tiene SHA-256
  `31256b0fcc55976a810748fdb196a56b30a245affc42f674ddb35058f6883c12`.
  OpenBench central lo publicó en `91d5f3b1`.
- Producción seguía con su árbol sucio, por lo que se desplegó solo ese JSON,
  después de comparar hash y bytes, con backup
  `/opt/openbench/Engines/Terachess-Stockfish.json.bak-20260812-91d5f3b1`.
  `manage.py check` dio **0** errores y
  `makemigrations --check --dry-run` **0** cambios. Se reinició únicamente el
  servicio `openbench`; quedó activo, Gunicorn escuchando en
  `127.0.0.1:8000` y el endpoint canónico público
  `https://belzedar.duckdns.org/api/config/` respondió **HTTP 200**.
- Antes de escribir DB, el verificador canónico de creación resolvió GitHub a
  commit solicitado/resuelto `711177d…`, bench **32.541**, disponibilidad de
  artefactos `true` y **0 errores**. Las precondiciones midieron #352 cerrado
  con 0 partidas/0 chunks completos, red net-2 default y 0 colisiones de
  campaña/slot r2.
- La ruta canónica `create_workload(..., "DATAGEN")` creó #**353** y devolvió
  redirect **302** a `/index/`. Readback transaccional: aprobado, no terminado
  ni borrado, prioridad **302**, throughput **1.000**, 20.000 posiciones en un
  chunk `PENDING`, seed **202608120000000**, 0 intentos, máquina nula y 0
  registros; el perfil pasó de 257 a **258** workloads y se creó exactamente
  el evento `CREATE P=302 TP=1000`.
- Contratos de #353, todos recomputados vigentes: publicación
  `96f6e0a7a8fb65c17293a54ddccb42ad71b0ab5f981478df4e968438b8fb344d`,
  productor
  `2e92f4afcb63dc5da336a40f354697d3cbf35a569620ef3c61571033116aa853`
  y entorno
  `310d337236145781f7ebf75437014f9eda5e359dcc99a8323d8f607f316e3e36`.
  La publicación congela net-2 en **56.858.966 B**, SHA-256
  `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861`,
  startpos builtin y productor obligatorio.
- Build local limpio del `git archive` de `711177d…`, bajo el contrato
  OpenBench (`OPENBENCH_DATAGEN=1`, `GIT_SHA_FULL` exacto, `EVALFILE` net-2),
  GCC **16.1.0**, `-j2`, BMI2 y sin PGO: exit **0**. El compilador recibió
  `TERA_SOURCE_COMMIT` completo y `TERA_SOURCE_DIRTY=0`; binario
  **4.326.374 B**, SHA-256
  `7f31a51d4515387b87e791194ac4d7647109a1ba5f5eca12b4aee6d4143479b4`.
  El arnés de identidad reprodujo 5/5 negativos con exit 1 y 0 artefactos;
  positivo **4/4**, audit 0 warnings, round-trip 4/4, procedencia y resume
  exactos. Bench net-2 **32.541 ×4**, NPS **118.762, 103.304, 123.730 y
  123.730** bajo el worker T24. Antes de usar este binario en el canary, la
  sonda de unidades reprodujo la medición conocida de `c3_final`: NNUE
  **150/150**, pendiente **1,044**, correlación **0,991**, PASS.
- Orden observado sin mutaciones adicionales: Spell #351 ya había terminado
  por bound alto; #353 era la única carga activa con prioridad **302**. El
  worker T24 conservó PID **27540** y su comando `-T 24 -N 1`; estaba
  terminando el lease previo de Horde #332. La máquina Linux 16 llevaba sin
  actualizar desde **12:51:07 UTC**. No se detuvo, preemptó ni repriorizó
  ninguna carga.
- `tools/check_terabin_roundtrip.py` hace reproducible el gate byte a byte del
  blob de OpenBench sin exigir el sidecar debug que la plataforma no conserva.
  Antes de usar la sonda nueva sobre #353 se validó contra el smoke conocido:
  el arnés motor↔Python reprodujo **8/8**, la auditoría estricta aceptó los
  **16** registros y la sonda nueva dio **16/16** sobre los mismos 2.336 B.
  El control negativo `--expected-records 17` falló cerrado con exit **2** al
  leer el header real de 16.
- El intento **2** de #353 fue reclamado naturalmente por el worker T24 tras
  los workloads Spell; OpenBench volvió a construir el productor, reprodujo
  bench **32.541** y completó 20.000/20.000 entre **13:57:08** y
  **13:57:24 UTC**. Recibo v41 self-hash
  `4cffa7fe2caffa77cbd0efe85965047c1dd49def6a69fe205a26b6b9a63083dd`:
  máquina 12, intento 2, contrato de publicación `96f6e0a7…`, productor exacto
  `40315359…` de 4.326.374 B y artefacto `.bz2` de **660.219 B**, SHA-256
  `2cd8edc5d188b771036d7b9e532341acef7d8cbf85f449f13ccc97207ea8bdf3`.
  Servidor, DB y descarga independiente reprodujeron el mismo hash/tamaño.
- Descompresión: **2.880.032 B** exactos (`32 + 20.000 × 144`), SHA-256
  `db0a11687bd544e379cd093f3dcb73997db3b2c539555a5c8f1496667cb462b3`.
  `audit_terabin.py --strict`: header 20.000/20.000, source_count **33.387**,
  flags 0, 0 registros inválidos y **0 warnings**. W/D/L **8.458 / 3.023 /
  8.519**; fases opening/middle/late/endgame **41,225 % / 29,550 % /
  23,130 % / 6,095 %**; piezas min/media/max **6 / 84,11 / 128**.
  `check_terabin_roundtrip.py --expected-records 20000`:
  **20.000/20.000** byte-exactos.
- Gate de unidades sobre el blob descargado y el productor oficial: muestra
  exacta **300/300**, NNUE **300/300**, pendiente label/eval **1,017** dentro
  de [0,8; 1,25], correlación **0,994** ≥ 0,9, **PASS**.

**Incidentes y diagnóstico**:

1. El primer intento de deploy construyó una orden SSH con sustituciones
   `$(...)` que PowerShell evaluó localmente. Falló **antes de mutar**: el JSON
   activo conservó su hash anterior y el servicio siguió activo. Se rechazó
   el intento y se repitió con un script literal, verificando staging, destino
   y backup por hash/tamaño.
2. El primer health-check posterior al reinicio exigía erróneamente HTTP 200
   en `/api/config` sin barra; el comportamiento canónico es redirect **301**.
   La sonda se marcó inválida. Servicio, socket y `/api/config/` público
   confirmaron después el estado verde con HTTP **200**.
3. El primer dry-run de creación importó `verify_workload` fuera del orden de
   carga de la vista y encontró un ciclo de imports de Django. Terminó con exit
   **1** y no escribió DB. Importar primero `OpenBench.views`, como hace la
   aplicación, dio 0 errores; el script creador repitió todas las
   precondiciones dentro de la transacción.
4. La primera sonda del build limpio abrió Bash como login shell en
   `/home/djime` y no encontró el toolchain (exit **1**); dos intentos de pasar
   la ruta con espacios mediante quoting anidado produjeron `cd: too many
   arguments`: uno expiró con exit **124** y otro devolvió un exit 0 engañoso
   porque una orden posterior sí pasó. Ninguno invocó el compilador. Se
   rechazaron los tres y un script Bash literal, con cwd y TMP/TEMP explícitos,
   produjo el build medido arriba.
5. A las **15:44:29 CEST** apareció en el cliente local un `openbench.exit` de
   0 B no creado por este flujo. El worker terminó primero su lote ya concedido
   de Horde #332 (hasta la partida **1.430**) y salió; el supervisor lo
   relanzó con el mismo `-T 24 -N 1`. El flag aún presente hizo que ese proceso
   reclamara #353, construyera y bencheara correctamente, pero matara el
   generador antes de crear output. Evidencia servidor del intento 1: máquina
   **12**, 0 bytes, productor PE disponible **4.326.374 B**, SHA-256
   `4031535940f205704cfde57233e66e2960872fbea80fa7880eef9e1bcf4237b5`,
   commit/contrato exactos. Tras comprobar muerto el PID anterior **27540** se
   eliminó solo el flag; readback: un worker T24, flag ausente. La descarga del
   CAS oficial reprodujo hash, bytes y magic `4d5a`. El primer recinto local
   para sondarlo fue inválido: instaló la red como `tera-net2.tnn`, pero el
   build OpenBench horneó el basename `05162B61`; el arranque fail-closed salió
   **1** antes de aceptar el `setoption`. Tras añadir ese archivo solo después
   de verificar sus **56.858.966 B** y SHA completo, el productor oficial
   reprodujo la medición conocida: NNUE **150/150**, pendiente **1,044**,
   correlación **0,991**, PASS.
6. El stop local no llama a `requeue_chunk`: dejó el intento 1 en `RUNNING`
   hasta expirar aunque el worker ya atendía Spell SPSA #170. Con CAS sobre
   test 353/chunk 0/máquina 12/intento 1, la función canónica de servidor lo
   devolvió a `PENDING`, conservó `attempts=1`, limpió lease/productor y anotó
   el motivo; evento `REQUEUE chunk 0 after local exit (attempt 1)`. Readback:
   prioridad **302**, 0 registros, 0 bytes y máquina nula. No se interrumpió el
   Spell que el scheduler había concedido durante esa ventana.
7. Una sonda HTTP anónima a `/api/datagen/353/` devolvió el JSON esperado de
   política, `API requires authentication for this server`; no es prueba del
   manifiesto y se rechazó. Además, el wrapper intentó usar un método
   `SHA256.HashData` inexistente en este PowerShell y lanzó excepción después
   de recibir la respuesta. No se extrajeron ni reutilizaron credenciales: la
   evidencia aceptada es el recibo v41 recomputado en servidor y los bytes
   descargados/rehasheados por SSH.
8. Después de completar #353 apareció una segunda pausa local, distinta de la
   primera: `openbench.exit` creado a las **16:04:42 CEST**, **2 B** `0d0a`, y
   el supervisor histórico PID **31872** ya no existía; tampoco quedaba ningún
   `client.py`. Como no hay workload Terachess activo y la combinación flag +
   supervisor terminado indica una intervención concurrente que puede ser
   intencional, no se retiró el flag ni se relanzó el worker por segunda vez.
   Restaurar T24 requiere confirmar el nuevo estado operativo con el
   propietario; no se entra en una carrera contra otro operador.

**Decisión**: los gates de datos del canary #353 son **PASS** sin relajaciones.
La campaña de 10 M sigue **BLOQUEADA** por un único gate declarado antes del
resultado: el build Linux real de OpenBench para `711177d…`. #353 fue producido
en Windows/BMI2 y, por tanto, no demuestra que el include AVX-512 corrige el
fallo observado por la máquina Linux 16. Esa máquina no se actualiza desde
**12:51:07 UTC**; readback a las 14:02:05 UTC: Linux, T32, flags AVX512F/BW/DQ/
VNNI, Terachess soportado y **4.257,5 s** sin heartbeat. No se crea el workload
de 10 M ni se sustituye este gate por una compilación local/manual; todos los
tests deben permanecer en OpenBench.

**Learnings provisionales**: (1) los health-checks deben usar la ruta canónica
antes de interpretar un redirect como caída; (2) un script operativo remoto
debe ser literal cuando contiene sintaxis que también entiende PowerShell;
(3) la validación seca debe reproducir el orden de imports de la aplicación y
probar explícitamente que no creó filas; (4) un canary de datos puede quedar
verde sin cubrir la plataforma que motivó su relanzamiento: ambas evidencias
son gates distintos y no se sustituyen entre sí.

---

## 2026-08-12 — Campaña de régimen net-2 10 M: **EN COLA** (#361)

**Hipótesis**: tras el PASS completo de datos del canary #353, la campaña de
10 M puede quedar creada y visible en el OpenBench oficial sin consumir trabajo
ni alterar el orden de la flota mientras no haya worker. El propietario autorizó
explícitamente encolarla antes de resolver la deuda del build Linux; esa
autorización cambia la decisión operativa, pero no reclasifica el gate Linux
pendiente como PASS.

**Cambios**:

- Sin cambios de motor, red, formato ni prioridad. El dry-run canónico
  `verify_workload(request, "DATAGEN")` dio **0 errores** y resolvió el commit
  solicitado al pin `711177d601f5e16341277e81a141c63d0e61ef52`, bench
  **32.541**, red default `tera-net2.tnn` y artifacts disponibles.
- La ruta canónica `create_workload(request, "DATAGEN")`, dentro de una
  transacción con precondiciones fail-closed, creó el workload oficial
  **#361** (`train-10m-r2`). Respuesta **302** a `/index/`; exactamente un
  evento `belzedar: CREATE P=50 TP=1000`; contador del perfil **258 → 259**.
- Contrato congelado: campaña `terachess-net2-regime-20260812-r2`, rol
  `train`, cohort `net2-n8000-startpos-v1`, **10.000.000** registros,
  **100.000** por chunk, **100** chunks, seed **202608120100000**, prioridad
  **50**, throughput **1.000**, startpos, 8.000 nodos y net-2.

**Validación** (readback independiente a las **14:48:26,741 UTC**):

- #361 aprobado, no terminado ni borrado, **0** registros, **0/100** chunks;
  los 100 estaban `PENDING`, intentos **0**, `machine_id=null`, error vacío y
  sumaban exactamente **10.000.000** posiciones.
- Red: **56.858.966 B**, SHA-256
  `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861`.
  Los tres contratos recomputaron vigentes: publicación
  `f1c86e3f55790436947d4da3b3ad2a9c299bd5bc52d8a1815b047d2106c046e9`,
  productor
  `2e92f4afcb63dc5da336a40f354697d3cbf35a569620ef3c61571033116aa853`
  y entorno
  `310d337236145781f7ebf75437014f9eda5e359dcc99a8323d8f607f316e3e36`.
- Estado local observado después de crearla: ningún proceso Python con
  `client.py`; `openbench.exit` conservado byte a byte en
  `openbench-spell/Client` (**2 B**, creado a las **16:04:42 CEST**). No se
  borró el flag, no se relanzó el supervisor y no se tocó ningún workload
  Spell ni ninguna prioridad.

**Decisión**: campaña **CREADA Y EN COLA**, deliberadamente inerte hasta que el
propietario reactive el worker. La prioridad queda congelada en **50**, por
debajo de las cargas de juego. La deuda Linux de `711177d…` sigue abierta y se
registrará como tal hasta que un build real admitido por OpenBench la resuelva;
ningún resultado de #361 podrá usarse para borrar retrospectivamente ese gate.

**Learnings**: (1) una autorización del propietario puede aceptar el riesgo de
encolar trabajo sin alterar el estado epistemológico de un gate; (2) crear una
carga no equivale a arrancarla: `PENDING`, intentos 0 y máquina nula son el
recibo falsable; (3) una prioridad declarada forma parte del orden contractual
y no se eleva para compensar la ausencia temporal de workers.

---

## 2026-08-12 — Libro Terachess v1 y onboarding del runner: **ARTEFACTO PASS / JUEGO OFICIAL PENDIENTE**

**Hipótesis**: P1 no debe entrar en granja con startpos repetido ni con un libro
sin autenticación. El primer artefacto debe ser determinista, legal según ambos
oráculos, inmutable por SHA-256 y reconocido por el runner de Terachess con cap
de al menos 1.000 plies. Registrar el libro no autoriza todavía un SPRT.

**Cambios**:

- `tools/make_book.py` exige desde `717ebd0` el SHA-256 completo de la red antes
  de arrancar. Con el productor oficial del canary (SHA-256 `40315359…`) y
  net-2 exacta generó `books/tera_openings_v1.epd`: 5.000 líneas, plies 8–16,
  5.000 nodos, MultiPV 6, gap 150 cp, límite final 800 cp, seed 2026 y 12
  descartes. Tiempo **1.087,503 s**.
- `python tools/validate_book.py --book books/tera_openings_v1.epd --receipt
  books/tera_openings_v1.epd.receipt.json --expected-lines 5000
  --expected-raw-sha256 1f117b0e... --json
  books/tera_openings_v1.validation.json` recorrió las 5.000 posiciones con
  los oráculos A/B. Resultado: **5.000/5.000** únicas y canónicas, 0
  discrepancias de legales, 0 terminales y 0 errores; **44,881 s**.
- Payload: **911.542 B**, SHA-256 raw/text
  `1f117b0ed03049afad62481494fff9e3232774d188433a99ffff1454d84babe7`.
  `tools/package_book.py` creó el zip determinista de **132.952 B**, SHA-256
  `87ed4fba357de4020e42e711c16e9f9a08ec0d6eac12851f224699aafa2cb256`.
- El checkout central de OpenBench publicó el runner Terachess y cap
  **1.200 plies** en `b64dd2ac`, el artefacto inmutable en `c716735d` y su
  registro en `62fa798a`. Los **231** tests del cliente pasaron, con 1 skip.
  La fuente del libro queda pineada a `c716735d…`, no a una rama móvil.
- Producción recibió quirúrgicamente el JSON/recibos del libro, conservando el
  resto de su árbol sucio; `manage.py check` dio 0 errores, se reinició solo
  `openbench` y `/api/config/` respondió HTTP 200. No se tocó el worker ni una
  prioridad.

**Decisión**: artefacto y registro **PASS**. Sigue `official_runner_validation:
false` y `authorized_for_strength_sprt:false`: antes del primer P1 se exige el
workload `VALIDATION_ONLY` con libro, net-2, cap 1.200 y reloj real. Registrar
el libro no equivale a probar el runner.

**Learnings**: libro, zip y registro son tres identidades diferentes y las tres
necesitan recibo. Un libro completamente legal puede seguir sin estar
autorizado para fuerza hasta que el camino servidor→worker→runner se ejerza de
extremo a extremo.

---

## 2026-08-12/29 — P1 LMP, arnés causal: **DESARROLLO STOP; HOLDOUT CERRADO**

**Hipótesis**: el LMP de ajedrez es la mayor distorsión de búsqueda restante,
pero el branching ~180 impide elegir un parche por intuición. Antes de usar los
seis slots SPRT congelados hay que medir colas quiets desde el estado exacto del
trigger baseline, demostrar no interferencia y pasar desarrollo+holdout sin
relajar cobertura después de ver resultados.

**Cambios y controles**:

- `tools/lmp_shadow_roots_v1.json` congela 256 raíces (128 desarrollo/128
  holdout), SHA-256
  `099d9eec8ef58f8608cefca4f7011546e8211de6e6b5b02f461741807fd0c661`,
  derivadas de `data/c3_final.bin` SHA-256 `24671a8c…`. Separación mínima
  **1.285** registros, plies 13–659 (media 250,41), 26–128 piezas y 51–284
  legales (media 181,46); ambos oráculos 256/256, 0 fallos y 0 solape con el
  libro.
- La instrumentación `TERA_LMP_TRACE` queda compilada fuera de producción.
  Su replay clona stack/MovePicker, restaura nodos/seldepth/NMP/arena y suprime
  escrituras TT/historial. Builds aislados BMI2, sin PGO: normal **4.326.107 B**
  SHA `0a309f16…`; trace **4.393.482 B** SHA `50342abe…`.
- `python tools/lmp_shadow_harness.py verify-controls ... --root-count 2
  --nodes 20000 --max-records 12` dio bench **32.541/32.541**, 11 registros,
  0 errores, búsquedas primarias idénticas en normal/dormant/active y repetición
  byte-exacta (trace SHA `8921f242…`).

**Autopsia 1 — probe U¾ no era estado baseline**:

- La primera implementación tomaba el snapshot en el trigger U¾ y reconstruía
  T0 con la profundidad de entrada. Dos pasadas dieron 23.989 registros,
  SHA-256 común `2621294c…`, pero `analyze` falló cerrado con **39** errores;
  p. ej. registro 532: trigger real 52 frente a T0 reconstruido 67.
- Causa: `depth` puede mutar dentro del move loop; además un estado anterior
  U¾ no puede etiquetar causalmente políticas lenientes desde el posterior
  trigger baseline. Se separaron modos: `baseline` solo etiqueta U2/D4/barrido
  leniente; `u34` queda como control direccional independiente. Esas métricas
  se rechazaron íntegramente.

**Autopsia 2 — última raíz truncada por `quit`**:

- Tras la corrección, dos colecciones de desarrollo parecían limpias: **23.990**
  registros, tiempos 134,387/156,468 s y SHA-256 byte-idéntico
  `2144896378f576207e9446b2a677e5ab6f6725024ea9ea87cbf3b8d2049bfd82`.
  El análisis original reportó 512 checks A/B, 0 errores y seis estratos de
  3.990–4.000. Señal exploratoria, no Elo: D4 recuperaba 1.995/5.120 casos
  críticos a +1,2239 % trabajo sombra; U2, 2.076/5.120 a +5,7164 %.
- Una auditoría posterior del recibo detectó exactamente **1/128** raíz con
  menos de 100.000 nodos en ambas ejecuciones: ID `7f88541d631042f6`, depth 1,
  `nodes=0`, bestmove `a4a5`. El transcript muestra el `info ... nodes 0`
  inmediatamente antes del último bestmove.
- Causa raíz en `uci_script`: `go` es asíncrono. Cada siguiente
  `ucinewgame` esperaba la búsqueda anterior, pero el último `go nodes 100000`
  iba directamente seguido de `quit`; `uci.cpp` procesa `quit` con
  `engine.stop()`. Por eso siempre se truncaba solo la raíz 128.
- El arnés ahora termina `go → ucinewgame → quit`, exige ≥100.000 nodos en
  **128/128**, autentica SHA de raíces y fuente, ambos receipts/traces/
  transcripts/binarios/red, paths distintos, `100k/1:1/cap24k` exactos y no
  permite `--limit` ni bajar los mínimos en un PASS. Los defaults del control
  se corrigieron a la capacidad conocida 2×20.000/12. `python -m unittest
  tools/test_lmp_shadow_harness.py` da **11/11** y `py_compile` exit 0.

**Validación/decisión**: el análisis endurecido emitió **STOP** aunque el trace
fuera repetible: dos errores, uno por recibo truncado. Se retira el PASS de
desarrollo y todas sus métricas quedan solo como diagnóstico. **Holdout nunca
se abrió** y no existe SPRT P1. Hay que repetir dos pasadas completas de
desarrollo con la barrera; solo un PASS nuevo permite abrir las 128 raíces
holdout con parámetros idénticos.

**Estado operativo revalidado el 2026-08-29 08:31:05 UTC**: OpenBench oficial
mantiene #361 aprobado, prioridad **50**, 0/100 chunks y 0/10 M registros. El
único worker reciente era máquina 9, `codex_local_worker`, T24, asignado a #407
prioridad **400** (1/8 chunks). No se cambió ninguna prioridad ni se lanzó el
desarrollo de un hilo encima del presupuesto de 24 hilos.

**Learnings**: (1) reproducibilidad byte a byte puede reproducir un bug de
orquestación; (2) todo `go` asíncrono necesita una barrera explícita, incluido
el último; (3) el recibo debe demostrar que cada raíz consumió su presupuesto,
no solo contar roots/bestmoves; (4) una señal offline grande no es Elo y una
sola raíz truncada invalida el gate completo; (5) endurecer un gate después de
descubrir que era insuficiente obliga a retirar el PASS anterior, no a
grandfatherizarlo.

---

## 2026-08-29 — Refresh de búsqueda y audit de fruta baja: **INVENTARIO PASS / 0 ELO ATRIBUIDO**

**Hipótesis**: además de NNUE y P1 podían existir supuestos 8×8, deuda de
correctness o parches upstream ya validados que dieran mejoras baratas. La
presencia de un TODO o un PASS de ajedrez no basta: cada candidato debía
clasificarse contra el código compilado, ADR-001 y el branching medido antes de
crear un workload.

**Cambios**: ninguno en producción ni en una prioridad OpenBench. Se clonó en
`.scratch/` Stockfish oficial solo para auditoría, pineado por
`git ls-remote` en
`8bc5caa2e4b1d4c189b1428e93158b10d3edb0b6`, y se corrigieron
`docs/search-audit.md`/`docs/staging-program.md`. La antigua P5 SEE se cerró y
su lugar lo ocupa el clasificador de amenazas de las 26 piezas con presupuesto
predeclarado de **4 SPRT**.

**Validación**:

- `git rev-list --count ebcea3ef..8bc5caa2` dio **71** commits; restringido a
  search/search.h/movepick/history/position/movegen/thread dio **24**. El blob
  `src/history.h` de F1a y el de upstream `ebcea3ef` coincide exactamente:
  `fc54cbee33905282270e9bbba3101ceb23913c69`.
- Los candidatos upstream transferibles de velocidad son `50221673`
  (inicialización shared por nodo NUMA), `5062aee5` (páginas grandes; upstream
  midió ~+1 % NPS) y `4150d22b` (prefetch). Aquí
  `ContinuationHistory[2][2]` son **4 × 32 MiB = 128 MiB** por nodo; el clear
  actual lo repite cada worker, por lo que T24 escribe aproximadamente **3
  GiB** redundantes al inicializar. Aún no hay NPS local ni Elo.
- Los cambios de fuerza `356d7c5c`, `218c74ec`, `c5aef2bf`, `c85637b3`,
  `fa8b6add`, `5f7348f0`, `6d215a03` y `598ae2c4` no forman un lote portable:
  alteran NMP/futility/LMR/correction con constantes sensibles a la escala de
  evaluación (peón Terachess = 50). Los fixes `ee515ad9`/`19a02f44` dependen de
  extensión Syzygy, eliminada del build; la nueva NNUE cambia arquitectura y
  está fuera del contrato S.
- La supuesta lectura fuera de rango de `threatByLesser[KING+1]` se falsó:
  `KING=26`, así que el array tiene **27** entradas, igual que
  `PIECE_TYPE_NB`. Tampoco es activo el NNUE upstream con bucles de 64
  casillas: `Makefile` compila únicamente `tera_features.cpp`,
  `tera_accumulator.cpp` y `tera_network.cpp`.
- La deuda SEE documentada también era falsa. Desde `94fab4f`, `see_ge`
  ejecuta `attackers_to(to, occupied)` en cada iteración; esa función recalcula
  `hopper_captures` y Eagle/Rhino con la ocupación hipotética. Se retiró la
  familia sin gastar un test.
- Sonda read-only con `oracle/terachess_b.py` sobre las **256** raíces P1:
  **46.453** legales, **45.198** quiets y 0 desacuerdos con los conteos del
  manifiesto. Solo **5.933 (13,127 %)** mueven N/B/R/Q y reciben un
  `threatByLesser` no vacío; **39.265 (86,873 %)** quedan sin esa señal. Es
  cobertura de clasificador, no una estimación Elo.
- `SEARCHEDLIST_CAPACITY=32` no desborda: el push está guardado por
  `moveCount <= 32`. Sí deja sin malus a la cola tardía en nodos anchos, pero el
  malus ya decae geométricamente y no se modifica sin medir exposición.

**Decisión**: no existe evidencia honesta de “cientos de Elo” lista para subir.
P1 sigue primero. Después quedan cuatro candidatos node-identical a perfilar de
uno en uno (clear NUMA, páginas grandes, prefetch y fusionar el do-and-revert de
`legal`/`gives_check`) y la nueva P5 de threat tiers, con arnés offline antes de
granja. No se lanzó ningún SPRT ni se alteró #361/prioridades.

**Learnings**: (1) `KING+1` cambia de significado cuando KING es el tipo 26;
(2) código residual no listado en `SRCS` no es una ruta de ejecución; (3) una
deuda escrita puede estar más desactualizada que el código y debe retirarse,
no perpetuarse; (4) los parches upstream de fuerza son hipótesis nuevas cuando
cambian tablero, unidades y red; (5) una clase ciega del 86,873 % justifica un
arnés, no una cifra Elo.

---

## 2026-08-29 — Candidatas exactas y estado de #361: **PREPARADAS / NO ADMITIDAS A OB**

**Hipótesis**: mientras T24 atiende cargas anteriores, se pueden aislar mejoras
node-identical sin consumir hilos de motor ni alterar la cola. Ninguna rama es
una candidata OpenBench hasta pasar build, fixtures/oráculo, paridad cuando
aplique, bench firmado y NPS-check en un hueco real de recursos.

**Cambios**: `main` y producción no cambiaron. Quedaron cuatro diffs sin commit
en worktrees de `.scratch/`, todos basados en `5226966`:

- `codex/legal-gives-check`: fusiona el do-and-revert de `legal()` y
  `gives_check()` en búsqueda y conserva ambas implementaciones como aserción
  debug (**67 inserciones/7 borrados**).
- `codex/nnue-avx2-exact`: transpone los stacks solo en memoria y usa dot
  products AVX2 `u8*i8`; TNN1, descriptor y `arch_hash` no cambian (**108
  inserciones**, dos ficheros).
- `codex/trust-generated-evasions`: evita el segundo `legal()` de las evasiones
  ya filtradas por `generate<EVASIONS>`; el TT move sigue validándose y debug
  reevalúa la premisa (**16 inserciones/3 borrados**).
- `codex/continuation-init-once`: adaptación NCF de upstream `50221673`; solo
  el thread NUMA 0 inicializa los **128 MiB** compartidos, eliminando ~**3 GiB**
  de escrituras redundantes a T24 (**10 inserciones/7 borrados**).

**Validación**:

- Los cuatro worktrees pasan `git diff --check`; no se compiló, bencheó ni
  ejecutó motor con T24 ocupado. A las **09:14:05,711 UTC**, PID 47696 seguía
  vivo a T24 y su hijo PID 33972 era un DATAGEN de 3Check.
- `curl -fsSL https://belzedar.duckdns.org/test/361/` confirmó #361 aprobado,
  prioridad **50**, **0/100** chunks, **0/10.000.000** posiciones y **0**
  intentos en todos los chunks. No se envió POST ni se tocó prioridad.
- Para el kernel SIMD, el extremo de cada suma adyacente es
  **[-32.512, 32.258]**, dentro de int16. La sonda corregida con seed 20260829
  comparó **20.000** vectores aleatorios de 32 entradas: **0** discrepancias
  entre producto escalar y agrupación `maddubs`.

**Autopsias propias**:

1. Un inventario de procesos pidió `CommandLine` completa y expuso en la salida
   interactiva una credencial del worker. El secreto no se copió a ningún
   fichero ni commit, pero el transcript ya no es apto para compartir sin
   redacción y la credencial debe rotarse. Regla derivada: inventariar PID,
   parent y nombre por defecto; línea de comando solo con redacción explícita.
2. `clang-format -i` se lanzó sin existir `.clang-format` y produjo un diff
   mecánico de **692 líneas**. Se verificó que el destino era el worktree
   creado por este agente y la rama exacta, se retiró solo ese scratch y se
   recreó desde `5226966`; el diff lógico volvió a **108 inserciones**. Regla:
   no ejecutar el formateador global sin estilo pineado; formatear hunks a mano.
3. La primera sonda aritmética terminó en `SyntaxError` por una expresión de
   asignación inválida dentro de una comprensión. Se contó como fallo de sonda,
   se corrigió el comando y solo la segunda ejecución aporta evidencia.

**Decisión**: **NO TEST / NO COMMIT** para las cuatro ramas hasta que el recurso
gate abra. En ese hueco, P1 desarrollo se repite primero; después se validan
estas ramas una por una. La validación oficial del runner/libro sigue siendo
prerrequisito de cualquier SPRT y P1 conserva la primera posición. #361 queda
esperando sin elevar su prioridad.

**Learnings**: una optimización matemáticamente exacta sigue necesitando paridad
del binario real; un ahorro NCF de arranque no es Elo; un diff aislado no es una
admisión a granja; y los diagnósticos también deben aplicar minimización de
secretos y contratos de formato reproducibles.

---

## 2026-08-29 — Predeclaración del smoke oficial de reloj: **VALIDATION_ONLY LISTO / NO ENVIADO**

**Hipótesis**: antes del primer SPRT Terachess, el camino real
servidor→worker→runner debe demostrar que carga net-2 y el libro autenticado,
envía `wtime/btime/winc/binc`, respeta el cap de 1.200 plies y conserva PGN
suficiente para medir tiempos. Una partida `GAMES` contra los mismos bytes es
un smoke operativo, no una comparación de fuerza.

**Estado e identidades verificados a las 09:27:27,987 UTC**:

- `belzedar.duckdns.org` respondió HTTP 200 y resolvió a `167.233.35.111`.
  El SSH documentado no aceptó la clave disponible (`Permission denied`), por
  lo que no se infirió el checkout de producción desde una copia local.
- La página oficial `/newTest/`, en sesión autenticada del propietario, lista
  `Terachess-Stockfish`, `TERACHESS_openings_v1.epd` y net-2. Las máquinas 9 y
  11 declaran Client **49**, T24 Windows/g++ 16.1.0 y T32 Linux/g++ 16.2.1.
- El pin v49 es `client_repo_ref=77b05ecd9c3d77a7934cab4d4406f103241daf4b`;
  ese árbol enruta `TERACHESS` a `uci_pair_runner.py` con variante `terachess`
  y añade literalmente `--max-plies 1200`.
- Ambos workers estaban en #407, prioridad **400**. Delante de Terachess siguen
  #396 Spell DATAGEN, prioridad **311**, y los SPRT Spell (por ejemplo #380,
  prioridad **304**). #361 permanece aprobado a prioridad **50**, 0/100 chunks
  y 0/10.000.000 posiciones. No se modificó ninguna fila ni prioridad.

**Contrato congelado antes del resultado**:

| Campo | Valor |
|---|---|
| modo | `GAMES`; etiqueta metodológica `VALIDATION_ONLY`; ninguna inferencia Elo |
| dev/base | `Terachess-Stockfish` @ `711177d601f5e16341277e81a141c63d0e61ef52` |
| bench | `32541` en ambos lados |
| red | `tera-net2.tnn`, SHA-256 `05162b618577fd28413f65c69aae9d549a9cd712451b5003e64dea7785e52861`, 56.858.966 B |
| libro | `TERACHESS_openings_v1.epd`, SHA-256 de texto/raw `1f117b0ed03049afad62481494fff9e3232774d188433a99ffff1454d84babe7` |
| TC/opciones | `60.0+0.6`; `Threads=1 Hash=16` en ambos lados |
| adjudicación | Syzygy WDL/ADJ `DISABLED`; win `movecount=6 score=5000`; draw `None` |
| evidencia | `upload_pgns=VERBOSE`, `workload_size=1`, `test_max_games=2` |
| cola | prioridad **50**, throughput **1000**; no se toca #361 ni cargas ajenas |

`test_max_games=2` pide un par con colores invertidos. OpenBench puede arrancar
más pares concurrentes antes del primer reporte; cualquier exceso se registra,
no se recorta ni se usa para cambiar el gate.

**Gate falsable**: PASS solo si ambos builds reproducen 32.541; hay al menos un
par completo y PGN verbose; cada PGN declara `Variant=terachess`, el FEN existe
en el libro registrado y termina antes de 1.200 plies sin crash, ilegal,
stall, time loss ni falsa tabla por cap. De los comentarios se publicarán por
máquina N, p50/p95/p99/máximo de ms por jugada y un límite inferior conservador
del reloj restante: `60.000 + 600*n - sum(floor(ms)) - n`; debe ser positivo
para ambos colores. El WDL y el `passed/failed` decorativo de un `GAMES`
self-play se ignoran por completo. Cualquier ausencia de PGN, identidad, ruta
de variante o timing es **FAIL cerrado** y se diagnostica antes de un SPRT.

**Decisión**: contrato listo, pero el formulario aún no se envió. P1 continúa
siendo el primer test de fuerza y este smoke no consume ninguno de sus seis
slots SPRT.

**Error de sellado**: el primer guard pre-commit trató la salida escalar de
`git diff --name-only` como si ya fuera un array y abortó antes de `git add`.
No hubo staging ni cambio externo; se corrigió envolviendo la captura en `@()`.

---

## 2026-08-29 — Enmienda de cola ordenada por el propietario: prioridad 400

**Hipótesis**: la prioridad es un parámetro operativo de encolado, no parte del
gate científico. Puede cambiarse por orden explícita sin alterar identidades,
TC, adjudicación, tamaño, evidencia ni criterio PASS/FAIL del smoke congelado.

**Cambio**: a las **10:01:20,274 UTC**, el propietario ordenó que todo workload
nuevo enviado por este agente use prioridad **400**. Esta instrucción sustituye
exclusivamente la fila `cola` de la predeclaración anterior para el smoke aún no
enviado: prioridad **400**, throughput **1000**. El workload DATAGEN #361 ya
existente permanece en prioridad **50** y no se modifica sin otra instrucción
explícita.

**Validación previa**: no se ha enviado aún ningún POST. El resto del contrato
permanece byte por byte: mismo commit `711177d601f5e16341277e81a141c63d0e61ef52`,
bench **32.541**, net/libro autenticados, `60.0+0.6`, `Threads=1 Hash=16`,
adjudicación declarada, `upload_pgns=VERBOSE`, `workload_size=1` y máximo **2**
partidas.

**Decisión**: usar prioridad **400** en el próximo envío. Se acepta que ese
valor empate la prioridad observada de #407 y supere las prioridades 311/304
observadas de las cargas Spell; el plan científico no cambia y el smoke sigue
siendo `VALIDATION_ONLY`, sin inferencia de fuerza ni consumo del presupuesto
SPRT de P1.

**Learning**: toda corrección operativa posterior al congelado se registra como
enmienda aditiva; nunca se reescribe el contrato histórico después de verlo.

---

## 2026-08-29 — OpenBench #408, smoke oficial de reloj: **PASS**; publicación PGN: **FAIL**

**Hipótesis**: el camino oficial servidor→cliente v49→runner Terachess debe
reproducir bench/red/libro, jugar al menos un par con reloj real, conservar
comentarios verbose y terminar legalmente antes de 1.200 plies. Es una prueba
`VALIDATION_ONLY`: el WDL entre los mismos bytes no mide fuerza.

**Cambios y envío**:

- La enmienda de prioridad quedó firmada y publicada en `8785e2e` (`Bench:
  32541`). Se usó prioridad **400** como ordenó el propietario; #361 siguió en
  **50**, sin modificación.
- Se confirmó acceso SSH administrativo con la clave específica ya configurada
  y el host/IP conocido `167.233.35.111`. No se reutilizó la credencial del
  worker ni se extrajo ninguna contraseña. El checkout de producción estaba
  `behind 61` y con cambios locales; se trató como solo lectura: ningún deploy,
  restart, edición, cambio de threads ni cambio de worker.
- Antes del alta, `verify_workload(request, "TEST")` devolvió **0 errores** y
  resolvió ambos lados al commit
  `711177d601f5e16341277e81a141c63d0e61ef52`, árbol GitHub
  `0d510e5a809989f8f0edeb3fcad16e4736161d9e`, bench **32.541** y artefactos
  disponibles. El alta se ejecutó por CLI dentro de una transacción con guard
  anti-duplicado y comprobaciones post-create; creó exactamente **#408** a las
  **10:08:20,684761 UTC**, aprobado, `awaiting=false` y prioridad **400**.
- Se añadió `tools/audit_openbench_pgn.py`, auditor fail-closed reutilizable de
  PGN verbose con autenticación de libro, detección de pares tras overshoot,
  bounds de reloj y replay opcional por ambos oráculos. El recibo compacto queda
  en `evidence/openbench_408_clock_smoke.json`.

**Identidad observada en producción**: la vista pública `/test/408/` respondió
HTTP **200** y mostró dev/base iguales, bench **32.541**, net-2, libro
`TERACHESS_openings_v1.epd`, `Threads=1 Hash=16`, TC canónica `60.0+0.60`, PGN
`VERBOSE`, Syzygy desactivado, win `movecount=6 score=5000` y draw `None`. El
cliente v49 enruta por los tokens `TERACHESS` del libro/motor a
`uci_pair_runner.py -variant terachess --max-plies 1200`; el campo DB
`variant_contract` vacío es el comportamiento esperado de este contrato
inferido y no una caída a ajedrez estándar.

**Resultado oficial**:

- Torom, máquina **11**, Linux, g++ **16.2.1**, cliente **49**, reportó a las
  **10:13:22,376439 UTC** un par: **2** partidas, W/D/L dev **0/0/2**, penta
  `[1,0,0,0,0]`, **0 crashes** y **0 time losses**. OpenBench marcó `failed`
  porque wins < losses; se ignora por contrato predeclarado, no se reinterpreta
  como fuerza. La máquina 9 llegó a reclamar trabajo, produjo **0** partidas y
  volvió a su carga anterior sin intervención.
- La concurrencia 32 arrancó runners antes del primer reporte: el blob de Torom
  contiene **33** partidas terminadas, de las que el servidor contó solo el par.
  Hay exactamente **1** par completo: juegos 5/32, Round 27, misma FEN SHA-256
  `6c9baa2ff14d3de70147fbedf5f0af050aeca30777417a24c3ca94d222f289d4`,
  colores dev/base invertidos, resultados `0-1`/`1-0` y **459/284 plies**.
- El BZip2 válido mide **184.304 B**, SHA-256
  `85b740f0a060595c74c95beb7868f58501f2fe48247aa44844f7b2349cfb850f`;
  descomprimido mide **638.012 B**, SHA-256
  `e0cd3c67a20c1073ee2accb585978744b53dd7565aae13d926346a45dfdda0de`.
  El blob del result vacío de máquina 9 mide **14 B**, SHA-256
  `d3dda84eb03b9738d118eb2be78e246106900493c0ae07819ad60815134a8058`.

**Gate de PGN/reloj** — comando reproducible:

```text
python tools/audit_openbench_pgn.py --pgn .scratch/ob408-evidence/408.532.1.pgn --book books/tera_openings_v1.epd --variant terachess --canonical-tc 60.0+0.6 --max-plies 1200 --min-complete-pairs 1 --replay-oracles --allow-mate-adjudication --output .scratch/ob408-evidence/generic_audit.json
```

- **33/33** FEN están en el libro autenticado de **5.000** posiciones, SHA-256
  `1f117b0ed03049afad62481494fff9e3232774d188433a99ffff1454d84babe7`;
  **33/33** declaran `Variant=terachess`, resultado header/trailer idéntico y
  comentarios verbose sin `unknown` ni malformados.
- Máximo **953 plies**, por debajo de 1.200. El replay final reproducible tardó
  **126,000 s**: **20.737/20.737** jugadas legales, **20.737** comparaciones de
  conjunto legal A/B, **0** ilegales y **0** divergencias. Hubo **26** finales
  terminales confirmados por ambos oráculos y **7** finales no terminales por la
  win-adjudication declarada, todos con secuencia final de mate. El par contado
  aporta **743/743** plies legales y dos mates/resultados exactos.
- La TC realmente escalada por Torom fue `21.43+0.21`, factor
  `0.3571651798706196`. Sobre las **20.737** muestras: p50/p95/p99/máximo por
  jugada **211/531/2.499/5.851 ms**; límite inferior mínimo del reloj canónico
  congelado **94.486 ms** y, como comprobación adicional más estricta sobre la
  cabecera escalada, **437 ms**. En el par: N **743**,
  **214/1.003/3.111/5.851 ms**, mínimos **94.486/536 ms**. Todos son positivos.
- Client v49 elimina `Termination` y `PlyCount` al compactar incluso en
  `VERBOSE`; por ello hay **0** headers `Termination`. No se fingió esa evidencia:
  los contadores servidor (0 crash/0 time loss), las 20.737 jugadas y los dos
  oráculos sustituyen esa señal perdida con evidencia más fuerte.

**Fallo operativo separado — agregador PGN público**: `/api/pgns/408/` devolvió
un JSON de **55 B**, SHA-256
`549b48fa2c362841731a04a832b8285b03ba67451bc5073660be18e1aa81d0f7`,
“Unable to find PGN”. No faltaba el upload: DB contiene PGN 228/229 con
`processed=false` y los dos blobs raw existen. Producción usa Gunicorn y no hay
servicio `PGNWatcher`; este watcher solo se arranca desde el `runserver` local.
No se arrancó manualmente: habría procesado y borrado el backlog global. Queda
**FAIL** la publicación por API, no el PGN raw ni el smoke del runner.

**Autopsias propias**:

1. La primera sonda importó `verify_workload` antes de `OpenBench.views` y chocó
   con el ciclo de imports; terminó antes de consultar o mutar. Se corrigió el
   orden y solo el segundo resultado (**0 errores**) cuenta.
2. El primer intento transaccional de alta esperaba TC almacenada `60.0+0.6` y
   `variant_contract=terachess`; OpenBench normaliza a `60.0+0.60` y persiste el
   contrato inferido como vacío. El guard vio **0** filas coincidentes y lanzó
   rollback: `max(id)` quedó en **407**. Una transacción de observación también
   se forzó a rollback, reveló los valores exactos y el alta corregida creó solo
   #408. Regla: el guard idempotente compara representación persistida, validada
   en rollback, no la representación del formulario.
3. La primera descarga confió en la extensión `.tar`; `tar` rechazó el fichero.
   Se midieron sus **55 B**, se leyó el JSON y se diagnosticaron DB/files antes
   de reintentar. Regla: validar tipo, tamaño y hash de toda descarga antes de
   tratar su nombre como evidencia de formato.

**Decisión**: smoke oficial de runner/reloj **PASS**; fuerza **NO MEDIDA**;
publicación agregada de PGN **FAIL** y deuda OpenBench separada. P1 queda
desbloqueada por este prerrequisito, mantiene su presupuesto de seis SPRT y su
orden experimental. #361 sigue aprobado, 0/10.000.000 y prioridad 50 salvo
avance posterior de workers; esta prueba no cambió su configuración.

**Learnings**: un flag visual `failed` no sustituye el gate; una API rota no
implica pérdida del upload si DB+bytes demuestran lo contrario; la concurrencia
convierte `max_games=2` en **33 PGN** aunque solo contabilice dos, por lo que el
par debe identificarse por FEN+colores; y el bound sobre la TC escalada (**437
ms**) es la comprobación de reloj real más exigente, mientras el bound canónico
se conserva exactamente como fue predeclarado.

---

## 2026-08-29 — P1 LMP después del smoke: **RAMAS PREPARADAS / RECURSO CERRADO / 0 TESTS**

**Hipótesis**: una vez validado el runner oficial, P1 sigue necesitando el gate
causal offline retirado el 12 de agosto. No se puede convertir la señal de las
127 raíces válidas en permiso de granja, ni competir con el workload Spell que
el propietario ordenó ejecutar primero. Las tres fórmulas congeladas deben
vivir en diffs separados y cualquier workload nuevo de este agente usará
prioridad **400**.

**Cambios preparatorios**:

- Se crearon desde `999ccfd` tres worktrees y ramas locales, todavía sin commit:
  `codex/lmp-u2`, `codex/lmp-d4` y `codex/lmp-u34`. Cada una toca solo
  `src/search.cpp`: `U2 = 2*T0`; D4 omite LMP exactamente para `depth <= 4`; y
  `U3/4 = max(1, floor(3*T0/4))`. `git diff --check` dio 0 errores en las tres.
- Una comprobación exhaustiva de las expresiones para depth 1--20 y ambos
  valores de `improving` dio **40 casos, 0 discrepancias** contra las fórmulas
  congeladas. No se compiló, no se midió bench/NPS y no se hizo push de esas
  ramas: por tanto siguen en estado **NO TEST**.
- Se estudió transportar el arnés como DATAGEN de OpenBench. El cliente v49 no
  ejecuta un comando externo: lanza el binario construido y le escribe por
  stdin una única línea UCI; solo conserva el fichero `{OUT}`. El arnés actual
  necesita lanzar Python, autenticar trace+transcript+receipt y dos procesos
  independientes. Alterar motor/cliente para envolverlo sería infraestructura
  nueva y cambiaría el experimento; se rechazó. La lectura correcta de "todos
  los tests por OpenBench" queda: todos los benches/matches oficiales van a
  OpenBench; el arnés causal previo sigue siendo una medición offline, como
  exige `docs/staging-program.md`.

**Gate de recurso**:

- La primera fotografía local mostró **24** procesos Spell y **25** procesos
  Python auxiliares; añadir el motor monohilo habría creado el hilo de motor 25
  contra el límite heredado de 24. No se lanzó.
- En una transición posterior hubo 0 motores visibles y **5.843 MiB** libres,
  pero producción todavía ligaba la máquina 9 al workload Spell **#378**,
  prioridad **304**, concurrencia **24**, actualizado a
  `2026-08-29 10:45:24,877790 UTC`. Una ausencia momentánea de procesos no es
  un lease: tampoco se lanzó ahí. No se paró, reinició ni reconfiguró el worker.
- #361 se releyó en producción: DATAGEN, aprobado, sin error, prioridad **50**,
  **0/10.000.000** registros y **0** chunks. No se modificó.

**Autopsia propia**: la primera inspección de cuatro diffs de rendimiento abrió
cuatro procesos PowerShell en paralelo mientras Spell ocupaba la RAM. Uno cayó
con error CLR y tres con `OutOfMemory`/inicialización de PowerShell; no hubo
escrituras ni procesos persistentes. La repetición secuencial, en un solo
PowerShell, leyó los cuatro diffs. Regla derivada: bajo worker T24, paralelizar
I/O con un solo proceso y herramientas ligeras; no multiplicar runtimes
PowerShell aunque la tarea sea solo lectura.

**Validación estática adicional**: `continuationHistory` es una referencia al
`SharedHistories` del nodo NUMA, por lo que su limpieza redundante sí es un
candidato real de arranque; `generate<EVASIONS>` filtra explícitamente cada
movimiento con `legal()`, por lo que evitar la segunda validación en search es
una hipótesis real. Ninguna de las dos observaciones es un dato de velocidad o
fuerza y ambas conservan estado **NO TEST**.

**Decisión**: **0 workloads nuevos** y **0 slots P1 consumidos**. El siguiente
paso sigue siendo dos colecciones completas de desarrollo con la barrera final,
128/128 raíces a >=100.000 nodos y paths distintos; solo un PASS permite abrir
holdout. Después se compila/benchea la rama P1 admitida y su SPRT STC se crea
por CLI en OpenBench con prioridad **400** y los bounds ya congelados `[1,6]`.

**Learnings**: una transición de lote no es capacidad autorizada; llevar el
arnés a DATAGEN no es un simple cambio de transporte cuando se perderían dos de
sus tres recibos; y preparar un diff no autoriza ni su commit funcional ni un
test antes de reproducir bench, NPS y el gate causal.

**Revalidación posterior (10:51:31 UTC)**: el hueco observado terminó sin que
este agente lanzara nada. La máquina 9 pasó a OpenBench #312,
`Horde-Stockfish test/qsearch-disable-movecount`, SPRT, prioridad **301**,
6.168 partidas, `finished=false`, `error=false`; localmente había **48**
procesos Horde y **6.074 MiB** disponibles. El gate de recurso continuó
cerrado. Una sonda de presencia de `openbench.exit` falló primero por un pipe
PowerShell sintácticamente inválido; la repetición corregida halló **0/2** flags
en los dos checkouts conocidos. Un monitor posterior de 30 s devolvió stdout
vacío y se descartó; la fotografía inmediata siguiente es la que aporta los
48 procesos y el workload #312. Ninguna de esas sondas mutó el worker.

**Fallo de publicación**: el commit documental se creó localmente, pero el
primer `git push origin main` abortó antes de obtener credenciales: Git
Credential Manager no pudo cargar `System.Net.Http` con HRESULT `0x800705AF`
(`paging file is too small`). No hubo push parcial ni cambio remoto. Se prohíbe
reintentar en bucle bajo los 48 motores; `main` queda un commit por delante
hasta que el lote libere memoria y permita un único reintento comprobado.
