#!/usr/bin/env python3
"""State-graph explorer for a GAL16V8 in registered mode whose state is all visible on its pins.
Read-only: the chip is only driven through the dupico's 10K resistors and clocked; nothing is programmed.

Placement as gal16v8_direct.toml: top-aligned in the ZIF42, pin 1 at ZIF 1, pins 11-20 at ZIF 33-42,
dupont jumper header 10 -> 21 (GND). Pin 1 = CLK, pin 11 = /OE (held 0 so registered outputs are driven).

Single-input-change (fundamental mode) model, because combinational outputs that feed back on themselves
(latches) make multi-input changes race (seen on AdSCSI U4):
  node = (S, x): S = values of all --state pins (registers, latches, plain outputs: list every driven pin, plain
         outputs add no nodes since they follow from the rest), x = current input vector, everything settled
  move = flip one input pin, or one CLK pulse (rising then falling edge) with x held
For every reachable node every move is measured once (navigate with known moves, BFS; power cycle if stuck).
Checks: --regs pins never change on an input flip, input pins read back as driven, state pins are driven
(not Z), repeated moves agree; a random sample of moves is replayed at the end.

usage: python3 regexplore16.py --state 12-18 --regs 12,15,16 --inputs 2-9,19 -o u4_moves.tsv
       python3 regexplore16.py --no-clk --state 12-19 --allow-z 12,19 --inputs 1-9,11 -o u5_moves.tsv   (complex mode)
"""
import argparse, collections, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dupico_check import Dupico, PIN2BIT

ZIF = [1, 2, 3, 4, 5, 6, 7, 8, 9, 21, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42]
CLK, OE = 1, 11
def bit(p): return 1 << PIN2BIT[ZIF[p - 1]]

def pinlist(s):
    out = []
    for part in filter(None, s.split(',')):
        a, _, b = part.partition('-')
        out += range(int(a), int(b or a) + 1)
    return out

class Explorer:
    def __init__(self, d, spins, regs, inputs, clk=True, allow_z=()):
        self.d = d
        self.spins = sorted(spins, reverse=True)               # state string: highest pin first
        self.regidx = [i for i, p in enumerate(self.spins) if p in regs]
        self.inputs = sorted(inputs)                           # x bit i = self.inputs[i]
        self.desc = sorted(inputs, reverse=True)
        self.moves = list(range(len(self.inputs))) + ([-1] if clk else [])   # -1 = CLK pulse
        self.zok = [i for i, p in enumerate(self.spins) if p in allow_z]   # pins where Z is a legal value
        self.pull = sum(bit(p) for p in self.spins)
        self.E = {}                                            # (S, x, move) -> S'
        self.adj = collections.defaultdict(dict)               # node -> {move: node'}
        self.issues = collections.Counter()
        self.examples = collections.defaultdict(list)
        self.cmds = 0
        self.power_ups = []

    def note(self, kind, detail):
        self.issues[kind] += 1
        if len(self.examples[kind]) < 5: self.examples[kind].append(detail)

    def drv(self, x): return sum(bit(p) for i, p in enumerate(self.inputs) if x >> i & 1)
    def xs(self, x):  return ''.join(str(x >> self.inputs.index(p) & 1) for p in self.desc)
    def ms(self, m):  return 'CLK' if m < 0 else f'pin{self.inputs[m]}'
    def regs_of(self, s): return ''.join(s[i] for i in self.regidx)

    def read_state(self, x, lo=None):
        v = self.drv(x)
        if lo is None: lo = self.d.write(v); self.cmds += 1
        hi = self.d.write(v | self.pull); self.d.write(v); self.cmds += 2
        s = ''
        for p in self.spins:
            l, h = bool(lo & bit(p)), bool(hi & bit(p))
            s += '1' if l and h else '0' if not (l or h) else 'Z' if h else '?'
        fight = [p for i, p in enumerate(self.inputs) if bool(lo & bit(p)) != bool(x >> i & 1)]
        if fight: self.note('input pin driven by the chip', (self.xs(x), fight))
        if any(c not in '01' and i not in self.zok for i, c in enumerate(s)):
            self.note('state pin not driven', (self.xs(x), s))
        return s

    def move(self, node, m):
        s, x = node
        if m < 0:
            v = self.drv(x); self.d.write(v | bit(CLK)); self.cmds += 1
            lo = self.d.write(v); self.cmds += 1
            nx = x
        else:
            nx = x ^ (1 << m)
            lo = self.d.write(self.drv(nx)); self.cmds += 1
        ns = self.read_state(nx, lo)
        if m >= 0 and self.regs_of(ns) != self.regs_of(s):
            self.note('register changed on an input flip', (s, self.xs(x), self.ms(m), ns))
        key = (s, x, m)
        old = self.E.get(key)
        if old is None:
            self.E[key] = ns; self.adj[node][m] = (ns, nx)
        elif old != ns:
            self.note('move not repeatable', (s, self.xs(x), self.ms(m), old, ns))
        return (ns, nx)

    def power_up(self):
        d = self.d
        d.power(False); d.write(0)
        t0 = time.time()
        while d.read() and time.time() - t0 < 30: time.sleep(0.25)
        time.sleep(1.0)
        d.power(True); time.sleep(0.5)
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

    def run(self, seed=1):
        rng = random.Random(seed)
        cur = self.power_up()
        print(f'power-up state (pins {",".join(map(str, self.spins))}) = {cur[0]} with inputs all 0', flush=True)
        t0, shown, last, steps, stall = time.time(), 0, 0, 0, 0
        while True:
            steps += 1
            if len(self.E) != last: last, stall = len(self.E), steps
            if steps - stall > 5000:
                print(f'  STALLED: no new move measured in 5000 moves, stopping', flush=True); break
            if len(self.power_ups) > 20:
                print('  too many power cycles, stopping', flush=True); break
            todo = [m for m in self.moves if m not in self.adj[cur]]
            if todo:
                cur = self.move(cur, rng.choice(todo))
            else:
                path = self.bfs(cur, self.unexplored)
                if path is None:
                    if not any(self.unexplored(n) for n in self.nodes()): break
                    print(f'  no known path from {cur[0]}/{self.xs(cur[1])} to an unexplored node: power cycling', flush=True)
                    cur = self.power_up(); continue
                for m in path:
                    want = self.adj[cur][m]
                    cur = self.move(cur, m)
                    if cur != want: break                       # diverged (reported as not repeatable): replan
            if len(self.E) // 5000 != shown:
                shown = len(self.E) // 5000
                print(f'  {len(self.E)} moves, {len(self.nodes())} nodes, {len({n[0] for n in self.nodes()})} states, '
                      f'{self.cmds} commands, {time.time() - t0:.0f} s, issues {dict(self.issues)}', flush=True)
        return cur

    def load(self, path):
        """Rebuild E/adj from a table written by this script (same --state/--inputs)."""
        idx = {p: i for i, p in enumerate(self.inputs)}
        names = {self.ms(m): m for m in self.moves}
        for l in open(path):
            if l.startswith('#') or l.startswith('state\t'): continue
            s, xb, mv, n = l.rstrip('\n').split('\t')
            x = sum(int(c) << idx[p] for c, p in zip(xb, self.desc))
            m = names[mv]
            nx = x if m < 0 else x ^ (1 << m)
            self.E[(s, x, m)] = n; self.adj[(s, x)][m] = (n, nx)

    def recheck_clk(self, cur, rounds, every=False):
        """Re-clock every CLK move whose result is not a fixed point under a second clock (where a double
        clock would show), `rounds` times each; keep the majority. Returns {(s, x): Counter of results}."""
        G = {(s, x): n for (s, x, m), n in self.E.items() if m < 0}
        keys = sorted(G) if every else sorted(k for k, n in G.items() if G.get((n, k[1]), n) != n)
        tally = collections.defaultdict(collections.Counter)
        for k in keys: tally[k][G[k]] += 1                      # the exploration's observation
        print(f'  rechecking {len(keys)} CLK moves x {rounds} rounds', flush=True)
        for r in range(rounds):
            for i, (s, x) in enumerate(keys):
                for _ in range(3):
                    path = self.bfs(cur, lambda t: t == (s, x))
                    if path is None: cur = self.power_up(); continue
                    for y in path:
                        want = self.adj[cur][y]; cur = self.move(cur, y)
                        if cur != want: break
                    if cur == (s, x): break
                else:
                    continue
                v = self.drv(x); self.d.write(v | bit(CLK)); lo = self.d.write(v); self.cmds += 2
                ns = self.read_state(x, lo)
                tally[(s, x)][ns] += 1
                cur = (ns, x)
            flaky = sum(len(c) > 1 for c in tally.values())
            print(f'  round {r + 1}: {flaky} CLK moves with more than one result so far, {self.cmds} commands', flush=True)
        for k, c in tally.items():
            best = c.most_common(1)[0][0]
            self.E[(k[0], k[1], -1)] = best; self.adj[k][-1] = (best, k[1])
        return tally

    def verify(self, cur, n, seed=2):
        rng = random.Random(seed)
        bad = done = 0
        for s, x, m in rng.sample(sorted(self.E), min(n, len(self.E))):
            path = self.bfs(cur, lambda t: t == (s, x))
            if path is None:
                cur = self.power_up(); path = self.bfs(cur, lambda t: t == (s, x))
                if path is None: continue
            for y in path: cur = self.move(cur, y)
            if cur != (s, x): continue
            cur = self.move(cur, m); done += 1
            bad += cur[0] != self.E[(s, x, m)]
        return bad, done

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-p', '--port', default='/dev/ttyACM0')
    ap.add_argument('--state', required=True, help='every driven output pin')
    ap.add_argument('--regs', default='', help='subset of --state that must only change on CLK')
    ap.add_argument('--no-clk', action='store_true', help='complex/simple mode: no CLK move, pins 1 and 11 may be inputs')
    ap.add_argument('--allow-z', default='', help='state pins with an output enable term, where Z is a legal value')
    ap.add_argument('--inputs', required=True)
    ap.add_argument('--verify', type=int, default=2000, help='random moves to replay at the end')
    ap.add_argument('--load', help='skip exploration: start from a table written by this script')
    ap.add_argument('--recheck-clk', type=int, default=0, metavar='N',
                    help='re-clock every double-clock-sensitive CLK move N times, keep the majority')
    ap.add_argument('--recheck-all', action='store_true', help='with --recheck-clk: re-clock every CLK move')
    ap.add_argument('-o', '--outfile', required=True)
    a = ap.parse_args()
    d = Dupico(a.port)
    print('model', d.model(), 'fw', d.version())
    e = Explorer(d, pinlist(a.state), pinlist(a.regs), pinlist(a.inputs), not a.no_clk, pinlist(a.allow_z))
    t0 = time.time()
    tally = {}
    try:
        if a.load:
            e.load(a.load); cur = e.power_up()
        else:
            cur = e.run()
        if a.recheck_clk:
            tally = e.recheck_clk(cur, a.recheck_clk, a.recheck_all); cur = e.power_up()
        bad, done = e.verify(cur, a.verify)
        e.power_up()
    finally:
        d.write(0); d.power(False)
    if tally:
        flaky = {k: c for k, c in tally.items() if len(c) > 1}
        tfile = os.path.splitext(a.outfile)[0] + '_clk_recheck.tsv'
        with open(tfile, 'w') as f:
            f.write(f'# CLK moves re-clocked {a.recheck_clk}x (+ the exploration observation); results with counts. '
                    f'{len(flaky)} of {len(tally)} gave more than one result.\nstate\tinputs\tresults\n')
            for (s, x), c in sorted(tally.items(), key=lambda kv: (kv[0][0], e.xs(kv[0][1]))):
                f.write(f'{s}\t{e.xs(x)}\t{" ".join(f"{n}:{k}" for n, k in c.most_common())}\n')
        print(f'CLK recheck: {len(flaky)} of {len(tally)} moves gave more than one result; wrote {tfile}')
        for (s, x), c in list(flaky.items())[:10]: print(f'  {s} {e.xs(x)}: {dict(c)}')
    nodes = e.nodes()
    states = sorted({n[0] for n in nodes})
    print(f'done in {time.time() - t0:.0f} s, {e.cmds} commands: {len(nodes)} nodes, {len(states)} distinct pin states, '
          f'{len(e.E)} moves, power-up states {dict(collections.Counter(e.power_ups))}, replayed {done}: {bad} mismatches')
    for k, v in e.issues.items(): print(f'  ISSUE {k}: {v}x, e.g. {e.examples[k]}')
    with open(a.outfile, 'w') as f:
        f.write(f'# GAL16V8 single-input-change move table, regexplore16.py, {time.strftime("%Y-%m-%d %H:%M")}\n')
        f.write(('# no CLK (combinational mode). ' if a.no_clk else f'# CLK pin {CLK}, /OE pin {OE} held 0. ') + f'state = pins {e.spins} (registers {sorted(pinlist(a.regs), reverse=True)}); '
                f'inputs = pins {e.desc}\n')
        f.write(f'# power-up states {dict(collections.Counter(e.power_ups))} (inputs all 0); {len(nodes)} nodes; '
                f'{len(states)} distinct pin states; replay mismatches {bad}/{done}; issues {dict(e.issues) or "none"}\n')
        f.write('# each row: settled node (state, inputs), move (flip one input pin, or CLK = rising+falling edge), settled state after\n')
        f.write('state\tinputs\tmove\tnext\n')
        for (s, x, m) in sorted(e.E, key=lambda k: (k[0], e.xs(k[1]), k[2])):
            f.write(f'{s}\t{e.xs(x)}\t{e.ms(m)}\t{e.E[(s, x, m)]}\n')
    print('wrote', a.outfile)

if __name__ == '__main__':
    main()
