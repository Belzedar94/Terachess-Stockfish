# Programa de mejora continua (F5) — familias, presupuestos y orden

Regla heredada de Spell (retroactive-review E1–E5), obligatoria aquí:

1. Toda idea con **clasificador, umbral o dosis** pasa por **arnés offline antes
   que granja**: instrumentar → métrica offline contra ground truth → matriz de
   variantes → solo entonces partidas, **una variable por vez**.
2. **Presupuesto de persistencia declarado al abrir la familia** y respetado en
   ambas direcciones: un toggle atómico = 1 test; una familia con espacio de
   diseño = 6–10.
3. **Confundidos prohibidos**: un knob por SPRT salvo interacción hipotetizada.
4. El ledger lleva columna "espacio explorado / presupuesto usado".
5. **LTC de confirmación siempre**; nada se mergea con STC solo.

## Familias abiertas, por prioridad

### P1 — LMP (late move pruning) · presupuesto 6 SPRT
**Por qué es la primera**: `moveCount >= (3 + depth²)/(2 − improving)` poda entre
el **18 % y el 89 %** de los movimientos quietos según profundidad
(`docs/search-audit.md` §2). En ajedrez esa fórmula no poda casi nada. Es la
distorsión más grande que queda en el árbol.

Arnés offline previo: comando debug que vuelca, por nodo, el número de legales y
la posición en el orden del mejor movimiento realmente jugado; métrica = qué
fracción de "mejores movimientos" caen más allá del umbral de LMP a cada
profundidad (recall del umbral) frente al ahorro de nodos (class-size). Solo los
2-3 puntos de la frontera de Pareto van a partidas.

Candidatas: (a) umbral proporcional al número de legales del nodo;
(b) `(3 + d²)·k` con k barrido en {1,5; 2; 3; 4}; (c) sin LMP a depth ≤ 4.
**Dirección esperada**: podar MENOS (meta-lección de Spell: con branching
gigante, "buscar más pero mejor elegido" gana; toda idea de podar la clase
especial falló).

### P2 — Escalas de futility / razoring / probcut · presupuesto 6 SPRT
Los márgenes están en cp de ajedrez (peón = 100) y nuestra escala es
Zillions×100 (peón = 50, Amazona = 1.020): valen ~2× lo pretendido medidos en
peones. Barrido de un factor global k ∈ {0,5; 0,75; 1; 1,5} sobre los márgenes,
un test por valor, antes de tocar cada margen por separado.

### P3 — Dosis del tope de LMR · presupuesto 4 SPRT
`min(moveCount, 40)` fue una corrección de **corrección**, no un valor afinado
(sonda #1 inconclusa: +8,7 ± 38,9). Barrer el tope en {20, 40, 60} y la variante
dinámica `moveCount·40/legales`.

### P4 — Bucketing de historiales · presupuesto 4 SPRT
`piece_slot = color·8 + type % 8` mezcla tipos sin relación semántica (PAWN con
MISSIONARY). Candidata: agrupar por **familia de movimiento** (saltador corto /
deslizante / hopper con pantalla / bent rider / real). Requiere medir primero,
offline, cuánta colisión real hay en las posiciones del datagen.

### P5 — SEE con piezas de pantalla · presupuesto 3 SPRT
El bucle de intercambios no recalcula pantallas tras cada captura
(`docs/search-audit.md` §5). Candidatas: excluir hoppers del bucle (valor fijo);
recalcular pantallas por iteración (caro). Riesgo de calidad de poda, no de
legalidad.

### P6 — Arquitectura de red S → M · presupuesto 2 SPRT
Solo tras net-2 verde y con ≥100 M posiciones. Incluye la fusión de buckets de
salida 0-1-2 pendiente (`docs/nnue-tera-s.md`, desviación declarada) y el
`arch_hash` nuevo que conlleva.

### P7 — Modelo WDL propio · sin SPRT (infraestructura)
Ajustar `win_rate_params` con resultados reales de Terachess para que la salida
`wdl` y cualquier futura normalización tengan sentido (hoy la de ajedrez está
neutralizada por ADR-001).

## Cerradas antes de abrirse

- **Magic bitboards**: imposibles en 256 casillas con 4 limbs (no existe la
  multiplicación de 64 bits que concentre el índice). Ray-scan es la baseline;
  kindergarten por línea es la optimización, no una familia de fuerza.
- **Adjudicación por tablas**: con 0 tablas en 246 partidas no hay nada que
  adjudicar.

## Condición de salida del programa

Si tras agotar P1 y P2 (12 SPRT) ninguna variante supera los bounds [1, 6], la
hipótesis "las constantes chess-tuned son el techo de esta búsqueda" queda
falsada y el esfuerzo se redirige a datos y arquitectura de red (P6), no a más
tuning de búsqueda.
