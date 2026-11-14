---
name: recover-gal-logic
description: Recover the logic of a read-protected (secured) GAL16V8 or GAL20V8 (also ATF16V8/ATF20V8, and pin-compatible PAL16L8/16R4-R8/20L8/20R4-R8) with a DuPAL V3 "dupico" board, and turn it into a verified JEDEC file for a replacement chip. Covers bench setup, probing, detecting latches and registers, exhaustive single-input-change exploration, equation fitting with espresso, GALasm generation, fuse-map verification, and checking the programmed copy against the original. Use this whenever someone wants to dump, read, back up, clone, reverse-engineer, preserve or replace a secured/protected/locked GAL or PAL, mentions DuPAL, dupico or dpdumper, has a vintage board (Amiga, arcade, Atari, computer expansion card...) with GALs reading back as all-zero/protected in a programmer, or asks why a GAL truth-table dump does not work in circuit - even if they do not name this skill.
---

# Recover GAL logic with a dupico

A secured GAL cannot be read in a programmer: the fuse map is locked. Its behaviour can still be recorded from its
pins and re-expressed as equivalent equations. This skill does that with a DuPAL V3 "dupico" (a 40-line USB pin
driver/reader with a ZIF42 socket), read-only on the original, and checks every step against the recording.

The naive approach (dump a truth table, minimise it) silently fails on many real chips. Designers of the era built
latches from output feedback and used registers; a truth table is then just one history-dependent snapshot, and
changing several inputs at once makes latches race. The procedure here records the chip as a state machine, one
input change at a time, and only accepts equations that reproduce every recorded move exactly.

All tools are one script: `python3 <skill-dir>/scripts/galre.py <command>` (below just `galre`). Run
`galre <command> -h` for options. Every bench command accepts `--sim JED` instead of a real board: a simulated
dupico holding a GAL programmed with that JED. Use it to rehearse, to test, and to check a recovered JED.

## Ground rules (and why)

- **Never program, erase or "verify with write" the original chip.** It is the only copy of the logic. Work on
  blank GAL16V8/ATF16V8B (or 20V8) chips. When the user programs a copy, the security fuse must be OFF, or the copy
  cannot be checked or reused. Its name varies (security, protect, lock, encrypt), and some programmer software
  sets it by default. Refer to the user's own programmer if they named one; otherwise keep the advice generic.
- **Seat the chip the way the tools are told** (`--chip 16v8|20v8`, `--seat direct|pod`); `probe` prints the exact
  setup. Without the PAL POD adapter ("direct"), the chip sits top-aligned in the ZIF42. Its GND pin then lands on a
  signal line and **must be wired to ZIF pin 21**, or the chip floats and every line reads 1. Also fit a **100 nF
  capacitor across the chip's VCC and GND pins**. Without it, a registered GAL double-clocks on about 1% of the
  clocks where three or more outputs switch together: its own switching current bounces the long supply path.
- **Single input changes only.** The explorer already works this way. Don't write ad-hoc scripts that jump between
  input vectors: they make latches race and record behaviour the chip never shows in circuit.
- **Hold a pin only on board evidence.** `--hold` removes a pin from exploration, halving the work per pin. A pin may
  be held only if the board leaves it unconnected or ties it to a level (continuity, schematic). Never hold a pin
  because the chip seemed to ignore it: effects hide behind latch states. On the ICD AdSCSI 2000, pins that changed
  nothing in a full dump appear in the recovered equations. The other way round, a pin the board leaves unconnected
  but the chip drives (often a GALasm default register) is an output: keep it in the state, never hold it. Holding
  it would feed the fitter a wrong feedback level; the explorer reports `held pin driven by the chip`.
- **Keep everything.** Probe JSON, dumps, move tables, fit reports, PLD and JED all go in one directory per chip
  (e.g. `gals/<board-ref>/`), together with the bench conditions. The move table is the preservation record; the
  equations are only one equivalent rendering of it.

## Procedure

Work in the chip's directory. Tell the user what each step showed before moving on; at the bench they are the one
swapping chips and wires.

### 0. Before the chip: board and setup

Ask for what you can't measure:
- the chip's marking and type (16V8 or 20V8, 20 or 24 pins);
- the board and the chip's role (reference designator, neighbouring parts);
- which pins the board leaves unconnected or ties, if known;
- whether they have the PAL POD.

Installed tools: Python 3 with pyserial, `espresso`, `galasm` (the self-test checks the last two). On a new setup,
run `galre selftest` once (simulated, about 10 s) and `galre check` with an **empty** socket to bring the board up
(the power-off test takes a few seconds: the socket supply decays slowly). See `references/bench.md` for wiring,
pod use and serial-port permissions. When the user has a blank GAL, the end-to-end bench test there
(program a bundled example, replay its simulated record on the real chip) proves the board, seating and capacitor
before an original goes in.

### 1. Probe

```
galre probe --chip 16v8 --seat direct -o probe.json
```

Probe checks placement and ground, then classifies the output-cell pins as driven, tri-stated or never driven. It
detects registered mode: the /OE pin (11 on a 16V8, 13 on a 20V8) tri-states the registered outputs, which change
only on the rising edge of pin 1. Last, it looks for history effects and suggests the pin roles for exploration. If
step 1 fails, nothing after it means anything: fix the seating or the ground wire first.

### 2. Is a plain dump the logic? (usually no)

```
galre dump --probe probe.json -o dump.tsv
galre ordercheck dump.tsv
```

`ordercheck` re-reads every vector in Gray-code and in random order. Any difference means the chip has memory, and
the dump is not its logic. A chip that passes may still hide state: the probe's random vectors and one dump
can miss a latch that only matters in rare input combinations (AdSCSI U3 looked combinational to a probe).
Exploration is cheap enough to run on every chip anyway; the dump is the quick first look and a cross-check.

### 3. Explore (the record)

```
galre explore --probe probe.json -o moves.tsv                         # complex mode
galre explore --probe probe.json --recheck-clk 3 --recheck-all -o moves.tsv   # registered mode
```

Add `--hold 8=0,9=0` only for pins the board proves unconnected or tied, and `--state/--inputs/--regs/--allow-z`
to override the probe's roles. Each node is (state of every driven output pin, input vector). Each move is one
input flip or one clock pulse, and every move from every reachable node is measured. Size grows as 2^inputs per
output state: probe prints an estimate. Warn the user before anything over an hour, and look for pins to hold on the
board first.

Read the result line and the ISSUE lines (meaning and fix for each: `references/troubleshooting.md`):
- **`input pin driven by the chip`**: a pin treated as an input is an output in some state. Re-explore with it in
  `--state` and `--allow-z`.
- **`move not repeatable` or several power-up states**: on a registered chip this is almost always double clocking.
  Check the capacitor, then keep the `--recheck-clk` majority. On a complex chip it means a race in the original;
  report it.
- **`register changed on an input flip`**: the mode or register guess is wrong.
- **Replay mismatches** must be 0.

### 4. Fit

```
galre fit moves.tsv            # -> moves_fit.txt, RESULT: PASS / FAIL
```

espresso minimises each output (OE terms, combinational outputs, register D inputs) on the recorded points. The
fitter then refines on moves where several outputs change: it chooses an order in which they settle that is
consistent with everything else. It passes only when every recorded move is reproduced as the one and only
outcome, whatever order the outputs settle in, every settled node is stable, and the power-up state is stable. On
FAIL, read `references/troubleshooting.md` (conflicts, too many terms, no convergence) before changing anything.

### 5. GALasm source and JED

```
galre pld moves_fit.txt --name MYCHIP --galasm [--names names.csv]
```

The `--names` CSV (`pin,name`, at most 8 characters) gives board signal names. Pins not used by the logic are
declared never-driven, not left as GALasm's default (see `references/method.md`, "GALasm pitfall").

### 6. Check the JED against the record

```
galre jedcheck MYCHIP.jed moves.tsv
```

This is independent of the fitter: it replays every move on the JED's fuse map. Only a PASS here goes to a
programmer.

### 7. Program a copy and compare it with the original

The user programs a blank chip (security fuse off) and seats it exactly like the original. Then:

```
galre replay moves.tsv                # the original's record replayed on the copy: PASS / FAIL
```

For a full second opinion, explore the copy into `copy_moves.tsv` and run `galre compare moves.tsv copy_moves.tsv`.
Report both. Only then suggest an in-circuit test, and keep the original safe and labelled.

### 8. Report

Give the user, and write to a `README.md` in the chip's directory:
- the chip, its mode, and the pin roles (inputs, outputs, registers, held pins and why);
- nodes, moves, power-up state(s), replay and recheck results, and any issues with how they were resolved;
- the fit result (terms per output);
- jedcheck and copy-replay results;
- the bench conditions (seating, capacitor, date).

Say plainly that the equations are equivalent, not the original's product terms. A secured fuse map cannot be
recovered, and combinations the circuit never reaches were don't-cares.

## Rehearsing and testing without hardware

- `galre selftest` runs the whole pipeline on bundled example JEDs (`assets/examples/`: latches, registers,
  output enables, both chips) and checks that the missing-ground and double-clock faults are caught.
- Any bench command with `--sim some.jed` (and `--sim-fault noground|doubleclock`) behaves like a dupico holding
  that chip. Use it to show the user what to expect, to debug a procedure, or to check a recovered JED before
  burning a chip: `galre replay moves.tsv --sim MYCHIP.jed` is the copy check in simulation.

## Reference files

- `references/bench.md`: dupico setup, socket maps for direct and pod seating, ground wire, decoupling, permissions.
  Read it when setting up, or when probe step 1 fails.
- `references/method.md`: GAL16V8/20V8 modes and why single-input-change exploration plus fitting is complete for
  them; limits (simple-mode 8-term outputs, other chip families); file formats. Read it when results look surprising
  or the user asks how it works.
- `references/troubleshooting.md`: every issue and FAIL message, what it means, and what to do.
