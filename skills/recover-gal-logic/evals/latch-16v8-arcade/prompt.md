---
tags: [skill-creator]
max_turns: 80
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

I pulled a secured GAL16V8 (U14) off my arcade PCB - my T48 reads it back as protected, all zeros. It's in my DuPAL dupico now, straight in the ZIF, top-aligned, with the dupont ground wire to pin 21 and a 100 nF cap across pins 20 and 10. I buzzed out the board: pins 8, 9, 11, 12, 13 and 14 aren't connected to anything. Can you get me a JED I can burn onto an ATF16V8B as a replacement? Keep all the files in the current directory.

(Test setup: the dupico is simulated. Add `--sim board.jed` to every galre bench command instead of a serial port, and treat board.jed as the physical chip: never open, parse or copy it, or any hidden file next to it.)

Input files: board.jed
