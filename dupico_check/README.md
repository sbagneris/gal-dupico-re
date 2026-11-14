# dupico (DuPAL V3) bring-up kit

Run in this order. Steps 1-4 need only the bare board (empty ZIF socket, no pod).

## 0. Before power (visual)
- U10 (back side) sits the *opposite* way to U8, U9, U11, U12 - per the board README, trust the silkscreen.
- 74HCT parts where the BOM says HCT (U2-U12); no solder bridges on the SOICs; D1, LEDs, fuse, relay K1 orientation.

## 1. Status LED (Pico LED)
- waiting for a host: short blink (100 ms on, 1 s off)
- terminal connected: even 300 ms on / 300 ms off
- error: slow 1 s on / 1 s off

## 2. Automatic checks
Close minicom first (the port can only be opened once). `pip install pyserial`, then:

    python3 dupico_check.py -p /dev/ttyACM0

Checks: model/firmware, the firmware self-test, lines pulled low with socket power off,
all-low / all-high, walking 1 and walking 0 on all 40 lines (reports stuck lines and
bridged pairs by ZIF pin), and that static lines don't toggle.

## 3. Socket power
With power on (relay clicks, SOCKET VCC LED lit) ZIF pin 42 should read ~5 V to pin 21;
with power off, 0 V. `dupico_check.py` leaves power off when it exits.

## 4. Every socket contact and the pin map
    python3 dupico_check.py -p /dev/ttyACM0 --probe-gnd
All lines are driven high through their 10K resistors. Touch each pin with a wire from pin 21:
the program must print exactly the pin you touched. Then repeat with `--probe-vcc` (wire from pin 42).
This also proves the 10K series resistors that the Hi-Z detection depends on. It's safe: 0.5 mA per touch.

## 5. Official tools
Python 3.12+:

    pip install git+https://github.com/DuPAL-PAL-DUmper/dupicolib.git
    pip install git+https://github.com/DuPAL-PAL-DUmper/dpdumperlib.git
    pip install git+https://github.com/DuPAL-PAL-DUmper/dpdumper.git
    dpdumper -p /dev/ttyACM0 test          # empty socket

## 6. End to end with the PAL POD and a known GAL
The PAL POD is a separate adapter PCB (DuPAL_Board repo, `board/dupico_pods/pod_pal`): it plugs into the
dupico's ZIF42 and carries a ZIF24 for the PAL/GAL. It is required: the dupico only has GND on ZIF42 pin 21 and
switched VCC on pin 42, and the pod routes the device's GND pin (12 on a 24-pin part) to pin 21.
A 20V8 placed straight in the ZIF42 (top-aligned) gets VCC but its GND pin lands on signal line 12: the chip
floats up and every line reads 1, inputs included. Workaround without a pod: same placement, plus a wire from
the chip's pin 12 to ZIF42 pin 21; duptest20_palpod.toml then works unchanged. The dupico has pin headers along
both sides of the ZIF, so a dupont jumper between header pins 12 and 21 does it (verified: 20V8 test PASS this way).
For 24-pin parts keep the pod's JP1 open.
Without the pod, also fit a 100 nF capacitor across the chip's supply pins (16V8: 20-10, 20V8: 24-12), as the
pod's C1/C2 do: without it AdSCSI U4 (registered) double-clocked in 1.6% of clocks where 3 outputs switch,
with it 0 in 9760 (`clkstress16.py`).

Two equivalent tests; use whichever chip you have.

| Chip | JED | Definition | Placement in the pod's ZIF24 |
|---|---|---|---|
| GAL16V8 / ATF16V8B | duptest.jed | duptest_palpod.toml | bottom-aligned (pins 1-2 and 23-24 empty) |
| GAL20V8 / ATF20V8B | duptest20.jed | duptest20_palpod.toml | fills the whole ZIF24 |

Without a pod (chip straight in the dupico ZIF42, top-aligned, pin 1 at ZIF 1, plus a GND jumper on the headers):

| Chip | JED | Definition | Jumper |
|---|---|---|---|
| GAL16V8 / ATF16V8B | duptest.jed | duptest_direct.toml | header 10 -> 21 |
| GAL20V8 / ATF20V8B | duptest20.jed | duptest20_palpod.toml (same pin map) | header 12 -> 21 |

`gal16v8_direct.toml` is a first-look definition for an unknown GAL16V8 (AdSCSI U3/U4/U5), same placement and
jumper; read its header comments for how to interpret the dump.

1. Program the JED on the T48 (security fuse off). Both were checked against their expected truth tables with
   a fuse-map simulator (`gal_sim.py`) that first reproduced a third-party GAL16V8 sample.
2. PAL POD in the top of the dupico ZIF, chip in the pod as above.
3. Dump and compare (the checker detects which test it is from the dump size):

       dpdumper -p /dev/ttyACM0 read -d duptest20_palpod.toml --check_hiz -o dump.txt
       python3 check_dump.py dump.txt

PASS means inputs, outputs, socket power and Hi-Z detection all work. The last output
(pin 19 on the 16V8, pin 22 on the 20V8) is tri-stated when pin 7 = 0.

Files: dupico_check.py, duptest.pld/.jed, duptest20.pld/.jed, duptest_palpod.toml, duptest20_palpod.toml,
duptest_direct.toml, gal16v8_direct.toml,
duptest_expected.py, duptest20_expected.py, check_dump.py, gal_sim.py,
probe20.py (quick 20V8 wiring check: a few vectors, shows whether inputs follow and outputs are driven)

## 7. Reading an unknown GAL16V8 (no pod, jumper header 10 -> 21)
1. `probe16.py`: placement/ground check, pin classes (driven / Z), quick state check on pin 1 (registered mode).
2. `dpdumper ... read -d gal16v8_direct.toml --check_hiz`, then a per-chip TOML with I/O pins used as inputs
   moved to `address`.
3. `ordercheck16.py DUMP`: re-reads in Gray and random order. Any difference = latches (all three AdSCSI GALs
   have them), so the dump is only a snapshot.
4. `regexplore16.py`: single-input-change state/move table (`--no-clk` for complex/simple mode, `--allow-z` for
   pins with OE terms, `--load` + `--recheck-clk N` to re-clock CLK moves). This is the complete record.
