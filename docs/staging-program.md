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

Arnés offline previo: la búsqueda primaria conserva el LMP baseline. En una
muestra determinista de nodos donde el baseline empieza a omitir quiets, un
replay aislado conserva posición, ventana, profundidad corriente, tipo de nodo,
orden de MovePicker y estado anterior al trigger. La etiqueta no es un
"mejor movimiento" genérico: mide si la cola omitida cambia el bound/PV del
nodo. Debe informar exposición, recall de la primera quiet crítica, fracción de
clase retenida y ratio de trabajo, ponderados por nodo y por raíz. Un recorrido
global con LMP desactivado **no es admisible** porque cambia árbol, TT e
historiales antes de tomar la muestra.

Gate del arnés: 256 raíces independientes (128 desarrollo + 128 holdout sin
solape con el holdout del libro), ≥20.000 nodos LMP-expuestos en holdout y
≥1.000 observaciones por estrato profundidad {4-5, 6-8, 9-12} × improving.
La simulación baseline debe reproducir el conjunto real retenido/omitido en
100 %, dos repeticiones deben ser byte-idénticas, y no-LMP debe retener el
100 % por construcción. Solo puntos no dominados del holdout pueden ir a
partidas.

Muestra congelada en `tools/lmp_shadow_roots_v1.json`: SHA-256
`099d9eec8ef58f8608cefca4f7011546e8211de6e6b5b02f461741807fd0c661`,
derivada de `data/c3_final.bin` SHA-256
`24671a8ca66eb241eb71d79c1cd3023410088dfdf0ba9ebfb75e79cf593627ff`.
Las raíces están separadas por ≥1.201 registros (mínimo obtenido: 1.285),
pasaron ambos oráculos con 0 fallos y tienen 0 FEN comunes con el libro v1.
Tras dimensionar **solo desarrollo**, se congeló el pase completo antes de
abrir holdout: Threads=1, Hash=16 MiB, `ucinewgame` por raíz, 100.000 nodos por
raíz, muestreo 1:1 y cap 24.000 repartido en 4.000 registros por cada una de
las seis celdas. Desarrollo y holdout se ejecutan dos veces desde proceso
nuevo; el holdout no puede alterar esos parámetros. Cada recibo debe autenticar
su propio trace, transcript, binario, red y manifiesto; las dos ejecuciones no
pueden reutilizar paths. Como `go` es asíncrono, el script UCI termina con una
barrera `ucinewgame` antes de `quit`, y las **128/128** raíces deben acreditar
≥100.000 nodos: una sola raíz truncada invalida el pase completo.

**Matriz congelada: seis SPRT son tres pares STC/LTC**, todos contra el mismo
baseline net-2. Corrección del propietario del 2026-08-30, aplicada antes de
que existiera una sola partida de fuerza válida: metodología OpenBench con
bounds `[0, 10]`, α=β=0,05 y cap 1.200 plies. STC usa `10.0+0.10`,
`Threads=1 Hash=32`, workload 32; LTC usa `30.0+0.30`,
`Threads=1 Hash=128`, workload 8. Un SPRT termina por LLR, sin un
`max_games` artificial.

1. `U2`: `T = 2·T0`, STC; su LTC solo si STC cruza H1.
2. `D4`: LMP desactivado solo cuando la profundidad corriente `d ≤ 4`, STC;
   su LTC solo si STC cruza H1.
3. `U¾`: `T = max(1, floor(3·T0/4))`, STC (control de dirección: poda más);
   su LTC solo si STC cruza H1.

`T0 = floor((3+d²)/(2-improving))` en el call-site actual; el resto de guards y
clases no cambia. Un slot LTC que no se activa se retira, no se reasigna. El
barrido offline conserva `k={1,5; 2; 3; 4}`, no-LMP, variantes D4 y
`max(T0, ceil(legales/2))`, pero no añade SPRT ni permite cambiar la matriz
después de mirar resultados. **Dirección esperada**: podar menos; la variante
`U¾` falsifica explícitamente esa expectativa y respeta el presupuesto en ambas
direcciones.

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

### P5 — Threat tiers de las 26 piezas · presupuesto 4 SPRT
El ordering quiet heredado solo construye `threatByLesser` para N/B/R/Q. En las
256 raíces congeladas de P1, el oráculo B enumeró 45.198 quiets: solo 5.933
(13,127 %) pertenecen a esos cuatro tipos y 39.265 (86,873 %) reciben una
señal vacía. No se infiere fuerza de esa cobertura.

Antes de granja, un arnés debe etiquetar por ocupación real si cada quiet entra
en/sale de ataques de piezas enemigas de valor estrictamente menor, incluidos
hoppers y bent riders, y medir cobertura, orden relativo, coste y estabilidad
por tipo. Como máximo dos puntos no dominados van a STC; cada uno consume su
LTC de confirmación dentro de los cuatro slots. Coeficiente y clasificador no
cambian juntos.

### P6 — Arquitectura de red S → M · presupuesto 2 SPRT
Solo tras net-2 verde y con ≥100 M posiciones. Incluye la fusión de buckets de
salida 0-1-2 pendiente (`docs/nnue-tera-s.md`, desviación declarada) y el
`arch_hash` nuevo que conlleva.

### P7 — Modelo WDL propio · sin SPRT (infraestructura)
Ajustar `win_rate_params` con resultados reales de Terachess para que la salida
`wdl` y cualquier futura normalización tengan sentido (hoy la de ajedrez está
neutralizada por ADR-001).

## Cerradas antes de abrirse

- **SEE con pantallas**: la familia P5 original partía de una lectura errónea.
  Desde `94fab4f`, `see_ge` recalcula `attackers_to(to, occupied)` en cada
  intercambio, incluidos hoppers y bent riders. No se ensaya como fuerza.
- **Magic bitboards**: imposibles en 256 casillas con 4 limbs (no existe la
  multiplicación de 64 bits que concentre el índice). Ray-scan es la baseline;
  kindergarten por línea es la optimización, no una familia de fuerza.
- **Adjudicación por tablas**: con 0 tablas en 246 partidas no hay nada que
  adjudicar.

## Condición de salida del programa

Si tras agotar P1 y P2 (12 SPRT) ninguna variante supera los bounds [0, 10], la
hipótesis "las constantes chess-tuned son el techo de esta búsqueda" queda
falsada y el esfuerzo se redirige a datos y arquitectura de red (P6), no a más
tuning de búsqueda.
