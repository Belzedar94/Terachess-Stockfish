# Resultados de la ejecución del plan (2026-07-18/19)

Ejecución completa de las fases 0 a 4 del plan aprobado, con la fase 5
documentada como programa. Todo lo que sigue está medido; el ledger con las
autopsias está en [AUDIT.md](AUDIT.md).

## Gates conseguidos

| Fase | Gate | Evidencia |
|---|---|---|
| **F0** | Espec + oráculo congelado + perft + round-trip | 87 fixtures / 0 fallos; **30.000 posiciones** diferenciales A↔B / 0 discrepancias; 13.000 round-trips FEN; 12/12 pares de aliasing; 15 posiciones de perft de referencia |
| **F1** | Port 256 correcto | **1.000 posiciones / 37.371.980 nodos hoja**, motor vs oráculo, **0 discrepancias**; perft startpos 54 / 2.916 / 175.508 / 10.562.564; bench determinista ×3 |
| **F2** | Datagen viable + búsqueda auditada | **88,6 pos/s** agregadas (24 hilos, umbral ≥60); round-trip doble 200/200 byte-exacto; determinismo sha256 ×3; resume con kill/relaunch real; auditoría de búsqueda con 1 bug corregido |
| **F3** | Paridad ==0 cp + red > material | **0 cp en 1.200 posiciones** estratificadas; **net-1: +330,2 ± 51,7 Elo** vs material (umbral +100) |
| **F4** | Calibración propia + net-2 | 246 partidas con **0 tablas** (IC95 <1,2 %); bounds [1,6] justificados; net-2 entrenada y con paridad 0 cp; SPRT net-2 vs net-1 ejecutado |
| **F5** | Programa con presupuestos | 7 familias priorizadas con arnés offline obligatorio y condición de salida |

## Lo que se construyó

- **Motor**: Stockfish master portado a 16×16 / 26 tipos de pieza. `Bitboard256`
  de 4×u64, `Square` de 16 bits, `Move` u32, ray-scan en lugar de magics (que no
  generalizan), bent riders y hoppers con pantalla, salto de Rey Metamachy,
  promoción forzada con la excepción del Troll. Historiales con bucketing de
  tipos (32 MiB por instancia en lugar de 2 GiB).
- **Doble oráculo Python** independiente (mailbox y bitboards sobre enteros),
  escritos por agentes distintos desde la especificación, más 87 fixtures
  derivados a mano. Es la autoridad de reglas del proyecto.
- **Datagen embebido** en el binario, conforme al contrato de OpenBench, con
  formato `tera-bin v1` versionado, auditor, y round-trip doble motor↔python.
- **Trainer NNUE** (red S: 8 king-buckets, L1=256, 8 output buckets, ~57 MB) con
  cuantización cuyo margen anti-overflow está demostrado por construcción
  (24.447 < 32.767) y gate de paridad de tolerancia cero contra el motor.
- **Infraestructura de medición**: runner de partidas con el oráculo como
  árbitro de legalidad, versión paralela, SPRT con bounds declarados, y gates
  automáticos de unidades, formato y paridad.

## Las tres cosas que salvaron el proyecto

1. **Auditar antes de medir.** El término `moveCount * 62` de la reducción
   tardía, afinado para ajedrez, convertía las reducciones en **extensiones de
   4,7 plies** con los 150-300 movimientos legales de Terachess. Se detectó
   calculando la fórmula antes de fiarse de ninguna medición.
2. **Un gate de unidades separado del de paridad.** La red imitaba a la
   evaluación material con correlación 0,992 pero **a 1/4 de escala**: el campo
   de Stockfish llamado `InternalUnits` contiene en realidad el cp ya convertido,
   y esa conversión dividía por el modelo WDL **de ajedrez**, que asigna valor
   cero a todas las piezas de Terachess. El gate de paridad no podía verlo
   —verifica que dos implementaciones coincidan, no que la magnitud sea
   correcta—. Ahora existe `check_label_units.py` (ADR-001).
3. **Medir la causa en vez de suponerla.** Con las unidades ya correctas la red
   seguía perdiendo 545 Elo. En lugar de tocar código, entrené cuatro redes con
   90k/180k/220k/370k registros y medí su acuerdo con el material en posiciones
   nunca vistas: 0,368 → 0,796. Era volumen de datos. Con 1,52 M la red pasó a
   **+330 Elo**.

## Errores propios, registrados

- **Extrapolé mal**: predije que hacían falta 4,5-36 M posiciones para que la red
  funcionase, razonando que debía reproducir el material casi exactamente.
  Falso: con 2,7 peones de error mediano ya ganaba 330 Elo. La métrica proxy
  (error de aproximación) no era monótona con el Elo en el rango que importaba.
- **Dos sondas mal construidas** casi produjeron conclusiones falsas: un `quit`
  que abortaba la búsqueda antes de empezar (parecía un motor roto) y un filtro
  que capturaba dos líneas por posición (daba correlación 0,202 donde había
  0,975). Ambas se cazaron por incoherencia con mediciones ya validadas.
- **Un gate que pasaba vacío**: `parity_gate` daba PASS con 0 posiciones cuando
  el volcado de referencia aún no existía. Corregido para fallar cerrado.
- **Un cap de 300 plies** fabricó un 100 % de tablas y estuve a punto de tomarlo
  por una propiedad del juego. Las partidas duran 575 plies de media.

## Descubrimientos sobre Terachess

- **Es radicalmente decisivo**: 0 tablas en 246 partidas (IC95 <1,2 %), todas por
  mate real. Menos incluso que Spell (4 %). Hace el SPRT muy eficiente.
- **Partidas larguísimas**: 575 plies de media, hasta 842. Cualquier cap por
  debajo de 1.000 plies falsea los resultados.
- **Branching 98-300** en mediojuego (54 en la posición inicial), confirmando el
  supuesto del plan.
- **Perft de referencia**: no existía ninguno publicado para la variante. Los 15
  de `oracle/perft_refs.json` son originales y verificados por tres
  implementaciones independientes.

## Qué falta (honesto)

- **Campaña de régimen**: 2,87 M posiciones generadas frente a los 20-30 M del
  plan. La curva medida sugiere que aún hay margen de mejora por datos.
- **Familias de búsqueda sin barrer**: LMP poda entre el 18 % y el 89 % de los
  movimientos quietos con constantes de ajedrez. Hasta barrerla, la fuerza
  absoluta no es representativa (las comparaciones A/B sí lo son).
- **Verificación externa de reglas**: cotejo con Jocly, Ai Ai y el ZRF del autor;
  consulta a Cazaux sobre repetición y regla de 50 (hoy son suposiciones
  documentadas).
- **Modelo WDL propio**: el de ajedrez está neutralizado; la salida `wdl` no debe
  usarse.
- **Fusión de output buckets 0-1-2**: medidos al 0,2 % y 3 % del dataset, por
  debajo del 5 % que exige el contrato. Desviación declarada, pendiente para la
  siguiente arquitectura.
- **OpenBench**: preset y runbook escritos, sin instalar en el servidor.
