# Autopsia de net-1: del −545 Elo al +330 Elo

> **DESENLACE (2026-07-19, posterior a todo lo que sigue)**: la hipótesis
> "faltan datos" se confirmó y **net-1 pasó el gate**. Con **1.522.654
> registros** (4× los 370 k de la última iteración fallida):
> **+87 −13 =0, +330,2 ± 51,7 Elo** contra la evaluación material — el triple
> del umbral exigido (+100). Paridad 0 cp sobre 600 posiciones.
> Red promovida a `tera-net1.tnn`, sha256 `6bb5cd48…b71d6d3d`.
>
> **Corrección importante a mi propio razonamiento**, registrada porque la
> equivocación es instructiva: yo supuse que la red debía reproducir el material
> *casi exactamente* para empatar, y extrapolé que hacían falta ~4,5 M (luego
> ~36 M) posiciones para bajar de 1 peón de error. **Falso.** Con 133 cp de
> error mediano (2,7 peones) y correlación 0,937, la red ya gana 330 Elo: el
> conocimiento posicional destilado de la búsqueda **domina** al ruido de
> aproximación mucho antes de lo que suponía. Entre 370 k y 1,52 M hay una
> transición de fase (−545 → +330 Elo), no una rampa suave.
>
> Lo que sigue es el diagnóstico tal y como se hizo, con sus predicciones
> —incluidas las que resultaron erróneas— sin retocar.

---

## (histórico) Por qué la red piloto perdía contra el material

**Veredicto**: gate de fuerza F3 **FAIL** en las dos iteraciones. Causa raíz
identificada y **cuantificada**: el volumen de datos está 12–80× por debajo de
lo que esta arquitectura necesita. No es un bug: la cadena está verificada.

## Lo que SÍ está verificado (descartado como causa)

| Verificación | Resultado |
|---|---|
| Gate de paridad motor↔python | **0 cp** en 1.200 posiciones (netA: 800) |
| Acumulador incremental vs refresh | 6.000 plies, 0 diferencias |
| Gate de unidades label↔eval | pendiente **1,012**, correlación 0,992 |
| Auditor de datos `--strict` | 370.437 registros, 0 avisos |
| Round-trip doble motor↔python | 200/200 byte-exacto |
| Táctica básica con red cargada | captura pieza colgada y encuentra el mate |
| Índices de features | 3 derivados a mano coinciden; 128 activos/perspectiva |

## Las dos iteraciones de diagnóstico

### Iteración 1 — bug real de unidades (ADR-001)

La red imitaba al material con correlación 0,992 pero **a 1/4 de escala**
(ratio 0,242): los labels venían normalizados por el modelo WDL de ajedrez.
Corregido. Resultado tras el arreglo: −544,7 ± 79,4 Elo. **Necesario pero no
suficiente.**

### Iteración 2 — capacidad vs datos, medido

Comparación de la red contra la evaluación material sobre posiciones
**generadas frescas por el oráculo** (jamás vistas) frente a posiciones de
entrenamiento:

| Datos | corr (entrenamiento) | **corr (frescas)** | err mediano frescas | sd red | sd material |
|---|---|---|---|---|---|
| 90 k | 0,754 | 0,368 | 244 cp | 238 | 548 |
| 180 k | 0,882 | 0,579 | 226 cp | 266 | 548 |
| 220 k | 0,934 | 0,692 | 210 cp | 322 | 548 |
| 370 k | 0,979 | **0,796** | **170 cp** | 356 | 548 |

Tres lecturas:

1. **Mejora monótona y fuerte con los datos** — la arquitectura aprende; le
   faltan muestras. La factorización estaba activa en todas.
2. **Brecha entrenamiento↔frescas de 0,18 puntos** a 370 k: sobreajuste medido,
   no supuesto. El transformer tiene ~26,7 M pesos y el dataset 370 k posiciones.
3. **La red se cubre las espaldas**: su dispersión en posiciones nuevas es 356
   frente a 548 del material — regresa hacia la media en lo que no conoce.

## Por qué eso son −545 Elo y no −20

El maestro es **material exacto**. La red no tiene ninguna otra fuente de
conocimiento que superar: para empatar tiene que reproducir el material casi
perfectamente, y solo gana con lo poco posicional que el search de 12 k nodos
haya destilado. Con un **error mediano de 170 cp = 3,4 peones** en posiciones
nuevas, el ruido de la aproximación es órdenes de magnitud mayor que cualquier
señal posicional que pudiera haber aprendido. Sustituir una función exacta por
una aproximación ruidosa solo puede empeorar la búsqueda.

Es la situación inversa a la de un motor con eval hecha a mano rica: allí la red
tiene margen para ganar aunque aproxime con ruido; aquí no.

## Cuántos datos hacen falta (extrapolación de la curva medida)

Ajuste sobre los cuatro puntos: `err_mediano ≈ 839 − 119·log10(N)` cp.

| N | error previsto | en peones | horas de torre a 200 pos/s |
|---|---|---|---|
| 370 k (actual) | 178 cp | 3,6 | 0,5 |
| 1 M | 127 cp | 2,5 | 1,4 |
| 3 M | 70 cp | 1,4 | 4,2 |
| **4,5 M** | **50 cp** | **1,0** | **6,3** |
| 10 M | ~8 cp | 0,2 | 13,9 |

**Umbral práctico estimado: ~4,5 M posiciones para bajar de 1 peón de error;
~10 M para que el ruido deje de dominar.** Coincide con el orden de magnitud
que el propio plan fijó para net-1 (20–30 M, más conservador).

La extrapolación de la correlación satura antes de ser informativa; el error
absoluto es la métrica útil y su tendencia log-lineal es limpia en el rango
medido. La extrapolación asume que la tendencia se mantiene: **es una
predicción falsable**, y el siguiente experimento la comprueba directamente.

## Decisión

Conforme a la regla predeclarada del plan ("si net-1 no supera +30 Elo tras 2
iteraciones de diagnóstico, STOP de entrenamiento y auditoría de la cadena
índices→forward"): la auditoría de la cadena **está hecha y sale limpia**, así
que no hay nada que arreglar en el código. Se detiene el entrenamiento a esta
escala y el proyecto pasa a **campaña de régimen**: 5–10 M posiciones
(6–14 horas de torre, o un workload de OpenBench distribuido, cuyo preset ya
está escrito), reentrenar, y repetir el gate.

**No se relaja el gate.** El umbral de +100 Elo sigue vigente; lo que cambia es
el insumo, no el listón.

## Trabajo derivado (registrado en el programa F5)

- Fusión de buckets de salida 0-1-2 (los buckets 0 y 1 estaban al 0,2 % y 3 %):
  reduce parámetros donde hay menos datos.
- Evaluar una variante de menor capacidad (L1 = 128) para el régimen de pocos
  datos: menos pesos, menos sobreajuste. Requiere `arch_hash` nuevo y volver a
  pasar el gate de paridad.
- Enriquecer el maestro antes de la campaña de régimen: una eval material + PST
  daría a la red algo posicional que aprender y margen real para superar al
  maestro.
