#!/usr/bin/env python3
"""Local web app for the PropertyGuru condo scraper.

Run it (or double-click "Run Web App.bat" on Windows) and a browser tab
opens at http://127.0.0.1:5000 with everything on one page: pick your
districts, click Start, watch live progress, and the listings appear in
a sortable, filterable table when done.

The scrape itself still opens a Chrome window on this computer — if it
shows a "verify you are human" check, click it and leave it alone.
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

from districts import DISTRICTS

HERE = Path(__file__).resolve().parent
SCRAPER = HERE / "propertyguru_scraper.py"
OUTPUT = "listings.csv"

app = Flask(__name__)

state_lock = threading.Lock()
state = {
    "running": False,
    "log": [],          # list[str]
    "exit_code": None,  # int | None
    "proc": None,       # subprocess.Popen | None
}


# ----------------------------------------------------------------- backend


def run_scrape(districts: list[str], max_pages: int) -> None:
    cmd = [
        sys.executable, "-u", str(SCRAPER),
        "--headful",
        "--max-pages", str(max_pages),
        "--output", OUTPUT,
    ]
    if districts:
        cmd += ["--districts", ",".join(districts)]

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(HERE), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            encoding="utf-8", errors="replace", env=env,
        )
    except OSError as exc:
        with state_lock:
            state["log"].append(f"Could not start scraper: {exc}")
            state["running"] = False
            state["exit_code"] = 1
        return

    with state_lock:
        state["proc"] = proc
    for line in proc.stdout:
        with state_lock:
            state["log"].append(line.rstrip())
    proc.wait()
    with state_lock:
        state["running"] = False
        state["exit_code"] = proc.returncode
        state["proc"] = None
        state["log"].append(
            "Scrape finished." if proc.returncode == 0
            else "Scrape ended without results - see messages above."
        )


@app.post("/start")
def start():
    data = request.get_json(force=True) or {}
    districts = [d for d in data.get("districts", []) if isinstance(d, str)]
    try:
        max_pages = max(1, min(100, int(data.get("max_pages", 5))))
    except (TypeError, ValueError):
        max_pages = 5

    with state_lock:
        if state["running"]:
            return jsonify({"ok": False, "error": "A scrape is already running"}), 409
        state["running"] = True
        state["exit_code"] = None
        state["log"] = ["Starting scrape" +
                        (f" for {', '.join(districts)}" if districts else " (all districts)") +
                        f", {max_pages} page(s)..."]
    threading.Thread(target=run_scrape, args=(districts, max_pages), daemon=True).start()
    return jsonify({"ok": True})


@app.post("/stop")
def stop():
    with state_lock:
        proc = state["proc"]
    if proc:
        proc.terminate()
    return jsonify({"ok": True})


@app.get("/status")
def status():
    with state_lock:
        return jsonify({
            "running": state["running"],
            "log": state["log"][-200:],
            "exit_code": state["exit_code"],
            "have_results": (HERE / OUTPUT).exists(),
        })


@app.get("/files")
def files():
    csvs = sorted(HERE.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify({"files": [p.name for p in csvs]})


@app.get("/results")
def results():
    name = request.args.get("file", OUTPUT)
    # only allow plain .csv filenames that live in the project folder
    if Path(name).name != name or not name.endswith(".csv"):
        return jsonify({"rows": []}), 400
    path = HERE / name
    if not path.exists():
        return jsonify({"rows": []})
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return jsonify({"rows": rows})


@app.post("/quit")
def quit_app():
    with state_lock:
        proc = state["proc"]
    if proc:
        proc.terminate()
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- frontend

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PropertyGuru Condo Scraper</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; background: #f4f5f7; color: #1c2733; }
  header { background: #17323f; color: #fff; padding: 14px 24px;
           display: flex; align-items: baseline; gap: 14px; }
  header h1 { font-size: 20px; margin: 0; }
  header span { opacity: .7; font-size: 13px; }
  main { max-width: 1200px; margin: 20px auto; padding: 0 16px; }
  .panel { background: #fff; border-radius: 10px; padding: 18px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 18px; }
  .districts { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
               gap: 2px 14px; margin: 10px 0; }
  .districts label { font-size: 13px; padding: 3px 2px; cursor: pointer; }
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 10px; }
  button { border: 0; border-radius: 7px; padding: 9px 18px; font-size: 14px;
           cursor: pointer; background: #dde3e8; }
  button.primary { background: #0b7a4b; color: #fff; }
  button.danger { background: #b3352f; color: #fff; }
  button:disabled { opacity: .45; cursor: default; }
  input[type=number] { width: 70px; padding: 7px; border: 1px solid #c6ccd2; border-radius: 6px; }
  input[type=search] { padding: 8px 10px; border: 1px solid #c6ccd2; border-radius: 6px; width: 260px; }
  select { padding: 7px 9px; border: 1px solid #c6ccd2; border-radius: 6px; background: #fff; }
  .filters { background: #f0f4f7; border-radius: 8px; padding: 10px 12px; }
  .filters label { font-size: 13px; display: flex; align-items: center; gap: 5px; }
  .filters input[type=number] { width: 95px; padding: 6px 8px;
    border: 1px solid #c6ccd2; border-radius: 6px; }
  #log { background: #10161c; color: #cfe3d8; font: 12px/1.5 ui-monospace, monospace;
         border-radius: 8px; padding: 12px; height: 150px; overflow-y: auto;
         white-space: pre-wrap; display: none; }
  .hint { color: #5a6a76; font-size: 13px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid #e6e9ec; }
  th { cursor: pointer; user-select: none; background: #f0f2f4; position: sticky; top: 0; }
  th .arrow { opacity: .5; font-size: 11px; }
  tr:hover td { background: #f6f9fb; }
  td a { color: #0b6aa4; }
  .tablewrap { overflow-x: auto; max-height: 65vh; overflow-y: auto; }
  #count { font-weight: 600; }
  footer { text-align: right; padding: 6px 24px 20px; }
  footer button { font-size: 12px; padding: 6px 12px; }
</style>
</head>
<body>
<header><h1>PropertyGuru Condo Scraper</h1><span>family house-hunting edition</span></header>
<main>
  <div class="panel">
    <strong>1 &mdash; Choose districts</strong>
    <span class="hint">(leave all unticked to search the whole of Singapore)</span>
    <div class="districts" id="districts"></div>
    <div class="controls">
      <strong>2 &mdash;</strong>
      <label>Pages <input type="number" id="pages" value="5" min="1" max="100"></label>
      <span class="hint">(~20 listings per page)</span>
      <button class="primary" id="startBtn" onclick="startScrape()">Start scraping</button>
      <button class="danger" id="stopBtn" onclick="stopScrape()" disabled>Stop</button>
      <span class="hint" id="runhint"></span>
    </div>
    <p class="hint">A Chrome window opens on this computer while scraping. If it asks you to
       verify you are human, click the checkbox and leave the window alone.</p>
    <div id="log"></div>
  </div>

  <div class="panel">
    <div class="controls" style="margin:0 0 10px">
      <strong>3 &mdash; Results</strong>
      <select id="csvfile" onchange="loadResults()" title="Which saved scrape to show"></select>
      <span class="hint"><span id="count">0</span> listings &mdash; click a column heading to sort</span>
      <input type="search" id="filter" placeholder="Search: e.g. Katong, Meyer..."
             oninput="renderTable()">
    </div>
    <div class="controls filters" style="margin:0 0 10px">
      <label>Price S$
        <input type="number" id="fPriceMin" placeholder="min" min="0" oninput="renderTable()">
        &ndash;
        <input type="number" id="fPriceMax" placeholder="max" min="0" oninput="renderTable()">
      </label>
      <label>Beds
        <select id="fBeds" onchange="renderTable()">
          <option value="">Any</option><option value="1">1+</option>
          <option value="2">2+</option><option value="3">3+</option>
          <option value="4">4+</option><option value="5">5+</option>
        </select>
      </label>
      <label>Baths
        <select id="fBaths" onchange="renderTable()">
          <option value="">Any</option><option value="1">1+</option>
          <option value="2">2+</option><option value="3">3+</option>
          <option value="4">4+</option><option value="5">5+</option>
        </select>
      </label>
      <label>Sqft
        <input type="number" id="fSqftMin" placeholder="min" min="0" oninput="renderTable()">
        &ndash;
        <input type="number" id="fSqftMax" placeholder="max" min="0" oninput="renderTable()">
      </label>
      <label>Tenure
        <select id="fTenure" onchange="renderTable()"><option value="">Any</option></select>
      </label>
      <button onclick="clearFilters()">Clear filters</button>
    </div>
    <div class="tablewrap"><table id="table"></table></div>
  </div>
</main>
<footer><button onclick="quitApp()">Shut down app</button></footer>

<script>
const DISTRICTS = {{ districts | tojson }};
const COLUMNS = [
  ["title", "Project"], ["asking_price_sgd", "Price (S$)"], ["bedrooms", "Beds"],
  ["bathrooms", "Baths"], ["area_sqft", "Sqft"], ["price_psf", "S$/sqft"],
  ["tenure", "Tenure"], ["mrt_proximity", "MRT"], ["location", "Location"],
];
const NUMERIC = new Set(["asking_price_sgd", "bedrooms", "bathrooms", "area_sqft", "price_psf"]);
let rows = [], sortKey = "asking_price_sgd", sortAsc = true, polling = false;

const $ = id => document.getElementById(id);

DISTRICTS.forEach(([code, name]) => {
  $("districts").insertAdjacentHTML("beforeend",
    `<label><input type="checkbox" value="${code}"> ${code} &nbsp;${name}</label>`);
});

async function startScrape() {
  const picked = [...document.querySelectorAll("#districts input:checked")].map(c => c.value);
  const res = await fetch("/start", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({districts: picked, max_pages: +$("pages").value || 5})});
  if (!res.ok) { alert((await res.json()).error || "Could not start"); return; }
  setRunning(true);
  if (!polling) poll();
}

async function stopScrape() { await fetch("/stop", {method: "POST"}); }

async function quitApp() {
  if (!confirm("Shut down the scraper app? (You can restart it any time.)")) return;
  await fetch("/quit", {method: "POST"});
  document.body.innerHTML = "<main><div class='panel'>App shut down. You can close this tab.</div></main>";
}

function setRunning(on) {
  $("startBtn").disabled = on;
  $("stopBtn").disabled = !on;
  $("runhint").textContent = on ? "Scraping…" : "";
  $("log").style.display = "block";
}

async function poll() {
  polling = true;
  try {
    const s = await (await fetch("/status")).json();
    $("log").textContent = s.log.join("\\n");
    $("log").scrollTop = $("log").scrollHeight;
    if (s.running) { setTimeout(poll, 1500); return; }
    polling = false;
    setRunning(false);
    if (s.have_results) loadFiles("listings.csv").then(loadResults);
  } catch (e) { polling = false; setRunning(false); }
}

async function loadFiles(prefer) {
  const files = (await (await fetch("/files")).json()).files;
  const sel = $("csvfile");
  const keep = prefer || sel.value;
  sel.innerHTML = files.map(f => `<option value="${f}">${f}</option>`).join("");
  if (keep && files.includes(keep)) sel.value = keep;
  return files;
}

async function loadResults() {
  const f = $("csvfile").value;
  if (!f) { rows = []; renderTable(); return; }
  rows = (await (await fetch("/results?file=" + encodeURIComponent(f))).json()).rows;
  // rebuild the tenure dropdown from the tenures present in this data
  const sel = $("fTenure"), keep = sel.value;
  const tenures = [...new Set(rows.map(r => r.tenure).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">Any</option>' +
    tenures.map(t => `<option>${t}</option>`).join("");
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  renderTable();
}

const num = v => parseFloat(String(v).replace(/[^0-9.]/g, "")) || 0;
const bedCount = r => /studio/i.test(r.bedrooms || "") ? 0 : num(r.bedrooms);

function clearFilters() {
  for (const id of ["filter", "fPriceMin", "fPriceMax", "fSqftMin", "fSqftMax"]) $(id).value = "";
  for (const id of ["fBeds", "fBaths", "fTenure"]) $(id).value = "";
  renderTable();
}

function renderTable() {
  const q = $("filter").value.toLowerCase();
  const pMin = +$("fPriceMin").value || 0, pMax = +$("fPriceMax").value || Infinity;
  const sMin = +$("fSqftMin").value || 0, sMax = +$("fSqftMax").value || Infinity;
  const minBeds = +$("fBeds").value || 0, minBaths = +$("fBaths").value || 0;
  const tenure = $("fTenure").value;
  let shown = rows.filter(r => {
    const price = num(r.asking_price_sgd), sqft = num(r.area_sqft);
    return (!q || Object.values(r).join(" ").toLowerCase().includes(q))
      && price >= pMin && price <= pMax
      && sqft >= sMin && sqft <= sMax
      && bedCount(r) >= minBeds
      && num(r.bathrooms) >= minBaths
      && (!tenure || (r.tenure || "") === tenure);
  });
  shown.sort((a, b) => {
    const [x, y] = [a[sortKey] || "", b[sortKey] || ""];
    const cmp = NUMERIC.has(sortKey) ? num(x) - num(y) : String(x).localeCompare(String(y));
    return sortAsc ? cmp : -cmp;
  });
  $("count").textContent = shown.length;
  const head = "<tr>" + COLUMNS.map(([k, label]) =>
    `<th onclick="sortBy('${k}')">${label} <span class="arrow">${k === sortKey ? (sortAsc ? "▲" : "▼") : ""}</span></th>`
  ).join("") + "</tr>";
  const body = shown.map(r => "<tr>" + COLUMNS.map(([k]) => {
    let v = r[k] || "";
    if (k === "title" && r.url) v = `<a href="${r.url}" target="_blank" rel="noopener">${v || "view listing"}</a>`;
    return `<td>${v}</td>`;
  }).join("") + "</tr>").join("");
  $("table").innerHTML = head + body;
}

function sortBy(k) {
  if (sortKey === k) sortAsc = !sortAsc; else { sortKey = k; sortAsc = true; }
  renderTable();
}

// on load: list saved scrapes (newest first), show one, resume polling if running
loadFiles().then(loadResults);
fetch("/status").then(r => r.json()).then(s => {
  if (s.running) { setRunning(true); poll(); }
});
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE, districts=DISTRICTS)


def main():
    port = int(os.environ.get("PORT", "5000"))
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"PropertyGuru scraper web app running at http://127.0.0.1:{port}")
    print("Keep this window open while using it (or use the app's Shut down button).")
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
