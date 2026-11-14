"""Fit GAL equations to a move table and validate them by replaying every recorded move.

Model (per chip and mode, from chips.py): array inputs V = the pins feeding the AND array (dedicated inputs +
feedback pins). Combinational output = OR of <= 7 terms (either polarity) enabled by 1 OE term; register D = OR of
<= 8 terms. Points each function must match (everything else is a don't-care):
  - settled node (S, x): f_p(V) = S_p and OE_p(V) = 1 for driven pins; OE_p(V) = 0 where S_p = Z
  - CLK move (S, x) -> S': D_r(V(S, x)) = S'_r for every register r
  - input flip (S, x) -> (S', x') where exactly one output q changes: f_q(V(S, x')) = S'_q (q was excited first)
Moves where several outputs change need an excitation order: refinement adds, for each move the current fit gets
wrong, the path of an order consistent with every point so far (preferring the order the fit is closest to).
Each function is minimised with espresso in both polarities and the smaller cover kept (OE: positive, 1 term).
Validation: a move passes only if the recorded state is the one and only stable outcome when outputs settle one
at a time in any order (no race the original did not show).
"""
import collections, itertools, subprocess, sys
from .chips import CHIPS
from .table import read_table

def espresso(nvars, on, off, names, oname):
    if not on: return []
    if not off: return ['-' * nvars]
    pla = [f'.i {nvars}', '.o 1', '.ilb ' + ' '.join(names), '.ob ' + oname, '.type fr']
    pla += [f'{k} 1' for k in on] + [f'{k} 0' for k in off] + ['.e']
    try:
        r = subprocess.run(['espresso'], input='\n'.join(pla) + '\n', text=True, capture_output=True, check=True)
    except FileNotFoundError:
        sys.exit('espresso not found on the PATH (Berkeley espresso logic minimiser; e.g. the chipsalliance fork)')
    return [l.split()[0] for l in r.stdout.splitlines() if l and l[0] in '01-']

class Cover:
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
    def __init__(self, meta, rows, log=print):
        self.meta, self.rows, self.log = meta, rows, log
        self.chip, self.mode = meta['chip'], meta['mode']
        if self.mode not in ('complex', 'registered'):
            sys.exit(f'fit: mode {self.mode} not supported (simple-mode chips fit as complex mode)')
        self.spins, self.inpins = meta['state'], meta['inputs']
        self.regs = list(meta.get('regs', []))
        self.V = sorted(CHIPS[self.chip]['col'][self.mode])
        self.limit_f = 7
        self.comb = [p for p in self.spins if p not in self.regs]
        self.si = {p: i for i, p in enumerate(self.spins)}
        self.xi = {p: i for i, p in enumerate(self.inpins)}
        self.held = meta.get('held', {})
        missing = [p for p in self.V if p not in self.si and p not in self.xi and p not in self.held]
        if missing:
            sys.exit(f'array input pins {missing} are neither state nor input pins in the table: re-explore with them '
                     f'listed (as --inputs if the chip never drives them)')
        self.names = [f'P{p}' for p in self.V]

    def vkeys(self, s, x):
        """Array-input keys for node (s, x). A Z feedback pin floats: the recorded behaviour held whatever level it
        was pulled to (the explorer pulls it both ways and would have read '?' otherwise), so the logic must give the
        same result for both levels: one key per combination. [] if a pin reads '?'."""
        keys = ['']
        for p in self.V:
            c = s[self.si[p]] if p in self.si else x[self.xi[p]] if p in self.xi else str(self.held[p])
            if c == 'Z': keys = [k + b for k in keys for b in '01']
            elif c in '01': keys = [k + c for k in keys]
            else: return []
        return keys
    def vint(self, key): return sum((c == '1') << i for i, c in enumerate(key))
    def flip(self, x, m):
        i = self.xi[int(m[3:])]; return x[:i] + ('1' if x[i] == '0' else '0') + x[i + 1:]

    def constraints(self):
        cons = collections.defaultdict(dict); conflicts = collections.defaultdict(list)
        def add(fn, keys, val, src):
            for key in keys:
                old = cons[fn].get(key)
                if old is None: cons[fn][key] = (val, src)
                elif old[0] != val: conflicts[fn].append((key, old, (val, src)))
        nodes = set()
        for s, x, m, n in self.rows:
            nodes.add((s, x)); nodes.add((n, x if m == 'CLK' else self.flip(x, m)))
        for s, x in nodes:
            k = self.vkeys(s, x)
            for p in self.comb:
                c = s[self.si[p]]
                if c in '01': add(('f', p), k, c, 'settled'); add(('oe', p), k, '1', 'settled')
                elif c == 'Z': add(('oe', p), k, '0', 'settled')
        for s, x, m, n in self.rows:
            if m == 'CLK':
                k = self.vkeys(s, x)
                for r in self.regs: add(('d', r), k, n[self.si[r]], 'clk')
                continue
            x2 = self.flip(x, m)
            changed = [p for p in self.spins if s[self.si[p]] != n[self.si[p]]]
            if len(changed) == 1 and changed[0] in self.comb:
                q = changed[0]; k = self.vkeys(s, x2); c = n[self.si[q]]
                if c in '01': add(('f', q), k, c, 'flip'); add(('oe', q), k, '1', 'flip')
                elif c == 'Z': add(('oe', q), k, '0', 'flip')
        self.nodes = nodes
        return cons, conflicts

    def targets_ok(self, cons, items):
        for p, k, t in items:
            if k is None: continue
            o = cons.get(('oe', p), {}).get(k)
            if t == 'Z':
                if o and o[0] != '0': return False
            else:
                if o and o[0] != '1': return False
                f = cons.get(('f', p), {}).get(k)
                if f and f[0] != t: return False
        return True

    def commit(self, cons, items, src):
        for p, k, t in items:
            if k is None: continue
            if t == 'Z': cons[('oe', p)].setdefault(k, ('0', src))
            else: cons[('oe', p)].setdefault(k, ('1', src)); cons[('f', p)].setdefault(k, (t, src))

    def path_items(self, t0, x, n, order):
        items, t = [], t0
        stay = [p for p in self.comb if t0[self.si[p]] == n[self.si[p]]]
        for q in order:
            for k in self.vkeys(t, x):
                items.append((q, k, n[self.si[q]]))
                items += [(p, k, t[self.si[p]]) for p in stay]
            i = self.si[q]; t = t[:i] + n[i] + t[i + 1:]
        return items

    def start(self, s, x, m):
        """State right after the move, before combinational settling (CLK: registers take D)."""
        if m != 'CLK': return s, self.flip(x, m)
        vs = [self.vint(k) for k in self.vkeys(s, x)]; t = s
        for r in self.regs:
            d = {'1' if self.fn[('d', r)](v) else '0' for v in vs}
            i = self.si[r]; t = t[:i] + (d.pop() if len(d) == 1 else 'X') + t[i + 1:]   # X: depends on a floating pin
        return t, x

    def refine(self, cons, max_rounds=30):
        for rnd in range(max_rounds):
            self.minimize(cons)
            fails = []
            for s, x, m, n in self.rows:
                t0, x2 = self.start(s, x, m)
                if self.settle(t0, x2) != {n}: fails.append((t0, x2, n))
            self.log(f'  refine round {rnd + 1}: {len(fails)} moves not reproduced exactly')
            if not fails: return True
            added = stuck = 0
            for t0, x2, n in fails:
                C = [p for p in self.comb if t0[self.si[p]] != n[self.si[p]]]
                best = None
                for order in itertools.permutations(C) if len(C) <= 6 else [tuple(C)]:
                    items = self.path_items(t0, x2, n, order)
                    if not self.targets_ok(cons, items): continue
                    agree = 0
                    for p, k, tg in items:
                        if k is None: continue
                        v = self.vint(k); oe = self.fn.get(('oe', p)); f = self.fn.get(('f', p))
                        cur = 'Z' if (oe is not None and not oe(v)) else ('1' if f and f(v) else '0')
                        agree += cur == tg
                    if best is None or agree > best[0]: best = (agree, items)
                if best is None: stuck += 1; continue
                self.commit(cons, best[1], 'path'); added += 1
            self.log(f'    added paths for {added} moves, {stuck} moves with no consistent order')
            if added == 0: return False
        return False

    def minimize(self, cons):
        self.fn = {}; report = []
        for fn in sorted(cons, key=lambda f: (f[1], f[0])):
            on = [k for k, (v, _) in cons[fn].items() if v == '1']
            off = [k for k, (v, _) in cons[fn].items() if v == '0']
            name = f'{fn[0]}{fn[1]}'
            pos = espresso(len(self.V), on, off, self.names, name)
            if fn[0] == 'oe':
                self.fn[fn] = Cover(pos); report.append((fn, len(on), len(off), 'pos', pos, len(pos) <= 1)); continue
            neg = espresso(len(self.V), off, on, self.names, name)
            inv = len(neg) < len(pos); cubes = neg if inv else pos
            self.fn[fn] = Cover(cubes, inv)
            report.append((fn, len(on), len(off), 'neg' if inv else 'pos', cubes,
                           len(cubes) <= (8 if fn[0] == 'd' else self.limit_f)))
        return report

    def comb_target(self, s, x, p):
        keys = self.vkeys(s, x)
        if not keys: return None
        oe = self.fn.get(('oe', p)); f = self.fn.get(('f', p))
        out = set()
        for k in keys:
            v = self.vint(k)
            if oe is not None and not oe(v): out.add('Z')
            elif f is None: out.add(s[self.si[p]])
            else: out.add('1' if f(v) else '0')
        return out.pop() if len(out) == 1 else 'X'       # X: the output would follow a floating pin

    def settle(self, s, x, cap=4096):
        seen, todo, stable = {s}, [s], set()
        while todo:
            t = todo.pop(); exc = []
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
            t0, x2 = self.start(s, x, m)
            out = self.settle(t0, x2)
            kind = ('no settle (oscillation?)' if out is None else 'exact' if out == {n} else
                    'ambiguous (race in the fitted logic)' if n in out else 'WRONG')
            res[kind] += 1
            if kind != 'exact' and len(bad) < 10: bad.append((s, x, m, n, sorted(out) if out else out))
        stable = sum(self.settle(s, x) == {s} for s, x in self.nodes)
        return res, bad, stable

    def cube_str(self, cube):
        lits = [('' if c == '1' else '/') + f'P{p}' for c, p in zip(cube, self.V) if c != '-']
        return ' * '.join(lits) if lits else 'VCC'

def run_fit(table, out_prefix, overrides=None, log=print):
    meta, rows = read_table(table, overrides)
    F = Fit(meta, rows, log)
    cons, conflicts = F.constraints()
    lines = [f'# fit of {table}: {CHIPS[F.chip]["name"]} {F.mode} mode, registers {F.regs or "-"}',
             f'# meta: {__import__("json").dumps(meta, sort_keys=True)}',
             f'# array inputs V = {F.names}', f'# {len(F.rows)} moves, {len(F.nodes)} settled nodes']
    ok_all = not conflicts
    if conflicts:
        lines.append('# CONFLICTS (no logic of this mode can explain the data):')
        for fn, c in conflicts.items(): lines.append(f'#   {fn}: {len(c)}, e.g. {c[:3]}')
    else:
        conv = F.refine(cons)
        ok_all &= conv
        lines.append(f'# refinement from multi-pin moves: {"converged" if conv else "DID NOT CONVERGE"}')
    rep = F.minimize(cons)
    lines.append('# function: on/off points, polarity, terms, fits the GAL term limit')
    for fn, non, noff, pol, cubes, ok in rep:
        ok_all &= ok
        lines.append(f'#   {fn[0]}{fn[1]}: on {non} off {noff}, {pol}, {len(cubes)} terms{"" if ok else "  ** TOO MANY TERMS **"}')
    res, bad, stable = F.validate()
    ok_all &= res['exact'] == len(rows) and stable == len(F.nodes)
    lines.append(f'# validation: {dict(res)}; settled nodes stable in the model: {stable}/{len(F.nodes)}')
    for b in bad: lines.append(f'#   {b}')
    x0 = '0' * len(F.inpins)
    for pu in sorted(set(meta.get('power_up', []))):
        st = F.settle(pu, x0) == {pu}
        ok_all &= st
        lines.append(f'# power-up state {pu} with inputs 0: stable in the model: {st}')
    lines.append(f'# RESULT: {"PASS" if ok_all else "FAIL"}')
    lines.append('')
    for fn, non, noff, pol, cubes, ok in rep:
        kind, p = fn
        lhs = {'f': f'P{p}', 'd': f'P{p}.R', 'oe': f'P{p}.E'}[kind]
        if kind != 'oe' and pol == 'neg': lhs = '/' + lhs
        rhs = [F.cube_str(c) for c in cubes] if cubes else ['GND']     # OR of no term; '/' on lhs keeps polarity
        lines.append(f'{lhs} = ' + '\n    + '.join(rhs))
    out = '\n'.join(lines) + '\n'
    open(out_prefix + '_fit.txt', 'w').write(out)
    return ok_all, out
