# PLAN DE IMPLEMENTACIÓN — Terachess-Stockfish

**Objetivo**: el mejor motor del mundo de **Terachess II** (Jean-Louis Cazaux, 2020; 16×16 = 256 casillas, 26 tipos de pieza, 64 piezas por bando), con datagen embebido y trainer NNUE, cumpliendo los contratos del playbook (`Atomic Project\docs\general\00–11`): gates falsables que fallan cerrados, cadena de confianza motor↔datos↔trainer, procedencia versionada, evidencia durable por fase.

**Método de este plan**: 12 agentes (8 lectores de docs/código/web + 3 diseñadores + 1 red-team adversarial que verificó afirmaciones contra el código real y ejecutó binarios). Los números corregidos por el red-team prevalecen.

**Decisiones del propietario (vinculantes)**:
1. La versión objetivo es **Terachess II (2020)**, la canónica según el autor.
2. **Base A: motor especializado partiendo de Stockfish master**, portado a 256 casillas. El propietario rechazó explícitamente la base generalista (Fairy-Stockfish-VLB): "el overhead de tomar como base un motor generalista es muy peligroso; prefiero que empecemos de uno especializado y que hagamos tantos cambios y mejoras como necesitemos". Este es el **ADR-0** del proyecto. Consecuencia asumida y mitigada: el riesgo dominante pasa a ser la corrección de reglas (movegen a mano de 26 tipos exóticos sin perft de referencia) → se compensa con triple oráculo y presupuesto de verificación reforzado en Fase 0.

---

## 0. Contexto

- `Terachess-Stockfish\` está vacío: proyecto nuevo.
- **Espécimen de base disponible**: `Spell-Stockfish\src` = Stockfish master 2026 oficial + capa spell. Tres migraciones del port ya están hechas y auditadas ahí: **Move de 32 bits** (layout con bits altos libres), **TT de entradas de 12 bytes** (`ClusterSize=5`, cabe un move u32), **ButterflyHistory 2×65536** indexado por `move & 0xFFFF` (válido tal cual con from/to de 8+8 bits). Además: arquitectura datagen run7 completa (rama `nnue-v2:src/datagen.cpp`, 1931 líneas), disciplina de bench/CI/AUDIT y el trainer spellnnue-pytorch con gate de paridad — todo reutilizable como patrón.
- **Trabajo previo del propietario** en `Very Large Boards Project\Fairy-Stockfish-VLB` (rama `verylargeboards`): soporta 256 casillas con `struct Bitboard {uint64_t b64[4]}` y perft 14×14 verificado. **Papel en este plan (tras ADR-0)**: NO es la base; queda como (a) referencia de código para los operadores del Bitboard de 4 palabras (código del propio propietario, GPLv3 compatible), y (b) **tercer oráculo opcional de perft** definiendo `terachess2` en su variants.ini — cubre ~24 de las 26 piezas (su Betza no expresa los bent riders Eagle/Rhinoceros: carece del modificador `a`, verificado en `piece.cpp:55-179`).
- Infraestructura operativa a reutilizar (no rediseñar): OpenBench en `https://belzedar.duckdns.org` con modo DATAGEN distribuido genérico (`openbench-spell\docs\datagen-mode.md`), formato binario versionado con round-trip byte-exacto (`run7.py`/`audit_run7.py`), trainer con paridad ≤0cp (`docs/spell-nnue-v2.md`).
- Lecciones Spell como restricciones: 2 divergencias de reglas contra la referencia viva (presupuestar que pasará); constantes chess-tuned (LMR/moveCount) envenenaron TODAS las mediciones previas; PGO fantasma en Windows; bounds SPRT [1,6] en fase de brecha; "arnés offline antes que granja"; un knob por SPRT; STC no filtra para LTC.

---

## 1. Resumen ejecutivo — decisiones clave

| Decisión | Elección | ADR en |
|---|---|---|
| Versión de reglas | **Terachess II (2020)** | Fase 0 |
| Base de código | **A: SF master especializado, portado a 256** (ADR-0 del propietario). Punto de partida concreto: chasis Spell-Stockfish (SF master + Move32/TT12B ya migrados) con la capa spell extirpada; fallback si la extirpación no es limpia en ≤1 semana: SF master virgen + replicar esas 2 migraciones | Fase 1 |
| Representación | `Bitboard256 = struct {u64 w[4]}`; `Square` u16 (SQ_NONE=256 no cabe en u8); Move u32 `to(8)|from(8)|promo(6)|flags(2)`; sliders **ray-scan como baseline correcto → kindergarten por línea como optimización medida** | Fase 1 |
| Oráculo | **Triple**: enumerador Python desde la espec (primario) + Jocly (~20 piezas compartidas con Terachess I) + FSF-VLB `terachess2` (~24 piezas, opcional); árbitros de intención: Ai Ai y ZRF del autor | Fase 0 |
| Formato de datos | **tera-bin v1 ("TC01")** propio, 144 B/registro, versionado, independiente del motor | Fase 2 |
| Primera red | **Candidata S**: 8 king-buckets, L1=256, 8 output buckets, ~56 MB, FT_SCALE=128 anti-overflow | Fase 3 |
| Trainer | Fork del esqueleto spellnnue-pytorch (spl2/model/parity/run7) — NO nnue-pytorch oficial (data loader C++ con casillas de 6 bits cableadas) | Fase 3 |
| Estadística | Bounds/adjudicación propios calibrados con piloto de 2.000 partidas ANTES del primer SPRT | Fase 4 |

**Esfuerzo estimado** (1 persona; el calendario depende de la dedicación): **hasta net-1 verificada ~19–28 semanas-persona** (F0: 5–7, F1: 6, F2: 5–7, F3: 6–9, con solapes); hasta net-2 confirmada por SPRT: **~26–35**. Coherente con el inventario del chasis (13–21 semanas desde el código, sin fase 0 ni calibración estadística).

---

## 2. La base y sus números (honestos)

### Lo que muere de Stockfish en 256 casillas (inventario verificado sobre el espécimen)
- `types.h`: `Bitboard=u64`, `Square:u8 {…SQUARE_NB=64}`, Move con from/to de 6 bits, `make_square (r<<3)+f`, `s&7`, `s>>3`, `^56`, `NORTH=8`, Piece en 4 bits. Reescritura casi total (~550 líneas) + **grep global obligatorio** de cada `&0x3F`, `<<6`, `^56`, `&7`, `>>3` disperso por el árbol.
- `bitboard/attacks`: magics fancy **no generalizan** — el truco exige una multiplicación de 64 bits que concentre el índice; con 4 limbs no existe, y la ocupación relevante de una torre 16×16 (28 bits) haría tablas imposibles. Los paths AVX512 (`compress_epi8` con el tablero como máscara de 64 lanes) e hyperbola de 64 bits también mueren. Alternativas reales: **ray-scan** (Ray[256][8]×32B = 64 KiB; primer bloqueador por lsb/msb de 4 palabras; ~8 escaneos por deslizador) y **kindergarten por línea** (ocupación de 16 bits por segmentos; tabla 16×2^16×2B = 512 KiB por geometría; re-depósito por tablas de dispersión). LineBB/BetweenBB/RayPassBB pasan de 96 KiB a **6 MiB** (misses constantes en legal()/pinners: coste asumido).
- `position`: Zobrist psq ~120–128 KiB; StateInfo copiado/nodo pasa de ~0,2 a ~1,5–2 KiB (checkSquares de ~30 tipos×32B); cuckoo de 8192 no escala (pares reversibles ≥10^5 con 26 tipos×256) → ampliar a 2^17–2^18 o eliminar `upcoming_repetition` (ADR); FEN/notación multi-dígito de rango.
- `movegen`: reescritura ~80% + catálogo de 26 tipos (tablas de leapers ~256 KiB; ray-scan para sliders; **hoppers con pantalla** Cannon/Vao/Sorceress y **bent riders** Eagle/Rhino compuestos como paso diagonal/ortogonal + rayos). `MAX_MOVES` a 2048–4096 (medir el máximo real de legales).
- `movepick/history`: ContinuationHistory naïve = **2 GiB → inviable**; diseño obligatorio de bucketing de tipos (~16 clases → 100–130 MiB/hilo; precedente operativo: FSF usa `PIECE_SLOTS=8` → 32 MiB/instancia). PawnHistory 256 MiB naïve → reducir base o bucketing. Butterfly 256 KiB ya válido.
- `search`: cambios mecánicos pequeños; el coste real es **re-derivar toda constante chess-tuned** (branching ~150–250 vs ~35; en Spell el término `moveCount*62` llegó a EXTENDER movimientos tardíos y contaminó todas las mediciones).
- `NNUE`: HalfKA pleno = 3,4–3,9 M features → FT de varios GB, **prohibido**; king-buckets obligatorios (Fase 3). Finny cache 1,2 MiB/hilo. `MaxActiveDimensions` ≥128 (hasta 128 piezas activas vs 32).
- Eliminar: syzygy (~1.844 líneas), bench FENs de ajedrez, castling clásico.
- **Clase de bug sistémica a auditar**: centinelas y empaquetados de 8 bits (`SQ_NONE` ya no cabe en u8; `DirtyThreat & 0xff`; `Move::null()`). Mitigación: `Square` u16 + auditoría exhaustiva + fuzz + perft profundo.

### Números de planificación (supuestos declarados)
- **NPS esperado**: ~8–15× menos que SF en 8×8 → **150–300 knps/hilo** con eval material; con red S, −30/−50 % → 80–200 knps. Perft con bulk counting: 1–3 Mnps/hilo.
- **Aritmética del datagen (la restricción que manda)**: a 25–40 k nodos/posición y 24 hilos, **≥60 pos/s agregadas** (≈5 M/día) hace viable una campaña de 30 M en ~6 días; **<40 pos/s** mata el ciclo experimental. El escalado ×24 NO es lineal (SMT + ~130 MiB de historiales/hilo): medir agregado real escribiendo a disco, nunca extrapolar del bench (lección test #66 y corrección del red-team).
- **Memoria por hilo**: historiales ~130–200 MiB + Finny 1,2 MiB; global: 6 MiB líneas + tablas sliders 0,1–2 MiB + TT (256 MB por defecto, entradas de 12 B) + red 56–250 MB.
- **Presupuesto de posiciones**: net-1 con 20–30 M; régimen 100–300 M. Incertidumbre honesta (señalada por el red-team): no hay literatura para este feature space; si hicieran falta ~10^9, incluso esta base queda marginal → la curva loss/Elo vs tamaño de dataset se mide en F4 antes de escalar campañas.

---

## 3. Ground truth de reglas: Terachess II

**Fuentes normativas** (jerarquía de autoridad predeclarada: autor > CVP+Interactive Diagram > Ai Ai > ZRF > Jocly):
1. `http://history.chess.free.fr/terachess.htm` — página del autor (⚠ solo HTTP plano; usar curl).
2. `https://www.chessvariants.com/rules/terachess-ii` — escrita por Cazaux, con Interactive Diagram de H.G. Muller cuyo **Betza es transcribible a la espec** (espejo sin Cloudflare: `http://ftp.chessvariants.com/rules/terachess-ii`).
3. `http://history.chess.free.fr/metamachy.htm` — reglas exactas del salto de Rey/peón/e.p./promoción referenciadas por Terachess II.

**Datos verificados**: 26 tipos, 64 piezas/bando (filas 1–4 y 13–16 llenas), Rey en h2/h15, Amazona en i2/i15. FEN-like inicial: `sjyhxfdoodfxhyjs/cmztuvlkalvutzmc/ernbwigqqgiwbnre/pppppppppppppppp/16/.../16/PPPPPPPPPPPPPPPP/ERNBWIGQQGIWBNRE/CMZTUVLKALVUTZMC/SJYHXFDOODFXHYJS w - - 0 1`. Reglas especiales:
- **Doble paso** de Peón y Príncipe **desde cualquier casilla** (bloqueable, no salto), sin captura.
- **E.p.**: solo el Peón captura, sobre doble paso de Peón o Príncipe, inmediatamente. El Troll ni da ni recibe e.p.
- **Salto inicial del Rey** (sin enroque): a distancia 2 (A/D/N), solo primer movimiento, no en jaque, intermedia no amenazada (en salto de caballo basta UNA de las dos intermedias libre de amenaza) — regla Metamachy.
- **Promoción**: inmediata y obligatoria en la última fila; tabla cerrada: P→Q, Príncipe→Amazon, N/Camel/Giraffe→Buffalo, Elephant/Machine/Centaur→Lion, **Troll→Q solo con paso de peón, nunca con salto de 3**. Nada más promociona.
- Mate/ahogado "identical to standard chess"; el autor NO especifica repetición ni regla de 50 → **FIDE por defecto, documentado como suposición + consulta abierta al autor**.
- Contradicciones ya detectadas (documentar con resolución): fila del Rey (CVP dice "third row" por arrastre de Terachess I; el diagrama del autor pone h2 — **usar fila 2**), Archer≡Crocodile (alias), erratas de Machine/Rhinoceros en el texto del autor (CVP corrige).
- Valores Zillions del autor (Rook=5, Amazon=10.2…) como semilla de la eval material ×100.

**Oráculos** — no existe NINGÚN perft publicado de Terachess (I ni II); los números de referencia serán contribución original:
- **Primario (congelado)**: enumerador Python exhaustivo escrito SOLO desde la espec (mailbox 16×16, sin bitboards, sin compartir código con el motor). ~3.000 líneas con pantallas y salto de rey.
- **Externo**: Jocly `src/games/chessbase/terachess-model.js` (commit pinneado de `github.com/aclap-dev/jocly`, AGPL-3.0, headless node) — Terachess I: cubre las ~20 piezas compartidas y la geometría.
- **Tercero (opcional, barato)**: FSF-VLB con entrada `terachess2` en variants.ini — ~24/26 piezas por Betza; motor UCI scriptable a escala.
- **Árbitros de intención** (desempates puntuales): Ai Ai (única implementación integral de II; investigar modo batch o pedir volcado a Tavener — activo), ZRF `cazauxchess.zip` del autor (verificar contenido en F0), preset Game Courier.
- **Cross-checks por pieza**: ChessV-Metamachy (12×12, CECP) y FSF estándar ≤12×10 para validar cada generador aislado antes del ensamblado 16×16.

---

## FASE 0 — Espec + oráculo congelado + suite perft + round-trip FEN
*(TODO antes de una línea de búsqueda — mandato del playbook y del encargo)*

**Entregables**:
1. **`TERACHESS_SPEC.md` normativo**: setup casilla a casilla, movimiento exacto de las 26 piezas (con la línea Betza del Interactive Diagram como fuente citada), las 4 reglas especiales, terminales, suposiciones FIDE, tabla de contradicciones con resolución, valores de pieza semilla.
2. **Inventario de suficiencia de estado y pares de aliasing** (playbook doc 02) ANTES de diseñar hash/record/features. Candidatos identificados: derecho al salto del Rey ×2 (afecta legalidad futura → Zobrist, FEN y record), casilla e.p. (el capturado puede ser Príncipe → codificar casilla, no tipo), promoción del Troll (propiedad del movimiento, NO estado — demostrar con par mínimo). Gate parcial: ≥5 pares mínimos ejecutados.
3. **Oráculo Python congelado** (tag + hash) con **≥60 fixtures**: ≥2 por pieza + e.p. ×4 + salto de rey ×8 (incluye "intermedia amenazada por Cannon con pantalla") + promociones ×9 (incluye Troll por salto = NO promociona) + jaques descubiertos con Cannon/Vao/Sorceress. Cada regla excepcional tiene fixture con resultado esperado (gate literal del playbook F0).
4. **Runner node para Jocly** (commit pinneado): perft/movelist para el subconjunto compartido. **Opcional recomendado**: entrada `terachess2` en FSF-VLB (sin bent riders) como tercer generador de perft masivo barato.
5. **Suite perft de referencia**: perft(1)–(3) del startpos + 40 posiciones diseñadas (una por familia de regla), generación cruzada Python↔Jocly↔(FSF-VLB); publicar en talkchess para revisión externa.
6. **Round-trip FEN**: gramática FEN-16×16 (filas hasta "16", e.p., flags de salto de rey por bando) con `parse(dump(parse(x)))==parse(x)` sobre 10.000 posiciones aleatorias del oráculo.
7. **Verificaciones externas**: descargar `cazauxchess.zip` y confirmar versión; confirmar API headless de Jocly; contacto con Cazaux (repetición/50, semántica del salto de caballo del Rey) y Tavener (volcado movegen de Ai Ai).
8. **Bootstrap del repo**: fork de Stockfish master en `Terachess-Stockfish\` (vía chasis Spell, ver F1), upstream pinneado (commit + toolchain), `AUDIT.md` (formato ledger Spell: hypothesis → files changed → validation → decision → learnings + columna "espacio explorado/presupuesto usado"), `BENCH_LOG.md`, `docs/` con ADRs (ADR-0 registrado).

**Gate falsable**: `python oracle/run_fixtures.py --all && python oracle/perft_suite.py --depth 3 --compare jocly` → exit 0 con 0 discrepancias sin explicar; cada discrepancia entre oráculos cerrada con causa raíz escrita (nunca "elegir el número conveniente" — lección 1878/1814 de Spell). Round-trip FEN 10.000/10.000.

**Presupuesto de divergencias**: asumir **≥2 divergencias de reglas** (pasó en Spell); 2 semanas de buffer explícito. Protocolo dominó predeclarado: divergencia → entrada AUDIT.md + fixture nuevo + regeneración de refs perft + (si ocurre tras F1) rebase de ramas y rebuild del baseline congelado.

**Criterio de abandono**: si Ai Ai, ZRF y la espec del autor discrepan de forma irreconciliable en ≥3 reglas núcleo y el autor no responde en 4 semanas → congelar interpretación propia versionada ("Terachess-II/TSF-1.0") con ADR. No mata el proyecto; degrada la pretensión de canonicidad.

**Esfuerzo**: 5 semanas + 2 de buffer. **AUDIT.md**: entrada por familia de fixtures, tabla de divergencias, ADRs, hashes del oráculo congelado y del commit Jocly.

---

## FASE 1 — Chasis 256 + movegen + perft masivo + bench determinista
*(~6 semanas)*

**1a. Punto de partida (micro-gate, semana 1)**: extirpar la capa spell del chasis Spell-Stockfish (SF master 2026 + Move32 + TT12B + butterfly 64k ya migrados y auditados). **Gate**: en ≤1 semana el árbol compila sin spell, suite estándar en verde y bench de ajedrez razonable. Si no → SF master virgen + replicar las 2 migraciones (Move u32, TT 12 B; ~150 líneas según inventario). Decisión con ADR.

**1b. Capa de tablero** (el corazón del port):
- `types.h`: `Square` u16 (0..255 + SQ_NONE=256), `Bitboard256 = struct {u64 w[4]}` con operadores completos (adaptar del FSF-VLB del propietario: `types.h:119-296`, incluida resta con acarreo), Move u32 `to(8)|from(8)|promo(6)|flags(2)`, Piece con PieceType de 6 bits (`PIECE_NB=128`, color en bit 6), `(r<<4)+f`, `s&15`, `s>>4`, `NORTH=16`, `flip s^0xF0`, `MAX_MOVES=2048` (revisar tras medir el máximo real de legales).
- **Auditoría sistemática de centinelas**: grep global de `&0x3F`, `<<6`, `^56`, `&7`, `>>3`, `&0xff` sobre casillas, `Move::null()`; tests de identidad del Bitboard256 contra enteros arbitrarios de Python.
- Sliders: **ray-scan primero** (correcto y simple: Ray[256][8], primer bloqueador por lsb/msb de 4 palabras) como oráculo interno estable; **kindergarten por línea después** como optimización con identidad demostrada contra ray-scan + microbenchmark (principio 7 del playbook: primero correcto, luego rápido). Tablas de leapers `PseudoAttacks[tipo][256]` (~256 KiB).
- `position`: Zobrist completo (stm, ep de 9 bits con centinela, derechos de salto de Rey ×2, rule50), cuckoo ampliado a 2^17–2^18 o `upcoming_repetition` eliminado (ADR con medición), FEN/notación multi-dígito, `pos_is_ok` reforzado.

**1c. Movegen completo + reglas especiales**: catálogo de los 26 tipos (leapers por tabla; sliders por ray-scan; compuestos por unión; hoppers Cannon/Vao/Sorceress con pantalla; bent riders Eagle/Rhino como paso inicial + rayos desde la intermedia); salto de Rey Metamachy (legalidad estilo enroque con chequeo de amenaza sobre intermedias); tabla de promoción forzada + excepción del Troll; e.p. asimétrico. Make/unmake con estado incremental.

**1d. Verificación masiva**:
- Suite perft de F0 completa + **10.000 posiciones aleatorias** por random-walk del oráculo con perft(2) C++ vs Python en todas; perft(4) del startpos registrado como referencia (~10^9 nodos con branching ~180).
- **Fuzz diferencial 72 h** con ASan/UBSan: secuencias make/unmake con identidad hash y reconstrucción.
- **Bench determinista**: `bench` sobre 20 posiciones Terachess fijadas (firma = nodos totales) en `BENCH_LOG.md`, toolchain pinneada. Regla PGO de Spell: nunca perfil-build sin check de NPS contra build plano; nunca mezclar .o entre toolchains. Cada commit lleva su firma (disciplina OpenBench `Bench: <nodos>`).

**Gate falsable**: `ctest -R perft_suite` verde; perft masivo **0 discrepancias**; 72 h de fuzz limpio; firma bench idéntica en 5 ejecuciones y entre Windows/Linux; **NPS de perft ≥1 Mnps/hilo** (bulk counting). Umbral de investigación: <300 knps → perfilar antes de escribir búsqueda (presagia datagen inviable); el objetivo tras kindergarten es que los sliders bajen de >50 % del tiempo a <25 %.

**Criterio de abandono/replanteo**: divergencia de perft que no converge a 0 tras 3 ciclos → volver a F0 (espec ambigua). NPS de perft <100 knps tras optimización razonable → replantear la representación (híbrido mailbox para consultas puntuales) ANTES de escribir búsqueda, con microbenchmark comparado en AUDIT.md.

**Esfuerzo**: 6 semanas. **AUDIT.md**: firma bench + NPS por plataforma, refs perft(1)-(4) con hashes, horas/seeds del fuzz, ADR ray-scan→kindergarten con microbenchmark, divergencias (esperadas 1–2 residuales) con protocolo dominó.

---

## FASE 2 — Búsqueda material + calibración depth↔nodos + datagen embebido + OpenBench
*(~5–7 semanas)*

### 2a. Búsqueda con eval material (2–2,5 semanas)
- Eval material (valores Zillions ×100) + PST simples como evaluador placeholder (SF master ya no trae eval clásica: es código nuevo pequeño).
- **Historiales rediseñados** (decisión de diseño, no búsqueda-y-reemplazo): ContinuationHistory con bucketing de ~16 clases de pieza (~100–130 MiB/hilo; precedente FSF `PIECE_SLOTS=8`); PawnHistory reducida o bucketed; butterfly tal cual.
- **Auditoría obligatoria de búsqueda** (playbook doc 10 + lección LMR de Spell): re-derivar `reductions[]` y todo término `moveCount*k` para branching 150–250; NMP restringido hasta demostrar seguridad; **SEE auditado para pantallas** (Cannon/Vao/Sorceress rompen el supuesto de que el atacante llega por su propia línea — auditar TODOS los consumidores de SEE; fallback seguro: excluir hoppers de SEE hasta tener ablation); build de referencia con podas peligrosas desactivadas. Receipt: tabla 10 áreas × Estado/Evidencia/Riesgo/Decisión en AUDIT.md.
- **Gate 2a**: 500 partidas self-play sin crash/time-loss; bench estable; receipt de auditoría completo.

### 2b. Calibración depth↔nodos ANTES de fijar el datagen (0,5 semanas)
Sobre 500 posiciones de partidas material-only: depth media y blunder-rate a 10/20/40/80/160 k nodos (blunder = re-búsqueda a 4× nodos refuta por >150 cp). **Regla predeclarada: nodos de datagen = mínimo N con blunder-rate <15 % y depth media ≥5.** Con branching ~180, esperar depth 5–7 a 40 k nodos (cada ply cuesta ~5× más que en ajedrez) — medir, no extrapolar. La depth NO es proxy de fuerza (lección "hollow depth" de Spell). De aquí sale también la equivalencia TC↔nodos para los STC/LTC de Fase 4 (ADR).

### 2c. tera-bin v1 + datagen embebido + adopción OpenBench (2–3 semanas)
- **Registro tera-bin v1**: cabecera 32 B `magic "TC01" + version + record_size + count + source_count + flags`; registro fijo **144 B**: occupancy 4×u64 (32 B) + 128 piezas × 6 bits (96 B, 52 códigos) + metadata ~16 B (stm:1, saltoRey:2, ep+1:9, rule50:7, fullmove:16, score:i16, move:u32 from/to 8+8, ply:u16, result:2, reserva). Espejo Python `terabin.py` (BitReader/BitWriter LSB-first, validación estricta: padding no-cero ⇒ error, `self_test()`), **doble round-trip**: motor→bytes→python→FEN == FEN-del-motor Y python→bytes byte-idénticos (patrón `datagen_resume_test.py`). Auditor `audit_terabin.py` streaming (cabecera vs tamaño físico, unpack de cada registro, histogramas WDL/material/fase/eval/ply/records-por-partida, `--strict`). Cambio de bytes = formato nuevo (v2), nunca mutación silenciosa.
- **Datagen dentro del binario** (clonar arquitectura run7): opciones `book/out/nodes/count/random_multi_pv(+diff)/random_move_*/write_min_ply/eval_limit/filter_*/threads/seed/--resume/--debug-sample`; 1 Engine por hilo, shard por hilo, seed `splitmix(seed, resumeNumber, threadId)` (streams jamás reutilizados), merge verificado + rename atómico + shards retenidos ante fallo, sidecar `out.meta.json` + `out.debug.txt`, resume idempotente (casa con el reintento de chunks de OpenBench).
- **Adopción OpenBench DATAGEN** (checklist de 3 pasos de datagen-mode.md): comando UCI de una línea con `{SEED}/{COUNT}/{OUT}/{THREADS}[/{BOOK}/{BOOK_SHA256}/{NETWORK}]`, exit 0 + `{OUT}` único; preset en `Engines/Terachess-Stockfish.json`; chunks de 20–40 min según pos/s medido.
- **Libro**: 5.000 líneas de 8–16 plies por self-play MultiPV del motor material (no hay teoría de Terachess; licencia limpia por construcción). SHA-256 registrado.
- **Piloto contractual de pos/s** (correcciones del red-team): medir **pos/s agregadas con los 24 hilos cargados escribiendo a disco**, en dos modos: (a) material-only (economía de la campaña 1) y (b) **con forward NNUE dummy activado** a dims reales (economía de las campañas ≥2 — el peaje de red es −30/−50 %). El bench NO predice el ritmo de escritura (test #66).

**Gate falsable de F2**: `audit_terabin.py --strict` exit 0 sobre piloto de 1 M posiciones; doble round-trip 100.000/100.000; un chunk DATAGEN real completado de punta a punta vía OpenBench (submit → worker → upload → merge local); piloto de pos/s: **≥60 pos/s agregadas material-only** y **≥35 pos/s con dummy-net**. Zona gris 40–60/25–35: bajar nodos/pos o sumar workers, con ADR. **<25 pos/s con red** tras optimización → condición de salida "muerte por física" (abajo).

---

## FASE 3 — Contrato NNUE + trainer + net-1
*(~6–9 semanas; contrato de red y de datos congelados ANTES de entrenar — playbook F4/F5)*

**Por qué no hay atajo**: HalfKA ingenuo = 256 reyes × 51 planos × 256 ≈ 3,4 M features/perspectiva → FT de 1,7–6,5 GiB. **Los king-buckets no son opcionales.**

- **Arquitectura candidata S (primera)**: espejo a↔p por columna del rey (`orient = 15 − file` si file<8) → 128 casillas efectivas; **8 king-buckets** (bandas fila {1–2, 3–4, 5–8, 9–16} × columna {a–d, e–h} post-espejo — el rey rara vez sale de su cuadrante en 16×16); 51 planos (25 tipos no-reales × 2 + reyes) × 256 = stride 13.056; dims/perspectiva = 104.448 (comparable a los 87.630 de Spell v2: dentro de lo demostrado entrenable); L1=256, pairwise+skip como Spell; **8 output buckets** `min(7,(n−1)/16)` con auditoría de que ningún bucket <5 % del dataset (fusionar colas si no); **~56 MB**. Candidatas M (16 buckets, L1=512, ~212 MB) y L (L1=1024, ~416 MB) solo tras S verde en SPRT y con ≥100 M posiciones. FullThreats: descartado en v1.
- **Factorización (train-only, coalescida en export — patrón Spell)**: factor pieza-casilla (13.056 virtuales, rescata tripletas raras), factor tipo (52, material aprendido), factor royal-relative (±7 → 225×51 ≈ 11,5 k; recomendado). El motor jamás ve las features virtuales.
- **Cuantización anti-overflow (donde Spell NO se puede copiar)**: hasta **128 features activas** (vs 32). Con FT_SCALE=256 el acumulador i16 desborda (128×255 = 32.640). **Decisión: FT_SCALE=128, clamp 0..127, `clip_weights_` del FT a ±127/128** → peor caso 128×127 + bias(≤8.191) = 24.447 < 32.767: margen 1,34× **garantizado por construcción**, no por entrenamiento. `MaxActiveDimensions=128`. Shifts re-derivados una vez y congelados en el `quantized_forward` de referencia (potencias de 2, trunc hacia cero — condición del gate ==0). Cota de eventos incrementales: ≤3 deltas por movimiento + refresh si el rey cambia de bucket (raro con geometría S; Finny lo amortigua).
- **Trainer**: fork del esqueleto spellnnue-pytorch (spl2/model/parity/run7) con las dims nuevas y el clip del FT. El nnue-pytorch oficial queda descartado (data loader C++ con casillas de 6 bits y binpack 8×8 cableados).
- **Gate de paridad motor↔python ==0 cp** antes de cualquier SPRT: comandos `eval` (psqt/positional/total/bucket) y `featuresv2` (volcado de índices) contra el espejo python; ≥1.000 posiciones reales no-terminales; estratificación: los 8 output buckets representados y ≥50 posiciones con cada uno de los 26 tipos en tablero. Export aborta ante peso fuera de rango; loader falla cerrado ante mutación estructural (hash-chain propio de la red).
- **Secuencia del playbook F6** (orden estricto): índices vs oráculo del engine → forward cuantizado vs eval nativo → gradientes → serialización/carga byte-exacta → canary de 1 paso → checkpoint/resume auténtico → época real. Splits por linaje de partida (no por registro), holdout intocado.
- **Campaña 1**: 20–30 M registros @ nodos calibrados en 2b, λ=1.0 (lección S4 de Spell: con datos pequeños, destilar el score de búsqueda supera resultados ruidosos).

**Gate falsable de F3**: (1) auditor `--strict` exit 0 sobre campaña 1; (2) paridad ==0 cp en 1.000/1.000; (3) red cargada nativamente con identidad (hash + dims en log UCI) y fallo cerrado con red mutada; (4) **match red vs material puro: ≥+100 Elo (α=β=0.05) a nodos fijos, 2.000 partidas** — la primera red DEBE ganar claramente o hay bug de pipeline; (5) firma bench del binario sin red idéntica a F2 (la integración NNUE no toca el árbol material).

**Criterio de abandono**: si net-1 no supera +30 Elo tras 2 iteraciones de diagnóstico formal (datos → objetivo → índices), STOP de entrenamiento y auditoría de la cadena índices→forward (nunca "más epochs a ciegas").

---

## FASE 4 — Calibración estadística propia + net-2 (direccional)

- **Piloto de 2.000 partidas** self-play con net-1 al STC provisional (de la equivalencia de 2b): medir tasa de tablas (esperada <10 %; Spell: 4 %) y longitud media. De ahí, ANTES del primer SPRT: (a) **adjudicación** `win_adj movecount=4 score=S` con S = percentil 95 del |eval| en posiciones ganadas del piloto (NO copiar el 800 de Spell: la escala cp con Amazon=1020 es otra); draw_adj solo si tablas >20 %; (b) **bounds**: con pocas tablas cada partida informa ~2× (1 nElo ≈ 2 Elo cuando draws→0) — bounds en Elo crudo, fase de brecha **[1,6] α=β=0.05** (política Spell: neutros mueren rápido; datapoints: PASS 2.600–10.300 partidas, FAIL claro <1.500), pasar a [0,3] cuando dos SPRT consecutivos pasen con <3.000 partidas; (c) coste real por SPRT calculado del piloto → presupuesto honesto de tests/semana.
- **LTC de confirmación SIEMPRE** (sign-flip +30 STC/−27 LTC de Spell: "methodology needs both"); nada se mergea con STC solo.
- **Curva datos→fuerza**: antes de escalar a 100 M+, medir loss/Elo de S entrenada con 10/20/30 M (resuelve la incertidumbre del presupuesto de posiciones con evidencia propia).
- **Campaña 2** (50 M+, generada CON net-1 — bootstrap) → net-2. Gate: net-2 > net-1 en SPRT [1,6] STC + confirmación LTC. Abandono: dos campañas consecutivas sin pasar [1,6] → diagnóstico formal por datos/objetivo/capacidad antes de una tercera.
- Plantilla YAML del playbook doc 09 ANTES de cada match (H0/H1/α/β/efecto mínimo/stopping/multiplicidad).

## FASE 5+ — Programa de mejora continua (direccional)

- **Staging de ideas**: toda idea con clasificador/umbral/dosis pasa por **arnés offline antes que granja** (comando debug del motor que vuelca features por movimiento + script Pareto recall/class-size contra ground truth de PV — patrón `staging_pareto.py`); presupuesto de persistencia declarado al abrir familia (toggle=1 SPRT; compleja=6–10) y respetado en ambas direcciones; un knob por SPRT; matrices, no one-shots. Candidatas: ordering por valor de pieza (26 tipos), futility re-escalado, upgrade S→M (ADR + ablation), FullThreats v2, SPSA de constantes de búsqueda re-derivadas.
- Meta-lección Spell para branching gigante: "buscar más pero mejor elegido" venció a "podar la clase especial".
- Mantenimiento del techo: seguir upstream SF para mejoras de búsqueda portables (el chasis diverge, pero las ideas se portan como knobs testeables).

---

## Registro de riesgos

| # | Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|---|
| 1 | **Bugs de reglas en movegen a mano de 26 tipos** (riesgo dominante de la base A, sin perft de referencia externo) | Alta | Crítico | Triple oráculo (Python+Jocly+FSF-VLB), 10 k posiciones de perft cruzado, fuzz 72 h, fixtures por regla; buffer de divergencias |
| 2 | **Centinelas/empaquetados de 8 bits** dispersos (SQ_NONE no cabe en u8) | Alta | Alto | Square u16 desde el día 1; grep global auditado; ASan/UBSan; perft profundo |
| 3 | NPS real ≪ estimado (ray-scan/kindergarten peor de lo previsto; StateInfo ×8; historiales fuera de caché) | Media | Alto | Gate NPS en F1 (≥1 Mnps perft); kindergarten con identidad + microbench; piloto pos/s agregado en F2 con umbral de muerte |
| 4 | ≥2 divergencias de reglas post-F1 (pasó en Spell) | Alta | Alto | Buffer 2 semanas; protocolo dominó predeclarado (fixture→refs→rebase→rebuild baseline) |
| 5 | **Bucketing de historiales** cambia la ordenación y exige retuning sin fishtest | Media | Alto | Diseño explícito en 2a con ablation; SPSA propio en F5; build de referencia sin podas |
| 6 | Constantes chess-tuned envenenan TODA medición (LMR de Spell) | Alta | Alto | Auditoría doc 10 completa en 2a ANTES del primer número |
| 7 | SEE incorrecto con pantallas (Cannon/Vao/Sorceress) | Alta | Medio | Fixtures específicos; auditar consumidores; fallback: excluir hoppers de SEE hasta ablation |
| 8 | **Desalineación silenciosa de índices NNUE engine↔trainer** (la clase de bug más cara: arruina meses de datagen) | Media | Crítico | Gate de paridad ==0 cp con cobertura por tipo y bucket; fixtures de índices; export/loader fallan cerrados |
| 9 | Saturación i16 con 128 features activas | Baja (con diseño) | Crítico | FT_SCALE=128 + clip garantizado por construcción; test sintético de peor caso |
| 10 | Datagen inviable en tiempo (pos/s agregadas, derating SMT/caché) | Media | Alto | Piloto contractual 24 hilos escribiendo, en modo material Y dummy-net; palancas: nodos/pos, workers OpenBench, tamaño de campaña |
| 11 | Presupuesto de posiciones insuficiente (¿100–300 M vs 10^9?) | Media | Alto | Curva datos→fuerza medida en F4 antes de escalar; factorización mitiga colas raras |
| 12 | net-1 no gana | Baja | Crítico | Gate +100 Elo; abandono a 2 iteraciones; cadena índices→forward con tests independientes |
| 13 | Tasa de tablas/escala cp rompe SPRT heredado | Media | Medio | Piloto 2.000 partidas fija adjudicación y bounds; nada se hereda de Spell sin medir |
| 14 | Toolchain/PGO fantasma en Windows (pasó en Spell) | Media | Alto | NPS-check contra BENCH_LOG en todo cambio de build; prohibido PGO sin comparación con build plano |
| 15 | Disco/backup (campañas de 3–10 GB) | Baja | Alto | Regla 3-2-1 para datasets irreemplazables; staging con purga verificada; unidad <5 % libre = bloqueo |
| 16 | Bus factor = 1 persona | Alta | Medio | Definition of Done doc 09: rehearsal de sesión limpia al cierre de cada fase; todo en AUDIT.md/docs versionados |

## Condiciones de salida del proyecto (evidencia que mata o fuerza replanteo)

- **Muerte por reglas**: imposibilidad de congelar una espec estable de Terachess II (divergencias irreconciliables sostenidas + autor sin respuesta) según el criterio de F0 — sin oráculo congelado no hay cadena de confianza y el playbook prohíbe continuar.
- **Muerte por física**: NPS material-only <100 knps/hilo tras F1 optimizada **y** pos/s de datagen <25 agregadas con red en el piloto de F2 → una campaña de 30 M costaría >2 semanas-máquina y cada SPRT días: el ciclo experimental deja de ser falsable en tiempo humano. Opciones restantes, por orden: híbrido de representación (mailbox para consultas puntuales, medido), más hardware/workers, o cierre documentado con evidencia. (El repliegue a base generalista queda descartado por ADR-0; FSF-VLB solo seguiría existiendo como oráculo.)
- **Muerte por señal**: dos generaciones de red consecutivas sin pasar [1,6] STC tras diagnóstico formal completo → la hipótesis "NNUE aprende Terachess con este vector de features" queda falsada; archivar con evidencia (el playbook exige registrar también lo rechazado) y replantear feature set (v2) como proyecto nuevo de contrato de red.
- **Replanteo de representación forzado**: si el fuzz o el perft masivo demuestran supuestos de 64 casillas irrecuperables en un subsistema (p. ej. TT o NNUE con anchos cableados no detectados), migrar ese subsistema es obligatorio antes de cualquier medición nueva — con firma bench nueva y refs regeneradas, nunca "parcheando" sobre mediciones viejas.

## Verificación end-to-end del plan

Cada fase tiene su gate como comando ejecutable (run_fixtures, perft_suite, fuzz, bench-signature, audit_terabin --strict, parity ==0, SPRT con bounds predeclarados). La verificación de sistema es el **criterio de éxito del playbook doc 00**: al cierre de F4, una sesión limpia debe poder — leyendo solo la documentación — (1) compilar motor y oráculo, (2) regenerar o autenticar un chunk de datos, (3) reproducir un entrenamiento canary, (4) cargar la red y verificar su hash en el log UCI, (5) correr la batería perft/paridad/bench, (6) decidir con el ledger si un candidato se publica. Ese rehearsal se ejecuta al cierre de cada fase (mitigación del riesgo 16).

## Referencias operativas (rutas exactas)

- Playbook: `Atomic Project\docs\general\00–11`.
- Ledger y lecciones: `Spell-Stockfish\AUDIT.md`, `docs\retroactive-review.md`, `docs\spell-staging-program.md`, `tools\staging_pareto.py`.
- Chasis espécimen (SF master 2026 + Move32/TT12B): `Spell-Stockfish\src\` (types.h, bitboard.h/attacks.*, position.*, movegen.*, history.h/movepick.*, tt.*, search.*, nnue/).
- Datagen/OpenBench: `openbench-spell\docs\datagen-mode.md` (contrato + checklist), `docs\operations.md`; código de referencia: `Spell-Stockfish` rama `nnue-v2:src/datagen.cpp` (1931 líneas; el datagen.cpp del working tree solo tiene pack_sfen v1), `nnue-v2:tools/spellnnue-pytorch/audit_run7.py`, `nnue-v2:tests/datagen_resume_test.py`, `tools/spellnnue-pytorch/run7.py`.
- NNUE de referencia: `Spell-Stockfish\docs\spell-nnue-v2.md`, `docs\nnue-training-guide.md`; trainer a forquear: `tools\spellnnue-pytorch\`.
- Bitboard256 de referencia (código del propietario): `Very Large Boards Project\Fairy-Stockfish-VLB\src\types.h:119-296`; FSF-VLB como tercer oráculo opcional (⚠ fix de cuckoo sin committear en el engine; Betza sin bent riders).
- Reglas: `http://history.chess.free.fr/terachess.htm` (HTTP plano), `http://ftp.chessvariants.com/rules/terachess-ii` (espejo sin Cloudflare), `http://history.chess.free.fr/metamachy.htm`; oráculo externo: `github.com/aclap-dev/jocly` → `src/games/chessbase/terachess-model.js`.
