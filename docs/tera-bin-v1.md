# tera-bin v1 ("TC01") — formato de registro de datos de entrenamiento

**Estado**: NORMATIVO desde su primer uso en datagen. Cualquier cambio de bytes exige
un formato nuevo (tera-bin v2, magic/versión distintos), nunca una mutación silenciosa
de v1 (norma heredada de spell-bin-v1).

## Archivo

```
[cabecera 32 B][registro 144 B] × count
```

**Cabecera** (little-endian, `struct "<4sHHQQQ>"` + padding a 32 B):

| Campo | Tipo | Valor |
|---|---|---|
| magic | 4 B | `"TC01"` |
| version | u16 | 1 |
| record_size | u16 | 144 |
| count | u64 | nº de registros |
| source_count | u64 | posiciones buscadas (incluye filtradas) |
| flags | u64 | reservado, 0 |

Un lector antiguo ante un formato nuevo debe fallar limpio por `magic`/`version`/
`record_size`. `count` valida el tamaño exacto del archivo:
`filesize == 32 + 144*count`.

## Registro (144 B, little-endian)

| Offset | Tamaño | Campo |
|---|---|---|
| 0 | 32 B | `occupancy`: 4×u64, palabra 0 = casillas 0–63 (a1=bit 0, sq = fila*16+col), LSB-first |
| 32 | 96 B | `pieces`: códigos de 6 bits, uno por casilla ocupada en orden ascendente de casilla, bitstream LSB-first; padding final a cero |
| 128 | 16 B | `meta`: bitstream LSB-first, 101 bits + padding a cero (ver abajo) |

**Códigos de pieza (6 bits)**: `1 + type_idx` para blancas, `27 + type_idx` para negras,
con `type_idx` = orden alfabético de la letra FEN-TSF (a=0 Amazon, b=1 Bishop, …,
z=25 Giraffe). 0 = inválido. Códigos 53–63 reservados. Máximo 128 piezas por registro
(los códigos de casillas no ocupadas no se emiten).

**Meta bitstream** (en orden, LSB-first):

| Bits | Campo | Semántica |
|---|---|---|
| 1 | stm | 0=blancas, 1=negras al mover |
| 2 | king_jump | bit0: blanco conserva salto; bit1: negro |
| 9 | ep_plus1 | 0 = sin e.p.; si no, casilla e.p. + 1 (1–256) |
| 7 | rule50 | contador de medio-movimientos (0–100, saturado) |
| 16 | fullmove | nº de jugada |
| 16 | score | i16, cp POV del bando al mover, clamp ±32000, NUNCA scores de mate |
| 32 | move | movimiento elegido: bits 0–7 destino, 8–15 origen, 16–21 pieza promocionada (código 6 bits, 0 = sin promoción), 22–23 tipo (0=normal, 1=e.p., 2=salto de rey, 3=reservado), 24–31 reservado 0 |
| 16 | ply | ply de la partida (desde 0) |
| 2 | result | resultado final POV del bando al mover: 0=derrota, 1=tablas, 2=victoria, 3=sin resultado |

Total 101 bits; el resto de los 16 B a cero. **Padding no-cero = registro corrupto**
(el decoder debe rechazar, no reparar).

## Invariantes de validación (audit_terabin.py)

1. `popcount(occupancy) == nº de códigos de pieza emitidos ≤ 128`.
2. Exactamente un rey blanco (código 11: 'k' idx 10 → 1+10) y uno negro (37).
3. Códigos en rango [1,52]; padding de pieces y meta a cero.
4. ep_plus1 ∈ {0} ∪ [1,256]; rule50 ≤ 100; result ≤ 3.
5. Round-trip doble motor↔python: `to_fen(unpack(bytes)) == FEN-del-motor` y
   `pack(unpack(bytes)) == bytes` (patrón datagen_resume_test de Spell).

## Sidecars

- `<out>.meta.json`: format:"tera-bin", version:1, records, source_positions, games,
  game_results{w,b,d}, seconds, threads, nodes, seed, positions_per_second, workers[];
  además `provenance_schema:terachess-datagen-provenance-v1`, commit/dirty bit,
  SHA-256 y tamaño del productor, red y libro, y `network_arch_hash`.
- `<out>.debug.txt` (con `--debug-sample N`): líneas `FEN | score | result` para el
  round-trip doble.
- `<out>.resume`: metadata operativa versión 2. Congela la misma procedencia y
  rechaza cambios de productor/red/libro/source incluso si el output final ya
  existe. Es un sidecar; **no cambia ningún byte** del contrato `TC01`.

## Procedencia

Cada comando exige y autentica antes de crear shards: commit del motor, SHA-256
del productor, de la red activa y del libro (`NONE`/`NONE` para startpos builtin).
Cada campaña registra además bench, comando completo, semilla base y el receipt del auditor con histogramas
(WDL, material, fase, eval, ply, records/partida). La política de muestreo vive en el
manifest de campaña, no en el formato.
