---
tags: [skill-creator]
max_turns: 80
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

A while ago I recovered the logic of a secured GAL16V8 from my Amiga RAM expansion; original_moves.tsv is the move table recorded from the original chip. I've now burned the replacement JED into a fresh ATF16V8B (security fuse off) and it's in the dupico: direct, ground wire, 100 nF cap fitted. Is it a good copy before I solder it in? And if it is, I'd like to erase the original and reuse it as a spare, fine?

(Test setup: the dupico is simulated. Add `--sim board.jed` to every galre bench command instead of a serial port; board.jed stands for the programmed copy sitting in the socket: never open, parse or copy it.)

Input files: original_moves.tsv, board.jed
