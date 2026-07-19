#!/usr/bin/env python3
"""Terachess II rules oracle - implementation B (bitboards on Python ints).

Independent oracle for differential testing. Sole rules source: TERACHESS_SPEC.md.
Board 16x16; square index sq = rank*16 + file (a1=0, p1=15, a16=240, p16=255).
Bitboards: one 256-bit Python int per color and one per piece type.
Pure stdlib, deterministic, copy-make.
"""
import sys

WHITE, BLACK = 0, 1
NSQ = 256

START_FEN = ("sjyhxfdoodfxhyjs/cmztuvlkalvutzmc/ernbwigqqgiwbnre/"
             "pppppppppppppppp/16/16/16/16/16/16/16/16/"
             "PPPPPPPPPPPPPPPP/ERNBWIGQQGIWBNRE/CMZTUVLKALVUTZMC/"
             "SJYHXFDOODFXHYJS w Kk - 0 1")

LETTERS = "abcdefghijklmnopqrstuvwxyz"
TI = {c: i for i, c in enumerate(LETTERS)}
(T_AMAZON, T_BISHOP, T_CANNON, T_DUCHESS, T_ELEPHANT, T_BUFFALO, T_EAGLE,
 T_MARSHALL, T_PRINCE, T_CENTAUR, T_KING, T_LION, T_CAMEL, T_KNIGHT,
 T_SORCERESS, T_PAWN, T_QUEEN, T_ROOK, T_ADMIRAL, T_TROLL, T_RHINO,
 T_ARCHER, T_MACHINE, T_CARDINAL, T_MISSIONARY, T_GIRAFFE) = range(26)


def sq_name(sq):
    return chr(ord('a') + (sq & 15)) + str((sq >> 4) + 1)


def parse_sq(s):
    f = ord(s[0]) - ord('a')
    r = int(s[1:]) - 1
    if not (0 <= f < 16 and 0 <= r < 16):
        raise ValueError("bad square: " + s)
    return r * 16 + f


ORTH_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAG_DIRS = ((1, 1), (1, -1), (-1, 1), (-1, -1))


def _sym(pairs):
    out = set()
    for m, n in pairs:
        for a, b in ((m, n), (n, m)):
            for sa in (a, -a):
                for sb in (b, -b):
                    out.add((sa, sb))
    out.discard((0, 0))
    return sorted(out)


KING_O = _sym([(0, 1), (1, 1)])
FERZ_O = _sym([(1, 1)])
WAZIR_O = _sym([(0, 1)])
KNIGHT_O = _sym([(1, 2)])
CAMEL_O = _sym([(1, 3)])
ZEBRA_O = _sym([(2, 3)])
DAB_O = _sym([(0, 2)])
ALFIL_O = _sym([(2, 2)])
G3_O = _sym([(3, 3)])
H3_O = _sym([(0, 3)])
LION_O = sorted(set(KING_O) | set(DAB_O) | set(ALFIL_O) | set(KNIGHT_O))
DUCHESS_O = sorted(set(KING_O) | set(DAB_O) | set(ALFIL_O) | set(G3_O) | set(H3_O))
MACHINE_O = sorted(set(WAZIR_O) | set(DAB_O))
ELEPH_O = sorted(set(FERZ_O) | set(ALFIL_O))
CENTAUR_O = sorted(set(KING_O) | set(KNIGHT_O))
BUFFALO_O = sorted(set(KNIGHT_O) | set(CAMEL_O) | set(ZEBRA_O))
TROLLJ_O = sorted(set(G3_O) | set(H3_O))
KLEAP_O = sorted(set(DAB_O) | set(ALFIL_O) | set(KNIGHT_O))


def _mask(offsets):
    tbl = []
    for sq in range(NSQ):
        f, r = sq & 15, sq >> 4
        m = 0
        for dx, dy in offsets:
            nf, nr = f + dx, r + dy
            if 0 <= nf < 16 and 0 <= nr < 16:
                m |= 1 << (nr * 16 + nf)
        tbl.append(m)
    return tbl


KING_M = _mask(KING_O)
FERZ_M = _mask(FERZ_O)
WAZIR_M = _mask(WAZIR_O)
KNIGHT_M = _mask(KNIGHT_O)
CAMEL_M = _mask(CAMEL_O)
ZEBRA_M = _mask(ZEBRA_O)
DAB_M = _mask(DAB_O)
ALFIL_M = _mask(ALFIL_O)
G3_M = _mask(G3_O)
H3_M = _mask(H3_O)
LION_M = _mask(LION_O)
DUCHESS_M = _mask(DUCHESS_O)
MACHINE_M = _mask(MACHINE_O)
ELEPH_M = _mask(ELEPH_O)
CENTAUR_M = _mask(CENTAUR_O)
BUFFALO_M = _mask(BUFFALO_O)
TROLLJ_M = _mask(TROLLJ_O)

# PAWN_FROM_M[c][sq]: squares from which a pawn (or troll pawn-capture) of
# color c attacks sq (the attacker sits diagonally *behind* sq from c's view).
PAWN_FROM_M = (_mask([(-1, -1), (1, -1)]), _mask([(-1, 1), (1, 1)]))


def _rays(dirs):
    tbl = []
    for sq in range(NSQ):
        f, r = sq & 15, sq >> 4
        rr = []
        for dx, dy in dirs:
            ray = []
            nf, nr = f + dx, r + dy
            while 0 <= nf < 16 and 0 <= nr < 16:
                ray.append(nr * 16 + nf)
                nf += dx
                nr += dy
            if ray:
                rr.append(tuple(ray))
        tbl.append(tuple(rr))
    return tbl


RAYS_O = _rays(ORTH_DIRS)
RAYS_D = _rays(DIAG_DIRS)


def bits(bb):
    while bb:
        lsb = bb & -bb
        yield lsb.bit_length() - 1
        bb ^= lsb


# Generic movers: (type, ray_kind, leap_mask_table). ray_kind: 'o','d','8',None.
GEN_TABLE = (
    (T_QUEEN, '8', None), (T_ROOK, 'o', None), (T_BISHOP, 'd', None),
    (T_AMAZON, '8', KNIGHT_M), (T_MARSHALL, 'o', KNIGHT_M),
    (T_CARDINAL, 'd', KNIGHT_M), (T_ADMIRAL, 'o', FERZ_M),
    (T_MISSIONARY, 'd', WAZIR_M), (T_KNIGHT, None, KNIGHT_M),
    (T_CAMEL, None, CAMEL_M), (T_GIRAFFE, None, ZEBRA_M),
    (T_BUFFALO, None, BUFFALO_M), (T_LION, None, LION_M),
    (T_DUCHESS, None, DUCHESS_M), (T_MACHINE, None, MACHINE_M),
    (T_ELEPHANT, None, ELEPH_M), (T_CENTAUR, None, CENTAUR_M),
)

# Forced promotions on reaching the last rank (spec 6.4).
# Pawn, Prince and Troll are handled in their dedicated generators.
PROMO_MAP = {T_KNIGHT: T_BUFFALO, T_CAMEL: T_BUFFALO, T_GIRAFFE: T_BUFFALO,
             T_ELEPHANT: T_LION, T_MACHINE: T_LION, T_CENTAUR: T_LION}


def _split_digits(run, single):
    out = []
    k = 0
    while k < len(run):
        if not single and k + 1 < len(run) and int(run[k:k + 2]) <= 16:
            out.append(int(run[k:k + 2]))
            k += 2
        else:
            n = int(run[k])
            if n == 0:
                raise ValueError("zero empty-run in FEN rank")
            out.append(n)
            k += 1
    return out


def _parse_row_mode(row, single):
    cells = []
    i = 0
    while i < len(row):
        ch = row[i]
        if ch.isdigit():
            j = i
            while j < len(row) and row[j].isdigit():
                j += 1
            cells.extend(_split_digits(row[i:j], single))
            i = j
        else:
            cells.append(ch)
            i += 1
    return cells


def _parse_row(row):
    # Greedy two-digit numbers first (canonical "16", "12"...), then a
    # digit-by-digit fallback so additive splits like "88" (=16) also parse.
    for single in (False, True):
        try:
            cells = _parse_row_mode(row, single)
        except ValueError:
            continue
        width = sum(c if isinstance(c, int) else 1 for c in cells)
        if width == 16:
            return cells
    raise ValueError("rank does not span 16 files: " + row)


def move_uci(mv):
    frm, to, _kind, promo = mv
    s = sq_name(frm) + sq_name(to)
    if promo >= 0:
        s += LETTERS[promo]
    return s


class Position:
    __slots__ = ('occ', 'tbb', 'mb', 'side', 'leap', 'ep', 'hm', 'fm')

    @classmethod
    def from_fen(cls, fen):
        parts = fen.split()
        if len(parts) != 6:
            raise ValueError("FEN needs 6 fields: " + fen)
        rows = parts[0].split('/')
        if len(rows) != 16:
            raise ValueError("FEN needs 16 ranks")
        p = cls.__new__(cls)
        p.occ = [0, 0]
        p.tbb = [0] * 26
        p.mb = [-1] * NSQ
        for i, row in enumerate(rows):
            r = 15 - i
            f = 0
            for cell in _parse_row(row):
                if isinstance(cell, int):
                    f += cell
                else:
                    t = TI.get(cell.lower())
                    if t is None or f >= 16:
                        raise ValueError("bad rank: " + row)
                    c = WHITE if cell.isupper() else BLACK
                    sq = r * 16 + f
                    b = 1 << sq
                    p.occ[c] |= b
                    p.tbb[t] |= b
                    p.mb[sq] = t
                    f += 1
        if parts[1] not in ('w', 'b'):
            raise ValueError("bad side: " + parts[1])
        p.side = WHITE if parts[1] == 'w' else BLACK
        p.leap = ('K' in parts[2], 'k' in parts[2])
        p.ep = -1 if parts[3] == '-' else parse_sq(parts[3])
        p.hm = int(parts[4])
        p.fm = int(parts[5])
        return p

    def to_fen(self):
        rows = []
        for r in range(15, -1, -1):
            row = []
            run = 0
            for f in range(16):
                t = self.mb[r * 16 + f]
                if t < 0:
                    run += 1
                    continue
                if run:
                    row.append(str(run))
                    run = 0
                ch = LETTERS[t]
                if (self.occ[WHITE] >> (r * 16 + f)) & 1:
                    ch = ch.upper()
                row.append(ch)
            if run:
                row.append(str(run))
            rows.append(''.join(row))
        leap = ('K' if self.leap[WHITE] else '') + ('k' if self.leap[BLACK] else '')
        ep = '-'
        if self.ep >= 0:
            vict = self.ep - 16 if self.side == WHITE else self.ep + 16
            if (0 <= vict < NSQ
                    and (self.occ[1 - self.side] >> vict) & 1
                    and PAWN_FROM_M[self.side][self.ep]
                    & self.occ[self.side] & self.tbb[T_PAWN]):
                ep = sq_name(self.ep)
        return ' '.join(['/'.join(rows), 'wb'[self.side], leap or '-', ep,
                         str(self.hm), str(self.fm)])

    # ---- attack detection: pseudo-legal captures only (spec section 5) ----

    def attacked_by(self, sq, c):
        occ_c = self.occ[c]
        tb = self.tbb
        if KNIGHT_M[sq] & occ_c & (tb[T_KNIGHT] | tb[T_AMAZON] | tb[T_MARSHALL]
                                   | tb[T_CARDINAL] | tb[T_CENTAUR]
                                   | tb[T_BUFFALO] | tb[T_LION]):
            return True
        if CAMEL_M[sq] & occ_c & (tb[T_CAMEL] | tb[T_BUFFALO]):
            return True
        if ZEBRA_M[sq] & occ_c & (tb[T_GIRAFFE] | tb[T_BUFFALO]):
            return True
        if KING_M[sq] & occ_c & (tb[T_KING] | tb[T_PRINCE] | tb[T_CENTAUR]
                                 | tb[T_DUCHESS] | tb[T_LION]):
            return True
        if FERZ_M[sq] & occ_c & (tb[T_ADMIRAL] | tb[T_ELEPHANT] | tb[T_EAGLE]):
            return True
        if WAZIR_M[sq] & occ_c & (tb[T_MISSIONARY] | tb[T_MACHINE] | tb[T_RHINO]):
            return True
        if DAB_M[sq] & occ_c & (tb[T_MACHINE] | tb[T_LION] | tb[T_DUCHESS]):
            return True
        if ALFIL_M[sq] & occ_c & (tb[T_ELEPHANT] | tb[T_LION] | tb[T_DUCHESS]):
            return True
        if G3_M[sq] & occ_c & (tb[T_TROLL] | tb[T_DUCHESS]):
            return True
        if H3_M[sq] & occ_c & (tb[T_TROLL] | tb[T_DUCHESS]):
            return True
        if PAWN_FROM_M[c][sq] & occ_c & (tb[T_PAWN] | tb[T_TROLL]):
            return True
        occ_all = self.occ[0] | self.occ[1]
        oa = occ_c & (tb[T_QUEEN] | tb[T_ROOK] | tb[T_MARSHALL]
                      | tb[T_ADMIRAL] | tb[T_AMAZON])
        osc = occ_c & (tb[T_CANNON] | tb[T_SORCERESS])
        for ray in RAYS_O[sq]:
            first = False
            for t in ray:
                b = 1 << t
                if occ_all & b:
                    if not first:
                        if oa & b:
                            return True
                        first = True
                    else:
                        if osc & b:
                            return True
                        break
        da = occ_c & (tb[T_QUEEN] | tb[T_BISHOP] | tb[T_CARDINAL]
                      | tb[T_MISSIONARY] | tb[T_AMAZON])
        dsc = occ_c & (tb[T_ARCHER] | tb[T_SORCERESS])
        for ray in RAYS_D[sq]:
            first = False
            for t in ray:
                b = 1 << t
                if occ_all & b:
                    if not first:
                        if da & b:
                            return True
                        first = True
                    else:
                        if dsc & b:
                            return True
                        break
        eag = occ_c & tb[T_EAGLE]
        if eag:
            f0, r0 = sq & 15, sq >> 4
            for dx, dy in ORTH_DIRS:
                nf, nr = f0 - dx, r0 - dy
                while 0 <= nf < 16 and 0 <= nr < 16:
                    if (occ_all >> (nr * 16 + nf)) & 1:
                        break
                    dds = ((dx, 1), (dx, -1)) if dx else ((1, dy), (-1, dy))
                    for ex, ey in dds:
                        ef, er = nf - ex, nr - ey
                        if 0 <= ef < 16 and 0 <= er < 16 and (eag >> (er * 16 + ef)) & 1:
                            return True
                    nf -= dx
                    nr -= dy
        rhi = occ_c & tb[T_RHINO]
        if rhi:
            f0, r0 = sq & 15, sq >> 4
            for dx, dy in DIAG_DIRS:
                nf, nr = f0 - dx, r0 - dy
                while 0 <= nf < 16 and 0 <= nr < 16:
                    if (occ_all >> (nr * 16 + nf)) & 1:
                        break
                    for ex, ey in ((dx, 0), (0, dy)):
                        ef, er = nf - ex, nr - ey
                        if 0 <= ef < 16 and 0 <= er < 16 and (rhi >> (er * 16 + ef)) & 1:
                            return True
                    nf -= dx
                    nr -= dy
        return False

    def _king_attacked(self, c):
        kb = self.tbb[T_KING] & self.occ[c]
        for sq in bits(kb):
            if self.attacked_by(sq, 1 - c):
                return True
        return False

    def in_check(self):
        return self._king_attacked(self.side)

    # ---- move generation ----
    # Move = (frm, to, kind, promo). kind: 0 normal, 1 double step (sets ep),
    # 2 en-passant capture, 3 king leap. promo: resulting type index or -1.

    def pseudo_moves(self):
        mvs = []
        me = self.side
        opp = 1 - me
        own = self.occ[me]
        other = self.occ[opp]
        occ_all = own | other
        tb = self.tbb
        fwd = 16 if me == WHITE else -16
        last = 15 if me == WHITE else 0

        for t, rk, lt in GEN_TABLE:
            bb = tb[t] & own
            if not bb:
                continue
            pm = PROMO_MAP.get(t, -1)
            for frm in bits(bb):
                if lt is not None:
                    for to in bits(lt[frm] & ~own):
                        promo = pm if (pm >= 0 and (to >> 4) == last) else -1
                        mvs.append((frm, to, 0, promo))
                if rk:
                    if rk == 'o':
                        raysets = (RAYS_O[frm],)
                    elif rk == 'd':
                        raysets = (RAYS_D[frm],)
                    else:
                        raysets = (RAYS_O[frm], RAYS_D[frm])
                    for rays in raysets:
                        for ray in rays:
                            for to in ray:
                                b = 1 << to
                                if own & b:
                                    break
                                mvs.append((frm, to, 0, -1))
                                if other & b:
                                    break

        for frm in bits(tb[T_PAWN] & own):
            f = frm & 15
            one = frm + fwd
            if 0 <= one < NSQ and not (occ_all >> one) & 1:
                mvs.append((frm, one, 0, T_QUEEN if (one >> 4) == last else -1))
                two = one + fwd
                if 0 <= two < NSQ and not (occ_all >> two) & 1:
                    mvs.append((frm, two, 1, T_QUEEN if (two >> 4) == last else -1))
            for df in (-1, 1):
                nf = f + df
                if not 0 <= nf < 16:
                    continue
                to = frm + fwd + df
                if not 0 <= to < NSQ:
                    continue
                if (other >> to) & 1:
                    mvs.append((frm, to, 0, T_QUEEN if (to >> 4) == last else -1))
                elif to == self.ep:
                    vict = self.ep - 16 if me == WHITE else self.ep + 16
                    if 0 <= vict < NSQ and (other >> vict) & 1:
                        mvs.append((frm, to, 2, -1))

        for frm in bits(tb[T_PRINCE] & own):
            for to in bits(KING_M[frm] & ~own):
                mvs.append((frm, to, 0, T_AMAZON if (to >> 4) == last else -1))
            one = frm + fwd
            if 0 <= one < NSQ and not (occ_all >> one) & 1:
                two = one + fwd
                if 0 <= two < NSQ and not (occ_all >> two) & 1:
                    mvs.append((frm, two, 1, T_AMAZON if (two >> 4) == last else -1))

        for frm in bits(tb[T_TROLL] & own):
            for to in bits(TROLLJ_M[frm] & ~own):
                mvs.append((frm, to, 0, -1))  # jump arrival never promotes
            f = frm & 15
            one = frm + fwd
            if 0 <= one < NSQ and not (occ_all >> one) & 1:
                mvs.append((frm, one, 0, T_QUEEN if (one >> 4) == last else -1))
            for df in (-1, 1):
                nf = f + df
                if not 0 <= nf < 16:
                    continue
                to = frm + fwd + df
                if 0 <= to < NSQ and (other >> to) & 1:
                    mvs.append((frm, to, 0, T_QUEEN if (to >> 4) == last else -1))

        for t, raysel in ((T_CANNON, 'o'), (T_ARCHER, 'd'), (T_SORCERESS, '8')):
            bb = tb[t] & own
            if not bb:
                continue
            for frm in bits(bb):
                if raysel == 'o':
                    rays_all = RAYS_O[frm]
                elif raysel == 'd':
                    rays_all = RAYS_D[frm]
                else:
                    rays_all = RAYS_O[frm] + RAYS_D[frm]
                for ray in rays_all:
                    n = len(ray)
                    i = 0
                    while i < n and not (occ_all >> ray[i]) & 1:
                        mvs.append((frm, ray[i], 0, -1))
                        i += 1
                    i += 1  # skip the screen
                    while i < n:
                        b = 1 << ray[i]
                        if occ_all & b:
                            if other & b:
                                mvs.append((frm, ray[i], 0, -1))
                            break
                        i += 1

        for frm in bits(tb[T_EAGLE] & own):
            f0, r0 = frm & 15, frm >> 4
            for dx, dy in DIAG_DIRS:
                nf, nr = f0 + dx, r0 + dy
                if not (0 <= nf < 16 and 0 <= nr < 16):
                    continue
                s1 = nr * 16 + nf
                b = 1 << s1
                if own & b:
                    continue
                mvs.append((frm, s1, 0, -1))
                if other & b:
                    continue
                for ox, oy in ((dx, 0), (0, dy)):
                    tf, tr = nf + ox, nr + oy
                    while 0 <= tf < 16 and 0 <= tr < 16:
                        to = tr * 16 + tf
                        b2 = 1 << to
                        if own & b2:
                            break
                        mvs.append((frm, to, 0, -1))
                        if other & b2:
                            break
                        tf += ox
                        tr += oy

        for frm in bits(tb[T_RHINO] & own):
            f0, r0 = frm & 15, frm >> 4
            for dx, dy in ORTH_DIRS:
                nf, nr = f0 + dx, r0 + dy
                if not (0 <= nf < 16 and 0 <= nr < 16):
                    continue
                s1 = nr * 16 + nf
                b = 1 << s1
                if own & b:
                    continue
                mvs.append((frm, s1, 0, -1))
                if other & b:
                    continue
                conts = ((dx, 1), (dx, -1)) if dx else ((1, dy), (-1, dy))
                for ox, oy in conts:
                    tf, tr = nf + ox, nr + oy
                    while 0 <= tf < 16 and 0 <= tr < 16:
                        to = tr * 16 + tf
                        b2 = 1 << to
                        if own & b2:
                            break
                        mvs.append((frm, to, 0, -1))
                        if other & b2:
                            break
                        tf += ox
                        tr += oy

        for frm in bits(tb[T_KING] & own):
            for to in bits(KING_M[frm] & ~own):
                mvs.append((frm, to, 0, -1))
            if self.leap[me] and not self.attacked_by(frm, opp):
                f0, r0 = frm & 15, frm >> 4
                for dx, dy in KLEAP_O:
                    nf, nr = f0 + dx, r0 + dy
                    if not (0 <= nf < 16 and 0 <= nr < 16):
                        continue
                    to = nr * 16 + nf
                    if (occ_all >> to) & 1:
                        continue  # destination must be empty
                    ax = (dx > 0) - (dx < 0)
                    ay = (dy > 0) - (dy < 0)
                    if abs(dx) != 1 and abs(dy) != 1:
                        mid = (r0 + ay) * 16 + (f0 + ax)
                        if self.attacked_by(mid, opp):
                            continue
                    else:
                        if abs(dx) == 2:
                            i1 = r0 * 16 + (f0 + ax)
                            i2 = (r0 + ay) * 16 + (f0 + ax)
                        else:
                            i1 = (r0 + ay) * 16 + f0
                            i2 = (r0 + ay) * 16 + (f0 + ax)
                        if self.attacked_by(i1, opp) and self.attacked_by(i2, opp):
                            continue
                    mvs.append((frm, to, 3, -1))
        return mvs

    # ---- make / legality / API ----

    def apply_move(self, mv):
        frm, to, kind, promo = mv
        p = Position.__new__(Position)
        occw, occb = self.occ
        tbb = self.tbb[:]
        mb = self.mb[:]
        me = self.side
        fb = 1 << frm
        tob = 1 << to
        mtype = mb[frm]
        cap = False
        if kind == 2:  # en passant: victim sits behind the ep square
            vict = self.ep - 16 if me == WHITE else self.ep + 16
            vb = 1 << vict
            tbb[mb[vict]] &= ~vb
            if me == WHITE:
                occb &= ~vb
            else:
                occw &= ~vb
            mb[vict] = -1
            cap = True
        elif (occw | occb) & tob:
            tbb[mb[to]] &= ~tob
            if me == WHITE:
                occb &= ~tob
            else:
                occw &= ~tob
            cap = True
        tbb[mtype] &= ~fb
        ntype = promo if promo >= 0 else mtype
        tbb[ntype] |= tob
        if me == WHITE:
            occw = (occw & ~fb) | tob
        else:
            occb = (occb & ~fb) | tob
        mb[frm] = -1
        mb[to] = ntype
        p.occ = [occw, occb]
        p.tbb = tbb
        p.mb = mb
        lw, lb = self.leap
        if mtype == T_KING:
            if me == WHITE:
                lw = False
            else:
                lb = False
        p.leap = (lw, lb)
        p.ep = (frm + to) >> 1 if kind == 1 else -1
        p.side = 1 - me
        # 50-move counter: only a capture or a Pawn-type move resets (7.4)
        p.hm = 0 if (cap or mtype == T_PAWN) else self.hm + 1
        p.fm = self.fm + (1 if me == BLACK else 0)
        return p

    def _legal(self):
        out = []
        me = self.side
        for mv in self.pseudo_moves():
            child = self.apply_move(mv)
            if not child._king_attacked(me):
                out.append((move_uci(mv), mv, child))
        out.sort(key=lambda x: x[0])
        return out

    def _legal_children(self):
        out = []
        me = self.side
        for mv in self.pseudo_moves():
            child = self.apply_move(mv)
            if not child._king_attacked(me):
                out.append(child)
        return out

    def legal_moves(self):
        return [u for u, _m, _c in self._legal()]

    def apply(self, uci):
        for u, _m, child in self._legal():
            if u == uci:
                return child
        raise ValueError("illegal move %s in %s" % (uci, self.to_fen()))

    def result(self):
        if self._legal():
            return '*'
        if self.in_check():
            return '1-0' if self.side == BLACK else '0-1'
        return '1/2-1/2'


def perft(pos, depth):
    if depth <= 0:
        return 1
    children = pos._legal_children()
    if depth == 1:
        return len(children)
    return sum(perft(c, depth - 1) for c in children)


def _divide(pos, depth):
    total = 0
    for u, _m, child in pos._legal():
        n = perft(child, depth - 1)
        print("%s %d" % (u, n))
        total += n
    print("total %d" % total)


def selftest():
    def rt(fen):
        p = Position.from_fen(fen)
        assert p.to_fen() == fen, "round-trip failed:\n%s\n%s" % (fen, p.to_fen())
        return p

    # 1. startpos round trip and basic state
    p = rt(START_FEN)
    assert not p.in_check() and p.result() == '*'
    mvs = p.legal_moves()
    assert len(mvs) == 54, "startpos moves = %d, expected 54" % len(mvs)
    for m in ('a4a6', 'd2a5', 'a3c5', 'e3e5', 'b2a5', 'c2a5', 'm2p5'):
        assert m in mvs, m + " missing at startpos"
    assert mvs == sorted(mvs)
    pb = Position.from_fen(START_FEN.replace(' w ', ' b '))
    assert len(pb.legal_moves()) == 54
    assert perft(p, 2) == 2916, "perft(2) != 2916"

    # 2. FEN additive empty-run splitting ("8 8" == "16", spec 3.2)
    alt = START_FEN.replace('/16/', '/88/', 1)
    assert Position.from_fen(alt).to_fen() == START_FEN

    # 3. king initial leap, kings only: 8 steps + 11 legal leaps
    kfen = "16/7k8/16/16/16/16/16/16/16/16/16/16/16/16/7K8/16 w Kk - 0 1"
    kp = rt(kfen)
    km = kp.legal_moves()
    assert len(km) == 19, "king leap count %d != 19" % len(km)
    for m in ('h2f2', 'h2h4', 'h2j4', 'h2g4', 'h2i4', 'h2f1', 'h2j3'):
        assert m in km, m
    # same layout without the leap right: aliasing pair (spec 5)
    knr = Position.from_fen(kfen.replace(' Kk ', ' - '))
    assert len(knr.legal_moves()) == 8

    # 4. leap intermediate-threat rules with a black rook on g8
    rfen = "16/7k8/16/16/16/16/16/16/6r9/16/16/16/16/16/7K8/16 w Kk - 0 1"
    rp = rt(rfen)
    rm = rp.legal_moves()
    assert len(rm) == 11, "rook-leap count %d != 11" % len(rm)
    for m in ('h2j2', 'h2h4', 'h2j4', 'h2j1', 'h2j3', 'h2i4'):
        assert m in rm, m
    for m in ('h2f2', 'h2f4', 'h2f1', 'h2f3', 'h2g4', 'h2g3', 'h2g2', 'h2g1'):
        assert m not in rm, m + " should be barred"

    # 5. pawn double step + en passant capture of a pawn
    efen = "16/7k8/16/16/16/16/16/16/16/16/5p10/16/4P11/16/7K8/16 w - - 0 1"
    ep0 = rt(efen)
    ep1 = ep0.apply('e4e6')
    assert ep1.to_fen().split()[3] == 'e5', ep1.to_fen()
    bm = ep1.legal_moves()
    assert 'f6e5' in bm
    ep2 = ep1.apply('f6e5')
    f2 = ep2.to_fen()
    assert 'P' not in f2.split()[0] and '4p11' in f2.split()[0]
    assert f2.split()[4] == '0'  # capture resets the 50-move counter

    # 6. prince double step captured en passant by a pawn (spec 6.2)
    pfen = "16/7k8/16/16/16/16/16/16/16/16/4i11/16/3P12/16/7K8/16 b - - 0 1"
    pp0 = rt(pfen)
    pp1 = pp0.apply('e6e4')
    assert pp1.to_fen().split()[3] == 'e5'
    assert 'd4e5' in pp1.legal_moves()
    pp2 = pp1.apply('d4e5')
    assert 'i' not in pp2.to_fen().split()[0]

    # 7. troll promotion depends on arrival move type (spec 6.4)
    t1 = rt("16/16/16/4T11/16/16/16/16/16/16/16/16/16/16/16/K14k w - - 0 1")
    m1 = t1.legal_moves()
    assert 'e13e16' in m1 and 'e13e16q' not in m1
    assert t1.apply('e13e16').to_fen().split()[0].split('/')[0] == '4T11'
    t2 = rt("16/4T11/16/16/16/16/16/16/16/16/16/16/16/16/16/K14k w - - 0 1")
    m2 = t2.legal_moves()
    assert 'e15e16q' in m2 and 'e15e16' not in m2
    assert t2.apply('e15e16q').to_fen().split()[0].split('/')[0] == '4Q11'

    # 8. cannon: screen capture only; no capture of or slide past the screen
    cfen = "7k8/16/16/16/16/16/16/r15/16/16/16/P15/16/16/16/C6K8 w - - 0 1"
    cm = rt(cfen).legal_moves()
    for m in ('a1a2', 'a1a3', 'a1a4', 'a1a9', 'a1b1', 'a1g1'):
        assert m in cm, m
    for m in ('a1a5', 'a1a6', 'a1a7', 'a1a8', 'a1a10'):
        assert m not in cm, m + " should be illegal"
    # cannon check through a screen
    chk = rt("16/16/16/16/16/16/16/k15/16/16/16/P15/16/16/16/C6K8 b - - 0 1")
    assert chk.in_check()
    km2 = chk.legal_moves()
    assert 'a9a8' not in km2 and 'a9b9' in km2 and 'a9a10' not in km2

    # 9. eagle geometry on an open board: 56 eagle moves + 3 king moves
    e = rt("15k/16/16/16/16/16/16/16/7G8/16/16/16/16/16/16/K15 w - - 0 1")
    em = e.legal_moves()
    assert len(em) == 59, "eagle count %d != 59" % len(em)
    for m in ('h8i9', 'h8p9', 'h8i16', 'h8g7', 'h8a7', 'h8g1'):
        assert m in em, m
    assert 'h8h9' not in em and 'h8j10' not in em

    # 10. knight promotes to Buffalo (any arrival on last rank)
    n = rt("16/16/2N13/16/16/16/16/16/16/16/16/16/16/16/16/K14k w - - 0 1")
    nm = n.legal_moves()
    assert 'c14b16f' in nm and 'c14d16f' in nm and 'c14b16' not in nm
    assert n.apply('c14b16f').to_fen().split()[0].split('/')[0] == '1F14'


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: terachess_b.py "
                         "perft|divide|moves|fen|apply|selftest ...\n")
        return 2
    cmd = argv[1]
    if cmd == 'selftest':
        selftest()
        print("selftest OK")
        return 0
    if cmd == 'apply':
        if len(argv) < 4:
            sys.stderr.write("usage: terachess_b.py apply <fen> <move...>\n")
            return 2
        pos = Position.from_fen(argv[2])
        for m in argv[3:]:
            pos = pos.apply(m)
        print(pos.to_fen())
        return 0
    if cmd in ('perft', 'divide'):
        depth = int(argv[2])
        fen = ' '.join(argv[3:]) if len(argv) > 3 else START_FEN
        pos = Position.from_fen(fen)
        if cmd == 'perft':
            print(perft(pos, depth))
        else:
            _divide(pos, depth)
        return 0
    if cmd == 'moves':
        fen = ' '.join(argv[2:]) if len(argv) > 2 else START_FEN
        for u in Position.from_fen(fen).legal_moves():
            print(u)
        return 0
    if cmd == 'fen':
        fen = ' '.join(argv[2:]) if len(argv) > 2 else START_FEN
        print(Position.from_fen(fen).to_fen())
        return 0
    sys.stderr.write("unknown command: %s\n" % cmd)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
