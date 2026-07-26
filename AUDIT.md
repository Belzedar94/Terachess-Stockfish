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
