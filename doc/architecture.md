## Requirements

- Mirror files from a Windows source directory (recursive) to a Linux destination directory.
- Only use `scp` to copy data, and `ssh` to execute commands on the Linux host.
- Only copy files when the contents of the source directory change, but the first iteration of the
  polling loop must check for consistency.
- Only copy the files that need to be copied.
- This is a mirror operation — if a file in the source is deleted, it must be deleted at the
  destination.
- Assume passwordless SSH is already set up.
- Use a YAML file for configuration. CLI flags override the YAML file. Default config file is
  `laptop_sync.yaml`.
- Compare files by modification date and file size. Preserve modification times when copying to
  the destination to prevent an update loop.

## Tech Stack

- Python 3.14
- Astral uv
- Python packages:
  - rich-click
  - pyyaml
