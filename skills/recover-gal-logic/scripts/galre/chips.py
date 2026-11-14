"""GAL16V8 / GAL20V8 architecture (JEDEC fuse layout and array input columns from GALasm 2.1) and the ways of
seating a chip in the DuPAL V3 "dupico" ZIF42 socket."""

# GALasm PinToFuse tables (pin -> first fuse column of its true/complement pair), -1 entries dropped.
# mode 'simple' = SYN 1 AC0 0, 'complex' = SYN 1 AC0 1, 'registered' = SYN 0 AC0 1.
def _cols(table):
    return {pin + 1: c for pin, c in enumerate(table) if c >= 0}

CHIPS = {
    '16v8': dict(
        name='GAL16V8', npins=20, gnd=10, vcc=20, clk=1, oe=11, fuses=2194, cols=32, base=2048,
        olmc=[19, 18, 17, 16, 15, 14, 13, 12],            # OLMC k (k = 0 first): rows 8k..8k+7, XOR/AC1 bit k
        dedicated=[1, 2, 3, 4, 5, 6, 7, 8, 9, 11],
        col={'simple': _cols([2, 0, 4, 8, 12, 16, 20, 24, 28, -1, 30, 26, 22, 18, -1, -1, 14, 10, 6, -1]),
             'complex': _cols([2, 0, 4, 8, 12, 16, 20, 24, 28, -1, 30, -1, 26, 22, 18, 14, 10, 6, -1, -1]),
             'registered': _cols([-1, 0, 4, 8, 12, 16, 20, 24, 28, -1, -1, 30, 26, 22, 18, 14, 10, 6, 2, -1])}),
    '20v8': dict(
        name='GAL20V8', npins=24, gnd=12, vcc=24, clk=1, oe=13, fuses=2706, cols=40, base=2560,
        olmc=[22, 21, 20, 19, 18, 17, 16, 15],
        dedicated=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 23],
        col={'simple': _cols([2, 0, 4, 8, 12, 16, 20, 24, 28, 32, 36, -1, 38, 34, 30, 26, 22, -1, -1, 18, 14, 10, 6, -1]),
             'complex': _cols([2, 0, 4, 8, 12, 16, 20, 24, 28, 32, 36, -1, 38, 34, -1, 30, 26, 22, 18, 14, 10, -1, 6, -1]),
             'registered': _cols([-1, 0, 4, 8, 12, 16, 20, 24, 28, 32, 36, -1, -1, 38, 34, 30, 26, 22, 18, 14, 10, 6, 2, -1])}),
}
# offsets after the AND array: XOR (8), SIG (64), AC1 (8), PTD (64), SYN, AC0
OFF_XOR, OFF_AC1, OFF_PTD, OFF_SYN, OFF_AC0 = 0, 72, 80, 144, 145

def chip_by_fuses(n):
    for k, c in CHIPS.items():
        if c['fuses'] == n: return k
    raise ValueError(f'{n} fuses: not a GAL16V8 (2194) or GAL20V8 (2706) JEDEC file')

def array_inputs(chip, mode):
    """Pins feeding the AND array in this mode, sorted."""
    return sorted(CHIPS[chip]['col'][mode])

def feedback_pins(chip, mode):
    c = CHIPS[chip]
    return [p for p in c['olmc'] if p in c['col'][mode]]

# ---- seating in the dupico ZIF42 ------------------------------------------------------------------------------
# dupico lines: ZIF42 pins 1-20 = bits 0-19, 22-41 = bits 20-39; ZIF 21 = GND, ZIF 42 = switched VCC.
def zif_bit(z):
    if z in (21, 42): return None
    return z - 1 if z <= 20 else z - 2

PLACEMENTS = {
    # direct: chip top-aligned in the ZIF42 (pin 1 at ZIF 1), VCC lands on ZIF 42; its GND pin lands on a signal
    # line and MUST be jumpered to ZIF 21 with a wire; fit 100 nF across the chip's VCC and GND pins.
    ('16v8', 'direct'): dict(zif={**{p: p for p in range(1, 11)}, **{p: p + 22 for p in range(11, 21)}},
                             hi_lines=[], gnd_jumper=(10, 21), cap='pins 20-10'),
    ('20v8', 'direct'): dict(zif={**{p: p for p in range(1, 13)}, **{p: p + 18 for p in range(13, 25)}},
                             hi_lines=[], gnd_jumper=(12, 21), cap='pins 24-12'),
    # PAL POD in the top of the ZIF42 (it has its own decoupling). 20-pin parts sit at the bottom of the pod's ZIF24
    # and get VCC through the pod's transistor, switched on by ZIF42 line 22 held high.
    ('16v8', 'pod'): dict(zif={**{p: p + 2 for p in range(1, 10)}, 10: 21, **{p: p + 20 for p in range(11, 20)}, 20: 42},
                          hi_lines=[22], gnd_jumper=None, cap=None),
    ('20v8', 'pod'): dict(zif={**{p: p for p in range(1, 12)}, 12: 21, **{p: p + 18 for p in range(13, 24)}, 24: 42},
                          hi_lines=[], gnd_jumper=None, cap=None),
}

class Seat:
    """Pin <-> dupico bit mapping for a chip seated a given way."""
    def __init__(self, chip, placement):
        if (chip, placement) not in PLACEMENTS: raise ValueError(f'unknown chip/placement {chip}/{placement}')
        self.chip, self.placement = chip, placement
        self.c = CHIPS[chip]; self.p = PLACEMENTS[(chip, placement)]
        self.signal_pins = [p for p in range(1, self.c['npins'] + 1) if p not in (self.c['gnd'], self.c['vcc'])]
        self._bit = {p: zif_bit(self.p['zif'][p]) for p in self.signal_pins}
        self.hi_mask = sum(1 << zif_bit(z) for z in self.p['hi_lines'])

    def bit(self, pin): return 1 << self._bit[pin]

    def drive(self, levels):
        """{pin: 0/1} -> 40-bit word (plus the placement's always-high lines)."""
        return sum(self.bit(p) for p, v in levels.items() if v) | self.hi_mask

    def level(self, word, pin): return 1 if word & self.bit(pin) else 0

    def setup_text(self):
        n = self.c['name']
        if self.placement == 'pod':
            if self.p['hi_lines']:
                return (f'{n} at the bottom of the PAL POD\'s ZIF24 (pod pins 1-2 and 23-24 empty), pod in the topmost '
                        f'position of the dupico ZIF42; the tools hold ZIF line 22 high to power the chip through the pod')
            return (f'{n} filling the PAL POD\'s ZIF24, pod in the topmost position of the dupico ZIF42 (the tools keep ZIF line '
                    f'22 low: through the pod it would put 5 V on chip pin 22)')
        g, z = self.p['gnd_jumper']
        return (f'{n} straight in the dupico ZIF42, top-aligned (chip pin 1 at ZIF 1, lever end); wire from ZIF {g} '
                f'(the chip\'s GND pin) to ZIF {z}; 100 nF capacitor across the chip\'s {self.p["cap"]}')
