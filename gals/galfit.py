#!/usr/bin/env python3
"""Fit GAL16V8 equations to a single-input-change move table (dupico_check/regexplore16.py) and validate them by
replaying every recorded move in a simulator of the fitted logic.

GAL16V8 model:
  complex mode:    array inputs = pins 1-9, 11 + feedback from I/O pins 13-18 (pin value). Every output 12-19 =
                   OR of <= 7 product terms (either polarity), enabled by 1 product term. Pins 12, 19: no feedback.
  registered mode: pin 1 = CLK, pin 11 = /OE (held low in the tables). Array inputs = pins 2-9 + feedback from
                   all 8 OLMCs (12-19). Registered OLMC: pin after a CLK edge = D (<= 8 terms). Combinational
                   OLMC: as in complex mode (<= 7 terms + 1 OE term).
Points each function must match (everything else is a don't-care):
  - settled node (S, x): f_p(V) = S_p and OE_p(V) = 1 for driven pins; OE_p(V) = 0 where S_p = Z
  - CLK move (S, x) -> S': D_r(V(S, x)) = S'_r for every register r
  - input flip (S, x) -> (S', x') where exactly one pin q changes: f_q(V(S, x')) = S'_q (q was excited first)
Each function is minimized with espresso in both polarities; the smaller cover is kept (OE terms: positive only,
must be one product term). Validation replays every move with asynchronous settling (outputs may change in any
order): a move passes only if the recorded state is the one and only stable outcome.

usage: python3 galfit.py TABLE --mode complex|registered [--regs 12,15,16] -o OUT_PREFIX
"""
import argparse, ast, collections, re, subprocess, sys

def parse_table(path):
    head, rows = '', []
    for l in open(path):
        if l.startswith('#'): head += l; continue
        if l.startswith('state\t'): continue
        s, x, m, n = l.rstrip('\n').split('\t')
        rows.append((s, x, m, n))
    spins = ast.literal_eval(re.search(r'state = pins (\[[^\]]*\])', head).group(1))
    inpins = ast.literal_eval(re.search(r'inputs = pins (\[[^\]]*\])', head).group(1))
    pu = re.search(r"power-up states \{([^}]*)\}", head)
    power_up = re.findall(r"'([01Z?]+)'", pu.group(1)) if pu else []
    return spins, inpins, rows, power_up

def espresso(nvars, on, off, names, oname):
    if not on: return []                       # constant 0
    if not off: return ['-' * nvars]           # constant 1
    pla = [f'.i {nvars}', '.o 1', '.ilb ' + ' '.join(names), '.ob ' + oname, '.type fr']
    pla += [f'{k} 1' for k in on] + [f'{k} 0' for k in off] + ['.e']
    r = subprocess.run(['espresso'], input='\n'.join(pla) + '\n', text=True, capture_output=True, check=True)
    return [l.split()[0] for l in r.stdout.splitlines() if l and l[0] in '01-']

class Cover:
    """Sum of products over V (cubes as strings), optionally inverted (GAL XOR polarity)."""
    def __init__(self, cubes, invert=False):
        self.cubes, self.invert = cubes, invert
        self.mv = []
        for c in cubes:
            mask = val = 0
            for i, ch in enumerate(c):
                if ch != '-': mask |= 1 << i; val |= (ch == '1') << i
            self.mv.append((mask, val))
    def __call__(self, v):
        return any((v & m) == val for m, val in self.mv) != self.invert

class Fit:
    def __init__(self, table, mode, regs):
        self.spins, self.inpins, self.rows, self.power_up = parse_table(table)
        self.mode, self.regs = mode, [p for p in regs]
        if mode == 'complex':
            self.V = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 14, 15, 16, 17, 18]
            self.limit_f = 7
        else:
            self.V = [2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19]
            self.limit_f = 7
        self.comb = [p for p in self.spins if p not in self.regs]
        self.si = {p: i for i, p in enumerate(self.spins)}
        self.xi = {p: i for i, p in enumerate(self.inpins)}
        for p in self.V:
            if p not in self.si and p not in self.xi: sys.exit(f'array input pin {p} is neither state nor input in the table')
        self.names = [f'P{p}' for p in self.V]

    def vkey(self, s, x):
        """V as a '0'/'1' string in self.V order, or None if a feedback pin is Z."""
        out = []
        for p in self.V:
            c = s[self.si[p]] if p in self.si else x[self.xi[p]]
            if c not in '01': return None
            out.append(c)
        return ''.join(out)
    def vint(self, key): return sum((c == '1') << i for i, c in enumerate(key))

    def flip(self, x, m):
        p = int(m[3:]); i = self.xi[p]
        return x[:i] + ('1' if x[i] == '0' else '0') + x[i + 1:]

    def constraints(self):
        cons = collections.defaultdict(dict)
        conflicts = collections.defaultdict(list)
        def add(fn, key, val, src):
            if key is None: return
            old = cons[fn].get(key)
            if old is None: cons[fn][key] = (val, src)
            elif old[0] != val: conflicts[fn].append((key, old, (val, src)))
        nodes = set()
        for s, x, m, n in self.rows:
            nodes.add((s, x)); nodes.add((n, x if m == 'CLK' else self.flip(x, m)))
        for s, x in nodes:
            k = self.vkey(s, x)
            for p in self.comb:
                c = s[self.si[p]]
                if c in '01': add(('f', p), k, c, 'settled'); add(('oe', p), k, '1', 'settled')
                elif c == 'Z': add(('oe', p), k, '0', 'settled')
        for s, x, m, n in self.rows:
            if m == 'CLK':
                k = self.vkey(s, x)
                for r in self.regs: add(('d', r), k, n[self.si[r]], 'clk')
                continue
            x2 = self.flip(x, m)
            changed = [p for p in self.spins if s[self.si[p]] != n[self.si[p]]]
            if len(changed) == 1 and changed[0] in self.comb:
                q = changed[0]; k = self.vkey(s, x2); c = n[self.si[q]]
                if c in '01': add(('f', q), k, c, 'flip'); add(('oe', q), k, '1', 'flip')
                elif c == 'Z': add(('oe', q), k, '0', 'flip')
        self.nodes = nodes
        return cons, conflicts

    # ---- refinement from moves the fitted logic gets wrong ------------------------------------------------------
    def targets_ok(self, cons, items):
        """items = [(pin, key, target)]: True if none contradicts a stored point."""
        for p, k, t in items:
            if k is None: continue
            if t == 'Z':
                o = cons.get(('oe', p), {}).get(k)
                if o and o[0] != '0': return False
            else:
                o = cons.get(('oe', p), {}).get(k)
                if o and o[0] != '1': return False
                f = cons.get(('f', p), {}).get(k)
                if f and f[0] != t: return False
        return True

    def commit(self, cons, items, src):
        for p, k, t in items:
            if k is None: continue
            if t == 'Z':
                cons[('oe', p)].setdefault(k, ('0', src))
            else:
                cons[('oe', p)].setdefault(k, ('1', src)); cons[('f', p)].setdefault(k, (t, src))

    def path_items(self, t0, x, n, order):
        """Constraints for settling from t0 to n by changing the pins in `order` one at a time: the pin about to
        change is excited, pins that do not change in this move stay put at every intermediate state."""
        items, t = [], t0
        stay = [p for p in self.comb if t0[self.si[p]] == n[self.si[p]]]
        for q in order:
            k = self.vkey(t, x)
            items.append((q, k, n[self.si[q]]))
            items += [(p, k, t[self.si[p]]) for p in stay]
            i = self.si[q]; t = t[:i] + n[i] + t[i + 1:]
        return items

    def refine(self, cons, max_rounds=30, log=print):
        import itertools
        for rnd in range(max_rounds):
            self.minimize(cons)
            fails = []
            for s, x, m, n in self.rows:
                if m == 'CLK':
                    v = self.vint(self.vkey(s, x)); t0 = s
                    for r in self.regs:
                        i = self.si[r]; t0 = t0[:i] + ('1' if self.fn[('d', r)](v) else '0') + t0[i + 1:]
                    x2 = x
                else:
                    t0, x2 = s, self.flip(x, m)
                out = self.settle(t0, x2)
                if out != {n}: fails.append((t0, x2, n))
            log(f'  refine round {rnd + 1}: {len(fails)} moves not reproduced exactly')
            if not fails: return True
            added = stuck = 0
            for t0, x2, n in fails:
                C = [p for p in self.comb if t0[self.si[p]] != n[self.si[p]]]
                best = None
                for order in itertools.permutations(C) if len(C) <= 6 else [tuple(C)]:
                    items = self.path_items(t0, x2, n, order)
                    if not self.targets_ok(cons, items): continue
                    # score: how many of these points the current fit already gets right (prefer the closest order)
                    agree = 0
                    for p, k, tg in items:
                        if k is None: continue
                        v = self.vint(k)
                        oe = self.fn.get(('oe', p)); f = self.fn.get(('f', p))
                        cur = 'Z' if (oe is not None and not oe(v)) else ('1' if f and f(v) else '0')
                        agree += cur == tg
                    if best is None or agree > best[0]: best = (agree, items)
                if best is None: stuck += 1; continue
                self.commit(cons, best[1], 'path'); added += 1
            log(f'    added paths for {added} moves, {stuck} moves with no consistent order')
            if added == 0: return False
        return False

    def minimize(self, cons):
        self.fn = {}
        report = []
        for fn in sorted(cons, key=lambda f: (f[1], f[0])):
            on = [k for k, (v, _) in cons[fn].items() if v == '1']
            off = [k for k, (v, _) in cons[fn].items() if v == '0']
            name = f'{fn[0]}{fn[1]}'
            pos = espresso(len(self.V), on, off, self.names, name)
            if fn[0] == 'oe':
                self.fn[fn] = Cover(pos)
                report.append((fn, len(on), len(off), 'pos', pos, len(pos) <= 1))
                continue
            neg = espresso(len(self.V), off, on, self.names, name)
            inv = len(neg) < len(pos)
            cubes = neg if inv else pos
            self.fn[fn] = Cover(cubes, inv)
            limit = 8 if fn[0] == 'd' else self.limit_f
            report.append((fn, len(on), len(off), 'neg' if inv else 'pos', cubes, len(cubes) <= limit))
        return report

    # ---- simulation -------------------------------------------------------------------------------------------
    def comb_target(self, s, x, p):
        k = self.vkey(s, x)
        if k is None: return None
        v = self.vint(k)
        oe = self.fn.get(('oe', p))
        if oe is not None and not oe(v): return 'Z'
        f = self.fn.get(('f', p))
        if f is None: return s[self.si[p]]           # never driven in the data: leave as is
        return '1' if f(v) else '0'

    def settle(self, s, x, cap=4096):
        seen, todo, stable = {s}, [s], set()
        while todo:
            t = todo.pop()
            exc = []
            for p in self.comb:
                tgt = self.comb_target(t, x, p)
                if tgt is not None and tgt != t[self.si[p]]: exc.append((p, tgt))
            if not exc: stable.add(t); continue
            for p, tgt in exc:
                i = self.si[p]; u = t[:i] + tgt + t[i + 1:]
                if u not in seen:
                    seen.add(u); todo.append(u)
                    if len(seen) > cap: return None
        return stable

    def validate(self):
        res = collections.Counter(); bad = []
        for s, x, m, n in self.rows:
            if m == 'CLK':
                k = self.vkey(s, x); v = self.vint(k); t = s
                for r in self.regs:
                    i = self.si[r]; t = t[:i] + ('1' if self.fn[('d', r)](v) else '0') + t[i + 1:]
                out = self.settle(t, x)
            else:
                out = self.settle(s, self.flip(x, m))
            if out is None: kind = 'no settle (oscillation?)'
            elif out == {n}: kind = 'exact'
            elif n in out: kind = 'ambiguous (race in the fitted logic)'
            else: kind = 'WRONG'
            res[kind] += 1
            if kind != 'exact' and len(bad) < 10: bad.append((s, x, m, n, sorted(out) if out else out))
        stable_nodes = sum(self.settle(s, x) == {s} for s, x in self.nodes)
        return res, bad, stable_nodes

    def cube_str(self, cube):
        lits = [('' if c == '1' else '/') + f'P{p}' for c, p in zip(cube, self.V) if c != '-']
        return ' * '.join(lits) if lits else 'VCC'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('table'); ap.add_argument('--mode', choices=('complex', 'registered'), required=True)
    ap.add_argument('--regs', default=''); ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    regs = [int(p) for p in a.regs.split(',') if p]
    F = Fit(a.table, a.mode, regs)
    cons, conflicts = F.constraints()
    lines = [f'# galfit.py {a.table} --mode {a.mode} --regs {a.regs or "-"}',
             f'# array inputs V = {F.names}', f'# {len(F.rows)} moves, {len(F.nodes)} settled nodes']
    if conflicts:
        lines.append('# CONFLICTS (the model cannot explain the data):')
        for fn, c in conflicts.items(): lines.append(f'#   {fn}: {len(c)}, e.g. {c[:3]}')
    if not conflicts:
        ok = F.refine(cons)
        lines.append(f'# refinement from multi-pin moves: {"converged" if ok else "DID NOT CONVERGE"}')
    rep = F.minimize(cons)
    lines.append('# function: on/off points, polarity, terms, fits the GAL term limit')
    for fn, non, noff, pol, cubes, ok in rep:
        lines.append(f'#   {fn[0]}{fn[1]}: on {non} off {noff}, {pol}, {len(cubes)} terms{"" if ok else "  ** TOO MANY TERMS **"}')
    res, bad, stable = F.validate()
    lines.append(f'# validation: {dict(res)}; settled nodes stable in the model: {stable}/{len(F.nodes)}')
    for b in bad: lines.append(f'#   {b}')
    if F.power_up:
        x0 = '0' * len(F.inpins)
        for pu in sorted(set(F.power_up)):
            lines.append(f'# power-up state {pu} with inputs 0: stable in the model: {F.settle(pu, x0) == {pu}}')
    lines.append('')
    for fn, non, noff, pol, cubes, ok in rep:
        kind, p = fn
        lhs = {'f': f'P{p}', 'd': f'P{p}.R', 'oe': f'P{p}.E'}[kind]
        if kind != 'oe' and pol == 'neg': lhs = '/' + lhs
        if not cubes: rhs = ['GND'] if pol == 'pos' else ['VCC']
        else: rhs = [F.cube_str(c) for c in cubes]
        lines.append(f'{lhs} = ' + '\n    + '.join(rhs))
    out = '\n'.join(lines) + '\n'
    open(a.out + '_fit.txt', 'w').write(out)
    print(out)

if __name__ == '__main__':
    main()
