"""Single-input-change (fundamental mode) state exploration of a GAL in the dupico. Read-only on the chip.

node = (S, x): S = levels of every driven output pin (registers, latches, plain outputs), x = the input vector,
       everything settled. A GAL16V8/20V8 has no buried state with /OE low: its whole state is on its pins.
move = flip one input pin, or one CLK pulse (rising then falling edge) with x held (registered mode).
Multi-input changes are never applied: latches built from output feedback would race.
Every reachable node gets every move measured once (random walk + BFS over known moves, power cycle when stuck).
Checks: registers never change on an input flip, inputs read back as driven, state pins driven unless allowed Z,
repeated moves agree; a random sample of moves is replayed at the end.
"""
import collections, random, time
from .dupico import power_cycle

class Explorer:
    def __init__(self, d, seat, state, regs, inputs, mode, allow_z=(), held=None, log=print):
        self.d, self.seat, self.log = d, seat, log
        self.held = {int(k): int(v) for k, v in (held or {}).items()}
        c = seat.c
        self.mode = mode
        self.clk = c['clk'] if mode == 'registered' else None
        self.oe = c['oe'] if mode == 'registered' else None
        bad = (set(state) & set(inputs)) | (set(self.held) & (set(state) | set(inputs)))
        if bad: raise SystemExit(f'pins {sorted(bad)} are in more than one of state / inputs / held')
        if mode == 'registered' and ({self.clk, self.oe} & (set(state) | set(inputs) | set(self.held))):
            raise SystemExit(f'registered mode: pins {self.clk} (CLK) and {self.oe} (/OE) are driven by the explorer, '
                             f'leave them out of --state/--inputs')
        self.spins = sorted(state, reverse=True)
        self.regidx = [i for i, p in enumerate(self.spins) if p in regs]
        self.inputs = sorted(inputs)
        self.desc = sorted(inputs, reverse=True)
        self.moves = list(range(len(self.inputs))) + ([-1] if self.clk else [])
        self.zok = [i for i, p in enumerate(self.spins) if p in allow_z]
        self.pull = sum(seat.bit(p) for p in self.spins)
        self.E = {}
        self.adj = collections.defaultdict(dict)
        self.issues = collections.Counter()
        self.examples = collections.defaultdict(list)
        self.power_ups = []

    def note(self, kind, detail):
        self.issues[kind] += 1
        if len(self.examples[kind]) < 5: self.examples[kind].append(detail)

    def drv(self, x):
        lv = {**self.held, **{p: x >> i & 1 for i, p in enumerate(self.inputs)}}
        return self.seat.drive(lv)                         # CLK and /OE stay 0
    def xs(self, x): return ''.join(str(x >> self.inputs.index(p) & 1) for p in self.desc)
    def xparse(self, s): return sum(int(c) << self.inputs.index(p) for c, p in zip(s, self.desc))
    def ms(self, m): return 'CLK' if m < 0 else f'pin{self.inputs[m]}'
    def regs_of(self, s): return ''.join(s[i] for i in self.regidx)

    def read_state(self, x, lo=None):
        v = self.drv(x); b = self.seat.bit
        if lo is None: lo = self.d.write(v)
        hi = self.d.write(v | self.pull); self.d.write(v)
        s = ''
        for p in self.spins:
            l, h = bool(lo & b(p)), bool(hi & b(p))
            s += '1' if l and h else '0' if not (l or h) else 'Z' if h else '?'
        fight = [p for i, p in enumerate(self.inputs) if bool(lo & b(p)) != bool(x >> i & 1)]
        if fight: self.note('input pin driven by the chip', (self.xs(x), fight))
        hfight = [p for p, lv in self.held.items() if bool(lo & b(p)) != bool(lv)]
        if hfight: self.note('held pin driven by the chip', (self.xs(x), hfight))
        if any(c not in '01' and i not in self.zok for i, c in enumerate(s)):
            self.note('state pin not driven', (self.xs(x), s))
        return s

    def move(self, node, m):
        s, x = node
        if m < 0:
            v = self.drv(x); self.d.write(v | self.seat.bit(self.clk)); lo = self.d.write(v); nx = x
        else:
            nx = x ^ (1 << m); lo = self.d.write(self.drv(nx))
        ns = self.read_state(nx, lo)
        if m >= 0 and self.regs_of(ns) != self.regs_of(s):
            self.note('register changed on an input flip', (s, self.xs(x), self.ms(m), ns))
        key = (s, x, m); old = self.E.get(key)
        if old is None:
            self.E[key] = ns; self.adj[node][m] = (ns, nx)
        elif old != ns:
            self.note('move not repeatable', (s, self.xs(x), self.ms(m), old, ns))
        return (ns, nx)

    def power_up(self):
        self.d.write(0); power_cycle(self.d)
        s = self.read_state(0)
        self.power_ups.append(s)
        return (s, 0)

    def unexplored(self, node): return len(self.adj[node]) < len(self.moves)

    def bfs(self, src, want):
        prev, q = {src: None}, collections.deque([src])
        while q:
            n = q.popleft()
            if want(n):
                path = []
                while prev[n]: n, m = prev[n]; path.append(m)
                return path[::-1]
            for m, nn in self.adj[n].items():
                if nn not in prev: prev[nn] = (n, m); q.append(nn)
        return None

    def nodes(self):
        return set(self.adj) | {nn for d in self.adj.values() for nn in d.values()}

    def goto(self, cur, target):
        for _ in range(3):
            path = self.bfs(cur, lambda t: t == target)
            if path is None: cur = self.power_up(); continue
            for y in path:
                want = self.adj[cur][y]; cur = self.move(cur, y)
                if cur != want: break
            if cur == target: return cur, True
        return cur, False

    def run(self, seed=1, max_power_cycles=200):
        rng = random.Random(seed)
        cur = self.power_up()
        self.log(f'power-up state (pins {",".join(map(str, self.spins))}) = {cur[0]} with inputs all 0')
        t0, shown, last, steps, stall = time.time(), 0, 0, 0, 0
        while True:
            steps += 1
            if len(self.E) != last: last, stall = len(self.E), steps
            if steps - stall > 5000: self.log('  STALLED: no new move in 5000 steps, stopping'); break
            if len(self.power_ups) > max_power_cycles: self.log('  too many power cycles, stopping'); break
            todo = [m for m in self.moves if m not in self.adj[cur]]
            flips = [m for m in todo if m >= 0]
            if todo:   # input flips first: a clock can leave states (e.g. power-up register values) for good
                cur = self.move(cur, rng.choice(flips or todo))
            else:
                path = self.bfs(cur, self.unexplored)
                if path is None:
                    if not any(self.unexplored(n) for n in self.nodes()): break
                    self.log(f'  no known path from {cur[0]}/{self.xs(cur[1])} to an unexplored node: power cycling')
                    cur = self.power_up(); continue
                for m in path:
                    want = self.adj[cur][m]; cur = self.move(cur, m)
                    if cur != want: break
            if len(self.E) // 5000 != shown:
                shown = len(self.E) // 5000
                self.log(f'  {len(self.E)} moves, {len(self.nodes())} nodes, {len({n[0] for n in self.nodes()})} '
                         f'states, {self.d.cmds} commands, {time.time() - t0:.0f} s, issues {dict(self.issues)}')
        return cur

    def load(self, rows):
        names = {self.ms(m): m for m in self.moves}
        for s, xb, mv, n in rows:
            x = self.xparse(xb); m = names[mv]
            nx = x if m < 0 else x ^ (1 << m)
            self.E[(s, x, m)] = n; self.adj[(s, x)][m] = (n, nx)

    def recheck_clk(self, cur, rounds, every=False):
        """Re-clock CLK moves `rounds` times each (all of them with every=True, otherwise only those whose result
        is not a fixed point under a second clock, where a double clock would show) and keep the majority."""
        G = {(s, x): n for (s, x, m), n in self.E.items() if m < 0}
        keys = sorted(G) if every else sorted(k for k, n in G.items() if G.get((n, k[1]), n) != n)
        tally = collections.defaultdict(collections.Counter)
        for k in keys: tally[k][G[k]] += 1
        self.log(f'  re-clocking {len(keys)} CLK moves x {rounds}')
        for r in range(rounds):
            for s, x in keys:
                cur, ok = self.goto(cur, (s, x))
                if not ok: continue
                v = self.drv(x); self.d.write(v | self.seat.bit(self.clk)); lo = self.d.write(v)
                ns = self.read_state(x, lo); tally[(s, x)][ns] += 1; cur = (ns, x)
            self.log(f'  round {r + 1}: {sum(len(c) > 1 for c in tally.values())} CLK moves with more than one result')
        for k, c in tally.items():
            best = c.most_common(1)[0][0]
            self.E[(k[0], k[1], -1)] = best; self.adj[k][-1] = (best, k[1])
        return tally

    def verify(self, cur, n, seed=2):
        """Replay n moves of self.E (all of them if n <= 0 or larger than the table); returns (bad, done, examples)."""
        rng = random.Random(seed)
        keys = sorted(self.E)
        sample = keys if n <= 0 or n >= len(keys) else rng.sample(keys, n)
        bad = done = 0; ex = []
        for s, x, m in sample:
            cur, ok = self.goto(cur, (s, x))
            if not ok: continue
            exp = self.E[(s, x, m)]
            if m < 0:
                v = self.drv(x); self.d.write(v | self.seat.bit(self.clk)); lo = self.d.write(v); nx = x
            else:
                nx = x ^ (1 << m); lo = self.d.write(self.drv(nx))
            ns = self.read_state(nx, lo); done += 1
            if ns != exp:
                bad += 1
                if len(ex) < 10: ex.append((s, self.xs(x), self.ms(m), exp, ns))
                cur = (ns, nx)
            else:
                cur = self.adj[(s, x)][m]
        return bad, done, ex

    def rows(self):
        return [(s, self.xs(x), self.ms(m), self.E[(s, x, m)])
                for (s, x, m) in sorted(self.E, key=lambda k: (k[0], self.xs(k[1]), k[2]))]
