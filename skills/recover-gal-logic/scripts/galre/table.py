"""Move tables: the record of a chip's behaviour, one input change (or one clock pulse) at a time.

Format (TSV): '#' comment lines, one '# meta: {json}' line, a header line, then rows
    state   inputs   move   next
state = levels of the state pins (0/1/Z), highest pin first; inputs = levels of the input pins, highest pin first;
move = 'pinN' (flip input pin N) or 'CLK' (rising + falling edge with the inputs held); next = settled state after.
Tables written before the meta line existed (AdSCSI v0) are read from their '# ... state = pins [...]' header.
"""
import ast, json, re

def pinlist(s):
    out = []
    for part in filter(None, str(s).replace(' ', '').split(',')):
        a, _, b = part.partition('-')
        out += range(int(a), int(b or a) + 1)
    return out

def read_table(path, overrides=None):
    head, rows, meta = '', [], None
    for l in open(path):
        if l.startswith('# meta:'): meta = json.loads(l[7:]); continue
        if l.startswith('#'): head += l; continue
        if l.startswith('state\t'): continue
        if l.strip():
            s, x, m, n = l.rstrip('\n').split('\t')
            rows.append((s, x, m, n))
    if meta is None:                                         # v0 table
        meta = dict(chip='16v8',
                    state=ast.literal_eval(re.search(r'state = pins (\[[^\]]*\])', head).group(1)),
                    inputs=ast.literal_eval(re.search(r'inputs = pins (\[[^\]]*\])', head).group(1)),
                    regs=ast.literal_eval(mr.group(1)) if (mr := re.search(r'registers (\[[^\]]*\])', head)) else [],
                    mode='complex' if 'no CLK' in head else 'registered')
        pu = re.search(r"power-up states \{([^}]*)\}", head)
        meta['power_up'] = re.findall(r"'([01Z?]+)'", pu.group(1)) if pu else []
        # v0 tables have no allow_z list: a state pin the original's own record shows floating (Z) may float
        order = sorted(meta['state'], reverse=True)
        meta['allow_z'] = sorted({order[i] for s, _, _, n in rows for st in (s, n) for i, c in enumerate(st) if c == 'Z'},
                                 reverse=True)
    for k, v in (overrides or {}).items():
        if v is not None: meta[k] = v
    meta['state'] = sorted(meta['state'], reverse=True)
    meta['inputs'] = sorted(meta['inputs'], reverse=True)
    meta.setdefault('regs', []); meta.setdefault('allow_z', []); meta.setdefault('power_up', [])
    meta['held'] = {int(k): int(v) for k, v in meta.get('held', {}).items()}
    return meta, rows

def write_table(path, meta, rows, comments=()):
    with open(path, 'w') as f:
        for c in comments: f.write(f'# {c}\n')
        f.write('# meta: ' + json.dumps(meta, sort_keys=True) + '\n')
        f.write('# each row: settled node (state, inputs), move (flip one input pin, or CLK = rising+falling edge), '
                'settled state after\n')
        f.write('state\tinputs\tmove\tnext\n')
        for r in rows: f.write('\t'.join(r) + '\n')
