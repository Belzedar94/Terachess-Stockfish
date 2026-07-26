# NNUE Terachess — red S (contrato normativo v1)

**Estado**: CONGELADO antes de generar datos ni entrenar (playbook F4: "el
contrato de red se congela antes que el trainer"). Cualquier cambio de
indexación, dims o cuantización = arquitectura NUEVA con `arch_hash` distinto;
el loader del motor debe **fallar cerrado** ante cualquier mutación.

Motivación del tamaño: HalfKA ingenuo en 16×16 = 256 king-sq × 51 planos × 256
= 3.342.336 features/perspectiva → FT de 1,7 GiB con L1=256. Inviable. Los
king-buckets no son opcionales.

## 1. Perspectivas y orientación

Dos perspectivas: índice 0 = bando al mover (stm), índice 1 = el otro.

Para la perspectiva de color `c`:
1. **Vista** (flip de fila para negras): `vsq = (c == WHITE) ? sq : sq ^ 0xF0`.
   Así el bando de la perspectiva "avanza hacia arriba" siempre.
2. **Espejo horizontal** condicionado por el rey de esa perspectiva:
   sea `vksq` la vista de la casilla del propio rey. Si `file(vksq) < 8`
   entonces se aplica `x ^ 0x0F` (f' = 15 − f) a **todas** las casillas de esa
   perspectiva, incluido `vksq`. Tras el espejo, `file(vksq) ∈ [8, 15]`.

El espejo es legítimo: el setup inicial es simétrico salvo el par Rey/Amazona
(h2/i2), exactamente igual que Rey/Dama en ajedrez, donde HalfKAv2_hm hace lo
mismo.

## 2. King buckets (8)

Con `r = vksq >> 4` y `f = vksq & 15` (post-espejo, `f ∈ [8,15]`):

```
band_r = r < 2 ? 0 : r < 4 ? 1 : r < 8 ? 2 : 3     // 1-2 / 3-4 / 5-8 / 9-16
band_f = f < 12 ? 0 : 1                            // i-l / m-p  (post-espejo)
bucket = band_r * 2 + band_f                       // 0..7
```

Racional: en 16×16 el rey casi nunca abandona su cuadrante (arranca en h2, el
salto inicial lo mueve ≤2 casillas, cruzar el tablero cuesta ~14 tempos), así
que la resolución es fina cerca del origen y gruesa lejos.

## 3. Planos e índice de feature

51 planos:
- `0..24`: piezas NO-rey del bando de la perspectiva, en el orden de tipo del
  motor (`docs/port-256-design.md`): PAWN, KNIGHT, BISHOP, ROOK, QUEEN, CAMEL,
  GIRAFFE, ELEPHANT, MACHINE, PRINCE, TROLL, ARCHER, CANNON, CENTAUR,
  MISSIONARY, ADMIRAL, CARDINAL, MARSHALL, BUFFALO, DUCHESS, LION, RHINO,
  SORCERESS, EAGLE, AMAZON → índice de plano = `type - PAWN` (0..24).
- `25..49`: las mismas 25 clases del bando contrario → `25 + (type - PAWN)`.
- `50`: ambos reyes (propio y rival comparten plano; los distingue la casilla).

```
feature(persp, pc, sq) = KING_BUCKET[vksq] * 13056
                       + plane(persp, pc) * 256
                       + vsq_mirrored
```
`13056 = 51 × 256`. **Dims por perspectiva = 8 × 13056 = 104.448.**
Comparación: Spell v2 usaba 87.630 → estamos en el mismo régimen ya demostrado
entrenable.

Piezas activas por perspectiva: hasta **128** (`MaxActiveDimensions = 128`).

## 4. Arquitectura

```
entrada dispersa (104448)  --FT-->  acc[256] i16   (por perspectiva)
[acc_stm | acc_nstm] (512) --clipped_relu 0..127--> pairwise mul >>7 --> 256
   --fc0--> 16 --relu--> fc1 --> 32 --relu--> fc2 --> 1        (por output bucket)
+ PSQT[bucket] (i32, camino directo desde el FT)
```

**Aclaración normativa (2026-07-19, resuelve una contradicción de la v1)**: el
pairwise **reduce 512 → 256** (cada mitad del acumulador concatenado se
multiplica con la otra), luego `FC0_IN = 256`. La tabla de §7 decía
`fc0 i8[512×16]`, que era incompatible; **manda este párrafo**: `fc0` es
`i8[256×16]`. Detectado por la verificación cruzada trainer↔contrato antes de
que ninguna red se entrenara.

**Combinación final y escala** (tampoco estaban definidas en la v1; convención
Stockfish, obligatoria para ambos lados):
```
psqt      = (psqt_stm[bucket] - psqt_nstm[bucket]) / 2      // trunc hacia cero
eval_cp   = (psqt + positional[bucket]) / FV_SCALE          // trunc hacia cero
FV_SCALE  = 16
```
- `L1 = 256` por perspectiva.
- **8 output buckets** por material: `bucket = min(7, (pieceCount - 1) / 16)`
  (128 piezas iniciales → bucket 7; finales → 0-1). Cada bucket tiene su propio
  stack fc0/fc1/fc2 y su columna PSQT. Auditoría obligatoria del dataset: si un
  bucket queda por debajo del 5 % de los registros, se fusiona ANTES de
  entrenar (`min(5, …)`) y se documenta.

**Medición real (campaña 1, muestra de 16.607 registros, 2026-07-19)**:

| bucket | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| % del dataset | 0,2 | 3,0 | 8,1 | 12,7 | 14,6 | 15,7 | 16,3 | 29,4 |

Los buckets 0 y 1 (finales de ≤32 piezas) están **por debajo del 5 %** que
exige la regla. Con 2 M registros el bucket 0 recibiría ~4.200 muestras para un
stack de ~4.640 pesos: infra-entrenado.

**DESVIACIÓN DECLARADA (net-1)**: se mantiene la arquitectura de 8 buckets para
la red piloto en lugar de fusionar. Motivo: el bucket lo calcula el motor de
forma independiente, así que una remapeo solo en el trainer rompería el gate de
paridad, y cambiar la arquitectura con dos implementaciones ya escritas contra
el contrato congelado destruiría su coherencia. El objetivo de net-1 es cerrar
la cadena, no maximizar fuerza. **Programado para net-2**: fusionar 0-1-2 en uno
(`bucket = max(2, min(7, (n-1)/16)) - 2`, 6 buckets, todos ≥8 %), con
`arch_hash` nuevo y re-verificación de paridad. No es una relajación del umbral
tras ver un resultado: el umbral sigue vigente y se aplicará en la siguiente
arquitectura; lo que se difiere es el rediseño, con la deuda anotada.
- `eval_cp = (psqt[bucket] + positional[bucket]) / FV_SCALE`.

## 5. Cuantización — la garantía anti-overflow

Aquí es donde Spell NO se puede copiar: allí hay ≤32 features activas, aquí
hasta 128 (margen 4× menor).

- `FT_SCALE = 128` (no 256). Activación del FT: `clamp(acc, 0, 127)` (7 bits).
- **`clip_weights_` del FT a ±127/128 durante el entrenamiento** (tras cada
  paso del optimizador), y bias del FT a ±8191.
- Peor caso demostrable: `128 × 127 + 8191 = 24.447 < 32.767`. **Margen 1,34×
  garantizado por construcción**, no por suerte del entrenamiento.
- Pairwise: `(l · r) >> 7` con `l, r ≤ 127` → producto ≤ 16.129, salida ≤ 126
  (cabe en u8).
- Todas las escalas de las capas siguientes son potencias de 2 y toda división
  es truncamiento hacia cero (condición necesaria del gate de paridad ==0).
- El export **aborta** si algún peso sale de rango tras coalescer los factores;
  jamás envuelve ni satura silenciosamente.

## 6. Factorización (train-only, coalescida en export)

Features virtuales que existen SOLO en PyTorch y se suman a las filas reales al
exportar (patrón Spell `freeze_factor_weight` → coalesce):

| Factor | Dims | Qué rescata |
|---|---|---|
| A — pieza-casilla | 13.056 (51×256), compartido por los 8 buckets | filas reales con pocas muestras heredan el conocimiento de las ~8× del factor |
| Tipo | 51 | valor material aprendido; estabiliza el arranque |
| Royal-relative | 11.475 (15×15 offsets × 51) | tropismo/escudo del rey, transferible entre buckets; offset = clamp(Δfile, ±7), clamp(Δrank, ±7) respecto al rey propio |

El motor **nunca** ve features virtuales: tras la coalescencia, el fichero de
red contiene solo las 104.448 filas reales.

## 7. Formato de red TNN1

```
magic      4 B   "TNN1"
version    u16   1
arch_hash  32 B  sha256 del descriptor canónico (abajo)
dims       u32×4 kingBuckets=8, planes=51, L1=256, outBuckets=8
ft_weights i16[104448 × 256]
ft_bias    i16[256]
psqt       i32[104448 × 8]
stacks     8 × (fc0 i8[256×16] + b i32[16] + fc1 i8[16×32] + b i32[32] + fc2 i8[32×1] + b i32[1])
```
Descriptor canónico (texto exacto que se hashea):
`terachess-nnue-S;kb=8;planes=51;sq=256;L1=256;ob=8;ftscale=128;act=clip0_127;pairwise=shr7;stack=16-32-1`

El loader del motor compara magic, version, arch_hash y dims: **cualquier
diferencia = rechazo**, sin intentar adaptación.

## 8. Gate de paridad motor↔python (==0 cp)

Antes de CUALQUIER SPRT con una red:
- El motor expone `eval` (psqt / positional / total / bucket) y `features`
  (volcado de los índices activos por perspectiva).
- `quantized_forward.py` (enteros puros, sin torch) es la **autoridad**.
- ≥1.000 posiciones reales no terminales, estratificadas: los 8 output buckets
  representados y **≥50 posiciones con cada uno de los 26 tipos** en tablero
  (los tipos raros en finales — Troll, Duchess — son el análogo de las "zonas
  vivas" de Spell).
- Tolerancia **exactamente 0 cp** en total, psqt y positional. Un solo cp de
  diferencia bloquea el pipeline.

## 9. Presupuestos

| Concepto | Valor |
|---|---|
| FT | 8 × 13.056 × 256 × 2 B = **51,0 MB** |
| PSQT | 104.448 × 8 × 4 B = 3,3 MB |
| Stacks | < 1 MB |
| **Total red S** | **~55 MB** |
| Refresh del acumulador | 128 filas × 256 i16 = 64 KB → ~1–2 µs (AVX2) |
| Update incremental | ≤3 deltas por movimiento; refresh solo si el rey cambia de bucket |
| Dataset mínimo net-1 | 20–30 M registros (≈37 k visitas/fila con 30 M) |
| Régimen | 100–300 M |

Candidatas futuras (solo tras S verde en SPRT): **M** = 16 buckets, L1=512
(~212 MB); **L** = 16 buckets, L1=1024 (~416 MB). FullThreats queda descartado
en v1 (su enumeración explota en 16×16).
