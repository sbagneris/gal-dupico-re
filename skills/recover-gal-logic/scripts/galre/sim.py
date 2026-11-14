"""GAL16V8 / GAL20V8 behaviour from a JEDEC fuse map (simple, complex and registered modes).

Fuse semantics (validated on third-party and GALasm JEDs, and on the AdSCSI U3/U4/U5 replacements):
  AND array: fuse 0 = literal connected (column c = pin true, c + 1 = pin complement); a row with both literals
  of one pin connected is never true. PTD fuse 1 = product term enabled. XOR fuse 1 = active high.
  complex mode: OLMC row 0 = output enable term, rows 1-7 = sum. Feedback = pin level.
  registered mode: AC1 = 0 -> registered OLMC: D = OR(8 rows) ^ polarity, clocked by pin 1, pin driven while the
  /OE pin is low; feedback = the register (its pin value), also when /OE is high. AC1 = 1 -> as complex mode.
  simple mode: AC1 = 1 -> the pin is an input; AC1 = 0 -> combinational output, always enabled, 8 rows.
  Simple-mode feedback is modelled as the pin level (not validated on hardware: few secured parts use it).
Registers power up with their pins high (GAL datasheets: Q reset low, inverting output buffer).
"""
import re
from .chips import CHIPS, OFF_XOR, OFF_AC1, OFF_PTD, OFF_SYN, OFF_AC0, chip_by_fuses

def load_jed(path):
    txt = open(path, errors='replace').read()
    body = txt[txt.index('\x02') + 1:] if '\x02' in txt else txt
    body = body.split('\x03')[0]
    fields = [x.strip() for x in body.split('*')]
    q = next((int(x[2:]) for x in fields if x.startswith('QF')), None)
    d = next((int(x[1]) for x in fields if re.fullmatch(r'F[01]', x)), 0)
    if q is None: raise ValueError(f'{path}: no QF field (not a JEDEC fuse file?)')
    f = [d] * q
    for x in fields:
        m = re.fullmatch(r'L(\d+)\s+([01\s]+)', x, re.S)
        if m:
            bits = re.sub(r'\s', '', m.group(2))
            for i, b in enumerate(bits): f[int(m.group(1)) + i] = int(b)
    return f

class GAL:
    def __init__(self, fuses):
        self.chip = chip_by_fuses(len(fuses))
        c = self.c = CHIPS[self.chip]; self.f = fuses; b = c['base']
        syn, ac0 = fuses[b + OFF_SYN], fuses[b + OFF_AC0]
        self.mode = {(1, 0): 'simple', (1, 1): 'complex', (0, 1): 'registered'}.get((syn, ac0))
        if self.mode is None: raise ValueError(f'SYN={syn} AC0={ac0}: not a valid GAL mode')
        self.V = sorted(c['col'][self.mode])                    # array inputs; bit i of a V word = pin V[i]
        self.vi = {p: i for i, p in enumerate(self.V)}
        n = c['cols']
        self.rows = []                                         # per row: (enabled, mask, val, never)
        for r in range(64):
            mask = val = 0; never = False
            for p in self.V:
                col = c['col'][self.mode][p]; t, f = fuses[r * n + col] == 0, fuses[r * n + col + 1] == 0
                if t and f: never = True
                elif t: mask |= 1 << self.vi[p]; val |= 1 << self.vi[p]
                elif f: mask |= 1 << self.vi[p]
            self.rows.append((fuses[b + OFF_PTD + r] == 1, mask, val, never))
        self.cells = []                                        # per OLMC: (pin, kind, polarity)
        for k, pin in enumerate(c['olmc']):
            ac1 = fuses[b + OFF_AC1 + k]; pol = 1 - fuses[b + OFF_XOR + k]
            if self.mode == 'registered': kind = 'reg' if ac1 == 0 else 'comb'
            elif self.mode == 'complex': kind = 'comb'
            else: kind = 'in' if ac1 == 1 else 'out'
            self.cells.append((pin, kind, pol))
        self.kinds = {pin: kind for pin, kind, _ in self.cells}
        self.ac1_zero_in_complex = [pin for k, (pin, _, _) in enumerate(self.cells)
                                    if self.mode == 'complex' and fuses[b + OFF_AC1 + k] == 0]
        self._cache = {}

    def _row(self, r, v):
        en, mask, val, never = self.rows[r]
        return en and not never and (v & mask) == val

    def vword(self, levels):
        """{pin: 0/1} -> V word (missing pins = 0)."""
        return sum(1 << i for i, p in enumerate(self.V) if levels.get(p))

    def targets(self, v):
        """V word -> {OLMC pin: value}: comb/out -> 0, 1 or 'Z' (output disabled); reg -> D (value at the next
        clock); in -> 'Z'."""
        t = self._cache.get(v)
        if t is not None: return t
        t = {}
        for k, (pin, kind, pol) in enumerate(self.cells):
            rows = range(8 * k, 8 * k + 8)
            if kind == 'reg':
                t[pin] = int(any(self._row(r, v) for r in rows)) ^ pol
            elif kind == 'out':
                t[pin] = int(any(self._row(r, v) for r in rows)) ^ pol
            elif kind == 'in':
                t[pin] = 'Z'
            else:
                oe = self._row(8 * k, v)
                t[pin] = (int(any(self._row(r, v) for r in rows[1:])) ^ pol) if oe else 'Z'
        if len(self._cache) < 1 << 20: self._cache[v] = t
        return t

class Chip:
    """A GAL in a socket: pins driven from outside (through resistors), outputs settling one at a time.
    Settling order is fixed (OLMC order), so a JED with a race resolves deterministically here."""
    def __init__(self, gal):
        self.g = gal; c = gal.c
        self.oe_pin = c['oe'] if gal.mode == 'registered' else None
        self.clk_pin = c['clk'] if gal.mode == 'registered' else None
        self.reset()

    def reset(self):
        self.reg = {pin: 1 for pin, kind, _ in self.g.cells if kind == 'reg'}
        self.out = {pin: 0 for pin, kind, _ in self.g.cells if kind in ('comb', 'out')}
        self.clk = None

    def pin_levels(self, ext):
        """Actual pin levels given external drive ext {pin: 0/1}: chip-driven pins win."""
        lv = dict(ext)
        for p, v in self.out.items():
            if v != 'Z': lv[p] = v
        if self.reg and not ext.get(self.oe_pin, 0):
            lv.update(self.reg)
        return lv

    def vword(self, ext):
        lv = self.pin_levels(ext)
        lv.update(self.reg)                                   # registered feedback = the register itself
        return self.g.vword(lv)

    def settle(self, ext, limit=1000):
        for _ in range(limit):
            t = self.g.targets(self.vword(ext))
            for p in self.out:
                if t[p] != self.out[p]:
                    self.out[p] = t[p]; break
            else:
                return True
        return False                                           # oscillates

    def apply(self, ext, double_clock=False):
        """New external drive: clock on a rising CLK edge (registered mode), then settle."""
        if self.clk_pin is not None:
            c = ext.get(self.clk_pin, 0)
            if self.clk == 0 and c == 1:
                for _ in range(2 if double_clock else 1):
                    t = self.g.targets(self.vword({**ext, self.clk_pin: 0}))
                    for p in self.reg: self.reg[p] = t[p]
                    self.settle(ext)
            self.clk = c
        self.settle(ext)
        return self.pin_levels(ext)

    def driven(self, ext):
        """{pin: value} for pins the chip drives right now."""
        d = {p: v for p, v in self.out.items() if v != 'Z'}
        if self.reg and not ext.get(self.oe_pin, 0): d.update(self.reg)
        return d
