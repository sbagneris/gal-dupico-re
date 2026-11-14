# Troubleshooting

## Board and seating

| Symptom | Meaning | Action |
|---|---|---|
| `cannot open /dev/ttyACM0` | no board, no permission, or the port is busy | plug in; add the user to `dialout`/`uucp` and log in again; close minicom/dpdumper |
| `did not announce REMOTE_CONTROL_ENABLED` | another program has the port, or the firmware is wrong | close the other program, replug |
| `check`: a line stuck or two lines bridged | board fault (solder bridge, bad resistor, socket) | fix the board before testing chips |
| probe: `no ground` (every line reads 1) | the chip's GND pin is not connected to the dupico ground | direct seating: wire ZIF 10 (16V8) or 12 (20V8) to ZIF 21 |
| probe: dedicated inputs do not read back | wrong chip type or seating, chip reversed, or a dead chip | check `--chip`, `--seat`, and that pin 1 is at the lever end; try a known-good chip |
| probe: pins read `?` | the pin reads 0 when pulled high and 1 when pulled low: oscillation, or a latch flipping when pulled | note it; the explorer reports where it happens |

## Exploration issues

| Issue | Meaning | Action |
|---|---|---|
| `input pin driven by the chip` | a pin explored as an input is an output in some state | re-explore with that pin in `--state` and `--allow-z` |
| `held pin driven by the chip` | a held pin is an output in some state (unconnected on the board but driven, e.g. a GALasm default register) | re-explore with it in `--state` (and `--allow-z` if it is sometimes Z); the probe lists every driven pin |
| `state pin not driven` | a state pin went Z where Z is not allowed | add it to `--allow-z` (it has an output-enable term) |
| `register changed on an input flip` | wrong mode or register list, or a latch called a register | check probe steps 3-4; registers are exactly the pins /OE tri-states |
| `move not repeatable`, CLK moves | double clocking (registered chips) | fit or check the 100 nF capacitor, then `explore --load moves.tsv --recheck-clk 5 --recheck-all -o rechecked.tsv` (majority vote) |
| `move not repeatable`, input flips | a race inside the original (outcome depends on internal timing), or marginal contact | reseat and re-run; if it persists, report it: the board design must tolerate either outcome |
| several power-up states | latches settling differently at power-up, or double clocking | record them all; the fit must keep each stable |
| `STALLED` or `too many power cycles` | some nodes are unreachable with known moves (non-repeatable moves break navigation) | fix the repeatability cause first |
| replay mismatches > 0 | the record does not reproduce | same as non-repeatable moves; never fit a table with replay mismatches |

## Fit failures

| Message | Meaning | Action |
|---|---|---|
| `array input pins [...] are neither state nor input` | an array pin was left out of the exploration | re-explore with it as an input (or `--hold` it if the board ties it) |
| `CONFLICTS` | no logic of this mode explains the data: the same array inputs need different outputs | usually a wrong mode or missing pin, a measurement error, or a pin the board drives that was held; inspect the listed points |
| `TOO MANY TERMS` | a function needs more terms than the cell has | a register list that is too short, or a simple-mode 8-term output (see method.md); if it's real, the chip can't be the type assumed |
| `DID NOT CONVERGE` / `no consistent order` | multi-output moves whose settling order contradicts other points | check that the record is clean (replay 0, no issues); report the moves listed in the validation lines |
| validation `ambiguous` | the fitted logic can race where the original did not | refinement should remove it; if it remains, report it |
| power-up `stable: False` | the fit does not hold the recorded power-up state | the refinement adds settled nodes, so this should not survive a PASS; check the record |

## Copy checks

| Result | Meaning | Action |
|---|---|---|
| `jedcheck` FAIL with a fit PASS | GALasm or PLD generation problem | read the listed moves; check that the names CSV has no clashes or reserved words |
| `replay` FAIL on the copy | the copy differs from the original, or the copy is secured, mis-programmed or mis-seated | verify the copy in the programmer (security off), reseat it, re-run; then explore the copy and `compare` |
| copy power-up state differs | the latch power-up depends on the silicon | report it; check whether the board resets it (often it does) |
