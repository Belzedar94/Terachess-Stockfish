#!/usr/bin/env python3
"""Terachess II rules oracle -- implementation A (MAILBOX).

Single file, Python 3.12 stdlib only, deterministic.
Source of truth: TERACHESS_SPEC.md (spec v1.0, 2026-07-18).

Board: 256-square mailbox list; sq = rank*16 + file (a1=0, p1=15, a16=240,
p16=255).  White moves toward rank 16 (+16 per step), black toward rank 1.
Piece letters per spec section 3.1: uppercase = white, lowercase = black,
'.' = empty square.

CLI contract:
  perft <depth> [fen]       -> one integer
  divide <depth> [fen]      -> "<uci> <count>" lines (alphabetical) + "total <n>"
  moves [fen]               -> legal UCI moves, alphabetical, one per line
  fen [fen]                 -> canonical FEN round-trip
  apply <fen> <m1> [m2 ...] -> FEN after applying moves in order
  selftest                  -> internal tests, exit 0 on success
"""

import re
import sys

START_FEN = (
    "sjyhxfdoodfxhyjs/cmztuvlkalvutzmc/ernbwigqqgiwbnre/pppppppppppppppp/"
    "16/16/16/16/16/16/16/16/PPPPPPPPPPPPPPPP/ERNBWIGQQGIWBNRE/"
    "CMZTUVLKALVUTZMC/SJYHXFDOODFXHYJS w Kk - 0 1"
)

FILES = "abcdefghijklmnop"
EMPTY = "."
MOVE_RE = re.compile(r"^([a-p])(\d{1,2})([a-p])(\d{1,2})([qafl]?)$")
SQ_RE = re.compile(r"^([a-p])(\d{1,2})$")


def sq_index(f, r):
    return r * 16 + f


def sq_name(sq):
    return FILES[sq & 15] + str((sq >> 4) + 1)


def parse_sq(name):
    m = SQ_RE.match(name)
    if not m:
        raise ValueError("bad square: %r" % (name,))
    r = int(m.group(2)) - 1
    if not 0 <= r <= 15:
        raise ValueError("bad square rank: %r" % (name,))
    return sq_index(FILES.index(m.group(1)), r)


# ---------------------------------------------------------------------------
# Geometry precomputation
# ---------------------------------------------------------------------------

ORTH = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))
KING8 = ORTH + DIAG


def _sym(m, n):
    s = set()
    for a in (m, -m):
        for b in (n, -n):
            s.add((a, b))
            s.add((b, a))
    return tuple(sorted(s))


N_OFF = _sym(1, 2)    # Knight
C_OFF = _sym(1, 3)    # Camel
Z_OFF = _sym(2, 3)    # Giraffe (zebra)
D2_OFF = _sym(2, 0)   # (2,0) jump
A2_OFF = _sym(2, 2)   # (2,2) jump
H3_OFF = _sym(3, 0)   # (3,0) jump
G3_OFF = _sym(3, 3)   # (3,3) jump


def _on(f, r):
    return 0 <= f < 16 and 0 <= r < 16


def _leap_table(offs):
    offs = tuple(sorted(set(offs)))
    t = []
    for r in range(16):
        for f in range(16):
            t.append(tuple(sq_index(f + df, r + dr)
                           for df, dr in offs if _on(f + df, r + dr)))
    return t


STEP_O = _leap_table(ORTH)
STEP_D = _leap_table(DIAG)
STEP_K = _leap_table(KING8)
LEAP_N = _leap_table(N_OFF)
LEAP_C = _leap_table(C_OFF)
LEAP_Z = _leap_table(Z_OFF)
LEAP_D2 = _leap_table(D2_OFF)
LEAP_A2 = _leap_table(A2_OFF)
LEAP_H3 = _leap_table(H3_OFF)
LEAP_G3 = _leap_table(G3_OFF)
CENTAUR_T = _leap_table(KING8 + N_OFF)
LION_T = _leap_table(KING8 + N_OFF + D2_OFF + A2_OFF)          # Chebyshev <= 2
DUCHESS_T = _leap_table(KING8 + D2_OFF + A2_OFF + H3_OFF + G3_OFF)
BUFFALO_T = _leap_table(N_OFF + C_OFF + Z_OFF)
MACHINE_T = _leap_table(ORTH + D2_OFF)
ELEPH_T = _leap_table(DIAG + A2_OFF)
TROLL_LEAP = _leap_table(H3_OFF + G3_OFF)


def _ray_table(dirs):
    t = []
    for r in range(16):
        for f in range(16):
            rays = []
            for df, dr in dirs:
                ray = []
                nf, nr = f + df, r + dr
                while _on(nf, nr):
                    ray.append(sq_index(nf, nr))
                    nf += df
                    nr += dr
                if ray:
                    rays.append(tuple(ray))
            t.append(tuple(rays))
    return t


RAYS_O = _ray_table(ORTH)
RAYS_D = _ray_table(DIAG)

# Eagle (spec 4.1): 1 diagonal step d to s1, then slides orthogonally AWAY in
# the two component directions (dx,0) and (0,dy).  May stop/capture at s1.
# EAGLE_MOVE[sq] = tuple of (s1, (ray, ray)) per available diagonal.
EAGLE_MOVE = []
for _r in range(16):
    for _f in range(16):
        _entries = []
        for _df, _dr in DIAG:
            _nf, _nr = _f + _df, _r + _dr
            if not _on(_nf, _nr):
                continue
            _rays = []
            for _of, _orr in ((_df, 0), (0, _dr)):
                _ray = []
                _mf, _mr = _nf + _of, _nr + _orr
                while _on(_mf, _mr):
                    _ray.append(sq_index(_mf, _mr))
                    _mf += _of
                    _mr += _orr
                if _ray:
                    _rays.append(tuple(_ray))
            _entries.append((sq_index(_nf, _nr), tuple(_rays)))
        EAGLE_MOVE.append(tuple(_entries))

# Rhinoceros (spec 4.1, symmetric): 1 orth step d to s1, then slides along the
# two diagonals that contain d as a component.  May stop/capture at s1.
RHINO_MOVE = []
for _r in range(16):
    for _f in range(16):
        _entries = []
        for _df, _dr in ORTH:
            _nf, _nr = _f + _df, _r + _dr
            if not _on(_nf, _nr):
                continue
            if _dr == 0:
                _diags = ((_df, 1), (_df, -1))
            else:
                _diags = ((1, _dr), (-1, _dr))
            _rays = []
            for _gf, _gr in _diags:
                _ray = []
                _mf, _mr = _nf + _gf, _nr + _gr
                while _on(_mf, _mr):
                    _ray.append(sq_index(_mf, _mr))
                    _mf += _gf
                    _mr += _gr
                if _ray:
                    _rays.append(tuple(_ray))
            _entries.append((sq_index(_nf, _nr), tuple(_rays)))
        RHINO_MOVE.append(tuple(_entries))

# Reverse-scan tables for bent-rider attacks on a target square t.
# Eagle travelled along orth dir o and lands on t; scan from t along -o over
# EMPTY squares s1 (the elbow, must be empty); eagle origin E = s1 - o +- perp.
# EAGLE_ATT[t] = per direction: tuple of (s1, (E candidates...)) by distance.
EAGLE_ATT = []
for _r in range(16):
    for _f in range(16):
        _dirlists = []
        for _of, _orr in ORTH:
            _pf, _pr = ((0, 1) if _orr == 0 else (1, 0))
            _entries = []
            _m = 1
            while True:
                _sf, _sr = _f - _m * _of, _r - _m * _orr
                if not _on(_sf, _sr):
                    break
                _es = []
                for _sg in (1, -1):
                    _ef, _er = _sf - _of + _sg * _pf, _sr - _orr + _sg * _pr
                    if _on(_ef, _er):
                        _es.append(sq_index(_ef, _er))
                _entries.append((sq_index(_sf, _sr), tuple(_es)))
                _m += 1
            if _entries:
                _dirlists.append(tuple(_entries))
        EAGLE_ATT.append(tuple(_dirlists))

# Rhino travelled along diagonal g; elbow s1 empty; origin E = s1 - w where w
# is one of the two orthogonal components of g.
RHINO_ATT = []
for _r in range(16):
    for _f in range(16):
        _dirlists = []
        for _gf, _gr in DIAG:
            _entries = []
            _m = 1
            while True:
                _sf, _sr = _f - _m * _gf, _r - _m * _gr
                if not _on(_sf, _sr):
                    break
                _es = []
                for _wf, _wr in ((_gf, 0), (0, _gr)):
                    _ef, _er = _sf - _wf, _sr - _wr
                    if _on(_ef, _er):
                        _es.append(sq_index(_ef, _er))
                _entries.append((sq_index(_sf, _sr), tuple(_es)))
                _m += 1
            if _entries:
                _dirlists.append(tuple(_entries))
        RHINO_ATT.append(tuple(_dirlists))

# King leap (spec 6.3).  KING_LEAP[sq] = tuple of (dest, mids, is_knight_type).
# (2,0)/(2,2): single mid square, must NOT be threatened (occupancy free).
# (1,2)-type: two mids, at least ONE must not be threatened.
KING_LEAP = []
for _r in range(16):
    for _f in range(16):
        _entries = []
        for _df, _dr in sorted(set(D2_OFF + A2_OFF)):
            _nf, _nr = _f + _df, _r + _dr
            if _on(_nf, _nr):
                _mid = sq_index(_f + _df // 2, _r + _dr // 2)
                _entries.append((sq_index(_nf, _nr), (_mid,), False))
        for _df, _dr in N_OFF:
            _nf, _nr = _f + _df, _r + _dr
            if not _on(_nf, _nr):
                continue
            if abs(_df) == 2:   # (+-2,+-1): from+(+-1,0) and from+(+-1,+-1)
                _m1 = (_f + _df // 2, _r)
                _m2 = (_f + _df // 2, _r + _dr)
            else:               # (+-1,+-2): from+(0,+-1) and from+(+-1,+-1)
                _m1 = (_f, _r + _dr // 2)
                _m2 = (_f + _df, _r + _dr // 2)
            _mids = tuple(sq_index(a, b) for a, b in (_m1, _m2) if _on(a, b))
            _entries.append((sq_index(_nf, _nr), _mids, True))
        KING_LEAP.append(tuple(_entries))

# ---------------------------------------------------------------------------
# Attack sets (which enemy piece types attack a square via each pattern)
# ---------------------------------------------------------------------------


def _both(letters):
    return frozenset(letters.upper()), frozenset(letters)


N_ATT_W, N_ATT_B = _both("nahxjfl")        # knight-leap attackers
C_ATT_W, C_ATT_B = _both("mf")             # camel-leap
Z_ATT_W, Z_ATT_B = _both("zf")             # zebra-leap
D2_ATT_W, D2_ATT_B = _both("wld")          # (2,0)-jump
A2_ATT_W, A2_ATT_B = _both("eld")          # (2,2)-jump
H3_ATT_W, H3_ATT_B = _both("dt")           # (3,0)-jump
G3_ATT_W, G3_ATT_B = _both("dt")           # (3,3)-jump
OSTEP_ATT_W, OSTEP_ATT_B = _both("kjiywdlu")   # 1-step orthogonal capturers
DSTEP_ATT_W, DSTEP_ATT_B = _both("kjisedlg")   # 1-step diagonal capturers
OSLIDE_ATT_W, OSLIDE_ATT_B = _both("rqahs")    # orthogonal sliders
DSLIDE_ATT_W, DSLIDE_ATT_B = _both("bqaxy")    # diagonal sliders
OSCREEN_ATT_W, OSCREEN_ATT_B = _both("co")     # orth screen-capturers
DSCREEN_ATT_W, DSCREEN_ATT_B = _both("vo")     # diag screen-capturers


# ---------------------------------------------------------------------------
# Attack predicate (spec 5): a square is attacked if an enemy piece could
# make a pseudo-legal CAPTURE onto it.  Move-only steps never attack.
# ---------------------------------------------------------------------------


def attacked(bd, t, by_white):
    """True if side (by_white) attacks square t on board bd."""
    if by_white:
        nset, cset, zset = N_ATT_W, C_ATT_W, Z_ATT_W
        d2set, a2set, h3set, g3set = D2_ATT_W, A2_ATT_W, H3_ATT_W, G3_ATT_W
        ostep, dstep = OSTEP_ATT_W, DSTEP_ATT_W
        oslide, dslide = OSLIDE_ATT_W, DSLIDE_ATT_W
        oscreen, dscreen = OSCREEN_ATT_W, DSCREEN_ATT_W
        eagle, rhino = "G", "U"
        pawnish = ("P", "T")
    else:
        nset, cset, zset = N_ATT_B, C_ATT_B, Z_ATT_B
        d2set, a2set, h3set, g3set = D2_ATT_B, A2_ATT_B, H3_ATT_B, G3_ATT_B
        ostep, dstep = OSTEP_ATT_B, DSTEP_ATT_B
        oslide, dslide = OSLIDE_ATT_B, DSLIDE_ATT_B
        oscreen, dscreen = OSCREEN_ATT_B, DSCREEN_ATT_B
        eagle, rhino = "g", "u"
        pawnish = ("p", "t")

    for s in LEAP_N[t]:
        if bd[s] in nset:
            return True
    for s in LEAP_C[t]:
        if bd[s] in cset:
            return True
    for s in LEAP_Z[t]:
        if bd[s] in zset:
            return True
    for s in LEAP_D2[t]:
        if bd[s] in d2set:
            return True
    for s in LEAP_A2[t]:
        if bd[s] in a2set:
            return True
    for s in LEAP_H3[t]:
        if bd[s] in h3set:
            return True
    for s in LEAP_G3[t]:
        if bd[s] in g3set:
            return True

    # Pawn / Troll forward-diagonal captures (directional).
    tf = t & 15
    if by_white:
        base = t - 16   # a white pawn attacking t sits one rank below t
        if base >= 0:
            if tf > 0 and bd[base - 1] in pawnish:
                return True
            if tf < 15 and bd[base + 1] in pawnish:
                return True
    else:
        base = t + 16
        if base < 256:
            if tf > 0 and bd[base - 1] in pawnish:
                return True
            if tf < 15 and bd[base + 1] in pawnish:
                return True

    # Orthogonal rays: sliders, adjacent steppers, screen pieces.
    for ray in RAYS_O[t]:
        ln = len(ray)
        i = 0
        while i < ln:
            p = bd[ray[i]]
            if p != EMPTY:
                if p in oslide:
                    return True
                if i == 0 and p in ostep:
                    return True
                j = i + 1
                while j < ln:
                    p2 = bd[ray[j]]
                    if p2 != EMPTY:
                        if p2 in oscreen:
                            return True
                        break
                    j += 1
                break
            i += 1

    # Diagonal rays.
    for ray in RAYS_D[t]:
        ln = len(ray)
        i = 0
        while i < ln:
            p = bd[ray[i]]
            if p != EMPTY:
                if p in dslide:
                    return True
                if i == 0 and p in dstep:
                    return True
                j = i + 1
                while j < ln:
                    p2 = bd[ray[j]]
                    if p2 != EMPTY:
                        if p2 in dscreen:
                            return True
                        break
                    j += 1
                break
            i += 1

    # Bent riders beyond the first step: elbow squares must be empty.
    for entries in EAGLE_ATT[t]:
        for s1, es in entries:
            if bd[s1] != EMPTY:
                break
            for e in es:
                if bd[e] == eagle:
                    return True
    for entries in RHINO_ATT[t]:
        for s1, es in entries:
            if bd[s1] != EMPTY:
                break
            for e in es:
                if bd[e] == rhino:
                    return True
    return False


# ---------------------------------------------------------------------------
# Pseudo-legal move generation.
# Move = (frm, to, promo, kind); promo: '' or resulting-piece lowercase letter.
# kind: 0 = normal, 1 = double step (sets e.p.), 2 = e.p. capture,
#       3 = king initial leap.
# ---------------------------------------------------------------------------


def _gen_slide(bd, white, frm, rays, add):
    for ray in rays:
        for s in ray:
            q = bd[s]
            if q == EMPTY:
                add((frm, s, "", 0))
            else:
                if q.isupper() != white:
                    add((frm, s, "", 0))
                break


def _gen_leap(bd, white, frm, dests, add):
    for s in dests:
        q = bd[s]
        if q == EMPTY or q.isupper() != white:
            add((frm, s, "", 0))


def _gen_leap_promo(bd, white, frm, dests, add, promo):
    last = 15 if white else 0
    for s in dests:
        q = bd[s]
        if q == EMPTY or q.isupper() != white:
            add((frm, s, promo if (s >> 4) == last else "", 0))


def _gen_screen(bd, white, frm, rays, add):
    """Cannon/Archer/Sorceress: slide without capture; capture over exactly
    one screen (any color), taking the first piece beyond it (spec 4.2)."""
    for ray in rays:
        ln = len(ray)
        i = 0
        while i < ln and bd[ray[i]] == EMPTY:
            add((frm, ray[i], "", 0))
            i += 1
        j = i + 1
        while j < ln:
            q = bd[ray[j]]
            if q != EMPTY:
                if q.isupper() != white:
                    add((frm, ray[j], "", 0))
                break
            j += 1


def _gen_bent(bd, white, frm, entries, add):
    """Eagle/Rhinoceros (spec 4.1)."""
    for s1, rays in entries:
        q = bd[s1]
        if q != EMPTY:
            if q.isupper() != white:
                add((frm, s1, "", 0))
            continue
        add((frm, s1, "", 0))
        for ray in rays:
            for s in ray:
                q2 = bd[s]
                if q2 == EMPTY:
                    add((frm, s, "", 0))
                else:
                    if q2.isupper() != white:
                        add((frm, s, "", 0))
                    break


def _gen_pawn(pos, bd, white, sq, add):
    fw = 16 if white else -16
    last = 15 if white else 0
    f = sq & 15
    one = sq + fw
    if 0 <= one < 256:
        if bd[one] == EMPTY:
            add((sq, one, "q" if (one >> 4) == last else "", 0))
            two = one + fw
            if 0 <= two < 256 and bd[two] == EMPTY:
                # Double step from ANY square (spec 6.1); may promote (rare).
                add((sq, two, "q" if (two >> 4) == last else "", 1))
        ep = pos.ep
        for df in (-1, 1):
            nf = f + df
            if 0 <= nf < 16:
                s = one + df
                q = bd[s]
                if q != EMPTY:
                    if q.isupper() != white:
                        add((sq, s, "q" if (s >> 4) == last else "", 0))
                elif ep is not None and s == ep:
                    cap = s - fw
                    qc = bd[cap]
                    if qc != EMPTY and qc.isupper() != white:
                        add((sq, s, "", 2))     # e.p. never promotes


def _gen_prince(pos, bd, white, sq, add):
    _gen_leap_promo(bd, white, sq, STEP_K[sq], add, "a")
    fw = 16 if white else -16
    last = 15 if white else 0
    one = sq + fw
    two = sq + 2 * fw
    if 0 <= one < 256 and 0 <= two < 256 and bd[one] == EMPTY \
            and bd[two] == EMPTY:
        add((sq, two, "a" if (two >> 4) == last else "", 1))


def _gen_troll(pos, bd, white, sq, add):
    # 3-jumps: move+capture, NEVER promote (spec 6.4).
    _gen_leap(bd, white, sq, TROLL_LEAP[sq], add)
    # Pawn steps: promote to Queen on last rank.  No double step, no e.p.
    fw = 16 if white else -16
    last = 15 if white else 0
    f = sq & 15
    one = sq + fw
    if 0 <= one < 256:
        if bd[one] == EMPTY:
            add((sq, one, "q" if (one >> 4) == last else "", 0))
        for df in (-1, 1):
            nf = f + df
            if 0 <= nf < 16:
                s = one + df
                q = bd[s]
                if q != EMPTY and q.isupper() != white:
                    add((sq, s, "q" if (s >> 4) == last else "", 0))


def _gen_king(pos, bd, white, sq, add):
    _gen_leap(bd, white, sq, STEP_K[sq], add)
    right = pos.leap_w if white else pos.leap_b
    if right and not attacked(bd, sq, not white):
        for dest, mids, is_knight in KING_LEAP[sq]:
            if bd[dest] != EMPTY:
                continue                      # destination must be free
            if is_knight:
                ok = False
                for m in mids:
                    if not attacked(bd, m, not white):
                        ok = True
                        break
                if not ok:
                    continue
            else:
                if attacked(bd, mids[0], not white):
                    continue
            add((sq, dest, "", 3))


def gen_pseudo(pos):
    bd = pos.board
    white = pos.white_to_move
    moves = []
    add = moves.append
    for sq in range(256):
        p = bd[sq]
        if p == EMPTY or p.isupper() != white:
            continue
        t = p.lower()
        if t == "p":
            _gen_pawn(pos, bd, white, sq, add)
        elif t == "q":
            _gen_slide(bd, white, sq, RAYS_O[sq], add)
            _gen_slide(bd, white, sq, RAYS_D[sq], add)
        elif t == "r":
            _gen_slide(bd, white, sq, RAYS_O[sq], add)
        elif t == "b":
            _gen_slide(bd, white, sq, RAYS_D[sq], add)
        elif t == "n":
            _gen_leap_promo(bd, white, sq, LEAP_N[sq], add, "f")
        elif t == "a":
            _gen_slide(bd, white, sq, RAYS_O[sq], add)
            _gen_slide(bd, white, sq, RAYS_D[sq], add)
            _gen_leap(bd, white, sq, LEAP_N[sq], add)
        elif t == "h":
            _gen_slide(bd, white, sq, RAYS_O[sq], add)
            _gen_leap(bd, white, sq, LEAP_N[sq], add)
        elif t == "x":
            _gen_slide(bd, white, sq, RAYS_D[sq], add)
            _gen_leap(bd, white, sq, LEAP_N[sq], add)
        elif t == "j":
            _gen_leap_promo(bd, white, sq, CENTAUR_T[sq], add, "l")
        elif t == "s":
            _gen_slide(bd, white, sq, RAYS_O[sq], add)
            _gen_leap(bd, white, sq, STEP_D[sq], add)
        elif t == "y":
            _gen_slide(bd, white, sq, RAYS_D[sq], add)
            _gen_leap(bd, white, sq, STEP_O[sq], add)
        elif t == "g":
            _gen_bent(bd, white, sq, EAGLE_MOVE[sq], add)
        elif t == "u":
            _gen_bent(bd, white, sq, RHINO_MOVE[sq], add)
        elif t == "l":
            _gen_leap(bd, white, sq, LION_T[sq], add)
        elif t == "m":
            _gen_leap_promo(bd, white, sq, LEAP_C[sq], add, "f")
        elif t == "z":
            _gen_leap_promo(bd, white, sq, LEAP_Z[sq], add, "f")
        elif t == "f":
            _gen_leap(bd, white, sq, BUFFALO_T[sq], add)
        elif t == "c":
            _gen_screen(bd, white, sq, RAYS_O[sq], add)
        elif t == "v":
            _gen_screen(bd, white, sq, RAYS_D[sq], add)
        elif t == "o":
            _gen_screen(bd, white, sq, RAYS_O[sq], add)
            _gen_screen(bd, white, sq, RAYS_D[sq], add)
        elif t == "d":
            _gen_leap(bd, white, sq, DUCHESS_T[sq], add)
        elif t == "w":
            _gen_leap_promo(bd, white, sq, MACHINE_T[sq], add, "l")
        elif t == "e":
            _gen_leap_promo(bd, white, sq, ELEPH_T[sq], add, "l")
        elif t == "i":
            _gen_prince(pos, bd, white, sq, add)
        elif t == "t":
            _gen_troll(pos, bd, white, sq, add)
        elif t == "k":
            _gen_king(pos, bd, white, sq, add)
        else:
            raise ValueError("unknown piece %r at %s" % (p, sq_name(sq)))
    return moves


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


def _apply_to_board(bd, mv, white):
    frm, to, promo, kind = mv
    nb = bd.copy()
    p = nb[frm]
    nb[frm] = EMPTY
    if kind == 2:                       # e.p.: victim sits behind the ep square
        fw = 16 if white else -16
        nb[to - fw] = EMPTY
    if promo:
        p = promo.upper() if white else promo
    nb[to] = p
    return nb


def _board_rows(bd):
    rows = []
    for r in range(15, -1, -1):
        row = []
        run = 0
        base = r * 16
        for f in range(16):
            p = bd[base + f]
            if p == EMPTY:
                run += 1
            else:
                if run:
                    row.append(str(run))
                    run = 0
                row.append(p)
        if run:
            row.append(str(run))
        rows.append("".join(row))
    return "/".join(rows)


def move_to_uci(mv):
    return sq_name(mv[0]) + sq_name(mv[1]) + mv[2]


class Position:
    __slots__ = ("board", "white_to_move", "leap_w", "leap_b", "ep",
                 "halfmove", "fullmove", "wk", "bk", "_legal_cache")

    # -- construction -------------------------------------------------------

    @classmethod
    def from_fen(cls, fen):
        parts = fen.split()
        if len(parts) != 6:
            raise ValueError("FEN must have 6 fields, got %d" % len(parts))
        board_s, turn, rights, ep_s, hm_s, fm_s = parts
        rows = board_s.split("/")
        if len(rows) != 16:
            raise ValueError("FEN board must have 16 ranks")
        bd = [EMPTY] * 256
        for i, row in enumerate(rows):
            r = 15 - i
            f = 0
            k = 0
            while k < len(row):
                ch = row[k]
                if ch.isdigit():
                    # Greedy 2-digit read for 10..16 when it fits the rank.
                    if (ch == "1" and k + 1 < len(row)
                            and row[k + 1].isdigit()):
                        v2 = int(row[k:k + 2])
                        if v2 <= 16 and f + v2 <= 16:
                            f += v2
                            k += 2
                            continue
                    v = int(ch)
                    if v == 0 or f + v > 16:
                        raise ValueError("bad empty run in rank %d" % (r + 1))
                    f += v
                    k += 1
                else:
                    if not ("a" <= ch.lower() <= "z"):
                        raise ValueError("bad piece letter %r" % ch)
                    if f >= 16:
                        raise ValueError("rank %d overflow" % (r + 1))
                    bd[r * 16 + f] = ch
                    f += 1
                    k += 1
            if f != 16:
                raise ValueError("rank %d has width %d" % (r + 1, f))
        if turn not in ("w", "b"):
            raise ValueError("bad turn %r" % turn)
        if rights == "-":
            lw = lb = False
        else:
            if not set(rights) <= {"K", "k"} or len(set(rights)) != len(rights):
                raise ValueError("bad king-leap rights %r" % rights)
            lw = "K" in rights
            lb = "k" in rights
        ep = None if ep_s == "-" else parse_sq(ep_s)
        pos = cls.__new__(cls)
        pos.board = bd
        pos.white_to_move = (turn == "w")
        pos.leap_w = lw
        pos.leap_b = lb
        pos.ep = ep
        pos.halfmove = int(hm_s)
        pos.fullmove = int(fm_s)
        pos.wk = bd.index("K") if "K" in bd else None
        pos.bk = bd.index("k") if "k" in bd else None
        pos._legal_cache = None
        return pos

    # -- FEN emission -------------------------------------------------------

    def _ep_capturable(self):
        """Canonical-emitter filter (spec 3.2): emit the e.p. square only if
        an enemy (side-to-move) Pawn stands ready to capture on it and the
        double-stepper is still there to be taken."""
        ep = self.ep
        if ep is None:
            return False
        white = self.white_to_move
        fw = 16 if white else -16
        pl = "P" if white else "p"
        base = ep - fw                  # victim square; capturers at base+-1
        if not 0 <= base < 256:
            return False
        victim = self.board[base]
        if victim == EMPTY or victim.isupper() == white:
            return False
        f = ep & 15
        if f > 0 and self.board[base - 1] == pl:
            return True
        if f < 15 and self.board[base + 1] == pl:
            return True
        return False

    def to_fen(self):
        rights = ("K" if self.leap_w else "") + ("k" if self.leap_b else "")
        ep_s = sq_name(self.ep) if self._ep_capturable() else "-"
        return "%s %s %s %s %d %d" % (
            _board_rows(self.board),
            "w" if self.white_to_move else "b",
            rights or "-",
            ep_s,
            self.halfmove,
            self.fullmove,
        )

    # -- legality -----------------------------------------------------------

    def _legal(self):
        if self._legal_cache is None:
            white = self.white_to_move
            bd = self.board
            res = []
            for mv in gen_pseudo(self):
                nb = _apply_to_board(bd, mv, white)
                k = self.wk if white else self.bk
                if k is not None:
                    if mv[0] == k:
                        k = mv[1]
                    if attacked(nb, k, not white):
                        continue
                res.append((move_to_uci(mv), mv))
            res.sort()
            self._legal_cache = res
        return self._legal_cache

    def legal_moves(self):
        return [u for u, _ in self._legal()]

    def in_check(self):
        k = self.wk if self.white_to_move else self.bk
        return k is not None and attacked(self.board, k,
                                          not self.white_to_move)

    def result(self):
        if self._legal():
            return "*"
        if self.in_check():
            return "0-1" if self.white_to_move else "1-0"
        return "1/2-1/2"

    # -- make ---------------------------------------------------------------

    def _apply(self, mv):
        frm, to, promo, kind = mv
        white = self.white_to_move
        bd = self.board
        p = bd[frm]
        captured = (bd[to] != EMPTY) or kind == 2
        nb = _apply_to_board(bd, mv, white)
        np_ = Position.__new__(Position)
        np_.board = nb
        np_.white_to_move = not white
        np_.leap_w = self.leap_w and p != "K"
        np_.leap_b = self.leap_b and p != "k"
        np_.ep = (frm + to) // 2 if kind == 1 else None
        # Spec 7.4: reset only on capture or a move of a piece of type Pawn.
        np_.halfmove = 0 if (captured or p in ("P", "p")) \
            else self.halfmove + 1
        np_.fullmove = self.fullmove + (0 if white else 1)
        np_.wk = to if p == "K" else self.wk
        np_.bk = to if p == "k" else self.bk
        np_._legal_cache = None
        return np_

    def apply(self, uci):
        for u, mv in self._legal():
            if u == uci:
                return self._apply(mv)
        raise ValueError("illegal move %r in %r" % (uci, self.to_fen()))


def perft(pos, depth):
    if depth <= 0:
        return 1
    legal = pos._legal()
    if depth == 1:
        return len(legal)
    total = 0
    for _, mv in legal:
        total += perft(pos._apply(mv), depth - 1)
    return total


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


def _mk_fen(pieces, turn="w", rights="-", ep="-", hm=0, fm=1):
    bd = [EMPTY] * 256
    for name, letter in pieces.items():
        bd[parse_sq(name)] = letter
    return "%s %s %s %s %d %d" % (_board_rows(bd), turn, rights, ep, hm, fm)


def selftest():
    ok = []

    # 1. Start position round-trip and inventory.
    p0 = Position.from_fen(START_FEN)
    assert p0.to_fen() == START_FEN, "startpos FEN round-trip failed"
    wcount = sum(1 for x in p0.board if x != EMPTY and x.isupper())
    bcount = sum(1 for x in p0.board if x != EMPTY and x.islower())
    assert wcount == 64 and bcount == 64, "startpos must have 64+64 pieces"
    assert p0.wk == parse_sq("h2") and p0.bk == parse_sq("h15"), "king squares"
    for letter in "abcdefghijlmnoqrstuvwxyz":
        n = sum(1 for x in p0.board if x == letter.upper())
        want = 1 if letter == "a" else 16 if letter == "p" else 2
        assert n == want, "piece count %s: %d != %d" % (letter, n, want)
    assert sum(1 for x in p0.board if x == "K") == 1
    ok.append("startpos")

    # 2. Legal moves: sorted, well-formed, mirror-symmetric.
    mw = p0.legal_moves()
    assert mw == sorted(mw), "moves must be sorted"
    assert all(MOVE_RE.match(u) for u in mw), "malformed UCI move"
    pb = Position.from_fen(START_FEN.replace(" w ", " b "))
    assert len(mw) == len(pb.legal_moves()), "start position not symmetric"
    ok.append("symmetry")

    # 3. Perft anchors (verified against hand analysis of the spec setup).
    assert perft(p0, 1) == 54, "perft(1) != 54: %d" % perft(p0, 1)
    assert perft(p0, 2) == 2916, "perft(2) != 2916"
    ok.append("perft-anchors")

    # 4. Eagle geometry on an open board: 4 x (1 + two away-rays) = 56.
    pe = Position.from_fen(_mk_fen(
        {"h8": "G", "a1": "K", "p16": "k"}))
    egm = [u for u in pe.legal_moves() if u.startswith("h8")]
    assert len(egm) == 56, "eagle open-board moves: %d != 56" % len(egm)
    assert "h8i9" in egm and "h8i16" in egm and "h8p9" in egm
    assert "h8h9" not in egm and "h8p16" not in egm
    ok.append("eagle")

    # 5. Rhinoceros geometry on an open board: also 56.
    pu = Position.from_fen(_mk_fen(
        {"h8": "U", "a1": "K", "p16": "k"}))
    rgm = [u for u in pu.legal_moves() if u.startswith("h8")]
    assert len(rgm) == 56, "rhino open-board moves: %d != 56" % len(rgm)
    assert "h8h9" in rgm and "h8a16" in rgm and "h8p15" in rgm
    assert "h8i9" not in rgm
    ok.append("rhino")

    # 6. King initial leap (spec 6.3).
    pk = Position.from_fen(_mk_fen(
        {"h2": "K", "h15": "k"}, rights="Kk"))
    mk = pk.legal_moves()
    assert len(mk) == 19, "kings-only white moves: %d != 19" % len(mk)
    for u in ("h2j3", "h2f2", "h2h4", "h2g4", "h2j1"):
        assert u in mk, "missing king leap %s" % u
    # Right is lost after the leap.
    assert " k " in pk.apply("h2j3").to_fen()
    # No right -> steps only.
    pk2 = Position.from_fen(_mk_fen({"h2": "K", "h15": "k"}))
    assert len(pk2.legal_moves()) == 8
    # Threatened intermediates: black rook on g16 sweeps the g-file.
    pk3 = Position.from_fen(_mk_fen(
        {"h2": "K", "h15": "k", "g16": "r"}, rights="Kk"))
    mk3 = pk3.legal_moves()
    assert len(mk3) == 11, "king vs rook g16: %d != 11" % len(mk3)
    for u in ("h2j2", "h2h4", "h2j4", "h2i4", "h2j3", "h2j1"):
        assert u in mk3, "leap %s should be legal" % u
    for u in ("h2f2", "h2f4", "h2g4", "h2f3", "h2f1", "h2g2", "h2g1", "h2g3"):
        assert u not in mk3, "move %s should be illegal" % u
    ok.append("king-leap")

    # 7. En passant (spec 6.2): pawn double, prince double, prince cannot ep.
    pa = Position.from_fen(_mk_fen(
        {"d8": "P", "e10": "p", "h2": "K", "h15": "k"}, hm=7))
    pa1 = pa.apply("d8d10")
    assert pa1.ep == parse_sq("d9"), "ep square after pawn double"
    assert pa1.halfmove == 0, "pawn move must reset halfmove clock"
    assert " d9 " in pa1.to_fen(), "canonical FEN must emit capturable ep"
    assert "e10d9" in pa1.legal_moves()
    pa2 = pa1.apply("e10d9")
    assert pa2.board[parse_sq("d10")] == EMPTY, "ep victim not removed"
    assert pa2.board[parse_sq("d9")] == "p"
    assert pa2.halfmove == 0
    # ep evaporates after any other reply.
    assert pa1.apply("e10e9").ep is None
    # Prince double step is ep-capturable by a pawn.
    pi = Position.from_fen(_mk_fen(
        {"d8": "I", "e10": "p", "h2": "K", "h15": "k"}, hm=7))
    pi1 = pi.apply("d8d10")
    assert pi1.ep == parse_sq("d9")
    assert pi1.halfmove == 8, "prince move must NOT reset halfmove clock"
    pi2 = pi1.apply("e10d9")
    assert pi2.board[parse_sq("d10")] == EMPTY, "prince not captured ep"
    # A prince may NOT capture en passant.
    pj = Position.from_fen(_mk_fen(
        {"d8": "P", "e10": "i", "h2": "K", "h15": "k"}))
    pj1 = pj.apply("d8d10")
    assert " - " in pj1.to_fen().replace("w", "").replace("b", ""), \
        "no pawn can capture -> canonical FEN omits ep"
    pj2 = pj1.apply("e10d9")        # ordinary prince step, not a capture
    assert pj2.board[parse_sq("d10")] == "P", "prince must not ep-capture"
    ok.append("en-passant")

    # 8. Troll promotion depends on the arriving move type (spec 6.4).
    pt = Position.from_fen(_mk_fen(
        {"g13": "T", "h16": "n", "a1": "K", "p16": "k"}))
    mt = pt.legal_moves()
    assert "g13g16" in mt and "g13g16q" not in mt, "3-jump must not promote"
    assert pt.apply("g13g16").board[parse_sq("g16")] == "T"
    pt2 = Position.from_fen(_mk_fen(
        {"g15": "T", "h16": "n", "a1": "K", "p16": "k"}))
    mt2 = pt2.legal_moves()
    assert "g15g16q" in mt2 and "g15g16" not in mt2, "pawn step must promote"
    assert "g15h16q" in mt2, "pawn capture to last rank must promote"
    assert pt2.apply("g15g16q").board[parse_sq("g16")] == "Q"
    assert pt2.apply("g15h16q").board[parse_sq("h16")] == "Q"
    ok.append("troll-promo")

    # 9. Other promotions: forced, suffix always emitted.
    pn = Position.from_fen(_mk_fen(
        {"g14": "N", "a1": "K", "p1": "k"}))
    mn = pn.legal_moves()
    assert "g14h16f" in mn and "g14h16" not in mn, "knight promotes to Buffalo"
    assert pn.apply("g14h16f").board[parse_sq("h16")] == "F"
    pw = Position.from_fen(_mk_fen(
        {"g14": "W", "a1": "K", "p1": "k"}))
    assert "g14g16l" in pw.legal_moves(), "machine promotes to Lion"
    pp = Position.from_fen(_mk_fen(
        {"g15": "P", "a1": "K", "p1": "k"}))
    assert "g15g16q" in pp.legal_moves(), "pawn promotes to Queen"
    pq = Position.from_fen(_mk_fen(
        {"g15": "I", "a1": "K", "p1": "k"}))
    assert "g15g16a" in pq.legal_moves(), "prince promotes to Amazon"
    # Pawn double step onto the last rank promotes and still marks ep.
    pd = Position.from_fen(_mk_fen(
        {"g14": "P", "a1": "K", "p1": "k"}))
    pd1 = pd.apply("g14g16q")
    assert pd1.board[parse_sq("g16")] == "Q"
    assert pd1.ep == parse_sq("g15")
    ok.append("promotions")

    # 10. Cannon screens (spec 4.2).
    pc = Position.from_fen(_mk_fen(
        {"a1": "C", "a5": "P", "a9": "r", "h2": "K", "h15": "k"}))
    mc = [u for u in pc.legal_moves() if u.startswith("a1")]
    assert "a1a9" in mc, "cannon screen capture missing"
    assert "a1a4" in mc and "a1p1" in mc
    for u in ("a1a5", "a1a6", "a1a8", "a1a10", "a1a12"):
        assert u not in mc, "cannon move %s should be illegal" % u
    # Screen check detection (also covers Sorceress/Archer code path).
    pchk = Position.from_fen(_mk_fen(
        {"a16": "k", "a1": "C", "a9": "P", "h2": "K"}, turn="b"))
    assert pchk.in_check(), "cannon check through one screen"
    assert len(pchk.legal_moves()) == 2, "king must step off the a-file"
    pchk2 = Position.from_fen(_mk_fen(
        {"a16": "k", "a1": "C", "a9": "P", "a5": "P", "h2": "K"}, turn="b"))
    assert not pchk2.in_check(), "two screens must block the cannon"
    ok.append("cannon")

    # 11. Results: mate, stalemate, ongoing.
    rmate = Position.from_fen(_mk_fen(
        {"a16": "k", "a1": "R", "b14": "Q", "h2": "K"}, turn="b"))
    assert rmate.result() == "1-0", "back-rank mate not detected"
    rstale = Position.from_fen(_mk_fen(
        {"a16": "k", "c15": "Q", "h2": "K"}, turn="b"))
    assert rstale.result() == "1/2-1/2", "stalemate not detected"
    assert p0.result() == "*"
    ok.append("results")

    # 12. apply() contract.
    p1 = p0.apply("a4a6")
    assert Position.from_fen(p1.to_fen()).to_fen() == p1.to_fen()
    try:
        p0.apply("a4a7")
    except ValueError:
        pass
    else:
        raise AssertionError("illegal move must raise ValueError")
    ok.append("apply")

    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fen_from_args(args):
    return " ".join(args) if args else START_FEN


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    cmd = argv[1]
    try:
        if cmd == "selftest":
            names = selftest()
            print("selftest OK (%s)" % ", ".join(names))
            return 0
        if cmd in ("perft", "divide"):
            depth = int(argv[2])
            pos = Position.from_fen(_fen_from_args(argv[3:]))
            if cmd == "perft":
                print(perft(pos, depth))
            else:
                total = 0
                for u, mv in pos._legal():
                    n = perft(pos._apply(mv), depth - 1)
                    total += n
                    print("%s %d" % (u, n))
                print("total %d" % total)
            return 0
        if cmd == "moves":
            pos = Position.from_fen(_fen_from_args(argv[2:]))
            for u in pos.legal_moves():
                print(u)
            return 0
        if cmd == "fen":
            pos = Position.from_fen(_fen_from_args(argv[2:]))
            print(pos.to_fen())
            return 0
        if cmd == "apply":
            args = argv[2:]
            idx = len(args)
            while idx > 0 and MOVE_RE.match(args[idx - 1]):
                idx -= 1
            if idx == len(args):
                raise ValueError("apply needs at least one move")
            pos = Position.from_fen(_fen_from_args(args[:idx]))
            for u in args[idx:]:
                pos = pos.apply(u)
            print(pos.to_fen())
            return 0
    except (ValueError, IndexError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    sys.stderr.write("unknown command %r\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
