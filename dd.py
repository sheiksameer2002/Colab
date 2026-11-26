#Interactive charts - accepted-date replay (prev/next), no news, no yfinance
#v3: search box, 200EMA filter, Random sort, pagination (50 per page), lazy chart rendering
#v4: per-chart ↻ refresh button (reload from file, regenerate if missing/broken, Shift+click = force regenerate)
#    "Render Blank Charts" now regenerates charts that have no saved file / can't be drawn, then renders them
#v5: 1D regenerate = exactly 1 year before earliest accepted date (or whatever history exists)
#    1D charts open with a ~1 year window; Next/Prev/Reset keep the current zoom (no jump to full zoom-out)
import subprocess
import sys
import shutil
import os

def _ensure(pip_name, import_name=None):
    import_name = import_name or pip_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"📦 Installing missing package: {pip_name} ...")
        subprocess.run([sys.executable, "-m", "pip", "install", pip_name, "-q"], check=True)

_ensure("flask", "flask")
_ensure("requests", "requests")
_ensure("openpyxl", "openpyxl")

CLOUDFLARED_EXE = os.path.join(r"C:\Users\sheik\PycharmProjects\MyProject", "cloudflared.exe")

def _ensure_cloudflared():
    if shutil.which("cloudflared"):
        print("✅ cloudflared already on PATH, skipping download.")
        return "cloudflared"
    if os.path.exists(CLOUDFLARED_EXE):
        print("✅ cloudflared.exe already present, skipping download.")
        return CLOUDFLARED_EXE
    print("📦 Downloading cloudflared (Windows binary) ...")
    import urllib.request
    urllib.request.urlretrieve(
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
        CLOUDFLARED_EXE
    )
    print(f"✅ cloudflared downloaded to {CLOUDFLARED_EXE}")
    return CLOUDFLARED_EXE

CLOUDFLARED_CMD = _ensure_cloudflared()

import ast
import json
import time
import logging
import threading
import socket
import re
import urllib.parse
import requests as _req_lib
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify, request
import pandas as pd

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
logging.basicConfig(level=logging.ERROR)

BASE_DIR = r"C:\Users\sheik\PycharmProjects\MyProject"
os.makedirs(BASE_DIR, exist_ok=True)

CHART_DIR_1D = f"{BASE_DIR}/1daycharts"
CHART_DIR_1W = f"{BASE_DIR}/weeklycharts"
FUNDAMENTALS_CACHE_FILE = f"{BASE_DIR}/fundamentals_cache1.json"
CONDITION_MET_EXCEL = f"{BASE_DIR}/1hour_Condition_Met_Stocks.xlsx"
for _d in [CHART_DIR_1D, CHART_DIR_1W]:
    os.makedirs(_d, exist_ok=True)

# ── Stock list (plain NSE names, e.g. "RELIANCE") ─────────────
stocks_file = f"{BASE_DIR}/matched_stocks1.txt"
STOCKS = []

def _to_plain_name(s):
    s = s.strip()
    return s.split(":")[1].replace("-EQ", "") if ":" in s else s.replace("-EQ", "")

if os.path.exists(stocks_file):
    with open(stocks_file, "r") as f:
        content = f.read().strip()
    try:
        if "STOCKS" in content:
            content = content.split("=", 1)[1].strip()
        raw = [s.strip() for s in ast.literal_eval(content)]
        STOCKS = [_to_plain_name(s) for s in raw]
    except Exception as e:
        print(f"❌ Failed to parse STOCKS: {e}")

def _parse_excel_date(date_val):
    if pd.isna(date_val):
        return ""
    return date_val.strftime("%Y-%m-%d") if hasattr(date_val, 'strftime') \
           else str(date_val).strip()[:10]

def _norm_200(v):
    """Normalise AcceptedCloseVs200EMA → 'Above 200EMA' / 'Below 200EMA' / ''"""
    s = (v or "").strip().lower()
    if "above" in s:
        return "Above 200EMA"
    if "below" in s:
        return "Below 200EMA"
    return ""

# ── One ENTRY per (stock, condition-met, accepted) row → duplicate stocks get their own chart ──
ENTRIES = []

def load_entries():
    global ENTRIES
    ENTRIES = []
    rows = []
    if not os.path.exists(CONDITION_MET_EXCEL):
        print(f"⚠ Condition met Excel not found: {CONDITION_MET_EXCEL}")
    else:
        try:
            df = pd.read_excel(CONDITION_MET_EXCEL)
            df.columns = [c.strip() for c in df.columns]
            if "StockName" not in df.columns or "ConditionMetDate" not in df.columns:
                print(f"⚠ Excel columns not found. Got: {list(df.columns)}")
            else:
                cols = set(df.columns)
                if "AcceptedCloseVs200EMA" not in cols:
                    print("⚠ Column 'AcceptedCloseVs200EMA' not found in Excel — 200EMA filter will be empty")
                def g(row, col):
                    return str(row[col]).strip() if col in cols and pd.notna(row[col]) else ""
                seen = set()
                for _, row in df.iterrows():
                    sym = _to_plain_name(str(row["StockName"]))
                    if STOCKS and sym not in STOCKS:
                        continue
                    cm = _parse_excel_date(row["ConditionMetDate"])
                    if not cm:
                        continue
                    acc = _parse_excel_date(row["FinalAcceptedDate"]) if "FinalAcceptedDate" in cols else ""
                    key = (sym, cm, acc)
                    if key in seen:
                        continue
                    seen.add(key)
                    ema = None
                    if "EMADistancePct" in cols and pd.notna(row["EMADistancePct"]):
                        try: ema = float(row["EMADistancePct"])
                        except Exception: ema = None
                    rows.append({
                        "name": sym, "cm": cm, "acc": acc,
                        "pattern": g(row, "PatternLabel"), "trade": g(row, "TradeStatus"),
                        "updown": g(row, "UpDownMove"), "ema_dist": ema,
                        "never_below": g(row, "CloseNeverBelow10EMA"),
                        "vs200": _norm_200(g(row, "AcceptedCloseVs200EMA")),
                    })
        except Exception as e:
            print(f"❌ Failed to load condition met Excel: {e}")
    # stocks in the list with no Excel row still get one plain chart (ends at last saved candle)
    have = {r["name"] for r in rows}
    for s in STOCKS:
        if s not in have:
            rows.append({"name": s, "cm": "", "acc": "", "pattern": "", "trade": "",
                         "updown": "", "ema_dist": None, "never_below": "", "vs200": ""})
    rows.sort(key=lambda r: (r["name"], r["acc"] or ""), reverse=False)
    # same stock: newest accepted first
    from itertools import groupby
    out = []
    for _, grp in groupby(rows, key=lambda r: r["name"]):
        out.extend(sorted(list(grp), key=lambda r: r["acc"], reverse=True))
    for i, r in enumerate(out):
        r["id"] = i
    ENTRIES = out
    print(f"✅ {len(ENTRIES)} chart rows ({len({e['name'] for e in ENTRIES})} unique stocks)")

load_entries()

# ── Saved chart JSON loader (full history; the browser slices it up to the accepted date) ──
_chart_cache = {}
_chart_lock = threading.Lock()

def load_chart(name, tf, use_cache=True):
    k = (name, tf)
    if use_cache:
        with _chart_lock:
            if k in _chart_cache:
                return _chart_cache[k]
    path = os.path.join(CHART_DIR_1D if tf == "1d" else CHART_DIR_1W, f"{name}.json")
    if not os.path.exists(path):
        return {"error": "No saved chart file"}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if "times" not in data and "labels" in data:
            data["times"] = [datetime.strptime(l, "%d %b %y").strftime("%Y-%m-%d") for l in data["labels"]]
        if "times" not in data or "candles" not in data:
            return {"error": "Saved chart has no times/candles"}
        if use_cache:
            with _chart_lock:
                _chart_cache[k] = data
        return data
    except Exception as e:
        return {"error": f"Load failed: {e}"}

# ── Pre-compute per-entry summary at the accepted date (price, %chg, supertrend dir) ──
#    so sorting works across ALL items without having to load every chart in the browser.
def _summarize_name(name, lst=None):
    """(Re)compute ltp / %chg / vol / supertrend direction for every entry of one stock."""
    if lst is None:
        lst = [e for e in ENTRIES if e["name"] == name]
    for e in lst:
        e.update({"ltp": None, "pchange": None, "vol": 0, "st1d": 0, "st1w": 0})
    for tf in ("1d", "1w"):
        d = load_chart(name, tf, use_cache=False)   # don't keep in RAM, just read once
        if d.get("error"):
            continue
        times, candles = d["times"], d["candles"]
        g_arr, r_arr = d.get("st_green") or [], d.get("st_red") or []
        vols = d.get("volume") or []
        for e in lst:
            ai = (bisect_right(times, e["acc"]) - 1) if e["acc"] else len(times) - 1
            if ai < 0 or ai >= len(candles):
                continue
            c = candles[ai]
            prev = candles[ai - 1]["c"] if ai > 0 else c["o"]
            chg = c["c"] - prev
            pch = (chg / prev * 100) if prev else 0
            if g_arr and ai < len(g_arr) and g_arr[ai] is not None:
                e["st" + tf] = 1
            elif r_arr and ai < len(r_arr) and r_arr[ai] is not None:
                e["st" + tf] = -1
            if tf == "1d" or e["ltp"] is None:       # 1D preferred, else fall back to 1W
                e["ltp"] = c["c"]
                e["pchange"] = pch
                e["vol"] = vols[ai]["y"] if ai < len(vols) and isinstance(vols[ai], dict) and "y" in vols[ai] else 0

def compute_summaries():
    t0 = time.time()
    by_name = {}
    for e in ENTRIES:
        by_name.setdefault(e["name"], []).append(e)
    for name, lst in by_name.items():
        _summarize_name(name, lst)
    print(f"✅ Summaries computed for {len(ENTRIES)} rows in {time.time()-t0:.1f}s")

compute_summaries()

# =============================================================
#  Chart regeneration  (used when a saved JSON is missing / broken / doesn't reach the accepted date)
#  Data source: Yahoo Finance chart API (NSE symbol + ".NS", falls back to ".BO").
#  Writes the SAME json layout the dashboard already reads:
#     times, candles[{o,h,l,c}], volume[{y,up}], ema10, ema20, ema200, st_green, st_red
#  The old file (if any) is kept as <name>.json.bak
#
#  v5: for 1D the saved history starts exactly REGEN_BEFORE_ACC_DAYS["1d"] (365) days before the EARLIEST
#      accepted date of that stock (or 365 days before today if there is no accepted date).
#      If the stock has less history than that (newly listed), Yahoo simply returns what exists.
# =============================================================
ST_PERIOD = 10          # Supertrend ATR period      (change if your saved charts use something else)
ST_MULT   = 3.0         # Supertrend multiplier
REGEN_DEFAULT_DAYS    = {"1d": 365,  "1w": 2600}   # history length when there's no accepted date (1d: 1 year)
REGEN_BEFORE_ACC_DAYS = {"1d": 365,  "1w": 2600}   # history kept before the earliest accepted date (1d: 1 year)
REGEN_WARMUP_DAYS     = {"1d": 450,  "1w": 1700}   # extra history only used to warm up EMA200 / Supertrend (trimmed off)
_regen_lock = threading.Lock()
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

def _fetch_ohlcv(name, tf, start_dt, end_dt):
    """Returns (rows, error). rows = [(YYYY-MM-DD, o, h, l, c, vol), ...]"""
    interval = "1d" if tf == "1d" else "1wk"
    p1 = int(start_dt.replace(tzinfo=timezone.utc).timestamp())
    p2 = int(end_dt.replace(tzinfo=timezone.utc).timestamp())
    last_err = "no data returned"
    for suffix in (".NS", ".BO"):
        sym = urllib.parse.quote(name + suffix, safe="")
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?interval={interval}&period1={p1}&period2={p2}&includePrePost=false")
        for _attempt in range(2):
            try:
                r = _req_lib.get(url, headers=YF_HEADERS, timeout=25)
                if r.status_code == 429:
                    time.sleep(2)
                    continue
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code} for {name}{suffix}"
                    break
                res = (r.json().get("chart") or {}).get("result")
                if not res:
                    last_err = f"{name}{suffix} not found"
                    break
                res = res[0]
                ts = res.get("timestamp") or []
                q = res["indicators"]["quote"][0]
                off = (res.get("meta") or {}).get("gmtoffset", 19800)
                rows = []
                for i, t in enumerate(ts):
                    o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                    if o is None or h is None or l is None or c is None:
                        continue
                    v = q["volume"][i] or 0
                    d = datetime.fromtimestamp(t + off, tz=timezone.utc).strftime("%Y-%m-%d")
                    rows.append((d, float(o), float(h), float(l), float(c), int(v)))
                if rows:
                    return rows, None
                last_err = f"{name}{suffix}: empty candle list"
                break
            except Exception as ex:
                last_err = str(ex)
    return None, last_err

def _ema_list(vals, span):
    out, e, k = [], None, 2.0 / (span + 1)
    for i, v in enumerate(vals):
        e = v if e is None else (v * k + e * (1 - k))
        out.append(round(e, 2) if i >= span - 1 else None)
    return out

def _supertrend_lists(H, L, C, period, mult):
    n = len(C)
    atr = [0.0] * n
    for i in range(n):
        tr = (H[i] - L[i]) if i == 0 else max(H[i] - L[i], abs(H[i] - C[i-1]), abs(L[i] - C[i-1]))
        atr[i] = tr if i == 0 else (atr[i-1] * (period - 1) + tr) / period
    fu, fl, dr = [0.0] * n, [0.0] * n, [1] * n
    green, red = [None] * n, [None] * n
    for i in range(n):
        hl2 = (H[i] + L[i]) / 2.0
        bu, bl = hl2 + mult * atr[i], hl2 - mult * atr[i]
        if i == 0:
            fu[i], fl[i], dr[i] = bu, bl, 1
        else:
            fu[i] = bu if (bu < fu[i-1] or C[i-1] > fu[i-1]) else fu[i-1]
            fl[i] = bl if (bl > fl[i-1] or C[i-1] < fl[i-1]) else fl[i-1]
            if C[i] > fu[i-1]:
                dr[i] = 1
            elif C[i] < fl[i-1]:
                dr[i] = -1
            else:
                dr[i] = dr[i-1]
        if i >= period:
            if dr[i] == 1:
                green[i] = round(fl[i], 2)
            else:
                red[i] = round(fu[i], 2)
    return green, red

def regenerate_chart(name, tf):
    """Download fresh candles, rebuild indicators and overwrite the saved json. Returns the chart data or {'error':..}"""
    if tf not in ("1d", "1w"):
        return {"error": f"Unknown timeframe '{tf}'"}
    if name not in {e["name"] for e in ENTRIES}:
        return {"error": f"{name} is not in the stock list"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    first_acc = None
    accs = [e["acc"] for e in ENTRIES if e["name"] == name and e["acc"]]
    if accs:
        try:
            first_acc = datetime.strptime(min(accs), "%Y-%m-%d")
        except ValueError:
            first_acc = None

    if tf == "1d":
        # EXACTLY 1 year before the earliest accepted date (or before today if none). No older data is kept.
        base = first_acc if first_acc else now
        start = base - timedelta(days=REGEN_BEFORE_ACC_DAYS["1d"])
    else:
        start = now - timedelta(days=REGEN_DEFAULT_DAYS[tf])
        if first_acc:
            start = min(start, first_acc - timedelta(days=REGEN_BEFORE_ACC_DAYS[tf]))
    fetch_from = start - timedelta(days=REGEN_WARMUP_DAYS[tf])

    with _regen_lock:
        rows, err = _fetch_ohlcv(name, tf, fetch_from, now + timedelta(days=1))
        if not rows:
            print(f"❌ Regenerate {name} {tf} failed: {err}")
            return {"error": f"Could not download {name} {tf}: {err}"}
        by_date = {}
        for r in rows:
            by_date[r[0]] = r
        dates = sorted(by_date)
        O = [by_date[d][1] for d in dates]; H = [by_date[d][2] for d in dates]
        L = [by_date[d][3] for d in dates]; C = [by_date[d][4] for d in dates]
        V = [by_date[d][5] for d in dates]

        ema10, ema20, ema200 = _ema_list(C, 10), _ema_list(C, 20), _ema_list(C, 200)
        st_g, st_r = _supertrend_lists(H, L, C, ST_PERIOD, ST_MULT)

        start_str = start.strftime("%Y-%m-%d")
        # first candle on/after the 1-year start; if the stock has less history, this is simply its first candle
        first = next((i for i, d in enumerate(dates) if d >= start_str), None)
        if first is None:
            return {"error": f"No candles for {name} {tf} after {start_str}"}
        sl = slice(first, None)
        data = {
            "symbol": name, "timeframe": tf, "source": "regenerated (Yahoo Finance)",
            "generated": now.strftime("%Y-%m-%d %H:%M:%S"),
            "times": dates[sl],
            "candles": [{"o": round(O[i], 2), "h": round(H[i], 2), "l": round(L[i], 2), "c": round(C[i], 2)}
                        for i in range(first, len(dates))],
            "volume": [{"y": V[i], "up": C[i] >= O[i]} for i in range(first, len(dates))],
            "ema10": ema10[sl], "ema20": ema20[sl], "ema200": ema200[sl],
            "st_green": st_g[sl], "st_red": st_r[sl],
        }
        path = os.path.join(CHART_DIR_1D if tf == "1d" else CHART_DIR_1W, f"{name}.json")
        try:
            if os.path.exists(path):
                shutil.copyfile(path, path + ".bak")
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except Exception as ex:
            return {"error": f"Could not save {name} {tf}: {ex}"}
        with _chart_lock:
            _chart_cache[(name, tf)] = data
        _summarize_name(name)
        print(f"🔄 Regenerated {name} {tf}: {len(data['times'])} candles ({data['times'][0]} → {data['times'][-1]})")
        return data

fundamentals_cache = {}
def load_fundamentals_from_file():
    if not os.path.exists(FUNDAMENTALS_CACHE_FILE):
        return
    try:
        with open(FUNDAMENTALS_CACHE_FILE, "r") as f:
            fundamentals_cache.update(json.load(f))
        print(f"✅ Fundamentals loaded from file for {len(fundamentals_cache)} stocks")
    except Exception as e:
        print(f"⚠ Failed to load fundamentals cache file: {e}")

# ── Flask ─────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML, entries=ENTRIES)

@app.route("/api/chart")
def api_chart():
    sym = request.args.get("symbol", "")
    tf = request.args.get("tf", "1d")
    if request.args.get("fresh") == "1":              # re-read from disk, bypass server cache
        d = load_chart(sym, tf, use_cache=False)
        if not d.get("error"):
            with _chart_lock:
                _chart_cache[(sym, tf)] = d
            _summarize_name(sym)
        return jsonify(d)
    return jsonify(load_chart(sym, tf))

@app.route("/api/regenerate")
def api_regenerate():
    return jsonify(regenerate_chart(request.args.get("symbol", ""), request.args.get("tf", "1d")))

@app.route("/api/fundamentals_all")
def api_fundamentals_all():
    return jsonify(fundamentals_cache)

# =============================================================
#  HTML  (lightweight-charts, charts end at accepted date, Prev/Next replay)
# =============================================================
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Stock Charts Dashboard</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg:#000000; --surface:#111111; --border:#2a2a2a;
    --accent:#3b82f6; --accent2:#10b981;
    --text:#e5e7eb; --muted:#9ca3af; --green:#22c55e; --red:#ef4444; --yellow:#f59e0b;
    --font:'Inter','Segoe UI',system-ui,sans-serif;
    --info-w:230px; --row-sep:#262626;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html,body { height:100%; background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; }
  #topbar {
    position:sticky; top:0; z-index:200; background:var(--surface);
    border-bottom:1px solid var(--border); padding:7px 14px;
    display:flex; align-items:center; gap:10px; flex-wrap:wrap;
    box-shadow:0 1px 4px rgba(0,0,0,.5); min-width:max-content;
  }
  #topbar .brand { font-size:15px; font-weight:700; color:var(--accent); letter-spacing:.5px; }
  #topbar .sep   { width:1px; height:20px; background:var(--border); }
  .mode-badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:10px;
    background:#3b2f0f; color:#fbbf24; border:1px solid #78530f; }
  .sort-wrap { display:flex; align-items:center; gap:6px; }
  .sort-wrap label { color:var(--muted); font-size:11px; white-space:nowrap; }
  #sortSelect, #tradeFilter, #moveFilter, #emaFilter { background:#1a1a1a; color:var(--text); border:1px solid var(--border);
    border-radius:6px; padding:4px 8px; font-size:12px; cursor:pointer; outline:none; }
  .search-wrap { position:relative; display:flex; align-items:center; }
  #searchInput { background:#1a1a1a; color:var(--text); border:1px solid var(--border);
    border-radius:6px; padding:4px 24px 4px 8px; font-size:12px; width:190px; outline:none; }
  #searchInput:focus { border-color:var(--accent); }
  #searchInput::placeholder { color:#6b7280; }
  #searchClear { position:absolute; right:4px; background:none; border:none; color:var(--muted);
    cursor:pointer; font-size:12px; padding:2px 4px; display:none; }
  #searchClear:hover { color:var(--red); }
  .btn { display:inline-flex; align-items:center; gap:5px; padding:5px 11px; border-radius:6px;
    font-size:12px; font-weight:500; border:1px solid transparent; cursor:pointer;
    transition:all .15s; white-space:nowrap; }
  .btn-load { background:#1c1440; color:#c4b5fd; border-color:#3f2f6b; }
  .btn:hover { opacity:.8; transform:translateY(-1px); }
  .btn:active { transform:translateY(0); opacity:1; }
  .btn.loading { opacity:.5; pointer-events:none; }
  .btn-sm { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px;
    font-size:10px; font-weight:600; border:1px solid var(--border); background:#1a1a1a;
    color:var(--muted); cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn-sm:hover { border-color:var(--accent); color:var(--accent); background:#0d1f38; }
  .btn-sm.loading { opacity:.45; pointer-events:none; }
  .btn-sm.active { border-color:#f472b6; color:#f472b6; background:#3a0f24; }
  .btn-nav { color:#e5e7eb; background:#0d1f38; border-color:#1e3a5f; }
  .btn-refresh { color:#34d399; background:#062a1f; border-color:#0f5c43; font-size:11px; }
  .btn-refresh.loading { animation:spin-pulse 1s linear infinite; }
  @keyframes spin-pulse { 0%{opacity:.35} 50%{opacity:.8} 100%{opacity:.35} }
  #statusBadge { margin-left:auto; font-size:11px; color:var(--muted); white-space:nowrap; }
  #sizeBar {
    background:#0d0d0d; border-bottom:1px solid var(--border); padding:5px 14px;
    display:flex; align-items:center; gap:16px; flex-wrap:wrap;
    min-width:max-content; position:sticky; top:44px; z-index:190;
  }
  .tf-size-group { display:flex; align-items:center; gap:8px; padding:3px 9px;
    background:var(--surface); border:1px solid var(--border); border-radius:7px; }
  .tf-size-label { font-size:11px; font-weight:700; color:var(--accent); min-width:20px; text-align:center; }
  .size-ctrl { display:flex; align-items:center; gap:4px; }
  .size-ctrl label { color:var(--muted); font-size:10px; white-space:nowrap; }
  .step-btn { width:19px; height:19px; border-radius:4px; border:1px solid var(--border);
    background:#1a1a1a; color:var(--text); font-size:13px; line-height:1; cursor:pointer;
    display:flex; align-items:center; justify-content:center; font-weight:700; transition:all .12s; }
  .step-btn:hover { border-color:var(--accent); color:var(--accent); background:#0d1f38; }
  .size-val { font-size:10px; color:var(--text); min-width:34px; text-align:center;
    background:#1a1a1a; border:1px solid var(--border); border-radius:4px; padding:1px 3px; }
  #colHeader { display:flex; align-items:stretch; background:#0d0d0d;
    border-bottom:2px solid var(--border); position:sticky; top:82px; z-index:180; min-width:max-content; }
  .ch-info { flex:0 0 var(--info-w); min-width:var(--info-w); padding:5px 10px;
    font-size:10px; font-weight:700; letter-spacing:.6px; color:var(--muted);
    text-transform:uppercase; border-right:2px solid var(--border); display:flex; align-items:center; }
  .ch-tf { flex:0 0 auto; padding:5px 10px; font-size:11px; font-weight:700; letter-spacing:.8px;
    color:var(--muted); text-transform:uppercase; border-right:1px solid var(--border);
    display:flex; align-items:center; }
  .ch-tf:last-child { border-right:none; }
  #stockTable { min-width:max-content; }
  .stock-row { display:flex; align-items:stretch; border-bottom:2px solid var(--row-sep);
    background:var(--surface); min-width:max-content; }
  .stock-row:hover { background:#161616; }
  .info-strip { flex:0 0 var(--info-w); min-width:var(--info-w); padding:10px 10px 8px 12px;
    border-right:2px solid var(--border); display:flex; flex-direction:column; gap:3px;
    justify-content:flex-start; background:#0c0c0c; position:sticky; left:0; z-index:10; }
  .info-name { font-size:14px; font-weight:700; color:var(--accent); letter-spacing:.3px; }
  .pattern-badge { font-size:10px; font-weight:700; color:#fb923c; letter-spacing:.3px;
    display:none; width:fit-content; }
  .info-ltp  { font-size:13px; font-weight:600; color:var(--text); }
  .info-chg  { font-size:11px; font-weight:600; padding:2px 6px; border-radius:4px;
    display:inline-block; width:fit-content; }
  .chg-up { background:#0f3320; color:var(--green); }
  .chg-dn { background:#3a1414; color:var(--red); }
  .info-vol { font-size:9.5px; color:var(--muted); }
  .stock-links { display:flex; gap:5px; margin-top:4px; flex-wrap:wrap; }
  .stock-link {
    display:inline-flex; align-items:center; gap:3px;
    font-size:9px; font-weight:600; padding:2px 6px; border-radius:4px;
    text-decoration:none; border:1px solid; transition:all .15s; white-space:nowrap;
  }
  .stock-link:hover { opacity:.75; transform:translateY(-1px); }
  .link-screener { color:#2dd4bf; background:#0a2a26; border-color:#0f5c50; }
  .link-tv       { color:#60a5fa; background:#0d1f38; border-color:#1e3a5f; }
  
.ev-wrap { margin-top:5px; border-top:1px solid var(--border); padding-top:4px; }
.ev-title { font-size:9px; font-weight:700; color:#fbbf24; letter-spacing:.5px; text-transform:uppercase; margin-bottom:3px; }
.ev-list { position:relative; display:flex; flex-direction:column; gap:3px; overflow-y:auto; padding-right:3px; }
.ev-list::-webkit-scrollbar { width:5px; }
.ev-list::-webkit-scrollbar-thumb { background:#3a3a3a; border-radius:3px; }
.ev-item { flex:0 0 auto; border:1px solid var(--border); border-radius:4px; padding:3px 5px;
  background:#111; font-size:9px; line-height:1.5; }
.ev-item .ev-dates { display:flex; gap:8px; flex-wrap:wrap; }
.ev-item .ev-lbl { color:#9ca3af; font-weight:600; }
.ev-item .ev-acc { color:#22d3ee; font-weight:700; }
.ev-item .ev-badges { display:flex; gap:4px; align-items:center; flex-wrap:wrap; margin-top:2px; }
.ev-item.current { border-color:#f97316; background:#3a1d07; box-shadow:0 0 0 1px #f97316 inset; }
.ev-item.current .ev-acc, .ev-item.current .ev-lbl { color:#fb923c; }

  .fund-header { display:flex; align-items:center; justify-content:space-between;
    margin-top:5px; border-top:1px solid var(--border); padding-top:4px; }
  .fund-header-label { font-size:9px; font-weight:700; color:#22d3ee; letter-spacing:.5px; text-transform:uppercase; }
  .news-loading { font-size:9px; color:var(--muted); font-style:italic; padding:3px 0; }
  .chart-cell { flex:0 0 auto; border-right:1px solid var(--border); padding:6px 6px 4px;
    display:flex; flex-direction:column; gap:0; background:var(--bg); }
  .chart-cell:last-child { border-right:none; }
  .cell-label { font-size:9.5px; font-weight:600; letter-spacing:.8px; color:var(--muted);
    text-transform:uppercase; display:flex; align-items:center; justify-content:space-between;
    margin-bottom:3px; gap:4px; }
  .cell-label-btns { display:flex; align-items:center; gap:4px; }
  .cur-info { color:#fbbf24; font-weight:700; letter-spacing:0; text-transform:none; margin-left:4px; }
  .st-dir { font-size:9px; padding:1px 5px; border-radius:3px; font-weight:700; }
  .st-bull { background:#0f3320; color:var(--green); }
  .st-bear { background:#3a1414; color:var(--red); }
  .chart-wrap { border:1px solid var(--border); border-radius:4px; overflow:hidden; position:relative; }
  .chart-wrap.draw-mode { cursor:crosshair; }
  .chart-pending { display:flex; align-items:center; justify-content:center;
    color:var(--muted); font-size:11px; font-style:italic; text-align:center; padding:0 8px;
    background:#111111; border:1px dashed var(--border); border-radius:4px; }
  #moreWrap { position:sticky; left:0; width:100vw; padding:16px 0 40px; display:flex;
    justify-content:center; align-items:center; gap:12px; }
  #moreWrap .btn { font-size:13px; padding:8px 20px; }
  #moreInfo { color:var(--muted); font-size:12px; }
  #emptyMsg { position:sticky; left:0; width:100vw; padding:60px 0; text-align:center;
    color:var(--muted); font-size:14px; display:none; }
</style>
</head>
<body>

<div id="topbar">
  <span class="brand">📈 StockDash</span>
  <span class="mode-badge">🎯 Accepted-Date Replay</span>
  <div class="sep"></div>
  <div class="search-wrap">
    <input id="searchInput" type="text" placeholder="🔍 Search stock… (e.g. tcs, infy)" autocomplete="off" spellcheck="false"/>
    <button id="searchClear" title="Clear search (Esc)">✕</button>
  </div>
  <div class="sep"></div>
  <div class="sort-wrap">
    <label>Sort:</label>
    <select id="sortSelect">
      <option value="default">Default (A → Z)</option>
      <option value="random">🎲 Random Charts</option>
      <option value="price_asc">Price: Low → High</option>
      <option value="price_desc">Price: High → Low</option>
      <option value="change_asc">% Change: Low → High</option>
      <option value="change_desc">% Change: High → Low</option>
      <option value="cond_date">Condition Date: Latest First</option>
      <option value="accept_date">Accepted Date: Latest First</option>
      <option value="ema_dist_asc">EMA Distance: Near → Far</option>
      <option value="never_below_10ema">Close Never Below 10 EMA: Yes → No</option>
      <option value="pattern:Possibly Uptrend Reversal">Pattern: Possibly Uptrend Reversal</option>
      <option value="pattern:Uptrend">Pattern: Uptrend</option>
      <option value="pattern:Sideways">Pattern: Sideways</option>
      <option value="pattern:Downtrend">Pattern: Downtrend</option>
      <option value="pattern:Possibly Downtrend Reversal">Pattern: Possibly Downtrend Reversal</option>
      <option value="above_st_1d">Above Supertrend (1D)</option>
      <option value="above_st_1w">Above Supertrend (1W)</option>
      <option value="fund_score_desc">Fundamentals: Strong → Avoid</option>
    </select>
    <button class="btn-sm" id="btnShuffle" onclick="reshuffle()" title="New random order">🎲 Reshuffle</button>
  </div>
  <div class="sep"></div>
  <div class="sort-wrap">
    <label>Filter:</label>
    <select id="tradeFilter">
      <option value="all">All Stocks</option>
      <option value="TG Hit">TG Hit</option>
      <option value="SL Hit">SL Hit</option>
    </select>
  </div>
  <div class="sort-wrap">
    <label>Move:</label>
    <select id="moveFilter">
      <option value="all">All Moves</option>
      <option value="up">UP</option>
      <option value="down">DOWN (all)</option>
      <option value="down_not_recovered">DOWN – Not Recovered Yet</option>
      <option value="down_recovered">DOWN – Recovered</option>
    </select>
  </div>
  <div class="sort-wrap">
    <label>200 EMA:</label>
    <select id="emaFilter">
      <option value="all">All</option>
      <option value="Above 200EMA">Above 200EMA</option>
      <option value="Below 200EMA">Below 200EMA</option>
    </select>
  </div>
  <div class="sep"></div>
  <button class="btn btn-load" id="btnLoad1d" onclick="loadAll('1d')">📂 Load 1D</button>
  <button class="btn btn-load" id="btnLoad1w" onclick="loadAll('1w')">📂 Load 1W</button>
  <button class="btn btn-load" id="btnFixBlank" onclick="renderBlankCharts()" title="Draws blank charts; if a chart still can't be drawn (no saved file etc.) it is regenerated and drawn">🧩 Render Blank Charts</button>
  <button class="btn btn-load" onclick="resetAll()">⟲ Reset All to Accepted</button>
  <span id="statusBadge">—</span>
</div>

<div id="sizeBar">
  <div class="tf-size-group">
    <span class="tf-size-label" style="color:#34d399">1D</span>
    <div class="size-ctrl"><label>Canvas Width</label>
      <button class="step-btn" onclick="stepSize('1d','width',-40)">−</button>
      <span class="size-val" id="val-1d-width">500px</span>
      <button class="step-btn" onclick="stepSize('1d','width',40)">+</button></div>
    <div class="size-ctrl"><label>Canvas Height</label>
      <button class="step-btn" onclick="stepSize('1d','h',-30)">−</button>
      <span class="size-val" id="val-1d-h">320px</span>
      <button class="step-btn" onclick="stepSize('1d','h',30)">+</button></div>
    <button class="btn-sm" onclick="applyAll('1d')">Apply to all 1D</button>
  </div>
  <div class="tf-size-group">
    <span class="tf-size-label" style="color:#fbbf24">1W</span>
    <div class="size-ctrl"><label>Canvas Width</label>
      <button class="step-btn" onclick="stepSize('1w','width',-40)">−</button>
      <span class="size-val" id="val-1w-width">500px</span>
      <button class="step-btn" onclick="stepSize('1w','width',40)">+</button></div>
    <div class="size-ctrl"><label>Canvas Height</label>
      <button class="step-btn" onclick="stepSize('1w','h',-30)">−</button>
      <span class="size-val" id="val-1w-h">320px</span>
      <button class="step-btn" onclick="stepSize('1w','h',30)">+</button></div>
    <button class="btn-sm" onclick="applyAll('1w')">Apply to all 1W</button>
  </div>
  <span style="font-size:10px;color:var(--muted)">Each chart ends at its Accepted Date (cyan marker). Use ◀ Prev / Next ▶ on a chart to step one candle at a time and see what happened after (the label shows +N candles after accepted) — your zoom is kept while stepping. ⟲ jumps back to the accepted date. ↻ reloads that chart from its saved file (and regenerates it if the file is missing / broken / doesn't reach the accepted date); Shift+↻ forces a fresh re-download (1D = 1 year before the accepted date). The Weekly Line chart follows the 1W chart. Charts render automatically as you scroll (50 rows per page). Search accepts several names separated by comma/space.</span>
</div>

<div id="colHeader">
  <div class="ch-info">Stock</div>
  <div class="ch-tf" style="width:520px">1 Day · up to Accepted Date</div>
  <div class="ch-tf" style="width:520px">Weekly Line · up to Accepted Date</div>
  <div class="ch-tf" style="width:520px">1 Week · up to Accepted Date</div>
</div>

<div id="stockTable"></div>
<div id="emptyMsg">No charts match your search / filters.</div>
<div id="moreWrap" style="display:none">
  <button class="btn btn-load" id="btnMore" onclick="viewMore()">⬇ View more</button>
  <span id="moreInfo"></span>
</div>

<script>
const ENTRIES = {{ entries | tojson }};
const TF_LIST = ['1d','1w'];
const PAGE_SIZE = 50;
const INITIAL_DAYS_1D = 365;     // 1D charts open showing ~1 year before the accepted candle
const entryMap = {};
// all events (rows) of each stock, newest accepted first (ENTRIES is already ordered that way)
const eventsByName = {};
ENTRIES.forEach(e => { (eventsByName[e.name] = eventsByName[e.name] || []).push(e); });
const curEvent = {};       // row id -> event currently shown on the chart

// Which event is "current" at the cursor candle?
//  - at or before the row's own accepted candle → the row's own event (original)
//  - after it → the latest event whose accepted date has been reached
function eventAtDate(id, dateStr, cur, accI, tf) {
  const e = entryMap[id];
  if (!e.acc) return e;
  if (accI != null && cur === accI) return e;          // at the row's own accepted candle → always its own event
  // weekly candle is dated at week start, so compare against the end of that week
  const d = (tf === '1w') ? dateMinusDays(dateStr, -6) : dateStr;
  let best = null;
  for (const ev of (eventsByName[e.name] || [])) {
    if (!ev.acc || ev.acc > d) continue;               // this event's Acc date not reached yet
    if (!best || ev.acc > best.acc) best = ev;         // latest Acc date reached wins
  }
  return best || e;                                    // before every Acc date → fall back to own event
}

function setActiveEvent(id, evId) {
  const list = document.getElementById(`evlist-${id}`);
  if (!list) return;
  let active = null;
  list.querySelectorAll('.ev-item').forEach(el => {
    const on = +el.dataset.evid === evId;
    el.classList.toggle('current', on);
    if (on) active = el;
  });
  if (active) {   // scroll only the panel list (not the page) so the active event is visible
    if (active.offsetTop < list.scrollTop || active.offsetTop + active.offsetHeight > list.scrollTop + list.clientHeight)
      list.scrollTop = Math.max(0, active.offsetTop - 4);
  }
}

// show ~5 events, scroll beyond that
function fitEventList(id) {
  const list = document.getElementById(`evlist-${id}`);
  if (!list) return;
  const items = list.querySelectorAll('.ev-item');
  if (items.length <= 5) { list.style.maxHeight = ''; return; }
  const last = items[4];
  list.style.maxHeight = (last.offsetTop + last.offsetHeight + 2) + 'px';
}

function eventItemHTML(ev) {
  return `<div class="ev-item" data-evid="${ev.id}">
    <div class="ev-dates">
      <span><span class="ev-lbl">CM:</span> ${ev.cm || '—'}</span>
      <span><span class="ev-lbl">Acc:</span> <span class="ev-acc">${ev.acc || '—'}</span></span>
    </div>
    <div class="ev-badges">${tradeBadge(ev.trade)}${moveBadge(ev.updown)}${emaBadge(ev.vs200)}</div>
  </div>`;
}
ENTRIES.forEach(e => { entryMap[e.id] = e; });

const chartData = {};      // `${name}_${tf}` -> full saved data (shared by duplicate stocks)
const fundMap   = {};      // name -> fundamentals
const chartReg  = {};      // `${id}_${tf}` | `${id}_wline`
const cursors   = {};      // `${id}_${tf}` -> index of last visible candle
const accIdx    = {};      // `${id}_${tf}` -> index of accepted-date candle
const stDirMap  = {};      // `${id}_${tf}` -> 1 / -1 / 0
let tradeFilter = 'all', moveFilter = 'all', emaFilter = 'all', sortMode = 'default';
let searchTerms = [];      // lowercase search terms (OR-matched against stock name)

// pagination state: viewList = ALL entries after search+filter+sort; shownCount = how many are in the DOM
let viewList = [];
let shownCount = 0;

const sizeState = { '1d': { width:500, h:320 }, '1w': { width:500, h:320 } };
function wlineSize() { return sizeState['1w']; }

function classifyMove(v) {
  const s = (v || '').toLowerCase().trim();
  if (!s) return '';
  if (s.startsWith('up')) return 'up';
  if (s.startsWith('down')) {
    if (s.includes('not recovered')) return 'down_not_recovered';
    if (s.includes('recover'))       return 'down_recovered';
    return 'down_other';
  }
  return '';
}

function setStatus(msg) { document.getElementById('statusBadge').textContent = msg; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function shownEntries() { return viewList.slice(0, shownCount); }

function stepSize(tf, dim, delta) {
  if (dim === 'width') {
    sizeState[tf].width = Math.max(300, Math.min(3000, sizeState[tf].width + delta));
    document.getElementById(`val-${tf}-width`).textContent = sizeState[tf].width + 'px';
  } else {
    sizeState[tf].h = Math.max(120, Math.min(1200, sizeState[tf].h + delta));
    document.getElementById(`val-${tf}-h`).textContent = sizeState[tf].h + 'px';
  }
}

function applyAll(tf) {
  const keys = tf === '1w' ? ['1w', 'wline'] : [tf];
  for (const e of shownEntries()) {
    for (const k of keys) {
      const sz = k === 'wline' ? wlineSize() : sizeState[tf];
      const container = document.getElementById(`chartdiv-${e.id}-${k}`);
      if (container) { container.style.width = sz.width + 'px'; container.style.height = sz.h + 'px'; }
      const reg = chartReg[`${e.id}_${k}`];
      if (reg) { reg.chart.resize(sz.width, sz.h); reg.chart.timeScale().fitContent(); }
    }
  }
  setStatus(`Applied canvas size to all visible ${tf}${tf === '1w' ? ' + Weekly Line' : ''} charts`);
}

function makeStockLinks(name) {
  const screenerUrl = `https://www.screener.in/company/${name}/consolidated/`;
  const tvUrl       = `https://in.tradingview.com/symbols/NSE-${name}/`;
  return `
    <div class="stock-links">
      <a class="stock-link link-screener" href="${screenerUrl}" target="_blank" rel="noopener">📊 Screener</a>
      <a class="stock-link link-tv" href="${tvUrl}" target="_blank" rel="noopener">📈 TV</a>
    </div>`;
}

// ── Fundamentals (read-only, from cache file) ────────────────
async function fetchAllFundamentals() {
  try {
    const r = await fetch('/api/fundamentals_all');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) {
      if (data.error) continue;
      fundMap[name] = data;
    }
  } catch(e) {}
}

function renderFundamentals(id, d) {
  const el = document.getElementById(`fund-unified-${id}`);
  if (!el) return;
  const signal = d.signal || '—';
  const signalColor = {'Strong':'#4ade80','Moderate':'#facc15','Weak':'#fb923c','Avoid':'#f87171'}[signal] || '#9ca3af';
  const signalBg    = {'Strong':'#0f3320','Moderate':'#332b05','Weak':'#3a1f05','Avoid':'#3a1414'}[signal] || '#1a1a1a';
  const bdMap = {};
  (d.breakdown || []).forEach(b => { bdMap[b.metric] = b; });
  function verdict(metric) {
    const b = bdMap[metric]; if (!b) return '';
    const m = b.display.match(/(✅|⚠|❌|➖)\s*.+$/); return m ? ' ' + m[0] : '';
  }
  function fmtGrowth(g) { return g == null ? '' : ` (${g > 0 ? '+' : ''}${g}% YoY)`; }
  function row(label, val) {
    if (val == null || val === '—') return '';
    return `<div style="display:flex;gap:3px;align-items:baseline;font-size:9px;line-height:1.5">
      <span style="color:#9ca3af;font-weight:600;min-width:52px;flex-shrink:0">${label}</span>
      <span style="color:#e5e7eb;font-weight:500;word-break:break-word">${val}</span>
    </div>`;
  }
  const peVal   = d.pe   != null ? `${Number(d.pe).toFixed(1)}x${verdict('PE')}`   : null;
  const pbVal   = d.pb   != null ? `${Number(d.pb).toFixed(1)}x${verdict('PB')}`   : null;
  const pegVal  = d.peg  != null ? `${Number(d.peg).toFixed(2)}${d.peg_calculated ? ' ~est' : ''}${verdict('PEG')}` : null;
  const roeVal  = d.roe  != null ? `${d.roe}%${verdict('ROE')}`                    : null;
  const deVal   = d.de   != null ? `${Number(d.de).toFixed(2)}x${verdict('D/E')}`  : null;
  const marginVal = d.pm != null ? `${d.pm}%${verdict('Margin')}`                  : null;
  const patVal = d.pat_cr != null ? `₹${d.pat_cr}Cr${fmtGrowth(d.pat_growth ?? null)}${verdict('PAT Growth') || verdict('PAT (Qtr)')}` : null;
  const revVal = d.rev_cr != null ? `₹${d.rev_cr}Cr${fmtGrowth(d.rev_growth ?? null)}${verdict('Rev Growth') || verdict('Revenue')}` : null;
  const epsVal = d.eps_trend_signal && d.eps_trend_signal !== 'Insufficient Data'
    ? `${d.eps_trend_signal}${d.eps_cagr != null ? ` (${d.eps_cagr}% CAGR)` : ''}${verdict('EPS Trend')}` : null;
  const epsHistStr = (d.eps_history || []).map(e => `${e.year}:₹${e.eps}`).join(' → ');
  el.innerHTML = `
    <div style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;
      background:${signalBg};color:${signalColor};border:1px solid ${signalColor}40;
      display:inline-block;margin-bottom:3px">
      ${signal} (${d.score > 0 ? '+' : ''}${d.score})
    </div>
    ${row('MCap', d.mcap || null)}${row('PE', peVal)}${row('PB', pbVal)}${row('PEG', pegVal)}
    ${row('ROE', roeVal)}${row('D/E', deVal)}${row('PAT Qtr', patVal)}${row('Revenue', revVal)}
    ${row('EPS Trend', epsVal)}${row('EPS History', epsHistStr || null)}${row('Margin', marginVal)}`;
}

// ── Row scaffolding: ONE ROW PER ENTRY (duplicate stocks get a row each) ──
function tradeBadge(t) {
  if (!t) return '';
  let color = '#9ca3af', bg = '#1a1a1a', border = '#2a2a2a';
  if (t === 'TG Hit')      { color = '#4ade80'; bg = '#0f3320'; border = '#166534'; }
  else if (t === 'SL Hit') { color = '#f87171'; bg = '#3a1414'; border = '#7f1d1d'; }
  return `<span style="font-size:9px;font-weight:700;padding:0 5px;border-radius:3px;color:${color};background:${bg};border:1px solid ${border};white-space:nowrap">${t}</span>`;
}
function moveBadge(v) {
  const c = classifyMove(v);
  if (!c) return '';
  let color = '#9ca3af', bg = '#1a1a1a', border = '#2a2a2a';
  if (c === 'up')                      { color = '#4ade80'; bg = '#0f3320'; border = '#166534'; }
  else if (c === 'down_not_recovered') { color = '#f87171'; bg = '#3a1414'; border = '#7f1d1d'; }
  else if (c === 'down_recovered')     { color = '#fbbf24'; bg = '#332405'; border = '#5c4008'; }
  else if (c === 'down_other')         { color = '#fb923c'; bg = '#3a1f05'; border = '#7c2d12'; }
  return `<span style="font-size:9px;font-weight:700;padding:0 5px;border-radius:3px;color:${color};background:${bg};border:1px solid ${border};white-space:nowrap">${v}</span>`;
}
function emaBadge(v) {
  if (!v) return '';
  const above = v === 'Above 200EMA';
  const color = above ? '#4ade80' : '#f87171', bg = above ? '#0f3320' : '#3a1414', border = above ? '#166534' : '#7f1d1d';
  return `<span style="font-size:9px;font-weight:700;padding:0 5px;border-radius:3px;color:${color};background:${bg};border:1px solid ${border};white-space:nowrap">${above ? '▲' : '▼'} ${v}</span>`;
}

const REFRESH_TITLE = 'Refresh: reload from saved file (regenerates if missing / broken / date out of range). Shift+click = force fresh re-download';

function navCell(id, tf, label) {
  return `
    <div class="cell-label">
      <span id="stdir-${id}-${tf}" class="st-dir"></span>
      <span>${label}<span class="cur-info" id="cur-${id}-${tf}"></span></span>
      <div class="cell-label-btns">
        <button class="btn-sm btn-refresh" data-refresh="${id}-${tf}" onclick="refreshChart(${id},'${tf}',event)" title="${REFRESH_TITLE}">↻</button>
        <button class="btn-sm btn-nav" onclick="stepChart(${id},'${tf}',-1)">◀ Prev</button>
        <button class="btn-sm btn-nav" onclick="stepChart(${id},'${tf}',1)">Next ▶</button>
        <button class="btn-sm" onclick="resetChart(${id},'${tf}')" title="Back to accepted date">⟲</button>
      </div>
    </div>`;
}

function buildRow(e) {
  const id = e.id;
  const pendingMsg = 'Loading…';
  const row = document.createElement('div');
  row.className = 'stock-row';
  row.id = `row-${id}`;
  row.dataset.id = id;

  const ltpTxt = e.ltp != null ? '₹' + Number(e.ltp).toFixed(2) : '—';
  const pch = e.pchange;
  const chgTxt = pch != null ? `${pch >= 0 ? '+' : ''}${pch.toFixed(2)}%` : '—';
  const chgCls = pch != null ? (pch >= 0 ? 'chg-up' : 'chg-dn') : '';
  const volTxt = e.ltp != null ? 'Vol: ' + (e.vol || 0).toLocaleString() : '';

  row.innerHTML = `
    <div class="info-strip" id="info-${id}">
      <div class="info-name">${e.name}</div>
      <div class="pattern-badge" id="pattern-${id}" style="${e.pattern ? 'display:block' : ''}">${e.pattern || ''}</div>
      <div class="info-ltp" id="ltp-${id}">${ltpTxt}</div>
      <div class="info-chg ${chgCls}" id="chg-${id}">${chgTxt}</div>
      <div class="info-vol" id="vol-${id}">${volTxt}</div>
      ${makeStockLinks(e.name)}
            <div class="ev-wrap">
        <div class="ev-title">📅 Events (${(eventsByName[e.name] || []).filter(x => x.cm || x.acc).length})</div>
        <div class="ev-list" id="evlist-${id}">
          ${(eventsByName[e.name] || []).filter(x => x.cm || x.acc).map(eventItemHTML).join('') || '<div class="news-loading">No events</div>'}
        </div>
      </div>
      <div class="fund-header"><span class="fund-header-label">📊 Fundamentals</span></div>
      <div id="fund-unified-${id}" style="margin-top:3px;font-size:9px;line-height:1.5;color:#e5e7eb;">
        <div class="news-loading">No cached fundamentals</div>
      </div>
    </div>`;

  const c1d = document.createElement('div');
  c1d.className = 'chart-cell'; c1d.id = `cell-${id}-1d`;
  c1d.innerHTML = navCell(id, '1d', '1 Day') + `
    <div class="chart-wrap" id="chartdiv-${id}-1d" style="width:${sizeState['1d'].width}px;height:${sizeState['1d'].h}px">
      <div class="chart-pending" style="width:100%;height:100%">${pendingMsg}</div>
    </div>`;
  row.appendChild(c1d);

  const wcell = document.createElement('div');
  wcell.className = 'chart-cell'; wcell.id = `cell-${id}-wline`;
  wcell.innerHTML = `
    <div class="cell-label">
      <span></span><span>Weekly Line</span>
      <div class="cell-label-btns">
        <button class="btn-sm btn-refresh" data-refresh="${id}-1w" onclick="refreshChart(${id},'1w',event)" title="${REFRESH_TITLE}">↻</button>
        <button class="btn-sm" id="drawbtn-${id}" onclick="toggleDrawMode(${id}, this)" title="Draw a trend line on this chart">✏ Draw</button>
        <button class="btn-sm" onclick="clearDrawings(${id})" title="Clear drawn lines">✕</button>
      </div>
    </div>
    <div class="chart-wrap" id="chartdiv-${id}-wline" style="width:${wlineSize().width}px;height:${wlineSize().h}px">
      <div class="chart-pending" style="width:100%;height:100%">${pendingMsg}</div>
    </div>`;
  row.appendChild(wcell);

  const c1w = document.createElement('div');
  c1w.className = 'chart-cell'; c1w.id = `cell-${id}-1w`;
  c1w.innerHTML = navCell(id, '1w', '1 Week') + `
    <div class="chart-wrap" id="chartdiv-${id}-1w" style="width:${sizeState['1w'].width}px;height:${sizeState['1w'].h}px">
      <div class="chart-pending" style="width:100%;height:100%">${pendingMsg}</div>
    </div>`;
  row.appendChild(c1w);

  return row;
}

// ── helpers ──────────────────────────────────────────────────
function disposeChart(key) {
  const reg = chartReg[key];
  if (reg && reg.chart) { try { reg.chart.remove(); } catch(e) {} }
  delete chartReg[key];
}
function cleanSeriesData(arr) {
  const ok = arr.filter(p => p && p.time != null &&
    Object.values(p).every(v => v !== null && !Number.isNaN(v)));
  ok.sort((a, b) => (a.time > b.time ? 1 : a.time < b.time ? -1 : 0));
  const out = [];
  for (const p of ok) {
    if (out.length && out[out.length - 1].time === p.time) out[out.length - 1] = p;
    else out.push(p);
  }
  return out;
}
function lastIdxLE(times, s) {          // last index whose date <= s  (binary search, YYYY-MM-DD strings)
  let lo = 0, hi = times.length - 1, r = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= s) { r = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return r;
}
function dateMinusDays(dateStr, days) {   // 'YYYY-MM-DD' minus N days → 'YYYY-MM-DD'
  const d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

// ── Zoom keeping ──
// Before a chart is re-drawn (Next / Prev / Reset) we remember its visible logical range + cursor,
// then restore it on the new chart, shifted by however many candles the cursor moved.
function captureView(key) {
  const reg = chartReg[key];
  if (!reg || !reg.chart) return null;
  try {
    const range = reg.chart.timeScale().getVisibleLogicalRange();
    if (!range) return null;
    return { from: range.from, to: range.to, cur: reg.cur };
  } catch (err) { return null; }
}
function restoreView(chart, view, cur) {
  let from = view.from, to = view.to;
  // if the previous view was showing the latest candle, follow the cursor so the new candle stays visible
  if (view.cur != null && to >= view.cur - 0.5) {
    const shift = cur - view.cur;
    from += shift; to += shift;
  }
  try { chart.timeScale().setVisibleLogicalRange({ from, to }); }
  catch (err) { chart.timeScale().fitContent(); }
}

// Pivots recomputed from the VISIBLE candles only (no look-ahead):
// 1D → previous month, 1W → previous calendar year. Same Fibonacci formula as before.
function calcPivots(T, C, tf) {
  const n = T.length; if (n < 2) return null;
  const keyOf = t => tf === '1d' ? (+t.slice(0,4)) * 12 + (+t.slice(5,7)) : (+t.slice(0,4));
  const cur = keyOf(T[n-1]);
  let best = -1;
  for (let i = 0; i < n; i++) { const k = keyOf(T[i]); if (k < cur && k > best) best = k; }
  if (best < 0) return null;
  let H = -Infinity, L = Infinity, Cl = null;
  for (let i = 0; i < n; i++) {
    if (keyOf(T[i]) === best) { H = Math.max(H, C[i].h); L = Math.min(L, C[i].l); Cl = C[i].c; }
  }
  const PP = (H + L + Cl) / 3, diff = H - L;
  return { PP, R1:PP+0.382*diff, R2:PP+0.618*diff, R3:PP+1.0*diff, S1:PP-0.382*diff, S2:PP-0.618*diff, S3:PP-1.0*diff };
}

// fresh=true → bypass both the browser cache and the server cache (re-read the json from disk)
async function getData(name, tf, fresh = false) {
  const k = `${name}_${tf}`;
  if (!fresh && chartData[k]) return chartData[k];
  const r = await fetch(`/api/chart?symbol=${encodeURIComponent(name)}&tf=${tf}${fresh ? '&fresh=1' : ''}`);
  const d = await r.json();
  if (!d.error) chartData[k] = d; else delete chartData[k];
  return d;
}

// Ask the server to download candles + rebuild indicators + overwrite the saved json
async function regenerateData(name, tf) {
  const k = `${name}_${tf}`;
  delete chartData[k];
  try {
    const r = await fetch(`/api/regenerate?symbol=${encodeURIComponent(name)}&tf=${tf}`);
    const d = await r.json();
    if (!d.error) chartData[k] = d;
    return d;
  } catch (err) {
    return { error: 'Regenerate request failed: ' + err };
  }
}

function coversAccepted(d, e) {
  return !e.acc || lastIdxLE(d.times, e.acc) >= 0;
}

function showPending(id, tf, msg) {
  const c = document.getElementById(`chartdiv-${id}-${tf}`);
  if (c) c.innerHTML = `<div class="chart-pending" style="width:100%;height:100%">${msg}</div>`;
  if (tf === '1w') {
    const w = document.getElementById(`chartdiv-${id}-wline`);
    if (w) w.innerHTML = `<div class="chart-pending" style="width:100%;height:100%">${msg}</div>`;
  }
}

// Load one row/timeframe: cut the chart at the accepted date (fresh view, not keeping zoom)
async function loadRow(id, tf) {
  const e = entryMap[id];
  if (!document.getElementById(`row-${id}`)) return false;     // row no longer on screen
  const d = await getData(e.name, tf);
  if (!document.getElementById(`row-${id}`)) return false;     // removed while fetching
  if (d.error) { showPending(id, tf, d.error); return false; }
  const times = d.times;
  let ai = e.acc ? lastIdxLE(times, e.acc) : times.length - 1;
  if (ai < 0) { showPending(id, tf, 'Accepted date is before the saved data range'); return false; }
  accIdx[`${id}_${tf}`] = ai;
  cursors[`${id}_${tf}`] = ai;
  renderChart(id, tf, false);
  return true;
}

// ── Per-chart refresh (↻) ────────────────────────────────────
//  normal click : re-read the saved file; if it's missing / broken / doesn't reach the accepted date → regenerate it
//  Shift+click  : always regenerate (fresh download), keeps the old file as .bak
async function refreshChart(id, tf, ev) {
  const e = entryMap[id];
  if (!e) return;
  const force = !!(ev && ev.shiftKey);
  const btns = document.querySelectorAll(`[data-refresh="${id}-${tf}"]`);
  btns.forEach(b => b.classList.add('loading'));
  try {
    let d = null;
    if (!force) {
      setStatus(`${e.name} ${tf}: reloading saved file…`);
      d = await getData(e.name, tf, true);
    }
    if (force || d.error || !coversAccepted(d, e)) {
      setStatus(`${e.name} ${tf}: regenerating chart data…`);
      d = await regenerateData(e.name, tf);
    }
    if (d.error) {
      if (!chartReg[`${id}_${tf}`]) showPending(id, tf, d.error);   // don't wipe a chart that is still showing fine
      setStatus(`${e.name} ${tf}: refresh failed — ${d.error}`);
      return;
    }
    // every visible row of the same stock shares this file → redraw all of them
    let drawn = 0;
    for (const r of shownEntries()) {
      if (r.name !== e.name) continue;
      try { if (await loadRow(r.id, tf)) drawn++; } catch(err) { console.error('Refresh draw failed', r.name, tf, err); }
    }
    setStatus(drawn ? `${e.name} ${tf}: refreshed ✓ (${d.times.length} candles, ${d.times[0]} → ${d.times[d.times.length - 1]})`
                    : `${e.name} ${tf}: data refreshed but chart still can't be drawn (accepted date outside data?)`);
  } catch(err) {
    console.error(err);
    setStatus(`${e.name} ${tf}: refresh error — ${err}`);
  } finally {
    btns.forEach(b => b.classList.remove('loading'));
  }
}

function stepChart(id, tf, delta) {
  const key = `${id}_${tf}`;
  const e = entryMap[id];
  const d = chartData[`${e.name}_${tf}`];
  if (!d || cursors[key] == null) { setStatus('Chart not loaded yet'); return; }
  const next = Math.max(0, Math.min(d.times.length - 1, cursors[key] + delta));
  if (next === cursors[key]) { setStatus(delta > 0 ? 'No more saved candles after this' : 'Start of saved data'); return; }
  cursors[key] = next;
  renderChart(id, tf, true);            // keep zoom
}
function resetChart(id, tf) {
  const key = `${id}_${tf}`;
  if (accIdx[key] == null) return;
  cursors[key] = accIdx[key];
  renderChart(id, tf, true);            // keep zoom
}
function resetAll() {
  for (const e of shownEntries()) for (const tf of TF_LIST) {
    const key = `${e.id}_${tf}`;
    if (accIdx[key] != null && chartData[`${e.name}_${tf}`]) { cursors[key] = accIdx[key]; renderChart(e.id, tf, true); }
  }
  setStatus('All visible charts reset to their accepted dates');
}

// ── Chart building with lightweight-charts (sliced up to cursor) ──
function renderChart(id, tf, keepZoom = false) {
  const e = entryMap[id], key = `${id}_${tf}`;
  const data = chartData[`${e.name}_${tf}`];
  const container = document.getElementById(`chartdiv-${id}-${tf}`);
  if (!container || !data) return;
  const cur = cursors[key], n = cur + 1;
  const prevView = keepZoom ? captureView(key) : null;   // must be captured BEFORE the old chart is disposed
  disposeChart(key);
  container.innerHTML = '';
  container.style.width  = sizeState[tf].width + 'px';
  container.style.height = sizeState[tf].h + 'px';

  const T = data.times.slice(0, n);
  const C = data.candles.slice(0, n);
      const ev = eventAtDate(id, T[cur], cur, accIdx[key], tf);
  if (tf === '1d' || !chartReg[`${id}_1d`]) curEvent[id] = ev;   // 1D chart drives the current event

  const chart = LightweightCharts.createChart(container, {
    width: sizeState[tf].width, height: sizeState[tf].h,
    layout: { background: { color: '#000000' }, textColor: '#d1d5db', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.06)' }, horzLines: { color: 'rgba(255,255,255,0.06)' } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { borderVisible: false, timeVisible: false, secondsVisible: false, rightOffset: 2,
      fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#16a34a', downColor: '#dc2626',
    borderUpColor: '#16a34a', borderDownColor: '#dc2626',
    wickUpColor: '#16a34a', wickDownColor: '#dc2626',
  });
  candleSeries.setData(cleanSeriesData(C.map((c, i) => ({ time: T[i], open: c.o, high: c.h, low: c.l, close: c.c }))));

  const volSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const volumes = (data.volume || []).slice(0, n);
  volSeries.setData(cleanSeriesData(volumes.map((v, i) => ({
    time: T[i], value: v.y, color: v.up ? 'rgba(22,163,74,0.45)' : 'rgba(220,38,38,0.45)'
  }))));

  const mk = (arr, color, w) => {
    const s = chart.addLineSeries({ color, lineWidth:w, priceLineVisible:false, lastValueVisible:false });
    s.setData(cleanSeriesData((arr || []).slice(0, n).map((v, i) => ({ time: T[i], value: v })).filter(p => p.value != null)));
    return s;
  };
  const ema20Series  = mk(data.ema20,  '#fbbf24', 1);
  const ema10Series  = mk(data.ema10,  '#a5b4fc', 1);
  const ema200Series = mk(data.ema200, '#ffffff', 2);

  const stSeriesList = [];
  function addSegments(arr, color) {
    const a = (arr || []).slice(0, n);
    let seg = [];
    const flush = () => {
      if (!seg.length) return;
      const s = chart.addLineSeries({ color, lineWidth:2, priceLineVisible:false, lastValueVisible:false });
      s.setData(seg); stSeriesList.push(s); seg = [];
    };
    for (let i = 0; i < a.length; i++) {
      if (a[i] != null) seg.push({ time: T[i], value: a[i] }); else flush();
    }
    flush();
  }
  addSegments(data.st_green, '#16a34a');
  addSegments(data.st_red,   '#dc2626');

  const priceLines = [];
  const pv = calcPivots(T, C, tf);
  if (pv) {
    const pvColors = {PP:'#cbd5e1',R1:'#22c55e',R2:'#16a34a',R3:'#15803d',S1:'#ef4444',S2:'#dc2626',S3:'#b91c1c'};
    Object.entries(pv).forEach(([k, v]) => {
      priceLines.push(candleSeries.createPriceLine({
        price: v, color: pvColors[k] || '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: false, title: '',
      }));
    });
  }

    // Markers follow the CURRENT event only (CM amber + ACC cyan); old event's markers disappear on redraw
  if (tf === '1d') {
    const markers = [];
    if (ev.cm) {
      const ci = lastIdxLE(T, ev.cm);
      if (ci >= 0 && T[ci] === ev.cm) markers.push({ time: T[ci], position: 'belowBar', color: '#d97706', shape: 'arrowUp', text: 'CM' });
    }
    if (ev.acc) {
      const ai2 = lastIdxLE(T, ev.acc);
      if (ai2 >= 0) markers.push({ time: T[ai2], position: 'aboveBar', color: '#22d3ee', shape: 'arrowDown', text: 'ACC' });
    }
    if (markers.length) {
      markers.sort((a, b) => (a.time > b.time ? 1 : a.time < b.time ? -1 : 0));
      candleSeries.setMarkers(markers);
    }
  }

  // ── visible range ──
  if (prevView) {
    restoreView(chart, prevView, cur);                       // Next / Prev / Reset → keep the user's zoom
  } else if (tf === '1d') {
    // first draw of a 1D chart → show about 1 year before the accepted candle (older saved data stays reachable by zoom-out)
    const fromIdx = lastIdxLE(T, dateMinusDays(T[cur], INITIAL_DAYS_1D));
    if (fromIdx > 0) {
      try { chart.timeScale().setVisibleLogicalRange({ from: fromIdx, to: cur + 3 }); }
      catch (err) { chart.timeScale().fitContent(); }
    } else {
      chart.timeScale().fitContent();                        // less than 1 year of data → show everything we have
    }
  } else {
    chart.timeScale().fitContent();
  }

  chartReg[key] = { chart, candleSeries, volSeries, ema20Series, ema10Series, ema200Series, stSeriesList, priceLines, cur };

  // Supertrend direction at the cursor candle
  const g = (data.st_green || [])[cur], r = (data.st_red || [])[cur];
  stDirMap[key] = g != null ? 1 : (r != null ? -1 : 0);
  updateSTDir(id, tf);
  updateCurInfo(id, tf, T[cur]);
  updateQuote(id);
      setActiveEvent(id, curEvent[id].id);

  if (tf === '1w') renderLineChart(id, T, C, keepZoom);
}

function updateCurInfo(id, tf, dateStr) {
  const el = document.getElementById(`cur-${id}-${tf}`);
  if (!el) return;

  const ev = curEvent[id] || entryMap[id];
  const key = `${id}_${tf}`;
  const off = cursors[key] - (accIdx[key] ?? cursors[key]);
  const unit = tf === '1d' ? 'd' : 'w';

  let tag;
  if (!ev.acc) tag = '';
  else if (off === 0) tag = ' · Acc date';
  else tag = ` · Acc ${off > 0 ? '+' : ''}${off}${unit}`;

  el.textContent = `${dateStr}${tag}`;
}

function updateSTDir(id, tf) {
  const el = document.getElementById(`stdir-${id}-${tf}`);
  if (!el) return;
  const dir = stDirMap[`${id}_${tf}`];
  if      (dir === 1)  { el.className='st-dir st-bull'; el.textContent='▲ Bull'; }
  else if (dir === -1) { el.className='st-dir st-bear'; el.textContent='▼ Bear'; }
  else                 { el.className='st-dir'; el.textContent=''; }
}

// Info strip follows the last visible candle (1D preferred, else 1W)
function updateQuote(id) {
  const e = entryMap[id];
  let tf = (chartData[`${e.name}_1d`] && cursors[`${id}_1d`] != null) ? '1d' : '1w';
  const d = chartData[`${e.name}_${tf}`], cur = cursors[`${id}_${tf}`];
  if (!d || cur == null) return;
  const c = d.candles[cur];
  const prev = cur > 0 ? d.candles[cur - 1].c : c.o;
  const change = c.c - prev;
  const pchange = prev ? (change / prev) * 100 : 0;
  const volume = (d.volume && d.volume[cur]) ? d.volume[cur].y : 0;
  const ltpEl = document.getElementById(`ltp-${id}`);
  const chgEl = document.getElementById(`chg-${id}`);
  const volEl = document.getElementById(`vol-${id}`);
  if (!ltpEl) return;
  ltpEl.textContent = '₹' + c.c.toFixed(2);
  chgEl.textContent = `${pchange >= 0 ? '+' : ''}${pchange.toFixed(2)}%`;
  chgEl.className = 'info-chg ' + (pchange >= 0 ? 'chg-up' : 'chg-dn');
  volEl.textContent = 'Vol: ' + (volume || 0).toLocaleString();
}

// ── Weekly Line Chart (plain close line, follows the 1W cursor) ──
const drawState      = {};
const weeklyTimesMap = {};
const dragStateMap   = {};

function getDrawState(id) {
  if (!drawState[id]) drawState[id] = { active: false, pending: null, lines: [] };
  return drawState[id];
}

function renderLineChart(id, T, C, keepZoom = false) {
  const key = `${id}_wline`;
  const container = document.getElementById(`chartdiv-${id}-wline`);
  if (!container) return;
  const prevView = keepZoom ? captureView(key) : null;
  disposeChart(key);
  container.innerHTML = '';
  const sz = wlineSize();
  container.style.width  = sz.width + 'px';
  container.style.height = sz.h + 'px';

  const chart = LightweightCharts.createChart(container, {
    width: sz.width, height: sz.h,
    layout: { background: { color: '#000000' }, textColor: '#d1d5db', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.06)' }, horzLines: { color: 'rgba(255,255,255,0.06)' } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
  });
  const lineSeries = chart.addLineSeries({ color: '#60a5fa', lineWidth: 2, priceLineVisible: true, lastValueVisible: true });
  lineSeries.setData(cleanSeriesData(C.map((c, i) => ({ time: T[i], value: c.c })).filter(p => p.value != null)));

  const cur = T.length - 1;
  if (prevView) restoreView(chart, prevView, cur);
  else chart.timeScale().fitContent();

  weeklyTimesMap[id] = T;
  chartReg[key] = { chart, lineSeries, container, drawnSeries: [], cur };
  const st = getDrawState(id);
  st.lines.forEach(line => { line.series = null; _paintExtendedLine(id, line); });
  setupDrawInteractions(id, chart, lineSeries, container);
  updateDrawButtonUI(id);
}

function _computeExtendedLineData(id, p1, p2) {
  const times = weeklyTimesMap[id] || [];
  if (!times.length) return [];
  const dLogical = p2.logical - p1.logical;
  if (Math.abs(dLogical) < 1e-6) {
    const i1 = Math.min(times.length - 1, Math.max(0, Math.round(p1.logical)));
    const i2 = Math.min(times.length - 1, Math.max(0, Math.round(p2.logical)));
    return [{ time: times[i1], value: p1.price }, { time: times[i2], value: p2.price }];
  }
  const slope = (p2.price - p1.price) / dLogical;
  const data = [];
  for (let i = 0; i < times.length; i++) data.push({ time: times[i], value: p1.price + slope * (i - p1.logical) });
  return data;
}

function _paintExtendedLine(id, line) {
  const reg = chartReg[`${id}_wline`];
  if (!reg) return;
  if (line.series) { try { reg.chart.removeSeries(line.series); } catch(e) {} }
  const series = reg.chart.addLineSeries({
    color: '#f472b6', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
  });
  series.setData(_computeExtendedLineData(id, line.p1, line.p2));
  const times = weeklyTimesMap[id] || [];
  const i1 = Math.min(times.length - 1, Math.max(0, Math.round(line.p1.logical)));
  const i2 = Math.min(times.length - 1, Math.max(0, Math.round(line.p2.logical)));
  series.setMarkers([
    { time: times[i1], position: 'inBar', color: '#f472b6', shape: 'circle', text: '' },
    { time: times[i2], position: 'inBar', color: '#f472b6', shape: 'circle', text: '' },
  ]);
  line.series = series;
  reg.drawnSeries.push(series);
}

function setupDrawInteractions(id, chart, lineSeries, container) {
  chart.subscribeClick(param => {
    const st = getDrawState(id);
    if (!st.active) return;
    if (!param || param.logical == null || !param.point) return;
    const price = lineSeries.coordinateToPrice(param.point.y);
    if (price == null) return;
    const point = { logical: param.logical, price };
    if (!st.pending) {
      st.pending = point;
      setStatus(`${entryMap[id].name}: first point set — click the end point of the line`);
    } else {
      const line = { p1: st.pending, p2: point, series: null };
      st.pending = null;
      st.lines.push(line);
      _paintExtendedLine(id, line);
      setStatus(`${entryMap[id].name}: extended line drawn — drag either dot to adjust it`);
    }
  });

  const HIT_RADIUS = 10;
  function findHandleNear(x, y) {
    const st = getDrawState(id);
    for (let li = 0; li < st.lines.length; li++) {
      for (const pk of ['p1', 'p2']) {
        const p  = st.lines[li][pk];
        const px = chart.timeScale().logicalToCoordinate(p.logical);
        const py = lineSeries.priceToCoordinate(p.price);
        if (px == null || py == null) continue;
        if (Math.hypot(px - x, py - y) <= HIT_RADIUS) return { lineIndex: li, pointKey: pk };
      }
    }
    return null;
  }
  container.addEventListener('mousedown', (ev) => {
    if (!getDrawState(id).active) return;
    const rect = container.getBoundingClientRect();
    const hit = findHandleNear(ev.clientX - rect.left, ev.clientY - rect.top);
    if (hit) { dragStateMap[id] = hit; ev.stopPropagation(); }
  });
  container.addEventListener('mousemove', (ev) => {
    const drag = dragStateMap[id];
    if (!drag) return;
    const rect = container.getBoundingClientRect();
    const logical = chart.timeScale().coordinateToLogical(ev.clientX - rect.left);
    const price   = lineSeries.coordinateToPrice(ev.clientY - rect.top);
    if (logical == null || price == null) return;
    const line = getDrawState(id).lines[drag.lineIndex];
    if (!line) return;
    line[drag.pointKey] = { logical, price };
    _paintExtendedLine(id, line);
  });
  const endDrag = () => { dragStateMap[id] = null; };
  container.addEventListener('mouseup', endDrag);
  container.addEventListener('mouseleave', endDrag);
}

function toggleDrawMode(id, btn) {
  const st = getDrawState(id);
  st.active = !st.active;
  st.pending = null;
  const reg = chartReg[`${id}_wline`];
  if (reg && reg.chart) reg.chart.applyOptions({ handleScroll: !st.active, handleScale: !st.active });
  updateDrawButtonUI(id);
  setStatus(st.active ? `${entryMap[id].name}: Draw mode ON — click two points to place an extended line` : `${entryMap[id].name}: Draw mode OFF`);
}
function updateDrawButtonUI(id) {
  const st = getDrawState(id);
  const btn = document.getElementById(`drawbtn-${id}`);
  const wrap = document.getElementById(`chartdiv-${id}-wline`);
  if (btn) btn.classList.toggle('active', st.active);
  if (wrap) wrap.classList.toggle('draw-mode', st.active);
}
function clearDrawings(id) {
  const reg = chartReg[`${id}_wline`];
  const st  = getDrawState(id);
  st.lines = []; st.pending = null; dragStateMap[id] = null;
  if (reg && reg.drawnSeries) {
    reg.drawnSeries.forEach(s => { try { reg.chart.removeSeries(s); } catch(e) {} });
    reg.drawnSeries = [];
  }
  setStatus(`${entryMap[id].name}: drawings cleared`);
}

// ── Lazy chart loading: charts render only when their row scrolls near the viewport ──
const loadQueue = [];
let queueRunning = false;

async function runQueue() {
  queueRunning = true;
  while (loadQueue.length) {
    const id = loadQueue.shift();
    for (const tf of TF_LIST) {
      if (!document.getElementById(`row-${id}`)) break;      // row gone (filter/sort/search changed)
      if (chartReg[`${id}_${tf}`]) continue;                 // already drawn
      try { await loadRow(id, tf); } catch(err) { console.error('Load failed', id, tf, err); }
      await sleep(0);                                        // let the browser breathe between charts
    }
  }
  queueRunning = false;
}
function enqueueLoad(id) {
  loadQueue.push(id);
  if (!queueRunning) runQueue();
}

const rowObserver = new IntersectionObserver((items) => {
  for (const it of items) {
    if (!it.isIntersecting) continue;
    rowObserver.unobserve(it.target);
    enqueueLoad(+it.target.dataset.id);
  }
}, { rootMargin: '500px 0px' });

// ── Search + Filtering / sorting (works on ALL entries) + pagination (DOM only holds 50 at a time) ──
const FUND_SIGNAL_RANK = { 'Strong': 0, 'Moderate': 1, 'Weak': 2, 'Avoid': 3 };

function parseSearch(text) {
  return (text || '').toLowerCase().split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
}

function passesFilters(e) {
  if (searchTerms.length) {
    const nm = e.name.toLowerCase();
    if (!searchTerms.some(t => nm.includes(t))) return false;
  }
  if (tradeFilter !== 'all' && e.trade !== tradeFilter) return false;
  if (emaFilter !== 'all' && e.vs200 !== emaFilter) return false;
  if (moveFilter !== 'all') {
    const m = classifyMove(e.updown);
    if (moveFilter === 'up'                      && m !== 'up') return false;
    if (moveFilter === 'down'                    && !m.startsWith('down')) return false;
    if (moveFilter === 'down_not_recovered'      && m !== 'down_not_recovered') return false;
    if (moveFilter === 'down_recovered'          && m !== 'down_recovered') return false;
  }
  return true;
}

function shuffleInPlace(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
}

function sortComparator(ea, eb) {
  const tie = ea.id - eb.id;
  if (sortMode.startsWith('pattern:')) {
    const want = sortMode.slice('pattern:'.length);
    const ma = ea.pattern === want, mb = eb.pattern === want;
    if (ma && !mb) return -1;
    if (!ma && mb) return 1;
    return tie;
  }
  switch (sortMode) {
    case 'price_asc':   return (ea.ltp||0) - (eb.ltp||0) || tie;
    case 'price_desc':  return (eb.ltp||0) - (ea.ltp||0) || tie;
    case 'change_asc':  return (ea.pchange||0) - (eb.pchange||0) || tie;
    case 'change_desc': return (eb.pchange||0) - (ea.pchange||0) || tie;
    case 'cond_date':   return (eb.cm||'').localeCompare(ea.cm||'') || tie;
    case 'accept_date': return (eb.acc||'').localeCompare(ea.acc||'') || tie;
    case 'ema_dist_asc': {
      const hasA = ea.ema_dist != null, hasB = eb.ema_dist != null;
      if (hasA && !hasB) return -1;
      if (!hasA && hasB) return 1;
      if (!hasA && !hasB) return tie;
      return Math.abs(ea.ema_dist) - Math.abs(eb.ema_dist) || tie;
    }
    case 'never_below_10ema': {
      const rank = v => v === 'Yes' ? 0 : v === 'No' ? 1 : 2;
      return rank(ea.never_below) - rank(eb.never_below) || tie;
    }
    case 'above_st_1d': {
      const a = ea.st1d === 1, b = eb.st1d === 1;
      if (a && !b) return -1;
      if (!a && b) return 1;
      return tie;
    }
    case 'above_st_1w': {
      const a = ea.st1w === 1, b = eb.st1w === 1;
      if (a && !b) return -1;
      if (!a && b) return 1;
      return tie;
    }
    case 'fund_score_desc': {
      const fa = fundMap[ea.name], fb = fundMap[eb.name];
      const va = (fa && !fa.error && fa.signal != null), vb = (fb && !fb.error && fb.signal != null);
      if (va && !vb) return -1;
      if (!va && vb) return 1;
      if (!va && !vb) return tie;
      const ra = FUND_SIGNAL_RANK[fa.signal] ?? 2, rb = FUND_SIGNAL_RANK[fb.signal] ?? 2;
      if (ra !== rb) return ra - rb;
      return (fb.score || 0) - (fa.score || 0) || tie;
    }
    default: return tie;
  }
}

// Recompute the full searched+filtered+sorted list, then show only the first page
function refreshView(scrollTop = true) {
  viewList = ENTRIES.filter(passesFilters);
  if (sortMode === 'random') shuffleInPlace(viewList);
  else viewList.sort(sortComparator);
  shownCount = Math.min(PAGE_SIZE, viewList.length);
  renderTable();
  if (scrollTop) window.scrollTo(0, 0);
}

function renderTable() {
  rowObserver.disconnect();
  loadQueue.length = 0;
  Object.keys(chartReg).forEach(disposeChart);          // free every chart that's leaving the DOM
  const table = document.getElementById('stockTable');
  table.innerHTML = '';
  appendRows(0, shownCount);
  document.getElementById('emptyMsg').style.display = viewList.length ? 'none' : 'block';
  updateMoreUI();
  updateStatusSummary();
}

function appendRows(from, to) {
  const table = document.getElementById('stockTable');
  const frag = document.createDocumentFragment();
  const rows = [];
  for (let i = from; i < to; i++) {
    const e = viewList[i];
    const row = buildRow(e);
    frag.appendChild(row);
    rows.push(row);
    const f = fundMap[e.name];
    if (f) renderFundamentals(e.id, f);
  }
    table.appendChild(frag);
  for (let i = from; i < to; i++) {            // NEW
    const id = viewList[i].id;
    fitEventList(id);
    setActiveEvent(id, id);
  }
  rows.forEach(r => rowObserver.observe(r));
}

function viewMore() {
  const from = shownCount;
  shownCount = Math.min(shownCount + PAGE_SIZE, viewList.length);
  appendRows(from, shownCount);
  updateMoreUI();
  updateStatusSummary();
}

function updateMoreUI() {
  const wrap = document.getElementById('moreWrap');
  const remaining = viewList.length - shownCount;
  if (remaining > 0) {
    wrap.style.display = 'flex';
    const next = Math.min(PAGE_SIZE, remaining);
    document.getElementById('btnMore').textContent = `⬇ View more (next ${next})`;
    document.getElementById('moreInfo').textContent = `Showing ${shownCount} of ${viewList.length} · ${remaining} remaining`;
  } else {
    wrap.style.display = 'none';
  }
}

function updateStatusSummary() {
  const filtered = !(tradeFilter === 'all' && moveFilter === 'all' && emaFilter === 'all' && !searchTerms.length);
  if (!viewList.length) { setStatus('No charts match this search / filter'); return; }
  setStatus(filtered
    ? `Matches ${viewList.length} of ${ENTRIES.length} · showing ${shownCount}`
    : `Showing ${shownCount} of ${ENTRIES.length} charts`);
}

function reshuffle() {
  sortMode = 'random';
  document.getElementById('sortSelect').value = 'random';
  refreshView();
}

// ── Search box (debounced so typing never lags) ──
const searchInput = document.getElementById('searchInput');
const searchClear = document.getElementById('searchClear');
let searchTimer = null;

function applySearch() {
  searchTerms = parseSearch(searchInput.value);
  searchClear.style.display = searchInput.value ? 'block' : 'none';
  refreshView();
}
searchInput.addEventListener('input', () => {
  searchClear.style.display = searchInput.value ? 'block' : 'none';
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applySearch, 250);
});
searchInput.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') { clearTimeout(searchTimer); applySearch(); }
  else if (ev.key === 'Escape') { searchInput.value = ''; clearTimeout(searchTimer); applySearch(); }
});
searchClear.addEventListener('click', () => {
  searchInput.value = '';
  applySearch();
  searchInput.focus();
});

document.getElementById('tradeFilter').addEventListener('change', function() { tradeFilter = this.value; refreshView(); });
document.getElementById('moveFilter').addEventListener('change',  function() { moveFilter  = this.value; refreshView(); });
document.getElementById('emaFilter').addEventListener('change',   function() { emaFilter   = this.value; refreshView(); });
document.getElementById('sortSelect').addEventListener('change',  function() { sortMode    = this.value; refreshView(); });

// ── Load buttons (apply to the rows currently shown) ─────────
async function loadAll(tf) {
  const btn = document.getElementById(tf === '1d' ? 'btnLoad1d' : 'btnLoad1w');
  btn.classList.add('loading');
  const list = shownEntries();
  let i = 0;
  for (const e of list) {
    i++;
    setStatus(`Loading ${tf}: ${i}/${list.length} — ${e.name}`);
    if (chartReg[`${e.id}_${tf}`]) continue;
    try { await loadRow(e.id, tf); } catch(err) { console.error('Load failed', e.name, tf, err); }
    await sleep(0);
  }
  setStatus(`${tf} load complete ✓ (${list.length} visible rows)`);
  btn.classList.remove('loading');
}

// Render Blank Charts:
//   pass 1 → try to draw every blank chart from its saved file
//   pass 2 → anything STILL blank (no saved file / broken file / accepted date outside the data) is regenerated
//            (candles downloaded + indicators rebuilt + file saved) and then drawn
async function renderBlankCharts() {
  const btn = document.getElementById('btnFixBlank');
  btn.classList.add('loading');
  try {
    const isBlank = (id, tf) => !chartReg[`${id}_${tf}`] || (tf === '1w' && !chartReg[`${id}_wline`]);
    const blanks = [];
    for (const e of shownEntries()) for (const tf of TF_LIST) {
      if (isBlank(e.id, tf)) blanks.push([e.id, tf]);
    }
    if (!blanks.length) { setStatus('No blank charts found ✓'); return; }

    // pass 1: draw from saved files
    let i = 0;
    for (const [id, tf] of blanks) {
      i++;
      setStatus(`Rendering blanks: ${i}/${blanks.length} — ${entryMap[id].name} ${tf}`);
      try { await loadRow(id, tf); } catch(err) { console.error(err); }
      await sleep(0);
    }
    const left = blanks.filter(([id, tf]) => isBlank(id, tf));
    if (!left.length) { setStatus(`All ${blanks.length} blank charts rendered ✓`); return; }

    // pass 2: regenerate whatever is still blank (one download per stock+timeframe, even if the stock has several rows)
    const groups = new Map();
    for (const [id, tf] of left) {
      const name = entryMap[id].name;
      const k = name + '|' + tf;
      if (!groups.has(k)) groups.set(k, { name, tf, ids: [] });
      groups.get(k).ids.push(id);
    }
    let g = 0;
    const failed = [];
    for (const grp of groups.values()) {
      g++;
      setStatus(`Regenerating ${g}/${groups.size}: ${grp.name} ${grp.tf} …`);
      const d = await regenerateData(grp.name, grp.tf);
      if (d.error) {
        console.warn('Regenerate failed:', grp.name, grp.tf, d.error);
        failed.push(`${grp.name} ${grp.tf}`);
        continue;
      }
      for (const id of grp.ids) {
        try { await loadRow(id, grp.tf); } catch(err) { console.error(err); }
        await sleep(0);
      }
    }
    const still = blanks.filter(([id, tf]) => isBlank(id, tf));
    if (!still.length) {
      setStatus(`All ${blanks.length} blank charts rendered ✓ (${groups.size - failed.length} regenerated)`);
    } else {
      const names = [...new Set(still.map(([id, tf]) => `${entryMap[id].name} ${tf}`))];
      const shown = names.slice(0, 4).join(', ') + (names.length > 4 ? ` +${names.length - 4} more` : '');
      setStatus(`Done — ${still.length} still blank after regenerating: ${shown} (see each chart's message / Python console)`);
    }
  } finally {
    btn.classList.remove('loading');
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  await fetchAllFundamentals();     // so the fundamentals sort/rows have data from the start
  refreshView(false);
});
</script>
</body>
</html>
"""

# ── startup ───────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"✅ Loaded {len(STOCKS)} stocks")
    load_fundamentals_from_file()
    print("ℹ️  Charts are read from saved JSON files (1daycharts / weeklycharts). Missing/broken ones can be regenerated from the dashboard (↻ / Render Blank Charts).")

    time.sleep(1)

    def get_free_port(preferred=5000):
        try:
            s = socket.socket(); s.bind(("", preferred)); s.close(); return preferred
        except OSError:
            s = socket.socket(); s.bind(("", 0)); port = s.getsockname()[1]; s.close(); return port

    PORT = get_free_port(5000)

    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True),
        daemon=True
    ).start()
    print("✅ Flask server started")

    tunnel_process = subprocess.Popen(
        [CLOUDFLARED_CMD, "tunnel", "--protocol", "http2", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    public_url = None
    print("\n⏳ Starting Cloudflare tunnel…")

    _tunnel_url_holder = {"url": None}

    def _drain_tunnel_output():
        for line in tunnel_process.stdout:
            if _tunnel_url_holder["url"] is None:
                match = re.search(r"https://[-a-zA-Z0-9]+\.trycloudflare\.com", line)
                if match:
                    _tunnel_url_holder["url"] = match.group(0)
                    print(f"🔗 Tunnel found: {_tunnel_url_holder['url']}")
            print(f"[cloudflared] {line.rstrip()}")

    threading.Thread(target=_drain_tunnel_output, daemon=True).start()

    for _ in range(40):
        if _tunnel_url_holder["url"]:
            break
        time.sleep(0.5)

    public_url = _tunnel_url_holder["url"]
    print("⏳ Waiting for URL to become reachable…")

    if public_url:
        reachable = False
        for attempt in range(45):
            time.sleep(2)
            try:
                resp = _req_lib.get(public_url, timeout=6)
                if resp.status_code < 500:
                    reachable = True
                    break
            except Exception:
                pass
            if (attempt + 1) % 5 == 0:
                print(f"  … still waiting ({(attempt+1)*2}s elapsed)")
        print()
        if reachable:
            print("=" * 55)
            print("✅  URL IS LIVE — OPEN FROM ANY DEVICE:")
            print(f"    {public_url}")
            print("=" * 55)
        else:
            print(f"⚠️  URL did not respond within 90s: {public_url}")
            print("   It may still come up — try opening manually.")

    print("\n⏳ Server running… (keep this cell active)")
    while True:
        time.sleep(60)