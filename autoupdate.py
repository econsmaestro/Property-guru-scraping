#!/usr/bin/env python3
"""Scrape, export, and publish in one go — the full pipeline, hands-free.

Run it manually whenever you want the live site refreshed:

    python autoupdate.py --max-pages 10 --districts D15,D19

Or schedule it (e.g. daily at 8am) so the family always sees fresh
listings. Windows Task Scheduler action, with "Start in" set to this
project folder:

    Program:   C:\\path\\to\\Property-guru-scraping\\.venv\\Scripts\\python.exe
    Arguments: autoupdate.py --max-pages 10 --districts D15,D19 --headful

Notes:
- --headful shows the browser; keep it for scheduled runs on a PC where
  someone can click the "verify you are human" box if it ever appears.
  After one solved check the browser profile usually stays trusted, so
  most runs sail through untouched.
- If the scrape is blocked or fails, nothing is published — the live
  site keeps showing the previous good data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from export_static import export_page
from publish import git_publish

HERE = Path(__file__).resolve().parent
OUTPUT = "listings.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape, export, and publish the live site")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--districts", default="")
    parser.add_argument("--headful", action="store_true",
                        help="show the browser window (recommended)")
    args = parser.parse_args()

    cmd = [sys.executable, "-u", str(HERE / "propertyguru_scraper.py"),
           "--max-pages", str(args.max_pages), "--output", OUTPUT]
    if args.districts:
        cmd += ["--districts", args.districts]
    if args.headful:
        cmd += ["--headful"]

    print("Step 1/3: scraping...")
    if subprocess.run(cmd, cwd=str(HERE)).returncode != 0:
        print("Scrape failed - keeping the previous published page.")
        return 1

    print("Step 2/3: exporting page...")
    count = export_page(HERE / OUTPUT, HERE / "docs" / "index.html")
    print(f"Exported {count} listings.")

    print("Step 3/3: publishing (git push)...")
    ok, msg = git_publish()
    if not ok:
        print(f"Publish failed: {msg}")
        return 1
    print("Done - the live site updates in about a minute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
