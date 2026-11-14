# Bench setup: dupico, seating, wiring

## The board

The DuPAL V3 "dupico" (github.com/DuPAL-PAL-DUmper) drives and reads 40 lines of a ZIF42 socket through 10K
resistors, switches the socket supply with a relay, and talks a small binary protocol over USB serial at 115200.
`galre` speaks that protocol directly (pyserial only). It does not need the DuPAL Python tools (dupicolib,
dpdumper), but must not run while one of them, or a terminal program, holds the port.

- Port: `/dev/ttyACM0` on Linux by default (`-p` to change). The user must be in the serial group (`dialout` on
  Debian/Ubuntu/Fedora, `uucp` on Arch-based systems), then log in again.
- The board announces `REMOTE_CONTROL_ENABLED` when it starts. If it doesn't, another program has the port or the
  firmware is not the remote-control build.
- `galre check` (EMPTY socket) checks: firmware self-test, all lines low with the socket supply off, patterns,
  walking 1 and 0 on all 40 lines (stuck and bridged lines), and no toggling. The supply-off test takes a few
  seconds because about 10 uF on the switched supply decays slowly through high resistance: this is normal.
- Lines are driven through 10K, so a GAL output always wins over the dupico. That is how a pin is classified:
  read it with the dupico pulling low, then pulling high. 0 and 0 means driven 0, 1 and 1 driven 1, 0 then 1 not
  driven (Z).

## Seating

### Direct (no PAL POD): chip top-aligned in the ZIF42, pin 1 at ZIF 1 (lever end)

| Chip | Chip pins -> ZIF42 pins | GND wire | Capacitor |
|---|---|---|---|
| GAL16V8 (20 pins) | 1-10 -> 1-10, 11-20 -> 33-42 | ZIF 10 -> ZIF 21 | 100 nF across chip pins 20 and 10 |
| GAL20V8 (24 pins) | 1-12 -> 1-12, 13-24 -> 31-42 | ZIF 12 -> ZIF 21 | 100 nF across chip pins 24 and 12 |

VCC lands on ZIF 42 (the switched supply) by itself. GND does not: the chip's GND pin sits on a signal line, so a
dupont wire must join it to ZIF 21 (dupico ground) on the headers beside the ZIF. Without it the chip's ground
floats and every line reads 1 (probe reports "no ground").

The capacitor is not optional for registered chips. With the long supply path (relay, ZIF contacts, a dupont
ground), a GAL's own switching current bounces its rails. That happened while the clock edge, driven slowly
through 10K, was near threshold. Measured on a GAL16V8 in registered mode:
- **without the capacitor:** 0.8-1.6% of clocks double-clocked where three or more outputs switched at once;
- **with 100 nF fitted at the chip:** 0 in 9760.

Another measured factor is switching other dupico lines in the same write as the clock edge, which `galre` never
does. Keep the capacitor leads short. A short ground wire helps too.

### PAL POD (the DuPAL adapter with its own ZIF24 and decoupling)

| Chip | Placement | Chip pins -> ZIF42 pins |
|---|---|---|
| GAL16V8 | pod in the topmost ZIF42 position, chip at the bottom of the pod's ZIF24 (pod pins 1-2, 23-24 empty) | 1-9 -> 3-11, 10 -> 21 (GND), 11-19 -> 31-39, 20 -> 42 |
| GAL20V8 | pod in the topmost position, chip fills the ZIF24 | 1-11 -> 1-11, 12 -> 21 (GND), 13-24 -> 31-42 |

For a 20-pin chip, the tools hold ZIF line 22 high: it powers the chip's VCC through the pod's transistor. For a
24-pin chip that line must stay low (through the pod it would put 5 V on chip pin 22), and the tools never drive
it. No ground wire is needed, and the pod has its own decoupling.

### PLCC parts

The ZIF takes DIP only: use a PLCC-to-DIP adapter wired as the DIP pinout. A used PLCC GAL that trips the
programmer's over-current check on erase is probably dead, so don't use it as a blank.

## End-to-end bench test with a known chip

To prove the board, the seating and the tools together before trusting them with an original, program a blank with
one of the bundled examples (security fuse off). Record what it should do in the simulator, then replay that record
on the real chip:

```
galre explore --sim assets/examples/fsm16.jed --mode registered --state 12-19 --regs 12,13,16-19 \
      --allow-z 14 --inputs 2-5 --hold 6=0,7=0,8=0,9=0 -o fsm16_ref.tsv
galre replay fsm16_ref.tsv                      # real chip in the socket: must PASS
galre probe -o fsm16_probe.json                 # optional: same roles as the simulator found?
```

`fsm16` (registered, with a latch and an output enable) is the most demanding: three outputs switch on some clocks,
so a missing capacitor shows up as replay mismatches. `latch16`, `dec20` and `fsm20` cover the other cases (see
`galre selftest` for the roles of each).

## Programming copies

Any programmer that supports GAL16V8/20V8 (or ATF16V8B/20V8B) and loads JEDEC files works. Blanks: Lattice
GAL16V8/20V8 or Microchip ATF16V8B/ATF20V8B (DIP for the dupico). **Turn the security fuse OFF** (called security,
protect, lock or encrypt depending on the software; some programmer software sets it by default), and
a secured copy can't be verified or read back. Never put the original in a programmer socket for anything other
than a read attempt, and only if the user wants one (a secured chip reads back as all zeros or all ones).

## Recording bench conditions

Note with every result:
- the seating (direct or pod), and whether the capacitor and the GND wire were fitted;
- the date and the dupico firmware (probe prints it);
- which chip it was: the original, or copy N and its blank type.
