# RAM Optimizer

A lightweight Windows desktop app that frees up RAM without closing your programs — giving games and heavy apps more breathing room.

## Features

- **Trim All** — frees unused RAM from every running process instantly
- **Process List** — see every app's RAM usage and trim individually
- **Game Mode** — clears all background apps before you launch a game
- **Auto Watch** — automatically trims a program when it exceeds a RAM limit you set
- No install required — single `.exe`, works on any Windows 10/11 PC

## Download

Head to the [Releases](../../releases) page to download the latest version.

## How it works

RAM Optimizer uses Windows' `EmptyWorkingSet` API to move a process's idle memory pages to the standby list. The pages are freed from physical RAM but can be instantly reclaimed by the process if needed — so nothing crashes or slows down.

---

Built with Python + CustomTkinter.
