"""Replay every move of a table on a JEDEC fuse map, independently of the fitter's own model: checks the fit, the
PLD generator and GALasm together (or any JED against any table).

An input flip or CLK move passes only if the recorded state is the one and only stable outcome when outputs
settle one at a time in any order (CLK: registers take D first). Feedback from a Z state pin reads 0 (the explorer
holds state pins low). Also: every settled node and power-up state stable, output-cell pins the table drives as
inputs never driven by the JED, OLMC configuration sane (complex: AC1 = 1; registered: registers are state pins),
registers power up high.
"""
import collections
from .sim import GAL, load_jed
from .table import read_table

def jedcheck(jed, table, log=print):
    g = GAL(load_jed(jed))
    meta, rows = read_table(table)
    spins, inpins = meta['state'], meta['inputs']
    si = {p: i for i, p in enumerate(spins)}; xi = {p: i for i, p in enumerate(inpins)}
    ok = True
    if meta.get('chip') and meta['chip'] != g.chip:
        log(f'FAIL: the JED is a {g.c["name"]}, the table a {meta["chip"]}'); return False
    if meta.get('mode') and meta['mode'] != g.mode:
        log(f'note: table explored in {meta["mode"]} mode, JED is {g.mode} mode')
    kinds = g.kinds
    regs = [p for p in spins if kinds.get(p) == 'reg']
    comb = [p for p in spins if kinds.get(p) in ('comb', 'out')]
    log(f'{jed}: {g.c["name"]} {g.mode} mode; output cells {kinds}')
    held = meta.get('held', {})
    missing = [p for p in g.V if p not in si and p not in xi and p not in held]
    if missing:
        log(f'FAIL: array input pins {missing} are not in the table'); return False
    cache = {}
    def tg(s, x):
        k = (s, x)
        if k not in cache:
            # a Z feedback pin floats: the result must not depend on its level (X where it does)
            lvs = [{}]
            for p in g.V:
                c = s[si[p]] if p in si else x[xi[p]] if p in xi else str(held[p])
                lvs = [{**l, p: b} for l in lvs for b in ((0, 1) if c == 'Z' else (1 if c == '1' else 0,))]
            ts = [g.targets(g.vword(l)) for l in lvs]
            cache[k] = {q: (ts[0][q] if all(tt[q] == ts[0][q] for tt in ts) else 'X') for q in ts[0]}
        return cache[k]
    def settle(s, x, cap=4096):
        seen, todo, stable = {s}, [s], set()
        while todo:
            t = todo.pop(); T = tg(t, x); exc = []
            for p in comb:
                v = T[p]; v = v if v == 'Z' else str(v)
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
            for r in regs: i = si[r]; t = t[:i] + str(T[r]) + t[i + 1:]
            out = settle(t, x); nodes.add((n, x))
        else:
            x2 = flip(x, m); out = settle(s, x2); nodes.add((n, x2))
        kind = 'no settle' if out is None else 'exact' if out == {n} else 'ambiguous' if n in out else 'WRONG'
        res[kind] += 1
        if kind != 'exact' and len(bad) < 10: bad.append((s, x, m, n, sorted(out) if out else out))
    stable = sum(settle(s, x) == {s} for s, x in nodes)
    log(f'moves: {dict(res)}; settled nodes stable: {stable}/{len(nodes)}')
    ok &= res['exact'] == len(rows) and stable == len(nodes)
    for b in bad: log(f'   {b}')
    in_olmc = [p for p in list(inpins) + list(held) if p in kinds]
    driven = collections.Counter(p for s, x in nodes for p in in_olmc if tg(s, x)[p] != 'Z')
    log(f'output-cell pins used as inputs {in_olmc}: driven by the JED in {dict(driven) or "no"} settled nodes')
    ok &= not driven
    if g.mode == 'complex': bad_cfg = g.ac1_zero_in_complex
    elif g.mode == 'registered': bad_cfg = [p for p in g.kinds if g.kinds[p] == 'reg' and p not in si]
    else: bad_cfg = [p for p in g.kinds if g.kinds[p] == 'out' and p not in si]
    log(f'bad output-cell configuration (complex: AC1=0; registered/simple: driving cell outside the state): '
        f'{bad_cfg or "none"}')
    ok &= not bad_cfg
    x0 = '0' * len(inpins)
    for pu in sorted(set(meta.get('power_up', []))):
        st = settle(pu, x0) == {pu}; hi = all(pu[si[r]] == '1' for r in regs)
        log(f'power-up {pu}: stable {st}' + (f'; registers high (as a GAL powers up) {hi}' if regs else ''))
        ok &= st and hi
    log('PASS' if ok else 'FAIL')
    return ok
