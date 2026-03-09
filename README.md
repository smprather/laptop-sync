# laptop-sync

A CLI tool that mirrors files from a Windows source directory to a remote Linux host over SSH. It polls for changes and only copies what's needed — a true mirror that also deletes remote files removed from the source.

## Requirements

- Python 3.14+
- [Astral uv](https://docs.astral.sh/uv/)
- Passwordless SSH to the remote host

## Install

```bash
uv sync
```

## Configuration

Copy and edit `laptop_sync.yaml`:

```yaml
source: "C:\\Projects\\myapp"
host: "user@linuxbox"
dest: "/home/user/mirror"
interval: 5
ssh_port: 22
mtime_tolerance: 2
excludes:
  - ".git"
  - "__pycache__"
  - "*.pyc"
  - "node_modules"
  - ".env"
```

All options except `excludes` can be overridden on the command line.

### Exclude patterns

The `excludes` list uses [fnmatch](https://docs.python.org/3/library/fnmatch.html) syntax. Patterns are matched against both filenames and relative paths, and excluded directories are not descended into (so excluding `.git` skips the entire tree).

## Usage

```bash
# Run with default laptop_sync.yaml
uv run main.py

# Use a different config file
uv run main.py -c my_config.yaml

# Override specific options
uv run main.py --host user@otherbox --interval 10
```

The tool runs in a loop: it checks for local file changes (by modification time and size), copies changed files via `scp -p` (preserving timestamps), deletes remote files no longer in the source, and sleeps for the configured interval. The first iteration always does a full consistency check against the remote.

If the remote host is unreachable (e.g. VPN not yet connected), the tool waits and retries each poll cycle until the host becomes available. SSH connection multiplexing is used to avoid per-file handshake overhead.

Press `Ctrl+C` to stop.
