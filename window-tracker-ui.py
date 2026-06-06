#!/usr/bin/env python3
"""
Window Tracker UI — local web UI for visualizing the focus log.

Usage:
    python3 window-tracker-ui.py [--port 8765] [--log PATH] [--host 127.0.0.1]

Then open http://localhost:8765 in your browser.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DEFAULT_LOG = Path.home() / ".local/share/window-tracker/focus.log"
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"

_logo_bytes = None


def load_logo() -> bytes:
    """Read the logo once and cache it in memory; empty bytes if missing."""
    global _logo_bytes
    if _logo_bytes is None:
        try:
            _logo_bytes = LOGO_PATH.read_bytes()
        except OSError:
            _logo_bytes = b""
    return _logo_bytes

OLD_FORMAT_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] FOCUS_END\s*\| "
    r"App: (.+?) \| Window: (.+?) \| Duration: (\d+)s\s*$"
)
TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
DURATION_RE = re.compile(r"^(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?$")


def parse_duration(s: str) -> int:
    s = (s or "").strip()
    m = DURATION_RE.match(s)
    if not m or not s:
        return 0
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(se or 0)


def parse_time(s: str) -> int:
    h, m, se = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(se)


def parse_log(path: Path):
    """
    Returns list of entries:
      {date, start_sec, end_sec, status, app, window, duration_sec}

    Handles old single-line FOCUS_END format and new multi-line block format.

    Date inference for new-format (time-only) entries:
      1. Forward pass: advance date by 1 whenever start_sec jumps backward.
         (Catches the common case where the tracker keeps running past midnight.)
      2. Anchor to file mtime: the last entry's date should equal mtime's date.
         If the forward pass ends N days short, distribute N day-rollovers
         across the N longest "silent gaps" — gaps where the tracker was
         off (no backward jump fired) but a long pause followed. This
         catches cases where the tracker was stopped overnight.
    """
    entries = []
    current_date = None
    last_end_sec = -1
    silent_gaps = []  # (entry_index_after_gap, gap_seconds) where heuristic did NOT fire

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    n = len(lines)
    i = 0
    while i < n:
        line = lines[i].rstrip("\n")

        m = OLD_FORMAT_RE.match(line)
        if m:
            d_str, end_t, app, window, dur = m.groups()
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
                end_sec = parse_time(end_t)
                dur_s = int(dur)
                start_sec = max(0, end_sec - dur_s)
                entries.append({
                    "date": d_str,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "status": "Active",
                    "app": app.strip(),
                    "window": window.strip(),
                    "duration_sec": dur_s,
                })
                current_date = d_obj
                last_end_sec = end_sec
            except Exception:
                pass
            i += 1
            continue

        if line.startswith("-----"):
            block = []
            j = i + 1
            while j < n and len(block) < 6 and not lines[j].startswith("-----"):
                block.append(lines[j].rstrip("\n"))
                j += 1

            valid = (
                len(block) == 6
                and TIME_RE.match(block[0].strip())
                and block[1].startswith("Status:")
                and block[2].startswith("Window:")
                and block[3].startswith("App:")
                and block[4].startswith("End:")
                and block[5].startswith("Duration:")
            )
            if valid and current_date is not None:
                try:
                    start_sec = parse_time(block[0].strip())
                    status = block[1].split(":", 1)[1].strip()
                    window = block[2].split(":", 1)[1].strip()
                    app = block[3].split(":", 1)[1].strip()
                    end_t = block[4].split(":", 1)[1].strip()
                    end_sec = parse_time(end_t)
                    dur_s = parse_duration(block[5].split(":", 1)[1])

                    if start_sec < last_end_sec:
                        current_date = current_date + timedelta(days=1)
                    elif last_end_sec >= 0:
                        silent_gaps.append((len(entries), start_sec - last_end_sec))

                    if end_sec < start_sec:
                        end_sec = 86399  # clip cross-midnight overflow

                    entries.append({
                        "date": current_date.isoformat(),
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "status": status,
                        "app": app,
                        "window": window,
                        "duration_sec": dur_s,
                    })
                    last_end_sec = end_sec
                except Exception:
                    pass
            i = j if j > i else i + 1
            continue

        i += 1

    # Anchor end of file to mtime date, inserting rollovers in the largest gaps.
    try:
        mtime_date = datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        mtime_date = None

    if mtime_date and entries:
        last_date = datetime.strptime(entries[-1]["date"], "%Y-%m-%d").date()
        delta_days = (mtime_date - last_date).days
        if delta_days > 0 and silent_gaps:
            silent_gaps.sort(key=lambda g: -g[1])
            insert_at = sorted(g[0] for g in silent_gaps[:delta_days])
            cursor = 0
            shift = 0
            for idx in range(len(entries)):
                while cursor < len(insert_at) and insert_at[cursor] <= idx:
                    shift += 1
                    cursor += 1
                if shift:
                    d = datetime.strptime(entries[idx]["date"], "%Y-%m-%d").date()
                    entries[idx]["date"] = (d + timedelta(days=shift)).isoformat()

    return entries


def aggregate_by_day(entries):
    by_day = defaultdict(list)
    for e in entries:
        by_day[e["date"]].append(e)
    for k in by_day:
        by_day[k].sort(key=lambda x: x["start_sec"])
    return dict(by_day)


def day_summary(day_entries):
    total_active = sum(e["duration_sec"] for e in day_entries if e["status"] == "Active")
    total_afk = sum(e["duration_sec"] for e in day_entries if e["status"] == "AFK")

    apps = defaultdict(int)
    windows = defaultdict(int)
    for e in day_entries:
        if e["status"] == "Active":
            apps[e["app"]] += e["duration_sec"]
            windows[(e["app"], e["window"])] += e["duration_sec"]

    return {
        "total_active": total_active,
        "total_afk": total_afk,
        "top_apps": [{"app": a, "seconds": s} for a, s in sorted(apps.items(), key=lambda x: -x[1])],
        "top_windows": [
            {"app": a, "window": w, "seconds": s}
            for (a, w), s in sorted(windows.items(), key=lambda x: -x[1])[:30]
        ],
    }


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Window Tracker</title>
<link rel="icon" type="image/png" href="/logo.png">
<style>
:root {
  --bg: #0f1115;
  --panel: #181a20;
  --border: #25282f;
  --fg: #e6e8eb;
  --muted: #8a8f99;
  --accent: #6ea1ff;
  --afk-a: #3a3d44;
  --afk-b: #2a2d33;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; }
body {
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  display: flex;
  flex-direction: column;
}
header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 18px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
header h1 { font-size: 14px; margin: 0; font-weight: 600; letter-spacing: 0.02em; }
header .logo { height: 22px; width: 22px; border-radius: 5px; display: block; margin-right: -8px; }
.date-nav, .zoom-nav { display: flex; align-items: center; gap: 6px; }
.date-nav button, .date-nav input[type="date"], .zoom-nav button {
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  padding: 5px 10px;
  border-radius: 4px;
  font: inherit;
  cursor: pointer;
  color-scheme: dark;
}
.date-nav button:hover, .zoom-nav button:hover { background: var(--border); }
.date-nav button:disabled { opacity: 0.4; cursor: not-allowed; }
.zoom-nav button { min-width: 28px; }
.stats { display: flex; gap: 20px; margin-left: auto; color: var(--muted); }
.stats strong { color: var(--fg); margin-left: 6px; font-variant-numeric: tabular-nums; }
main {
  flex: 1;
  display: grid;
  grid-template-columns: 420px 1fr;
  overflow: hidden;
  min-height: 0;
}
.timeline-panel {
  overflow: auto;
  position: relative;
  background: var(--bg);
}
.timeline {
  position: relative;
  margin: 0 12px 12px 50px;
  padding-top: 4px;
}
.hour-row {
  position: relative;
  border-top: 1px solid var(--border);
}
.hour-row:last-child { border-bottom: 1px solid var(--border); }
.hour-row .hour-label {
  position: absolute;
  left: -50px;
  top: -7px;
  width: 42px;
  text-align: right;
  color: var(--muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.entry {
  position: absolute;
  left: 0;
  right: 0;
  border-radius: 2px;
  cursor: pointer;
  font-size: 10px;
  padding: 1px 5px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: rgba(255,255,255,0.95);
  border: 1px solid rgba(0,0,0,0.25);
  line-height: 1.3;
}
.entry.afk {
  background: repeating-linear-gradient(
    45deg, var(--afk-a), var(--afk-a) 5px,
    var(--afk-b) 5px, var(--afk-b) 10px
  );
  color: var(--muted);
}
.entry:hover { outline: 2px solid var(--accent); z-index: 10; }
.side-panel {
  overflow: auto;
  padding: 14px 20px;
  border-right: 1px solid var(--border);
}
.side-panel h2 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin: 0 0 8px 0;
  font-weight: 600;
}
.section { margin-bottom: 24px; }
.bar-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 1px 0;
  font-size: 12px;
}
.label-wrap {
  position: relative;
  padding: 5px 8px;
  border-radius: 3px;
  overflow: hidden;
  min-width: 0;
}
.bar {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  border-radius: 3px;
  z-index: 0;
}
.label {
  position: relative;
  z-index: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.duration {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  padding-right: 4px;
}
.popup {
  position: fixed;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 8px 12px;
  max-width: 420px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.5);
  pointer-events: none;
  z-index: 100;
  font-size: 12px;
}
.popup .pp-app { font-weight: 600; }
.popup .pp-window { color: var(--muted); margin-top: 3px; word-break: break-word; white-space: normal; }
.popup .pp-time { color: var(--muted); font-variant-numeric: tabular-nums; margin-top: 5px; }
.empty { color: var(--muted); padding: 12px 0; font-size: 12px; }
</style>
</head>
<body>
<header>
  <img class="logo" src="/logo.png" alt="">
  <h1>Window Tracker</h1>
  <div class="date-nav">
    <button id="prev" title="Previous day">‹</button>
    <input type="date" id="date">
    <button id="next" title="Next day">›</button>
    <button id="latest">Latest</button>
  </div>
  <div class="zoom-nav">
    <button id="zoom-out" title="Zoom out (Ctrl+−)">−</button>
    <button id="zoom-reset" title="Reset zoom">1×</button>
    <button id="zoom-in" title="Zoom in (Ctrl++)">+</button>
  </div>
  <div class="stats">
    <div>Active <strong id="stat-active">—</strong></div>
    <div>AFK <strong id="stat-afk">—</strong></div>
    <div>Tracked <strong id="stat-tracked">—</strong></div>
  </div>
</header>
<main>
  <div class="side-panel">
    <div class="section">
      <h2>Top Apps</h2>
      <div id="top-apps"></div>
    </div>
    <div class="section">
      <h2>Top Windows</h2>
      <div id="top-windows"></div>
    </div>
  </div>
  <div class="timeline-panel">
    <div class="timeline" id="timeline"></div>
  </div>
</main>
<div id="popup" class="popup" style="display:none"></div>
<script>
let pxPerMin = 3; // 180px per hour column at default zoom
const MIN_PX_PER_MIN = 0.5;
const MAX_PX_PER_MIN = 30;
let lastEntries = [];
const $ = id => document.getElementById(id);

const fmtDuration = s => {
  s = Math.round(s);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
};
const fmtTime = s => {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), se = s%60;
  return String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0') + ':' + String(se).padStart(2,'0');
};
const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function appColor(name, alpha = 1) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return alpha < 1
    ? `hsla(${hue} 60% 50% / ${alpha})`
    : `hsl(${hue} 55% 42%)`;
}

let availableDates = [];
let currentDate = null;

async function loadDates() {
  const r = await fetch('/api/dates');
  availableDates = (await r.json()).dates;
}

async function loadDay(date) {
  const r = await fetch('/api/day?date=' + encodeURIComponent(date));
  render(await r.json());
}

function buildTimeline(entries) {
  lastEntries = entries;
  const tl = $('timeline');
  tl.innerHTML = '';
  const rowHeight = 60 * pxPerMin;
  for (let h = 0; h < 24; h++) {
    const row = document.createElement('div');
    row.className = 'hour-row';
    row.style.height = rowHeight + 'px';
    const lbl = document.createElement('div');
    lbl.className = 'hour-label';
    lbl.textContent = String(h).padStart(2,'0') + ':00';
    row.appendChild(lbl);
    tl.appendChild(row);
  }
  for (const e of entries) {
    const div = document.createElement('div');
    div.className = 'entry' + (e.status === 'AFK' ? ' afk' : '');
    const top = (e.start_sec / 60) * pxPerMin;
    const height = Math.max(2, ((e.end_sec - e.start_sec) / 60) * pxPerMin);
    div.style.top = top + 'px';
    div.style.height = height + 'px';
    if (e.status !== 'AFK') div.style.background = appColor(e.app || '?');
    if (height > 12) {
      div.textContent = (e.status === 'AFK' ? '⏸ AFK' : (e.app || '?'))
        + (height > 22 ? '  •  ' + (e.window || '') : '');
    }
    div.addEventListener('mouseenter', ev => showPopup(ev, e));
    div.addEventListener('mousemove', positionPopup);
    div.addEventListener('mouseleave', hidePopup);
    tl.appendChild(div);
  }
}

function setZoom(newPx, anchorClientY) {
  newPx = Math.max(MIN_PX_PER_MIN, Math.min(MAX_PX_PER_MIN, newPx));
  if (newPx === pxPerMin) return;
  const panel = document.querySelector('.timeline-panel');
  const rect = panel.getBoundingClientRect();
  const anchor = anchorClientY != null ? (anchorClientY - rect.top) : rect.height / 2;
  const minuteAtAnchor = (panel.scrollTop + anchor) / pxPerMin;
  pxPerMin = newPx;
  buildTimeline(lastEntries);
  panel.scrollTop = minuteAtAnchor * pxPerMin - anchor;
}

function showPopup(ev, e) {
  const p = $('popup');
  p.innerHTML = `
    <div class="pp-app">${e.status === 'AFK' ? '⏸ AFK' : escape(e.app || '?')}</div>
    <div class="pp-window">${escape(e.window || '')}</div>
    <div class="pp-time">${fmtTime(e.start_sec)} → ${fmtTime(e.end_sec)} • ${fmtDuration(e.duration_sec)}</div>
  `;
  p.style.display = 'block';
  positionPopup(ev);
}
function positionPopup(ev) {
  const p = $('popup');
  const w = p.offsetWidth, h = p.offsetHeight;
  p.style.left = Math.min(ev.clientX + 14, window.innerWidth - w - 8) + 'px';
  p.style.top = Math.min(ev.clientY + 14, window.innerHeight - h - 8) + 'px';
}
function hidePopup() { $('popup').style.display = 'none'; }

function buildBars(containerId, items, getLabel, getColorKey) {
  const c = $(containerId);
  c.innerHTML = '';
  if (!items.length) {
    c.innerHTML = '<div class="empty">No data</div>';
    return;
  }
  const max = items[0].seconds;
  for (const item of items) {
    const row = document.createElement('div');
    row.className = 'bar-row';
    const wrap = document.createElement('div');
    wrap.className = 'label-wrap';
    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.width = (item.seconds / max * 100) + '%';
    const key = getColorKey(item);
    bar.style.background = appColor(key, 0.18);
    bar.style.borderLeft = '2px solid ' + appColor(key);
    const lbl = document.createElement('div');
    lbl.className = 'label';
    lbl.textContent = getLabel(item);
    lbl.title = getLabel(item);
    wrap.appendChild(bar);
    wrap.appendChild(lbl);
    const dur = document.createElement('div');
    dur.className = 'duration';
    dur.textContent = fmtDuration(item.seconds);
    row.appendChild(wrap);
    row.appendChild(dur);
    c.appendChild(row);
  }
}

function render(data) {
  const s = data.summary || {};
  $('stat-active').textContent = fmtDuration(s.total_active || 0);
  $('stat-afk').textContent = fmtDuration(s.total_afk || 0);
  $('stat-tracked').textContent = fmtDuration((s.total_active || 0) + (s.total_afk || 0));
  buildTimeline(data.entries || []);
  buildBars('top-apps', (s.top_apps || []).slice(0, 15), x => x.app, x => x.app);
  buildBars('top-windows', s.top_windows || [], x => x.window || '(no title)', x => x.app);
  updateNavButtons();
}

function updateNavButtons() {
  const idx = availableDates.indexOf(currentDate);
  $('prev').disabled = idx <= 0;
  $('next').disabled = idx === -1 || idx >= availableDates.length - 1;
}

function setDate(d) {
  if (!d) return;
  currentDate = d;
  $('date').value = d;
  loadDay(d);
}
function shiftDate(delta) {
  const idx = availableDates.indexOf(currentDate);
  if (idx === -1) return;
  const n = idx + delta;
  if (n < 0 || n >= availableDates.length) return;
  setDate(availableDates[n]);
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.ctrlKey && (e.key === '=' || e.key === '+')) { e.preventDefault(); setZoom(pxPerMin * 1.25); }
  else if (e.ctrlKey && e.key === '-') { e.preventDefault(); setZoom(pxPerMin / 1.25); }
  else if (e.ctrlKey && e.key === '0') { e.preventDefault(); setZoom(3); }
  else if (e.key === 'ArrowLeft') shiftDate(-1);
  else if (e.key === 'ArrowRight') shiftDate(1);
});

(async function init() {
  await loadDates();
  if (!availableDates.length) {
    $('timeline').innerHTML = '<div class="empty" style="padding:40px 20px;">No data found in log</div>';
    return;
  }
  setDate(availableDates[availableDates.length - 1]);
  $('prev').onclick = () => shiftDate(-1);
  $('next').onclick = () => shiftDate(1);
  $('latest').onclick = () => setDate(availableDates[availableDates.length - 1]);
  $('date').onchange = e => {
    if (availableDates.includes(e.target.value)) setDate(e.target.value);
    else setDate(currentDate);
  };
  $('zoom-in').onclick = () => setZoom(pxPerMin * 1.25);
  $('zoom-out').onclick = () => setZoom(pxPerMin / 1.25);
  $('zoom-reset').onclick = () => setZoom(3);

  const panel = document.querySelector('.timeline-panel');
  panel.addEventListener('wheel', e => {
    if (e.ctrlKey) {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 1/1.15;
      setZoom(pxPerMin * factor, e.clientY);
    }
  }, { passive: false });

  // Auto-scroll timeline to first entry of the day
  setTimeout(() => {
    const first = document.querySelector('.entry');
    if (first) first.scrollIntoView({block: 'center'});
  }, 100);
})();
</script>
</body>
</html>
"""


class Cache:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.mtime = 0.0
        self.by_day: dict = {}
        self.dates: list = []

    def refresh(self):
        try:
            mt = self.log_path.stat().st_mtime
        except OSError:
            return
        if mt == self.mtime:
            return
        t0 = datetime.now()
        entries = parse_log(self.log_path)
        self.by_day = aggregate_by_day(entries)
        self.dates = sorted(self.by_day.keys())
        self.mtime = mt
        elapsed = (datetime.now() - t0).total_seconds()
        print(
            f"[parser] {len(entries)} entries across {len(self.dates)} days in {elapsed:.2f}s",
            file=sys.stderr,
        )


class Handler(BaseHTTPRequestHandler):
    cache: Cache = None

    def _send(self, body: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status: int = 200):
        self._send(json.dumps(payload).encode("utf-8"), "application/json", status)

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)

        if url.path == "/":
            self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        if url.path == "/logo.png":
            data = load_logo()
            if not data:
                self._send(b"not found", "text/plain", 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return

        self.cache.refresh()

        if url.path == "/api/dates":
            self._send_json({"dates": self.cache.dates})
            return

        if url.path == "/api/day":
            d = (qs.get("date") or [None])[0]
            entries = self.cache.by_day.get(d, [])
            self._send_json({
                "date": d,
                "entries": entries,
                "summary": day_summary(entries),
            })
            return

        self._send(b"not found", "text/plain", 404)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[http] {self.address_string()} - {fmt % args}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"Log file not found: {args.log}", file=sys.stderr)
        sys.exit(1)

    cache = Cache(args.log)
    cache.refresh()
    Handler.cache = cache

    server = HTTPServer((args.host, args.port), Handler)
    print(f"Window Tracker UI", file=sys.stderr)
    print(f"  Log:     {args.log}", file=sys.stderr)
    print(f"  Serving: http://{args.host}:{args.port}", file=sys.stderr)
    print(f"  Ctrl+C to stop", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)


if __name__ == "__main__":
    main()
