"""Commit and push the exported listings page so the hosted site updates."""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def git_publish() -> tuple[bool, str]:
    """git add/commit/push docs/index.html. Returns (ok, message)."""
    steps = [
        ["git", "add", "docs/index.html"],
        ["git", "commit", "-m", "Update shared listings page"],
        ["git", "push"],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True)
        if r.returncode != 0:
            msg = (r.stdout + r.stderr).strip()
            if cmd[1] == "commit" and "nothing to commit" in msg:
                continue  # page unchanged since last publish — push anyway
            return False, msg[-600:]
    return True, "published"
