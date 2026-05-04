# AGENTS.md — Agent Cold-Start Reference

> **Maintenance rule:** Update this file before every commit that touches `main.py`, `README.md`, `laptop_sync.yaml`, `pyproject.toml`, or `doc/`.

## Project in one sentence

`laptop-sync` polls for file changes and mirrors them between a Windows local directory and a remote Linux host over plain SSH/SCP, supporting independent push and pull schedules.

---

## Repository layout

```
main.py               — entire application (single-file CLI, ~720 lines)
laptop_sync.yaml      — example / default config (user edits this)
pyproject.toml        — build metadata; entry point: laptop_sync = "main:main"
README.md             — user-facing docs (install, config, usage)
CLAUDE.md             — coding agent instructions (conventions, workflow)
AGENTS.md             — this file (agent cold-start reference)
doc/architecture.md   — original requirements + tech-stack constraints
uv.lock               — locked dependency tree (managed by uv)
.python-version       — pinned Python version for uv
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.14+ |
| Package manager | [Astral uv](https://docs.astral.sh/uv/) |
| CLI framework | `rich-click` (Click + Rich formatting) |
| Config format | YAML via `pyyaml` |
| File transfer | `scp -p` (preserves mtime) |
| Remote commands | `ssh` (batched where possible) |
| SSH multiplexing | `ControlMaster/ControlPersist` on Unix; **disabled on Windows** (no Unix sockets) |

---

## Code map (`main.py`)

All logic lives in one file. Top-level symbols in order:

| Symbol | Kind | Purpose |
|---|---|---|
| `console` | `Console` | Rich console instance (global) |
| `DEFAULT_CONFIG` | constant | `"laptop_sync.yaml"` |
| `_CONTROL_PATH` | constant | SSH ControlPath template |
| `_CAN_MULTIPLEX` | constant | `True` on Unix, `False` on Windows |
| `_verbose` | bool | Set by `--verbose`; gated by `debug()` |
| `_WIN_CMDLINE_LIMIT` | constant | `30_000` — safe ceiling for Windows `CreateProcess` (hard limit 32,767) |
| `_scp_batches(fixed, file_args, dest)` | fn | Splits file_args into command-line-length-safe batches; single batch on non-Windows |
| `_ssh_opts(port)` | fn | `["-p", port] + _multiplex_opts()` |
| `_scp_opts(port)` | fn | `["-P", port] + _multiplex_opts()` |
| `_scp_remote_path(host, path)` | fn | Builds `host:path`; shell-quotes path on Unix only (Windows SFTP mode needs unquoted) |
| `load_config(path)` | fn | `yaml.safe_load` from file |
| `_get_config_bool(cfg, key)` | fn | Raises `UsageError` if key missing or non-bool |
| `check_host_reachable(host, port)` | fn | `ssh … true` with 5s ConnectTimeout; returns bool |
| `compute_local_snapshot(source, excludes)` | fn | `os.walk` → `{rel_posix_path: (mtime, size)}`; prunes excluded dirs in-place |
| `compute_remote_snapshot(host, dest, port, excludes)` | fn | Single `ssh find -L … -printf '%T@ %s %p\n'` → same dict format |
| `compute_diff(source, dest, mtime_tolerance)` | fn | Returns `(to_copy, to_delete)`; uses mtime + size, never content hash |
| `copy_files(source, host, dest, port, files)` | fn | Groups files by remote subdir; batched `ssh mkdir -p` (100/batch) then one `scp -p` call per subdir with all files |
| `delete_remote_files(host, dest, port, files)` | fn | Batched `ssh rm -f` (100/batch) then `find … -empty -delete` |
| `pull_files(host, remote_source, port, local_dest, files)` | fn | Groups files by remote subdir; creates local dirs then one `scp -p` call per subdir with all files |
| `delete_local_files(local_dest, files)` | fn | `Path.unlink()` then bottom-up `rmdir` of empty dirs |
| `mirror(...)` | Click command | Main entry: loads config, merges CLI overrides, runs push+pull loop |
| `main()` | fn | Thin wrapper calling `mirror()` (entry point) |

---

## Configuration schema (`laptop_sync.yaml`)

```yaml
host: "user@linuxbox"       # required; SSH destination

push_enable: true            # required bool
pull_enable: true            # required bool

push_source: "C:\\path"      # required if push_enable
push_dest: "/remote/path"    # required if push_enable
pull_source: "/remote/path"  # required if pull_enable
pull_dest: "C:\\path"        # required if pull_enable; may be a symlink to dir

interval: 5                  # default poll seconds (fallback for both directions)
push_interval: 5             # overrides interval for push (optional)
pull_interval: 30            # overrides interval for pull (optional)
ssh_port: 22                 # default 22
mtime_tolerance: 2           # seconds; files within tolerance are considered unchanged
no_delete: false             # when true, never delete from destination

excludes:                    # fnmatch patterns; matched against filename AND rel path
  - ".git"
  - "__pycache__"
  - "*.pyc"
```

---

## CLI flags (all override YAML)

```
-c / --config         YAML config path (default: laptop_sync.yaml)
--host                remote host
--push-source         local push source directory
--push-dest           remote push destination
--push-enable / --no-push-enable
--pull-source         remote pull source
--pull-dest           local pull destination
--pull-enable / --no-pull-enable
--interval            default poll interval (seconds)
--push-interval       push-specific interval
--pull-interval       pull-specific interval
--ssh-port            SSH port
--mtime-tolerance     mtime tolerance (seconds)
--once                run one sync cycle and exit
--no-delete           never delete from destination (copy-only mode)
-v / --verbose        enable [DEBUG] output
```

---

## Data flow

### Push (local → remote)
```
compute_local_snapshot(push_source)
  ├─ first cycle OR snapshot changed?
  │    └─ compute_remote_snapshot(host, push_dest)
  │         └─ compute_diff(local, remote)
  │              ├─ copy_files(...)     ← scp -p per file
  │              └─ delete_remote_files(...)  ← batched ssh rm + find -empty -delete
  └─ no change → skip
```

### Pull (remote → local)
```
compute_remote_snapshot(host, pull_source)
  ├─ first cycle OR snapshot changed?
  │    └─ compute_local_snapshot(pull_dest)
  │         └─ compute_diff(remote, local)
  │              ├─ pull_files(...)     ← scp -p per file
  │              └─ delete_local_files(...)
  └─ no change → skip
```

Push and pull run on **independent timers** (`next_push_at`, `next_pull_at`). The main loop sleeps until the earlier of the two.

---

## Why SCP instead of rsync

rsync is blocked at the DPI (Deep Packet Inspection) level in the target work environment. SCP traffic is not blocked (it rides plain SSH/SFTP and passes DPI filters that flag rsync's protocol signature). This tool therefore **emulates rsync's core behavior** — snapshot both sides, diff by mtime+size, copy only changed files, delete orphans — using only `ssh` and `scp`. **Do not add rsync, rsyncd, or any rsync-over-SSH calls.** The SCP-only constraint is a hard network requirement, not a preference.

---



- **No rsync, no SFTP** — only `scp` for transfers, `ssh` for remote commands.
- **`shlex.quote()` on all remote paths in SSH commands** (not in SCP `host:path` on Windows).
- **Compare by mtime + size only** — never read file contents for diffing.
- **Windows `CreateProcess` limit**: command lines are capped at 32,767 chars on Windows. `_scp_batches()` splits large file sets into length-safe chunks; on Unix everything goes in one call.
- **`scp -p`** — always preserve mtime to prevent update loops.
- **Batch SSH calls** — `mkdir -p` and `rm -f` in groups of 100 to minimize roundtrips.
- **SSH multiplexing** — enabled on Unix via `ControlMaster`; must remain disabled on Windows.
- **Resilience** — catch `CalledProcessError` inside the loop; check reachability each cycle.

---

## Dev commands

```bash
uv sync                  # install / sync dependencies
uv run main.py           # run with default laptop_sync.yaml
uv run main.py -v --once # debug: single cycle with verbose output
```

No linter or test runner is currently configured.

### Pre-commit hook

A hook at `.hooks/pre-commit` blocks commits where source files change without `AGENTS.md` being staged. Install once per clone:

```bash
cp .hooks/pre-commit .git/hooks/pre-commit
# On Unix: chmod +x .git/hooks/pre-commit
```

---

## What to keep in sync

| When you change… | Also update… |
|---|---|
| CLI flags or config keys | `README.md` (Configuration + Usage sections), `AGENTS.md` (CLI flags + schema tables) |
| Core algorithm or functions | `AGENTS.md` (Code map, Data flow) |
| Architecture/constraints | `doc/architecture.md`, `AGENTS.md` (Design constraints) |
| Dependencies | `pyproject.toml` + run `uv sync`; update `AGENTS.md` tech stack if relevant |
| Install / usage behavior | `README.md`, `AGENTS.md` |
