# ADR-001 — Unidades de evaluación: el cp reportado ES la unidad interna

**Fecha**: 2026-07-19 · **Estado**: aceptada · **Detectado por**: el gate de
fuerza de F3 (la red perdió 511 Elo contra la eval material a la que imitaba con
correlación 0,992).

## Contexto

Tres componentes hablan de "centipeones" y hasta hoy no significaban lo mismo:

| Componente | Qué producía |
|---|---|
| `Eval::evaluate()` | unidad interna (peón = 50, Amazona = 1020) |
| `Score` / salida UCI | `to_cp(v, pos)` = `round(100·v / a)` con `a` del modelo WDL |
| Datagen (`score_to_cp`) | el valor de `Score::InternalUnits`, que **pese al nombre ya está convertido** (`score.cpp:34`) |

Dos defectos encadenados:

1. **El campo `InternalUnits` no contiene unidades internas.** En `score.cpp` se
   construye como `InternalUnits{UCIEngine::to_cp(v, pos)}`. El datagen confió
   en el nombre y grabó cp normalizados.
2. **El modelo WDL es de ajedrez.** `win_rate_params` calcula
   `material = P + 3N + 3B + 5R + 9Q`: **toda pieza propia de Terachess vale
   cero** (Camello, León, Cañón, Duquesa…), y además satura el conteo a [17, 78]
   cuando la posición inicial tiene 128 piezas. El divisor `a` degeneraba en una
   constante arbitraria.

**Consecuencia medida**: los scores grabados eran ~1/4 de la evaluación de la
que salían (ratio mediano 0,242; desviación 499 frente a 2.043 en las mismas
120 posiciones). La red aprendió fielmente ese espacio comprimido —correlación
0,992 con el material, Pearson 0,994 con su label— y al insertarla como
`Eval::evaluate()` todos los márgenes de poda (futility, razoring, delta,
ventanas de aspiración), calibrados en la escala del material, se volvieron
efectivamente 4× más agresivos. Resultado: **+8 −152 =0, −511 Elo**.

## Decisión

`UCIEngine::to_cp(v, pos) = int(v)` — **identidad**. En Terachess la unidad
interna ES el centipeón reportado. Con ello:

- Los labels del datagen vuelven a estar en el mismo espacio que
  `Eval::evaluate()`, que es lo que una red entrenada con ellos debe reproducir.
- La salida UCI deja de mentir: `score cp` coincide con `eval`.
- No hace falta tocar `score.cpp` ni el datagen.

La salida `wdl` queda sin sentido (ya lo estaba) hasta que se ajuste un modelo
WDL propio.

## Alternativas descartadas

- *Grabar el `Value` crudo en el datagen dejando `to_cp` como está*: arregla el
  entrenamiento pero mantiene una salida UCI de escala arbitraria y dos espacios
  de unidades conviviendo — justo la ambigüedad que causó el fallo.
- *Ajustar `win_rate_params` a Terachess ya*: requiere miles de partidas con
  resultado para ajustar el modelo. Es trabajo de F4, no un parche.

## Consecuencias

- **Los datos generados antes de este commit son inservibles para entrenar**
  (720.684 registros de la campaña 1). Su escala depende del material vía `a`,
  así que ni siquiera son recuperables con un factor constante. Se descartan.
- La red `net1pre` entrenada con ellos se archiva como evidencia del fallo.
- Todas las constantes de búsqueda heredadas siguen expresadas en unidades
  internas, que ahora son coherentes de punta a punta.

## Gate nuevo (para que no vuelva a pasar)

`tools/check_label_units.py`: sobre una muestra de registros, compara el label
grabado con la evaluación estática del motor en la misma posición y exige
que la pendiente de la regresión esté en [0,8, 1,25] y la correlación >0,9.
Se ejecuta como parte de la auditoría de toda campaña, ANTES de entrenar.
