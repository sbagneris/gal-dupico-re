"""Fit result -> GALasm source (GAL16V8 / GAL20V8), and GALasm invocation.

Combinational outputs become tristate outputs (Pn.T + Pn.E), which puts GALasm in complex mode (mode 2), or
registered mode (mode 3) when there are registers (Pn.R; pin 1 = CLK, /OE pin = OE).
Every other output-cell pin (input, never-enabled output, unused) is declared Pn.T = GND with Pn.E = GND: never
driven. GALasm would otherwise leave it NOTUSED with AC1 = 0, which in registered mode is a REGISTERED OUTPUT driving
the pin whenever /OE is low, and in complex mode an undocumented configuration.
"""
import csv, json, os, re, shutil, subprocess, sys
from .chips import CHIPS

def parse_fit(path):
    eqs, cur, meta = {}, None, None
    for l in open(path):
        if l.startswith('# meta:'): meta = json.loads(l[7:]); continue
        if l.startswith('#') or not l.strip(): cur = None; continue
        m = re.match(r'^(/?)P(\d+)(\.[REJT])?\s*=\s*(.*)$', l.strip())
        if m:
            neg, pin, suf, rhs = m.group(1) == '/', int(m.group(2)), (m.group(3) or '.F'), m.group(4)
            cur = (pin, suf); eqs[cur] = [neg, [rhs]]
        elif l.strip().startswith('+') and cur:
            eqs[cur][1].append(l.strip()[1:].strip())
    if meta is None: sys.exit(f'{path}: no "# meta:" line (fit it with galre.py fit)')
    return meta, eqs

def make_pld(fit_path, name, out, names_csv=None, desc=''):
    meta, eqs = parse_fit(fit_path)
    c = CHIPS[meta['chip']]; mode = meta['mode']; npins = c['npins']
    rename = {}
    if names_csv:
        rename = {int(r['pin']): r['name'] for r in csv.DictReader(open(names_csv))}
        bad = [n for n in rename.values() if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,7}', n)]
        if bad: sys.exit(f'GALasm pin names must be <= 8 characters, letters/digits/_: {bad}')
    used = set()
    for (pin, suf), (neg, terms) in eqs.items():
        for t in terms: used |= {int(x) for x in re.findall(r'P(\d+)', t)}
    outs = {}
    for (pin, suf), v in eqs.items(): outs.setdefault(pin, {})[suf] = v
    names = {c['gnd']: 'GND', c['vcc']: 'VCC'}
    if mode == 'registered': names[c['clk']], names[c['oe']] = 'CLK', 'OE'
    lines = []
    for pin in sorted(outs):
        o = outs[pin]
        if '.E' in o and o['.E'][1] == ['GND'] and '.F' not in o and '.R' not in o: continue
        names[pin] = f'P{pin}'
        if '.R' in o:
            neg, terms = o['.R']
            lines.append(f'{"/" if neg else ""}P{pin}.R = ' + '\n    + '.join(terms))
        else:
            neg, terms = o['.F']
            lines.append(f'{"/" if neg else ""}P{pin}.T = ' + '\n    + '.join(terms))
            lines.append(f'P{pin}.E = ' + ' + '.join(o.get('.E', [False, ['VCC']])[1]))
        lines.append('')
    for p in c['olmc']:
        if p not in outs or ('.F' not in outs[p] and '.R' not in outs[p]):
            names[p] = f'P{p}'; lines += [f'P{p}.T = GND', f'P{p}.E = GND', '']
    for p in used: names.setdefault(p, f'P{p}')
    for p in range(1, npins + 1): names.setdefault(p, 'NC')
    if rename:
        sub = lambda s: re.sub(r'(?<![A-Za-z0-9])P(\d+)(?![0-9])', lambda m: rename.get(int(m.group(1)), m.group(0)), s)
        lines = [sub(l) for l in lines]
        for p in range(1, npins + 1):
            if names[p] not in ('GND', 'VCC', 'NC') and p in rename: names[p] = rename[p]
    half = npins // 2
    src = [c['name'], re.sub(r'[^A-Za-z0-9]', '', name)[:8] or 'RECOVER', '',
           ' '.join(f'{names[p]:4s}' for p in range(1, half + 1)).rstrip(),
           ' '.join(f'{names[p]:4s}' for p in range(half + 1, npins + 1)).rstrip(), ''] + lines + [
           'DESCRIPTION', '', desc or f'Equivalent logic recovered from a secured {c["name"]} ({mode} mode) by '
           f'single-input-change exploration and fitting; generated from {os.path.basename(fit_path)}.']
    open(out, 'w').write('\n'.join(src) + '\n')
    return '\n'.join(src) + '\n'

def run_galasm(pld):
    exe = shutil.which('galasm')
    if not exe: sys.exit('galasm not found on the PATH (GALasm 2.1, github.com/daveho/GALasm)')
    r = subprocess.run([exe, '-c', '-f', '-p', os.path.basename(pld)], cwd=os.path.dirname(os.path.abspath(pld)),
                       capture_output=True, text=True)
    jed = os.path.splitext(pld)[0] + '.jed'
    if r.returncode != 0 or not os.path.exists(jed):
        sys.exit(f'galasm failed:\n{r.stdout}{r.stderr}')
    return jed, r.stdout + r.stderr
