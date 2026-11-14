# Method: why this works, where it stops

## Contents
1. GAL16V8 / GAL20V8 architecture as the tools model it
2. Why single-input-change exploration is a complete record
3. Fitting
4. What "equivalent" means, and the limits
5. GALasm pitfall
6. File formats

## 1. Architecture

Both chips have 8 output logic macrocells (OLMCs). Each has 8 product terms over the AND array, an XOR polarity
fuse and a configuration bit (AC1). Two global bits (SYN, AC0) choose the mode for the whole chip:

| Mode | SYN AC0 | Output cell | Array inputs (16V8) | Array inputs (20V8) |
|---|---|---|---|---|
| simple | 1 0 | AC1=0: combinational output, 8 terms, always on; AC1=1: input | 1-9, 11, 12-14, 17-19 | 1-11, 13, 14, 15-17, 20-23 |
| complex | 1 1 | combinational, 7 terms + 1 output-enable term | 1-9, 11, feedback 13-18 | 1-11, 13, 14, 23, feedback 16-21 |
| registered | 0 1 | AC1=0: D flip-flop on pin 1, 8 terms, driven while /OE is low; AC1=1: as complex | 2-9, feedback 12-19 | 2-11, 14, 23, feedback 15-22 |

/OE is pin 11 (16V8) or pin 13 (20V8) in registered mode. Feedback in complex mode, and for combinational cells, is
the pin level, so a tri-stated output cell acts as an input. Registered feedback is the register itself. Registers
power up with their pins high.

The consequence the whole method rests on: **with /OE held low, every bit of state is visible on a pin.** Each
register drives its pin, and a latch is a combinational output feeding back through its own pin. No buried state
exists that the pins can't show. So the set of (pin states, inputs) nodes is the chip's full state space.

The column tables are GALasm 2.1's (`PinToFuse16Mode*/20Mode*`). The fuse semantics in `scripts/galre/sim.py` were
checked against third-party JEDs, GALasm output, and the three AdSCSI 2000 replacement JEDs. The simulator
explores each of the AdSCSI JEDs into exactly the move table recorded from the real chip.

## 2. Exploration

A node is the settled state of every driven output pin together with the input vector. A move is:
- flip one input pin and wait for everything to settle, or
- one clock pulse (rising edge, then falling) with the inputs held, in registered mode.

From the power-up node, the explorer measures every move from every node it reaches. It walks at random among
unmeasured moves, uses BFS over known moves to reach unfinished nodes, and power-cycles when stuck. Built-in checks:
- inputs read back as driven;
- state pins are driven, except where Z is allowed;
- registers change only on a clock;
- a repeated move gives the same result;
- at the end, a random sample (or all) of the moves is replayed.

Why one change at a time: the circuit around a GAL changes several inputs "at once", but never exactly at once.
For a latch, the order decides the outcome. The original board design is supposed to avoid such races, or tolerate
either outcome; a dumper that jumps between arbitrary vectors creates races the board never does. Moves where
several outputs change in one step are kept, because the chip resolves them internally. The fitter has to explain
them.

What the record does not contain: states reachable only through simultaneous input changes (races, by design),
timing (propagation delay, glitches) and analogue behaviour. A copy is a functional replacement, not a timing
clone. Fast board logic that depends on a GAL's exact speed grade needs an equal or faster part.

## 3. Fitting

`fit` builds, per function (output value, output enable, register D), the points the data fixes. Everything else
is a don't-care:
- **Settled nodes:** the value of each driven output; its enable is 1, or 0 where the pin is Z.
- **Clock moves:** D of each register equals its value after the clock.
- **Single-output flips:** the output that changed was excited by the new inputs.

Each function goes through espresso in both polarities, and the smaller cover is kept (OE: positive and a single
term). The fit is then simulated with outputs settling one at a time in **any** order. Each move the fit gets wrong
gets an excitation path added, for an order consistent with all points so far; this repeats until nothing changes.
PASS means:
- every recorded move ends in exactly the recorded state, whatever order the outputs settle in (so the copy
  introduces no race the original didn't show);
- every settled node is stable;
- the power-up state is stable;
- every function fits its term limit.

Floating feedback (a Z state pin) reads as 0, the level the explorer holds it at.

## 4. Equivalence and limits

- The equations are equivalent on everything recorded. A secured fuse map's real product terms are unknowable,
  and input combinations the chip never reached are don't-cares. Say this when reporting.
- Power-up: GAL registers power up high. For latches, the power-up state recorded is what the original settled
  to, and the copy's fit is checked to hold it. A latch that powered up differently on another day would show as
  several power-up states: report it.
- Simple mode: from the pins, a simple-mode chip looks like complex mode with outputs always enabled. It fits as
  complex mode unless an output needs all 8 terms ("TOO MANY TERMS" on an output that is never Z). The tools
  don't generate simple-mode sources yet.
- PALs: PAL16L8/20L8 behave like complex mode, and PAL16R4/R6/R8 and 20R4/R6/R8 like registered mode, with the same
  pinouts. The procedure applies and the replacement is a GAL. Untested on real PALs.
- GAL22V10 and other architectures are not supported: the chip tables, simulator and fitter would all need them.
- Size: 2^inputs vectors per output state. At about 2-4 ms per dupico command (probe prints the measured rate) and
  roughly 4 commands per move, around 14 inputs with a handful of states is an evening. Beyond that, hold pins
  the board proves unconnected, or split the problem with the user.

## 5. GALasm pitfall

GALasm leaves an output cell that no equation names as NOTUSED with AC1 = 0. In registered mode that is a
registered output driving its pin whenever /OE is low; in complex mode it is an undocumented configuration. Many
original chips were assembled this way. The example `fsm16` shows it: the probe finds "registers" on pins the
design calls NC. `galre pld` declares every output-cell pin without logic as `Pn.T = GND` / `Pn.E = GND`
(combinational, never enabled). Recorded driven pins, even constant ones, are reproduced as fitted.

## 6. File formats

- `probe.json`: chip, placement, mode, suggested `state`, `regs`, `inputs`, `allow_z`, and per-pin evidence.
- Dump (`dump.tsv`): `# meta:` line, then `HEXVECTOR<TAB>outputs` (input bit i = the i-th pin of `inputs`,
  outputs highest pin first, 0/1/Z).
- Move table (`moves.tsv`): `# meta: {json}` with chip, placement, mode, state, regs, inputs, held, allow_z,
  power_up, counts and issues; then rows `state<TAB>inputs<TAB>move<TAB>next`. State and inputs are written highest
  pin first; move is `pinN` or `CLK`.
- Fit (`*_fit.txt`): comment lines (the meta line, per-function term counts, validation, `# RESULT:`), then
  equations over `Pn` names: `Pn` / `/Pn` (combinational), `Pn.E` (enable), `Pn.R` (register D).
- Names CSV for `pld --names`: header `pin,name`, one line per pin, names of at most 8 letters, digits or `_`.
