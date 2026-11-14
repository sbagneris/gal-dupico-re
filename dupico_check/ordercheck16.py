#!/usr/bin/env python3
"""Latch check for a GAL16V8 read as combinational (dpdumper dump, which walks the inputs in ascending order).
Re-reads every input vector in Gray-code order (single-input changes) and in random order (multi-input jumps),
each pass twice, and compares every entry with the dump. Any difference means the outputs depend on history.
Read-only. Placement as gal16v8_direct.toml (top-aligned, pin 1 at ZIF 1, jumper header 10 -> 21).
usage: python3 ordercheck16.py DUMP.txt [--inputs 1-9,11] [--data 12-19] [-p /dev/ttyACM0]"""
import argparse, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dupico_check import Dupico, PIN2BIT
from regexplore16 import pinlist

ZIF = [1, 2, 3, 4, 5, 6, 7, 8, 9, 21, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42]
def bit(p): return 1 << PIN2BIT[ZIF[p - 1]]

ap = argparse.ArgumentParser()
ap.add_argument('dump'); ap.add_argument('-p', '--port', default='/dev/ttyACM0')
ap.add_argument('--inputs', default='1-9,11'); ap.add_argument('--data', default='12-19')
a = ap.parse_args()
IN, DATA = pinlist(a.inputs), pinlist(a.data)            # dump: address bit i = IN[i]; string = DATA reversed
pull = sum(bit(p) for p in DATA)
ref = {}
for l in open(a.dump):
    t = l.split()
    if len(t) == 2 and len(t[1]) == len(DATA) and set(t[1]) <= set('01Z'): ref[int(t[0], 16)] = t[1]
n = 1 << len(IN)
assert len(ref) == n, f'dump has {len(ref)} entries, expected {n}'

d = Dupico(a.port)
d.write(0); d.power(True); time.sleep(0.5)
def read(x):
    v = sum(bit(p) for i, p in enumerate(IN) if x >> i & 1)
    lo = d.write(v); hi = d.write(v | pull); d.write(v)
    return ''.join('1' if lo & bit(p) and hi & bit(p) else '0' if not (lo & bit(p) or hi & bit(p))
                   else 'Z' if hi & bit(p) else '?' for p in reversed(DATA))
orders = {'gray': [i ^ (i >> 1) for i in range(n)]}
r = list(range(n)); random.Random(3).shuffle(r); orders['random'] = r
bad = 0
try:
    for name, order in orders.items():
        for rep in range(2):
            diff = [(x, ref[x], got) for x in order if (got := read(x)) != ref[x]]
            bad += len(diff)
            print(f'{name} order, pass {rep + 1}: {len(diff)} of {n} entries differ from the dump'
                  + (f', e.g. {[(f"{x:03X}", e, g) for x, e, g in diff[:5]]}' if diff else ''))
finally:
    d.write(0); d.power(False)
print('PASS: no history dependence seen' if not bad else 'FAIL: outputs depend on input history (latches?)')
sys.exit(1 if bad else 0)
