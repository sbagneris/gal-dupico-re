#!/usr/bin/env python3
"""Compare a dpdumper text dump of the DUPTEST GAL with the expected truth table.
The part is picked from the number of entries: 1024 = GAL16V8 (duptest.jed), 16384 = GAL20V8 (duptest20.jed).
usage: python3 check_dump.py dump.txt"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duptest_expected, duptest20_expected

rows = {}
for line in open(sys.argv[1]):
    parts = line.split()
    if len(parts) == 2 and len(parts[1]) == 8 and set(parts[1]) <= set('01Z'):
        rows[int(parts[0], 16)] = parts[1]
X = {1024: duptest_expected, 16384: duptest20_expected}.get(len(rows))
if X is None:
    sys.exit(f'dump has {len(rows)} entries; expected 1024 (16V8 test) or 16384 (20V8 test)')
part = 'GAL16V8' if X is duptest_expected else 'GAL20V8'
exp = X.table()
bad = {}
for a, e in enumerate(exp):
    for k, (gb, eb) in enumerate(zip(rows[a], e)):
        if gb != eb:
            bad.setdefault(list(reversed(X.OUT))[k], []).append((a, gb, eb))
if not bad:
    tri = X.OUT[-1]
    print(f'PASS ({part}): all {len(exp)} entries match, including the Z states of pin {tri}.')
    sys.exit(0)
print(f'FAIL ({part}):')
for pin, lst in sorted(bad.items()):
    kinds = {f'got {g} want {e}' for _, g, e in lst}
    print(f'  output pin {pin}: {len(lst)} wrong entries ({", ".join(sorted(kinds))}); first at address {lst[0][0]:04X}')
print('Hint: if every output is wrong, check chip power, orientation and pod seating; if one input is involved,\n'
      'its line is open or bridged - rerun dupico_check.py on the bare board.')
sys.exit(1)
