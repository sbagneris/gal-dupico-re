#!/usr/bin/env python3
"""Clock-stress test for a registered GAL16V8 already mapped with regexplore16.py: repeatedly clock the CLK moves
where at least --min-switch outputs change, and count results that differ from the reference table (the
majority / single-clock result). Used on AdSCSI U4 to test the ground-bounce explanation of double clocking.
Read-only. Placement as gal16v8_direct.toml (top-aligned, pin 1 at ZIF 1, jumper header 10 -> 21).

--pull low   state pins pulled low through the dupico's 10K during the clock edge (as in regexplore16.py)
--pull high  state pins pulled high during the edge (reverses the DC load current of switching outputs)
Each run appends one line to --log with the conditions given in --note.

usage: python3 clkstress16.py --table u4_moves_checked_all.tsv --state 12-18 --regs 12,15,16 --inputs 2-9,19 \\
           --rounds 10 --pull low --note "100nF pin 20-10" --log clkstress.tsv
"""
import argparse, collections, math, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dupico_check import Dupico
from regexplore16 import Explorer, pinlist, bit, CLK

def wilson(k, n, z=1.96):
    if n == 0: return 0.0, 0.0
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-p', '--port', default='/dev/ttyACM0')
    ap.add_argument('--table', required=True)
    ap.add_argument('--state', required=True); ap.add_argument('--regs', default=''); ap.add_argument('--inputs', required=True)
    ap.add_argument('--min-switch', type=int, default=3, help='select CLK moves where at least this many state pins change')
    ap.add_argument('--rounds', type=int, default=10)
    ap.add_argument('--pull', choices=('low', 'high'), default='low')
    ap.add_argument('--note', default='', help='test conditions for the log (decoupling, jumper, ...)')
    ap.add_argument('--log', default='clkstress.tsv')
    a = ap.parse_args()

    d = Dupico(a.port)
    e = Explorer(d, pinlist(a.state), pinlist(a.regs), pinlist(a.inputs))
    e.load(a.table)
    G = {(s, x): n for (s, x, m), n in e.E.items() if m < 0}
    nsw = lambda s, n: sum(i != j for i, j in zip(s, n))
    sel = sorted(k for k, n in G.items() if nsw(k[0], n) >= a.min_switch)
    pull = e.pull if a.pull == 'high' else 0
    print(f'model {d.model()} fw {d.version()}; {len(sel)} CLK moves with >= {a.min_switch} switching outputs, '
          f'{a.rounds} rounds, outputs pulled {a.pull} at the edge; note: {a.note or "-"}', flush=True)
    pulses = odd = dbl = other = 0
    pat = collections.Counter()
    t0 = time.time()
    cur = e.power_up()
    try:
        for r in range(a.rounds):
            for s, x in sel:
                for _ in range(3):
                    path = e.bfs(cur, lambda t: t == (s, x))
                    if path is None: cur = e.power_up(); continue
                    for y in path:
                        want = e.adj[cur][y]; cur = e.move(cur, y)
                        if cur != want: break
                    if cur == (s, x): break
                else:
                    continue
                v = e.drv(x)
                if pull: d.write(v | pull)                     # settle the pulls first: only CLK switches at the edge
                d.write(v | pull | bit(CLK)); d.write(v | pull)
                ns = e.read_state(x)
                pulses += 1
                if ns != G[(s, x)]:
                    odd += 1
                    if ns == G.get((G[(s, x)], x)): dbl += 1
                    else: other += 1
                    pat[(s, G[(s, x)], ns)] += 1
                cur = (ns, x)
            lo, hi = wilson(odd, pulses)
            print(f'  round {r + 1}: {odd} odd in {pulses} pulses = {odd / pulses:.2%} (95% CI {lo:.2%}-{hi:.2%}), '
                  f'{dbl} double clocks, {other} other, {time.time() - t0:.0f} s', flush=True)
    finally:
        d.write(0); d.power(False)
    lo, hi = wilson(odd, pulses)
    print(f'RESULT pull {a.pull}, {a.note or "-"}: {odd}/{pulses} = {odd / max(pulses, 1):.2%} (95% CI {lo:.2%}-{hi:.2%}); '
          f'double clocks {dbl}, other {other}')
    for (s, g, n), c in pat.most_common(5): print(f'  {s} -> expected {g}, got {n}: {c}x')
    new = not os.path.exists(a.log)
    with open(a.log, 'a') as f:
        if new: f.write('date\tnote\tpull\tmin_switch\tmoves\tpulses\todd\tdouble\tother\trate\tci95_lo\tci95_hi\n')
        f.write(f'{time.strftime("%Y-%m-%d %H:%M")}\t{a.note}\t{a.pull}\t{a.min_switch}\t{len(sel)}\t{pulses}\t{odd}\t'
                f'{dbl}\t{other}\t{odd / max(pulses, 1):.4f}\t{lo:.4f}\t{hi:.4f}\n')

if __name__ == '__main__':
    main()
