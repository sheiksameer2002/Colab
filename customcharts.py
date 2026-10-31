
import subprocess
import sys
import shutil
import os


def _ensure(pip_name, import_name=None):
    """Install pip_name if import_name (or pip_name) isn't importable."""
    import_name = import_name or pip_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"📦 Installing missing package: {pip_name} ...")
        subprocess.run([sys.executable, "-m", "pip", "install", pip_name, "-q"], check=True)


_ensure("flask", "flask")
_ensure("yfinance", "yfinance")
_ensure("requests", "requests")
_ensure("openpyxl", "openpyxl")

BASE_DIR = r"C:\Users\sheik\PycharmProjects\MyProject"
os.makedirs(BASE_DIR, exist_ok=True)
CLOUDFLARED_EXE = os.path.join(BASE_DIR, "cloudflared.exe")


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

import io
import ast
import time
import random
import logging
import threading
import socket
import re
import requests as _req_lib
from datetime import datetime, timedelta, timezone
from bisect import bisect_right
from flask import Flask, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np

IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
logging.basicConfig(level=logging.ERROR)

# ── yfinance rate-limit protection ────────────────────────────
YF_MIN_INTERVAL = 0.8
YF_MAX_RETRIES  = 5
YF_BASE_BACKOFF = 5

_yf_lock      = threading.Lock()
_yf_last_call = [0.0]


def _yf_wait():
    with _yf_lock:
        now = time.time()
        elapsed = now - _yf_last_call[0]
        if elapsed < YF_MIN_INTERVAL:
            time.sleep(YF_MIN_INTERVAL - elapsed)
        _yf_last_call[0] = time.time()


def _yf_call(func, label=""):
    last_exc = None
    for attempt in range(YF_MAX_RETRIES):
        _yf_wait()
        try:
            return func()
        except Exception as e:
            last_exc = e
            msg = str(e)
            is_rate_limit = ("Too Many Requests" in msg) or ("Rate limited" in msg) or ("429" in msg)
            if is_rate_limit and attempt < YF_MAX_RETRIES - 1:
                delay = YF_BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 2)
                print(f"⏳ {label} rate limited, retrying in {delay:.1f}s (attempt {attempt + 1}/{YF_MAX_RETRIES})")
                time.sleep(delay)
                continue
            raise
    raise last_exc


# ── stock universe (for the multi-select combo) ───────────────
stocks_file = f"{BASE_DIR}/matched_stocks1.txt"
STOCKS = []


def _to_plain_name(s):
    s = s.strip()
    s = s.split(":")[1] if ":" in s else s
    s = s.replace("-EQ", "")
    if s.upper().endswith(".NS"):
        s = s[:-3]
    return s.strip().upper()


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

UNIVERSE      = [{"sym": s, "name": ""} for s in STOCKS]
UNIVERSE_LOCK = threading.Lock()


def load_nse_universe():
    """Background: download the full NSE equity list so typing can find ANY NSE stock.
    If NSE blocks it, the combo still has your matched_stocks1.txt list, and typing any
    NSE symbol + Enter still works."""
    urls = [
        "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    ]
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for u in urls:
        try:
            r = _req_lib.get(u, headers=hdr, timeout=20)
            if r.status_code != 200 or not r.text.strip():
                continue
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            if "SYMBOL" not in df.columns:
                continue
            name_col = "NAME OF COMPANY" if "NAME OF COMPANY" in df.columns else None
            items = {}
            for _, row in df.iterrows():
                sym = str(row["SYMBOL"]).strip().upper()
                if sym:
                    items[sym] = str(row[name_col]).strip() if name_col else ""
            for s in STOCKS:
                items.setdefault(s, "")
            with UNIVERSE_LOCK:
                UNIVERSE[:] = [{"sym": k, "name": v} for k, v in items.items()]
            print(f"✅ NSE universe loaded for search: {len(items)} symbols")
            return
        except Exception as e:
            print(f"⚠ NSE universe download failed ({u}): {e}")
    print("ℹ️  Using only matched_stocks1.txt for the combo (typing any NSE symbol + Enter still works).")


def search_stocks(q, limit=12, show_all=False):
    q = (q or "").strip().upper()
    matched = set(STOCKS)
    with UNIVERSE_LOCK:
        uni = list(UNIVERSE)
    if not q:
        names = {u["sym"]: u["name"] for u in uni}
        lst = STOCKS if show_all else STOCKS[:limit]
        return [{"sym": s, "name": names.get(s, ""), "listed": True} for s in lst]
    scored = []
    for it in uni:
        sym = it["sym"]; nm = (it["name"] or "").upper()
        if sym == q:            sc = 0
        elif sym.startswith(q): sc = 1
        elif q in sym:          sc = 2
        elif q in nm:           sc = 3
        else:
            continue
        scored.append((sc, 0 if sym in matched else 1, sym, it))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [{"sym": x[3]["sym"], "name": x[3]["name"], "listed": x[3]["sym"] in matched}
            for x in scored[:limit]]


def yf_symbol(name):
    return f"{name}.NS"


# ── condition-met Excel (⚡ markers on the 1D chart) ───────────
CONDITION_MET_EXCEL = f"{BASE_DIR}/1hour_Condition_Met_Stocks.xlsx"
condition_met_map = {}
accepted_date_map = {}


def _parse_excel_date(v):
    if pd.isna(v):
        return ""
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v).strip()[:10]


def load_condition_met_excel():
    global condition_met_map, accepted_date_map
    condition_met_map, accepted_date_map = {}, {}
    if not os.path.exists(CONDITION_MET_EXCEL):
        print(f"⚠ Condition met Excel not found: {CONDITION_MET_EXCEL}")
        return
    try:
        df = pd.read_excel(CONDITION_MET_EXCEL)
        df.columns = [c.strip() for c in df.columns]
        if "StockName" not in df.columns or "ConditionMetDate" not in df.columns:
            print(f"⚠ Excel columns not found. Got: {list(df.columns)}")
            return
        has_acc = "FinalAcceptedDate" in df.columns
        for _, row in df.iterrows():
            sym = _to_plain_name(str(row["StockName"]))
            d = _parse_excel_date(row["ConditionMetDate"])
            if not d:
                continue
            condition_met_map.setdefault(sym, [])
            if d not in condition_met_map[sym]:
                condition_met_map[sym].append(d)
            if has_acc:
                a = _parse_excel_date(row["FinalAcceptedDate"])
                if a:
                    accepted_date_map.setdefault(sym, []).append({"condition_met": d, "accepted": a})
        for sym in accepted_date_map:
            accepted_date_map[sym].sort(key=lambda x: x["accepted"], reverse=True)
        print(f"✅ Condition met data loaded for {len(condition_met_map)} stocks")
    except Exception as e:
        print(f"❌ Failed to load condition met Excel: {e}")


load_condition_met_excel()


# ── indicators ───────────────────────────────────────────────
def calc_atr(df, n=14):
    df = df.copy()
    df['H-L']  = abs(df['High'] - df['Low'])
    df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
    df['L-PC'] = abs(df['Low']  - df['Close'].shift(1))
    df['TR']   = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['ATR']  = df['TR'].rolling(window=n).mean()
    return df


def calc_supertrend(df, n=14, m=2):
    """Same logic as before, on numpy arrays (fast on full history).
    Fully causal: a candle's value never changes when later candles are added."""
    df  = calc_atr(df, n)
    hl2 = ((df['High'] + df['Low']) / 2).values
    atr = df['ATR'].values
    close = df['Close'].values
    up = hl2 + m * atr
    lo = hl2 - m * atr
    N = len(df)
    st = np.full(N, np.nan)
    dr = np.ones(N, dtype=int)
    for i in range(1, N):
        ps, pdir = st[i-1], dr[i-1]
        if np.isnan(ps):
            st[i] = lo[i]; dr[i] = 1
        elif pdir == 1:
            if close[i] < ps:
                st[i] = up[i]; dr[i] = -1
            else:
                st[i] = max(lo[i], ps); dr[i] = 1
        else:
            if close[i] > ps:
                st[i] = lo[i]; dr[i] = 1
            else:
                st[i] = min(up[i], ps); dr[i] = -1
    df['ST'] = st
    df['ST_dir'] = dr
    return df


def _pivot_dict(H, L, C):
    PP = (H + L + C) / 3
    diff = H - L
    return {'PP': PP, 'R1': PP+0.382*diff, 'R2': PP+0.618*diff, 'R3': PP+1.0*diff,
            'S1': PP-0.382*diff, 'S2': PP-0.618*diff, 'S3': PP-1.0*diff}


def calc_pivots_1d(df):
    """Previous-month pivots relative to the LAST candle (= the end date)."""
    if len(df) < 2:
        return None
    df2 = df.copy()
    df2['Month'] = df2['Datetime'].dt.month
    df2['Year']  = df2['Datetime'].dt.year
    cm, cy = df2.iloc[-1]['Month'], df2.iloc[-1]['Year']
    prev = df2[(df2['Year'] < cy) | ((df2['Year'] == cy) & (df2['Month'] < cm))]
    if prev.empty:
        return None
    last_month = prev[(prev['Year'] == prev['Year'].iloc[-1]) & (prev['Month'] == prev['Month'].iloc[-1])]
    return _pivot_dict(last_month['High'].max(), last_month['Low'].min(), last_month['Close'].iloc[-1])


def calc_pivots_1w(daily_upto_end, end_date):
    """Previous calendar year (relative to the end date) -> pivots, from the daily data we already have."""
    py = daily_upto_end[daily_upto_end['Datetime'].dt.year == end_date.year - 1]
    if py.empty:
        return None
    return _pivot_dict(float(py['High'].max()), float(py['Low'].min()), float(py['Close'].iloc[-1]))


def _yf_normalize(df):
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else ("Date" if "Date" in df.columns else df.columns[0])
    df = df.rename(columns={dt_col: "Datetime"})
    if df["Datetime"].dt.tz is not None:
        df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df["Datetime"] = df["Datetime"].dt.normalize()
    keep = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
    for c in keep:
        if c not in df.columns:
            df[c] = np.nan
    df = df[keep]
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df["Volume"] = df["Volume"].fillna(0)
    df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
    return df


# ── data: ONE yfinance call per stock (full daily history), everything else is sliced ──
raw_cache   = {}     # sym -> (timestamp, daily_df)
_sym_locks  = {}
_cache_lock = threading.Lock()
CACHE_TTL   = 600    # seconds


def _sym_lock(sym):
    with _cache_lock:
        return _sym_locks.setdefault(sym, threading.Lock())


def get_daily(name, fresh=False):
    with _sym_lock(name):
        hit = raw_cache.get(name)
        if hit:
            age = time.time() - hit[0]
            if age < 5 or (not fresh and age < CACHE_TTL):
                return hit[1], None
        try:
            t = yf.Ticker(yf_symbol(name))
            raw = _yf_call(lambda: t.history(period="max", interval="1d", auto_adjust=False), label=f"{name} daily")
        except Exception as e:
            return None, f"Fetch error: {e}"
        df = _yf_normalize(raw)
        if df.empty:
            return None, f"No data found for {name}"
        raw_cache[name] = (time.time(), df)
        return df, None


def to_weekly(d):
    """Daily -> weekly candles labelled by Monday (same as yfinance weekly bars; last one may be partial)."""
    wk = d['Datetime'] - pd.to_timedelta(d['Datetime'].dt.weekday, unit='D')
    g = d.groupby(wk)
    w = pd.DataFrame({
        'Open': g['Open'].first(), 'High': g['High'].max(), 'Low': g['Low'].min(),
        'Close': g['Close'].last(), 'Volume': g['Volume'].sum(),
    })
    w.index.name = 'Datetime'
    return w.reset_index()


def compute_nav(daily, tf, end_date):
    """End dates to jump to for ◀ Prev / Next ▶.
       1d : previous / next trading day.
       1w : last trading day of the previous week / of the week that contains the next trading day
            (so a half-finished weekly candle gets completed first)."""
    dates = daily['Datetime'].dt.date.tolist()
    li = bisect_right(dates, end_date) - 1
    if li < 0:
        return None, None
    if tf == "1d":
        prev_e = dates[li - 1] if li > 0 else None
        next_e = dates[li + 1] if li + 1 < len(dates) else None
    else:
        def wk(d):
            return d - timedelta(days=d.weekday())
        last_of_week = {}
        for d in dates:
            last_of_week[wk(d)] = d
        cur_wk = wk(dates[li])
        earlier = [k for k in last_of_week if k < cur_wk]
        prev_e = last_of_week[max(earlier)] if earlier else None
        next_e = last_of_week[wk(dates[li + 1])] if li + 1 < len(dates) else None
    f = lambda x: x.strftime("%Y-%m-%d") if x else None
    return f(prev_e), f(next_e)


def build_chart_data(name, tf, end_date, fresh=False):
    daily, err = get_daily(name, fresh)
    if err:
        return {"error": err}
    upto = daily[daily['Datetime'] < pd.Timestamp(end_date + timedelta(days=1))].reset_index(drop=True)
    if upto.empty:
        return {"error": f"No data for {name} on/before {end_date}"}

    prev_end, next_end = compute_nav(daily, tf, end_date)

    if tf == "1d":
        df, show_days = upto.copy(), 365
    else:
        df, show_days = to_weekly(upto), 3 * 365

    # indicators on the FULL history up to the end date (so values are identical when you step forward)
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA10'] = df['Low'].ewm(span=10, adjust=False).mean()
    df = calc_supertrend(df)
    pivots = calc_pivots_1d(df) if tf == "1d" else calc_pivots_1w(upto, end_date)

    last = df.iloc[-1]
    prev_close = float(df['Close'].iloc[-2]) if len(df) >= 2 else float(last['Open'])
    ltp = float(last['Close'])
    info = {
        "ltp": round(ltp, 2), "prev_close": round(prev_close, 2),
        "change": round(ltp - prev_close, 2),
        "pchange": round(((ltp - prev_close) / prev_close) * 100, 2) if prev_close else 0,
        "volume": int(last['Volume']),
        "last_date": upto['Datetime'].iloc[-1].strftime("%Y-%m-%d"),
    }

    df = df[df['Datetime'] >= pd.Timestamp(end_date - timedelta(days=show_days))].reset_index(drop=True)

    times = [d.strftime("%Y-%m-%d") for d in df['Datetime']]
    O, H, L, C, V = (df[c].values for c in ['Open', 'High', 'Low', 'Close', 'Volume'])
    E20, E10, ST, SD = df['EMA20'].values, df['EMA10'].values, df['ST'].values, df['ST_dir'].values
    candles, volume, ema20, ema10, st_green, st_red = [], [], [], [], [], []
    for i in range(len(df)):
        candles.append({"o": round(float(O[i]), 2), "h": round(float(H[i]), 2),
                        "l": round(float(L[i]), 2), "c": round(float(C[i]), 2)})
        volume.append({"y": int(V[i]), "up": bool(C[i] >= O[i])})
        ema20.append(None if np.isnan(E20[i]) else round(float(E20[i]), 2))
        ema10.append(None if np.isnan(E10[i]) else round(float(E10[i]), 2))
        sv = None if np.isnan(ST[i]) else round(float(ST[i]), 2)
        if SD[i] == 1:
            st_green.append(sv); st_red.append(None)
        else:
            st_red.append(sv); st_green.append(None)

    end_str = end_date.strftime("%Y-%m-%d")
    cond_dates, cond_flags, accepted = [], [], []
    if tf == "1d":
        cond_dates = sorted([d for d in condition_met_map.get(name, []) if d <= end_str], reverse=True)
        cs = set(cond_dates)
        cond_flags = [t_ in cs for t_ in times]
        accepted = [e for e in accepted_date_map.get(name, []) if e["accepted"] <= end_str][:3]

    return {
        "symbol": name, "tf": tf, "end_date": end_str,
        "candles": candles, "times": times, "ema20": ema20, "ema10": ema10,
        "st_green": st_green, "st_red": st_red, "volume": volume,
        "pivots": pivots, "last_st_dir": int(SD[-1]) if len(SD) else 0,
        "condition_met_flag_1d": cond_flags, "condition_met_dates": cond_dates,
        "accepted": accepted, "info": info,
        "prev_end": prev_end, "next_end": next_end,
    }


# ── delivery data (NSE bhavcopy: one file per day, all stocks) ─────────────
BHAV_DIR = os.path.join(BASE_DIR, "bhav_cache")
os.makedirs(BHAV_DIR, exist_ok=True)
BHAV_FAIL_TTL = 300          # seconds before we retry a day that failed / isn't published yet
DELIV_DAYS    = 6            # end date + 5 previous trading days
_bhav_cache = {}             # "DDMMYYYY" -> (timestamp, {SYMBOL: (volume, deliv_qty, deliv_per)} or None)
_bhav_lock  = threading.Lock()
_BHAV_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/csv,application/octet-stream,*/*",
    "Referer": "https://www.nseindia.com/",
}


def _num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except Exception:
        return None


def _parse_bhav_text(text):
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    if "SYMBOL" not in df.columns:
        return None
    if "SERIES" in df.columns:
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        df = df[df["SERIES"] == "EQ"]
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
    for c in ("TTL_TRD_QNTY", "DELIV_QTY", "DELIV_PER"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    out = {}
    for sym, v, dq, dp in zip(df["SYMBOL"], df["TTL_TRD_QNTY"], df["DELIV_QTY"], df["DELIV_PER"]):
        v, dq, dp = _num(v), _num(dq), _num(dp)
        out[sym] = (int(v) if v is not None else None,
                    int(dq) if dq is not None else None,
                    round(dp, 2) if dp is not None else None)
    return out


def get_bhav(d):
    """Parsed bhavcopy for date d (cached in memory + on disk). None if not available."""
    key = d.strftime("%d%m%Y")
    with _bhav_lock:
        hit = _bhav_cache.get(key)
        if hit and (hit[1] is not None or time.time() - hit[0] < BHAV_FAIL_TTL):
            return hit[1]
        path = os.path.join(BHAV_DIR, f"sec_bhavdata_full_{key}.csv")
        text = None
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                text = None
        if not text:
            url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{key}.csv"
            try:
                r = _req_lib.get(url, headers=_BHAV_HDR, timeout=25)
                if r.status_code == 200 and r.text.strip() and "<html" not in r.text[:300].lower():
                    text = r.text
                else:
                    print(f"ℹ️  Bhavcopy not available for {d} (HTTP {r.status_code})")
            except Exception as e:
                print(f"⚠ Bhavcopy download failed for {d}: {e}")
        data = None
        if text:
            try:
                data = _parse_bhav_text(text)
                if data and not os.path.exists(path):
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
            except Exception as e:
                print(f"⚠ Bhavcopy parse failed for {d}: {e}")
                data = None
        _bhav_cache[key] = (time.time(), data)
        return data


# ── Flask ─────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def index():
    return HTML


@app.route("/api/search")
def api_search():
    return jsonify(search_stocks(request.args.get("q", ""), show_all=request.args.get("all") == "1"))


@app.route("/api/chart")
def api_chart():
    name  = _to_plain_name(request.args.get("symbol", ""))
    tf    = request.args.get("tf", "1d")
    end   = request.args.get("end", "")
    fresh = request.args.get("fresh") == "1"
    if not name or tf not in ("1d", "1w"):
        return jsonify({"error": "Bad request"})
    if not end:
        return jsonify({"error": "Pick an end date first"})
    try:
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"error": "Invalid end date"})
    today = datetime.now(IST).date()
    if end_date > today:
        end_date = today
    return jsonify(build_chart_data(name, tf, end_date, fresh))


@app.route("/api/delivery")
def api_delivery():
    """Volume / delivery qty / delivery % for the end date + 5 previous trading days (newest first)."""
    name = _to_plain_name(request.args.get("symbol", ""))
    end  = request.args.get("end", "")
    if not name or not end:
        return jsonify({"error": "Bad request"})
    try:
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"error": "Invalid end date"})
    today = datetime.now(IST).date()
    if end_date > today:
        end_date = today
    daily, err = get_daily(name)
    if err:
        return jsonify({"error": err})
    upto = daily[daily['Datetime'] < pd.Timestamp(end_date + timedelta(days=1))].tail(DELIV_DAYS)
    if upto.empty:
        return jsonify({"error": "No data"})
    out = []
    for _, r in upto.iloc[::-1].iterrows():
        d = r['Datetime'].date()
        b = get_bhav(d)
        rec = b.get(name) if b else None
        vol = rec[0] if (rec and rec[0] is not None) else int(r['Volume'])
        out.append({
            "date":  d.strftime("%Y-%m-%d"),
            "vol":   vol,
            "deliv": rec[1] if rec else None,
            "dper":  rec[2] if rec else None,
        })
    return jsonify({"symbol": name, "rows": out})


# =============================================================
#  HTML
# =============================================================
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>StockDash — Rows</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg:#000; --surface:#111; --border:#2a2a2a; --accent:#3b82f6;
    --text:#e5e7eb; --muted:#9ca3af; --green:#22c55e; --red:#ef4444; --yellow:#f59e0b;
    --font:'Inter','Segoe UI',system-ui,sans-serif; --info-w:250px; --row-sep:#262626;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html,body { height:100%; background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; }
  #stickyTop { position:sticky; top:0; z-index:200; min-width:max-content; box-shadow:0 2px 6px rgba(0,0,0,.6); }
  #topbar { background:var(--surface); border-bottom:1px solid var(--border); padding:8px 14px;
    display:flex; align-items:center; gap:12px; min-height:50px; }
  #topbar .brand { font-size:15px; font-weight:700; color:var(--accent); letter-spacing:.5px; white-space:nowrap; }
  .btn { display:inline-flex; align-items:center; gap:5px; padding:6px 12px; border-radius:6px; font-size:12px;
    font-weight:600; border:1px solid #1e3a5f; background:#0d1f38; color:#60a5fa; cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn:hover { opacity:.8; transform:translateY(-1px); }
  .btn-sm { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px; font-size:10px; font-weight:600;
    border:1px solid var(--border); background:#1a1a1a; color:var(--muted); cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn-sm:hover { border-color:var(--accent); color:var(--accent); background:#0d1f38; }
  .btn-sm.active { border-color:#f472b6; color:#f472b6; background:#3a0f24; }
  .btn-sm:disabled { opacity:.3; cursor:not-allowed; pointer-events:none; }
  .btn-step { border-color:#155e3a; color:#34d399; background:#0d2b1c; }
  .btn-step:hover { border-color:#34d399; color:#34d399; background:#0f3a26; }
  #statusBadge { margin-left:auto; font-size:11px; color:var(--muted); white-space:nowrap; padding-left:10px; }

  /* multi-select combo */
  .msel { display:flex; align-items:center; flex-wrap:wrap; gap:5px; width:520px; max-width:60vw; background:#1a1a1a;
    border:1px solid var(--border); border-radius:8px; padding:4px 6px; cursor:text; }
  .msel:focus-within { border-color:var(--accent); }
  #chips { display:contents; }
  .chip { display:inline-flex; align-items:center; gap:6px; background:#0d1f38; border:1px solid #1e3a5f; color:#93c5fd;
    border-radius:12px; padding:2px 5px 2px 9px; font-size:11px; font-weight:700; }
  .chip-x { cursor:pointer; color:var(--muted); font-size:13px; line-height:1; padding:0 3px; border-radius:50%; }
  .chip-x:hover { color:var(--red); }
  #comboInput { flex:1; min-width:150px; background:transparent; border:none; outline:none; color:var(--text);
    font-size:12px; font-weight:700; letter-spacing:.3px; padding:4px; font-family:var(--font); }
  #comboArrow { background:transparent; border:none; color:var(--muted); cursor:pointer; font-size:13px; padding:2px 6px; }
  #comboArrow:hover { color:var(--accent); }
  .gdate { display:flex; align-items:center; gap:6px; }
  .gdate label { font-size:10px; color:var(--muted); font-weight:700; text-transform:uppercase; white-space:nowrap; }
  .date-input { background:#1a1a1a; color:var(--text); border:1px solid var(--border); border-radius:6px;
    padding:5px 7px; font-size:12px; outline:none; font-family:var(--font); color-scheme:dark; }
  .date-input:focus { border-color:var(--accent); }

  #sizeBar { background:#0d0d0d; border-bottom:1px solid var(--border); padding:5px 14px; display:flex; align-items:center;
    gap:16px; height:38px; }
  .tf-size-group { display:flex; align-items:center; gap:8px; padding:3px 9px; background:var(--surface); border:1px solid var(--border); border-radius:7px; }
  .tf-size-label { font-size:11px; font-weight:700; text-align:center; }
  .size-ctrl { display:flex; align-items:center; gap:4px; }
  .size-ctrl label { color:var(--muted); font-size:10px; white-space:nowrap; }
  .step-btn { width:19px; height:19px; border-radius:4px; border:1px solid var(--border); background:#1a1a1a; color:var(--text);
    font-size:13px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center; font-weight:700; }
  .step-btn:hover { border-color:var(--accent); color:var(--accent); }
  .size-val { font-size:10px; min-width:38px; text-align:center; background:#1a1a1a; border:1px solid var(--border); border-radius:4px; padding:1px 3px; }
  #colHeader { display:flex; align-items:stretch; background:#0d0d0d; border-bottom:2px solid var(--border); }
  .ch-info { flex:0 0 var(--info-w); min-width:var(--info-w); padding:6px 10px; font-size:10px; font-weight:700; letter-spacing:.6px;
    color:var(--muted); text-transform:uppercase; border-right:2px solid var(--border); position:sticky; left:0; background:#0d0d0d; z-index:5; }
  .ch-tf { flex:0 0 auto; padding:6px 10px; font-size:11px; font-weight:700; letter-spacing:.8px; color:var(--muted);
    text-transform:uppercase; border-right:1px solid var(--border); }
  #stockTable { min-width:max-content; }
  #emptyMsg { padding:60px 30px; color:var(--muted); font-size:14px; position:sticky; left:0; width:fit-content; line-height:1.7; }
  .stock-row { display:flex; align-items:stretch; border-bottom:2px solid var(--row-sep); background:var(--surface); min-width:max-content; }
  .info-strip { flex:0 0 var(--info-w); min-width:var(--info-w); padding:8px 10px; border-right:2px solid var(--border);
    display:flex; flex-direction:column; gap:4px; background:#0c0c0c; position:sticky; left:0; z-index:10; }
  .row-top { display:flex; align-items:center; justify-content:space-between; gap:6px; }
  .date-line { display:flex; align-items:center; gap:6px; }
  .date-line label { font-size:10px; color:var(--muted); font-weight:700; text-transform:uppercase; }
  .date-line .date-input { width:100%; padding:4px 6px; }
  .btn-row { display:flex; gap:5px; flex-wrap:wrap; }
  .info-name { font-size:15px; font-weight:700; color:var(--accent); letter-spacing:.3px; min-height:18px; }
  .info-ltp { font-size:13px; font-weight:600; }
  .info-chg { font-size:11px; font-weight:600; padding:2px 6px; border-radius:4px; width:fit-content; }
  .chg-up { background:#0f3320; color:var(--green); }
  .chg-dn { background:#3a1414; color:var(--red); }
  .info-vol { font-size:9.5px; color:var(--muted); }
  .stock-links { display:flex; gap:5px; margin-top:2px; }
  .stock-link { font-size:9px; font-weight:600; padding:2px 6px; border-radius:4px; text-decoration:none; border:1px solid; white-space:nowrap; }
  .link-screener { color:#2dd4bf; background:#0a2a26; border-color:#0f5c50; }
  .link-tv { color:#60a5fa; background:#0d1f38; border-color:#1e3a5f; }
  .cond-badge { font-size:9px; font-weight:600; padding:1px 6px; border-radius:8px; background:#332405; color:var(--yellow);
    border:1px solid #5c4008; display:none; width:fit-content; }
  .acc-title { font-size:9px; font-weight:700; color:var(--muted); letter-spacing:.5px; text-transform:uppercase; margin-top:2px; }
  .acc-row { font-size:9px; line-height:1.5; color:#e5e7eb; }
  .acc-row b { color:#fbbf24; }

  /* volume / delivery table */
  .deliv { min-height:0; }
  .dv { width:100%; border-collapse:collapse; font-size:9.5px; }
  .dv th { color:var(--muted); font-weight:700; text-align:right; padding:1px 2px; border-bottom:1px solid var(--border);
    text-transform:uppercase; font-size:8.5px; letter-spacing:.3px; }
  .dv td { text-align:right; padding:1px 2px; color:#e5e7eb; font-variant-numeric:tabular-nums; }
  .dv th:first-child, .dv td:first-child { text-align:left; color:var(--muted); }
  .dv tr.dv-last td { color:#fbbf24; }
  .dv-msg { font-size:9px; color:var(--muted); font-style:italic; }

  .chart-cell { flex:0 0 auto; border-right:1px solid var(--border); padding:6px 6px 4px; display:flex; flex-direction:column; background:var(--bg); }
  .cell-label { font-size:9.5px; font-weight:600; letter-spacing:.8px; color:var(--muted); text-transform:uppercase;
    display:flex; align-items:center; justify-content:space-between; margin-bottom:3px; gap:4px; min-height:20px; }
  .cell-label-btns { display:flex; gap:4px; align-items:center; }
  .st-dir { font-size:9px; padding:1px 5px; border-radius:3px; font-weight:700; min-width:8px; }
  .st-bull { background:#0f3320; color:var(--green); }
  .st-bear { background:#3a1414; color:var(--red); }
  .chart-wrap { border:1px solid var(--border); border-radius:4px; overflow:hidden; position:relative; }
  .chart-wrap.draw-mode { cursor:crosshair; }
  .chart-pending { display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:11px; font-style:italic;
    text-align:center; padding:0 8px; background:#111; border:1px dashed var(--border); border-radius:4px; }
  .chart-pending.err { color:#f87171; }
  #suggest { position:fixed; display:none; z-index:500; background:#151515; border:1px solid #3b82f6; border-radius:6px;
    max-height:320px; overflow-y:auto; box-shadow:0 6px 20px rgba(0,0,0,.7); }
  .sug-item { padding:6px 10px; cursor:pointer; display:flex; align-items:baseline; gap:8px; border-bottom:1px solid #222; }
  .sug-item.sel, .sug-item:hover { background:#0d1f38; }
  .sug-check { width:12px; color:#34d399; font-size:11px; font-weight:700; }
  .sug-item.picked .sug-sym { color:#34d399; }
  .sug-sym { font-weight:700; font-size:12px; color:#e5e7eb; }
  .sug-star { color:#fbbf24; font-size:10px; }
  .sug-name { font-size:10px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
</style>
</head>
<body>

<div id="stickyTop">
  <div id="topbar">
    <span class="brand">📈 StockDash</span>
    <div class="msel" id="comboBox">
      <div id="chips"></div>
      <input id="comboInput" placeholder="Type & select stocks…" autocomplete="off" spellcheck="false">
      <button id="comboArrow" onmousedown="event.preventDefault()" onclick="toggleCombo()" title="Show all stocks">▾</button>
    </div>
    <div class="gdate"><label>End date</label><input type="date" class="date-input" id="gEnd" max=""></div>
    <button class="btn" onclick="clearAll()">✕ Clear all</button>
    <span id="statusBadge">1) select stocks  2) pick the end date  → charts fill in</span>
  </div>
  <div id="sizeBar"></div>
  <div id="colHeader">
    <div class="ch-info">Stock · End Date</div>
    <div class="ch-tf" id="hd-1d">1 Day · 1 Year</div>
    <div class="ch-tf" id="hd-wl">Weekly Line · 3 Years</div>
    <div class="ch-tf" id="hd-1w">1 Week · 3 Years</div>
  </div>
</div>

<div id="stockTable"></div>
<div id="emptyMsg"></div>
<div id="suggest"></div>

<script>
const $ = id => document.getElementById(id);
const sizeState = { '1d': { width:520, h:340 }, '1w': { width:520, h:340 } };   // Weekly Line shares the 1W size
const wlineSize = () => sizeState['1w'];

const rows = {};          // id -> { id, symbol, token, loadedSym, nav }
const chartReg = {};      // `${id}_${tf}` -> { chart, n, ... }
const drawState = {};     // id -> { active, pending, lines:[{a,b,series}] }   (anchors are TIME based: {t, frac, price})
const weeklyTimes = {};   // id -> times[] of the weekly chart
const dragState = {};
let rowSeq = 0;
let selected = [];        // PENDING stocks in the combo box (cleared once their rows are created)
let globalEnd = '';       // date typed in the top bar (cleared once it has been applied)

function setStatus(m) { $('statusBadge').textContent = m; }
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
function cleanSym(s) {
  s = (s || '').trim().toUpperCase();
  if (s.includes(':')) s = s.split(':')[1];
  return s.replace(/-EQ$/, '').replace(/\.NS$/, '').trim();
}

// ── size controls ─────────────────────────────────────────────
function buildSizeBar() {
  const defs = [['1d','1D','#34d399'], ['1w','1W + Weekly Line','#fbbf24']];
  $('sizeBar').innerHTML = defs.map(([tf, label, color]) => `
    <div class="tf-size-group">
      <span class="tf-size-label" style="color:${color}">${label}</span>
      <div class="size-ctrl"><label>Width</label>
        <button class="step-btn" onclick="stepSize('${tf}','width',-40)">−</button>
        <span class="size-val" id="val-${tf}-width">${sizeState[tf].width}px</span>
        <button class="step-btn" onclick="stepSize('${tf}','width',40)">+</button></div>
      <div class="size-ctrl"><label>Height</label>
        <button class="step-btn" onclick="stepSize('${tf}','h',-30)">−</button>
        <span class="size-val" id="val-${tf}-h">${sizeState[tf].h}px</span>
        <button class="step-btn" onclick="stepSize('${tf}','h',30)">+</button></div>
    </div>`).join('') +
    `<span style="font-size:10px;color:var(--muted)">Size changes apply to every row. Scroll / pinch / drag on a chart to zoom & pan.</span>`;
}
function updateHeader() {
  $('hd-1d').style.width = (sizeState['1d'].width + 15) + 'px';
  $('hd-wl').style.width = (sizeState['1w'].width + 15) + 'px';
  $('hd-1w').style.width = (sizeState['1w'].width + 15) + 'px';
}
function stepSize(tf, dim, delta) {
  if (dim === 'width') sizeState[tf].width = Math.max(300, Math.min(3000, sizeState[tf].width + delta));
  else                 sizeState[tf].h     = Math.max(120, Math.min(1200, sizeState[tf].h + delta));
  $(`val-${tf}-width`).textContent = sizeState[tf].width + 'px';
  $(`val-${tf}-h`).textContent     = sizeState[tf].h + 'px';
  applySize(tf);
}
function applySize(tf) {
  const tfs = tf === '1w' ? ['1w', 'wline'] : [tf];
  for (const id of Object.keys(rows)) {
    for (const t of tfs) {
      const sz = t === 'wline' ? wlineSize() : sizeState[t];
      const c = $(`chartdiv-${id}-${t}`);
      if (c) { c.style.width = sz.width + 'px'; c.style.height = sz.h + 'px'; }
      const reg = chartReg[`${id}_${t}`];
      if (reg) { reg.chart.resize(sz.width, sz.h); reg.chart.timeScale().fitContent(); }
    }
  }
  updateHeader();
}

// ── selection (combo chips = PENDING stocks) → rows ──────────
function renderChips() {
  $('chips').innerHTML = selected.map(s =>
    `<span class="chip">${esc(s)}<b class="chip-x" data-sym="${esc(s)}" title="Remove">×</b></span>`).join('');
}
$('chips').addEventListener('mousedown', e => {
  const x = e.target.closest('.chip-x');
  if (x) { e.preventDefault(); removeStock(x.dataset.sym); }
});
$('comboBox').addEventListener('mousedown', e => {
  if (e.target === $('comboBox')) { e.preventDefault(); $('comboInput').focus(); }
});

function updateEmpty() {
  const el = $('emptyMsg');
  if (Object.keys(rows).length) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  if (!selected.length && !globalEnd) el.innerHTML = '👆 Step 1: type / pick one or more stocks in the box at the top.<br>Step 2: pick the end date. A chart row appears for every selected stock.';
  else if (!selected.length)          el.innerHTML = `End date <b>${esc(globalEnd)}</b> is set — now select the stocks in the box at the top.`;
  else                                el.innerHTML = `<b>${selected.length}</b> stock${selected.length > 1 ? 's' : ''} selected — now pick the <b>end date</b> at the top to see the charts.`;
}

// stock picked in the combo (pending). If a date is already waiting in the top bar, build the row right away.
function addStock(sym) {
  sym = cleanSym(sym);
  if (!sym || selected.includes(sym)) return;
  selected.push(sym);
  renderChips();
  if (globalEnd) { commitSelected(); return; }
  updateEmpty();
  setStatus(`${selected.length} stock(s) selected — now pick the end date`);
}

// remove a PENDING chip (not yet turned into a row)
function removeStock(sym) {
  selected = selected.filter(s => s !== sym);
  renderChips();
  updateEmpty();
  setStatus(`${sym} removed from selection`);
}

// remove an existing ROW (✕ Remove button on the row)
function removeRowBtn(id) {
  const r = rows[id]; if (!r) return;
  const sym = r.symbol;
  removeRow(id);
  updateEmpty();
  setStatus(`${sym} row removed`);
}

function clearAll() {
  selected = [];
  globalEnd = '';
  $('gEnd').value = '';
  $('comboInput').value = '';
  renderChips();
  Object.keys(rows).forEach(id => removeRow(Number(id)));
  updateEmpty();
  setStatus('Cleared. Select stocks, then pick the end date.');
}

// Build a NEW row for every pending stock with the date in the top bar (even if that stock already has a row),
// then CLEAR the combo + top date so the next stock(s) can use a different date.
// Rows created earlier are NOT touched.
function commitSelected() {
  const date = globalEnd;
  if (!date || !selected.length) return;
  const syms = selected.slice();
  for (const sym of syms) {
    const id = createRow(sym, date);
    loadRow(id);
  }
  // reset the top controls for the next entry
  selected = [];
  globalEnd = '';
  $('gEnd').value = '';
  $('comboInput').value = '';
  renderChips();
  updateEmpty();
  setStatus(`${syms.join(', ')} loaded up to ${date} — pick next stock(s) & date`);
}

function applyGlobalDate() {
  const d = $('gEnd').value;
  if (!d) return;
  globalEnd = d;
  if (!selected.length) { updateEmpty(); setStatus(`End date ${d} set — now select the stock(s)`); return; }
  commitSelected();
}
$('gEnd').addEventListener('change', applyGlobalDate);

// ── rows ─────────────────────────────────────────────────────
function createRow(sym, date) {
  const id = ++rowSeq;
  rows[id] = { id, symbol: sym, token: 0, loadedSym: null, nav: {} };
  const el = document.createElement('div');
  el.className = 'stock-row';
  el.id = `row-${id}`;
  const pend = 'Loading…';
  el.innerHTML = `
    <div class="info-strip">
      <div class="row-top"><div class="info-name" id="name-${id}">${esc(sym)}</div>
        <button class="btn-sm" onclick="removeRowBtn(${id})" title="Remove this stock row">✕ Remove</button></div>
      <div class="deliv" id="deliv-${id}"></div>
      <div class="date-line"><label>End</label>
        <input type="date" class="date-input" id="end-${id}" value="${esc(date || '')}" max="${todayStr()}" title="This row's own end date"></div>
      <div class="btn-row">
        <button class="btn-sm" onclick="loadRow(${id}, true)" title="Re-fetch this stock from yfinance">Go ↻</button>
      </div>
      <div class="info-ltp" id="ltp-${id}"></div>
      <div class="info-chg" id="chg-${id}" style="display:none"></div>
      <div class="info-vol" id="vol-${id}"></div>
      <div id="links-${id}"></div>
      <div class="cond-badge" id="cbadge-${id}"></div>
      <div id="acc-${id}"></div>
    </div>
    <div class="chart-cell">
      <div class="cell-label"><span id="stdir-${id}-1d" class="st-dir"></span><span>1 Day</span>
        <div class="cell-label-btns">
          <button class="btn-sm btn-step" id="prev-${id}-1d" onclick="stepRow(${id},'1d',-1)" disabled>◀ Prev</button>
          <button class="btn-sm btn-step" id="next-${id}-1d" onclick="stepRow(${id},'1d',1)" disabled>Next ▶</button></div></div>
      <div class="chart-wrap" id="chartdiv-${id}-1d" style="width:${sizeState['1d'].width}px;height:${sizeState['1d'].h}px">
        <div class="chart-pending" style="width:100%;height:100%">${pend}</div></div>
    </div>
    <div class="chart-cell">
      <div class="cell-label"><span>Weekly Line</span>
        <div class="cell-label-btns">
          <button class="btn-sm" id="drawbtn-${id}" onclick="toggleDrawMode(${id})" title="Draw a trend line">✏ Draw</button>
          <button class="btn-sm" onclick="clearDrawings(${id})" title="Clear drawn lines">✕</button>
          <button class="btn-sm btn-step" id="prev-${id}-wl" onclick="stepRow(${id},'1w',-1)" disabled>◀ Prev</button>
          <button class="btn-sm btn-step" id="next-${id}-wl" onclick="stepRow(${id},'1w',1)" disabled>Next ▶</button></div></div>
      <div class="chart-wrap" id="chartdiv-${id}-wline" style="width:${wlineSize().width}px;height:${wlineSize().h}px">
        <div class="chart-pending" style="width:100%;height:100%">${pend}</div></div>
    </div>
    <div class="chart-cell">
      <div class="cell-label"><span id="stdir-${id}-1w" class="st-dir"></span><span>1 Week</span>
        <div class="cell-label-btns">
          <button class="btn-sm btn-step" id="prev-${id}-1w" onclick="stepRow(${id},'1w',-1)" disabled>◀ Prev</button>
          <button class="btn-sm btn-step" id="next-${id}-1w" onclick="stepRow(${id},'1w',1)" disabled>Next ▶</button></div></div>
      <div class="chart-wrap" id="chartdiv-${id}-1w" style="width:${sizeState['1w'].width}px;height:${sizeState['1w'].h}px">
        <div class="chart-pending" style="width:100%;height:100%">${pend}</div></div>
    </div>`;
  $('stockTable').appendChild(el);
  $(`end-${id}`).addEventListener('change', () => loadRow(id));   // only THIS row reloads
  updateHeader();
  updateEmpty();
  return id;
}

function removeRow(id) {
  for (const t of ['1d', '1w', 'wline']) disposeChart(`${id}_${t}`);
  delete rows[id]; delete drawState[id]; delete weeklyTimes[id]; delete dragState[id];
  const el = $(`row-${id}`); if (el) el.remove();
}

function setPending(id, tf, msg, isErr) {
  disposeChart(`${id}_${tf}`);
  const c = $(`chartdiv-${id}-${tf}`); if (!c) return;
  const sz = tf === 'wline' ? wlineSize() : sizeState[tf];
  c.style.width = sz.width + 'px'; c.style.height = sz.h + 'px';
  c.innerHTML = `<div class="chart-pending ${isErr ? 'err' : ''}" style="width:100%;height:100%">${esc(msg)}</div>`;
}

function clearInfo(id) {
  $(`ltp-${id}`).textContent = ''; $(`vol-${id}`).textContent = '';
  $(`chg-${id}`).style.display = 'none';
  $(`cbadge-${id}`).style.display = 'none'; $(`acc-${id}`).innerHTML = ''; $(`links-${id}`).innerHTML = '';
  const dv = $(`deliv-${id}`); if (dv) dv.innerHTML = '';
  ['1d', '1w'].forEach(t => { const e = $(`stdir-${id}-${t}`); if (e) { e.textContent = ''; e.className = 'st-dir'; } });
}

// ── volume / delivery table ──────────────────────────────────
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtQty(n) {
  if (n == null) return '—';
  if (n >= 1e7) return (n / 1e7).toFixed(2) + 'Cr';
  if (n >= 1e5) return (n / 1e5).toFixed(2) + 'L';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}
function fmtDay(d) { return d.slice(8, 10) + '-' + MONTHS[+d.slice(5, 7) - 1]; }

function renderDelivery(id, d) {
  const el = $(`deliv-${id}`); if (!el) return;
  const list = d.rows || [];
  if (!list.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<table class="dv"><tr><th>Date</th><th>Vol</th><th>Deliv</th><th>Del%</th></tr>` +
    list.map((r, i) => `<tr class="${i === 0 ? 'dv-last' : ''}"><td>${fmtDay(r.date)}</td><td>${fmtQty(r.vol)}</td>` +
      `<td>${fmtQty(r.deliv)}</td><td>${r.dper == null ? '—' : r.dper.toFixed(1) + '%'}</td></tr>`).join('') +
    `</table>`;
}

async function loadDelivery(id, sym, end, token) {
  const el = $(`deliv-${id}`); if (!el) return;
  if (!el.innerHTML) el.innerHTML = '<div class="dv-msg">Loading delivery…</div>';
  try {
    const resp = await fetch(`/api/delivery?symbol=${encodeURIComponent(sym)}&end=${end}`);
    const d = await resp.json();
    const r = rows[id];
    if (!r || token !== r.token) return;        // row removed or date changed meanwhile
    if (d.error) { el.innerHTML = `<div class="dv-msg">Delivery: ${esc(d.error)}</div>`; return; }
    renderDelivery(id, d);
  } catch (e) {
    const r = rows[id];
    if (r && token === r.token && el) el.innerHTML = '<div class="dv-msg">Delivery data unavailable</div>';
  }
}

// ◀ Prev / Next ▶ buttons (per row)
function setNav(id, tf, prev, next) {
  const r = rows[id]; if (!r) return;
  r.nav[tf] = { prev, next };
  const sfx = tf === '1w' ? ['1w', 'wl'] : [tf];
  sfx.forEach(s => {
    const pb = $(`prev-${id}-${s}`), nb = $(`next-${id}-${s}`);
    if (pb) { pb.disabled = !prev; pb.title = prev ? `Go back to ${prev}` : 'No earlier candle'; }
    if (nb) { nb.disabled = !next; nb.title = next ? `Go to ${next}` : 'No later candle (latest data reached)'; }
  });
}
function stepRow(id, tf, dir) {
  const r = rows[id]; if (!r) return;
  const nav = r.nav[tf];
  const target = nav && (dir > 0 ? nav.next : nav.prev);
  if (!target) { setStatus(`${r.symbol}: no ${dir > 0 ? 'later' : 'earlier'} candle`); return; }
  $(`end-${id}`).value = target;      // only this row's date moves
  loadRow(id);
}

async function loadRow(id, fresh) {
  const r = rows[id]; if (!r) return;
  const sym = r.symbol;
  const end = $(`end-${id}`).value;
  const symChanged = sym !== r.loadedSym;

  if (!end) {     // row date cleared -> no charts
    r.token++; r.loadedSym = null;
    ['1d', 'wline', '1w'].forEach(t => setPending(id, t, `Pick an end date for ${sym} to see the charts`));
    clearInfo(id); $(`name-${id}`).textContent = sym;
    setNav(id, '1d', null, null); setNav(id, '1w', null, null);
    return;
  }

  const token = ++r.token;
  const keep = !symChanged;     // already loaded before => keep zoom + drawn lines and don't blank the charts
  if (symChanged) {
    const st = getDraw(id); st.lines = []; st.pending = null;
    ['1d', 'wline', '1w'].forEach(t => setPending(id, t, `Loading ${sym} up to ${end}…`));
    clearInfo(id);
  }
  setNav(id, '1d', null, null); setNav(id, '1w', null, null);
  $(`name-${id}`).textContent = sym + ' ⏳';
  setStatus(`Loading ${sym} up to ${end}…`);

  loadDelivery(id, sym, end, token);     // separate call, charts don't wait for it

  const get = async tf => {
    try {
      const resp = await fetch(`/api/chart?symbol=${encodeURIComponent(sym)}&tf=${tf}&end=${end}${fresh ? '&fresh=1' : ''}`);
      return await resp.json();
    } catch (e) { return { error: String(e) }; }
  };
  const p1 = get('1d').then(d => {
    if (token !== r.token || !rows[id]) return;
    if (d.error) { setPending(id, '1d', d.error, true); $(`name-${id}`).textContent = sym; return; }
    r.loadedSym = sym;
    renderChart(id, '1d', d, keep); updateSTDir(id, '1d', d); updateInfo(id, d);
    setNav(id, '1d', d.prev_end, d.next_end);
  });
  const pw = get('1w').then(d => {
    if (token !== r.token || !rows[id]) return;
    if (d.error) { setPending(id, '1w', d.error, true); setPending(id, 'wline', d.error, true); return; }
    r.loadedSym = sym;
    renderChart(id, '1w', d, keep); updateSTDir(id, '1w', d);
    setNav(id, '1w', d.prev_end, d.next_end);
  });
  await Promise.all([p1, pw]);
  if (token === r.token) setStatus(`${sym} shown up to ${end} ✓`);
}

function updateInfo(id, d) {
  const info = d.info || {};
  $(`name-${id}`).textContent = d.symbol;
  $(`ltp-${id}`).textContent = '₹' + (info.ltp || 0).toFixed(2);
  const chg = $(`chg-${id}`), pc = info.pchange || 0;
  chg.style.display = 'inline-block';
  chg.textContent = `${pc >= 0 ? '+' : ''}${pc.toFixed(2)}%`;
  chg.className = 'info-chg ' + (pc >= 0 ? 'chg-up' : 'chg-dn');
  $(`vol-${id}`).textContent = `Vol: ${(info.volume || 0).toLocaleString()} · ${info.last_date || ''}`;
  $(`links-${id}`).innerHTML = `<div class="stock-links">
    <a class="stock-link link-screener" href="https://www.screener.in/company/${encodeURIComponent(d.symbol)}/consolidated/" target="_blank" rel="noopener">📊 Screener</a>
    <a class="stock-link link-tv" href="https://in.tradingview.com/chart/?symbol=NSE%3A${encodeURIComponent(d.symbol)}" target="_blank" rel="noopener">📈 TradingView</a></div>`;
  const cd = d.condition_met_dates || [];
  const badge = $(`cbadge-${id}`);
  if (cd.length) { badge.textContent = '⚡ ' + cd[0]; badge.style.display = 'block'; } else badge.style.display = 'none';
  const acc = d.accepted || [];
  $(`acc-${id}`).innerHTML = acc.length
    ? `<div class="acc-title">⚡ Recent Accepted Dates</div>` +
      acc.map(e => `<div class="acc-row">CM: ${esc(e.condition_met)} → Acc: <b>${esc(e.accepted)}</b></div>`).join('')
    : '';
}

function updateSTDir(id, tf, d) {
  const el = $(`stdir-${id}-${tf}`); if (!el) return;
  if (d.last_st_dir === 1)       { el.className = 'st-dir st-bull'; el.textContent = '▲ Bull'; }
  else if (d.last_st_dir === -1) { el.className = 'st-dir st-bear'; el.textContent = '▼ Bear'; }
  else el.textContent = '';
}

// ── multi-select combo: suggestions ──────────────────────────
const sug = { items: [], idx: -1, timer: null, seq: 0, skipFocus: false };
const cInp = $('comboInput');

function scheduleSuggest(delay = 150) {
  clearTimeout(sug.timer);
  sug.timer = setTimeout(() => fetchSuggest(false), delay);
}
async function fetchSuggest(forceAll, focusSym) {
  const q = cInp.value.trim().toUpperCase();
  const showAll = forceAll || !q;     // empty box => your whole matched list
  const mySeq = ++sug.seq;
  try {
    const r = await fetch(`/api/search?q=${showAll ? '' : encodeURIComponent(q)}&all=${showAll ? 1 : 0}`);
    const items = await r.json();
    if (mySeq !== sug.seq) return;
    sug.items = items;
    const fi = focusSym ? items.findIndex(it => it.sym === focusSym) : -1;
    sug.idx = items.length ? Math.max(0, fi) : -1;
    renderSuggest();
  } catch (e) {}
}
function renderSuggest() {
  const box = $('suggest');
  if (!sug.items.length || document.activeElement !== cInp) { box.style.display = 'none'; return; }
  const rc = $('comboBox').getBoundingClientRect();
  box.style.left = rc.left + 'px'; box.style.top = (rc.bottom + 3) + 'px';
  box.style.width = Math.max(rc.width, 340) + 'px';
  box.innerHTML = sug.items.map((it, i) => {
    const picked = selected.includes(it.sym);
    return `<div class="sug-item ${i === sug.idx ? 'sel' : ''} ${picked ? 'picked' : ''}" data-i="${i}">
      <span class="sug-check">${picked ? '✓' : ''}</span>
      <span class="sug-sym">${esc(it.sym)}</span>${it.listed ? '<span class="sug-star" title="In your matched list">★</span>' : ''}
      <span class="sug-name">${esc(it.name || '')}</span></div>`;
  }).join('');
  box.style.display = 'block';
  const sel = box.querySelector('.sel'); if (sel) sel.scrollIntoView({ block: 'nearest' });
}
function hideSuggest() { $('suggest').style.display = 'none'; }
function toggleCombo() {
  if ($('suggest').style.display === 'block') { hideSuggest(); return; }
  sug.skipFocus = true; cInp.focus(); sug.skipFocus = false;
  fetchSuggest(true);
}
$('suggest').addEventListener('mousedown', e => {
  const el = e.target.closest('.sug-item');
  if (!el) return;
  e.preventDefault();               // keep the box focused + open so you can keep picking
  pickSuggest(+el.dataset.i);
});
function pickSuggest(i) {
  const it = sug.items[i]; if (!it) return;
  if (selected.includes(it.sym)) removeStock(it.sym); else addStock(it.sym);
  cInp.value = '';
  fetchSuggest(true, it.sym);       // refresh the ✓ marks, stay open
}

cInp.addEventListener('input', () => scheduleSuggest());
cInp.addEventListener('focus', () => { if (!sug.skipFocus) scheduleSuggest(0); });
cInp.addEventListener('blur',  () => setTimeout(hideSuggest, 150));
cInp.addEventListener('keydown', e => {
  const open = $('suggest').style.display === 'block';
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (!open) { toggleCombo(); return; }
    sug.idx = Math.min(sug.items.length - 1, sug.idx + 1); renderSuggest();
  } else if (e.key === 'ArrowUp' && open) {
    e.preventDefault(); sug.idx = Math.max(0, sug.idx - 1); renderSuggest();
  } else if (e.key === 'Escape') {
    hideSuggest();
  } else if (e.key === 'Backspace' && !cInp.value && selected.length) {
    removeStock(selected[selected.length - 1]);
    if (open) fetchSuggest(true);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const typed = cleanSym(cInp.value);
    if (open && sug.items[sug.idx]) pickSuggest(sug.idx);
    else if (typed) { addStock(typed); cInp.value = ''; fetchSuggest(true, typed); }
    else hideSuggest();
  }
});
window.addEventListener('scroll', e => { if (e.target && e.target.id === 'suggest') return; renderSuggest(); }, true);
window.addEventListener('resize', renderSuggest);

// ── charts ───────────────────────────────────────────────────
function disposeChart(key) {
  const reg = chartReg[key];
  if (reg && reg.chart) { try { reg.chart.remove(); } catch (e) {} }
  delete chartReg[key];
}

// remember the visible range (relative to the last candle) so stepping candles doesn't reset your zoom
function captureView(key) {
  const reg = chartReg[key];
  if (!reg) return null;
  try {
    const rg = reg.chart.timeScale().getVisibleLogicalRange();
    return rg ? { from: rg.from, to: rg.to, n: reg.n } : null;
  } catch (e) { return null; }
}
function restoreView(chart, view, n) {
  if (!view) { chart.timeScale().fitContent(); return; }
  const to = (n - 1) + (view.to - (view.n - 1));
  const from = to - (view.to - view.from);
  chart.timeScale().setVisibleLogicalRange({ from, to });
}

function renderChart(id, tf, data, keep) {
  const key = `${id}_${tf}`;
  const container = $(`chartdiv-${id}-${tf}`);
  if (!container) return;
  const view = keep ? captureView(key) : null;
  disposeChart(key);
  container.innerHTML = '';
  container.style.width = sizeState[tf].width + 'px';
  container.style.height = sizeState[tf].h + 'px';

  const chart = LightweightCharts.createChart(container, {
    width: sizeState[tf].width, height: sizeState[tf].h,
    layout: { background: { color: '#000000' }, textColor: '#d1d5db', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.06)' }, horzLines: { color: 'rgba(255,255,255,0.06)' } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { borderVisible: false, rightOffset: 2, fixLeftEdge: true, lockVisibleTimeRangeOnResize: true },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#16a34a', downColor: '#dc2626', borderUpColor: '#16a34a', borderDownColor: '#dc2626',
    wickUpColor: '#16a34a', wickDownColor: '#dc2626',
  });
  const times = data.times || [], candles = data.candles || [];
  candleSeries.setData(candles.map((c, i) => ({ time: times[i], open: c.o, high: c.h, low: c.l, close: c.c })));

  const volSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  volSeries.setData((data.volume || []).map((v, i) => ({
    time: times[i], value: v.y, color: v.up ? 'rgba(22,163,74,0.45)' : 'rgba(220,38,38,0.45)'
  })));

  const ema20S = chart.addLineSeries({ color: '#fbbf24', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const ema10S = chart.addLineSeries({ color: '#a5b4fc', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  ema20S.setData((data.ema20 || []).map((v, i) => ({ time: times[i], value: v })).filter(p => p.value != null));
  ema10S.setData((data.ema10 || []).map((v, i) => ({ time: times[i], value: v })).filter(p => p.value != null));

  const stSeries = [];
  function addSegments(arr, color) {
    let seg = [];
    const flush = () => {
      if (!seg.length) return;
      const s = chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      s.setData(seg); stSeries.push(s); seg = [];
    };
    for (let i = 0; i < arr.length; i++) {
      if (arr[i] != null) seg.push({ time: times[i], value: arr[i] }); else flush();
    }
    flush();
  }
  addSegments(data.st_green || [], '#16a34a');
  addSegments(data.st_red || [], '#dc2626');

  if (data.pivots) {
    const pv = { PP:'#cbd5e1', R1:'#22c55e', R2:'#16a34a', R3:'#15803d', S1:'#ef4444', S2:'#dc2626', S3:'#b91c1c' };
    Object.entries(data.pivots).forEach(([k, v]) => {
      if (v == null) return;
      candleSeries.createPriceLine({
        price: v, color: pv[k] || '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: false, title: '',
      });
    });
  }

  if (tf === '1d') {
    const markers = [];
    (data.condition_met_flag_1d || []).forEach((f, i) => {
      if (f) markers.push({ time: times[i], position: 'belowBar', color: '#d97706', shape: 'arrowUp', text: '' });
    });
    if (markers.length) candleSeries.setMarkers(markers);
  }

  restoreView(chart, view, candles.length);
  chartReg[key] = { chart, candleSeries, n: candles.length };

  if (tf === '1w') renderLineChart(id, data, keep);   // Weekly Line shares the same weekly data
}

// ── Weekly Line chart + trend-line drawing (time-anchored, so lines survive ◀ ▶ stepping) ──
function getDraw(id) {
  if (!drawState[id]) drawState[id] = { active: false, pending: null, lines: [] };
  return drawState[id];
}
function logicalOf(id, p) {
  const times = weeklyTimes[id] || [];
  const i = times.indexOf(p.t);
  if (i >= 0) return i + p.frac;
  if (!times.length) return p.frac;
  const wk = (new Date(p.t + 'T00:00:00Z') - new Date(times[0] + 'T00:00:00Z')) / 604800000;   // anchor fell outside the window
  return Math.round(wk) + p.frac;
}
function anchorFromLogical(id, logical, price) {
  const times = weeklyTimes[id] || [];
  const i = Math.min(times.length - 1, Math.max(0, Math.round(logical)));
  return { t: times[i], frac: logical - i, price };
}

function renderLineChart(id, weeklyData, keep) {
  const key = `${id}_wline`;
  const container = $(`chartdiv-${id}-wline`);
  if (!container) return;
  const view = keep ? captureView(key) : null;
  disposeChart(key);
  container.innerHTML = '';
  const sz = wlineSize();
  container.style.width = sz.width + 'px'; container.style.height = sz.h + 'px';

  const st = getDraw(id);
  const chart = LightweightCharts.createChart(container, {
    width: sz.width, height: sz.h,
    layout: { background: { color: '#000000' }, textColor: '#d1d5db', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.06)' }, horzLines: { color: 'rgba(255,255,255,0.06)' } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, rightOffset: 2, fixLeftEdge: true, lockVisibleTimeRangeOnResize: true },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: { mouseWheel: !st.active, pressedMouseMove: !st.active, horzTouchDrag: !st.active, vertTouchDrag: false },
    handleScale: { mouseWheel: !st.active, pinch: !st.active, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
  });
  const lineSeries = chart.addLineSeries({ color: '#60a5fa', lineWidth: 2, priceLineVisible: true, lastValueVisible: true });
  const times = weeklyData.times || [], candles = weeklyData.candles || [];
  lineSeries.setData(candles.map((c, i) => ({ time: times[i], value: c.c })).filter(p => p.value != null));
  restoreView(chart, view, candles.length);

  weeklyTimes[id] = times;
  chartReg[key] = { chart, lineSeries, drawnSeries: [], n: candles.length };
  st.lines.forEach(l => { l.series = null; _paintLine(id, l); });     // re-draw your lines on the new data

  chart.subscribeClick(param => {
    const s = getDraw(id);
    if (!s.active || !param || param.logical == null || !param.point) return;
    const price = lineSeries.coordinateToPrice(param.point.y);
    if (price == null) return;
    const pt = anchorFromLogical(id, param.logical, price);
    if (!s.pending) { s.pending = pt; setStatus(`${rows[id] ? rows[id].symbol : ''}: first point set — click the end point`); }
    else {
      const line = { a: s.pending, b: pt, series: null };
      s.pending = null; s.lines.push(line); _paintLine(id, line);
      setStatus(`${rows[id] ? rows[id].symbol : ''}: line drawn — drag either dot to adjust`);
    }
  });
  bindDrag(id, container);
  updateDrawButtonUI(id);
}

function _lineData(id, a, b) {
  const times = weeklyTimes[id] || [];
  if (!times.length) return [];
  const l1 = logicalOf(id, a), l2 = logicalOf(id, b);
  const dl = l2 - l1;
  const clampI = l => Math.min(times.length - 1, Math.max(0, Math.round(l)));
  if (Math.abs(dl) < 1e-6) return [{ time: times[clampI(l1)], value: a.price }];
  const slope = (b.price - a.price) / dl;
  return times.map((t, i) => ({ time: t, value: a.price + slope * (i - l1) }));   // extends across the whole chart
}

function _paintLine(id, line) {
  const reg = chartReg[`${id}_wline`]; if (!reg) return;
  if (line.series) {
    try { reg.chart.removeSeries(line.series); } catch (e) {}
    reg.drawnSeries = reg.drawnSeries.filter(s => s !== line.series);
  }
  const s = reg.chart.addLineSeries({
    color: '#f472b6', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
  });
  s.setData(_lineData(id, line.a, line.b));
  const times = weeklyTimes[id] || [];
  const mk = p => {
    const l = logicalOf(id, p);
    return (l >= 0 && l <= times.length - 1) ? { time: times[Math.round(l)], position: 'inBar', color: '#f472b6', shape: 'circle', text: '' } : null;
  };
  const marks = [mk(line.a), mk(line.b)].filter(Boolean).sort((x, y) => (x.time > y.time ? 1 : x.time < y.time ? -1 : 0));
  if (marks.length) s.setMarkers(marks);
  line.series = s; reg.drawnSeries.push(s);
}

function bindDrag(id, container) {
  if (container._bound) return;      // DOM listeners bound once per container
  container._bound = true;
  const HIT = 10;
  const findHandle = (x, y) => {
    const reg = chartReg[`${id}_wline`]; if (!reg) return null;
    const st = getDraw(id);
    for (let li = 0; li < st.lines.length; li++) {
      for (const pk of ['a', 'b']) {
        const p = st.lines[li][pk];
        const px = reg.chart.timeScale().logicalToCoordinate(logicalOf(id, p));
        const py = reg.lineSeries.priceToCoordinate(p.price);
        if (px == null || py == null) continue;
        if (Math.hypot(px - x, py - y) <= HIT) return { lineIndex: li, pointKey: pk };
      }
    }
    return null;
  };
  container.addEventListener('mousedown', e => {
    if (!getDraw(id).active) return;
    const rc = container.getBoundingClientRect();
    const hit = findHandle(e.clientX - rc.left, e.clientY - rc.top);
    if (hit) { dragState[id] = hit; e.stopPropagation(); e.preventDefault(); }
  }, true);
  container.addEventListener('mousemove', e => {
    const drag = dragState[id]; if (!drag) return;
    const reg = chartReg[`${id}_wline`]; if (!reg) return;
    const rc = container.getBoundingClientRect();
    const logical = reg.chart.timeScale().coordinateToLogical(e.clientX - rc.left);
    const price = reg.lineSeries.coordinateToPrice(e.clientY - rc.top);
    if (logical == null || price == null) return;
    const line = getDraw(id).lines[drag.lineIndex]; if (!line) return;
    line[drag.pointKey] = anchorFromLogical(id, logical, price);
    _paintLine(id, line);
  });
  window.addEventListener('mouseup', () => { dragState[id] = null; });
}

function toggleDrawMode(id) {
  const st = getDraw(id);
  st.active = !st.active; st.pending = null;
  const reg = chartReg[`${id}_wline`];
  if (reg) reg.chart.applyOptions({
    handleScroll: { mouseWheel: !st.active, pressedMouseMove: !st.active, horzTouchDrag: !st.active, vertTouchDrag: false },
    handleScale: { mouseWheel: !st.active, pinch: !st.active, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
  });
  updateDrawButtonUI(id);
  const sym = rows[id] ? rows[id].symbol : '';
  setStatus(st.active ? `${sym}: Draw mode ON — click two points` : `${sym}: Draw mode OFF`);
}
function updateDrawButtonUI(id) {
  const st = getDraw(id), btn = $(`drawbtn-${id}`), wrap = $(`chartdiv-${id}-wline`);
  if (btn) btn.classList.toggle('active', st.active);
  if (wrap) wrap.classList.toggle('draw-mode', st.active);
}
function clearDrawings(id) {
  const reg = chartReg[`${id}_wline`], st = getDraw(id);
  st.lines = []; st.pending = null; dragState[id] = null;
  if (reg && reg.drawnSeries) {
    reg.drawnSeries.forEach(s => { try { reg.chart.removeSeries(s); } catch (e) {} });
    reg.drawnSeries = [];
  }
  setStatus(`${rows[id] ? rows[id].symbol : ''}: drawings cleared`);
}

window.addEventListener('DOMContentLoaded', () => {
  $('gEnd').max = todayStr();
  buildSizeBar();
  updateHeader();
  renderChips();
  updateEmpty();
});
</script>
</body>
</html>
"""

# ── startup ───────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"✅ Loaded {len(STOCKS)} stocks from matched_stocks1.txt")
    print(f"✅ Condition met data: {len(condition_met_map)} stocks")

    threading.Thread(target=load_nse_universe, daemon=True).start()
    time.sleep(1)

    def get_free_port(preferred=5000):
        try:
            s = socket.socket(); s.bind(("", preferred)); s.close(); return preferred
        except OSError:
            s = socket.socket(); s.bind(("", 0)); port = s.getsockname()[1]; s.close(); return port

    PORT = get_free_port(5000)

    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
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