#!/usr/bin/env python3
"""Quick wiring check for a GAL20V8 programmed with duptest20.jed: drive a few input vectors, read outputs with data lines
pulled low and then high (through the dupico's 10K) to tell driven from floating.
Placement: PAL POD, or straight in the ZIF42 top-aligned with a jumper from pin 12 to pin 21 (same pin map).
If inputs do not read back as driven, the chip ground is floating (missing pod / jumper).
usage: python3 probe20.py [/dev/ttyACM0]"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dupico_check import Dupico, PIN2BIT
import duptest20_expected as X

ZIF = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 21, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42]
def bit(dev_pin): return 1 << PIN2BIT[ZIF[dev_pin - 1]]
DATA = sum(bit(p) for p in X.OUT)

def vec(**kw):
    return {p: kw.get(f'i{p}', 0) for p in X.IN}

VECTORS = [
    ('all inputs 0 (I7=0 -> O22 Z)', vec()),
    ('I7=1 only (O22 = /I6 = 1)', vec(i7=1)),
    ('I7=1 I6=1 (O22 = 0)', vec(i7=1, i6=1)),
    ('I1=1 (O15=1)', vec(i1=1)),
    ('I2=1 (O16=1)', vec(i2=1)),
    ('I13=1 (O17=1)', vec(i13=1)),
    ('I4=1 (O18=1)', vec(i4=1)),
    ('I5 I7 =1 (O19=1, O22=1)', vec(i5=1, i7=1)),
    ('I8=1 (O20=1)', vec(i8=1)),
    ('I23=1 I10=1 (O21=1)', vec(i23=1, i10=1)),
]

d = Dupico(sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0')
print('model', d.model(), 'fw', d.version())
d.write(0); d.power(True); time.sleep(0.5)
try:
    print(f'{"vector":34} {"pins":>24}  {"expected":>8} {"pull-lo":>8} {"pull-hi":>8}')
    for name, iv in VECTORS:
        drive = sum(bit(p) for p, v in iv.items() if v)
        lo = d.write(drive); time.sleep(0.01); lo = d.read()
        d.write(drive | DATA); time.sleep(0.01); hi = d.read()
        o = X.outputs(iv)
        exp = ''.join(str(o[p]) for p in reversed(X.OUT))
        rl = ''.join(str(int(bool(lo & bit(p)))) for p in reversed(X.OUT))
        rh = ''.join(str(int(bool(hi & bit(p)))) for p in reversed(X.OUT))
        # inputs read back (should equal what we drive)
        inp_bad = [p for p in X.IN if bool(lo & bit(p)) != bool(iv[p])]
        print(f'{name:34} {"O22..O15":>24}  {exp:>8} {rl:>8} {rh:>8}' + (f'  INPUT READBACK WRONG on pins {inp_bad}' if inp_bad else ''))
    # Pins 12 and 24 are GND/VCC: show the raw read of all ZIF lines with everything low
    d.write(0); time.sleep(0.01); v = d.read()
    print('all driven low, lines reading high (ZIF42 pins):', [p for p, b in PIN2BIT.items() if v >> b & 1])
finally:
    d.write(0); d.power(False)
