#!/usr/bin/env python3
"""Expected outputs of duptest20.pld (GAL20V8) for every input combination, in dpdumper's order.
Address bits = 'address' list in duptest20_palpod.toml (bit 0 = I1). Data bits = O15..O22 (bit 0 = O15)."""
IN = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 23]
OUT = [15, 16, 17, 18, 19, 20, 21, 22]

def outputs(i):
    return {15: i[1], 16: i[2] ^ i[3], 17: (i[3] & i[4]) | i[13], 18: i[4] | (i[5] & i[14]),
            19: i[5] & (1 - i[6]) & i[7], 20: i[8] ^ i[9],
            21: (i[23] & i[10]) | ((1 - i[23]) & i[11]),
            22: (1 - i[6]) if i[7] else 'Z'}

def table():
    rows = []
    for a in range(1 << len(IN)):
        i = {p: a >> n & 1 for n, p in enumerate(IN)}
        o = outputs(i)
        rows.append(''.join(str(o[p]) for p in reversed(OUT)))
    return rows
