# gal-dupico-re: recover secured GAL logic with a DuPAL V3 ("dupico")

A Claude Code plugin and a standalone tool for preserving read-protected GAL16V8 and GAL20V8 chips. A secured GAL
cannot be read in a programmer, but its behaviour can be recorded from its pins and re-expressed as equivalent
equations for a replacement chip. The tool does this with a [DuPAL V3 "dupico"](https://github.com/DuPAL-PAL-DUmper)
board, never writing to the original, and checks every step against the recording.

It was built to recover the three GALs of the ICD AdSCSI 2000 Amiga SCSI card
([adscsi2000](https://github.com/sbagneris/adscsi2000)). All three are stateful (latches from output feedback, one
also registered), so a plain truth-table dump would have produced wrong replacements.

## Why not just dump a truth table

Many GALs of the era build latches from output feedback, and some use registers. A dump is then one
history-dependent snapshot, and changing several inputs at once makes the latches race. Instead:

1. **probe**: seating and ground check, pin classes, registered-mode detection, suggested pin roles.
2. **dump + ordercheck**: the quick first look, and the proof that it isn't the logic when the chip has memory.
3. **explore**: one input change (or one clock pulse) at a time, every move from every reachable state. With /OE
   held low, all of a GAL16V8/20V8's state is on its pins, so this is the chip's complete record.
4. **fit**: espresso per output, enable and register. The fit is refined until every recorded move is reproduced
   as the only outcome, whatever order the outputs settle in.
5. **pld --galasm**: GALasm source and JED. Unused output cells are made never-driven, not left as GALasm's default.
6. **jedcheck**: replay every recorded move on the JED's fuse map.
7. **replay**: program a copy (security fuse off) and replay the original's record on it in the dupico.

Every bench command also runs against a **simulated dupico** holding any JED (`--sim chip.jed`, with optional
`--sim-fault noground|doubleclock`), for rehearsal and testing. Exploring the three recovered AdSCSI JEDs in the
simulator gives exactly the move tables recorded from the real chips.

## Install

As a Claude Code plugin (the skill `recover-gal-logic` then guides Claude through the procedure and its pitfalls):

```
/plugin marketplace add sbagneris/gal-dupico-re
/plugin install gal-dupico-re@gal-dupico-re
```

Or use the tool directly: `python3 skills/recover-gal-logic/scripts/galre.py -h`.

Requirements: Python 3.9+, pyserial (bench only), [espresso](https://github.com/chipsalliance/espresso) and
[GALasm](https://github.com/daveho/GALasm) on the PATH. Check with `galre.py selftest`: about 10 seconds, no
hardware. It runs the whole pipeline on four example designs and injects both bench faults.

## Bench essentials

- Without the PAL POD, seat the chip top-aligned in the ZIF42 (pin 1 at ZIF 1). Wire its GND pin to ZIF 21, or every
  line reads 1, and fit a **100 nF capacitor across its supply pins**. Without the capacitor, a registered GAL
  double-clocked on 0.8-1.6% of clocks where three outputs switched (0 with it).
- Never program the original. Turn the security fuse off on copies (some programmer software sets it by default).
- Hold a pin out of the exploration only when the board shows it is unconnected or tied, never because the chip
  seemed to ignore it.

Details: `skills/recover-gal-logic/SKILL.md` and its `references/`.

## Limits

GAL16V8 and GAL20V8 (and ATF16V8B/20V8B) in complex and registered modes. Simple-mode chips fit as complex mode
unless an output uses all 8 terms. Pin-compatible PALs (16L8, 16R4-R8, 20L8, 20R4-R8) should work but are
untested. GAL22V10 is not supported. The equations are functionally equivalent on everything recorded: the
original product terms of a secured chip cannot be recovered, and timing is not captured.

History: v0 (tag `v0`) is the set of scripts exactly as used on the AdSCSI 2000; v1 generalises them into this
plugin.

## Licence

BSD-3-Clause (see `LICENSE`). Copyright (c) 2026 Stephane Bagneris. Tools written with Claude (Anthropic). The
DuPAL board and protocol are by the DuPAL-PAL-DUmper project; chip column tables follow GALasm 2.1.
