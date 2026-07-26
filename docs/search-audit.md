# Auditoría de búsqueda (playbook doc 10) — receipt F2a

**Motivo**: el chasis hereda constantes afinadas para ajedrez (branching ~35).
Terachess II tiene **branching medido 98–300** (perft(1) sobre 15 posiciones de
random-walk; 54 en la inicial). Antes de creerse UNA SOLA medición hay que
auditar cada heurística cuyo comportamiento dependa del número de movimientos.
Lección Spell: *"This distortion affected every previous measurement."*

Formato: Área / Supuesto de ajedrez / Estado en Terachess / Evidencia / Riesgo
residual / Decisión.

---

## 1. LMR — término lineal en moveCount — **BUG CONFIRMADO Y CORREGIDO**

**Supuesto**: `r -= moveCount * 62` compensa el término logarítmico
`reductions[d] * reductions[mn]` en el rango de ajedrez (mn ≤ ~60).

**Estado medido** (`reductions[i] = int(22.14·ln i)`), nodo a depth 8, improving:

| moveCount | reductionScale | r (1024 = 1 ply) | efecto |
|---|---|---|---|
| 4 | 1.380 | +2.256 | reduce 2,20 plies |
| 20 | 3.036 | +2.920 | reduce 2,85 plies |
| 40 | 3.726 | +2.370 | reduce 2,31 plies |
| 67 | 4.278 | +1.248 | reduce 1,22 plies |
| **120** | 4.830 | **−1.486** | **EXTIENDE 1,45 plies** |
| **180** | 5.244 | **−4.792** | **EXTIENDE 4,68 plies** |
| **300** | 5.796 | **−11.680** | **EXTIENDE 11,41 plies** |

Cruce a extensión por profundidad: depth 4 → mn 63; depth 6 → 79; depth 8 → 93;
depth 10 → 100; depth 12 → 111; depth 14 → 117. Con 150–300 legales, **todos
los nodos de mediojuego entran en la zona de extensión perversa**.

**Decisión**: `r -= std::min(moveCount, 40) * 62` — se conserva el
comportamiento de ajedrez en su rango de validez y se elimina la extensión.
El 40 es un tope conservador, NO un valor afinado: queda como knob para SPSA en
F5 (presupuesto de familia: 1 toggle ya gastado aquí, dosis pendiente).

**Evidencia del cambio**: sonda A/B a nodos fijos, 80 partidas a 10 k nodos con
cap de 1.000 plies (`tools/fixed_nodes_match.py`, oráculo como árbitro):
**+41 −39 =0, Elo +8,7 ± 38,9, 0 anomalías, 464,7 plies de media**.
Firma de bench: 22.723 → 21.519 (árbol distinto, como debe ser).

**Lectura honesta**: el resultado es **INCONCLUSO en Elo** — la barra de error
(±39) es cuatro veces el efecto medido. La corrección NO está justificada por
ganancia demostrada, sino por **corrección matemática**: extender 4,7 plies los
movimientos tardíos es indefendible en cualquier árbol, y la sonda descarta una
regresión grande. Resolver ±10 Elo exigiría varios miles de partidas; queda como
candidato a SPRT formal en F4/F5 junto con la dosis del tope (40).

**Riesgo residual**: el tope 40 es arbitrario; puede que el óptimo escale con el
branching real de cada nodo (p. ej. `min(mn, 40)` vs `mn · 40/movesGenerated`).
No se explora aquí (un knob por experimento).

---

## 2. LMP (late move pruning) — **RIESGO ALTO, NO TOCADO (medición pendiente)**

**Supuesto**: `moveCount >= (3 + depth²)/(2 − improving)` deja pasar casi todos
los movimientos en ajedrez (a depth 8 improving: 67 ≥ los ~35 legales típicos
⇒ no poda nada).

**Estado en Terachess** (de ~180 legales):

| depth | umbral improving | umbral no-improving | % de movimientos podados (improving) |
|---|---|---|---|
| 4 | 19 | 9 | ~89 % |
| 6 | 39 | 19 | ~78 % |
| 8 | 67 | 33 | ~63 % |
| 10 | 103 | 51 | ~43 % |
| 12 | 147 | 73 | ~18 % |

Es decir: una heurística que en ajedrez es casi inocua aquí **poda entre el 18 %
y el 89 % de los movimientos quietos**. Es la explicación más probable de la
"profundidad hueca" observada en la calibración (depth media 8,4 con 10 k nodos
y branching 180 — imposible con un árbol sano).

**Decisión**: NO se toca en F2. Es una familia con dosis y umbral ⇒ el playbook
y la lección Spell C1 exigen **arnés offline antes que granja** y matriz de
variantes, no un one-shot. Se abre como familia en F5 con presupuesto declarado
(6 SPRT) y estas candidatas: (a) umbral proporcional al número de legales;
(b) `(3 + d²)·k` con k barrido; (c) sin LMP a depth ≤ 4. Meta-lección Spell:
*"every idea that searches FEWER moves fails"* — la dirección esperada es podar
MENOS, pero se mide.

**Riesgo residual (declarado)**: todas las mediciones de F2/F3 se hacen con este
LMP chess-tuned. Son válidas como comparaciones RELATIVAS entre builds propios;
no lo son como estimación de la fuerza absoluta alcanzable.

---

## 3. Métrica de blunder de la calibración — **CONTAMINADA, documentado**

`tools/calibrate_nodes.py` mide blunder = re-búsqueda a 4N refuta por >150 cp.
Resultado medido (40 posiciones): 10 k → 0,0 %; 20 k → 2,5 %; 40 k → 0,0 %;
delta medio 3,7–9,6 cp. **No me lo creo, y no debe usarse tal cual**:

1. Con **eval material**, el árbitro a 4N tiene la MISMA función de evaluación:
   la métrica mide autoconsistencia, no calidad. En posiciones quietas el
   material no cambia ⇒ delta ≈ 0 por construcción.
2. La depth reportada está inflada por el LMP del punto 2 (profundidad hueca):
   depth 8,4 a 10 k nodos con branching 180 no es un árbol sano.

**Decisión**: la regla predeclarada del plan ("mínimo N con blunder <15 % y
depth ≥5") **no puede resolverse con eval material**. Se sustituye por:
- **F2 (entonces)**: nodos de datagen fijados por presupuesto de tiempo real
  (pos/s medidas) y por el punto de rendimientos decrecientes del delta medio.
- **F3b (tras net-1)**: re-ejecutar la calibración con la red S cargada, donde
  el árbitro sí aporta información independiente.

### DEUDA CERRADA (2026-07-19, con net-1 cargada)

`calibrate_nodes.py --net tera-net1.tnn`, 30 posiciones:

| nodos | depth media | blunder-rate | delta medio |
|---|---|---|---|
| 10.000 | 9,40 | **6,7 %** | 22,4 cp |
| 20.000 | 10,77 | 0,0 % | 5,1 cp |
| 40.000 | 12,03 | 0,0 % | 6,7 cp |

Ahora la métrica **sí discrimina** (6,7 % → 0 %, delta 22,4 → 5,1 cp), porque el
árbitro a 4N nodos evalúa con una función aprendida y no con el mismo material
del que se derivaba la jugada. Comparar con la tabla contaminada de arriba
(0 % / 2,5 % / 0 % sin señal) muestra exactamente qué aportaba la red.

**Regla aplicada**: mínimo N con blunder <15 % y depth ≥5 ⇒ **10.000 nodos**.
Valida a posteriori los 8.000–12.000 usados en las campañas 2 y 3.
La depth reportada (9,4–12,0) sigue inflada por el LMP chess-tuned (§2): no debe
leerse como profundidad efectiva.

---

## 3bis. Longitud y decisividad de las partidas — **MEDIDO (dato fundacional)**

Piloto de 6 partidas self-play a 10 k nodos, cap de 1.200 plies, sin
adjudicación (`tools/`, oráculo como árbitro):

| Partida | Plies | Final | Piezas restantes |
|---|---|---|---|
| 0 | 842 | 0-1 (mate) | 7 |
| 1 | 473 | 1-0 (mate) | 51 |
| 2 | 380 | 0-1 (mate) | 58 |
| 3 | 639 | 1-0 (mate) | 29 |
| 4 | 631 | 1-0 (mate) | 20 |
| 5 | 483 | 1-0 (mate) | 34 |

**Media 575 plies (~287 jugadas). 6/6 decisivas por mate real, 0 tablas.**
~18 s por partida a 10 k nodos y 1 hilo.

Consecuencias operativas:
1. **Cualquier cap por debajo de ~800 plies fabrica tablas artificiales.** Una
   medición previa con cap 300 dio 100 % de tablas y era un artefacto del cap,
   no una propiedad del juego. Regla: cap ≥1.000 plies en toda sonda y SPRT.
2. La tasa de tablas real es baja (consistente con la predicción del plan <10 %)
   ⇒ SPRT eficiente: cada partida informa ~2× lo que una de ajedrez
   (1 nElo ≈ 2 Elo). Se mantienen bounds en Elo crudo [1, 6].
3. Coste por SPRT estimado: 5.000 partidas × ~18 s ≈ 25 h monohilo ⇒ ~1 h con
   24 hilos en paralelo. Presupuesto viable.
4. Una partida de ~575 plies genera del orden de 300–500 registros tras filtros
   ⇒ el datagen es eficiente por partida aunque cada partida sea larga.
5. La adjudicación por evaluación es **opcional** (red de seguridad), no
   necesaria para resolver partidas. Se deja en |eval| ≥5.000 cp × 6 jugadas.

## 4. Historiales bucketed — **RIESGO MEDIO, aceptado con evidencia pendiente**

`ContinuationHistory` naïve en 256 casillas serían ~2 GiB. Se implementó
bucketing de tipos a 8 clases (`piece_slot(pc) = color*8 + type%8`), 32 MiB por
instancia. El hash `type % 8` mezcla tipos sin relación semántica (p. ej. PAWN
con MISSIONARY). CorrectionHistory también bucketed en ambas dimensiones.

**Decisión**: aceptado para F2 (sin alternativa viable en memoria). Candidatas
para F5: agrupar por familia de movimiento (leaper corto / slider / hopper /
bent rider / real) en vez de `% 8`. Riesgo residual: la ordenación de
movimientos es peor de lo que sería con historial por tipo exacto; afecta a
todas las mediciones por igual (no sesga comparaciones A/B).

## 5. SEE con piezas de pantalla — **RIESGO ALTO, mitigado parcialmente**

Cannon/Archer/Sorceress capturan saltando una pantalla: rompen el supuesto de
SEE de que el atacante llega por su propia línea despejada, y de que retirar un
atacante descubre al siguiente. `attackers_to` sí las modela; el bucle de
intercambios de `see_ge` NO recalcula pantallas tras cada captura.

**Decisión F2**: se deja el SEE estándar (los hoppers participan como atacantes
detectados por `attackers_to` pero el intercambio simulado puede ser inexacto).
Fixtures de pantallas existen en F0 y el movegen es correcto (perft masivo
1.000 posiciones / 37,4 M nodos, 0 discrepancias) ⇒ el riesgo es de *calidad de
poda*, no de legalidad. Candidata F5: excluir hoppers del bucle de SEE
(tratarlos con valor fijo) y medir.

## 6–10. Resto de heurísticas

| Área | Supuesto | Estado | Decisión |
|---|---|---|---|
| Null move | Zugzwang raro; `non_pawn_material` como guardia | Con 64 piezas por bando el zugzwang es aún más raro; la guardia sigue siendo válida | Sin cambios; riesgo bajo |
| Futility / razoring | Márgenes en cp de ajedrez (peón=100) | Nuestra escala es Zillions×100 (peón=50, Amazona=1020): los márgenes valen ~2× más de lo pretendido en peones | Documentado; re-escalado es familia F5 con dosis |
| ProbCut | Margen fijo sobre beta | Igual que futility (escala) | Idem |
| TT | Entrada de 12 B, move u32, clave con ep-por-casilla y derechos de salto | Verificado: el estado semántico completo entra en la clave (test_aliasing) | Sin cambios |
| Repetición / cuckoo | `upcoming_repetition` desactivado (tabla de 8192 no escala a 26 tipos × 256 casillas) | Se pierde detección temprana de repetición; la repetición normal sí funciona | ADR aceptado; re-evaluar con tabla 2^17 en F5 |
| Gestión de tiempo | Sin cambios respecto a master | Las partidas son 2–4× más largas (plies medios medidos en las sondas) | Medir en F4 antes de fijar TC |

---

## Conclusión de la auditoría

Un bug real y corregido (LMR), una heurística de riesgo alto documentada y
abierta como familia (LMP), una métrica de calibración invalidada honestamente
(blunder con eval material) y cuatro riesgos aceptados con su condición de
revisión. **Ninguna medición posterior de este proyecto debe presentarse como
fuerza absoluta hasta que LMP y las escalas de futility se hayan barrido en
F5.** Las comparaciones A/B entre builds propios sí son válidas desde ya.
