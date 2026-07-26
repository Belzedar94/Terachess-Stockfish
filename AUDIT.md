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
