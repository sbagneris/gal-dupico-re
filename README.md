# gal-dupico-re - recovering secured GAL logic with a DuPAL V3 ("dupico")

Tools for preserving the logic of read-protected GAL16V8 chips (and checking GAL20V8 / GAL16V8 test chips) with a
[DuPAL V3 "dupico"](https://github.com/DuPAL-PAL-DUmper) board. Version 0: the tools exactly as used to recover the
three GALs of the ICD AdSCSI 2000 ([adscsi2000](https://github.com/sbagneris/adscsi2000)); they are being
generalised into a reusable procedure (and a Claude Code skill).

Everything here is **read-only** on the chip under test: it is only driven through the dupico's 10K resistors and
clocked, never programmed.

## Why not a plain dump

Many GALs of the era build latches from output feedback, and some use registers. A truth-table dump then shows only
one history-dependent snapshot, and changing several inputs at once makes the latches race. The approach here:

1. `probe16.py`: placement / ground check, pin classes (driven, Z), quick state check.
2. `dpdumper` + `gal16v8_direct.toml`: first-look dump; `ordercheck16.py`: re-read in Gray and random order. Any
   difference means state: the dump is not the chip's logic.
3. `regexplore16.py`: map the chip one input change (or one clock pulse) at a time: every reachable state and every
   move from it, with replays; `--recheck-clk --recheck-all` re-clocks every clock move (`clkstress16.py` measures
   clock robustness).
4. `galfit.py`: fit equations with espresso from those moves and refine until every recorded move replays exactly
   with outputs settling in any order.
5. `fit2pld.py` -> GALasm source -> `galasm`; `jedcheck.py` replays every move on the JEDEC fuse map
   (`gal_sim.py`, complex and registered GAL16V8 modes).

## Bench lessons (read before using)

- Without the PAL POD adapter, a chip in the dupico ZIF42 needs a wire from its GND pin to ZIF pin 21 (otherwise its
  ground floats and every line reads 1) and a **100 nF capacitor across its supply pins**: without it a registered
  GAL double-clocks when three outputs switch at once (measured 0.8-1.6% of such clocks, 0 with the cap).
- Never switch other dupico lines in the same write as a clock edge.
- GALasm leaves an output cell that no equation mentions as an active registered output in registered mode;
  `fit2pld.py` declares such pins as never-enabled tri-state outputs instead.
- Turn the security fuse off when programming test and replacement chips (XGpro sets it by default).

## Contents

- `dupico_check/`: dupico bring-up checks (`dupico_check.py`), GAL16V8/GAL20V8 end-to-end test JEDs and
  definitions, the probe / order-check / explorer / clock-stress tools, `gal_sim.py`.
- `gals/`: `galfit.py`, `fit2pld.py`, `jedcheck.py`.

Requirements: Python 3.12+, pyserial, `espresso` and `galasm` on
the PATH, the DuPAL tools (`dpdumper`) for first-look dumps.

## Licence

BSD-3-Clause (see `LICENSE`). Copyright (c) 2026 Stephane Bagneris.
