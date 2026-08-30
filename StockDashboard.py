#Interacitve charts code with no auto load
import subprocess
import sys
import shutil

def _ensure(pip_name, import_name=None):
    """Install pip_name if import_name (or pip_name) isn't importable."""
    import_name = import_name or pip_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"📦 Installing missing package: {pip_name} ...")
        subprocess.run([sys.executable, "-m", "pip", "install", pip_name, "-q"], check=True)

# Packages that Colab does NOT pre-install and that vanish on session restart
_ensure("beautifulsoup4", "bs4")
_ensure("python-dateutil", "dateutil")
_ensure("flask", "flask")
_ensure("yfinance", "yfinance")
import os
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

import os
import ast
import json
import time
import random
import logging
import threading
import socket
import re
import requests as _req_lib
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from dateutil import parser as _dateutil_parser

IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
logging.basicConfig(level=logging.ERROR)


YF_MIN_INTERVAL  = 0.8   # seconds enforced between ANY two yfinance calls
YF_MAX_RETRIES   = 5     # retry attempts per call before giving up
YF_BASE_BACKOFF  = 5     # first retry waits ~5s, then doubles each attempt

_yf_lock      = threading.Lock()
_yf_last_call = [0.0]


def _yf_wait():
    """Blocks just long enough to guarantee at least YF_MIN_INTERVAL seconds
    have passed since the last yfinance request made by ANY thread."""
    with _yf_lock:
        now = time.time()
        elapsed = now - _yf_last_call[0]
        if elapsed < YF_MIN_INTERVAL:
            time.sleep(YF_MIN_INTERVAL - elapsed)
        _yf_last_call[0] = time.time()


def _yf_call(func, label=""):
    """
    Runs `func()` (a yfinance network call, passed as a lambda), spacing it
    via _yf_wait() first. On a rate-limit error, sleeps with exponential
    backoff + jitter and retries, up to YF_MAX_RETRIES times.
    """
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

BASE_DIR = r"C:\Users\sheik\PycharmProjects\MyProject"
os.makedirs(BASE_DIR, exist_ok=True)

log_dir = f"{BASE_DIR}/logs/"
os.makedirs(log_dir, exist_ok=True)

CHART_DIR_5M     = f"{BASE_DIR}/5mnsCharts"
CHART_DIR_1D     = f"{BASE_DIR}/1daycharts"
CHART_DIR_1W     = f"{BASE_DIR}/weeklycharts"
DROP_DETAILS_LOG = f"{BASE_DIR}/Dropdetails.txt"
FUNDAMENTALS_CACHE_FILE = f"{BASE_DIR}/fundamentals_cache1.json"
NEWS_CACHE_FILE  = f"{BASE_DIR}/news_cache1.json"
ALL_NEWS_JSON_FILE = f"{BASE_DIR}/all_news_items1.json"
END_DATE_FILE    = f"{BASE_DIR}/end_date.txt"

for _d in [CHART_DIR_5M, CHART_DIR_1D, CHART_DIR_1W]:
    os.makedirs(_d, exist_ok=True)

# ── chart mode ───────────────────────────────────────────────
print("\n" + "="*40)
print("  SELECT CHART MODE")
print("="*40)
print("  1. New Charts  (generate fresh charts)")
print("  2. Old Charts  (load saved charts)")
print("="*40)

CHART_MODE = ""
while CHART_MODE not in ("1", "2"):
    CHART_MODE = input("Enter choice (1 or 2): ").strip()

print(f"\n✅ Mode selected: {'New Charts' if CHART_MODE == '1' else 'Old Charts'}\n")

# ── 5-min chart date mode (only for New Charts) ──────────────
FIVEM_DATE_MODE  = ""
FIVEM_START_DATE = None
FIVEM_END_DATE   = None
FIVEM_END_TIME   = None

if CHART_MODE == "1":
    print("\n" + "="*40)
    print("  SELECT 5-MIN CHART DATE MODE")
    print("="*40)
    print("  1. Auto Dates    (last 3 trading days)")
    print("  2. Manual Dates  (custom Start Date / End Date / End Time)")
    print("="*40)

    while FIVEM_DATE_MODE not in ("1", "2"):
        FIVEM_DATE_MODE = input("Enter choice (1 or 2): ").strip()

    if FIVEM_DATE_MODE == "2":
        while FIVEM_START_DATE is None:
            try:
                _s = input("Enter Start Date (YYYY-MM-DD): ").strip()
                FIVEM_START_DATE = datetime.strptime(_s, "%Y-%m-%d").date()
            except Exception:
                print("⚠ Invalid date format. Please use YYYY-MM-DD.")
                FIVEM_START_DATE = None
        while FIVEM_END_DATE is None:
            try:
                _e = input("Enter End Date (YYYY-MM-DD): ").strip()
                FIVEM_END_DATE = datetime.strptime(_e, "%Y-%m-%d").date()
            except Exception:
                print("⚠ Invalid date format. Please use YYYY-MM-DD.")
                FIVEM_END_DATE = None
        while FIVEM_END_TIME is None:
            try:
                _t = input("Enter End Time (HH:MM, 24-hr e.g. 13:30): ").strip()
                FIVEM_END_TIME = datetime.strptime(_t, "%H:%M").time()
            except Exception:
                print("⚠ Invalid time format. Please use HH:MM.")
                FIVEM_END_TIME = None
        print(f"\n✅ 5-min Manual range: {FIVEM_START_DATE} → {FIVEM_END_DATE}  "
              f"(ending at {FIVEM_END_TIME.strftime('%H:%M')})\n")

        try:
            with open(END_DATE_FILE, "w") as _f:
                _f.write(FIVEM_END_DATE.strftime("%Y-%m-%d"))
            print(f"✅ End date saved: {FIVEM_END_DATE}")
        except Exception as _e:
            print(f"⚠ Could not save end date: {_e}")

    else:
        print("\n✅ 5-min Date Mode: Auto (last 3 trading days)\n")
        try:
            _today = datetime.now(IST).date()
            _d_temp = _today
            _trading = []
            while len(_trading) < 5:
                if _d_temp.weekday() < 5:
                    _trading.append(_d_temp)
                _d_temp -= timedelta(days=1)
            _auto_end = _trading[0]
            with open(END_DATE_FILE, "w") as _f:
                _f.write(_auto_end.strftime("%Y-%m-%d"))
            print(f"✅ End date saved (auto): {_auto_end}")
        except Exception as _e:
            print(f"⚠ Could not save auto end date: {_e}")

else:
    FIVEM_DATE_MODE = "1"
    print("✅ Old Charts mode — skipping date selection (loading saved charts)\n")
    print("ℹ️  Old Charts mode: automatic preload uses saved JSON files only. Manual ↻ refresh buttons will still fetch live data on demand.\n")

    SAVED_END_DATE = None
    if os.path.exists(END_DATE_FILE):
        try:
            with open(END_DATE_FILE, "r") as _f:
                SAVED_END_DATE = datetime.strptime(_f.read().strip(), "%Y-%m-%d").date()
            print(f"✅ Loaded saved end date: {SAVED_END_DATE}")
        except Exception as _e:
            print(f"⚠ Could not load end date: {_e}")
    else:
        print("⚠ No saved end date found. Quotes will use today's date.")

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

def yf_symbol(name):
    return f"{name}.NS"

CONDITION_MET_EXCEL = f"{BASE_DIR}/1hour_Condition_Met_Stocks.xlsx"
condition_met_map   = {}
accepted_date_map   = {}   # { symbol: [ {"condition_met": "YYYY-MM-DD", "accepted": "YYYY-MM-DD"}, ... ] } sorted newest accepted first
ema_distance_map    = {}   # { symbol: [ {"condition_met": "YYYY-MM-DD", "ema_distance": float}, ... ] } sorted newest condition_met first
never_below_10ema_map = {}   # { symbol: [ {"condition_met": "YYYY-MM-DD", "value": "Yes"/"No"}, ... ] } sorted newest condition_met first

def _parse_excel_date(date_val):
    if pd.isna(date_val):
        return ""
    return date_val.strftime("%Y-%m-%d") if hasattr(date_val, 'strftime') \
           else str(date_val).strip()[:10]

def load_condition_met_excel():
    global condition_met_map, accepted_date_map, ema_distance_map, never_below_10ema_map
    condition_met_map = {}
    accepted_date_map  = {}
    ema_distance_map   = {}
    never_below_10ema_map = {}
    if not os.path.exists(CONDITION_MET_EXCEL):
        print(f"⚠ Condition met Excel not found: {CONDITION_MET_EXCEL}")
        return
    try:
        df_cond = pd.read_excel(CONDITION_MET_EXCEL)
        df_cond.columns = [c.strip() for c in df_cond.columns]
        if "StockName" not in df_cond.columns or "ConditionMetDate" not in df_cond.columns:
            print(f"⚠ Excel columns not found. Got: {list(df_cond.columns)}")
            return
        has_accepted_col = "FinalAcceptedDate" in df_cond.columns
        has_ema_dist_col = "EMADistancePct" in df_cond.columns
        has_never_below_col = "CloseNeverBelow10EMA" in df_cond.columns
        for _, row in df_cond.iterrows():
            sym      = _to_plain_name(str(row["StockName"]))
            date_str = _parse_excel_date(row["ConditionMetDate"])
            if not date_str:
                continue
            condition_met_map.setdefault(sym, [])
            if date_str not in condition_met_map[sym]:
                condition_met_map[sym].append(date_str)

            if has_accepted_col:
                accepted_str = _parse_excel_date(row["FinalAcceptedDate"])
                if accepted_str:
                    accepted_date_map.setdefault(sym, [])
                    accepted_date_map[sym].append({
                        "condition_met": date_str,
                        "accepted": accepted_str,
                    })

            if has_ema_dist_col:
                ema_dist_val = row["EMADistancePct"]
                if pd.notna(ema_dist_val):
                    ema_distance_map.setdefault(sym, [])
                    ema_distance_map[sym].append({
                        "condition_met": date_str,
                        "ema_distance": float(ema_dist_val),
                    })

            if has_never_below_col:
                nb_val = row["CloseNeverBelow10EMA"]
                if pd.notna(nb_val):
                    nb_str = str(nb_val).strip()
                    never_below_10ema_map.setdefault(sym, [])
                    never_below_10ema_map[sym].append({
                        "condition_met": date_str,
                        "value": nb_str,
                    })

        for sym in accepted_date_map:
            accepted_date_map[sym].sort(key=lambda x: x["accepted"], reverse=True)
        for sym in ema_distance_map:
            ema_distance_map[sym].sort(key=lambda x: x["condition_met"], reverse=True)
        for sym in never_below_10ema_map:
            never_below_10ema_map[sym].sort(key=lambda x: x["condition_met"], reverse=True)
        print(f"✅ Condition met data loaded for {len(condition_met_map)} stocks")
        print(f"✅ Accepted date data loaded for {len(accepted_date_map)} stocks")
        print(f"✅ EMA distance data loaded for {len(ema_distance_map)} stocks")
        print(f"✅ Never-below-10EMA data loaded for {len(never_below_10ema_map)} stocks")
    except Exception as e:
        print(f"❌ Failed to load condition met Excel: {e}")

load_condition_met_excel()

def get_last_n_trading_days(n=3):
    today   = datetime.now(IST).date()
    trading = []
    d       = today
    while len(trading) < n + 5:
        if d.weekday() < 5:
            trading.append(d)
        d -= timedelta(days=1)
    trading = trading[:n]
    return trading[-1], trading[0]

live_data    = {}
last_updated = ""
live_running = True
preload_done = {"5m": False, "1d": False, "1w": False}

chart_cache      = {"5m": {}, "1d": {}, "1w": {}}
chart_cache_lock = threading.Lock()

refresh_ts_lock  = threading.Lock()
refresh_ts       = {}

fundamentals_cache      = {}
fundamentals_cache_lock = threading.Lock()

# ── Fundamentals refresh status (on-demand only, via buttons) ──
fundamentals_refresh_status = {"running": False, "done": 0, "total": 0, "current": ""}
fundamentals_refresh_lock   = threading.Lock()
chart_refresh_status = {
    "5m": {"running": False, "done": 0, "total": 0, "current": ""},
    "1d": {"running": False, "done": 0, "total": 0, "current": ""},
    "1w": {"running": False, "done": 0, "total": 0, "current": ""},
}
chart_refresh_lock = threading.Lock()

# ── Load-from-file status (Old Charts mode "Load" buttons, on-demand only) ──
load_status = {
    "5m": {"running": False, "done": 0, "total": 0, "current": ""},
    "1d": {"running": False, "done": 0, "total": 0, "current": ""},
    "1w": {"running": False, "done": 0, "total": 0, "current": ""},
}
load_status_lock = threading.Lock()

# ── News cache ────────────────────────────────────────────────
news_cache      = {}   # { symbol: [ {headline, url, timestamp_ist, provider}, ... ] }
news_cache_lock = threading.Lock()

# ── News refresh status (on-demand only, via buttons) ──────────
news_refresh_status = {"running": False, "done": 0, "total": 0, "current": ""}
news_refresh_lock   = threading.Lock()

def load_news_from_file():
    global news_cache
    if not os.path.exists(NEWS_CACHE_FILE):
        return
    try:
        with open(NEWS_CACHE_FILE, "r") as f:
            data = json.load(f)
        with news_cache_lock:
            news_cache.update(data)
        print(f"✅ News cache loaded for {len(data)} stocks")
    except Exception as e:
        print(f"⚠ Failed to load news cache: {e}")

def save_news_to_file():
    try:
        with news_cache_lock:
            data = dict(news_cache)
        with open(NEWS_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠ Failed to save news cache: {e}")

def save_all_news_items(symbol_name, unique_items):
    try:
        existing = {}
        if os.path.exists(ALL_NEWS_JSON_FILE):
            try:
                with open(ALL_NEWS_JSON_FILE, "r") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}

        clean_items = []
        for it in unique_items:
            clean_items.append({
                "headline":      it.get("headline", ""),
                "url":           it.get("url", ""),
                "timestamp_ist": it.get("timestamp_ist", ""),
                "provider":      it.get("provider", ""),
                "source_tag":    it.get("source_tag", ""),
            })

        existing[symbol_name] = {
            "fetched_at_ist": datetime.now(IST).strftime("%d %b %Y %H:%M:%S IST"),
            "total_items":    len(clean_items),
            "items":          clean_items,
        }

        with open(ALL_NEWS_JSON_FILE, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

    except Exception as e:
        print(f"⚠ Failed to save all_news_items for {symbol_name}: {e}")

# ── News debug raw storage ────────────────────────────────────
news_debug_raw  = {}   # { symbol: { source: [items...] } }
news_debug_lock = threading.Lock()

def fetch_tv_news(symbol_name, max_items=10):
    all_items = []
    full_name = ""   # resolved from yfinance, e.g. "Premier Energies Limited"

    # ── Source 1: yfinance ────────────────────────────────────
    try:
        t = yf.Ticker(f"{symbol_name}.NS")
        try:
            info = _yf_call(lambda: t.info, label=f"{symbol_name} info") or {}
            full_name = (info.get("longName") or info.get("shortName") or "").strip()
        except Exception as e:
            print(f"  [yfinance] {symbol_name} could not resolve full name: {e}")

        news = _yf_call(lambda: t.news, label=f"{symbol_name} news") or []
        cnt  = 0
        for entry in news[:max_items * 2]:
            content  = entry.get("content", {})
            headline = (content.get("title") or entry.get("title", "")).strip()
            if not headline:
                continue
            url = ""
            click_url = content.get("clickThroughUrl") or {}
            if isinstance(click_url, dict):
                url = click_url.get("url", "")
            if not url:
                cu = content.get("canonicalUrl", {})
                url = cu.get("url", "") if isinstance(cu, dict) else ""
            epoch = 0
            timestamp_ist = ""
            pub = content.get("pubDate") or entry.get("providerPublishTime")
            if pub:
                try:
                    if isinstance(pub, str):
                        from dateutil import parser as dp
                        dt_utc = dp.parse(pub).astimezone(timezone.utc)
                        epoch  = int(dt_utc.timestamp())
                    else:
                        epoch  = int(pub)
                        dt_utc = datetime.utcfromtimestamp(epoch).replace(tzinfo=timezone.utc)
                    timestamp_ist = dt_utc.astimezone(IST).strftime("%d %b %H:%M")
                except Exception:
                    pass
            provider = ""
            prov = content.get("provider", {})
            if isinstance(prov, dict):
                provider = prov.get("displayName", "")
            all_items.append({
                "headline": headline, "url": url,
                "timestamp_ist": timestamp_ist, "provider": provider,
                "epoch": epoch, "source_tag": "yfinance",
            })
            cnt += 1
        print(f"  [yfinance] {symbol_name}: {cnt} items | full_name='{full_name}'")
    except Exception as e:
        print(f"  [yfinance] {symbol_name} error: {e}")

    # ── Build BOTH query variants: full name + trimmed name ───
    _SUFFIX_PATTERN = re.compile(
        r'\b(limited|ltd\.?|industries|inds\.?|corporation|corp\.?|company|co\.?)\b\s*$',
        re.IGNORECASE
    )
    clean_name = full_name
    while True:
        new_clean = _SUFFIX_PATTERN.sub('', clean_name).strip()
        if new_clean == clean_name:
            break
        clean_name = new_clean

    query_variants = []
    if full_name:
        query_variants.append(full_name)
    if clean_name and clean_name.lower() != full_name.lower():
        query_variants.append(clean_name)
    if not query_variants:
        query_variants.append(symbol_name)

    _name_source = clean_name or full_name or symbol_name
    relevance_words = [w.lower() for w in re.findall(r"[A-Za-z]+", _name_source) if len(w) >= 4]
    sym_lower = symbol_name.lower()

    def _is_relevant(headline):
        hdl_lower = headline.lower()
        if sym_lower in hdl_lower:
            return True
        return any(w in hdl_lower for w in relevance_words)

    # ── Source 2: Google News RSS (runs once per query variant) ──
    try:
        import xml.etree.ElementTree as ET
        from urllib.parse import quote as _quote
        total_count = 0
        skipped_count = 0
        for query in query_variants:
            gn_url = f"https://news.google.com/rss/search?q={_quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
            resp   = _req_lib.get(gn_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                root  = ET.fromstring(resp.content)
                count = 0
                for item in root.findall(".//item"):
                    title   = (item.findtext("title")   or "").strip()
                    link    = (item.findtext("link")     or "").strip()
                    pub_str = (item.findtext("pubDate")  or "").strip()
                    src_el  = item.find("source")
                    provider = src_el.text.strip() if src_el is not None else "Google News"
                    if not title:
                        continue
                    if not _is_relevant(title):
                        skipped_count += 1
                        continue
                    epoch = 0; timestamp_ist = ""
                    if pub_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            dt    = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                            epoch = int(dt.timestamp())
                            timestamp_ist = dt.astimezone(IST).strftime("%d %b %H:%M")
                        except Exception:
                            pass
                    all_items.append({
                        "headline": title, "url": link,
                        "timestamp_ist": timestamp_ist, "provider": provider,
                        "epoch": epoch, "source_tag": "google_news",
                    })
                    count += 1
                total_count += count
                print(f"  [google_news] {symbol_name}: {count} items (query='{query}')")
            else:
                print(f"  [google_news] {symbol_name}: HTTP {resp.status_code} (query='{query}')")
        print(f"  [google_news] {symbol_name}: {total_count} total items across "
              f"{len(query_variants)} query variant(s), {skipped_count} filtered out as irrelevant")
    except Exception as e:
        print(f"  [google_news] {symbol_name} error: {e}")

    # ── Source 3: Yahoo Finance RSS ───────────────────────────
    try:
        import xml.etree.ElementTree as ET
        yh_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol_name}.NS&region=IN&lang=en-IN"
        resp   = _req_lib.get(yh_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            root  = ET.fromstring(resp.content)
            count = 0
            for item in root.findall(".//item"):
                title   = (item.findtext("title")   or "").strip()
                link    = (item.findtext("link")     or "").strip()
                pub_str = (item.findtext("pubDate")  or "").strip()
                if not title:
                    continue
                epoch = 0; timestamp_ist = ""
                if pub_str:
                    try:
                        from email.utils import parsedate_to_datetime
                        dt    = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                        epoch = int(dt.timestamp())
                        timestamp_ist = dt.astimezone(IST).strftime("%d %b %H:%M")
                    except Exception:
                        pass
                all_items.append({
                    "headline": title, "url": link,
                    "timestamp_ist": timestamp_ist, "provider": "Yahoo Finance",
                    "epoch": epoch, "source_tag": "yahoo_rss",
                })
                count += 1
            print(f"  [yahoo_rss] {symbol_name}: {count} items")
        else:
            print(f"  [yahoo_rss] {symbol_name}: HTTP {resp.status_code}")
    except Exception as e:
        print(f"  [yahoo_rss] {symbol_name} error: {e}")

    # ── Source 4: Bing News RSS (runs once per query variant) ────
    try:
        import xml.etree.ElementTree as ET
        from urllib.parse import quote as _quote
        total_count = 0
        skipped_count = 0
        for query in query_variants:
            bn_url = f"https://www.bing.com/news/search?q={_quote(query)}&format=RSS"
            resp   = _req_lib.get(bn_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.status_code == 200:
                root  = ET.fromstring(resp.content)
                count = 0
                for item in root.findall(".//item"):
                    title   = (item.findtext("title")   or "").strip()
                    link    = (item.findtext("link")     or "").strip()
                    pub_str = (item.findtext("pubDate")  or "").strip()
                    if not title:
                        continue
                    if not _is_relevant(title):
                        skipped_count += 1
                        continue
                    epoch = 0; timestamp_ist = ""
                    if pub_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            dt    = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                            epoch = int(dt.timestamp())
                            timestamp_ist = dt.astimezone(IST).strftime("%d %b %H:%M")
                        except Exception:
                            pass
                    all_items.append({
                        "headline": title, "url": link,
                        "timestamp_ist": timestamp_ist, "provider": "Bing News",
                        "epoch": epoch, "source_tag": "bing_news",
                    })
                    count += 1
                total_count += count
                print(f"  [bing_news] {symbol_name}: {count} items (query='{query}')")
            else:
                print(f"  [bing_news] {symbol_name}: HTTP {resp.status_code} (query='{query}')")
        print(f"  [bing_news] {symbol_name}: {total_count} total items across "
              f"{len(query_variants)} query variant(s), {skipped_count} filtered out as irrelevant")
    except Exception as e:
        print(f"  [bing_news] {symbol_name} error: {e}")

    # ── Source 5: NSE Corporate Announcements (block noise, show rest) ──
    NSE_BLOCK_PHRASES = [
        "disclosure under sebi", "disclosure pursuant to", "disclosure of reasons",
        "continual disclosure", "disclosure under regulation", "disclosure under clause",
        "certificate under", "compliance certificate", "reconciliation of share capital",
        "statement of investor complaints", "investor grievance", "investor complaint",
        "trading window", "closure of trading window", "opening of trading window",
        "shareholding pattern", "regulation 29", "regulation 31", "regulation 74",
        "insider trading", "code of conduct", "newspaper publication", "newspaper advertisement",
        "intimation of board meeting", "outcome of board meeting", "proceedings of",
        "minutes of", "corrigendum", "erratum", "notice of agm", "notice of egm",
        "notice of extraordinary", "postal ballot", "e-voting", "evoting",
        "scrutinizer report", "voting results", "annual report", "annual return",
        "loss of share certificate", "duplicate share certificate", "transfer of shares",
        "transmission of shares", "unclaimed dividend", "iepf", "change in address",
        "change in registrar", "appointment of registrar", "book closure for agm",
    ]

    try:
        nse_session = _req_lib.Session()
        nse_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "text/html,application/xhtml+xml",
        })
        nse_session.get("https://www.nseindia.com", timeout=8)
        nse_session.headers.update({
            "Accept":           "application/json",
            "Referer":          "https://www.nseindia.com/",
            "X-Requested-With": "XMLHttpRequest",
        })
        nse_url = (f"https://www.nseindia.com/api/corporate-announcements"
                   f"?index=equities&symbol={symbol_name}")
        resp = nse_session.get(nse_url, timeout=10)
        if resp.status_code == 200:
            data  = resp.json()
            count = 0
            for entry in (data if isinstance(data, list) else []):
                subject = (entry.get("subject") or entry.get("desc") or "").strip()
                if not subject:
                    continue
                subj_lower = subject.lower()
                if any(bp in subj_lower for bp in NSE_BLOCK_PHRASES):
                    continue
                bm_id = entry.get("bm_id") or entry.get("id", "")
                link  = (
                    f"https://www.nseindia.com/api/corporate-announcements-pdf?id={bm_id}"
                    if bm_id else
                    "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
                )
                an_date = entry.get("an_dt") or entry.get("date", "")
                epoch = 0; timestamp_ist = ""
                if an_date:
                    try:
                        dt = datetime.strptime(an_date[:16], "%d-%b-%Y %H:%M").replace(tzinfo=IST)
                        epoch = int(dt.timestamp())
                        timestamp_ist = dt.strftime("%d %b %H:%M")
                    except Exception:
                        timestamp_ist = an_date[:12]
                all_items.append({
                    "headline": subject, "url": link,
                    "timestamp_ist": timestamp_ist, "provider": "NSE",
                    "epoch": epoch, "source_tag": "nse",
                })
                count += 1
                if count >= max_items:
                    break
            print(f"  [nse] {symbol_name}: {count} items (after noise filter)")
        else:
            print(f"  [nse] {symbol_name}: HTTP {resp.status_code}")
    except Exception as e:
        print(f"  [nse] {symbol_name} error: {e}")

    with news_debug_lock:
        news_debug_raw[symbol_name] = {
            "yfinance":    [i for i in all_items if i.get("source_tag") == "yfinance"],
            "google_news": [i for i in all_items if i.get("source_tag") == "google_news"],
            "yahoo_rss":   [i for i in all_items if i.get("source_tag") == "yahoo_rss"],
            "bing_news":   [i for i in all_items if i.get("source_tag") == "bing_news"],
            "nse":         [i for i in all_items if i.get("source_tag") == "nse"],
        }

    seen   = set()
    unique = []
    for item in all_items:
        key = "".join(item["headline"].lower().split())[:60]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(key=lambda x: x["epoch"], reverse=True)
    save_all_news_items(symbol_name, unique)

    today_ist = datetime.now(IST).date()

    def _item_is_today(it):
        ts = it.get("timestamp_ist", "")
        if not ts:
            return False
        try:
            dt = datetime.strptime(f"{ts} {today_ist.year}", "%d %b %H:%M %Y")
            return dt.date() == today_ist
        except Exception:
            return False

    today_items = [it for it in unique if _item_is_today(it)]
    if today_items:
        final = today_items[:25]
        if len(final) < max_items:
            older = [it for it in unique if it not in today_items]
            final += older[:max_items - len(final)]
    else:
        final = unique[:max_items]

    for item in final:
        item.pop("epoch",      None)
        item.pop("source_tag", None)

    print(f"  ✅ {symbol_name}: {len(final)} news items served "
          f"({len(today_items)} from today) of {len(unique)} total deduped, "
          f"all saved to all_news_items.json")
    return final


def save_news_debug_log(all_stocks_news):
    log_path     = f"{BASE_DIR}/news_debug_log.txt"
    SOURCE_ORDER = ["yfinance", "google_news", "yahoo_rss", "bing_news", "nse"]
    try:
        with news_debug_lock:
            raw_snapshot = dict(news_debug_raw)

        total_all = sum(
            sum(len(src_items) for src_items in raw.values())
            for raw in raw_snapshot.values()
        )

        lines = []
        lines.append(f"{'='*70}\n")
        lines.append(f"  NEWS DEBUG LOG — ALL RAW ITEMS (pre-dedup)\n")
        lines.append(f"  Generated : {datetime.now(IST).strftime('%d %b %Y %H:%M:%S IST')}\n")
        lines.append(f"  Stocks    : {len(all_stocks_news)}\n")
        lines.append(f"  Total raw : {total_all} items across all sources\n")
        lines.append(f"{'='*70}\n\n")

        for symbol, final_items in all_stocks_news.items():
            raw  = raw_snapshot.get(symbol, {})
            total_sym = sum(len(v) for v in raw.values())
            lines.append(f"{'─'*70}\n")
            lines.append(f"  ▶ {symbol}   (total raw: {total_sym}  |  final served: {len(final_items)})\n")
            lines.append(f"{'─'*70}\n")
            for src in SOURCE_ORDER:
                src_items = raw.get(src, [])
                lines.append(f"\n  [{src.upper()}]  {len(src_items)} items\n")
                if not src_items:
                    lines.append("    (none)\n")
                    continue
                for i, item in enumerate(src_items, 1):
                    ts   = item.get("timestamp_ist", "").ljust(14)
                    prov = item.get("provider",      "").ljust(22)
                    hdl  = item.get("headline", "")
                    url  = item.get("url",      "")
                    lines.append(f"    {i:>3}. {ts} {prov}\n")
                    lines.append(f"         {hdl}\n")
                    if url:
                        lines.append(f"         {url}\n")
                    lines.append("\n")
            lines.append(f"  ── FINAL SERVED (top {len(final_items)} after dedup+sort) ──\n")
            if not final_items:
                lines.append("    (none)\n")
            for i, item in enumerate(final_items, 1):
                ts   = item.get("timestamp_ist", "").ljust(14)
                prov = item.get("provider",      "").ljust(22)
                hdl  = item.get("headline", "")
                lines.append(f"    {i}. {ts} {prov} {hdl}\n")
            lines.append("\n\n")

        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"📄 News debug log saved → {log_path}  ({total_all} raw items total)")
    except Exception as e:
        print(f"⚠ Could not save news debug log: {e}")


def refresh_all_news_background():
    with news_refresh_lock:
        news_refresh_status["running"] = True
        news_refresh_status["done"]    = 0
        news_refresh_status["total"]   = len(STOCKS)
        news_refresh_status["current"] = ""
    print(f"\n{'='*60}\n🔄 News Refresh (All) started — {len(STOCKS)} stocks\n{'='*60}")
    try:
        for name in STOCKS:
            with news_refresh_lock:
                news_refresh_status["current"] = name
            items = fetch_tv_news(name, max_items=10)
            with news_cache_lock:
                news_cache[name] = items
            save_news_to_file()
            with news_refresh_lock:
                news_refresh_status["done"] += 1
            time.sleep(1.5)
        with news_cache_lock:
            save_news_debug_log(dict(news_cache))
        print(f"\n{'='*60}\n✅ News refresh (all) complete for {len(news_cache)} stocks\n{'='*60}\n")
    finally:
        with news_refresh_lock:
            news_refresh_status["running"] = False
            news_refresh_status["current"] = ""

def load_fundamentals_from_file():
    global fundamentals_cache
    if not os.path.exists(FUNDAMENTALS_CACHE_FILE):
        print(f"⚠ Fundamentals cache file not found: {FUNDAMENTALS_CACHE_FILE}")
        return
    try:
        with open(FUNDAMENTALS_CACHE_FILE, "r") as f:
            data = json.load(f)
        with fundamentals_cache_lock:
            fundamentals_cache.update(data)
        print(f"✅ Fundamentals loaded from file for {len(data)} stocks")
    except Exception as e:
        print(f"⚠ Failed to load fundamentals cache file: {e}")

def save_fundamentals_to_file():
    try:
        with fundamentals_cache_lock:
            data = dict(fundamentals_cache)
        with open(FUNDAMENTALS_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✅ Fundamentals cache saved to file ({len(data)} stocks)")
    except Exception as e:
        print(f"⚠ Failed to save fundamentals cache file: {e}")

def fetch_eps_history(symbol_name, ticker_obj=None):
    try:
        t = ticker_obj or yf.Ticker(symbol_name.upper() + ".NS")
        fin = _yf_call(lambda: t.income_stmt, label=f"{symbol_name} income_stmt")
        if fin is None or fin.empty:
            return []
        eps_row = None
        for label in ["Diluted EPS", "Basic EPS"]:
            if label in fin.index:
                eps_row = fin.loc[label]
                break
        if eps_row is None:
            return []
        eps_list = []
        for col in eps_row.index:
            val = eps_row[col]
            if pd.notna(val):
                year = col.year if hasattr(col, "year") else str(col)[:4]
                eps_list.append({"year": str(year), "eps": round(float(val), 2)})
        return sorted(eps_list, key=lambda x: x["year"])
    except Exception as e:
        print(f"⚠ [eps_history] {symbol_name} error: {e}")
        return []


def analyze_eps_trend(eps_list):
    result = {
        "eps_history": eps_list, "eps_cagr": None, "eps_yoy": [],
        "eps_trend_signal": None, "eps_trend_score": 0, "eps_trend_note": "",
    }
    if len(eps_list) < 2:
        result["eps_trend_signal"] = "Insufficient Data"
        return result

    values = [e["eps"] for e in eps_list]
    yoy = []
    for i in range(1, len(values)):
        prev, curr = values[i-1], values[i]
        yoy.append(None if prev == 0 else round(((curr - prev) / abs(prev)) * 100, 1))
    result["eps_yoy"] = yoy

    first, last = values[0], values[-1]
    n_years = len(values) - 1
    if first > 0 and last > 0 and n_years > 0:
        result["eps_cagr"] = round((((last / first) ** (1 / n_years)) - 1) * 100, 1)

    valid_yoy   = [g for g in yoy if g is not None]
    negatives   = sum(1 for g in valid_yoy if g < 0)
    all_positive = bool(valid_yoy) and all(g >= 0 for g in valid_yoy)
    cagr = result["eps_cagr"]

    if not valid_yoy:
        signal, score, note = "Insufficient Data", 0, ""
    elif any(v <= 0 for v in values):
        signal, score, note = "Erratic / Loss Years", -1, "EPS was negative/zero in at least one year"
    elif all_positive and cagr is not None and cagr >= 15:
        signal, score, note = "Steady Strong Growth", 2, f"CAGR {cagr}%, no down years"
    elif all_positive and cagr is not None and cagr >= 5:
        signal, score, note = "Steady Growth", 1, f"CAGR {cagr}%, no down years"
    elif negatives == 1 and cagr is not None and cagr > 0:
        signal, score, note = "Mostly Growing", 0, "One down year, overall trend still positive"
    elif negatives >= 2:
        signal, score, note = "Volatile / Imbalanced", -1, f"{negatives} declining years out of {len(valid_yoy)}"
    else:
        signal, score, note = "Flat / Mixed", 0, "No clear direction"

    result["eps_trend_signal"] = signal
    result["eps_trend_score"]  = score
    result["eps_trend_note"]   = note
    return result

def fetch_fundamentals(symbol_name):
    ticker_sym = symbol_name.upper() + ".NS"
    result = {
        "mcap": None, "roe": None, "de": None, "pe": None, "pb": None,
        "peg": None, "eps_ttm": None, "eps_forward": None,
        "pm": None, "pat_cr": None, "pat_prev_cr": None,
        "rev_cr": None, "rev_prev_cr": None,
        "signal": None, "breakdown": [], "error": None,
    }
    try:
        t = yf.Ticker(ticker_sym)
        info = _yf_call(lambda: t.info, label=f"{symbol_name} info")
        print(f"🔍 {symbol_name} | trailingEps:{info.get('trailingEps')} | forwardEps:{info.get('forwardEps')} | earningsGrowth:{info.get('earningsGrowth')} | pegRatio:{info.get('pegRatio')} | trailingPegRatio:{info.get('trailingPegRatio')}")
        mc = info.get("marketCap") or info.get("enterpriseValue")
        if mc:
            if mc >= 1e12:   result["mcap"] = f"₹{mc/1e12:.2f}T"
            elif mc >= 1e9:  result["mcap"] = f"₹{mc/1e9:.2f}B"
            elif mc >= 1e7:  result["mcap"] = f"₹{mc/1e7:.2f}Cr"
            else:            result["mcap"] = f"₹{mc:,.0f}"
        result["pe"]         = info.get("trailingPE") or info.get("forwardPE")
        result["forward_pe"] = info.get("forwardPE")
        result["pb"]         = info.get("priceToBook")
        result["peg"]        = info.get("pegRatio")
        result["eps_ttm"]      = info.get("trailingEps")
        result["eps_forward"]  = info.get("forwardEps")
        result["earnings_growth"] = info.get("earningsGrowth")
        eps_hist = fetch_eps_history(symbol_name, ticker_obj=t)
        result.update(analyze_eps_trend(eps_hist))
        roe = info.get("returnOnEquity")
        result["roe"] = round(roe * 100, 1) if roe is not None else None
        de = info.get("debtToEquity")
        result["de"] = round(de / 100, 2) if de is not None else None
        pm = info.get("profitMargins")
        result["pm"] = round(pm * 100, 1) if pm is not None else None
        try:
            qfin = _yf_call(lambda: t.quarterly_financials, label=f"{symbol_name} quarterly_financials")
            if qfin is not None and not qfin.empty:
                for label in ["Net Income", "Net Income Common Stockholders"]:
                    if label in qfin.index:
                        row = qfin.loc[label]
                        if pd.notna(row.iloc[0]):
                            result["pat_cr"] = round(row.iloc[0] / 1e7, 1)
                        if len(row) >= 5 and pd.notna(row.iloc[4]):
                            result["pat_prev_cr"] = round(row.iloc[4] / 1e7, 1)
                        break
        except Exception as e:
            print(f"⚠ [qfin PAT] {symbol_name}: {e}")
        try:
            qrev = _yf_call(lambda: t.quarterly_income_stmt, label=f"{symbol_name} quarterly_income_stmt")
            if qrev is not None and not qrev.empty:
                for label in ["Total Revenue", "Revenue"]:
                    if label in qrev.index:
                        row = qrev.loc[label]
                        if pd.notna(row.iloc[0]):
                            result["rev_cr"] = round(row.iloc[0] / 1e7, 1)
                        if len(row) >= 5 and pd.notna(row.iloc[4]):
                            result["rev_prev_cr"] = round(row.iloc[4] / 1e7, 1)
                        break
        except Exception as e:
            print(f"⚠ [qrev] {symbol_name}: {e}")
        if result["mcap"] or result["roe"] is not None:
            result = score_fundamentals(result)
            _print_fundamentals(symbol_name, result)
            return result
        print(f"⚠ [yfinance] {symbol_name} → all fields empty.")
    except Exception as e:
        print(f"⚠ [yfinance] {symbol_name} failed: {e}")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://finance.yahoo.com/",
        }
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker_sym}"
               f"?modules=defaultKeyStatistics,financialData,incomeStatementHistory")
        r = _req_lib.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            js  = r.json()
            res = js.get("quoteSummary", {}).get("result") or []
            if res:
                ks = res[0].get("defaultKeyStatistics", {})
                fd = res[0].get("financialData", {})
                mc = (ks.get("enterpriseValue") or {}).get("raw") or \
                     (ks.get("marketCap") or {}).get("raw")
                if mc:
                    if mc >= 1e12:   result["mcap"] = f"₹{mc/1e12:.2f}T"
                    elif mc >= 1e9:  result["mcap"] = f"₹{mc/1e9:.2f}B"
                    elif mc >= 1e7:  result["mcap"] = f"₹{mc/1e7:.2f}Cr"
                    else:            result["mcap"] = f"₹{mc:,.0f}"
                result["pe"] = (ks.get("trailingEps") or {}).get("raw")
                result["pb"] = (ks.get("priceToBook") or {}).get("raw")
                result["peg"] = (ks.get("pegRatio") or {}).get("raw")
                roe_raw = (fd.get("returnOnEquity") or {}).get("raw")
                result["roe"] = round(roe_raw * 100, 1) if roe_raw is not None else None
                de_raw = (fd.get("debtToEquity") or {}).get("raw")
                result["de"] = round(de_raw, 2) if de_raw is not None else None
                pm_raw = (fd.get("profitMargins") or {}).get("raw")
                result["pm"] = round(pm_raw * 100, 1) if pm_raw is not None else None
                ish = res[0].get("incomeStatementHistory", {}).get("incomeStatementHistory", [])
                if ish:
                    ni = (ish[0].get("netIncome") or {}).get("raw")
                    if ni is not None:
                        result["pat_cr"] = round(ni / 1e7, 1)
                result = score_fundamentals(result)
                _print_fundamentals(symbol_name, result)
                return result
    except Exception as e:
        print(f"⚠ [yahoo fallback] {symbol_name} error: {e}")
    result["error"] = "Both yfinance and Yahoo fallback returned no data"
    print(f"❌ Fundamentals FAILED for {symbol_name}: no data from either source")
    return result


def _print_fundamentals(symbol_name, r):
    pe  = f"{r.get('pe'):.1f}"  if r.get('pe')  is not None else 'N/A'
    peg = f"{r.get('peg'):.2f}" if r.get('peg') is not None else 'N/A'
    pb  = f"{r.get('pb'):.1f}"  if r.get('pb')  is not None else 'N/A'
    roe = f"{r.get('roe')}%"    if r.get('roe') is not None else 'N/A'
    de  = f"{r.get('de')}"      if r.get('de')  is not None else 'N/A'
    pm  = f"{r.get('pm')}%"     if r.get('pm')  is not None else 'N/A'
    pat = f"₹{r.get('pat_cr')}Cr" if r.get('pat_cr') is not None else 'N/A'
    rev = f"₹{r.get('rev_cr')}Cr" if r.get('rev_cr') is not None else 'N/A'
    print(f"📊 {symbol_name} | MCap:{r.get('mcap','N/A')} | PE:{pe} | PEG:{peg} | PB:{pb} | ROE:{roe} | D/E:{de} | Margin:{pm} | PAT:{pat} | Rev:{rev} | {r.get('signal','N/A')}({r.get('score','N/A')})")

def score_fundamentals(r):
    breakdown = []
    total     = 0
    def add(metric, value, score, verdict, display):
        nonlocal total
        total += score
        breakdown.append({"metric": metric, "display": display, "verdict": verdict, "score": score})
    pe = r.get("pe")
    if pe is not None and pe > 0:
        if   pe < 15:  add("PE", pe, +1, "good",    f"{pe:.1f}x  ✅ Undervalued")
        elif pe < 30:  add("PE", pe,  0, "neutral",  f"{pe:.1f}x  ➖ Fair")
        elif pe < 50:  add("PE", pe, -1, "weak",     f"{pe:.1f}x  ⚠ Expensive")
        else:          add("PE", pe, -1, "weak",     f"{pe:.1f}x  ❌ Very Expensive")
    else:
        add("PE", pe, 0, "neutral", "N/A  ➖")

    peg = r.get("peg")
    if (peg is None or peg <= 0):
        pe_val          = r.get("pe")
        earnings_growth = r.get("earnings_growth")
        eps_growth      = None
        if earnings_growth and earnings_growth != 0:
            eps_growth = round(earnings_growth * 100, 1)
        if pe_val and pe_val > 0 and eps_growth and eps_growth != 0:
            peg = round(pe_val / eps_growth, 2)
            r["peg"] = peg
            r["peg_calculated"] = True

    if peg is not None and peg != 0:
        calc_tag = " ~est" if r.get("peg_calculated") else ""
        if peg < 0:
            add("PEG", peg, -1, "weak",    f"{peg:.2f}{calc_tag}  ❌ Negative Growth")
        elif peg < 1.0: add("PEG", peg, +1, "good",   f"{peg:.2f}{calc_tag}  ✅ Undervalued")
        elif peg < 1.5: add("PEG", peg, +1, "good",   f"{peg:.2f}{calc_tag}  ✅ Fair Growth")
        elif peg < 2.0: add("PEG", peg,  0, "neutral", f"{peg:.2f}{calc_tag}  ➖ Moderate")
        elif peg < 3.0: add("PEG", peg, -1, "weak",   f"{peg:.2f}{calc_tag}  ⚠ Expensive")
        else:           add("PEG", peg, -1, "weak",   f"{peg:.2f}{calc_tag}  ❌ Very Expensive")
    else:
        add("PEG", peg,  0, "neutral", "N/A  ➖")

    pb = r.get("pb")
    if pb is not None and pb > 0:
        if   pb < 1:  add("PB", pb, +1, "good",    f"{pb:.1f}x  ✅ Below Book")
        elif pb < 3:  add("PB", pb, +1, "good",    f"{pb:.1f}x  ✅ Reasonable")
        elif pb < 6:  add("PB", pb,  0, "neutral",  f"{pb:.1f}x  ➖ Moderate")
        else:         add("PB", pb, -1, "weak",     f"{pb:.1f}x  ❌ Expensive")
    else:
        add("PB", pb, 0, "neutral", "N/A  ➖")
    roe = r.get("roe")
    if roe is not None:
        if   roe >= 20: add("ROE", roe, +1, "good",    f"{roe}%  ✅ Excellent")
        elif roe >= 15: add("ROE", roe, +1, "good",    f"{roe}%  ✅ Good")
        elif roe >= 10: add("ROE", roe,  0, "neutral",  f"{roe}%  ➖ Average")
        elif roe >= 0:  add("ROE", roe, -1, "weak",     f"{roe}%  ⚠ Weak")
        else:           add("ROE", roe, -1, "weak",     f"{roe}%  ❌ Negative")
    else:
        add("ROE", roe, 0, "neutral", "N/A  ➖")
    de = r.get("de")
    if de is not None:
        if   de < 0.5: add("D/E", de, +1, "good",    f"{de:.2f}x  ✅ Low Debt")
        elif de < 1.0: add("D/E", de, +1, "good",    f"{de:.2f}x  ✅ Manageable")
        elif de < 2.0: add("D/E", de,  0, "neutral",  f"{de:.2f}x  ➖ Moderate")
        else:          add("D/E", de, -1, "weak",     f"{de:.2f}x  ❌ High Debt")
    else:
        add("D/E", de, 0, "neutral", "N/A  ➖")
    pm = r.get("pm")
    if pm is not None:
        if   pm >= 20: add("Margin", pm, +1, "good",    f"{pm}%  ✅ Excellent")
        elif pm >= 10: add("Margin", pm, +1, "good",    f"{pm}%  ✅ Good")
        elif pm >= 5:  add("Margin", pm,  0, "neutral",  f"{pm}%  ➖ Average")
        elif pm >= 0:  add("Margin", pm, -1, "weak",     f"{pm}%  ⚠ Thin")
        else:          add("Margin", pm, -1, "weak",     f"{pm}%  ❌ Losing Money")
    else:
        add("Margin", pm, 0, "neutral", "N/A  ➖")
    pat      = r.get("pat_cr")
    pat_prev = r.get("pat_prev_cr")
    if pat is not None and pat_prev is not None and pat_prev != 0:
        pat_growth = round(((pat - pat_prev) / abs(pat_prev)) * 100, 1)
        r["pat_growth"] = pat_growth
        if   pat_growth >= 20:  add("PAT Growth", pat_growth, +1, "good",    f"{pat_growth}%  ✅ Strong YoY")
        elif pat_growth >= 0:   add("PAT Growth", pat_growth, +1, "good",    f"{pat_growth}%  ✅ Positive YoY")
        elif pat_growth >= -10: add("PAT Growth", pat_growth,  0, "neutral",  f"{pat_growth}%  ➖ Slight Decline")
        else:                   add("PAT Growth", pat_growth, -1, "weak",     f"{pat_growth}%  ❌ Declining")
    else:
        pat_display = f"₹{pat}Cr" if pat is not None else "N/A"
        add("PAT (Qtr)", pat, 0, "neutral", f"{pat_display}  ➖ No YoY data")
    rev      = r.get("rev_cr")
    rev_prev = r.get("rev_prev_cr")
    if rev is not None and rev_prev is not None and rev_prev != 0:
        rev_growth = round(((rev - rev_prev) / abs(rev_prev)) * 100, 1)
        r["rev_growth"] = rev_growth
        if   rev_growth >= 15:  add("Rev Growth", rev_growth, +1, "good",    f"{rev_growth}%  ✅ Strong YoY")
        elif rev_growth >= 5:   add("Rev Growth", rev_growth, +1, "good",    f"{rev_growth}%  ✅ Growing")
        elif rev_growth >= 0:   add("Rev Growth", rev_growth,  0, "neutral",  f"{rev_growth}%  ➖ Flat")
        elif rev_growth >= -10: add("Rev Growth", rev_growth,  0, "neutral",  f"{rev_growth}%  ➖ Slight Decline")
        else:                   add("Rev Growth", rev_growth, -1, "weak",     f"{rev_growth}%  ❌ Shrinking")
    else:
        rev_display = f"₹{rev}Cr" if rev is not None else "N/A"
        add("Revenue", rev, 0, "neutral", f"{rev_display}  ➖ No YoY data")

    eps_signal = r.get("eps_trend_signal")
    eps_score  = r.get("eps_trend_score", 0)
    if eps_signal and eps_signal != "Insufficient Data":
        verdict = "good" if eps_score > 0 else "weak" if eps_score < 0 else "neutral"
        note = r.get("eps_trend_note", "")
        icon = "✅" if eps_score > 0 else "❌" if eps_score < 0 else "➖"
        add("EPS Trend", eps_score, eps_score, verdict, f"{icon} {eps_signal}" + (f" ({note})" if note else ""))

    if   total >= 5:  signal = "Strong"
    elif total >= 2:  signal = "Moderate"
    elif total >= -1: signal = "Weak"
    else:             signal = "Avoid"
    r["signal"] = signal; r["breakdown"] = breakdown; r["score"] = total
    return r


def refresh_all_fundamentals_background():
    with fundamentals_refresh_lock:
        fundamentals_refresh_status["running"] = True
        fundamentals_refresh_status["done"]    = 0
        fundamentals_refresh_status["total"]   = len(STOCKS)
        fundamentals_refresh_status["current"] = ""
    print(f"\n{'='*60}\n🔄 Fundamentals Refresh (All) started — {len(STOCKS)} stocks\n{'='*60}")
    try:
        for name in STOCKS:
            with fundamentals_refresh_lock:
                fundamentals_refresh_status["current"] = name
            try:
                data = fetch_fundamentals(name)
                with fundamentals_cache_lock:
                    fundamentals_cache[name] = data
                save_fundamentals_to_file()
            except Exception as e:
                print(f"⚠ Fundamentals failed {name}: {e}")
            with fundamentals_refresh_lock:
                fundamentals_refresh_status["done"] += 1
            time.sleep(1.2)
        print(f"\n{'='*60}\n✅ Fundamentals refresh (all) complete for {len(STOCKS)} stocks\n{'='*60}\n")
    finally:
        with fundamentals_refresh_lock:
            fundamentals_refresh_status["running"] = False
            fundamentals_refresh_status["current"] = ""

# ── indicators ───────────────────────────────────────────────
def calc_atr(df, n=14):
    df = df.copy()
    df['H-L']  = abs(df['High'] - df['Low'])
    df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
    df['L-PC'] = abs(df['Low']  - df['Close'].shift(1))
    df['TR']   = df[['H-L','H-PC','L-PC']].max(axis=1)
    df['ATR']  = df['TR'].rolling(window=n).mean()
    return df

def calc_supertrend(df, n=14, m=2):
    df  = calc_atr(df, n)
    hl2 = (df['High'] + df['Low']) / 2
    df['UpperBand'] = hl2 + (m * df['ATR'])
    df['LowerBand'] = hl2 - (m * df['ATR'])
    df['ST']     = np.nan
    df['ST_dir'] = 1
    for i in range(1, len(df)):
        ps  = df['ST'].iloc[i-1]
        cu  = df['UpperBand'].iloc[i]
        cl  = df['LowerBand'].iloc[i]
        cc  = df['Close'].iloc[i]
        pd_ = df['ST_dir'].iloc[i-1]
        if np.isnan(ps):
            df.loc[df.index[i], 'ST']     = cl
            df.loc[df.index[i], 'ST_dir'] = 1
        else:
            if pd_ == 1:
                if cc < ps:
                    df.loc[df.index[i], 'ST']     = cu
                    df.loc[df.index[i], 'ST_dir'] = -1
                else:
                    df.loc[df.index[i], 'ST']     = max(cl, ps)
                    df.loc[df.index[i], 'ST_dir'] = 1
            else:
                if cc > ps:
                    df.loc[df.index[i], 'ST']     = cl
                    df.loc[df.index[i], 'ST_dir'] = 1
                else:
                    df.loc[df.index[i], 'ST']     = min(cu, ps)
                    df.loc[df.index[i], 'ST_dir'] = -1
    return df

def calc_pivots_5m(df):
    today      = df['Datetime'].dt.date.max()
    prev_dates = sorted([d for d in df['Datetime'].dt.date.unique() if d < today])
    if not prev_dates:
        return None
    prev = prev_dates[-1]
    pdf  = df[df['Datetime'].dt.date == prev]
    if pdf.empty:
        return None
    H = pdf['High'].max(); L = pdf['Low'].min(); C = pdf['Close'].iloc[-1]
    PP = (H + L + C) / 3; diff = H - L
    return {'PP': PP, 'R1': PP+0.382*diff, 'R2': PP+0.618*diff, 'R3': PP+1.0*diff,
            'S1': PP-0.382*diff, 'S2': PP-0.618*diff, 'S3': PP-1.0*diff}

def calc_pivots_1d(df):
    if len(df) < 2:
        return None
    df2 = df.copy()
    df2['Month'] = df2['Datetime'].dt.month
    df2['Year']  = df2['Datetime'].dt.year
    current_month = df2.iloc[-1]['Month']
    current_year  = df2.iloc[-1]['Year']
    prev = df2[(df2['Year'] < current_year) |
               ((df2['Year'] == current_year) & (df2['Month'] < current_month))]
    if prev.empty:
        return None
    last_month = prev[(prev['Year'] == prev['Year'].iloc[-1]) &
                      (prev['Month'] == prev['Month'].iloc[-1])]
    H = last_month['High'].max(); L = last_month['Low'].min(); C = last_month['Close'].iloc[-1]
    PP = (H + L + C) / 3; diff = H - L
    return {'PP': PP, 'R1': PP+0.382*diff, 'R2': PP+0.618*diff, 'R3': PP+1.0*diff,
            'S1': PP-0.382*diff, 'S2': PP-0.618*diff, 'S3': PP-1.0*diff}

def fetch_prev_year_ohlc(name):
    """Previous calendar year daily OHLC (used for weekly pivot calc). yfinance-based."""
    current_year = datetime.now().year
    prev_year    = current_year - 1
    try:
        t = yf.Ticker(yf_symbol(name))
        df = _yf_call(lambda: t.history(start=f"{prev_year}-01-01", end=f"{prev_year}-12-31", interval="1d", auto_adjust=False), label=f"{name} prev_year_ohlc")
    except Exception as e:
        print(f"⚠ [fetch_prev_year_ohlc] {name} error: {e}")
        return None
    if df is None or df.empty:
        return None
    return float(df["High"].max()), float(df["Low"].min()), float(df["Close"].iloc[-1])

def calc_pivots_1w(name):
    result = fetch_prev_year_ohlc(name)
    if result is None:
        return None
    H, L, C = result
    PP = (H + L + C) / 3; diff = H - L
    return {'PP': PP, 'R1': PP+0.382*diff, 'R2': PP+0.618*diff, 'R3': PP+1.0*diff,
            'S1': PP-0.382*diff, 'S2': PP-0.618*diff, 'S3': PP-1.0*diff}

def filter_trading_hours(df):
    start = pd.Timestamp("09:15").time()
    end   = pd.Timestamp("15:30").time()
    mask  = (df['Datetime'].dt.time >= start) & (df['Datetime'].dt.time <= end)
    return df[mask].reset_index(drop=True)

def filter_trading_hours_manual(df, end_date, end_time):
    start = pd.Timestamp("09:15").time()
    end   = pd.Timestamp("15:30").time()
    mask  = (df['Datetime'].dt.time >= start) & (df['Datetime'].dt.time <= end)
    df    = df[mask]
    cap_mask = ~((df['Datetime'].dt.date == end_date) & (df['Datetime'].dt.time > end_time))
    df = df[cap_mask]
    return df.reset_index(drop=True)

# ── yfinance candle fetchers (replaces all Fyers history calls) ──
def _yf_normalize(df):
    """yfinance history() -> flat df with Datetime column, tz-naive IST, sorted+deduped."""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else ("Date" if "Date" in df.columns else df.columns[0])
    df = df.rename(columns={dt_col: "Datetime"})
    if df["Datetime"].dt.tz is not None:
        df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    keep = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
    for c in keep:
        if c not in df.columns:
            df[c] = np.nan
    df = df[keep]
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
    return df

def fetch_candles_5m(name):
    if FIVEM_DATE_MODE == "2":
        start_date, end_date = FIVEM_START_DATE, FIVEM_END_DATE
    else:
        start_date, end_date = get_last_n_trading_days(3)
    try:
        t = yf.Ticker(yf_symbol(name))
        df = _yf_call(lambda: t.history(start=start_date.strftime("%Y-%m-%d"),
                        end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                        interval="5m", auto_adjust=False), label=f"{name} 5m")
    except Exception as e:
        print(f"⚠ [5m] {name} fetch error: {e}")
        return pd.DataFrame()
    df = _yf_normalize(df)
    if df.empty:
        return df
    if FIVEM_DATE_MODE == "2":
        df = filter_trading_hours_manual(df, FIVEM_END_DATE, FIVEM_END_TIME)
    else:
        df = filter_trading_hours(df)
    return df

def fetch_candles_1d(name):
    """1 Day timeframe → 1 full year of daily candles."""
    try:
        t = yf.Ticker(yf_symbol(name))
        df = _yf_call(lambda: t.history(period="1y", interval="1d", auto_adjust=False), label=f"{name} 1d")
    except Exception as e:
        print(f"⚠ [1d] {name} fetch error: {e}")
        return pd.DataFrame()
    return _yf_normalize(df)

def fetch_candles_1w(name):
    """1 Week timeframe → 3 years of weekly candles."""
    try:
        t = yf.Ticker(yf_symbol(name))
        df = _yf_call(lambda: t.history(period="3y", interval="1wk", auto_adjust=False), label=f"{name} 1w")
    except Exception as e:
        print(f"⚠ [1w] {name} fetch error: {e}")
        return pd.DataFrame()
    return _yf_normalize(df)

def compute_volume_spike_indices(df, labels):
    if df.empty:
        return set(), []
    spike_indices = set()
    dates = df['Datetime'].dt.date
    unique_dates = sorted(dates.unique())
    if not unique_dates:
        return set(), []
    today = unique_dates[-1]
    prev_dates = unique_dates[:-1]
    for d in prev_dates:
        day_df = df[dates == d]
        if day_df.empty: continue
        spike_indices.add(int(df.index.get_loc(day_df['Volume'].idxmax())))
    today_df = df[dates == today]
    if not today_df.empty:
        spike_indices.add(int(df.index.get_loc(today_df['Volume'].idxmax())))
    return spike_indices, [i in spike_indices for i in range(len(df))]

def get_day_volumes(df):
    if df.empty: return {}
    return {str(d): int(g['Volume'].sum()) for d, g in df.groupby(df['Datetime'].dt.date)}

def detect_small_candles(candles, labels):
    n = len(candles)
    flags = [False] * n
    if n < 3: return flags
    from collections import OrderedDict
    day_map = OrderedDict()
    for idx, lbl in enumerate(labels):
        day_map.setdefault(lbl[:6], []).append(idx)
    all_day_keys = list(day_map.keys())
    for day_key, day_indices in day_map.items():
        is_last_day   = (day_key == all_day_keys[-1])
        day_qualified = []
        nd = len(day_indices)
        for pos in range(nd):
            idx = day_indices[pos]
            if pos == 0: continue
            if is_last_day and pos == nd - 1: continue
            c = candles[idx]
            total_range = c['h'] - c['l']
            if total_range <= 0.0: continue
            pct_range  = (total_range / c['l']) * 100
            upper_wick = c['h'] - max(c['o'], c['c'])
            lower_wick = min(c['o'], c['c']) - c['l']
            has_upper  = upper_wick > 0
            has_lower  = lower_wick > 0
            if has_upper != has_lower: continue
            if not has_upper and not has_lower:
                if pct_range > 0.10: continue
            else:
                if pct_range > 0.21: continue
            c_prev = candles[day_indices[pos - 1]]
            if c_prev['c'] >= c_prev['o']: continue
            if pos + 1 >= nd: continue
            if is_last_day and pos + 1 == nd - 1: continue
            c_next = candles[day_indices[pos + 1]]
            if c_next['c'] <= c_next['o']: continue
            session_high  = max(candles[i]['h'] for i in day_indices[:pos + 1])
            rejection_pct = ((session_high - c['c']) / session_high) * 100
            if rejection_pct < 0.50: continue
            day_qualified.append(idx)
        for keep_idx in day_qualified[-4:]:
            flags[keep_idx] = True
    return flags

def build_condition_met_flag_1d(name, df_labels):
    dates_for_sym = condition_met_map.get(name, [])
    dates_set     = set(dates_for_sym)
    flags = []
    for lbl in df_labels:
        try:
            date_str = datetime.strptime(lbl, "%d %b %y").strftime("%Y-%m-%d")
        except Exception:
            date_str = ""
        flags.append(date_str in dates_set)
    return flags, sorted(dates_for_sym, reverse=True)

def save_chart_json(name, tf, result):
    _dir_map = {'5m': CHART_DIR_5M, '1d': CHART_DIR_1D, '1w': CHART_DIR_1W}
    json_path = os.path.join(_dir_map[tf], f"{name}.json")
    try:
        with open(json_path, "w") as f:
            json.dump(result, f)
    except Exception as e:
        print(f"⚠ JSON save failed {name} {tf}: {e}")

def _get_end_date_for_fetch():
    if CHART_MODE == "2":
        if 'SAVED_END_DATE' in globals() and SAVED_END_DATE is not None:
            return SAVED_END_DATE
        today = datetime.now(IST).date()
        d = today
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    else:
        if FIVEM_DATE_MODE == "2":
            return FIVEM_END_DATE
        else:
            _, end = get_last_n_trading_days(3)
            return end

def build_chart_data(name, tf, force_fetch=False):
    """Fetch + compute everything needed for one stock/timeframe chart via yfinance.
    Used both for initial preload AND for manual (single/all) refresh — yfinance has
    no concept of a stale local API session like Fyers did, so one function suffices.

    In Old Charts mode (CHART_MODE == "2"), the automatic preload path is blocked
    from hitting the network (it should only load previously-saved JSON files).
    However, when the person explicitly clicks a manual ↻ refresh button, we DO
    want to go fetch live data even in Old Charts mode — that's what force_fetch=True
    is for. All manual-refresh call sites (regenerate_tf, /api/refresh_single_tf)
    pass force_fetch=True so they always hit yfinance regardless of chart mode."""
    if CHART_MODE == "2" and not force_fetch:
        print(f"⛔ build_chart_data() blocked in Old Charts mode for {name} {tf} (preload/auto path — use ↻ to force a live fetch)")
        return {"error": "Old Charts mode — use saved files only"}

    if tf == "5m":
        df        = fetch_candles_5m(name)
        label_fmt = "%d %b %H:%M"
    elif tf == "1d":
        df        = fetch_candles_1d(name)
        label_fmt = "%d %b %y"
    else:
        df        = fetch_candles_1w(name)
        label_fmt = "%d %b %y"
    if df.empty:
        return {"error": "No data"}

    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA10'] = df['Low'].ewm(span=10, adjust=False).mean()
    df = calc_supertrend(df)
    if tf == "5m":
        pivots = calc_pivots_5m(df)
    elif tf == "1d":
        pivots = calc_pivots_1d(df)
    else:
        pivots = calc_pivots_1w(name)
    df = df.reset_index(drop=True)

    candles, ema20, ema10, st_green, st_red, volume, xlabels, times = [], [], [], [], [], [], [], []
    last_st_dir = int(df['ST_dir'].iloc[-1]) if not df.empty else 0

    if tf == "5m":
        today    = df['Datetime'].dt.date.max()
        today_df = df[df['Datetime'].dt.date == today]
        today_open = float(today_df['Open'].iloc[0]) if not today_df.empty else 0
        today_low  = float(today_df['Low'].min())    if not today_df.empty else 0
    else:
        today_open = 0; today_low = 0

    for _, row in df.iterrows():
        dt = row['Datetime']
        xlabels.append(dt.strftime(label_fmt))
        if tf == "5m":
            # intraday: real Unix timestamp (IST-localized) so intraday gaps show correctly
            times.append(int(dt.replace(tzinfo=IST).timestamp()))
        else:
            # daily/weekly: business-day string so weekends collapse (no chart gaps)
            times.append(dt.strftime("%Y-%m-%d"))
        candles.append({"o": round(row['Open'],2), "h": round(row['High'],2),
                        "l": round(row['Low'],2),  "c": round(row['Close'],2)})
        volume.append({"y": int(row['Volume']), "up": row['Close'] >= row['Open']})
        ema20.append(round(row['EMA20'],2) if not np.isnan(row['EMA20']) else None)
        ema10.append(round(row['EMA10'],2) if not np.isnan(row['EMA10']) else None)
        st_val = row['ST'] if not np.isnan(row['ST']) else None
        if row['ST_dir'] == 1:
            st_green.append(round(st_val,2) if st_val else None); st_red.append(None)
        else:
            st_red.append(round(st_val,2) if st_val else None); st_green.append(None)

    vol_spike_flag = []
    day_volumes    = {}
    if tf == "5m":
        _, vol_spike_flag = compute_volume_spike_indices(df, xlabels)
        day_volumes = get_day_volumes(df)

    small_candle_flag       = []
    small_candle_recent_idx = -1
    if tf == "5m":
        small_candle_flag = detect_small_candles(candles, xlabels)
        for si in range(len(small_candle_flag)-1, -1, -1):
            if small_candle_flag[si]: small_candle_recent_idx = si; break

    condition_met_flag_1d = []; condition_met_dates = []
    if tf == "1d":
        condition_met_flag_1d, condition_met_dates = build_condition_met_flag_1d(name, xlabels)

    if force_fetch and tf == "5m" and not df.empty:
        # Manual (forced) refresh: always compute the change against the most
        # recent live trading session present in the freshly fetched data —
        # not the frozen Old-Charts-mode SAVED_END_DATE. This is what makes
        # the ↻ button show the latest live % change instead of a stale
        # historical snapshot when you're in Old Charts mode.
        end_date_ref = df['Datetime'].dt.date.max()
    else:
        end_date_ref = _get_end_date_for_fetch()
    end_date_pchange = None; end_date_ltp = None
    end_date_open = None; end_date_high = None; end_date_low = None
    if tf == "5m" and not df.empty:
        end_df = df[df['Datetime'].dt.date == end_date_ref]
        if not end_df.empty:
            end_date_open  = float(end_df['Open'].iloc[0])
            end_date_high  = float(end_df['High'].max())
            end_date_low   = float(end_df['Low'].min())
            end_date_ltp   = float(end_df['Close'].iloc[-1])
            if end_date_open:
                end_date_pchange = round(((end_date_ltp - end_date_open) / end_date_open) * 100, 2)

    result = {
        "candles": candles, "times": times, "ema20": ema20, "ema10": ema10,
        "st_green": st_green, "st_red": st_red,
        "volume": volume, "labels": xlabels,
        "pivots": pivots, "symbol": name,
        "chart_updated": datetime.now(IST).strftime("%H:%M:%S"),
        "last_st_dir": last_st_dir,
        "today_open": round(today_open, 2), "today_low": round(today_low, 2),
        "tf": tf, "vol_spike_flag": vol_spike_flag, "day_volumes": day_volumes,
        "small_candle_flag": small_candle_flag,
        "small_candle_recent_idx": small_candle_recent_idx,
        "condition_met_flag_1d": condition_met_flag_1d,
        "condition_met_dates": condition_met_dates,
        "end_date_ref": str(end_date_ref),
        "end_date_ltp": end_date_ltp,
        "end_date_open": end_date_open,
        "end_date_high": end_date_high,
        "end_date_low": end_date_low,
        "end_date_pchange": end_date_pchange,
    }
    try:
        save_chart_json(name, tf, result)
    except Exception as _e:
        print(f"⚠ JSON save failed {name} {tf}: {_e}")
    return result

# ── live quotes ───────────────────────────────────────────────
def _quote_from_5m_df(df, name, end_date=None):
    if df.empty:
        return None
    if end_date is not None:
        current_date = end_date
    else:
        current_date = df["Datetime"].dt.date.max()
    current_df = df[df["Datetime"].dt.date == current_date].reset_index(drop=True)
    if current_df.empty:
        return None
    prev_dates = sorted([d for d in df["Datetime"].dt.date.unique() if d < current_date])
    ltp      = float(current_df["Close"].iloc[-1])
    day_open = float(current_df["Open"].iloc[0])
    day_high = float(current_df["High"].max())
    day_low  = float(current_df["Low"].min())
    if prev_dates:
        prev_df    = df[df["Datetime"].dt.date == prev_dates[-1]].reset_index(drop=True)
        prev_close = float(prev_df["Close"].iloc[-1])
    else:
        prev_close = day_open
    change  = ltp - day_open
    pchange = round((change / day_open) * 100, 2) if day_open else 0
    volume  = int(current_df["Volume"].sum())
    return {
        "name": name, "ltp": round(ltp,2), "open": round(day_open,2),
        "high": round(day_high,2), "low": round(day_low,2),
        "prev_close": round(prev_close,2),
        "change": round(change,2), "pchange": pchange, "volume": volume,
    }

def fetch_quotes():
    global live_data, last_updated

    if CHART_MODE == "2":
        print("ℹ️  Old Charts mode — fetching end-date quotes from yfinance (one-time).")
        _fetch_end_date_quotes()
        last_updated = f"End date: {_get_end_date_for_fetch()}"
        while True:
            time.sleep(60)

    while True:
        try:
            for name in STOCKS:
                try:
                    df = fetch_candles_5m(name)
                    if df.empty:
                        continue
                    current_date = FIVEM_END_DATE if FIVEM_DATE_MODE == "2" else None
                    q = _quote_from_5m_df(df, name, end_date=current_date)
                    if q:
                        live_data[name] = q
                except Exception as e:
                    print(f"{name} quote error: {e}")
            last_updated = f"Last 3 Trading Days  {datetime.now(IST).strftime('%d %b %H:%M')}"
        except Exception as e:
            print(f"fetch_quotes error: {e}")
        time.sleep(5)

def _fetch_end_date_quotes():
    global live_data, last_updated
    end_date = _get_end_date_for_fetch()
    print(f"📅 Fetching end-date quotes for {end_date}…")
    for name in STOCKS:
        try:
            t = yf.Ticker(yf_symbol(name))
            df = _yf_call(lambda: t.history(start=(end_date - timedelta(days=6)).strftime("%Y-%m-%d"),
                            end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                            interval="5m", auto_adjust=False), label=f"{name} end_date_quote")
            df = _yf_normalize(df)
            if df.empty:
                print(f"  ⚠ {name}: no data from yfinance for end date quote")
                continue
            df = filter_trading_hours(df)
            q = _quote_from_5m_df(df, name, end_date=end_date)
            if not q:
                print(f"  ⚠ {name}: no candles found for end date {end_date}")
                continue
            live_data[name] = q
            print(f"  ✅ {name}: LTP={q['ltp']} O={q['open']} %chg={q['pchange']}%")
            with chart_cache_lock:
                cached_5m = chart_cache["5m"].get(name)
                if cached_5m and "error" not in cached_5m:
                    cached_5m["end_date_ltp"]     = q["ltp"]
                    cached_5m["end_date_open"]    = q["open"]
                    cached_5m["end_date_high"]    = q["high"]
                    cached_5m["end_date_low"]     = q["low"]
                    cached_5m["end_date_pchange"] = q["pchange"]
        except Exception as e:
            print(f"  ❌ {name} end-date quote error: {e}")
    last_updated = f"End Date: {end_date}"
    print(f"✅ End-date quotes loaded for {len(live_data)} stocks")

# ── preloading ────────────────────────────────────────────────
TF_LABEL    = {"5m": "5min", "1d": "1D", "1w": "1W"}
RETRY_WAIT  = 8
MAX_RETRIES = 2

def fetch_with_retry(name, tf, label):
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            data = build_chart_data(name, tf)
        except Exception as e:
            data = {"error": str(e)}
        if "error" not in data:
            return name, data
        if attempt <= MAX_RETRIES:
            print(f"{name} {label} ERROR: {data['error']} — retrying in {RETRY_WAIT}s "
                  f"(attempt {attempt}/{MAX_RETRIES+1})")
            time.sleep(RETRY_WAIT)
        else:
            print(f"{name} {label} ERROR: gave up after {MAX_RETRIES+1} attempts")
    return name, data

def _build_drop_details_log():
    from collections import OrderedDict
    lines = [f"# DropDetails log — {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}\n"]
    no_upwick_by_day = {0: [], 1: [], 2: [], 3: []}
    skipped = []
    with chart_cache_lock:
        cache_5m = dict(chart_cache["5m"])
    for name in STOCKS:
        data = cache_5m.get(name)
        if not data or "error" in data:
            skipped.append(name); continue
        labels  = data.get("labels", [])
        candles = data.get("candles", [])
        if not labels or not candles or len(labels) != len(candles):
            skipped.append(name); continue
        day_map = OrderedDict()
        for idx, lbl in enumerate(labels):
            date_key = lbl[:6]
            if date_key not in day_map:
                day_map[date_key] = idx
        day_keys = list(day_map.keys())
        for offset in range(4):
            target_idx = -(offset + 1)
            if abs(target_idx) > len(day_keys): continue
            candle = candles[day_map[day_keys[target_idx]]]
            if candle["o"] == candle["h"]:
                no_upwick_by_day[offset].append(name)
    for offset, dlabel in enumerate(["N1 (Today)", "N2 (Prev Day)", "N3 (2 Days Back)", "N4 (3 Days Back)"]):
        names = no_upwick_by_day[offset]
        lines.append(f"\n[{dlabel}] No UpWick Stocks ({len(names)}):\n")
        lines.append((", ".join(names) + "\n") if names else "(none)\n")
    if skipped:
        lines.append(f"\n[Skipped / No Data] ({len(skipped)}):\n")
        lines.append(", ".join(skipped) + "\n")
    try:
        with open(DROP_DETAILS_LOG, "w") as _f:
            _f.writelines(lines)
        print(f"📄 Dropdetails.txt written → {DROP_DETAILS_LOG}")
    except Exception as e:
        print(f"⚠ Could not write Dropdetails.txt: {e}")

def preload_tf(tf):
    label = TF_LABEL[tf]
    for name in STOCKS:
        name, data = fetch_with_retry(name, tf, label)
        with chart_cache_lock:
            chart_cache[tf][name] = data
        if "error" not in data:
            print(f"{name} {label} generated")
            with refresh_ts_lock:
                refresh_ts[f"{name}_{tf}"] = time.time()
    preload_done[tf] = True
    if tf == "5m":
        _build_drop_details_log()

def preload_all():
    if CHART_MODE == "2":
        preload_old_charts()
    else:
        preload_tf("5m")
        preload_tf("1d")
        preload_tf("1w")

def preload_old_charts():
    _dir_map = {"5m": CHART_DIR_5M, "1d": CHART_DIR_1D, "1w": CHART_DIR_1W}
    for tf, _dir in _dir_map.items():
        label = TF_LABEL[tf]
        for name in STOCKS:
            _json_path = os.path.join(_dir, f"{name}.json")
            if os.path.exists(_json_path):
                try:
                    with open(_json_path, "r") as _f:
                        data = json.load(_f)
                    if tf == "5m" and "candles" in data and "labels" in data:
                        sc_flag = detect_small_candles(data["candles"], data["labels"])
                        sc_recent = -1
                        for _si in range(len(sc_flag)-1, -1, -1):
                            if sc_flag[_si]: sc_recent = _si; break
                        data["small_candle_flag"]       = sc_flag
                        data["small_candle_recent_idx"] = sc_recent
                    if tf == "1d" and "labels" in data:
                        cm_flag, cm_dates = build_condition_met_flag_1d(name, data["labels"])
                        data["condition_met_flag_1d"] = cm_flag
                        data["condition_met_dates"]   = cm_dates
                    with chart_cache_lock:
                        chart_cache[tf][name] = data
                    with refresh_ts_lock:
                        refresh_ts[f"{name}_{tf}"] = time.time()
                    print(f"✅ {name} {label} — loaded from JSON")
                    continue
                except Exception as _e:
                    print(f"⚠ JSON load failed {name} {tf}: {_e} — marking as missing")
            msg = "No saved chart — click ↻ to regenerate"
            print(f"⚠  {name} {tf} — {msg}")
            with chart_cache_lock:
                chart_cache[tf][name] = {"error": msg}
        preload_done[tf] = True
    _build_drop_details_log()

def load_tf_from_files(tf):
    """Load ONE timeframe's charts for all stocks from previously saved JSON
    files in Google Drive (Old Charts mode only). This is the "Load" button
    path — it never touches the network, it only reads whatever was saved to
    disk on a previous run. Never runs automatically anymore; only fires when
    the person clicks the corresponding 'Load 5m / Load 1D / Load 1W' button
    on the page."""
    _dir_map = {"5m": CHART_DIR_5M, "1d": CHART_DIR_1D, "1w": CHART_DIR_1W}
    label = TF_LABEL[tf]
    _dir  = _dir_map[tf]
    with load_status_lock:
        load_status[tf].update(running=True, done=0, total=len(STOCKS), current="")
    print(f"🔄 Load-from-file started for {label} — {len(STOCKS)} stocks")
    try:
        for name in STOCKS:
            with load_status_lock:
                load_status[tf]["current"] = name
            _json_path = os.path.join(_dir, f"{name}.json")
            if os.path.exists(_json_path):
                try:
                    with open(_json_path, "r") as _f:
                        data = json.load(_f)
                    if tf == "5m" and "candles" in data and "labels" in data:
                        sc_flag = detect_small_candles(data["candles"], data["labels"])
                        sc_recent = -1
                        for _si in range(len(sc_flag)-1, -1, -1):
                            if sc_flag[_si]: sc_recent = _si; break
                        data["small_candle_flag"]       = sc_flag
                        data["small_candle_recent_idx"] = sc_recent
                    if tf == "1d" and "labels" in data:
                        cm_flag, cm_dates = build_condition_met_flag_1d(name, data["labels"])
                        data["condition_met_flag_1d"] = cm_flag
                        data["condition_met_dates"]   = cm_dates
                    with chart_cache_lock:
                        chart_cache[tf][name] = data
                    with refresh_ts_lock:
                        refresh_ts[f"{name}_{tf}"] = time.time()
                    print(f"✅ {name} {label} — loaded from JSON")
                except Exception as _e:
                    print(f"⚠ JSON load failed {name} {tf}: {_e} — marking as missing")
                    msg = "No saved chart — click ↻ Refresh to fetch live"
                    with chart_cache_lock:
                        chart_cache[tf][name] = {"error": msg}
            else:
                msg = "No saved chart — click ↻ Refresh to fetch live"
                print(f"⚠  {name} {tf} — {msg}")
                with chart_cache_lock:
                    chart_cache[tf][name] = {"error": msg}
            with load_status_lock:
                load_status[tf]["done"] += 1
        preload_done[tf] = True
        if tf == "5m":
            _build_drop_details_log()
        print(f"✅ Load-from-file complete for {label}")
    finally:
        with load_status_lock:
            load_status[tf]["running"] = False
            load_status[tf]["current"] = ""

def _live_data_from_chart(name, chart_data):
    """Build a fresh live_data quote dict from a freshly-fetched 5m chart_data
    result. Used by BOTH the per-row single-tf refresh AND the top-bar
    'Refresh 5m (All)' button, so every manual-refresh path in Old Charts
    mode keeps the LTP / % change / Volume strip in sync with the latest
    live candle instead of the stale SAVED_END_DATE snapshot taken at
    startup."""
    if chart_data.get("end_date_ltp") is None:
        return None
    ltp  = chart_data["end_date_ltp"]
    open_ = chart_data.get("end_date_open", 0) or 0
    return {
        "name": name,
        "ltp": ltp,
        "open": open_,
        "high": chart_data.get("end_date_high", 0),
        "low": chart_data.get("end_date_low", 0),
        "prev_close": open_,
        "change": round(ltp - open_, 2),
        "pchange": chart_data.get("end_date_pchange", 0),
        "volume": sum(v["y"] for v in chart_data.get("volume", [])),
    }

def _quote_from_last_candle(name, chart_data):
    candles = chart_data.get("candles") or []
    volume  = chart_data.get("volume")  or []
    if not candles:
        return None
    last = candles[-1]
    ltp = last["c"]
    prev_close = candles[-2]["c"] if len(candles) >= 2 else last["o"]
    change  = round(ltp - prev_close, 2)
    pchange = round((change / prev_close) * 100, 2) if prev_close else 0
    vol = volume[-1]["y"] if volume else 0
    return {
        "name": name, "ltp": round(ltp, 2),
        "open": last["o"], "high": last["h"], "low": last["l"],
        "prev_close": round(prev_close, 2),
        "change": change, "pchange": pchange, "volume": vol,
    }

def regenerate_tf(tf):
    print(f"🔄 Manual {tf} regeneration started (force_fetch=True)")
    global last_updated
    with chart_refresh_lock:
        chart_refresh_status[tf].update(running=True, done=0, total=len(STOCKS), current="")
    try:
        for name in STOCKS:
            with chart_refresh_lock:
                chart_refresh_status[tf]["current"] = name
            try:
                data = build_chart_data(name, tf, force_fetch=True)
            except Exception as e:
                data = {"error": str(e)}
            with chart_cache_lock:
                chart_cache[tf][name] = data
            if "error" not in data:
                with refresh_ts_lock:
                    refresh_ts[f"{name}_{tf}"] = time.time()
                # Push a fresh quote into the info strip for ANY tf, not just
                # 5m. 5m keeps its more precise trading-hours-based quote;
                # 1D/1W now use the last-candle-derived generic quote so
                # the strip actually moves when you click ↻ 1D / ↻ 1W.
                if tf == "5m":
                    q = _live_data_from_chart(name, data)
                else:
                    q = _quote_from_last_candle(name, data)
                if q:
                    live_data[name] = q
                print(f"✅ {name} {tf} regenerated")
            with chart_refresh_lock:
                chart_refresh_status[tf]["done"] += 1
        last_updated = f"Manual Refresh (All) [{tf}]: {datetime.now(IST).strftime('%d %b %H:%M:%S')}"
        print(f"✅ Manual {tf} regeneration completed")
    finally:
        with chart_refresh_lock:
            chart_refresh_status[tf]["running"] = False
            chart_refresh_status[tf]["current"] = ""

# ── Flask ─────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/api/chart_mode")
def api_chart_mode():
    return jsonify({"mode": CHART_MODE})

@app.route("/api/load_5m", methods=["POST"])
def api_load_5m():
    if CHART_MODE != "2":
        return jsonify({"success": False, "message": "Load is only available in Old Charts mode. Use ↻ Refresh instead."})
    threading.Thread(target=load_tf_from_files, args=("5m",), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/load_1d", methods=["POST"])
def api_load_1d():
    if CHART_MODE != "2":
        return jsonify({"success": False, "message": "Load is only available in Old Charts mode. Use ↻ Refresh instead."})
    threading.Thread(target=load_tf_from_files, args=("1d",), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/load_1w", methods=["POST"])
def api_load_1w():
    if CHART_MODE != "2":
        return jsonify({"success": False, "message": "Load is only available in Old Charts mode. Use ↻ Refresh instead."})
    threading.Thread(target=load_tf_from_files, args=("1w",), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/load_status")
def api_load_status():
    tf = request.args.get("tf", "5m")
    with load_status_lock:
        return jsonify(dict(load_status.get(tf, {})))

@app.route("/api/refresh_5m", methods=["POST"])
def refresh_5m():
    threading.Thread(target=regenerate_tf, args=("5m",), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/refresh_1d", methods=["POST"])
def refresh_1d():
    threading.Thread(target=regenerate_tf, args=("1d",), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/refresh_1w", methods=["POST"])
def refresh_1w():
    threading.Thread(target=regenerate_tf, args=("1w",), daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/refresh_single_tf", methods=["POST"])
def refresh_single_tf():
    data = request.get_json()
    name = data.get("symbol")
    tf   = data.get("tf")
    if not name or tf not in ["5m", "1d", "1w"]:
        return jsonify({"success": False})
    try:
        chart_data = build_chart_data(name, tf, force_fetch=True)
        server_ts = time.time()
        with chart_cache_lock:
            chart_cache[tf][name] = chart_data
        with refresh_ts_lock:
            refresh_ts[f"{name}_{tf}"] = server_ts
        if "error" not in chart_data:
            q = _live_data_from_chart(name, chart_data) if tf == "5m" \
                else _quote_from_last_candle(name, chart_data)
            if q:
                live_data[name] = q
        print(f"✅ {name} {tf} manually refreshed")
        return jsonify({"success": "error" not in chart_data, "ts": server_ts})
    except Exception as e:
        print(e)
        return jsonify({"success": False})

@app.route("/api/live_start", methods=["POST"])
def live_start():
    global live_running
    live_running = True
    return jsonify({"running": True})

@app.route("/api/live_stop", methods=["POST"])
def live_stop():
    global live_running
    live_running = False
    return jsonify({"running": False})

@app.route("/")
def index():
    return render_template_string(HTML, stocks=STOCKS, chart_mode=CHART_MODE)

@app.route("/api/quotes")
def api_quotes():
    payload = {}
    for name, q in live_data.items():
        payload[name] = {**q, "updated": last_updated}
    return jsonify(payload)

@app.route("/api/chart")
def api_chart():
    name = request.args.get("symbol","")
    tf   = request.args.get("tf","5m")
    with chart_cache_lock:
        data = chart_cache.get(tf, {}).get(name)
    if data is None:
        return jsonify({"pending": True})
    return jsonify(data)

@app.route("/api/cache_status")
def api_cache_status():
    with chart_cache_lock:
        ready_5m = [k for k,v in chart_cache["5m"].items() if "error" not in v]
        ready_1d = [k for k,v in chart_cache["1d"].items() if "error" not in v]
        ready_1w = [k for k,v in chart_cache["1w"].items() if "error" not in v]
    return jsonify({
        "total": len(STOCKS),
        "ready_5m": len(ready_5m), "ready_1d": len(ready_1d), "ready_1w": len(ready_1w),
        "all_names": STOCKS, "preload_done": preload_done,
    })

@app.route("/api/ready_stocks")
def api_ready_stocks():
    since = float(request.args.get("since", 0))
    result = []
    with refresh_ts_lock:
        for key, ts in refresh_ts.items():
            if ts > since:
                parts = key.rsplit("_", 1)
                if len(parts) == 2:
                    result.append({"name": parts[0], "tf": parts[1], "ts": ts})
    return jsonify({"items": result, "server_time": time.time()})

@app.route("/api/refresh_status")
def api_refresh_status():
    since = float(request.args.get("since", 0))
    with refresh_ts_lock:
        newly_ready = [name for name, ts in refresh_ts.items() if ts > since]
        max_ts = max(refresh_ts.values()) if refresh_ts else 0
    return jsonify({"max_ts": max_ts, "newly_ready": newly_ready})

@app.route("/api/condition_met_summary")
def api_condition_met_summary():
    result = {}
    with chart_cache_lock:
        cache_1d = dict(chart_cache["1d"])
    for name in STOCKS:
        data = cache_1d.get(name)
        if data and "condition_met_dates" in data and data["condition_met_dates"]:
            result[name] = data["condition_met_dates"][0]
        else:
            dates = condition_met_map.get(name, [])
            result[name] = sorted(dates, reverse=True)[0] if dates else ""
    return jsonify(result)

@app.route("/api/accepted_date_summary")
def api_accepted_date_summary():
    result = {}
    for name in STOCKS:
        entries = accepted_date_map.get(name, [])
        result[name] = {
            "latest_accepted": entries[0]["accepted"] if entries else "",
            "recent": entries[:3],
        }
    return jsonify(result)

@app.route("/api/ema_distance_summary")
def api_ema_distance_summary():
    result = {}
    for name in STOCKS:
        entries = ema_distance_map.get(name, [])
        result[name] = {
            "latest_ema_distance": entries[0]["ema_distance"] if entries else None,
            "recent": entries[:3],
        }
    return jsonify(result)

@app.route("/api/never_below_10ema_summary")
def api_never_below_10ema_summary():
    result = {}
    for name in STOCKS:
        entries = never_below_10ema_map.get(name, [])
        result[name] = {
            "latest_value": entries[0]["value"] if entries else "",
            "recent": entries[:3],
        }
    return jsonify(result)

@app.route("/api/fundamentals")
def api_fundamentals():
    name = request.args.get("symbol", "")
    if not name:
        return jsonify({"error": "no symbol"})
    with fundamentals_cache_lock:
        data = fundamentals_cache.get(name)
    if data is None:
        return jsonify({"not_fetched": True})
    return jsonify(data)

@app.route("/api/fundamentals_all")
def api_fundamentals_all():
    with fundamentals_cache_lock:
        data = dict(fundamentals_cache)
    return jsonify(data)

@app.route("/api/fundamentals_refresh_single", methods=["POST"])
def api_fundamentals_refresh_single():
    payload = request.get_json()
    name = payload.get("symbol", "") if payload else ""
    if not name:
        return jsonify({"success": False})
    print(f"\n🔄 [Fundamentals] Single refresh requested: {name}")
    data = fetch_fundamentals(name)
    with fundamentals_cache_lock:
        fundamentals_cache[name] = data
    save_fundamentals_to_file()
    return jsonify({"success": data.get("error") is None, "data": data})

@app.route("/api/fundamentals_refresh_all", methods=["POST"])
def api_fundamentals_refresh_all():
    with fundamentals_refresh_lock:
        if fundamentals_refresh_status["running"]:
            return jsonify({"success": False, "message": "Already running"})
    threading.Thread(target=refresh_all_fundamentals_background, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/fundamentals_refresh_status")
def api_fundamentals_refresh_status():
    with fundamentals_refresh_lock:
        return jsonify(dict(fundamentals_refresh_status))

@app.route("/api/refresh_chart_status")
def api_refresh_chart_status():
    tf = request.args.get("tf", "5m")
    with chart_refresh_lock:
        return jsonify(dict(chart_refresh_status.get(tf, {})))

@app.route("/api/news/<symbol>")
def api_news(symbol):
    with news_cache_lock:
        data = news_cache.get(symbol)
    if data is not None:
        return jsonify({"items": data})
    def _bg():
        items = fetch_tv_news(symbol, max_items=10)
        with news_cache_lock:
            news_cache[symbol] = items
        save_news_to_file()
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"pending": True})

@app.route("/api/news_refresh/<symbol>", methods=["POST"])
def api_news_refresh(symbol):
    def _bg():
        items = fetch_tv_news(symbol, max_items=10)
        with news_cache_lock:
            news_cache[symbol] = items
        save_news_to_file()
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/news_refresh_all", methods=["POST"])
def api_news_refresh_all():
    with news_refresh_lock:
        if news_refresh_status["running"]:
            return jsonify({"success": False, "message": "Already running"})
    threading.Thread(target=refresh_all_news_background, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/news_refresh_status")
def api_news_refresh_status():
    with news_refresh_lock:
        return jsonify(dict(news_refresh_status))


# =============================================================
#  HTML  (interactive TradingView-style charts via lightweight-charts)
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
    --info-w:200px; --row-sep:#262626;
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
  .mode-badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:10px; }
  .mode-old { background:#3b2f0f; color:#fbbf24; border:1px solid #78530f; }
  .mode-new { background:#0f3320; color:#4ade80; border:1px solid #166534; }
  .sort-wrap { display:flex; align-items:center; gap:6px; }
  .sort-wrap label { color:var(--muted); font-size:11px; white-space:nowrap; }
  #sortSelect { background:#1a1a1a; color:var(--text); border:1px solid var(--border);
    border-radius:6px; padding:4px 8px; font-size:12px; cursor:pointer; outline:none; }
  .btn { display:inline-flex; align-items:center; gap:5px; padding:5px 11px; border-radius:6px;
    font-size:12px; font-weight:500; border:1px solid transparent; cursor:pointer;
    transition:all .15s; white-space:nowrap; }
  .btn-5m  { background:#0d1f38; color:#60a5fa;  border-color:#1e3a5f; }
  .btn-1d  { background:#0d2b1c; color:#34d399; border-color:#155e3a; }
  .btn-1w  { background:#332405; color:#fbbf24;  border-color:#5c4008; }
  .btn-load { background:#1c1440; color:#c4b5fd; border-color:#3f2f6b; }
  .btn-fund { background:#082c33; color:#22d3ee; border-color:#0e5a66; }
  .btn-news { background:#3a0f24; color:#f472b6; border-color:#6b1a41; }
  .btn:hover { opacity:.8; transform:translateY(-1px); }
  .btn:active { transform:translateY(0); opacity:1; }
  .btn.loading { opacity:.5; pointer-events:none; }
  .btn-sm { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px;
    font-size:10px; font-weight:600; border:1px solid var(--border); background:#1a1a1a;
    color:var(--muted); cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn-sm:hover { border-color:var(--accent); color:var(--accent); background:#0d1f38; }
  .btn-sm.loading { opacity:.45; pointer-events:none; }
  .btn-sm.active { border-color:#f472b6; color:#f472b6; background:#3a0f24; }
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
  #loadingOverlay { position:fixed; inset:0; background:rgba(0,0,0,.92);
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    z-index:999; gap:12px; }
  #loadingOverlay.hidden { display:none; }
  .spinner { width:36px; height:36px; border:3px solid var(--border);
    border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #loadMsg  { color:var(--muted); font-size:13px; }
  #loadProg { color:var(--accent); font-size:12px; }
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
    background:var(--surface); transition:background .15s; min-width:max-content; }
  .stock-row:hover { background:#161616; }
  .info-strip { flex:0 0 var(--info-w); min-width:var(--info-w); padding:10px 10px 8px 12px;
    border-right:2px solid var(--border); display:flex; flex-direction:column; gap:3px;
    justify-content:flex-start; background:#0c0c0c; position:sticky; left:0; z-index:10; }
  .info-name { font-size:14px; font-weight:700; color:var(--accent); letter-spacing:.3px; }
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
  .cond-badge { font-size:9px; font-weight:600; padding:1px 6px; border-radius:8px;
    background:#332405; color:var(--yellow); border:1px solid #5c4008;
    display:none; width:fit-content; }
  .fund-header { display:flex; align-items:center; justify-content:space-between;
    margin-top:5px; border-top:1px solid var(--border); padding-top:4px; }
  .fund-header-label { font-size:9px; font-weight:700; color:#22d3ee; letter-spacing:.5px; text-transform:uppercase; }
  .news-section { margin-top:5px; border-top:1px solid var(--border); padding-top:4px; }
  .news-header {
    display:flex; align-items:center; justify-content:space-between;
    font-size:9px; font-weight:700; color:var(--muted); letter-spacing:.5px;
    text-transform:uppercase; margin-bottom:3px; cursor:pointer; user-select:none;
  }
  .news-header:hover { color:var(--accent); }
  .news-toggle-icon { font-size:8px; transition:transform .2s; }
  .news-toggle-icon.open { transform:rotate(180deg); }
  .news-list { display:none; flex-direction:column; gap:4px; }
  .news-list.visible { display:flex; }
  .news-item {
    display:flex; flex-direction:column; gap:1px;
    padding:3px 5px; border-radius:4px;
    background:#151515; border:1px solid var(--border);
    transition:background .12s;
  }
  .news-item:hover { background:#0d1f38; border-color:#1e3a5f; }
  .news-meta { display:flex; align-items:center; gap:4px; }
  .news-time { font-size:8.5px; color:var(--muted); white-space:nowrap; font-weight:600; }
  .news-provider { font-size:8px; color:#c4b5fd; background:#241a3d;
    border:1px solid #3f2f6b; border-radius:3px; padding:0 4px; font-weight:600; white-space:nowrap; }
  .news-headline {
    font-size:9px; color:var(--text); font-weight:500; line-height:1.35;
    text-decoration:none; display:block;
  }
  .news-headline:hover { color:var(--accent); text-decoration:underline; }
  .news-loading { font-size:9px; color:var(--muted); font-style:italic; padding:3px 0; }
  .news-refresh-btn {
    font-size:8px; color:var(--accent); cursor:pointer; border:none;
    background:none; padding:0; font-weight:600;
  }
  .news-refresh-btn:hover { text-decoration:underline; }
  .chart-cell { flex:0 0 auto; border-right:1px solid var(--border); padding:6px 6px 4px;
    display:flex; flex-direction:column; gap:0; background:var(--bg); }
  .chart-cell:last-child { border-right:none; }
  .cell-label { font-size:9.5px; font-weight:600; letter-spacing:.8px; color:var(--muted);
    text-transform:uppercase; display:flex; align-items:center; justify-content:space-between;
    margin-bottom:3px; gap:4px; }
  .cell-label-btns { display:flex; align-items:center; gap:4px; }
  .st-dir { font-size:9px; padding:1px 5px; border-radius:3px; font-weight:700; }
  .st-bull { background:#0f3320; color:var(--green); }
  .st-bear { background:#3a1414; color:var(--red); }
  .chart-wrap { border:1px solid var(--border); border-radius:4px; overflow:hidden; position:relative; }
  .chart-wrap.draw-mode { cursor:crosshair; }
  .chart-pending { display:flex; align-items:center; justify-content:center;
    color:var(--muted); font-size:11px; font-style:italic; text-align:center; padding:0 8px;
    background:#111111; border:1px dashed var(--border); border-radius:4px; }
</style>
</head>
<body>

<div id="loadingOverlay" class="hidden">
  <div class="spinner"></div>
  <div id="loadMsg">Loading charts…</div>
  <div id="loadProg"></div>
</div>

<div id="topbar">
  <span class="brand">📈 StockDash</span>
  {% if chart_mode == "2" %}
  <span class="mode-badge mode-old">🗂 Old Charts</span>
  {% else %}
  <span class="mode-badge mode-new">✨ New Charts</span>
  {% endif %}
  <div class="sep"></div>
  <div class="sort-wrap">
    <label>Sort:</label>
    <select id="sortSelect">
      <option value="default">Default (A → Z)</option>
      <option value="price_asc">Price: Low → High</option>
      <option value="price_desc">Price: High → Low</option>
      <option value="change_asc">% Change: Low → High</option>
      <option value="change_desc">% Change: High → Low</option>
      <option value="cond_date">Condition Date: Latest First</option>
               <option value="accept_date">Accepted Date: Latest First</option>
               <option value="ema_dist_asc">EMA Distance: Near → Far</option>
               <option value="never_below_10ema">Close Never Below 10 EMA: Yes → No</option>
      <option value="above_st_1d">Above Supertrend (1D)</option>
      <option value="above_st_1w">Above Supertrend (1W)</option>
      <option value="fund_score_desc">Fundamentals: Strong → Avoid</option>
    </select>
  </div>
  <div class="sep"></div>
  {% if chart_mode == "2" %}
  <button class="btn btn-load" id="btnLoad5m" onclick="loadTF('5m')">📂 Load 5m</button>
  <button class="btn btn-load" id="btnLoad1d" onclick="loadTF('1d')">📂 Load 1D</button>
  <button class="btn btn-load" id="btnLoad1w" onclick="loadTF('1w')">📂 Load 1W</button>
  <div class="sep"></div>
  {% endif %}
  <button class="btn btn-5m" id="btn5m" onclick="refreshTF('5m')">↻ Refresh 5m</button>
  <button class="btn btn-1d" id="btn1d" onclick="refreshTF('1d')">↻ Refresh 1D</button>
  <button class="btn btn-1w" id="btn1w" onclick="refreshTF('1w')">↻ Refresh 1W</button>
  <button class="btn btn-fund" id="btnFundAll" onclick="refreshAllFundamentals()">↻ Refresh Fundamentals (All)</button>
  <button class="btn btn-news" id="btnNewsAll" onclick="refreshAllNews()">↻ Refresh News (All)</button>
  <span id="statusBadge">—</span>
</div>

<div id="sizeBar">
  <div class="tf-size-group">
    <span class="tf-size-label">5m</span>
    <div class="size-ctrl"><label>Canvas Width</label>
      <button class="step-btn" onclick="stepSize('5m','width',-40)">−</button>
      <span class="size-val" id="val-5m-width">500px</span>
      <button class="step-btn" onclick="stepSize('5m','width',40)">+</button></div>
    <div class="size-ctrl"><label>Canvas Height</label>
      <button class="step-btn" onclick="stepSize('5m','h',-30)">−</button>
      <span class="size-val" id="val-5m-h">320px</span>
      <button class="step-btn" onclick="stepSize('5m','h',30)">+</button></div>
    <button class="btn-sm" onclick="applyAll('5m')">Apply to all 5m</button>
  </div>
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
  <span style="font-size:10px;color:var(--muted)">Tip: use Canvas Width/Height to resize the chart area — all candles for that timeframe will auto-fit inside it (more width = more room per candle, e.g. widen 1D since it has a year of data). You can still scroll / pinch / drag on any chart to zoom & pan further. The Weekly Line chart shares the 1W canvas size.</span>
</div>

<div id="colHeader">
  <div class="ch-info">Stock</div>
  <div class="ch-tf" style="width:520px">5 Min · 3 Days</div>
  <div class="ch-tf" style="width:520px">1 Day · 1 Year</div>
  <div class="ch-tf" style="width:520px">Weekly Line · 3 Years</div>
  <div class="ch-tf" style="width:520px">1 Week · 3 Years</div>
</div>

<div id="stockTable"></div>

<script>
const STOCKS      = {{ stocks | tojson }};
const CHART_MODE  = "{{ chart_mode }}";
const TF_LIST     = ['5m','1d','1w'];

const quotesMap    = {};
const condDates    = {};
const chartData    = {};
const fundMap      = {};
const acceptedMap  = {};   // { name: { latest_accepted, recent: [{condition_met, accepted}, ...] } }
const emaDistMap   = {};   // { name: { latest_ema_distance, recent: [{condition_met, ema_distance}, ...] } }
const neverBelow10emaMap = {};   // { name: { latest_value: "Yes"/"No"/"", recent: [{condition_met, value}, ...] } }
const chartReg   = {}; // `${name}_${tf}` -> {chart, candleSeries, volSeries, extraSeries:[]}

// sizeState now controls the actual chart CANVAS dimensions (width/height).
// Bar spacing is no longer set manually — after every render/resize we call
// chart.timeScale().fitContent() so all candles for that timeframe always
// fit and are visible inside whatever canvas size you choose. Widening the
// canvas (e.g. for 1D, which has ~1yr of daily candles) simply gives each
// candle more room instead of cutting candles off or requiring scrolling.
// The Weekly Line chart ('wline') intentionally shares the same canvas size
// as the 1W candlestick chart — resizing 1W resizes its line-chart sibling.
const sizeState = {
  '5m': { width:500, h:320 },
  '1d': { width:500, h:320 },
  '1w': { width:500, h:320 },
};
function wlineSize() { return sizeState['1w']; }

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
  for (const name of STOCKS) {
    const key = `${name}_${tf}`;
    const reg = chartReg[key];
    const container = document.getElementById(`chartdiv-${name}-${tf}`);
    if (container) {
      container.style.width  = sizeState[tf].width + 'px';
      container.style.height = sizeState[tf].h + 'px';
    }
    if (reg) {
      reg.chart.resize(sizeState[tf].width, sizeState[tf].h);
      // Re-fit so every candle stays visible inside the new canvas size.
      reg.chart.timeScale().fitContent();
    }
    // Weekly Line chart shares the 1W canvas size — keep it in sync.
    if (tf === '1w') {
      const lkey = `${name}_wline`;
      const lreg = chartReg[lkey];
      const lcontainer = document.getElementById(`chartdiv-${name}-wline`);
      if (lcontainer) {
        lcontainer.style.width  = sizeState['1w'].width + 'px';
        lcontainer.style.height = sizeState['1w'].h + 'px';
      }
      if (lreg) {
        lreg.chart.resize(sizeState['1w'].width, sizeState['1w'].h);
        lreg.chart.timeScale().fitContent();
      }
    }
  }
  setStatus(`Applied canvas size to all ${tf}${tf === '1w' ? ' + Weekly Line' : ''} charts`);
}

// ── Stock external links ──────────────────────────────────────
function makeStockLinks(name) {
  const screenerUrl = `https://www.screener.in/company/${name}/consolidated/`;
  const tvUrl       = `https://in.tradingview.com/symbols/NSE-${name}/news/`;
  return `
    <div class="stock-links">
      <a class="stock-link link-screener" href="${screenerUrl}" target="_blank" rel="noopener">📊 Screener</a>
      <a class="stock-link link-tv" href="${tvUrl}" target="_blank" rel="noopener">📈 TV News</a>
    </div>`;
}

// ── News rendering ────────────────────────────────────────────
const newsRetryMap = {};

function makeNewsSection(name) {
  return `
    <div class="news-section">
      <div class="news-header" onclick="toggleNews('${name}')" id="news-hdr-${name}">
        <span>📰 News</span>
        <div style="display:flex;align-items:center;gap:5px">
          <button class="news-refresh-btn" onclick="event.stopPropagation();refreshNews('${name}')" title="Refresh news">↻</button>
          <span class="news-toggle-icon" id="news-icon-${name}">▾</span>
        </div>
      </div>
      <div class="news-list" id="news-list-${name}">
        <div class="news-loading" id="news-loading-${name}">Not fetched yet — click ▾ to load, or ↻ to refresh</div>
      </div>
    </div>`;
}

function toggleNews(name) {
  const list = document.getElementById(`news-list-${name}`);
  const icon = document.getElementById(`news-icon-${name}`);
  if (!list) return;
  const opening = !list.classList.contains('visible');
  list.classList.toggle('visible');
  if (icon) icon.classList.toggle('open', opening);
  if (opening) {
    const loading = document.getElementById(`news-loading-${name}`);
    const hasItems = list.querySelectorAll('.news-item').length > 0;
    if (!hasItems) {
      if (loading) { loading.textContent = 'Loading news…'; loading.style.display = 'block'; }
      fetchNewsForStock(name);
    }
  }
}

async function fetchNewsForStock(name) {
  const loadEl = document.getElementById(`news-loading-${name}`);
  try {
    const r = await fetch(`/api/news/${encodeURIComponent(name)}`);
    const d = await r.json();
    if (d.pending) {
      const attempt = (newsRetryMap[name] || 0) + 1;
      newsRetryMap[name] = attempt;
      if (attempt < 20) { setTimeout(() => fetchNewsForStock(name), 3000); }
      else if (loadEl) { loadEl.textContent = 'News unavailable.'; }
      return;
    }
    renderNewsItems(name, d.items || []);
  } catch(e) {
    if (loadEl) loadEl.textContent = 'News fetch error.';
  }
}

function renderNewsItems(name, items) {
  const list    = document.getElementById(`news-list-${name}`);
  const loadEl  = document.getElementById(`news-loading-${name}`);
  if (!list) return;
  if (loadEl) loadEl.style.display = 'none';
  list.querySelectorAll('.news-item').forEach(el => el.remove());
  if (!items || items.length === 0) {
    if (loadEl) { loadEl.textContent = 'No news found.'; loadEl.style.display = 'block'; }
    return;
  }
  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'news-item';
    const provHtml = item.provider ? `<span class="news-provider">${item.provider}</span>` : '';
    const timeHtml = item.timestamp_ist ? `<span class="news-time">${item.timestamp_ist}</span>` : '';
    div.innerHTML = `
      <div class="news-meta">${timeHtml}${provHtml}</div>
      <a class="news-headline" href="${item.url}" target="_blank" rel="noopener"
         title="${item.headline.replace(/"/g,'&quot;')}">${item.headline}</a>`;
    list.appendChild(div);
  });
  newsRetryMap[name] = 0;
}

async function refreshNews(name) {
  const loadEl = document.getElementById(`news-loading-${name}`);
  const list   = document.getElementById(`news-list-${name}`);
  if (loadEl) { loadEl.textContent = 'Refreshing…'; loadEl.style.display = 'block'; }
  if (list) list.querySelectorAll('.news-item').forEach(el => el.remove());
  newsRetryMap[name] = 0;
  try {
    await fetch(`/api/news_refresh/${encodeURIComponent(name)}`, { method:'POST' });
    setTimeout(() => fetchNewsForStock(name), 2000);
  } catch(e) {
    if (loadEl) loadEl.textContent = 'Refresh failed.';
  }
}

let newsAllPolling = false;
async function refreshAllNews() {
  const btn = document.getElementById('btnNewsAll');
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/news_refresh_all', { method:'POST' });
    const d = await r.json();
    setStatus(d.success === false ? (d.message || 'News refresh already running') : 'News refresh started for all stocks…');
    if (!newsAllPolling) { newsAllPolling = true; pollNewsRefreshStatus(); }
  } catch(e) { setStatus('News refresh error'); btn.classList.remove('loading'); }
}

async function pollNewsRefreshStatus() {
  try {
    const r = await fetch('/api/news_refresh_status');
    const d = await r.json();
    if (d.running) {
      setStatus(`News refresh: ${d.done}/${d.total}${d.current ? ' — ' + d.current : ''}`);
      document.querySelectorAll('.news-list.visible').forEach(list => {
        fetchNewsForStock(list.id.replace('news-list-', ''));
      });
      setTimeout(pollNewsRefreshStatus, 2000);
    } else {
      setStatus('News refresh complete ✓');
      document.getElementById('btnNewsAll').classList.remove('loading');
      document.querySelectorAll('.news-list.visible').forEach(list => {
        fetchNewsForStock(list.id.replace('news-list-', ''));
      });
      newsAllPolling = false;
    }
  } catch(e) {
    newsAllPolling = false;
    document.getElementById('btnNewsAll').classList.remove('loading');
  }
}

// ── Fundamentals ────────────────────────────────────────────
async function fetchAllFundamentals() {
  try {
    const r = await fetch('/api/fundamentals_all');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) { if (!data.error) renderFundamentals(name, data); }
  } catch(e) {}
}
async function fetchAcceptedDates() {
  try {
    const r = await fetch('/api/accepted_date_summary');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) {
      acceptedMap[name] = data;
      renderAcceptedDates(name, data);
    }
  } catch(e) {}
}

async function fetchEmaDistances() {
  try {
    const r = await fetch('/api/ema_distance_summary');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) {
      emaDistMap[name] = data;
    }
  } catch(e) {}
}

async function fetchNeverBelow10Ema() {
  try {
    const r = await fetch('/api/never_below_10ema_summary');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) {
      neverBelow10emaMap[name] = data;
    }
  } catch(e) {}
}

function renderAcceptedDates(name, data) {
  const el = document.getElementById(`accdates-${name}`);
  if (!el) return;
  const recent = data.recent || [];
  if (!recent.length) { el.innerHTML = ''; return; }
  const rows = recent.map(entry => `
    <div style="display:flex;gap:4px;align-items:baseline;font-size:9px;line-height:1.5">
      <span style="color:#9ca3af;font-weight:600">CM:</span>
      <span style="color:#e5e7eb">${entry.condition_met}</span>
      <span style="color:#9ca3af;font-weight:600">→ Acc:</span>
      <span style="color:#fbbf24;font-weight:600">${entry.accepted}</span>
    </div>`).join('');
  el.innerHTML = `
    <div style="font-size:9px;font-weight:700;color:var(--muted);letter-spacing:.5px;
      text-transform:uppercase;margin-top:4px;margin-bottom:2px">⚡ Recent Accepted Dates</div>
    ${rows}`;
}

async function refreshSingleFundamentals(name, btn) {
  btn.classList.add('loading'); btn.textContent = '…';
  const el = document.getElementById(`fund-unified-${name}`);
  if (el) el.innerHTML = `<div class="news-loading">Fetching…</div>`;
  try {
    const r = await fetch('/api/fundamentals_refresh_single', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ symbol:name })
    });
    const d = await r.json();
    if (d.data && !d.data.error) renderFundamentals(name, d.data);
    else if (el) el.innerHTML = `<div class="news-loading">Error: ${(d.data && d.data.error) || 'fetch failed'}</div>`;
  } catch(e) {
    if (el) el.innerHTML = `<div class="news-loading">Refresh error</div>`;
  }
  btn.classList.remove('loading'); btn.textContent = '↻ Fund';
}

let fundAllPolling = false;
async function refreshAllFundamentals() {
  const btn = document.getElementById('btnFundAll');
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/fundamentals_refresh_all', { method:'POST' });
    const d = await r.json();
    setStatus(d.success === false ? (d.message || 'Fundamentals refresh already running') : 'Fundamentals refresh started for all stocks…');
    if (!fundAllPolling) { fundAllPolling = true; pollFundRefreshStatus(); }
  } catch(e) { setStatus('Fundamentals refresh error'); btn.classList.remove('loading'); }
}

async function pollFundRefreshStatus() {
  try {
    const r = await fetch('/api/fundamentals_refresh_status');
    const d = await r.json();
    await fetchAllFundamentals();
    if (d.running) {
      setStatus(`Fundamentals refresh: ${d.done}/${d.total}${d.current ? ' — ' + d.current : ''}`);
      setTimeout(pollFundRefreshStatus, 2000);
    } else {
      setStatus('Fundamentals refresh complete ✓');
      document.getElementById('btnFundAll').classList.remove('loading');
      fundAllPolling = false;
    }
  } catch(e) {
    fundAllPolling = false;
    document.getElementById('btnFundAll').classList.remove('loading');
  }
}

function renderFundamentals(name, d) {
  fundMap[name] = d;
  const el = document.getElementById(`fund-unified-${name}`);
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
    ${row('MCap',    d.mcap || null)}
    ${row('PE',      peVal)}
    ${row('PB',      pbVal)}
    ${row('PEG',     pegVal)}
    ${row('ROE',     roeVal)}
    ${row('D/E',     deVal)}
    ${row('PAT Qtr', patVal)}
    ${row('Revenue', revVal)}
    ${row('EPS Trend', epsVal)}
    ${row('EPS History', epsHistStr || null)}
    ${row('Margin',  marginVal)}`;
}

// ── Row scaffolding ─────────────────────────────────────────
function buildRows() {
  const table = document.getElementById('stockTable');
  table.innerHTML = '';
  const pendingMsg = CHART_MODE === '2'
    ? 'Not loaded — click Load or Refresh ↻ above'
    : 'Not loaded — click Refresh ↻ above';
  STOCKS.forEach(name => {
    const row = document.createElement('div');
    row.className = 'stock-row';
    row.id = `row-${name}`;
    row.innerHTML = `
      <div class="info-strip" id="info-${name}">
        <div class="info-name">${name}</div>
        <div class="info-ltp" id="ltp-${name}">—</div>
        <div class="info-chg" id="chg-${name}">—</div>
        <div class="info-vol" id="vol-${name}"></div>
        ${makeStockLinks(name)}
    <div class="accepted-dates" id="accdates-${name}"></div>
        <div class="fund-header">
          <span class="fund-header-label">📊 Fundamentals</span>
          <button class="btn-sm" id="fundbtn-${name}" onclick="refreshSingleFundamentals('${name}', this)">↻ Fund</button>
        </div>
        <div id="fund-unified-${name}" style="margin-top:3px;font-size:9px;line-height:1.5;color:#e5e7eb;">
          <div class="news-loading">Not fetched — click ↻ Fund</div>
        </div>
        <div class="cond-badge" id="cbadge-${name}"></div>
        ${makeNewsSection(name)}
      </div>`;
    TF_LIST.forEach(tf => {
      const cell = document.createElement('div');
      cell.className = 'chart-cell';
      cell.id = `cell-${name}-${tf}`;
      const tfLabel = tf==='5m' ? '5 Min' : tf==='1d' ? '1 Day' : '1 Week';
      cell.innerHTML = `
        <div class="cell-label">
          <span id="stdir-${name}-${tf}" class="st-dir"></span>
          <span>${tfLabel}</span>
          <button class="btn-sm" id="rbtn-${name}-${tf}" onclick="refreshSingleChart('${name}','${tf}',this)">↻</button>
        </div>
        <div class="chart-wrap" id="chartdiv-${name}-${tf}" style="width:${sizeState[tf].width}px;height:${sizeState[tf].h}px">
          <div class="chart-pending" style="width:100%;height:100%">${pendingMsg}</div>
        </div>`;
      row.appendChild(cell);
      // Insert the Weekly Line chart column right after the 1D column and
      // before the 1W candlestick column — same weekly data, no indicators.
      if (tf === '1d') {
        const wcell = document.createElement('div');
        wcell.className = 'chart-cell';
        wcell.id = `cell-${name}-wline`;
        wcell.innerHTML = `
          <div class="cell-label">
            <span></span>
            <span>Weekly Line</span>
            <div class="cell-label-btns">
              <button class="btn-sm" id="drawbtn-${name}" onclick="toggleDrawMode('${name}', this)" title="Draw a trend line on this chart">✏ Draw</button>
              <button class="btn-sm" onclick="clearDrawings('${name}')" title="Clear drawn lines">✕</button>
            </div>
          </div>
          <div class="chart-wrap" id="chartdiv-${name}-wline" style="width:${wlineSize().width}px;height:${wlineSize().h}px">
            <div class="chart-pending" style="width:100%;height:100%">${pendingMsg}</div>
          </div>`;
        row.appendChild(wcell);
      }
    });
    table.appendChild(row);
  });
}

// ── Chart building with lightweight-charts ───────────────────
function disposeChart(key) {
  const reg = chartReg[key];
  if (reg && reg.chart) {
    try { reg.chart.remove(); } catch(e) {}
  }
  delete chartReg[key];
}

function renderChart(name, tf, data) {
  const key  = `${name}_${tf}`;
  const container = document.getElementById(`chartdiv-${name}-${tf}`);
  if (!container) return;
  disposeChart(key);
  container.innerHTML = '';
  container.style.width  = sizeState[tf].width + 'px';
  container.style.height = sizeState[tf].h + 'px';

  const chart = LightweightCharts.createChart(container, {
    width: sizeState[tf].width,
    height: sizeState[tf].h,
    layout: { background: { color: '#000000' }, textColor: '#d1d5db', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.06)' }, horzLines: { color: 'rgba(255,255,255,0.06)' } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: {
      borderVisible: false,
      timeVisible: tf === '5m',
      secondsVisible: false,
      rightOffset: 2,
      fixLeftEdge: true,
      fixRightEdge: true,
      lockVisibleTimeRangeOnResize: true,
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      mouseWheel: true,
      pinch: true,
      axisPressedMouseMove: { time: true, price: false },
      axisDoubleClickReset: true,
    },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#16a34a', downColor: '#dc2626',
    borderUpColor: '#16a34a', borderDownColor: '#dc2626',
    wickUpColor: '#16a34a', wickDownColor: '#dc2626',
  });

  const times   = data.times   || [];
  const candles = data.candles || [];
  const ohlc = candles.map((c, i) => ({ time: times[i], open: c.o, high: c.h, low: c.l, close: c.c }));
  candleSeries.setData(ohlc);

  // Volume histogram, squeezed into the bottom of the same pane
  const volSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const volumes = data.volume || [];
  const spikeFlag = data.vol_spike_flag || [];
  const volData = volumes.map((v, i) => ({
    time: times[i],
    value: v.y,
    color: spikeFlag[i] ? 'rgba(217,119,6,0.75)' : (v.up ? 'rgba(22,163,74,0.45)' : 'rgba(220,38,38,0.45)')
  }));
  volSeries.setData(volData);

  // EMA20 / EMA10 — brightened for visibility against the black chart background
  const ema20Series = chart.addLineSeries({ color:'#fbbf24', lineWidth:1, priceLineVisible:false, lastValueVisible:false });
  const ema10Series = chart.addLineSeries({ color:'#a5b4fc', lineWidth:1, priceLineVisible:false, lastValueVisible:false });
  const ema20 = (data.ema20 || []).map((v,i)=>({time:times[i], value:v})).filter(p=>p.value!=null);
  const ema10 = (data.ema10 || []).map((v,i)=>({time:times[i], value:v})).filter(p=>p.value!=null);
  ema20Series.setData(ema20);
  ema10Series.setData(ema10);

  // Supertrend: split into contiguous same-color segments (own series each,
  // so segments never draw a connecting line across a color flip)
  const stSeriesList = [];
  const stGreen = data.st_green || [];
  const stRed   = data.st_red   || [];
  function addSegments(arr, color) {
    let seg = [];
    for (let i = 0; i < arr.length; i++) {
      if (arr[i] != null) {
        seg.push({ time: times[i], value: arr[i] });
      } else if (seg.length) {
        const s = chart.addLineSeries({ color, lineWidth:2, priceLineVisible:false, lastValueVisible:false });
        s.setData(seg);
        stSeriesList.push(s);
        seg = [];
      }
    }
    if (seg.length) {
      const s = chart.addLineSeries({ color, lineWidth:2, priceLineVisible:false, lastValueVisible:false });
      s.setData(seg);
      stSeriesList.push(s);
    }
  }
  addSegments(stGreen, '#16a34a');
  addSegments(stRed,   '#dc2626');

  // Pivot points as horizontal price lines (lines only — no P/R1/S1 labels)
  const priceLines = [];
  if (data.pivots) {
    const pvColors = {PP:'#cbd5e1',R1:'#22c55e',R2:'#16a34a',R3:'#15803d',S1:'#ef4444',S2:'#dc2626',S3:'#b91c1c'};
    Object.entries(data.pivots).forEach(([k,v]) => {
      if (v == null) return;
      const pl = candleSeries.createPriceLine({
        price: v, color: pvColors[k] || '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: false, title: '',
      });
      priceLines.push(pl);
    });
  }

  // Markers: small-candle flags (5m) + condition-met flags (1d)
  const markers = [];
  if (tf === '5m') {
    (data.small_candle_flag || []).forEach((flag, i) => {
      if (flag) markers.push({ time: times[i], position: 'aboveBar', color: '#7c3aed', shape: 'circle', text: '' });
    });
  }
  if (tf === '1d') {
    (data.condition_met_flag_1d || []).forEach((flag, i) => {
      if (flag) markers.push({ time: times[i], position: 'belowBar', color: '#d97706', shape: 'arrowUp', text: '' });
    });
  }
  if (markers.length) {
    markers.sort((a,b) => (a.time > b.time ? 1 : a.time < b.time ? -1 : 0));
    candleSeries.setMarkers(markers);
  }

  // Canvas-driven sizing: rather than a manual bar-spacing slider, we let the
  // chart auto-fit ALL candles inside the current canvas width/height via
  // fitContent(). Increasing "Canvas Width" (e.g. for 1D, which has ~1yr of
  // daily candles) gives every candle more room and keeps them all visible;
  // decreasing it compresses them but they still all remain in view. Panning
  // beyond the data range is still bounded via fixLeftEdge/fixRightEdge above,
  // and you can further zoom/pan manually with scroll/pinch/drag at any time.
  chart.timeScale().fitContent();

  chartReg[key] = { chart, candleSeries, volSeries, ema20Series, ema10Series, stSeriesList, priceLines };

  // The Weekly Line chart is sourced from the exact same "1w" chart_data —
  // no separate fetch/refresh needed. Render/refresh it right alongside the
  // candlestick chart so a single ↻ (row button or top-bar "Refresh 1W")
  // updates both at once, quickly.
  if (tf === '1w') {
    renderLineChart(name, data);
  }
}

// ── Weekly Line Chart (no indicators, plain close-price line) ─────────────
// Draw-mode state per stock: { active, pending:{logical,price}|null, lines:[{p1,p2,series}] }
const drawState      = {};
const weeklyTimesMap = {}; // name -> times[] array of the weekly chart (needed to extend lines)
const dragStateMap   = {}; // name -> {lineIndex, pointKey} currently being dragged, or null

function getDrawState(name) {
  if (!drawState[name]) drawState[name] = { active: false, pending: null, lines: [] };
  return drawState[name];
}

function renderLineChart(name, weeklyData) {
  const key = `${name}_wline`;
  const container = document.getElementById(`chartdiv-${name}-wline`);
  if (!container) return;
  disposeChart(key);
  container.innerHTML = '';
  const sz = wlineSize();
  container.style.width  = sz.width + 'px';
  container.style.height = sz.h + 'px';

  const chart = LightweightCharts.createChart(container, {
    width: sz.width,
    height: sz.h,
    layout: { background: { color: '#000000' }, textColor: '#d1d5db', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(255,255,255,0.06)' }, horzLines: { color: 'rgba(255,255,255,0.06)' } },
    rightPriceScale: { borderVisible: false },
    timeScale: {
      borderVisible: false, rightOffset: 2,
      fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true,
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
  });

  // Plain line series off closing price only — NO EMA, NO Supertrend,
  // NO pivots, NO volume, NO markers, per request.
  const lineSeries = chart.addLineSeries({
    color: '#60a5fa', lineWidth: 2, priceLineVisible: true, lastValueVisible: true,
  });
  const times   = weeklyData.times   || [];
  const candles = weeklyData.candles || [];
  const lineData = candles.map((c, i) => ({ time: times[i], value: c.c })).filter(p => p.value != null);
  lineSeries.setData(lineData);
  chart.timeScale().fitContent();

  weeklyTimesMap[name] = times;
  chartReg[key] = { chart, lineSeries, container, drawnSeries: [] };

  // Re-draw any extended lines the user already made for this stock (so a
  // weekly refresh doesn't wipe them out).
  const st = getDrawState(name);
  st.lines.forEach(line => _paintExtendedLine(name, line));

  setupDrawInteractions(name, chart, lineSeries, container);
  updateDrawButtonUI(name);
}

// Given two anchor points (fractional bar "logical" index + price), compute
// a line's value at EVERY bar across the whole weekly range — this is what
// makes the line extend fully across the chart instead of stopping at the
// two clicked points.
function _computeExtendedLineData(name, p1, p2) {
  const times = weeklyTimesMap[name] || [];
  if (!times.length) return [];
  const dLogical = p2.logical - p1.logical;
  if (Math.abs(dLogical) < 1e-6) {
    // Near-vertical click (same bar) — just draw the short segment, can't
    // meaningfully extend a vertical line across a time axis.
    const i1 = Math.min(times.length - 1, Math.max(0, Math.round(p1.logical)));
    const i2 = Math.min(times.length - 1, Math.max(0, Math.round(p2.logical)));
    return [{ time: times[i1], value: p1.price }, { time: times[i2], value: p2.price }];
  }
  const slope = (p2.price - p1.price) / dLogical;
  const data = [];
  for (let i = 0; i < times.length; i++) {
    data.push({ time: times[i], value: p1.price + slope * (i - p1.logical) });
  }
  return data;
}

function _paintExtendedLine(name, line) {
  const key = `${name}_wline`;
  const reg = chartReg[key];
  if (!reg) return;
  if (line.series) { try { reg.chart.removeSeries(line.series); } catch(e) {} }
  const series = reg.chart.addLineSeries({
    color: '#f472b6', lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => null,
  });
  series.setData(_computeExtendedLineData(name, line.p1, line.p2));
  const times = weeklyTimesMap[name] || [];
  const i1 = Math.min(times.length - 1, Math.max(0, Math.round(line.p1.logical)));
  const i2 = Math.min(times.length - 1, Math.max(0, Math.round(line.p2.logical)));
  series.setMarkers([
    { time: times[i1], position: 'inBar', color: '#f472b6', shape: 'circle', text: '' },
    { time: times[i2], position: 'inBar', color: '#f472b6', shape: 'circle', text: '' },
  ]);
  line.series = series;
  reg.drawnSeries.push(series);
}

function setupDrawInteractions(name, chart, lineSeries, container) {
  // Click twice (while Draw mode is ON) to define the two anchor points.
  chart.subscribeClick(param => {
    const st = getDrawState(name);
    if (!st.active) return;
    if (!param || param.logical == null || !param.point) return;
    const price = lineSeries.coordinateToPrice(param.point.y);
    if (price == null) return;
    const point = { logical: param.logical, price };
    if (!st.pending) {
      st.pending = point;
      setStatus(`${name}: first point set — click the end point of the line`);
    } else {
      const line = { p1: st.pending, p2: point, series: null };
      st.pending = null;
      st.lines.push(line);
      _paintExtendedLine(name, line);
      setStatus(`${name}: extended line drawn — drag either dot to adjust it`);
    }
  });

  const HIT_RADIUS = 10; // px

  function findHandleNear(x, y) {
    const st = getDrawState(name);
    for (let li = 0; li < st.lines.length; li++) {
      const line = st.lines[li];
      for (const pk of ['p1', 'p2']) {
        const p  = line[pk];
        const px = chart.timeScale().logicalToCoordinate(p.logical);
        const py = lineSeries.priceToCoordinate(p.price);
        if (px == null || py == null) continue;
        if (Math.hypot(px - x, py - y) <= HIT_RADIUS) return { lineIndex: li, pointKey: pk };
      }
    }
    return null;
  }

  container.addEventListener('mousedown', (e) => {
    const st = getDrawState(name);
    if (!st.active) return;
    const rect = container.getBoundingClientRect();
    const hit = findHandleNear(e.clientX - rect.left, e.clientY - rect.top);
    if (hit) {
      dragStateMap[name] = hit;
      e.stopPropagation();
    }
  });

  container.addEventListener('mousemove', (e) => {
    const drag = dragStateMap[name];
    if (!drag) return;
    const rect = container.getBoundingClientRect();
    const logical = chart.timeScale().coordinateToLogical(e.clientX - rect.left);
    const price   = lineSeries.coordinateToPrice(e.clientY - rect.top);
    if (logical == null || price == null) return;
    const st   = getDrawState(name);
    const line = st.lines[drag.lineIndex];
    if (!line) return;
    line[drag.pointKey] = { logical, price };
    _paintExtendedLine(name, line);
  });

  function endDrag() {
    dragStateMap[name] = null;
  }
  container.addEventListener('mouseup', endDrag);
  container.addEventListener('mouseleave', endDrag);
}

function toggleDrawMode(name, btn) {
  const st = getDrawState(name);
  st.active = !st.active;
  st.pending = null;


  const reg = chartReg[`${name}_wline`];
  if (reg && reg.chart) {
    reg.chart.applyOptions({
      handleScroll: !st.active,
      handleScale: !st.active,
    });
  }

  updateDrawButtonUI(name);
  setStatus(st.active ? `${name}: Draw mode ON — click two points to place an extended line` : `${name}: Draw mode OFF`);
}

function updateDrawButtonUI(name) {
  const st = getDrawState(name);
  const btn = document.getElementById(`drawbtn-${name}`);
  const wrap = document.getElementById(`chartdiv-${name}-wline`);
  if (btn) btn.classList.toggle('active', st.active);
  if (wrap) wrap.classList.toggle('draw-mode', st.active);
}

function clearDrawings(name) {
  const key = `${name}_wline`;
  const reg = chartReg[key];
  const st  = getDrawState(name);
  st.lines = [];
  st.pending = null;
  dragStateMap[name] = null;
  if (reg && reg.drawnSeries) {
    reg.drawnSeries.forEach(s => { try { reg.chart.removeSeries(s); } catch(e) {} });
    reg.drawnSeries = [];
  }
  setStatus(`${name}: drawings cleared`);
}
function updateSTDir(name, tf, data) {
  const el = document.getElementById(`stdir-${name}-${tf}`);
  if (!el) return;
  const dir = data.last_st_dir;
  if      (dir === 1)  { el.className='st-dir st-bull'; el.textContent='▲ Bull'; }
  else if (dir === -1) { el.className='st-dir st-bear'; el.textContent='▼ Bear'; }
  else                 { el.textContent=''; }
}

async function refreshSingleChart(name, tf, btn) {
  btn.classList.add('loading'); btn.textContent = '…';
  setStatus(`Refreshing ${name} ${tf}…`);
  try {
    const r = await fetch('/api/refresh_single_tf', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ symbol:name, tf })
    });
    const d = await r.json();
    if (d.success) {
      await fetchAndRenderChart(name, tf);
      renderedSet.add(`${name}_${tf}`);
      // IMPORTANT: only ever advance pollSince using a SERVER-side timestamp
      // (the one the backend just recorded for this refresh in refresh_ts),
      // never the client's Date.now(). Mixing client-clock time into
      // pollSince can push it ahead of the server's real clock — even by a
      // little natural clock drift — which then makes every future
      // /api/ready_stocks?since=... check ("ts > since") permanently false.
      // That was the root cause of "single ↻ works, but the global
      // 'Refresh (All)' buttons stop updating charts / the info strip
      // afterwards": the backend was refreshing fine, the browser's
      // background polling loop just silently stopped noticing any of it.
      if (typeof d.ts === 'number' && d.ts > pollSince) pollSince = d.ts;
      await fetchQuotes();
      setStatus(`${name} ${tf} refreshed ✓${tf === '1w' ? ' (Weekly Line updated too)' : ''}`);
    } else {
      setStatus(`${name} ${tf} refresh failed`);
    }
  } catch(e) {
    setStatus(`${name} ${tf} refresh error`);
  }
  btn.classList.remove('loading'); btn.textContent = '↻';
}

let pollSince     = 0;
let renderedSet   = new Set();
let overlayHidden = true;

async function initialBatchRender() {
  try {
    const r = await fetch('/api/ready_stocks?since=0');
    const d = await r.json();
    if (!d.items || d.items.length === 0) return;
    for (const item of d.items) { if (item.ts > pollSince) pollSince = item.ts; }
    const byName = {};
    for (const item of d.items) {
      if (!byName[item.name]) byName[item.name] = [];
      byName[item.name].push(item.tf);
    }
    let rendered = 0;
    const total  = Object.keys(byName).length;
    const loadProg = document.getElementById('loadProg');
    for (const name of STOCKS) {
      const tfs = byName[name];
      if (!tfs || tfs.length === 0) continue;
      for (const tf of tfs) {
        await fetchAndRenderChart(name, tf);
        renderedSet.add(`${name}_${tf}`);
      }
      rendered++;
      if (loadProg) loadProg.textContent = `${rendered} / ${total} stocks loaded`;
      await sleep(0);
    }
  } catch(e) { console.error('initialBatchRender error:', e); }
}

async function fetchAndRenderChart(name, tf) {
  try {
    const r = await fetch(`/api/chart?symbol=${encodeURIComponent(name)}&tf=${tf}`);
    const d = await r.json();
    if (d.pending || d.error) return;
    chartData[`${name}_${tf}`] = d;
    renderChart(name, tf, d);   // renderChart() also refreshes the Weekly Line chart when tf === '1w'
    updateSTDir(name, tf, d);
    if (tf === '5m' && d.end_date_ltp != null) {
      // FIX: previously this merge omitted `volume`, so after a manual
      // refresh (single-row ↻ or the top-bar "Refresh 5m (All)" button) the
      // Vol figure stayed frozen on the old value while LTP/%chg updated.
      // Now we recompute total volume directly from the freshly-fetched
      // chart's per-candle volume array, so LTP / %chg / Volume all move
      // together to the latest live data, every time this function runs —
      // whether triggered by a single-stock refresh, "Refresh All", or the
      // normal live-polling loop.
      const existing = quotesMap[name] || {};
      const freshVol = (d.volume || []).reduce((sum, v) => sum + (v.y || 0), 0);
      const updated = {
        ...existing,
        ltp:     d.end_date_ltp,
        open:    d.end_date_open,
        high:    d.end_date_high,
        low:     d.end_date_low,
        pchange: d.end_date_pchange ?? existing.pchange,
        change:  d.end_date_ltp != null && d.end_date_open != null
                   ? parseFloat((d.end_date_ltp - d.end_date_open).toFixed(2)) : existing.change,
        volume:  freshVol,
      };
      quotesMap[name] = updated;
      updateQuoteUI(name, updated);
    }
    if (tf === '1d' && d.condition_met_dates && d.condition_met_dates.length) {
      condDates[name] = d.condition_met_dates[0];
      const badge = document.getElementById(`cbadge-${name}`);
      if (badge) { badge.textContent = '⚡ ' + condDates[name]; badge.style.display = 'block'; }
    }
  } catch(e) {}
}

async function startLivePolling() {
  while (true) {
    try {
      const r = await fetch(`/api/ready_stocks?since=${pollSince}`);
      const d = await r.json();
      if (d.items && d.items.length > 0) {
        for (const item of d.items) {
          const key = `${item.name}_${item.tf}`;
          if (!renderedSet.has(key)) {
            renderedSet.add(key);
            await fetchAndRenderChart(item.name, item.tf);
            const el = document.getElementById('loadProg');
            if (el) {
              const done5m = [...renderedSet].filter(k => k.endsWith('_5m')).length;
              el.textContent = `${done5m} / ${STOCKS.length} stocks loaded`;
            }
          }
          if (item.ts > pollSince) pollSince = item.ts;
        }
      }
    } catch(e) {}
    await sleep(1500);
  }
}

function startQuotePolling() {
  fetchQuotes();
  // New Charts mode: quotes genuinely move every few seconds (live 5m
  // candles streaming in), so poll fast. Old Charts mode: quotes only
  // change when a ↻ refresh (single or "All") actually completes, and
  // fetchAndRenderChart() already pushes the fresh LTP/%chg/Volume straight
  // into the info strip the moment that refresh's 5m chart data is picked
  // up — so this is just a slow safety-net poll (cheap: /api/quotes only
  // reads the existing live_data dict in memory, it never calls yfinance)
  // in case anything was ever missed by that path.
  setInterval(fetchQuotes, CHART_MODE === '2' ? 15000 : 5000);
}

async function fetchQuotes() {
  try {
    const r = await fetch('/api/quotes');
    const d = await r.json();
    for (const [name, q] of Object.entries(d)) {
      quotesMap[name] = q;
      updateQuoteUI(name, q);
    }
    const first = Object.values(d)[0];
    if (first?.updated) setStatus(first.updated);
  } catch(e) {}
}

function updateQuoteUI(name, q) {
  const ltpEl = document.getElementById(`ltp-${name}`);
  const chgEl = document.getElementById(`chg-${name}`);
  const volEl = document.getElementById(`vol-${name}`);
  if (!ltpEl) return;
  ltpEl.textContent = '₹' + (q.ltp || 0).toFixed(2);
  const sign = (q.pchange || 0) >= 0 ? '+' : '';
  chgEl.textContent = `${sign}${(q.pchange || 0).toFixed(2)}%`;
  chgEl.className   = 'info-chg ' + ((q.pchange || 0) >= 0 ? 'chg-up' : 'chg-dn');
  volEl.textContent = 'Vol: ' + (q.volume||0).toLocaleString();
}

document.getElementById('sortSelect').addEventListener('change', function() {
  sortMode = this.value; applySortOrder();
});
let sortMode = 'default';

const FUND_SIGNAL_RANK = { 'Strong': 0, 'Moderate': 1, 'Weak': 2, 'Avoid': 3 };

function applySortOrder() {
  const table = document.getElementById('stockTable');
  const rows  = Array.from(table.querySelectorAll('.stock-row'));
  rows.sort((a, b) => {
    const na=a.id.replace('row-',''), nb=b.id.replace('row-','');
    const qa=quotesMap[na]||{}, qb=quotesMap[nb]||{};
    switch (sortMode) {
      case 'price_asc':   return (qa.ltp    ||0)-(qb.ltp    ||0);
      case 'price_desc':  return (qb.ltp    ||0)-(qa.ltp    ||0);
      case 'change_asc':  return (qa.pchange||0)-(qb.pchange||0);
      case 'change_desc': return (qb.pchange||0)-(qa.pchange||0);
      case 'cond_date':   return (condDates[nb]||'').localeCompare(condDates[na]||'');
           case 'accept_date': {
                 const aa = (acceptedMap[na] && acceptedMap[na].latest_accepted) || '';
                 const ab = (acceptedMap[nb] && acceptedMap[nb].latest_accepted) || '';
                 return ab.localeCompare(aa);
            }
           case 'ema_dist_asc': {
                 // "Near → Far": rank by ABSOLUTE distance from the EMA (0% = right on
                 // the EMA, further from 0 in either direction = farther away).
                 // Stocks with no EMA distance data sink to the bottom.
                 const va = emaDistMap[na] && emaDistMap[na].latest_ema_distance;
                 const vb = emaDistMap[nb] && emaDistMap[nb].latest_ema_distance;
                 const hasA = va != null, hasB = vb != null;
                 if (hasA && !hasB) return -1;
                 if (!hasA && hasB) return  1;
                 if (!hasA && !hasB) return na.localeCompare(nb);
                 return Math.abs(va) - Math.abs(vb);
            }
           case 'never_below_10ema': {
                 // "Yes → No": stocks whose close never dipped below the 10 EMA
                 // (CloseNeverBelow10EMA == "Yes") sort first, then "No", then
                 // anything with no data at all sinks to the bottom.
                 const va = neverBelow10emaMap[na] && neverBelow10emaMap[na].latest_value;
                 const vb = neverBelow10emaMap[nb] && neverBelow10emaMap[nb].latest_value;
                 const rank = v => v === 'Yes' ? 0 : v === 'No' ? 1 : 2;
                 const ra = rank(va), rb = rank(vb);
                 if (ra !== rb) return ra - rb;
                 return na.localeCompare(nb);
            }
      case 'above_st_1d':
      case 'above_st_1w': {
        const stTf = sortMode === 'above_st_1d' ? '1d' : '1w';
        const da = chartData[`${na}_${stTf}`];
        const db = chartData[`${nb}_${stTf}`];
        const aboveA = da && da.last_st_dir === 1;
        const aboveB = db && db.last_st_dir === 1;
        if (aboveA && !aboveB) return -1;
        if (!aboveA && aboveB) return  1;
        return na.localeCompare(nb);
      }
      case 'fund_score_desc': {
        const fa = fundMap[na], fb = fundMap[nb];
        const va = (fa && !fa.error && fa.signal != null);
        const vb = (fb && !fb.error && fb.signal != null);
        if (va && !vb) return -1;
        if (!va && vb) return  1;
        if (!va && !vb) return na.localeCompare(nb);
        const ra = FUND_SIGNAL_RANK[fa.signal] ?? 2;
        const rb = FUND_SIGNAL_RANK[fb.signal] ?? 2;
        if (ra !== rb) return ra - rb;
        return (fb.score || 0) - (fa.score || 0);
      }
      default: return na.localeCompare(nb);
    }
  });
  rows.forEach(r => table.appendChild(r));
}

let chartAllPolling = {};
let loadAllPolling  = {};

async function loadTF(tf) {
  const btnId = {'5m':'btnLoad5m','1d':'btnLoad1d','1w':'btnLoad1w'}[tf];
  const btn   = document.getElementById(btnId);
  if (btn) btn.classList.add('loading');
  setStatus(`Loading ${tf} charts from saved files…`);
  for (const name of STOCKS) renderedSet.delete(`${name}_${tf}`);
  try {
    const r = await fetch(`/api/load_${tf}`, { method:'POST' });
    const d = await r.json();
    if (d.success === false) {
      setStatus(d.message || `${tf} load not available`);
      if (btn) btn.classList.remove('loading');
      return;
    }
    if (!loadAllPolling[tf]) {
      loadAllPolling[tf] = true;
      pollLoadStatus(tf, btn);
    }
  } catch(e) {
    if (btn) btn.classList.remove('loading');
    setStatus(`${tf} load error`);
  }
}

async function pollLoadStatus(tf, btn) {
  try {
    const statusResp = await fetch(`/api/load_status?tf=${tf}`);
    const d = await statusResp.json();
    const readyResp = await fetch(`/api/ready_stocks?since=${pollSince}`);
    const rd = await readyResp.json();
    if (rd.items && rd.items.length) {
      for (const item of rd.items) {
        const key = `${item.name}_${item.tf}`;
        if (item.tf === tf && !renderedSet.has(key)) {
          renderedSet.add(key);
          await fetchAndRenderChart(item.name, item.tf);   // 1w renders the Weekly Line chart too
        }
        if (item.ts > pollSince) pollSince = item.ts;
      }
    }
    await fetchQuotes();
    if (d.running) {
      setStatus(`Loading ${tf}: ${d.done}/${d.total}${d.current ? ' — ' + d.current : ''}`);
      setTimeout(() => pollLoadStatus(tf, btn), 800);
    } else {
      setStatus(`${tf} load complete ✓${tf === '1w' ? ' (Weekly Line charts loaded too)' : ''}`);
      if (btn) btn.classList.remove('loading');
      loadAllPolling[tf] = false;
    }
  } catch(e) {
    if (btn) btn.classList.remove('loading');
    loadAllPolling[tf] = false;
  }
}

async function refreshTF(tf) {
  const btnId = {'5m':'btn5m','1d':'btn1d','1w':'btn1w'}[tf];
  const btn   = document.getElementById(btnId);
  btn.classList.add('loading');
  setStatus(`Starting ${tf} refresh…`);
  for (const name of STOCKS) renderedSet.delete(`${name}_${tf}`);
  try {
    await fetch(`/api/refresh_${tf}`, { method:'POST' });
    if (!chartAllPolling[tf]) {
      chartAllPolling[tf] = true;
      pollChartRefreshStatus(tf, btn);
    }
  } catch(e) {
    btn.classList.remove('loading');
    setStatus(`${tf} refresh error`);
  }
}

async function pollChartRefreshStatus(tf, btn) {
  try {
    const statusResp = await fetch(`/api/refresh_chart_status?tf=${tf}`);
    const d = await statusResp.json();
    const readyResp = await fetch(`/api/ready_stocks?since=${pollSince}`);
    const rd = await readyResp.json();
    if (rd.items && rd.items.length) {
      for (const item of rd.items) {
        const key = `${item.name}_${item.tf}`;
        if (item.tf === tf && !renderedSet.has(key)) {
          renderedSet.add(key);
          await fetchAndRenderChart(item.name, item.tf);   // 1w renders the Weekly Line chart too
        }
        if (item.ts > pollSince) pollSince = item.ts;
      }
    }

    // All three tfs now push fresh quotes into live_data on the backend
    // (see the earlier fix), so the strip should refresh regardless of tf.
    await fetchQuotes();

    if (d.running) {
      setStatus(`${tf} refresh: ${d.done}/${d.total}${d.current ? ' — ' + d.current : ''}`);
      setTimeout(() => pollChartRefreshStatus(tf, btn), 1000);
    } else {
      setStatus(`${tf} refresh complete ✓${tf === '1w' ? ' (Weekly Line charts updated too)' : ''}`);
      btn.classList.remove('loading');
      chartAllPolling[tf] = false;
    }
  } catch(e) {
    btn.classList.remove('loading');
    chartAllPolling[tf] = false;
  }
}

function setStatus(msg) { document.getElementById('statusBadge').textContent = msg; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

window.addEventListener('DOMContentLoaded', () => {
  buildRows();
  if (CHART_MODE === '2') {
    setStatus('Old Charts mode — click 📂 Load or ↻ Refresh above to load charts.');
  } else {
    setStatus('New Charts mode — click ↻ Refresh above to load charts.');
  }
  // NOTE: charts are no longer auto-loaded on page open in either mode.
  // startLivePolling() just watches for anything that becomes ready as a
  // result of the person clicking a Load/Refresh button, and renders it.
  startLivePolling();
  startQuotePolling();
  fetchAllFundamentals();
  fetchAcceptedDates();
  fetchEmaDistances();
  fetchNeverBelow10Ema();
});</script>
</body>
</html>
"""

# ── startup ───────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"✅ Loaded {len(STOCKS)} stocks")
    print(f"✅ Condition met data: {len(condition_met_map)} stocks")
    print(f"✅ Chart mode: {'Old Charts (use the page Load/Refresh buttons)' if CHART_MODE == '2' else 'New Charts (use the page Refresh buttons)'}")

    # Fundamentals and News caches are loaded from their saved files on disk in
    # BOTH New Charts and Old Charts modes. Neither is ever auto-fetched from
    # the internet at startup — that only ever happens when you click a ↻
    # button (per-stock) or one of the "Refresh ... (All)" buttons in the top bar.
    load_fundamentals_from_file()
    load_news_from_file()
    print("ℹ️  Fundamentals / News caches loaded from file (if available). "
          "Use the ↻ buttons or 'Refresh ... (All)' buttons to fetch fresh data.")

    t1 = threading.Thread(target=fetch_quotes, daemon=True); t1.start()
    # NOTE: chart data is intentionally NOT auto-loaded/fetched here anymore,
    # in either mode. In Old Charts mode, use the page's 📂 Load 5m/1D/1W
    # buttons to load previously-saved charts from disk, or ↻ Refresh to
    # fetch fresh live data instead. In New Charts mode, use ↻ Refresh to
    # fetch charts live. Fundamentals and news are also on-demand only.
    print("ℹ️  Chart auto-loading is disabled — use the Load/Refresh buttons on the page.")


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

    _tunnel_url_holder = { "url": None }


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
        time.sleep(60)#interactive charts with extended line chart with no autoload