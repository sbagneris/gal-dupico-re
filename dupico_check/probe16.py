#!/usr/bin/env python3
"""Read-only first probe of an unknown GAL16V8 placed straight in the dupico ZIF42 (top-aligned, pin 1 at ZIF 1,
pins 11-20 at ZIF 33-42, dupont jumper header 10 -> 21 for GND). Same pin map as gal16v8_direct.toml.

1. ground/placement: every pin driven low and high, inputs 1-9/11 must read back what is driven
2. I/O pins 12-19 per vector: driven 0/1, or Z (follows the dupico's 10K pull)
3. state: hold a vector, pulse pin 1 (CLK in registered mode) and compare; repeat a vector after different histories
usage: python3 probe16.py [/dev/ttyACM0]"""
import os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dupico_check import Dupico, PIN2BIT

ZIF = [1, 2, 3, 4, 5, 6, 7, 8, 9, 21, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42]
IN = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11]
IO = [12, 13, 14, 15, 16, 17, 18, 19]
def bit(p): return 1 << PIN2BIT[ZIF[p - 1]]
IOMASK = sum(bit(p) for p in IO)

def drive(d, inputs):
    return sum(bit(p) for p in IN if inputs.get(p))

def sample(d, inputs):
    """-> {pin: '0'/'1'/'Z'} for the I/O pins, plus input pins that did not read back."""
    v = drive(d, inputs)
    d.write(v); lo = d.read()
    d.write(v | IOMASK); hi = d.read()
    d.write(v)
    out = {}
    for p in IO:
        l, h = bool(lo & bit(p)), bool(hi & bit(p))
        out[p] = 'Z' if (l, h) == (False, True) else ('1' if l and h else ('0' if not l and not h else '?'))
    bad = [p for p in IN if bool(lo & bit(p)) != bool(inputs.get(p))]
    return out, bad

def fmt(o): return ''.join(o[p] for p in reversed(IO))   # pin 19 .. pin 12

d = Dupico(sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0')
print('model', d.model(), 'fw', d.version())
d.write(0); d.power(True); time.sleep(0.5)
try:
    # 1. inputs must follow the drive
    worst = []
    for name, iv in (('all 0', {}), ('all 1', {p: 1 for p in IN})) + tuple(
            (f'only {p}', {p: 1}) for p in IN):
        _, bad = sample(d, iv)
        if bad: worst.append((name, bad))
    if worst:
        print('INPUT READBACK WRONG (ground jumper / placement?):', worst)
        sys.exit(1)
    print('1. inputs follow the drive: ground and placement OK')

    # 2. I/O pin classes over a few vectors
    print('2. I/O pins 19..12 (0/1 driven, Z floating/input, ? fights the pull):')
    random.seed(1)
    vecs = [{}, {p: 1 for p in IN}] + [{p: random.randint(0, 1) for p in IN} for _ in range(14)]
    seen = {p: set() for p in IO}
    for iv in vecs:
        o, _ = sample(d, iv)
        for p in IO: seen[p].add(o[p])
        print(f'   in 11,9..1 = {iv.get(11,0)} {"".join(str(iv.get(p,0)) for p in reversed(IN[:9]))}  ->  {fmt(o)}')
    print('   per pin:', ', '.join(f'{p}:{"".join(sorted(seen[p]))}' for p in IO))

    # 3. state: pulse pin 1 with everything else fixed, and replay vectors after different histories
    changed = []
    for iv in vecs[:8]:
        for pin1 in (0, 1):
            v = dict(iv); v[1] = pin1
            before, _ = sample(d, v)
            for _ in range(3):
                d.write(drive(d, {**v, 1: 1 - pin1})); d.write(drive(d, v))
            after, _ = sample(d, v)
            if before != after: changed.append((fmt(before), fmt(after), v))
    hist = []
    for iv in vecs[:6]:
        ref, _ = sample(d, iv)
        for jv in random.sample(vecs, 6):
            sample(d, jv)
            again, _ = sample(d, iv)
            if again != ref: hist.append((fmt(ref), fmt(again)))
    if changed or hist:
        print(f'3. STATEFUL: outputs changed after pin-1 pulses in {len(changed)} cases, after other vectors in '
              f'{len(hist)} cases -> registered (pin 1 = CLK) or latch feedback; a plain dump is not enough.')
        for b, a, v in changed[:5]: print(f'   pin-1 pulses: {b} -> {a}')
        for b, a in hist[:5]: print(f'   history:      {b} -> {a}')
    else:
        print('3. no state seen: pin-1 pulses and history did not change any output -> looks combinational')
finally:
    d.write(0); d.power(False)
