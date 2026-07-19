# Diseño del port a 256 casillas (F1b–F1d) — contrato para la cirugía

**Fuente de reglas**: `TERACHESS_SPEC.md` (única autoridad). **Base**: `src/` (chasis
SF master limpio, gate F1a en verde: perft ajedrez exacto). Este documento congela
las decisiones de representación para que todas las etapas del port sean coherentes.

## Tipos núcleo (types.h)

- **Square**: valores 0–255 + `SQ_NONE = 256` → el tipo subyacente es de 16 bits
  (`enum Square : int` está bien; PROHIBIDO almacenar Square en u8 en ninguna
  estructura — auditar todo empaquetado).
  `sq = (rank << 4) | file`; `file_of(s) = s & 15`; `rank_of(s) = s >> 4`;
  `make_square(f, r) = (r << 4) | f`. Files `FILE_A..FILE_P` (0–15),
  ranks `RANK_1..RANK_16` (0–15). `flip_rank(s) = s ^ 0xF0`; `flip_file(s) = s ^ 0x0F`.
  `relative_square(c, s) = c == WHITE ? s : flip_rank(s)`.
- **Direction**: `NORTH = 16`, `SOUTH = -16`, `EAST = 1`, `WEST = -1`, diagonales
  ±15/±17. Saltos de caballo: ±14, ±18, ±31, ±33.
- **Bitboard**: `struct Bitboard { uint64_t w[4]; }` (w[0] = casillas 0–63).
  Operadores completos: `& | ^ ~ << >> == != bool -(unaria y binaria con acarreo)`,
  `operator&(Square)`, etc. Patrón de referencia (GPLv3, código del propietario):
  `Very Large Boards Project\Fairy-Stockfish-VLB\src\types.h:119-296`.
  `popcount` = 4×POPCNT; `lsb/msb` en cascada por palabra; `pop_lsb` idem.
  NADA de `1ULL << s` directo: usar `square_bb(s)`.
- **PieceType** (`enum : int`, 5 bits de almacenamiento): orden optimizado para el
  motor: `NO_PIECE_TYPE=0, PAWN=1, KNIGHT, BISHOP, ROOK, QUEEN, CAMEL, GIRAFFE,
  ELEPHANT, MACHINE, PRINCE, TROLL, ARCHER, CANNON, CENTAUR, MISSIONARY, ADMIRAL,
  CARDINAL, MARSHALL, BUFFALO, DUCHESS, LION, RHINO, SORCERESS, EAGLE, AMAZON,
  KING=26`, `PIECE_TYPE_NB=27`. Tabla `PieceToChar` / `CharToPiece` con las letras
  FEN-TSF de la espec §3.1 (mayúscula blanca / minúscula negra). La conversión a
  códigos tera-bin (orden alfabético) vive SOLO en el datagen (F2), no aquí.
- **Piece**: `piece = (color << 5) | type`; `PIECE_NB = 64`;
  `type_of(pc) = pc & 31`; `color_of(pc) = pc >> 5`; `~pc = pc ^ 32`.
- **Move**: `uint32_t`. Layout: bits 0–7 `to`, 8–15 `from`, 16–20 `promo`
  (PieceType resultante, 0 = sin promoción), 21–22 tipo especial
  (0 = normal, 1 = en-passant, 2 = salto de rey), resto 0.
  `Move::none() = 0`; `Move::null() = (1<<8)|2` (from=b1,to=c1 con from≠to pero
  imposible como movimiento legal codificado; documentar).
  La promoción es forzada y determinista (§6.4) pero el Move la lleva explícita.
- **Value**: valores de pieza = Zillions ×100 (§8): P=50, Z=170, M=180, E=200,
  N=200, W=220, I=230, T=240, V=330, B=340, J=410, Y=440, C=500, R=500, X=530,
  F=540, D=580, L=600, S=600, U=610, H=690, O=820, Q=830, G=840, A=1020.
  (letras de la espec; en código usar los nombres de PieceType). KING=0.
  `VALUE_MATE` etc. sin cambios.
- `MAX_MOVES = 2048` (a revisar con medición en F1d).
- **CastlingRights**: ELIMINADO. Sustituto: `KingJumpRights` 2 bits (WHITE_JUMP=1,
  BLACK_JUMP=2) en StateInfo.

## Ataques (bitboard.h/.cpp — reescritura)

- Tablas: `LineBB[256][256]`, `BetweenBB[256][256]` (32 B × 65536 = 2 MiB cada
  una); `Ray[256][8]` (8 direcciones, 64 KiB); `PseudoAttacks[PIECE_TYPE_NB][256]`
  para leapers y máscaras de slider vacío.
- **Sliders por ray-scan** (baseline correcto; kindergarten después con identidad
  demostrada): a lo largo de `Ray[s][d]`, primer bloqueador = `lsb`/`msb` de
  `occ & Ray[s][d]` (según signo de d); `attacks = Ray[s][d] ^ Ray[blocker][d]`.
- **Leapers por tabla**: N, Camel(1,3), Giraffe(2,3), Buffalo(N∪C∪Z), rey/K-step,
  F, W, A(2,2), D(2,0), G(3,3), H(0,3), Lion (anillo Chebyshev ≤2), Duchess
  (K∪A∪D∪G∪H), Machine (W∪D), Elephant (F∪A), Centaur (K∪N), Troll-jumps (G∪H).
- **Compuestos**: Q=R|B, Amazon=Q|N, Marshall=R|N, Cardinal=B|N, Admiral=R|F-step,
  Missionary=B|W-step.
- **Bent riders** (§4.1): `eagle_attacks(s, occ)` = por cada diagonal d: si
  `s+d` fuera → nada; si ocupada → `s+d` (el llamador decide si es captura);
  si vacía → `s+d` ∪ ray-scan ortogonal desde `s+d` en las 2 componentes de d.
  `rhino_attacks` simétrico. Estas funciones devuelven el conjunto de DESTINOS
  alcanzables con semántica mover-o-capturar estándar.
- **Pantallas (§4.2)**: dos funciones por geometría de línea:
  `hopper_quiet(s, occ, dirs)` = deslizamiento normal SIN capturas (se detiene
  antes del primer bloqueador) y `hopper_captures(s, occ, dirs)` = por dirección:
  primer bloqueador B1 (cualquier color), segundo bloqueador B2; si B2 existe →
  destino candidato B2 (el llamador comprueba que sea enemigo). Cannon usa dirs
  ortogonales, Archer diagonales, Sorceress las 8.

## Position (position.h/.cpp)

- `board[256]`, `byTypeBB[PIECE_TYPE_NB]`, `byColorBB[2]`, listas/conteos por pieza.
- **StateInfo**: `key`, `epSquare` (Square o SQ_NONE), `kingJumpRights` (2 bits),
  `rule50`, `pliesFromNull`, `capturedPiece`, `repetition`. SIN
  checkSquares/blockers precalculados en F1 (ver legalidad).
- **Zobrist**: `psq[64][256]`, `ep[257]` (¡por CASILLA, no por columna! el doble
  paso ocurre desde cualquier fila), `kingJump[4]`, `side`. El derecho de salto y
  la casilla ep ENTRAN en la clave (aliasing §5).
- **Legalidad (F1, corrección ante todo)**: generación pseudo-legal +
  `legal(m)` = hacer el movimiento (do_move ligero o copia) y comprobar
  `!attackers_to(king_sq(us), them)`. SIN máquina de pins en F1 (las clavadas con
  pantallas la complican; optimización para F2+ con identidad de bench).
- **`attackers_to(sq, occ, byColor)`**: leapers/sliders/steps por simetría
  (patrón desde sq) + pantallas por simetría (hopper_captures desde sq también es
  simétrico: B2 enemigo con exactamente B1 entre medias ⟺ el hopper en B2 ataca
  sq… ¡OJO: verificarlo! si no, iterar los hoppers del rival) + bent riders SIN
  simetría: iterar los Eagles/Rhinos rivales (≤2 por bando) y comprobar
  `sq ∈ eagle_attacks(pieza)`. La captura tipo peón del Troll y del Peón atacan
  en diagonal-adelante; el Príncipe ataca como K; el doble paso y los `m`-moves
  NO atacan (§5).
- **do_move / undo_move**: incremental estándar + promoción forzada (§6.4, con
  la condición del Troll por TIPO de movimiento), captura ep (retira la pieza de
  la casilla saltada — puede ser Príncipe), salto de rey (mueve el rey, apaga el
  derecho), apagar derecho al mover el rey, fijar/limpiar epSquare en dobles pasos.
- **FEN**: §3.2 completo (16 filas, números 1–16, `Kk`/`-`, ep algebraico).
  Emisor canónico: ep solo si hay capturador; parser: acepta ambas.
- **Cuckoo/upcoming_repetition**: DESACTIVADO en F1 (return false) con comentario
  ADR — se re-evalúa en F2 con tabla ampliada.
- `gives_check(m)`: en F1, versión directa (tras do, ¿attackers_to(king rival)?)
  — puede ser lenta; optimizar en F2.

## Movegen (movegen.cpp — reescritura)

- Por pieza: tabla de leapers → `PseudoAttacks[pt][s] & ~ours` (capturas y quiets
  juntos); sliders → ray-scan; bent riders → función propia; hoppers → quiet +
  captures por separado; Peón (1/2 adelante sin captura §6.1, diagonal captura,
  ep §6.2, promoción §6.4); Príncipe (K-step + doble adelante sin captura, genera
  epSquare); Troll (jumps mover-o-capturar + fmW + fcF, promoción SOLO en
  fmW/fcF); Rey (K-step + salto inicial §6.3: 11–16 destinos, VACÍOS, con el
  chequeo de amenaza de intermedias — amenaza = attackers_to del rival sobre la
  intermedia, ocupación irrelevante; en saltos de caballo basta UNA intermedia
  sin amenaza; prohibido en jaque).
- GenType: CAPTURES / QUIETS / EVASIONS / NON_EVASIONS / LEGAL como en master
  (EVASIONS puede generar NON_EVASIONS filtrado en F1 — corrección primero).
- `MoveList` con MAX_MOVES nuevo.

## Search/eval mínimos para compilar (F1; el retuning REAL es F2)

- `evaluate()` = material (valores §8) + tempo 20cp — ya existe el fallback F1a;
  ampliarlo a los 26 tipos.
- movepick/history: butterfly ya vale (`move & 0xFFFF`); CapturePieceToHistory →
  `[64][256][32]` (1 MiB, ok); ContinuationHistory → **bucket de tipo a 8 slots**
  (hash `type % 8` documentado, patrón FSF PIECE_SLOTS): `[2×8][256]` ×
  `[2×8][256]` = 32 MiB/instancia; PawnHistory: tamaño 512 o desactivada (ADR).
- Constantes de search: NO retunear ahora; solo evitar crashes (índices).
- NNUE: FUERA del build (quitar de SRCS; evaluate() material). F3 lo reintroduce.
- Syzygy: eliminar. `bench`: startpos + posiciones fijas provisionales (se
  regeneran en F1d desde el oráculo).

## UCI

- Casillas multi-dígito (`a1`–`p16`) en parser y emisor; promoción con sufijo
  de letra minúscula SIEMPRE (§11); `position startpos` = START_FEN de la espec;
  `go perft N` con bulk counting en hoja; `d` dibuja 16×16.
- `UCI_Chess960` y opciones sin sentido: eliminar.

## Gates de etapa (cada etapa compila antes de pasar a la siguiente)

1. **board layer**: types/bitboard compilan + selftest de operadores Bitboard256
   (shifts/carry vs enteros Python de referencia impresos en un test unitario).
2. **position layer**: FEN round-trip de START_FEN y 5 FENs de la espec; `d` correcto.
3. **movegen**: `perft 1` startpos = **54** (conteo a mano del arquitecto:
   32 peones + 4 caballos + 4 camellos + 4 jirafas + 2 máquinas + 2 elefantes +
   6 trolls — verificar independientemente); `go perft 2/3` estables sin crash.
4. **integración**: perft(1–3) == oráculo Python en startpos + fixtures; bench corre.
