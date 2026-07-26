# Calibración estadística de Terachess (F4)

Todo lo de aquí está **medido en Terachess**, no heredado de Spell ni de ajedrez.
Regla del playbook: los bounds y la adjudicación se declaran ANTES de lanzar y
no se tocan después de ver un resultado.

## 1. Tasa de tablas — medida

| Fuente | Partidas | Tablas | Nota |
|---|---|---|---|
| Piloto de longitud (10 k nodos, cap 1.200) | 6 | 0 | 6/6 por mate real |
| Sonda #1 lmr-cap-40 (10 k nodos, cap 1.000) | 80 | 0 | 0 anomalías en ~37.000 plies |
| Gate F3 net vs material (10 k nodos, cap 1.000) | 160 | 0 | — |
| **Total** | **246** | **0** | IC95 (regla de tres) **< 1,2 %** |

Terachess es **radicalmente decisivo**: ni una sola tabla en 246 partidas
arbitradas por el oráculo. Menos aún que Spell (4 %).

Consecuencia: cada partida aporta cerca del máximo de información posible. La
relación nElo↔Elo tiende a 1 nElo ≈ 2 Elo cuando la tasa de tablas → 0, así que
los bounds se declaran **en Elo crudo** y pueden ser estrechos.

## 2. Longitud de partida — medida

- Media **575 plies** (piloto sin adjudicación), **465 plies** con cap de 1.000.
- Rango observado 380–842 plies.
- **Cap mínimo obligatorio: 1.000 plies.** Un cap de 300 produjo 100 % de tablas
  artificiales en una sonda temprana; el error se detectó comparando con el
  piloto largo.

## 3. Adjudicación

- `win_adj movecount=6 score=5000` — **red de seguridad, no mecanismo
  necesario**: las partidas terminan solas en mate.
- `draw_adj` **desactivada**: con ~0 % de tablas solo añadiría ruido.
- El umbral 5.000 está en la escala interna (ADR-001: pawn = 50, Amazona =
  1.020), es decir ~10 torres de ventaja: inequívocamente ganado.

## 4. Bounds de SPRT

**Fase de brecha grande (actual): `[1.00, 6.00]` con α = β = 0,05**
(→ límites de LLR [−2,94, +2,94]).

Coste de resolución con estos bounds, calculado con la fórmula de Wald
implementada en `tools/sprt.py` (LLR = n·(s₁−s₀)·(x̄ − (s₀+s₁)/2)/σ̂²):

| Efecto real | Partidas hasta cruzar ±2,94 |
|---|---|
| +100 Elo | ~670 |
| +50 Elo | ~1.500 |
| +20 Elo | ~4.300 |
| +5 Elo (neutro) | no cruza; muere por el límite de partidas |

A ~23 s por partida y 20 procesos en paralelo (`tools/parallel_match.py`):
**~13 min por cada 670 partidas**, ~1,6 h para 5.000. Presupuesto viable.

**Paso a `[0.00, 3.00]`** cuando dos SPRT consecutivos pasen con <3.000
partidas (síntoma de que la fruta baja se acabó). Política heredada de Spell.

## 5. Control de tiempo

Equivalencia derivada de la longitud de partida, no copiada de ajedrez: con
~287 jugadas por bando, un STC de ajedrez (8 s + 0,08) daría ~28 ms por jugada.
Para un presupuesto táctico comparable:

| Preset | TC | ms/jugada aprox |
|---|---|---|
| STC | 60,0 + 0,6 | ~200 |
| LTC | 180,0 + 1,8 | ~600 |
| FIXED NODES | N=10000 | (sin reloj; para sondas A/B entre builds propios) |

**LTC de confirmación siempre** antes de mergear: la lección del sign-flip de
Spell (+30 STC / −27 LTC) aplica igual aquí y aún no hemos medido si Terachess
tiene el mismo patrón.

## 6. Qué NO está calibrado todavía

- **Relación nElo↔Elo empírica**: se deduce de la tasa de tablas, no se ha
  ajustado con datos propios.
- **Modelo WDL propio**: `win_rate_params` sigue siendo el de ajedrez y da
  resultados sin sentido (ver ADR-001). La salida `wdl` del motor no debe
  usarse. Ajustarlo requiere miles de partidas con resultado; es trabajo
  pendiente de F4 avanzado.
- **Si STC filtra para LTC** en esta variante: sin datos.
