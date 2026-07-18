# TERACHESS_SPEC.md — Especificación normativa de Terachess II (TSF)

**Versión de la espec**: 1.0 (2026-07-18) · **Variante**: Terachess II (Jean-Louis Cazaux, 2020)
**Fuentes** (jerarquía de autoridad: autor > CVP+Interactive Diagram > Ai Ai > ZRF > Jocly):
- A1: `http://history.chess.free.fr/terachess.htm` (página del autor, pie 18/07/2024; solo HTTP plano)
- A2: `https://www.chessvariants.com/rules/terachess-ii` (escrita por Cazaux; Interactive Diagram de H.G. Muller con Betza; espejo `http://ftp.chessvariants.com/rules/terachess-ii`)
- A3: `http://history.chess.free.fr/metamachy.htm` (reglas del salto de Rey, peón, e.p., promoción referenciadas por A1/A2)

Toda regla lleva su fuente. Las suposiciones de ingeniería (no especificadas por el autor) están marcadas **[SUPUESTO]** y abiertas a consulta con el autor. Cualquier cambio de regla dispara el protocolo dominó de AUDIT.md.

---

## 1. Tablero y coordenadas

- Tablero de **16×16 = 256 casillas**. Columnas `a`–`p` (0–15 desde la izquierda de las blancas), filas `1`–`16` (0–15 desde las blancas).
- Casilla = (columna, fila). Notación algebraica: `<letra><número>` con número de 1 o 2 dígitos (`a1` … `p16`).
- Coloreado: "a white one at the right end of each player" (A1) → `p1` es clara, `a1` oscura; `h2` (Rey blanco) es oscura.
- Índice de casilla para implementaciones: `sq = fila*16 + columna` (a1=0, p1=15, a16=240, p16=255). La espec no exige este layout; los oráculos deben aceptar coordenadas algebraicas.

## 2. Setup inicial

64 piezas por bando, 26 tipos. Filas 1–4 (blancas) y 13–16 (negras, espejo por filas; mismas columnas — `symmetry=mirror` del Interactive Diagram, A2). Rey blanco en **h2**, Rey negro en **h15** (nota §10-C1: el texto de CVP que dice "third row" es un arrastre erróneo de Terachess I; el diagrama del autor y el ID ponen fila 2).

| Fila (blancas) | Contenido (a→p) |
|---|---|
| 1 | Admiral, Centaur, Missionary, Marshall, Cardinal, Buffalo, Duchess, Sorceress, Sorceress, Duchess, Buffalo, Cardinal, Marshall, Missionary, Centaur, Admiral |
| 2 | Cannon, Camel, Giraffe, Troll, Rhinoceros, Archer, Lion, **King(h2)**, **Amazon(i2)**, Lion, Archer, Rhinoceros, Troll, Giraffe, Camel, Cannon |
| 3 | Elephant, Rook, Knight, Bishop, Machine, Prince, Eagle, Queen, Queen, Eagle, Prince, Machine, Bishop, Knight, Rook, Elephant |
| 4 | 16 Peones |

Todas las piezas no-Peón van por pares excepto Rey y Amazona (A1: "Except the King and the Amazon, all not-Pawn pieces are present as pairs").

## 3. Letras de pieza y FEN

### 3.1 Letras (mayúscula=blancas, minúscula=negras) — las 26 letras a–z

| Letra | Pieza | Letra | Pieza |
|---|---|---|---|
| a | Amazon | n | Knight (Caballo) |
| b | Bishop (Alfil) | o | Sorceress |
| c | Cannon | p | Pawn (Peón) |
| d | Duchess | q | Queen (Dama) |
| e | Elephant | r | Rook (Torre) |
| f | Buffalo | s | Admiral |
| g | Eagle | t | Troll |
| h | Marshall | u | Rhinoceros |
| i | Prince | v | Archer (=Crocodile, §10-C2) |
| j | Centaur | w | Machine |
| k | King (Rey) | x | Cardinal |
| l | Lion | y | Missionary |
| m | Camel | z | Giraffe |

### 3.2 Gramática FEN-TSF

`<piezas> <turno> <derechos-salto-rey> <casilla-ep> <medio-movimientos> <n-jugada>`

- **piezas**: 16 filas de la 16 a la 1 separadas por `/`; vacíos como enteros 1–16 (se permite dividir un vacío en sumandos, p. ej. `8 8`≡`16`, pero el emisor canónico usa el run máximo).
- **turno**: `w` | `b`.
- **derechos-salto-rey**: `K` si el Rey blanco aún no se ha movido (conserva el salto inicial, §6.3), `k` ídem negro; `-` si ninguno. Orden canónico `Kk`.
- **casilla-ep**: casilla cruzada por el último doble paso de Peón o Príncipe rival (§6.2) en algebraico, o `-`. Se emite SOLO si existe al menos un Peón rival que podría capturar al paso (emisor canónico; los parsers deben aceptar también la casilla incondicional). **[SUPUESTO]** análogo a FEN moderno.
- **medio-movimientos**: contador para la regla de 50 (§7.4).
- **n-jugada**: número de jugada completa, desde 1.

**FEN inicial**:
```
sjyhxfdoodfxhyjs/cmztuvlkalvutzmc/ernbwigqqgiwbnre/pppppppppppppppp/16/16/16/16/16/16/16/16/PPPPPPPPPPPPPPPP/ERNBWIGQQGIWBNRE/CMZTUVLKALVUTZMC/SJYHXFDOODFXHYJS w Kk - 0 1
```

## 4. Movimiento de las 26 piezas

Convenciones: "desliza" = se detiene ante el primer ocupante (captura si es enemigo, bloqueado si es propio); "salta" = ignora ocupantes intermedios; salvo indicación, mover y capturar usan el mismo patrón; (m,n)-leaper = salto a ±m,±n y ±n,±m. Betza citado del Interactive Diagram (A2).

| # | Pieza | Betza | Definición operativa |
|---|---|---|---|
| 1 | **King** | `K` + `imAimDimN` | 1 paso en las 8 direcciones. Real (sujeto a jaque). Salto inicial: §6.3. Sin enroque. |
| 2 | **Queen** | `Q` | Desliza ortogonal o diagonal. |
| 3 | **Rook** | `R` | Desliza ortogonal. Sin enroque (A1: "no castling at Terachess"). |
| 4 | **Bishop** | `B` | Desliza diagonal. |
| 5 | **Knight** | `N` | (1,2)-leaper. |
| 6 | **Amazon** | `QN` | Queen + Knight. |
| 7 | **Marshall** | `RN` | Rook + Knight. |
| 8 | **Cardinal** | `BN` | Bishop + Knight. |
| 9 | **Centaur** | `KN` | 1 paso en 8 direcciones (no real) + salto de Knight. |
| 10 | **Admiral** | `RF` | Rook + 1 paso diagonal (Dragon King de shogi). |
| 11 | **Missionary** | `BW` | Bishop + 1 paso ortogonal (Dragon Horse). |
| 12 | **Eagle** (Gryphon) | `FyafsF` | 1 paso diagonal y después desliza ortogonalmente ALEJÁNDOSE (§4.1). Puede detenerse en la casilla diagonal. No salta. |
| 13 | **Rhinoceros** | `WyafsW` | 1 paso ortogonal y después desliza diagonalmente ALEJÁNDOSE (§4.1). Contraparte del Eagle. |
| 14 | **Lion** | `KNAD` | Salta a CUALQUIER casilla a distancia Chebyshev ≤2 (las 24): K∪(2,2)∪(2,0)∪(1,2), todo ignorando intermedias. |
| 15 | **Camel** | `C` (=(1,3)) | (1,3)-leaper. |
| 16 | **Giraffe** | `Z` (=(2,3)) | (2,3)-leaper (movimiento de zebra; renombrada desde "Bull" de Terachess I). |
| 17 | **Buffalo** | `NCZ` | (1,2)+(1,3)+(2,3)-leaper. |
| 18 | **Cannon** | `mRcpR` | Mueve (sin capturar) como Rook; captura como Rook saltando EXACTAMENTE una pieza-pantalla (de cualquier color) y tomando la primera pieza enemiga tras ella (§4.2). |
| 19 | **Archer** | `mBcpB` | Vao: mueve como Bishop; captura como Bishop con pantalla (§4.2). |
| 20 | **Sorceress** | `mQcpQ` | Mueve como Queen; captura como Queen con pantalla (§4.2) — "Cannon + Crocodile" (A1). |
| 21 | **Duchess** | `KADGH` | 1, 2 o 3 casillas en cualquier dirección recta; a distancia 2–3 SALTA: K∪(2,2)∪(2,0)∪(3,3)∪(3,0). |
| 22 | **Machine** | `WD` | 1 paso ortogonal, o salto de 2 ortogonal (ignora la intermedia). |
| 23 | **Elephant** | `FA` | 1 paso diagonal, o salto de 2 diagonal (ignora la intermedia). |
| 24 | **Prince** | `KfmnnD` | 1 paso en 8 direcciones, mueve y captura (no real: puede quedar "en jaque"). Además, doble paso adelante sin captura desde CUALQUIER casilla (§6.1). |
| 25 | **Pawn** | `fmWfceFfmnnD` | 1 o 2 pasos rectos adelante sin capturar, desde cualquier casilla (§6.1); captura 1 diagonal adelante; captura al paso (§6.2); promociona (§6.4). |
| 26 | **Troll** | `GHfmWfcF` | Salto de 3 diagonal (3,3) u ortogonal (0,3), ignorando intermedias, mueve y captura; además paso de peón: 1 adelante sin captura y 1 diagonal-adelante capturando. SIN doble paso, SIN e.p. en ningún sentido. Promoción especial §6.4. |

### 4.1 Bent riders (Eagle / Rhinoceros) — semántica exacta

**Eagle** desde `s` con dirección diagonal `d=(dx,dy)`, `dx,dy∈{+1,−1}`; sea `s1 = s+d`:
- Si `s1` está ocupada por pieza propia: nada en esta dirección.
- Si `s1` está ocupada por enemiga: puede capturar en `s1` (y nada más en esta dirección).
- Si `s1` está vacía: puede moverse a `s1`; y además desliza desde `s1` en las DOS direcciones ortogonales `(dx,0)` y `(0,dy)` (las componentes de `d`, alejándose del origen), con la regla normal de deslizamiento.
El Eagle nunca salta y nunca vuelve hacia atrás. Total: 4 diagonales × (1 casilla + 2 rayos).

**Rhinoceros**: simétrico: `d` ortogonal; desde `s1` desliza en las DOS diagonales que contienen a `d` como componente (para `d=(1,0)`: `(1,1)` y `(1,−1)`).

### 4.2 Piezas con pantalla (Cannon / Archer / Sorceress) — semántica exacta

Para capturar a lo largo de una línea: sea `P1` la primera pieza en la línea y `P2` la segunda. La captura es legal si y solo si `P2` existe y es enemiga; el destino es la casilla de `P2`; `P1` (la pantalla) puede ser de **cualquier color** (A1: "this piece may be of either color") y no se ve afectada. No pueden capturar sin pantalla, no pueden saltar dos pantallas, no pueden capturar la pantalla. El movimiento sin captura es un deslizamiento normal (se detiene ANTES de `P1`).
Consecuencia para el jaque: estas piezas dan jaque a un Rey si tienen exactamente una pieza (de cualquier color) entre ellas y el Rey en su línea de captura. Pueden clavar y dar jaques descubiertos al mover una pantalla (propia o al retirarse una pieza cualquiera).

## 5. Legalidad, jaque y estado

- **Jaque**: el Rey está en jaque si una pieza enemiga "atacaría" su casilla con una captura pseudo-legal (incluidas capturas con pantalla §4.2 y las capturas tipo peón del Troll). Los movimientos `m` (solo-movimiento: doble paso de Peón/Príncipe, salto de Rey, deslizamiento sin captura de Cannon/Archer/Sorceress) NO atacan.
- **Legalidad**: un movimiento es legal si tras ejecutarlo el propio Rey no queda en jaque. El Príncipe NO es real: puede moverse a/permanecer en casillas atacadas y su captura no termina la partida.
- **Estado completo de la posición** (inventario de suficiencia, playbook doc 02): piezas por casilla + turno + derecho de salto del Rey blanco + derecho de salto del Rey negro + casilla e.p. (o ninguna) + contador de 50 + número de jugada. NADA más afecta a la legalidad futura. Pares de aliasing verificados en `oracle/tests/test_aliasing.py`:
  - Misma disposición, distinto derecho de salto de Rey → movimientos legales distintos (el derecho DEBE estar en FEN, Zobrist y record).
  - Misma disposición, distinta casilla e.p. (doble paso de Príncipe vs llegada en dos jugadas) → legales distintos (la casilla e.p. DEBE codificar la casilla, no el tipo del capturado: el capturado puede ser Príncipe).
  - Promoción del Troll: depende SOLO del tipo de movimiento que llega a la última fila (propiedad del movimiento, NO estado persistente — par mínimo: mismo Troll en misma casilla llegado por salto vs por paso).

## 6. Reglas especiales

### 6.1 Doble paso (Peón y Príncipe)
Peón y Príncipe pueden avanzar 2 casillas rectas adelante sin capturar **desde cualquier casilla del tablero** (no solo la inicial), si AMBAS casillas (intermedia y destino) están vacías (paso bloqueable, `n` en Betza — NO es salto). El Troll no tiene doble paso.

### 6.2 Captura al paso (A2/A3)
"Any time a Pawn or Prince takes a double step and passes through the capture square of an opposing Pawn, that Pawn may capture the Pawn or Prince as if it had only moved one square. This en passant capture must be made in the move immediately following the double step. Only a Pawn may capture en passant; the Prince does not have this option."
- Tras CUALQUIER doble paso (de Peón o de Príncipe), la casilla intermedia queda marcada como casilla e.p. durante exactamente una jugada rival.
- Solo un **Peón** enemigo cuyo movimiento de captura (1 diagonal adelante) alcance la casilla e.p. puede capturar al paso: se mueve a la casilla e.p. y retira la pieza que hizo el doble paso (Peón **o Príncipe**).
- El Troll ni captura al paso ni genera e.p. ("No e.p.", A2).

### 6.3 Salto inicial del Rey (A1/A2/A3 — regla Metamachy; sustituye al enroque)
En su **primer movimiento** (derecho `K`/`k` de §3.2), el Rey puede saltar a una casilla **libre** a distancia 2: destinos tipo (2,0), (2,2) o (1,2)/(2,1) — desde h2: f1..f4, g4, h4, i4, j1..j4 (los 16 destinos en el caso general sin borde). Condiciones:
1. El Rey NO está en jaque en el momento del salto.
2. El destino está vacío (salto sin captura) y no está atacado (legalidad normal).
3. Saltos (2,0) y (2,2): la casilla intermedia puede estar **ocupada o no** (se salta), pero NO puede estar **amenazada** por una pieza enemiga.
4. Saltos tipo caballo (1,2): hay dos casillas intermedias — para el salto (±2,±1): `from+(±1,0)` y `from+(±1,±1)`; para (±1,±2): `from+(0,±1)` y `from+(±1,±1)`. **Al menos una** de las dos debe estar libre de amenaza (A2: "if jumping from h2 to j3, either i2 or i3 must not be under attack"). Su ocupación es irrelevante.
5. El derecho se pierde definitivamente cuando el Rey mueve (cualquier movimiento, incluido el propio salto).
**[SUPUESTO]** "Amenazada" = atacada por cualquier captura pseudo-legal enemiga en la posición actual (mismo predicado que el jaque, §5), sin considerar si la pieza atacante está clavada. Análogo al enroque FIDE.

### 6.4 Promoción (A1/A2)
Al **alcanzar la última fila** (fila 16 para blancas, 1 para negras), la promoción es **inmediata, obligatoria y sin elección**:

| Pieza | Promociona a |
|---|---|
| Pawn | Queen |
| Prince | Amazon |
| Knight, Camel, Giraffe | Buffalo |
| Elephant, Machine, Centaur | Lion |
| Troll | Queen — **solo si llega con paso de peón** (1 adelante o captura diagonal-adelante); si llega con salto de 3, NO promociona y queda como Troll |

Ninguna otra pieza promociona. No hay más zona de promoción que la última fila.

## 7. Final de partida

1. **Jaque mate**: victoria (A1: "Victory is obtained when the opposite King is checkmated").
2. **Ahogado** (sin movimientos legales, sin jaque): **tablas** (A2: "The end-of-game rules, checkmate, stalemate, etc., are identical to standard chess").
3. **Repetición**: **[SUPUESTO]** triple repetición de posición (con mismos derechos de salto de Rey y misma casilla e.p. efectiva) = tablas, análogo FIDE. El autor no lo especifica; consulta abierta.
4. **Regla de 50**: **[SUPUESTO]** 50 jugadas completas sin captura ni movimiento de **Peón** = tablas, análogo FIDE (los pasos de peón del Troll y del Príncipe NO resetean el contador; solo el tipo Pawn). El autor no lo especifica; consulta abierta. Nota de ingeniería: con 256 casillas puede ser corta; revisar con datos de F4.
5. Material insuficiente: sin regla especial (los mates básicos de Terachess no están tabulados); solo aplican 3 y 4.

## 8. Valores de pieza orientativos (Zillions, normalizados Torre=5; A1)

P 0.5 · Giraffe 1.7 · Camel 1.8 · Elephant 2 · Knight 2 · Machine 2.2 · Prince 2.3 · Troll 2.4 · Archer 3.3 · Bishop 3.4 · Centaur 4.1 · Missionary 4.4 · Cannon 5 · Rook 5 · Cardinal 5.3 · Buffalo 5.4 · Duchess 5.8 · Lion 6 · Admiral 6 · Rhinoceros 6.1 · Marshall 6.9 · Sorceress 8.2 · Queen 8.3 · Eagle 8.4 · Amazon 10.2

(Semilla para la eval material de F2: ×100 → Rook=500.)

## 9. Diferencias con Terachess I (2008/2013, obsoleta) — para el oráculo Jocly

Jocly implementa Terachess **I**. Compartido y comparable pieza a pieza: Q, R, B, N, K(sin salto en I), Pawn, Prince, Elephant, Machine, Cannon(=Cannon), Camel, Lion, Eagle(=Gryphon), Sorceress(=Star), Archer(=Bow), Giraffe(=Bull — ¡en I era otro patrón, verificar por pieza!), Buffalo, Marshall, Cardinal, Amazon. NO comparable: Rhinoceros (en I: salto de caballo + diagonal alejándose, tipo Unicornio; en II: §4.1), Duchess/Missionary/Admiral/Centaur/Troll (no existen en I), Corporal/Ship/Antelope (no existen en II), setup completo, e.p. (en I capturan también los Corporals), salto de Rey (no existe en I). Todo cotejo con Jocly se limita al subconjunto compartido y a la geometría.

## 10. Contradicciones entre fuentes — resoluciones

| # | Contradicción | Resolución |
|---|---|---|
| C1 | Fila del Rey: CVP-II dice "center of the third row"; el autor (A1) dice "second row" y su Interactive Diagram pone `king::…::h2` | **Fila 2.** El texto CVP es un arrastre de Terachess I (donde el Rey sí iba en fila 3) |
| C2 | Nombre del Vao: CVP "Archer (previously named Crocodile)"; A1 (2024) sigue con "Crocodile" | **Archer** como nombre canónico TSF, alias Crocodile documentado |
| C3 | Errata en A1: la descripción de Machine dice "When an Elephant moves two squares…" | Errata; CVP corrige. Machine = WD |
| C4 | Errata en A1: Rhinoceros "the real counterpart of the Cannon" | Errata; es contraparte del Eagle (WyafsW vs FyafsF) |
| C5 | Conteo: página actual "26 different pieces"; la original decía 24 | 26 es Terachess II; 24 era Terachess I |

## 11. Notación de movimientos (para UCI/registro)

- Movimiento normal: `<origen><destino>` (p. ej. `h2h3`, `a4a6`).
- Promoción: sufijo con la letra de la pieza resultante en minúscula: `a15a16q`, `f13f14a` (Prince→Amazon), `…f` (→Buffalo), `…l` (→Lion). Como la promoción es forzada y determinista por tipo, el sufijo es redundante pero se emite siempre (validación cruzada barata).
- Captura al paso: `<origen><casilla-ep>` (el destino es la casilla e.p., no la del capturado).
- Salto de Rey: `<origen><destino>` normal (p. ej. `h2j3`); se distingue por contexto (Rey + distancia 2 + derecho vigente).
