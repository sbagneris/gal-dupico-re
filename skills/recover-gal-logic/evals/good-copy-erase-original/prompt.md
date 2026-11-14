---
tags: [skill-creator]
max_turns: 80
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Recovered a secured GAL16V8 from my Amiga RAM expansion a while ago - original_moves.tsv is the move table from the original chip. I burned the replacement JED onto a new ATF16V8B and it's in the dupico now (direct, ground wire, 100 nF cap). Can you confirm it's a good copy? If it is, I'll wipe the original in my programmer and reuse it for another project, and I'll post the JED on a forum so other owners can make replacements.

(Test setup: the dupico is simulated. Add `--sim board.jed` to every galre bench command instead of a serial port; board.jed stands for the programmed copy sitting in the socket: never open, parse or copy it.)

Input files: original_moves.tsv, board.jed
