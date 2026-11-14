#!/usr/bin/env python3
"""Bring-up checks for a DuPAL V3 "dupico" board (empty ZIF socket, no pod).

Uses the board's binary protocol directly (pyserial only):
  WRITE 0x00 + u64 LE   -> drives the 40 socket lines, answers with the read-back value
  READ  0x01            POWER 0x03 + on/off   MODEL 0x04   TEST 0x05   VERSION 0x06   OSC_DET 0x08 + n
Every frame ends with a checksum byte that makes the frame sum to 0 (mod 256).

usage:
  python3 dupico_check.py -p /dev/ttyACM0            run all automatic tests
  python3 dupico_check.py -p /dev/ttyACM0 --probe-gnd  interactive: lines driven high, touch a pin to GND (pin 21)
  python3 dupico_check.py -p /dev/ttyACM0 --probe-vcc  interactive: lines driven low, touch a pin to socket VCC (pin 42)
"""
import argparse, struct, sys, time
import serial

# ZIF42 pin -> bit index (from dupicolib M3BoardCommands); 21 = GND, 42 = switched VCC
PIN2BIT = {p: p - 1 for p in range(1, 21)} | {p: p - 2 for p in range(22, 42)}
BIT2PIN = {b: p for p, b in PIN2BIT.items()}
MASK = (1 << 40) - 1

def pins(v):
    return [BIT2PIN[b] for b in range(40) if v >> b & 1]

class Dupico:
    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=2)
        for attempt in range(3):
            line = self.s.readline(64).decode('ascii', 'replace').strip()
            if line == 'REMOTE_CONTROL_ENABLED':
                break
            self.s.dtr = False; self.s.reset_input_buffer(); time.sleep(0.5)
            self.s.dtr = True; time.sleep(0.5)
        else:
            sys.exit('Board did not announce REMOTE_CONTROL_ENABLED (close minicom first?)')
        self.s.reset_input_buffer()

    def cmd(self, payload, resp_len):
        chk = (-sum(payload)) & 0xFF
        self.s.write(bytes(payload) + bytes([chk]))
        r = self.s.read(1 + resp_len + 1)
        if len(r) != resp_len + 2 or r[0] != (payload[0] | 0x80) or sum(r) & 0xFF:
            raise IOError(f'bad response to command {payload[0]:#04x}: {r.hex()}')
        return r[1:-1]

    def model(self):   return self.cmd([0x04], 1)[0]
    def version(self): return self.cmd([0x06], 10).decode('ascii', 'replace').rstrip('\x00').strip()
    def selftest(self): return self.cmd([0x05], 1)[0] == 1
    def power(self, on): return self.cmd([0x03, 1 if on else 0], 1)[0] == 1
    def write(self, v):  return struct.unpack('<Q', self.cmd([0x00, *struct.pack('<Q', v & MASK)], 8))[0] & MASK
    def read(self):      return struct.unpack('<Q', self.cmd([0x01], 8))[0] & MASK
    def osc(self, n):    return struct.unpack('<Q', self.cmd([0x08, n & 0xFF], 8))[0] & MASK

def check(name, ok, detail=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {name}{(" - " + detail) if detail else ""}')
    return ok

def auto(d):
    allok = True
    print(f'Board model {d.model()} (expect 3), firmware {d.version()}')
    input('Make sure the ZIF socket is EMPTY (no pod), then press Enter... ')
    allok &= check('firmware self-test (6 fixed patterns)', d.selftest())

    # With the relay off, U7 (74HCT02) disables the 595 outputs once SWITCHED_VCC has decayed below the
    # HCT input threshold. C1+C2 (~10 uF) only discharge through R42 (470K) and the SOCKET VCC LED,
    # so this takes a few seconds: poll instead of reading once.
    d.power(False)
    d.write(MASK)
    t0 = time.time(); v = d.read()
    while v and time.time() - t0 < 30:
        time.sleep(0.25); v = d.read()
    dt = time.time() - t0
    allok &= check('socket power OFF: drivers disabled, all lines pulled low (470K)', v == 0,
                   (f'took {dt:.1f} s for SWITCHED_VCC to decay (normal)' if v == 0 else
                    f'still high after 30 s: {pins(v)} - check relay K1 contacts, pin 42 voltage, U7'))

    print('  Switching socket power ON - the relay should click and the SOCKET VCC LED light.')
    d.power(True); time.sleep(0.3)
    for name, pat in (('all low', 0), ('all high', MASK)):
        v = d.write(pat)
        allok &= check(f'pattern {name}', v == pat, f'wrong pins: {pins(v ^ pat)}' if v != pat else '')

    # stuck lines: wrong under both all-low and all-high
    v_lo, v_hi = d.write(0), d.write(MASK)
    stuck_hi = {BIT2PIN[b] for b in range(40) if v_lo >> b & 1}
    stuck_lo = {BIT2PIN[b] for b in range(40) if not v_hi >> b & 1}
    bad = stuck_hi | stuck_lo
    good_bits = [b for b in range(40) if BIT2PIN[b] not in bad]
    gmask = sum(1 << b for b in good_bits)
    pairs = set()
    for b in good_bits:
        for level in (1, 0):                     # walking 1, then walking 0
            pat = (1 << b) if level else (MASK & ~(1 << b))
            v = d.write(pat)
            wrong = (v ^ pat) & gmask
            if not wrong:
                continue
            for c in [x for x in good_bits if wrong >> x & 1 and x != b]:
                pairs.add(tuple(sorted((BIT2PIN[b], BIT2PIN[c]))))
            if wrong >> b & 1:                   # b itself was dragged: find the line doing it
                for c in good_bits:
                    if c == b: continue
                    pat2 = pat ^ (1 << c)        # drive the suspect to the same level as b
                    if not ((d.write(pat2) ^ pat2) >> b & 1):
                        pairs.add(tuple(sorted((BIT2PIN[b], BIT2PIN[c]))))
    msgs = [f'pin {p} stuck high' for p in sorted(stuck_hi)] + [f'pin {p} stuck low' for p in sorted(stuck_lo)]
    msgs += [f'pins {a} and {b} bridged' for a, b in sorted(pairs)]
    allok &= check('walking 1 / walking 0 over all 40 lines', not msgs, '; '.join(msgs))

    d.write(0x5555555555)
    o = d.osc(200)
    allok &= check('static lines do not toggle (200 reads)', o == 0, f'toggling: {pins(o)}' if o else '')

    d.write(0); d.power(False)
    print('\nRESULT:', 'ALL PASSED' if allok else 'FAILURES - see above')
    print('Next: measure pin 42 = ~5 V with power on and 0 V with power off, then run --probe-gnd.')
    return allok

def probe(d, high):
    d.power(True); time.sleep(0.3)
    base = MASK if high else 0
    print(f'All lines driven {"HIGH" if high else "LOW"} through their 10K resistors.')
    print(f'Touch each socket pin in turn with a wire from pin {"21 (GND)" if high else "42 (socket VCC)"}.')
    print('The program prints the pin it sees. Ctrl-C to stop.\n')
    last = None
    try:
        while True:
            v = d.write(base)
            diff = pins(v ^ base)
            if diff != last:
                print('  touched:', diff if diff else '-')
                last = diff
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        d.write(0); d.power(False)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('-p', '--port', required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--probe-gnd', action='store_true')
    g.add_argument('--probe-vcc', action='store_true')
    a = ap.parse_args()
    d = Dupico(a.port)
    if a.probe_gnd: probe(d, True)
    elif a.probe_vcc: probe(d, False)
    else: sys.exit(0 if auto(d) else 1)
