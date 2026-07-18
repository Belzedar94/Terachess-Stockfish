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
