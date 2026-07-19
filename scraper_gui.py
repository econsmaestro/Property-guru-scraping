#!/usr/bin/env python3
"""Point-and-click window for running the PropertyGuru condo scraper.

No coding needed: tick the districts you care about (or none for all of
Singapore), choose how many pages, and click Start. The scrape runs in
a browser window; results are saved to a CSV you can open with one click.

On Windows, launch by double-clicking Run Scraper.bat.
Elsewhere: python scraper_gui.py
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from districts import DISTRICTS

HERE = Path(__file__).resolve().parent
SCRAPER = HERE / "propertyguru_scraper.py"


class ScraperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.output_file = "listings.csv"

        root.title("PropertyGuru Condo Scraper")
        root.minsize(760, 520)

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        # ---- left: district picker
        left = ttk.LabelFrame(main, text="Districts (leave all unticked = whole of Singapore)")
        left.pack(side="left", fill="y", padx=(0, 10))

        self.district_vars: dict[str, tk.BooleanVar] = {}
        for i, (code, name) in enumerate(DISTRICTS):
            var = tk.BooleanVar(value=False)
            self.district_vars[code] = var
            ttk.Checkbutton(left, text=f"{code}  {name}", variable=var).grid(
                row=i % 14, column=i // 14, sticky="w", padx=6, pady=1
            )
        ttk.Button(left, text="Clear all", command=self.clear_districts).grid(
            row=14, column=0, columnspan=2, pady=(8, 4)
        )

        # ---- right: options, buttons, log
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)

        opts = ttk.Frame(right)
        opts.pack(fill="x")
        ttk.Label(opts, text="Pages to scrape (~20 listings each):").grid(row=0, column=0, sticky="w")
        self.pages_var = tk.IntVar(value=5)
        ttk.Spinbox(opts, from_=1, to=100, width=5, textvariable=self.pages_var).grid(
            row=0, column=1, sticky="w", padx=6
        )
        ttk.Label(opts, text="Save results as:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.output_var = tk.StringVar(value="listings.csv")
        ttk.Entry(opts, width=28, textvariable=self.output_var).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0)
        )

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=8)
        self.start_btn = ttk.Button(btns, text="▶  Start scraping", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■  Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(btns, text="Open results", command=self.open_results, state="disabled")
        self.open_btn.pack(side="left", padx=6)

        ttk.Label(
            right,
            text="A Chrome window will open — if it asks you to verify you are human,\n"
                 "click the checkbox and leave the window open. It closes by itself when done.",
            foreground="#555",
        ).pack(anchor="w")

        self.log = tk.Text(right, height=16, state="disabled", wrap="word",
                           background="#111", foreground="#ddd")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_lines()

    # ------------------------------------------------------------- helpers

    def clear_districts(self):
        for var in self.district_vars.values():
            var.set(False)

    def log_line(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -------------------------------------------------------------- actions

    def start(self):
        if self.proc:
            return
        picked = [code for code, var in self.district_vars.items() if var.get()]
        self.output_file = self.output_var.get().strip() or "listings.csv"

        cmd = [
            sys.executable, "-u", str(SCRAPER),
            "--headful",
            "--max-pages", str(self.pages_var.get()),
            "--output", self.output_file,
        ]
        if picked:
            cmd += ["--districts", ",".join(picked)]

        self.log_line("Starting scrape" + (f" for {', '.join(picked)}" if picked else " (all districts)") + "…")
        try:
            import os
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            self.proc = subprocess.Popen(
                cmd, cwd=str(HERE), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                encoding="utf-8", errors="replace", env=env,
            )
        except OSError as exc:
            messagebox.showerror("Could not start", str(exc))
            self.proc = None
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.open_btn.configure(state="disabled")
        threading.Thread(target=self.read_output, daemon=True).start()

    def read_output(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line)
        self.proc.wait()
        self.lines.put(None)  # sentinel: finished

    def poll_lines(self):
        try:
            while True:
                line = self.lines.get_nowait()
                if line is None:
                    self.finished()
                else:
                    self.log_line(line)
        except queue.Empty:
            pass
        self.root.after(200, self.poll_lines)

    def finished(self):
        code = self.proc.returncode if self.proc else 1
        self.proc = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if code == 0 and (HERE / self.output_file).exists():
            self.open_btn.configure(state="normal")
            self.log_line("Done! Click 'Open results' to view the listings.")
        else:
            self.log_line("Finished without results — see messages above.")

    def stop(self):
        if self.proc:
            self.log_line("Stopping…")
            self.proc.terminate()

    def open_results(self):
        path = HERE / self.output_file
        if sys.platform == "win32":
            import os
            os.startfile(path)  # noqa: S606 - opening the user's own CSV
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def on_close(self):
        if self.proc:
            if not messagebox.askyesno("Scrape running", "A scrape is still running. Stop it and quit?"):
                return
            self.proc.terminate()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    ScraperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
