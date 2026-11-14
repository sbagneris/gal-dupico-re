#!/usr/bin/env python3
"""Minimal GAL16V8 / GAL20V8 complex-mode simulator from a JEDEC file (for checking test vectors).
Validated on a third-party GAL16V8 JED (PAL_samples 00_SR_GAL16V8.JED, EQN2JED); 20V8 uses the same structure
with GALasm's GAL20V8 column table."""
import re, sys
PARTS = {
    '16V8': dict(cols=32, base=2048,
                 col={1: 2, 2: 0, 3: 4, 4: 8, 5: 12, 6: 16, 7: 20, 8: 24, 9: 28, 11: 30,
                      13: 26, 14: 22, 15: 18, 16: 14, 17: 10, 18: 6},
                 olmc=[19, 18, 17, 16, 15, 14, 13, 12], fb=(13, 14, 15, 16, 17, 18), fuses=2194),
    '20V8': dict(cols=40, base=2560,
                 col={1: 2, 2: 0, 3: 4, 4: 8, 5: 12, 6: 16, 7: 20, 8: 24, 9: 28, 10: 32, 11: 36, 13: 38, 14: 34, 23: 6,
                      16: 30, 17: 26, 18: 22, 19: 18, 20: 14, 21: 10},
                 olmc=[22, 21, 20, 19, 18, 17, 16, 15], fb=(16, 17, 18, 19, 20, 21), fuses=2706),
}
# offsets after the AND array: XOR(8) SIG(64) AC1(8) PTD(64) SYN AC0
def load(path):
    txt = open(path, errors='replace').read()
    body = txt[txt.index('\x02') + 1:] if '\x02' in txt else txt
    fields = [x.strip() for x in body.split('*')]
    q = next(int(x[2:]) for x in fields if x.startswith('QF'))
    d = next((int(x[1]) for x in fields if re.fullmatch(r'F[01]', x)), 0)
    f = [d] * q
    for x in fields:
        m = re.fullmatch(r'L(\d+)\s+([01\s]+)', x, re.S)
        if m:
            bits = re.sub(r'\s', '', m.group(2))
            for i, b in enumerate(bits): f[int(m.group(1)) + i] = int(b)
    return f

# GAL16V8 registered mode (SYN=0, AC0=1): pins 1 (CLK) and 11 (/OE) leave the array; columns per GALasm's
# PinToFuse16Mode3. Registered OLMC (AC1=0): pin after CLK = (OR of all 8 terms) ^ (1 - XOR), feedback = pin level,
# enabled by pin 11 low, power-up pin = 1. Combinational OLMC (AC1=1): row 0 = OE term, rows 1-7 = sum.
MODE3_COL16 = {2: 0, 3: 4, 4: 8, 5: 12, 6: 16, 7: 20, 8: 24, 9: 28,
               12: 30, 13: 26, 14: 22, 15: 18, 16: 14, 17: 10, 18: 6, 19: 2}

class GAL:
    def __init__(self, fuses):
        self.p = next(p for p in PARTS.values() if p['fuses'] == len(fuses))
        self.f = fuses; b = self.p['base']
        self.xor, self.ac1, self.ptd = b, b + 8 + 64, b + 8 + 64 + 8
        self.syn, self.ac0 = fuses[b + 144], fuses[b + 145]
        if (self.syn, self.ac0) == (1, 1):
            self.mode, self.col = 'complex', self.p['col']
        elif (self.syn, self.ac0) == (0, 1) and self.p['fuses'] == 2194:
            self.mode, self.col = 'registered', MODE3_COL16
        else:
            raise AssertionError('only complex mode (16V8/20V8) and registered mode (16V8) are simulated')
    def kind(self, k):
        """OLMC k (k = 0 is the highest output pin): 'reg' or 'comb'."""
        return 'reg' if self.mode == 'registered' and self.f[self.ac1 + k] == 0 else 'comb'
    def targets(self, val):
        """val = {pin: 0/1} for every array input (inputs and feedback pin levels). Returns {output pin: (kind, v)}:
        comb -> 0/1 or 'Z' (OE term false); reg -> the value the pin takes at the next CLK rising edge."""
        out = {}
        for k, pin in enumerate(self.p['olmc']):
            rows = list(range(8 * k, 8 * k + 8)); pol = 1 - self.f[self.xor + k]
            if self.kind(k) == 'reg':
                out[pin] = ('reg', int(any(self.term(r, val) for r in rows if self.f[self.ptd + r])) ^ pol)
            else:
                oe = self.term(rows[0], val) if self.f[self.ptd + rows[0]] else False
                s = any(self.term(r, val) for r in rows[1:] if self.f[self.ptd + r])
                out[pin] = ('comb', (int(s) ^ pol) if oe else 'Z')
        return out
    def term(self, row, val):
        n = self.p['cols']
        for pin, c in self.col.items():
            v = val.get(pin, 0)
            if self.f[row * n + c] == 0 and not v: return False
            if self.f[row * n + c + 1] == 0 and v: return False
        return True
    def eval(self, inputs, state=None, iterations=8):
        val = dict(inputs); out = dict(state) if state else {p: 0 for p in self.p['olmc']}
        for _ in range(iterations):
            for p in self.p['fb']: val[p] = out[p] if out[p] != 'Z' else 0
            new = {}
            for k, pin in enumerate(self.p['olmc']):
                rows = list(range(8 * k, 8 * k + 8))
                oe = self.term(rows[0], val) if self.f[self.ptd + rows[0]] else False
                s = any(self.term(r, val) for r in rows[1:] if self.f[self.ptd + r])
                new[pin] = (int(s) ^ (1 - self.f[self.xor + k])) if oe else 'Z'
            if new == out: break
            out = new
        return out
