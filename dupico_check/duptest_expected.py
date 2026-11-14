#!/usr/bin/env python3
"""Expected outputs of duptest.pld for every input combination, in dpdumper's order.
Address bit order = the 'address' list in duptest_palpod.toml: I1..I9, I11 (bit 0 = I1).
Data bit order    = the 'data' list: O12..O19 (bit 0 = O12). Returns 'Z' for tri-stated outputs."""
IN = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11]
OUT = [12, 13, 14, 15, 16, 17, 18, 19]

def outputs(i):
    o = {12: i[1], 13: i[2] ^ i[3], 14: i[3] & i[4], 15: i[4] | i[5],
         16: i[5] & (1 - i[6]) & i[7], 17: i[8] ^ i[9],
         18: (i[11] & i[1]) | ((1 - i[11]) & i[9]),
         19: (1 - i[6]) if i[7] else 'Z'}
    return o

def table():
    rows = []
    for a in range(1 << len(IN)):
        i = {p: a >> n & 1 for n, p in enumerate(IN)}
        o = outputs(i)
        rows.append(''.join(str(o[p]) for p in reversed(OUT)))   # dpdumper prints MSB (O19) first
    return rows
