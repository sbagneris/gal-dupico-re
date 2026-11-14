"""DuPAL V3 "dupico" access: the real board (binary protocol over USB serial, pyserial only) and a simulated board
holding a GAL defined by a JEDEC file, so every procedure can be rehearsed and tested without hardware.

Protocol: WRITE 0x00 + u64 LE drives the 40 ZIF lines through 10K resistors and answers with the read-back value;
READ 0x01; POWER 0x03 + on/off; MODEL 0x04; TEST 0x05; VERSION 0x06; OSC_DET 0x08 + n. Each frame ends with a
checksum byte that makes the frame sum to 0 mod 256.
"""
import os, random, struct, sys, time
from .chips import zif_bit

MASK = (1 << 40) - 1
PIN2BIT = {z: zif_bit(z) for z in range(1, 43) if zif_bit(z) is not None}
BIT2PIN = {b: z for z, b in PIN2BIT.items()}

def zif_pins(v):
    return [BIT2PIN[b] for b in range(40) if v >> b & 1]

class Dupico:
    fake = False
    def __init__(self, port):
        import serial
        try:
            self.s = serial.Serial(port, 115200, timeout=2)
        except serial.SerialException as e:
            sys.exit(f'cannot open {port}: {e}\n(board plugged in? user in the serial group (dialout/uucp)? '
                     f'another program (minicom, dpdumper) holding the port?)')
        for _ in range(3):
            line = self.s.readline(64).decode('ascii', 'replace').strip()
            if line == 'REMOTE_CONTROL_ENABLED': break
            self.s.dtr = False; self.s.reset_input_buffer(); time.sleep(0.5)
            self.s.dtr = True; time.sleep(0.5)
        else:
            sys.exit('the board did not announce REMOTE_CONTROL_ENABLED (close minicom / other tools first?)')
        self.s.reset_input_buffer()
        self.cmds = 0

    def cmd(self, payload, resp_len):
        chk = (-sum(payload)) & 0xFF
        self.s.write(bytes(payload) + bytes([chk]))
        r = self.s.read(1 + resp_len + 1)
        if len(r) != resp_len + 2 or r[0] != (payload[0] | 0x80) or sum(r) & 0xFF:
            raise IOError(f'bad response to command {payload[0]:#04x}: {r.hex()}')
        self.cmds += 1
        return r[1:-1]

    def model(self):    return self.cmd([0x04], 1)[0]
    def version(self):  return self.cmd([0x06], 10).decode('ascii', 'replace').rstrip('\x00').strip()
    def selftest(self): return self.cmd([0x05], 1)[0] == 1
    def power(self, on): return self.cmd([0x03, 1 if on else 0], 1)[0] == 1
    def write(self, v): return struct.unpack('<Q', self.cmd([0x00, *struct.pack('<Q', v & MASK)], 8))[0] & MASK
    def read(self):     return struct.unpack('<Q', self.cmd([0x01], 8))[0] & MASK
    def osc(self, n):   return struct.unpack('<Q', self.cmd([0x08, n & 0xFF], 8))[0] & MASK
    def sleep(self, t): time.sleep(t)
    def describe(self): return f'dupico model {self.model()}, firmware {self.version()}'

class FakeDupico:
    """Simulated dupico with a GAL seated as `seat`. Faults: 'noground' (GND jumper missing: every chip line reads 1),
    'doubleclock' (a rising CLK edge with >= 3 outputs switching clocks twice 2% of the time, as on a chip without
    its 100 nF capacitor)."""
    fake = True
    def __init__(self, chip, seat, fault=None, seed=0):
        self.chip, self.seat, self.fault = chip, seat, fault
        self.on = False; self.last = 0; self.cmds = 0
        self.rng = random.Random(seed)
        if chip.g.chip != seat.chip:
            sys.exit(f'--sim JED is a {chip.g.c["name"]} but --chip says {seat.c["name"]}')

    def model(self): return 3
    def version(self): return 'SIM'
    def selftest(self): return True
    def describe(self): return f'SIMULATED dupico, {self.chip.g.c["name"]} in {self.chip.g.mode} mode from the JED'
    def sleep(self, t): pass
    def osc(self, n): return 0
    def read(self): return self.last

    def power(self, on):
        self.cmds += 1
        if not on or not self.on: self.chip.reset()
        self.on = bool(on)
        return True

    def write(self, v):
        self.cmds += 1
        v &= MASK
        if not self.on: self.last = 0; return 0
        s = self.seat
        if s.hi_mask and (v & s.hi_mask) != s.hi_mask:      # pod not powering the chip
            self.last = v; return v
        if self.fault == 'noground':
            self.last = v | sum(s.bit(p) for p in s.signal_pins); return self.last
        ext = {p: s.level(v, p) for p in s.signal_pins}
        dbl = False
        if self.fault == 'doubleclock' and s.chip and self.chip.clk_pin:
            before = dict(self.chip.driven(ext))
            snap = (dict(self.chip.reg), dict(self.chip.out), self.chip.clk)
            self.chip.apply(ext)
            after = self.chip.driven(ext)
            switched = sum(before.get(p) != after.get(p) for p in after)
            if snap[2] == 0 and ext.get(self.chip.clk_pin) == 1 and switched >= 3 and self.rng.random() < 0.02:
                self.chip.reg, self.chip.out, self.chip.clk = dict(snap[0]), dict(snap[1]), snap[2]
                dbl = True
            else:
                return self._word(v, ext)
        self.chip.apply(ext, double_clock=dbl)
        return self._word(v, ext)

    def _word(self, v, ext):
        s = self.seat
        for p, val in self.chip.driven(ext).items():
            v = (v | s.bit(p)) if val else (v & ~s.bit(p))
        self.last = v
        return v

def open_board(a, seat):
    """From parsed args (--sim JED / --sim-fault / -p PORT) -> board object."""
    if getattr(a, 'sim', None):
        from .sim import GAL, Chip, load_jed
        fault = getattr(a, 'sim_fault', None)
        side = os.path.join(os.path.dirname(os.path.abspath(a.sim)), '.simfault')   # blind tests: fault not on the
        if not fault and os.path.exists(side): fault = open(side).read().strip() or None  # command line
        return FakeDupico(Chip(GAL(load_jed(a.sim))), seat, fault)
    return Dupico(a.port)

def power_cycle(d, settle=1.0):
    """Power off, wait until the socket supply has decayed (lines read 0), power on."""
    d.power(False); d.write(0)
    t0 = time.time()
    while d.read() and time.time() - t0 < 30: d.sleep(0.25)
    d.sleep(settle)
    d.power(True); d.sleep(0.5)
