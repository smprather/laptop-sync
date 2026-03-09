# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- `uv run main.py` — run the mirror tool (requires `laptop_sync.yaml`)
- `uv sync` — install/sync dependencies

## Architecture

Single-file CLI tool (`main.py`) using rich-click. See `doc/architecture.md` for requirements and design constraints.

Key flow: poll loop → compute local snapshot (mtime + size) → on first iteration or local changes, fetch remote snapshot via single `ssh stat` call → diff → `scp -p` changed files / `ssh rm` deleted files → sleep.

Config is loaded from YAML (`laptop_sync.yaml` default), with CLI flags as overrides.

## Workflow

- Keep `README.md` in sync with any changes to configuration, CLI options, usage, or behavior.

## Conventions

- Use `scp` for file transfer and `ssh` for remote commands — no rsync, no SFTP
- Use `shlex.quote()` on all remote paths passed through SSH
- Batch remote operations (mkdir, rm) into single SSH calls to minimize roundtrips
- Compare files by mtime + size, never by content hash
- Preserve modification times on copy (`scp -p`) to prevent update loops
