from __future__ import annotations

import posixpath
import shlex
import subprocess
import time
from pathlib import Path

import rich_click as click
import yaml
from rich.console import Console

console = Console()

DEFAULT_CONFIG = "laptop_sync.yaml"


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def compute_local_snapshot(source: Path) -> dict[str, tuple[float, int]]:
    """Walk the source directory and return {relative_posix_path: (mtime, size)}."""
    snapshot = {}
    for file_path in sorted(source.rglob("*")):
        if file_path.is_file():
            rel = file_path.relative_to(source).as_posix()
            stat = file_path.stat()
            snapshot[rel] = (stat.st_mtime, stat.st_size)
    return snapshot


def compute_remote_snapshot(host: str, dest: str, ssh_port: int) -> dict[str, tuple[float, int]]:
    """SSH into the remote host and stat all files under dest.

    Uses find + stat to get modification time (epoch) and size for each file.
    """
    cmd = [
        "ssh", "-p", str(ssh_port), host,
        f"find {shlex.quote(dest)} -type f -exec stat --format='%Y %s %n' {{}} \\;",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    snapshot = {}
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        mtime_str, size_str, abs_path = parts
        if abs_path.startswith(dest):
            rel = abs_path[len(dest):].lstrip("/")
        else:
            rel = abs_path
        snapshot[rel] = (float(mtime_str), int(size_str))
    return snapshot


def compute_diff(
    local: dict[str, tuple[float, int]],
    remote: dict[str, tuple[float, int]],
    mtime_tolerance: float = 2,
) -> tuple[list[str], list[str]]:
    """Return (files_to_copy, files_to_delete) based on mtime and size."""
    to_copy = []
    for rel, (l_mtime, l_size) in local.items():
        if rel not in remote:
            to_copy.append(rel)
        else:
            r_mtime, r_size = remote[rel]
            if l_size != r_size or abs(l_mtime - r_mtime) > mtime_tolerance:
                to_copy.append(rel)
    to_delete = [rel for rel in remote if rel not in local]
    return to_copy, to_delete


def copy_files(
    source: Path, host: str, dest: str, ssh_port: int, files: list[str]
) -> None:
    """Create remote directories and scp each changed file, preserving mtime."""
    if not files:
        return

    # Batch-create all needed remote directories in one SSH call
    dirs = {posixpath.dirname(f) for f in files if posixpath.dirname(f)}
    if dirs:
        mkdir_cmd = " && ".join(
            f"mkdir -p {shlex.quote(posixpath.join(dest, d))}" for d in sorted(dirs)
        )
        subprocess.run(["ssh", "-p", str(ssh_port), host, mkdir_cmd], check=True)

    for rel in files:
        local_path = source / rel
        remote_path = f"{host}:{shlex.quote(posixpath.join(dest, rel))}"
        console.print(f"  [cyan]copying[/cyan] {rel}")
        # -p flag preserves modification times
        subprocess.run(
            ["scp", "-p", "-P", str(ssh_port), str(local_path), remote_path],
            check=True,
        )


def delete_remote_files(
    host: str, dest: str, ssh_port: int, files: list[str]
) -> None:
    """Remove files from remote and clean up empty directories."""
    if not files:
        return

    batch_size = 100
    for i in range(0, len(files), batch_size):
        batch = files[i : i + batch_size]
        rm_cmd = " && ".join(
            f"rm -f {shlex.quote(posixpath.join(dest, f))}" for f in batch
        )
        subprocess.run(["ssh", "-p", str(ssh_port), host, rm_cmd], check=True)
        for f in batch:
            console.print(f"  [red]deleted[/red] {f}")

    # Clean up empty directories
    subprocess.run(
        [
            "ssh", "-p", str(ssh_port), host,
            f"find {shlex.quote(dest)} -type d -empty -delete",
        ],
        check=True,
    )


@click.command()
@click.option(
    "-c", "--config",
    default=DEFAULT_CONFIG,
    type=click.Path(exists=True),
    help="Path to YAML config file.",
)
@click.option("--source", default=None, help="Override source directory.")
@click.option("--host", default=None, help="Override remote host.")
@click.option("--dest", default=None, help="Override remote destination directory.")
@click.option("--interval", default=None, type=int, help="Override poll interval (seconds).")
@click.option("--ssh-port", default=None, type=int, help="Override SSH port.")
@click.option("--mtime-tolerance", default=None, type=float, help="Override mtime tolerance (seconds).")
def mirror(
    config: str,
    source: str | None,
    host: str | None,
    dest: str | None,
    interval: int | None,
    ssh_port: int | None,
    mtime_tolerance: float | None,
) -> None:
    """Mirror files from a local directory to a remote Linux host."""
    cfg = load_config(config)

    # CLI flags override config file values
    source_dir = source or cfg["source"]
    remote_host = host or cfg["host"]
    remote_dest = dest or cfg["dest"]
    poll_interval = interval if interval is not None else cfg.get("interval", 5)
    port = ssh_port if ssh_port is not None else cfg.get("ssh_port", 22)
    tolerance = mtime_tolerance if mtime_tolerance is not None else cfg.get("mtime_tolerance", 2)

    source_path = Path(source_dir)
    if not source_path.is_dir():
        raise click.BadParameter(f"Source directory does not exist: {source_dir}")

    console.print(f"[bold]Mirroring[/bold] {source_dir} -> {remote_host}:{remote_dest}")
    console.print(f"[dim]Poll interval: {poll_interval}s | SSH port: {port}[/dim]")

    # Ensure remote base directory exists
    subprocess.run(
        ["ssh", "-p", str(port), remote_host, f"mkdir -p {shlex.quote(remote_dest)}"],
        check=True,
    )

    previous_local_snapshot: dict[str, tuple[float, int]] | None = None

    try:
        while True:
            local_snapshot = compute_local_snapshot(source_path)

            if previous_local_snapshot is None:
                # First iteration: always check consistency with remote
                console.print("\n[bold]First sync: checking consistency with remote...[/bold]")
                remote_snapshot = compute_remote_snapshot(remote_host, remote_dest, port)
                to_copy, to_delete = compute_diff(local_snapshot, remote_snapshot, tolerance)
            else:
                if local_snapshot == previous_local_snapshot:
                    time.sleep(poll_interval)
                    continue

                console.print("\n[bold yellow]Changes detected, syncing...[/bold yellow]")
                remote_snapshot = compute_remote_snapshot(remote_host, remote_dest, port)
                to_copy, to_delete = compute_diff(local_snapshot, remote_snapshot, tolerance)

            if to_copy:
                console.print(f"[cyan]Copying {len(to_copy)} file(s)[/cyan]")
                copy_files(source_path, remote_host, remote_dest, port, to_copy)

            if to_delete:
                console.print(f"[red]Deleting {len(to_delete)} file(s)[/red]")
                delete_remote_files(remote_host, remote_dest, port, to_delete)

            if not to_copy and not to_delete:
                console.print("[green]Already in sync.[/green]")
            else:
                console.print(
                    f"[green]Synced:[/green] {len(to_copy)} copied, {len(to_delete)} deleted"
                )

            previous_local_snapshot = local_snapshot
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        console.print("\n[bold]Stopped.[/bold]")


if __name__ == "__main__":
    mirror()
