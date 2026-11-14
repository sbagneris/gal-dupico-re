"""Pipeline self-test on the bundled example JEDs with the simulated board (needs espresso and galasm):
probe -> explore -> fit -> pld + galasm -> jedcheck -> replay the original's table on the "programmed copy" (the
new JED in the simulated socket), plus the two bench faults (missing ground wire, double clocking + recheck)."""
import json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GALRE = os.path.join(HERE, 'galre.py')
EX = os.path.join(os.path.dirname(HERE), 'assets', 'examples')
EXAMPLES = {   # name: chip, expected mode, pins unconnected on the (imaginary) board
    'latch16': ('16v8', 'complex', [8, 9, 11, 12, 13, 14, 15]),
    'fsm16': ('16v8', 'registered', [6, 7, 8, 9, 12, 13, 16, 19]),
    'dec20': ('20v8', 'complex', [7, 8, 9, 10, 11, 13, 14, 15, 21, 23]),
    'fsm20': ('20v8', 'registered', [5, 6, 7, 8, 9, 10, 11, 14, 15, 20, 21, 22, 23]),
    'tristate16': ('16v8', 'complex', [5, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17]),   # floating feedback must not be used
}

def run(args, log):
    r = subprocess.run([sys.executable, GALRE] + args, capture_output=True, text=True)
    log.write(f'$ galre.py {" ".join(args)}\n{r.stdout}{r.stderr}\n')
    return r.returncode, r.stdout + r.stderr

def selftest(workdir=None, only=None):
    wd = workdir or tempfile.mkdtemp(prefix='galre-selftest-')
    os.makedirs(wd, exist_ok=True)
    log = open(os.path.join(wd, 'selftest.log'), 'w')
    results = []
    def step(name, ok, detail=''):
        results.append(ok); print(f'  [{"PASS" if ok else "FAIL"}] {name}{" - " + detail if detail else ""}', flush=True)
        return ok
    for name, (chip, mode, nc) in EXAMPLES.items():
        if only and name not in only.split(','): continue
        t0 = time.time(); print(f'{name} ({chip}, {mode})', flush=True)
        jed = os.path.join(EX, name + '.jed'); p = lambda s: os.path.join(wd, f'{name}{s}')
        rc, out = run(['probe', '--chip', chip, '--sim', jed, '-o', p('_probe.json')], log)
        pr = json.load(open(p('_probe.json')))
        if not step('probe', rc == 0 and pr.get('mode') == mode, f'mode {pr.get("mode")}, state {pr.get("state")}, '
                    f'regs {pr.get("regs")}, inputs {pr.get("inputs")}'): continue
        hold = [x for x in nc if x in pr['inputs']]
        rc, out = run(['explore', '--sim', jed, '--probe', p('_probe.json'), '--hold', ','.join(f'{x}=0' for x in hold),
                       '-o', p('_moves.tsv')], log)
        if not step('explore', rc == 0, out.strip().splitlines()[-2] if rc == 0 else out[-300:]): continue
        rc, out = run(['fit', p('_moves.tsv'), '-o', p('')], log)
        if not step('fit', rc == 0, next((l for l in out.splitlines() if l.startswith('# validation')), '')): continue
        rc, out = run(['pld', p('_fit.txt'), '--name', name, '-o', p('_copy.pld'), '--galasm'], log)
        if not step('pld + galasm', rc == 0 and os.path.exists(p('_copy.jed'))): continue
        rc, out = run(['jedcheck', p('_copy.jed'), p('_moves.tsv')], log)
        step('jedcheck', rc == 0, out.strip().splitlines()[-1])
        rc, out = run(['replay', p('_moves.tsv'), '--sim', p('_copy.jed')], log)
        step('replay the original table on the copy', rc == 0, next((l for l in out.splitlines() if l.startswith('replayed')), ''))
        print(f'  ({time.time() - t0:.0f} s)', flush=True)
    if not only or 'faults' in only.split(','):
        print('bench faults', flush=True)
        jed = os.path.join(EX, 'latch16.jed')
        rc, out = run(['probe', '--sim', jed, '--sim-fault', 'noground', '-o', os.path.join(wd, 'noground.json')], log)
        step('missing ground wire detected', rc != 0 and 'no ground' in out)
        jed = os.path.join(EX, 'fsm16.jed'); clean = os.path.join(wd, 'fsm16_moves.tsv')
        if not os.path.exists(clean):
            run(['probe', '--sim', jed, '-o', os.path.join(wd, 'fsm16_probe.json')], log)
            run(['explore', '--sim', jed, '--probe', os.path.join(wd, 'fsm16_probe.json'), '--hold', '6=0,7=0,8=0,9=0',
                 '-o', clean], log)
        dc = os.path.join(wd, 'fsm16_dc.tsv')
        rc, out = run(['explore', '--sim', jed, '--sim-fault', 'doubleclock', '--probe', os.path.join(wd, 'fsm16_probe.json'),
                       '--hold', '6=0,7=0,8=0,9=0', '--recheck-clk', '5', '--recheck-all', '-o', dc], log)
        step('double clocking reported by the explorer', 'not repeatable' in out or 'more than one result' in out)
        rc, out = run(['compare', clean, dc], log)
        step('recheck (5x, majority) recovers the clean table', rc == 0, out.strip().splitlines()[-1])
    ok = all(results)
    print(f'SELFTEST {"PASSED" if ok else "FAILED"} ({sum(results)}/{len(results)} steps); log and files in {wd}')
    return ok
