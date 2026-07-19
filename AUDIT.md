# AUDIT.md — Terachess-Stockfish

Running log of all iterations. Every implementation/iteration/fix (including
discarded attempts) gets an entry: hypothesis → files changed → validation →
decision → learnings. Columna adicional obligatoria en familias de experimentos:
"espacio explorado / presupuesto usado" (lección retroactive-review E4 de Spell).

Convenciones heredadas de Spell-Stockfish\AUDIT.md:
- Bench determinista por commit (firma = nodos totales) registrado en BENCH_LOG.md;
  disciplina OpenBench `Bench: <nodos>` en el mensaje de commit.
- Gates fallan cerrados: un gate fallido bloquea downstream y se registra con
  evidencia exacta; nunca se relaja un umbral después de ver el resultado.
- Divergencias de reglas: protocolo dominó (fixture nuevo → refs perft
  regeneradas → rebase de ramas → rebuild del baseline congelado).

---

## 2026-07-18 — ADR-0: elección de base (decisión del propietario)

**Contexto**: plan de implementación completo elaborado y aprobado
([PLAN.md](PLAN.md)). El análisis comparado (4 opciones, con red-team
adversarial verificando números contra código real) recomendaba la opción C
(extender Fairy-Stockfish-VLB, 256 casillas ya operativas) por menor tiempo
hasta la primera red (11–18 semanas vs 15–24) y menor riesgo de reglas (Betza
batido en cientos de variantes).

**Decisión del propietario**: **opción A — motor especializado partiendo de
Stockfish master, portado a 256 casillas.** Razón textual: "El overhead de
tomar como base un motor generalista es muy peligroso. Prefiero que empecemos
de uno especializado y que hagamos tantos cambios y mejoras como necesitemos."

**Consecuencias asumidas** (detalle en [docs/adr/ADR-000-base-especializada.md](docs/adr/ADR-000-base-especializada.md)):
- El riesgo dominante pasa a ser la corrección de reglas (movegen a mano de 26
  tipos sin perft de referencia) → mitigado con triple oráculo (Python desde la
  espec + Jocly + FSF-VLB opcional) y presupuesto de verificación reforzado.
- FSF-VLB queda relegado a referencia de código (Bitboard256 del propietario) y
  tercer oráculo opcional de perft; nunca base.
- Punto de partida concreto: chasis Spell-Stockfish (SF master 2026 con Move32/
  TT12B/butterfly-64k ya migrados), extirpando la capa spell; fallback SF master
  virgen si la extirpación no es limpia en ≤1 semana (micro-gate F1a).

**Estado**: plan aprobado; versión objetivo Terachess II (2020) confirmada por
el propietario. Siguiente paso: Fase 0 (TERACHESS_SPEC.md + oráculo Python +
suite perft + round-trip FEN — todo antes de una línea de búsqueda).

---

## 2026-07-18/19 — F0 parcial + micro-gate F1a: PASS

**Hipótesis**: el chasis Spell-Stockfish puede volver a SF master puro en ≤1
semana de trabajo (micro-gate del ADR-0); la espec de Terachess II es
transcribible del Interactive Diagram sin ambigüedades bloqueantes.

**Hecho**:
- `TERACHESS_SPEC.md` v1.0: 26 piezas con semántica operativa (bent riders
  §4.1, pantallas §4.2, salto de Rey §6.3, e.p. §6.2, promoción §6.4),
  suposiciones FIDE marcadas [SUPUESTO], 5 contradicciones entre fuentes
  resueltas con criterio documentado.
- Arnés F0: run_fixtures / differential A↔B / perft_suite / test_roundtrip /
  test_aliasing (6 pares). Contrato tera-bin v1 congelado (docs/tera-bin-v1.md).
- **F1a (commit 72bfb25)**: capa spell extirpada por completo (16 archivos
  eliminados, ~25 modificados). Validación: perft startpos d5=4.865.609,
  d6=119.060.324; kiwipete d4=4.085.603; pos3 d5=674.624; pos4 d4=422.333;
  pos5 d4=2.103.487; pos6 d4=3.894.594 — TODOS exactos contra las referencias
  públicas de ajedrez. Bench determinista 2.692.515 nodos en 3 corridas
  (~1,38 Mnps, 1 hilo, eval material fallback, sin red). Mate-en-1, ahogado y
  mate-en-raíz correctos. Conservado: Move u32 (bits altos libres), TT 12 B,
  MAX_MOVES 32768, arena MovePicker.
- Conteo a mano del arquitecto para el gate de movegen 256: **perft(1)
  startpos Terachess II = 54** (32 peones + 4 caballos + 4 camellos +
  4 jirafas + 2 máquinas + 2 elefantes + 6 trolls; filas 1–4 llenas ⇒ Lion/
  Buffalo/Duchess/hoppers/bent-riders/Rey/Príncipe = 0). Pendiente de
  verificación independiente por el oráculo.

**Decisión**: gate F1a PASS → la base del port es el chasis extirpado (no hizo
falta el fallback a SF virgen). Port 256 lanzado en 4 etapas
(docs/port-256-design.md como contrato congelado de representación).

**Learnings**: (1) el make de MSYS2 pierde TMP/TEMP bajo el harness — pasarlas
como variables de make; documentado en el contrato de build. (2) Los node
counts de búsqueda del chasis no son byte-idénticos a SF oficial (constantes
SPSA del fork heredadas) — irrelevante: F2 re-deriva todas las constantes para
branching 150–250 de todos modos. (3) Interrupción por límite de sesión a
mitad de cirugía deja estado parcial en disco — verificar SIEMPRE el estado
real con ls/git status antes de reanudar agentes.
