"""Bench procedures: board bring-up check, first probe of an unknown chip, first-look dump, order check.
All read-only on the chip: it is only driven through the dupico's 10K resistors (and clocked)."""
import json, random, time
from .dupico import MASK, BIT2PIN, zif_pins, power_cycle

def sample(d, seat, levels, pins):
    """Drive `levels` ({pin: 0/1}, others 0), read `pins` with the pull low then high.
    -> ({pin: '0'|'1'|'Z'|'?'}, low read-back word)."""
    v = seat.drive(levels)
    lo = d.write(v); hi = {}
    for p in pins:                       # pull one pin at a time: an output-cell pin used as an input must not
        if not levels.get(p):            # disturb the others while they are being read
            hi[p] = d.write(v | seat.bit(p)); d.write(v)
    out = {}
    for p in pins:
        l, h = bool(lo & seat.bit(p)), bool(hi.get(p, lo) & seat.bit(p))
        if levels.get(p): out[p] = '1' if l else '0'          # pin we drive high: chip pulling it low shows as 0
        else: out[p] = '1' if l and h else '0' if not (l or h) else 'Z' if h else '?'
    return out, lo

def probe(d, seat, n_vectors=64, seed=1, log=print):
    c = seat.c; ded = c['dedicated']; olmc = c['olmc']
    rng = random.Random(seed); t0 = time.time(); c0 = d.cmds
    d.write(0); power_cycle(d)
    rep = dict(chip=seat.chip, placement=seat.placement, setup=seat.setup_text())
    # 1. placement / ground: dedicated inputs must read back what is driven
    _, lo = sample(d, seat, {}, olmc)
    if all(lo & seat.bit(p) for p in seat.signal_pins):
        rep['verdict'] = 'NO GROUND: every chip line reads 1 with everything driven low'
        log('1. FAIL: every chip line reads 1 with all lines driven low -> the chip has no ground. ' +
            ('Fit the wire from the chip GND pin to ZIF 21.' if seat.p['gnd_jumper'] else 'Check the pod seating.'))
        return rep
    wrong = []
    pats = [('all 0', {}), ('all 1', {p: 1 for p in ded})] + [(f'only {p}', {p: 1}) for p in ded]
    for name, lv in pats:
        _, lo = sample(d, seat, lv, olmc)
        bad = [p for p in ded if bool(lo & seat.bit(p)) != bool(lv.get(p))]
        if bad: wrong.append((name, bad))
    if wrong:
        rep['verdict'] = f'PLACEMENT: dedicated inputs do not read back: {wrong}'
        log(f'1. FAIL: dedicated input pins do not follow the drive: {wrong[:6]}\n   -> wrong chip type / seating / '
            f'pin 1 orientation, a missing GND wire, or a dead chip. Nothing else is meaningful until this passes.')
        return rep
    log(f'1. placement and ground OK ({seat.setup_text()})')
    # 2. output-cell pin classes
    samples = []
    for i in range(n_vectors):
        lv = {} if i == 0 else {p: 1 for p in ded} if i == 1 else {p: rng.randint(0, 1) for p in ded}
        o, _ = sample(d, seat, lv, olmc); samples.append((lv, o))
    # 3. /OE pin test (registered mode: registered outputs go Z when the /OE pin is high, whatever else)
    oe, clk = c['oe'], c['clk']
    zcount = {p: 0 for p in olmc}; trials = 0
    for _ in range(24):
        lv = {p: rng.randint(0, 1) for p in ded}; lv[clk] = 0
        o0, _ = sample(d, seat, {**lv, oe: 0}, olmc); o1, _ = sample(d, seat, {**lv, oe: 1}, olmc)
        trials += 1
        for p in olmc: zcount[p] += o0[p] in '01' and o1[p] == 'Z'
    regcand = [p for p in olmc if zcount[p] == trials]
    # 4. clock test: changes on the rising and falling edge of pin 1 with everything else held (/OE low)
    rise = {p: 0 for p in olmc}; fall = {p: 0 for p in olmc}; back = 0
    for _ in range(16):
        lv = {p: rng.randint(0, 1) for p in ded}; lv[oe] = 0; lv[clk] = 0
        for _ in range(3):
            a, _ = sample(d, seat, lv, olmc); b, _ = sample(d, seat, {**lv, clk: 1}, olmc); e, _ = sample(d, seat, lv, olmc)
            for p in olmc: rise[p] += a[p] != b[p]; fall[p] += b[p] != e[p]
            back += a != e                                     # pin 1 back to 0 but outputs not as before
    # 4b. influence: flip each input pin alone from random settled vectors (single changes: latch-safe)
    effect = {p: 0 for p in ded + olmc}
    for _ in range(32):
        lv = {p: rng.randint(0, 1) for p in ded}; lv[clk] = 0; lv[oe] = 0
        base, _ = sample(d, seat, lv, olmc)
        for p in ded + olmc:
            if p in (clk, oe) or (p in olmc and base[p] != 'Z'): continue
            q = {**lv, p: 1 - lv.get(p, 0)}
            o, _ = sample(d, seat, q, [x for x in olmc if x != p])
            effect[p] += any(o[x] != base[x] for x in o)
            sample(d, seat, lv, olmc)
    # 5. history: the same vector after different others
    hist = 0
    for lv, ref in samples[2:10]:
        for jv, _ in rng.sample(samples, 6):
            sample(d, seat, jv, olmc); again, _ = sample(d, seat, lv, olmc)
            hist += again != ref
    registered = bool(regcand) and not any(fall[p] for p in regcand)
    mode = 'registered' if registered else 'complex'
    use = [(lv, o) for lv, o in samples if not (registered and lv.get(oe))]
    seen = {p: {o[p] for _, o in use} for p in olmc}
    state = [p for p in olmc if seen[p] & {'0', '1'}]
    allow_z = [p for p in state if 'Z' in seen[p] and p not in regcand]
    inputs = [p for p in ded if not (registered and p in (clk, oe))] + [p for p in olmc if p not in state]
    odd = [p for p in olmc if '?' in seen[p]]
    no_effect = [p for p in inputs if effect.get(p, 1) == 0 and p not in (clk, oe)]
    stateful = registered or hist > 0 or back > 0
    dt = (time.time() - t0) / max(1, d.cmds - c0)
    n_in = len(inputs); moves_min = (1 << n_in) * (n_in + (1 if registered else 0))
    rep.update(mode=mode, state=sorted(state), regs=sorted(regcand) if registered else [], allow_z=sorted(allow_z),
               inputs=sorted(inputs), odd_pins=odd, no_effect_seen=sorted(no_effect), stateful_evidence=dict(history_changes=hist, pin1_pulse_changed_state=back,
               clock_rise={p: v for p, v in rise.items() if v}, clock_fall={p: v for p, v in fall.items() if v}),
               per_pin={p: ''.join(sorted(seen[p])) for p in olmc}, oe_test={p: f'{zcount[p]}/{trials}' for p in olmc},
               seconds_per_command=dt, min_moves=moves_min,
               min_hours=moves_min * 3.5 * dt / 3600)
    log('2. output-cell pins (0/1 = driven, Z = not driven, ? = fights the pull): ' +
        ', '.join(f'{p}:{rep["per_pin"][p]}' for p in olmc))
    log(f'3. /OE pin {oe} high tri-states pins {regcand or "none"} in every trial '
        f'(per pin: {", ".join(f"{p}:{v}" for p, v in rep["oe_test"].items())})')
    log(f'4. pin {clk} rising edge changed {rep["stateful_evidence"]["clock_rise"] or "nothing"}; '
        f'falling edge changed {rep["stateful_evidence"]["clock_fall"] or "nothing"}')
    log(f'4b. input pins whose single flips never changed an output (32 random vectors): {sorted(no_effect) or "none"}')
    log(f'5. history test: {hist} re-reads of a vector differed; {back} pin-{clk} pulses left the outputs changed')
    log(f'-> mode: {mode.upper()}' + (f' (pin {clk} = CLK, pin {oe} = /OE, registers {rep["regs"]})' if registered else
        ' (no register signature; simple-mode chips also look like this)'))
    log(f'-> {"STATEFUL (latches/registers): a plain dump is NOT the logic" if stateful else "no state seen yet (run the order check before trusting a dump)"}')
    if odd: log(f'-> pins {odd} read "?" (pulled high reads 0, low reads 1): oscillation or a latch flipping when pulled')
    log(f'-> suggested exploration: state {rep["state"]}, inputs {rep["inputs"]}, regs {rep["regs"]}, '
        f'allow-z {rep["allow_z"]}')
    if no_effect:
        log(f'-> pins {sorted(no_effect)}: no effect seen in random single flips. Weak evidence (a decoder input only '
            f'matters under one combination of the others): the dump checks every combination.')
    log(f'-> size: {n_in} inputs -> at least {moves_min} moves per output state, ~{rep["min_hours"]:.2g} h per state '
        f'at {dt * 1000:.2f} ms per command')
    rep['verdict'] = 'OK'
    return rep

def dump(d, seat, inputs, outs, fixed=None, log=print):
    """Truth-table snapshot, input vectors in ascending order (like dpdumper). -> {x: out string (highest pin first)}"""
    fixed = fixed or {}
    d.write(0); power_cycle(d)
    res = {}
    desc = sorted(outs, reverse=True)
    for x in range(1 << len(inputs)):
        lv = {**fixed, **{p: x >> i & 1 for i, p in enumerate(inputs)}}
        o, _ = sample(d, seat, lv, outs)
        res[x] = ''.join(o[p] for p in desc)
    return res

def influence(res, inputs):
    """Pins whose flip never changes the outputs anywhere in a dump (every pair of entries differing in that pin)."""
    none = []
    for i, p in enumerate(inputs):
        if all(res[x] == res[x ^ (1 << i)] for x in res if not x >> i & 1): none.append(p)
    return none

def ordercheck(d, seat, inputs, outs, ref, fixed=None, log=print):
    """Re-read every vector in Gray-code and in random order, twice each; count entries differing from `ref`."""
    fixed = fixed or {}; n = 1 << len(inputs); desc = sorted(outs, reverse=True)
    d.write(0); power_cycle(d)
    orders = {'gray': [i ^ (i >> 1) for i in range(n)]}
    r = list(range(n)); random.Random(3).shuffle(r); orders['random'] = r
    total = 0
    for name, order in orders.items():
        for rep in range(2):
            diff = []
            for x in order:
                lv = {**fixed, **{p: x >> i & 1 for i, p in enumerate(inputs)}}
                o, _ = sample(d, seat, lv, outs); got = ''.join(o[p] for p in desc)
                if got != ref[x]: diff.append((x, ref[x], got))
            total += len(diff)
            log(f'{name} order, pass {rep + 1}: {len(diff)} of {n} entries differ from the dump'
                + (f', e.g. {[(f"{x:X}", e, g) for x, e, g in diff[:4]]}' if diff else ''))
    return total

def bringup(d, interactive=True, log=print):
    """dupico self-check with an EMPTY socket (no chip, no pod)."""
    def check(name, ok, detail=''):
        log(f'  [{"PASS" if ok else "FAIL"}] {name}{(" - " + detail) if detail else ""}'); return ok
    allok = True
    log(d.describe())
    if interactive: input('Make sure the ZIF socket is EMPTY (no chip, no pod), then press Enter... ')
    allok &= check('firmware self-test', d.selftest())
    d.power(False); d.write(MASK)
    t0 = time.time(); v = d.read()
    while v and time.time() - t0 < 30: d.sleep(0.25); v = d.read()
    allok &= check('socket power off: all lines low', v == 0,
                   f'took {time.time() - t0:.1f} s (the ~10 uF on the switched supply decays slowly: normal)' if v == 0
                   else f'still high after 30 s: ZIF pins {zif_pins(v)}')
    d.power(True); d.sleep(0.3)
    for name, pat in (('all low', 0), ('all high', MASK)):
        v = d.write(pat); allok &= check(f'pattern {name}', v == pat, f'wrong ZIF pins {zif_pins(v ^ pat)}' if v != pat else '')
    bad = []
    for b in range(40):
        for pat in (1 << b, MASK & ~(1 << b)):
            v = d.write(pat)
            if v != pat: bad.append((BIT2PIN[b], zif_pins(v ^ pat)))
    allok &= check('walking 1 / walking 0 on all 40 lines', not bad, f'{bad[:6]}' if bad else '')
    d.write(0x5555555555); o = d.osc(200)
    allok &= check('static lines stay static (200 reads)', o == 0, f'toggling ZIF pins {zif_pins(o)}' if o else '')
    d.write(0); d.power(False)
    log('RESULT: ' + ('ALL PASSED' if allok else 'FAILURES - fix the board before testing chips'))
    return allok
