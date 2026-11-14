#!/usr/bin/env python3
"""Replay every move of a regexplore16.py table on a JED fuse map (dupico_check/gal_sim.py), independently of
galfit.py's own model: checks the fit, fit2pld.py and GALasm together.

Same semantics as galfit.py's validation: an input flip or CLK move passes only if the recorded state is the one
and only stable outcome when the outputs settle in any order (CLK: registers take their D value first).
Array inputs read from the table: inputs from the input vector, feedback from the state (Z feedback = 0, the
dupico's pull). Also checks that every settled node and the power-up state are stable.

usage: python3 jedcheck.py u3/u3.jed u3/u3_moves.tsv
"""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dupico_check'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gal_sim
from galfit import parse_table

jed, table = sys.argv[1], sys.argv[2]
g = gal_sim.GAL(gal_sim.load(jed))
spins, inpins, rows, power_up = parse_table(table)
si = {p: i for i, p in enumerate(spins)}; xi = {p: i for i, p in enumerate(inpins)}
olmc = g.p['olmc']
kinds = {pin: g.kind(k) for k, pin in enumerate(olmc)}
regs = [p for p in spins if kinds.get(p) == 'reg']
comb = [p for p in spins if p in kinds and kinds[p] == 'comb']
print(f'{jed}: {g.mode} mode; OLMC kinds {kinds}; table state pins {spins}, inputs {inpins}')
for p in inpins:
    if p in kinds and p not in g.col: pass
missing = [p for p in g.col if p not in si and p not in xi]
if missing: sys.exit(f'array input pins {missing} are not in the table')

cache = {}
def tg(s, x):
    k = (s, x)
    if k not in cache:
        val = {}
        for p in g.col:
            c = s[si[p]] if p in si else x[xi[p]]
            val[p] = 1 if c == '1' else 0
        cache[k] = g.targets(val)
    return cache[k]

def settle(s, x, cap=4096):
    seen, todo, stable = {s}, [s], set()
    while todo:
        t = todo.pop(); T = tg(t, x); exc = []
        for p in comb:
            v = T[p][1]; v = v if v == 'Z' else str(v)
            if v != t[si[p]]: exc.append((p, v))
        if not exc: stable.add(t); continue
        for p, v in exc:
            i = si[p]; u = t[:i] + v + t[i + 1:]
            if u not in seen:
                seen.add(u); todo.append(u)
                if len(seen) > cap: return None
    return stable

def flip(x, m):
    i = xi[int(m[3:])]; return x[:i] + ('1' if x[i] == '0' else '0') + x[i + 1:]

res = collections.Counter(); bad = []; nodes = set()
for s, x, m, n in rows:
    nodes.add((s, x))
    if m == 'CLK':
        T = tg(s, x); t = s
        for r in regs: i = si[r]; t = t[:i] + str(T[r][1]) + t[i + 1:]
        out = settle(t, x); nodes.add((n, x))
    else:
        x2 = flip(x, m); out = settle(s, x2); nodes.add((n, x2))
    kind = 'no settle' if out is None else 'exact' if out == {n} else 'ambiguous' if n in out else 'WRONG'
    res[kind] += 1
    if kind != 'exact' and len(bad) < 10: bad.append((s, x, m, n, sorted(out) if out else out))
stable = sum(settle(s, x) == {s} for s, x in nodes)
print(f'moves: {dict(res)}; settled nodes stable: {stable}/{len(nodes)}')
in_olmc = [p for p in inpins if p in kinds]           # output-cell pins the table drives as inputs
driven = collections.Counter(p for s, x in nodes for p in in_olmc if tg(s, x)[p] != ('comb', 'Z'))
print(f'output-cell pins used as inputs {in_olmc}: driven by the JED in {dict(driven) or "no"} settled nodes')
unused = [pin for k, pin in enumerate(olmc) if pin not in si and pin not in xi]
if g.mode == 'complex':      # complex mode is only defined with AC1 = 1 on every OLMC
    bad_cfg = [pin for k, pin in enumerate(olmc) if g.f[g.ac1 + k] == 0]
else:                        # registered cells must be state pins of the table (they drive whenever /OE is low)
    bad_cfg = [pin for k, pin in enumerate(olmc) if g.kind(k) == 'reg' and pin not in si]
print(f'output-cell pins not in the state: {sorted(set(unused) | set(in_olmc))}; bad OLMC configuration '
      f'(complex: AC1=0, registered: register outside the state): {bad_cfg or "none"}')
for b in bad: print('  ', b)
x0 = '0' * len(inpins)
for pu in sorted(set(power_up)):
    ok = settle(pu, x0) == {pu}
    regs_hi = all(pu[si[r]] == '1' for r in regs)
    print(f'power-up {pu}: stable {ok}' + (f'; registers high as a GAL16V8 powers up: {regs_hi}' if regs else ''))
ok = res['exact'] == len(rows) and stable == len(nodes) and not driven and not bad_cfg
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
