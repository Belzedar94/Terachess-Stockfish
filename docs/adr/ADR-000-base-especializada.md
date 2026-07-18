# ADR-000 — Base de código: Stockfish master especializado, portado a 256 casillas

**Fecha**: 2026-07-18 · **Estado**: aceptada (decisión del propietario) · **Sustituye a**: — · **Sustituida por**: —

## Contexto

Terachess II se juega en 16×16 = 256 casillas con 26 tipos de pieza. El chasis
de Stockfish asume Bitboard=uint64, Square de 6 bits, Move de 16 bits y magics
de 64 casillas: todo requiere reescritura. Se evaluaron 4 opciones con números
(informe completo en [PLAN.md](../../PLAN.md) §2, verificado por red-team):

| | A: SF+256bb | B: SF mailbox | C: FSF-VLB ext. | D: C-oráculo + A |
|---|---|---|---|---|
| NPS/hilo estimado (eval material) | 150–300 k | 80–200 k | 80–180 k | 150–300 k |
| Semanas-persona a net-1 (sin F0) | 13–21 | 15–24 | 11–18 | 15–23 |
| Riesgo de bugs de reglas | Alto | Alto | Bajo-medio | Medio |
| Techo de Elo | Máximo | A−50 | A −200/−350 eq. | Máximo |

B queda dominada por A en todos los ejes (descartada sin spike). El análisis
técnico recomendaba C por tiempo-hasta-primera-red y riesgo de reglas.

## Decisión

**Opción A.** Razón del propietario (textual): "El overhead de tomar como base
un motor generalista es muy peligroso. Prefiero que empecemos de uno
especializado y que hagamos tantos cambios y mejoras como necesitemos."

Punto de partida concreto: el chasis Spell-Stockfish (`Spell-Stockfish\src\` =
SF master 2026 oficial) porque tres migraciones del port ya están hechas y
auditadas ahí: Move de 32 bits, TT de entradas de 12 bytes (ClusterSize=5),
ButterflyHistory 2×65536. Se extirpa la capa spell; micro-gate F1a: si no queda
limpio en ≤1 semana, SF master virgen + replicar esas 2 migraciones.

## Consecuencias

1. **Riesgo dominante = corrección de reglas** (movegen a mano de 26 tipos, sin
   perft publicado en el mundo). Mitigación: triple oráculo (enumerador Python
   desde la espec, primario; Jocly para ~20 piezas compartidas con Terachess I;
   FSF-VLB `terachess2` opcional para ~24 piezas), 10 k posiciones de perft
   cruzado, fuzz 72 h, ≥60 fixtures, buffer de 2 semanas para divergencias.
2. **Representación**: `Bitboard256 = struct {u64 w[4]}` (referencia: el propio
   FSF-VLB del propietario, `types.h:119-296`), `Square` u16 (SQ_NONE=256 no
   cabe en u8), Move u32 `to(8)|from(8)|promo(6)|flags(2)`. Sliders: ray-scan
   como baseline correcto → kindergarten por línea como optimización medida
   (los magics de 64 bits no generalizan a 4 limbs).
3. **Historiales**: bucketing de tipos obligatorio (ContinuationHistory naïve
   = 2 GiB); diseño en F2a.
4. **Trainer**: fork del esqueleto spellnnue-pytorch (el nnue-pytorch oficial
   tiene casillas de 6 bits cableadas en el data loader C++).
5. **FSF-VLB**: nunca base; solo referencia de código y oráculo auxiliar. El
   repliegue a base generalista queda descartado; si la física falla (ver
   condiciones de salida en PLAN.md), las opciones son híbrido de
   representación, más hardware, o cierre documentado.
