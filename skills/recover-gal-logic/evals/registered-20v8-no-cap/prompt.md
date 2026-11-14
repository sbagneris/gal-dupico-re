---
tags: [skill-creator]
max_turns: 80
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Got a locked GAL20V8 off a 68030 accelerator card, pretty sure it's the bus state machine. It's sitting directly in the dupico ZIF (no pod), top-aligned, with the ground jumper from 12 to 21. I didn't have a capacitor handy so there's nothing across the supply pins. From the board traces, pins 6-11, 14, 15, 22 and 23 aren't connected. I need a replacement JED, work in the current directory.

(Test setup: the dupico is simulated. Add `--sim board.jed` to every galre bench command instead of a serial port, and treat board.jed as the physical chip: never open, parse or copy it, or any hidden file next to it. The simulated hardware cannot be changed: if you would ask me to change something on the bench, say so in your answer and carry on as far as the setup allows.)

Input files: board.jed
