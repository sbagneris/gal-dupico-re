#!/usr/bin/env python3
"""galre - recover the logic of a read-protected GAL16V8 / GAL20V8 with a DuPAL V3 "dupico", read-only.

Bench commands (need the board, or --sim JED for a simulated board holding a GAL programmed with that JED):
  check                      dupico bring-up test, EMPTY socket
  probe                      placement/ground check, pin classes, mode, suggested exploration -> probe.json
  dump / ordercheck          first-look truth table and its history check (is a plain dump the logic? usually no)
  explore                    single-input-change state exploration -> move table (the record of the chip)
  replay TABLE               replay a move table on the chip in the socket (programmed copy vs the original)
Offline commands:
  fit TABLE                  fit equations (espresso), refine, validate on every move -> PREFIX_fit.txt
  pld FIT                    GALasm source (+ JED with --galasm)
  jedcheck JED TABLE         replay every move of the table on the JED's fuse map
  compare A B                compare two move tables
  selftest                   whole pipeline on the bundled example JEDs with the simulated board

Common bench options: --chip 16v8|20v8, --seat direct|pod, -p PORT (default /dev/ttyACM0), --sim JED.
"""
import argparse, collections, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from galre.chips import Seat, CHIPS
from galre.dupico import open_board
from galre.table import pinlist, read_table, write_table

def bench_args(ap):
    ap.add_argument('--chip', choices=sorted(CHIPS), help='16v8 or 20v8 (default: from --probe file, else 16v8)')
    ap.add_argument('--seat', choices=('direct', 'pod'), help='direct = no PAL POD, chip top-aligned in the ZIF42 '
                    '(GND wire + 100 nF cap); pod = in the PAL POD (default: from --probe file, else direct)')
    ap.add_argument('-p', '--port', default='/dev/ttyACM0')
    ap.add_argument('--sim', metavar='JED', help='simulated board holding a GAL programmed with this JED')
    ap.add_argument('--sim-fault', choices=('noground', 'doubleclock'), help='simulate a bench fault')

def from_probe(a):
    pr = json.load(open(a.probe)) if getattr(a, 'probe', None) else {}
    a.chip = a.chip or pr.get('chip') or '16v8'; a.seat = a.seat or pr.get('placement') or 'direct'
    return pr

def open_bench(a):
    seat = Seat(a.chip, a.seat)
    d = open_board(a, seat)
    if not d.fake and d.model() != 3: sys.exit(f'board model {d.model()}: this tool needs a DuPAL V3 (dupico)')
    print(d.describe()); print('setup:', seat.setup_text(), flush=True)
    return d, seat

def pins_or(a, name, pr, key):
    v = getattr(a, name)
    return pinlist(v) if v is not None else list(pr.get(key, []))

def cmd_check(a):
    from galre.bench import bringup
    a.chip, a.seat = a.chip or '16v8', a.seat or 'direct'
    d, _ = open_bench(a)
    return 0 if bringup(d, interactive=not a.yes) else 1

def cmd_probe(a):
    from galre.bench import probe
    a.chip, a.seat = a.chip or '16v8', a.seat or 'direct'
    d, seat = open_bench(a)
    try: rep = probe(d, seat)
    finally: d.write(0); d.power(False)
    json.dump(rep, open(a.out, 'w'), indent=1); print('wrote', a.out)
    return 0 if rep['verdict'] == 'OK' else 1

def cmd_dump(a):
    from galre.bench import dump
    pr = from_probe(a); d, seat = open_bench(a)
    inputs, outs = sorted(pins_or(a, 'inputs', pr, 'inputs')), pins_or(a, 'state', pr, 'state')
    fixed = {CHIPS[a.chip]['clk']: 0, CHIPS[a.chip]['oe']: 0} if pr.get('mode') == 'registered' else {}
    try: res = dump(d, seat, inputs, outs, fixed)
    finally: d.write(0); d.power(False)
    with open(a.out, 'w') as f:
        f.write('# meta: ' + json.dumps(dict(chip=a.chip, inputs=inputs, outputs=sorted(outs, reverse=True),
                                              fixed=fixed, order='ascending')) + '\n')
        f.write(f'# input vector x: bit i = pin {inputs} [i]; outputs highest pin first\n')
        for x, o in res.items(): f.write(f'{x:X}\t{o}\n')
    print(f'wrote {a.out}: {len(res)} entries')
    from galre.bench import influence
    none = influence(res, inputs)
    if none:
        print(f'input pins with no effect anywhere in this snapshot: {none}. NOT a reason to hold them: in a stateful chip '
              f'an effect can show only in states the snapshot never held (AdSCSI U3 pins 2-3 and U4 pin 2 looked like '
              f'this and appear in the recovered equations). Hold a pin (explore --hold) only when the board shows it unconnected or tied.')
    return 0

def cmd_ordercheck(a):
    from galre.bench import ordercheck
    meta = json.loads(open(a.dump).readline()[7:])
    a.chip = a.chip or meta['chip']; a.seat = a.seat or 'direct'
    ref = {int(l.split('\t')[0], 16): l.split('\t')[1].strip() for l in open(a.dump) if not l.startswith('#')}
    d, seat = open_bench(a)
    try: bad = ordercheck(d, seat, meta['inputs'], meta['outputs'], ref, {int(k): v for k, v in meta['fixed'].items()})
    finally: d.write(0); d.power(False)
    print('PASS: no history dependence seen' if not bad else
          'FAIL: outputs depend on input history (latches or registers): use explore, not the dump')
    return 1 if bad else 0

def cmd_explore(a):
    from galre.explore import Explorer
    pr = from_probe(a)
    mode = a.mode or pr.get('mode') or 'complex'
    state = pins_or(a, 'state', pr, 'state'); inputs = pins_or(a, 'inputs', pr, 'inputs')
    regs = pins_or(a, 'regs', pr, 'regs') if mode == 'registered' else []
    allow_z = pins_or(a, 'allow_z', pr, 'allow_z')
    held = {int(k): int(v) for k, v in (kv.split('=') for kv in a.hold.split(','))} if a.hold else {}
    inputs = [p for p in inputs if p not in held]
    if not state or not inputs: sys.exit('give --state and --inputs, or --probe probe.json')
    loaded = None
    if a.load:
        loaded, rows = read_table(a.load)
        state, inputs, regs, allow_z, mode = loaded['state'], loaded['inputs'], loaded['regs'], loaded['allow_z'], loaded['mode']
        held = loaded['held']
        a.chip = loaded['chip']
    d, seat = open_bench(a)
    e = Explorer(d, seat, state, regs, inputs, mode, allow_z, held)
    print(f'held {held or "none"}; {mode} mode; state pins {e.spins} (registers {sorted(regs, reverse=True)}), inputs {e.desc}, allow Z {allow_z}',
          flush=True)
    t0 = time.time(); tally = {}
    try:
        if loaded: e.load(rows); cur = e.power_up()
        else: cur = e.run(seed=a.seed)
        if a.recheck_clk and mode == 'registered':
            tally = e.recheck_clk(cur, a.recheck_clk, a.recheck_all); cur = e.power_up()
        bad, done, ex = e.verify(cur, a.verify)
        e.power_up()
    finally:
        d.write(0); d.power(False)
    nodes = e.nodes(); states = {n[0] for n in nodes}
    pu = dict(collections.Counter(e.power_ups))
    meta = dict(chip=seat.chip, placement=seat.placement, mode=mode, state=e.spins, regs=sorted(regs, reverse=True),
                inputs=e.desc, held=held, allow_z=sorted(allow_z, reverse=True), power_up=sorted(pu), nodes=len(nodes),
                pin_states=len(states), moves=len(e.E), replay=[bad, done], issues=dict(e.issues),
                simulated=bool(d.fake), recheck_clk=a.recheck_clk if tally else 0)
    if tally:
        flaky = {k: c for k, c in tally.items() if len(c) > 1}
        meta['clk_moves_with_disagreeing_results'] = len(flaky)
        tfile = os.path.splitext(a.out)[0] + '_clk_recheck.tsv'
        with open(tfile, 'w') as f:
            f.write(f'# CLK moves re-clocked {a.recheck_clk}x (+ the exploration); results with counts. {len(flaky)} '
                    f'of {len(tally)} gave more than one result.\nstate\tinputs\tresults\n')
            for (s, x), c in sorted(tally.items(), key=lambda kv: (kv[0][0], e.xs(kv[0][1]))):
                f.write(f'{s}\t{e.xs(x)}\t{" ".join(f"{n}:{k}" for n, k in c.most_common())}\n')
        print(f'CLK recheck: {len(flaky)} of {len(tally)} moves gave more than one result (majority kept); wrote {tfile}')
    write_table(a.out, meta, e.rows(), [
        f'{CHIPS[seat.chip]["name"]} single-input-change move table, galre.py explore, {time.strftime("%Y-%m-%d %H:%M")}',
        f'{mode} mode; ' + (f'CLK pin {e.clk}, /OE pin {e.oe} held 0; ' if e.clk else '') +
        f'state = pins {e.spins} (registers {sorted(regs, reverse=True)}); inputs = pins {e.desc}; held {held or "none"}',
        f'power-up states {pu} (inputs all 0); {len(nodes)} nodes; {len(states)} distinct pin states; '
        f'replay mismatches {bad}/{done}; issues {dict(e.issues) or "none"}'])
    print(f'done in {time.time() - t0:.0f} s, {d.cmds} commands: {len(nodes)} nodes, {len(states)} pin states, '
          f'{len(e.E)} moves, power-up {pu}, replay {bad}/{done} mismatches')
    for k, v in e.issues.items(): print(f'  ISSUE {k}: {v}x, e.g. {e.examples[k]}')
    for x in ex: print('  replay mismatch', x)
    print('wrote', a.out)
    return 0 if not e.issues and not bad and len(pu) == 1 else 2

def cmd_replay(a):
    from galre.explore import Explorer
    meta, rows = read_table(a.table)
    a.chip = meta['chip']; a.seat = a.seat or meta.get('placement') or 'direct'
    d, seat = open_bench(a)
    e = Explorer(d, seat, meta['state'], meta['regs'], meta['inputs'], meta['mode'], meta['allow_z'], meta['held'])
    e.load(rows)
    try:
        cur = e.power_up()
        pu_ok = cur[0] in meta['power_up'] if meta['power_up'] else True
        print(f'power-up state {cur[0]} (original {meta["power_up"]}): {"same" if pu_ok else "DIFFERENT"}', flush=True)
        bad, done, ex = e.verify(cur, a.count, seed=a.seed)
    finally:
        d.write(0); d.power(False)
    total = len(rows) if a.count <= 0 else min(a.count, len(rows))
    print(f'replayed {done} of {total} moves: {bad} mismatches; issues {dict(e.issues) or "none"}')
    for x in ex: print('  mismatch (state, inputs, move, expected, got):', x)
    ok = pu_ok and not bad and done == total and not e.issues
    print('PASS' if ok else 'FAIL'); return 0 if ok else 1

def cmd_fit(a):
    from galre.fit import run_fit
    ov = dict(mode=a.mode, chip=a.chip, regs=pinlist(a.regs) if a.regs is not None else None)
    prefix = a.out or os.path.splitext(a.table)[0]
    ok, out = run_fit(a.table, prefix, ov)
    print(out); print('wrote', prefix + '_fit.txt'); return 0 if ok else 1

def cmd_pld(a):
    from galre.pld import make_pld, run_galasm
    out = a.out or os.path.splitext(a.fit)[0].removesuffix('_fit') + '.pld'
    print(make_pld(a.fit, a.name, out, a.names, a.desc)); print('wrote', out)
    if a.galasm:
        jed, msg = run_galasm(out); print(msg.strip()); print('wrote', jed)
    return 0

def cmd_jedcheck(a):
    from galre.jedcheck import jedcheck
    return 0 if jedcheck(a.jed, a.table) else 1

def cmd_compare(a):
    ma, ra = read_table(a.a); mb, rb = read_table(b := a.b)
    if (ma['state'], ma['inputs']) != (mb['state'], mb['inputs']):
        print(f'different pin roles: {a.a} state {ma["state"]} inputs {ma["inputs"]}; {b} state {mb["state"]} '
              f'inputs {mb["inputs"]}'); return 1
    A = {(s, x, m): n for s, x, m, n in ra}; B = {(s, x, m): n for s, x, m, n in rb}
    na = {(s, x) for s, x, m in A}; nb = {(s, x) for s, x, m in B}
    diff = [(k, A[k], B[k]) for k in A.keys() & B.keys() if A[k] != B[k]]
    print(f'power-up: {ma["power_up"]} vs {mb["power_up"]}')
    print(f'nodes: {len(na)} vs {len(nb)}; only in A {len(na - nb)}, only in B {len(nb - na)}')
    print(f'moves: {len(A)} vs {len(B)}; common {len(A.keys() & B.keys())}, different results {len(diff)}')
    for k, x, y in diff[:10]: print('  ', k, x, y)
    for n in sorted(na - nb)[:5]: print('  only in A:', n)
    for n in sorted(nb - na)[:5]: print('  only in B:', n)
    ok = not diff and na == nb and sorted(ma['power_up']) == sorted(mb['power_up'])
    print('SAME BEHAVIOUR' if ok else 'DIFFERENT'); return 0 if ok else 1

def cmd_selftest(a):
    from galre.selftest import selftest
    return 0 if selftest(a.workdir, a.only) else 1

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('check', help='dupico bring-up, empty socket'); bench_args(p)
    p.add_argument('--yes', action='store_true', help='do not wait for Enter'); p.set_defaults(fn=cmd_check)
    p = sub.add_parser('probe', help='first probe of an unknown chip'); bench_args(p)
    p.add_argument('-o', '--out', default='probe.json'); p.set_defaults(fn=cmd_probe)
    for name, fn in (('dump', cmd_dump),):
        p = sub.add_parser(name, help='first-look truth table'); bench_args(p)
        p.add_argument('--probe'); p.add_argument('--inputs'); p.add_argument('--state')
        p.add_argument('-o', '--out', default='dump.tsv'); p.set_defaults(fn=fn)
    p = sub.add_parser('ordercheck', help='re-read a dump in other orders'); bench_args(p)
    p.add_argument('dump'); p.set_defaults(fn=cmd_ordercheck)
    p = sub.add_parser('explore', help='single-input-change exploration'); bench_args(p)
    p.add_argument('--probe', help='probe.json: default chip, seat, mode and pin roles')
    p.add_argument('--mode', choices=('complex', 'registered'))
    p.add_argument('--state'); p.add_argument('--regs'); p.add_argument('--inputs'); p.add_argument('--allow-z')
    p.add_argument('--hold', help='pins held at a fixed level and not explored, e.g. 8=0,9=0 (board NC / tied pins)')
    p.add_argument('--verify', type=int, default=2000, help='random moves replayed at the end (0 = all)')
    p.add_argument('--recheck-clk', type=int, default=0, metavar='N', help='re-clock CLK moves N times, keep majority')
    p.add_argument('--recheck-all', action='store_true', help='with --recheck-clk: every CLK move (recommended)')
    p.add_argument('--load', metavar='TABLE', help='start from an existing table (to recheck or verify it)')
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('-o', '--out', required=True); p.set_defaults(fn=cmd_explore)
    p = sub.add_parser('replay', help='replay a move table on the chip in the socket'); bench_args(p)
    p.add_argument('table'); p.add_argument('--count', type=int, default=0, help='moves to replay (0 = all)')
    p.add_argument('--seed', type=int, default=2); p.set_defaults(fn=cmd_replay)
    p = sub.add_parser('fit', help='fit equations to a move table'); p.add_argument('table')
    p.add_argument('--mode', choices=('complex', 'registered')); p.add_argument('--chip', choices=sorted(CHIPS))
    p.add_argument('--regs'); p.add_argument('-o', '--out', help='output prefix (default: the table name)')
    p.set_defaults(fn=cmd_fit)
    p = sub.add_parser('pld', help='GALasm source from a fit'); p.add_argument('fit')
    p.add_argument('--name', default='RECOVER'); p.add_argument('--names', help='CSV pin,name')
    p.add_argument('--desc', default=''); p.add_argument('-o', '--out')
    p.add_argument('--galasm', action='store_true', help='also run galasm -> .jed'); p.set_defaults(fn=cmd_pld)
    p = sub.add_parser('jedcheck', help='replay a table on a JED'); p.add_argument('jed'); p.add_argument('table')
    p.set_defaults(fn=cmd_jedcheck)
    p = sub.add_parser('compare', help='compare two move tables'); p.add_argument('a'); p.add_argument('b')
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser('selftest', help='pipeline test on the bundled examples (simulated board)')
    p.add_argument('--workdir', default=None); p.add_argument('--only'); p.set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    sys.exit(a.fn(a))

if __name__ == '__main__':
    main()
