
#test code
#MyReal Charts with screener and tradingview links and news fetch
#UPDATED: OI CSV format now matches OI_summary.csv (from #modify #condition #Main1)
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
2
# Packages that Colab does NOT pre-install and that vanish on session restart
_ensure("fyers-apiv3", "fyers_apiv3")
_ensure("beautifulsoup4", "bs4")
_ensure("python-dateutil", "dateutil")
_ensure("flask", "flask")
_ensure("yfinance", "yfinance")
_ensure("matplotlib", "matplotlib")

import os
CLOUDFLARED_EXE = os.path.join(r"C:\Users\sheik\PycharmProjects\MyProject", "cloudflared.exe")

def _ensure_cloudflared():
    """Download the cloudflared Windows binary only if it's not already present."""
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
import logging
import threading
import socket
import io
import re
import requests as _req_lib
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify, request, send_file, abort
from fyers_apiv3 import fyersModel
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from dateutil import parser as _dateutil_parser

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
logging.basicConfig(level=logging.ERROR)

client_id  = "6KX2O3OQK4-100"

BASE_DIR = r"C:\Users\sheik\PycharmProjects\MyProject"
os.makedirs(BASE_DIR, exist_ok=True)

token_file   = f"{BASE_DIR}/token.txt"
access_token = ""

if os.path.exists(token_file):
    with open(token_file, "r") as f:
        token_content = f.read().strip()
    if "access_token" in token_content:
        try:
            token_content = token_content.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    access_token = token_content.strip()

log_dir = f"{BASE_DIR}/logs/"
os.makedirs(log_dir, exist_ok=True)

CHART_DIR_5M     = f"{BASE_DIR}/5mnsCharts1"
CHART_DIR_1D     = f"{BASE_DIR}/1daycharts1"
CHART_DIR_1W     = f"{BASE_DIR}/weeklycharts1"
DROP_DETAILS_LOG = f"{BASE_DIR}/Dropdetails1.txt"
FUNDAMENTALS_CACHE_FILE = f"{BASE_DIR}/fundamentals_cache.json"
NEWS_CACHE_FILE  = f"{BASE_DIR}/news_cache.json"
ALL_NEWS_JSON_FILE = f"{BASE_DIR}/all_news_items.json"
END_DATE_FILE    = f"{BASE_DIR}/end_date.txt"
OI_CACHE_FILE    = f"{BASE_DIR}/oi_cache.json"

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
            _auto_start, _auto_end = None, None
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
    print("ℹ️  Old Charts mode: NO API calls will be made for charts. Saved JSON files only.\n")

    SAVED_END_DATE = None
    if os.path.exists(END_DATE_FILE):
        try:
            with open(END_DATE_FILE, "r") as _f:
                SAVED_END_DATE = datetime.strptime(_f.read().strip(), "%Y-%m-%d").date()
            print(f"✅ Loaded saved end date: {SAVED_END_DATE}")
        except Exception as _e:
            print(f"⚠ Could not load end date: {_e}")
    else:
        print("⚠ No saved end date found. Fyers quotes will use today's date.")

# ── Fyers client ──────────────────────────────────────────────
fyers = fyersModel.FyersModel(
    client_id=client_id,
    token=access_token,
    is_async=False,
    log_path=log_dir
)

stocks_file = f"{BASE_DIR}/matched_stocks.txt"
STOCKS = []

if os.path.exists(stocks_file):
    with open(stocks_file, "r") as f:
        content = f.read().strip()
    try:
        if "STOCKS" in content:
            content = content.split("=", 1)[1].strip()
        STOCKS = [s.strip() for s in ast.literal_eval(content)]
    except Exception as e:
        print(f"❌ Failed to parse STOCKS: {e}")

CONDITION_MET_EXCEL = f"{BASE_DIR}/1hour_Condition_Met_Stocks.xlsx"
condition_met_map   = {}

def load_condition_met_excel():
    global condition_met_map
    condition_met_map = {}
    if not os.path.exists(CONDITION_MET_EXCEL):
        print(f"⚠ Condition met Excel not found: {CONDITION_MET_EXCEL}")
        return
    try:
        df_cond = pd.read_excel(CONDITION_MET_EXCEL)
        df_cond.columns = [c.strip() for c in df_cond.columns]
        if "StockName" not in df_cond.columns or "ConditionMetDate" not in df_cond.columns:
            print(f"⚠ Excel columns not found. Got: {list(df_cond.columns)}")
            return
        for _, row in df_cond.iterrows():
            sym      = str(row["StockName"]).strip()
            date_val = row["ConditionMetDate"]
            if pd.isna(date_val):
                continue
            date_str = date_val.strftime("%Y-%m-%d") if hasattr(date_val, 'strftime') \
                       else str(date_val).strip()[:10]
            condition_met_map.setdefault(sym, [])
            if date_str not in condition_met_map[sym]:
                condition_met_map[sym].append(date_str)
        print(f"✅ Condition met data loaded for {len(condition_met_map)} stocks")
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

# ── OI Sentiment cache (on-demand only — see OI SECTION below) ─
oi_cache      = {}
oi_cache_lock = threading.Lock()

oi_refresh_status = {"running": False, "done": 0, "total": 0, "current": ""}
oi_refresh_lock   = threading.Lock()

# ── Fundamentals refresh status (on-demand only, via buttons) ──
fundamentals_refresh_status = {"running": False, "done": 0, "total": 0, "current": ""}
fundamentals_refresh_lock   = threading.Lock()

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
        import yfinance as yf
        t    = yf.Ticker(f"{symbol_name}.NS")

        # Resolve full company name for use in other sources' search queries
        try:
            info = t.info or {}
            full_name = (info.get("longName") or info.get("shortName") or "").strip()
        except Exception as e:
            print(f"  [yfinance] {symbol_name} could not resolve full name: {e}")

        news = t.news or []
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

    # ── Relevance filter setup for Google News / Bing News ────
    # Headline must contain the symbol OR a meaningful (4+ char) word from
    # the company name to be accepted from these two "loose" search sources.
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
        # SEBI disclosures / compliance
        "disclosure under sebi",
        "disclosure pursuant to",
        "disclosure of reasons",
        "continual disclosure",
        "disclosure under regulation",
        "disclosure under clause",
        "certificate under",
        "compliance certificate",
        "reconciliation of share capital",
        "statement of investor complaints",
        "investor grievance",
        "investor complaint",

        # Trading window
        "trading window",
        "closure of trading window",
        "opening of trading window",

        # Routine regulatory filings
        "shareholding pattern",
        "regulation 29",
        "regulation 31",
        "regulation 74",
        "insider trading",          # routine policy updates (not actual insider news)
        "code of conduct",
        "newspaper publication",
        "newspaper advertisement",
        "intimation of board meeting",   # keep actual results, block mere intimations
        "outcome of board meeting",      # too generic — remove if you want these
        "proceedings of",
        "minutes of",
        "corrigendum",
        "erratum",

        # AGM/EGM routine
        "notice of agm",
        "notice of egm",
        "notice of extraordinary",
        "postal ballot",
        "e-voting",
        "evoting",
        "scrutinizer report",
        "voting results",

        # Routine filings
        "annual report",
        "annual return",
        "loss of share certificate",
        "duplicate share certificate",
        "transfer of shares",
        "transmission of shares",
        "unclaimed dividend",
        "iepf",
        "change in address",
        "change in registrar",
        "appointment of registrar",
        "book closure for agm",
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

                # Block routine/noise announcements
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

    # ── Save ALL raw items per source into debug store ────────
    with news_debug_lock:
        news_debug_raw[symbol_name] = {
            "yfinance":    [i for i in all_items if i.get("source_tag") == "yfinance"],
            "google_news": [i for i in all_items if i.get("source_tag") == "google_news"],
            "yahoo_rss":   [i for i in all_items if i.get("source_tag") == "yahoo_rss"],
            "bing_news":   [i for i in all_items if i.get("source_tag") == "bing_news"],
            "nse":         [i for i in all_items if i.get("source_tag") == "nse"],
        }

    # ── Deduplicate by normalised headline ────────────────────
    seen   = set()
    unique = []
    for item in all_items:
        key = "".join(item["headline"].lower().split())[:60]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    # ── Sort latest first ──────────────────────────────────────
    unique.sort(key=lambda x: x["epoch"], reverse=True)

    # ── Save ALL deduped items (uncapped) to the all-news JSON ──
    save_all_news_items(symbol_name, unique)

    # ── Select items for the live dashboard cache ──────────────
    # Keep ALL of today's (IST) items instead of a hard cap. Older items
    # only pad the list up to max_items when today has fewer than that.
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
        final = today_items[:25]   # safety cap
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
    SOURCE_ORDER = [ "yfinance", "google_news", "yahoo_rss", "bing_news", "nse"]
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
            lines.append(f"  ▶ {symbol}   "
                         f"(total raw: {total_sym}  |  final served: {len(final_items)})\n")
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
    """Background job for the global 'Refresh News (All)' button.
    Fetches news for every stock one-by-one so the frontend can render
    each stock's news as soon as it lands in the cache (via polling)."""
    with news_refresh_lock:
        news_refresh_status["running"] = True
        news_refresh_status["done"]    = 0
        news_refresh_status["total"]   = len(STOCKS)
        news_refresh_status["current"] = ""
    print(f"\n{'='*60}")
    print(f"🔄 News Refresh (All) started — {len(STOCKS)} stocks")
    print(f"{'='*60}")
    try:
        for sym in STOCKS:
            name = sym.split(":")[1].replace("-EQ", "") if ":" in sym else sym
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
        print(f"\n{'='*60}")
        print(f"✅ News refresh (all) complete for {len(news_cache)} stocks")
        print(f"{'='*60}\n")
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
    """
    Pulls last 4 years of annual EPS (Diluted preferred, fallback Basic) from yfinance.
    Returns chronological list: [{"year": "2022", "eps": 12.3}, ...]
    """
    try:
        import yfinance as yf
        t = ticker_obj or yf.Ticker(symbol_name.upper() + ".NS")
        fin = t.income_stmt   # annual statement, columns = report dates (newest first)
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
        return sorted(eps_list, key=lambda x: x["year"])   # oldest -> newest
    except Exception as e:
        print(f"⚠ [eps_history] {symbol_name} error: {e}")
        return []


def analyze_eps_trend(eps_list):
    """
    Classifies EPS trend as steady growth / volatile / imbalanced / declining.
    Returns dict merged into fundamentals result.
    """
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
        import yfinance as yf
        t = yf.Ticker(ticker_sym)
        info = t.info
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
        result["eps_ttm"]      = info.get("trailingEps")   # current TTM EPS
        result["eps_forward"]  = info.get("forwardEps")
        result["earnings_growth"] = info.get("earningsGrowth")  # analyst consensus, e.g. 0.11 = 11%
        eps_hist = fetch_eps_history(symbol_name, ticker_obj=t)
        result.update(analyze_eps_trend(eps_hist))
        roe = info.get("returnOnEquity")
        result["roe"] = round(roe * 100, 1) if roe is not None else None
        de = info.get("debtToEquity")
        result["de"] = round(de / 100, 2) if de is not None else None
        pm = info.get("profitMargins")
        result["pm"] = round(pm * 100, 1) if pm is not None else None
        try:
            qfin = t.quarterly_financials
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
            qrev = t.quarterly_income_stmt
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
    """Background job for the global 'Refresh Fundamentals (All)' button.
    Fetches fundamentals for every stock one-by-one so the frontend can
    render each stock's fundamentals as soon as it lands in the cache
    (via polling), instead of waiting for the whole batch to finish."""
    with fundamentals_refresh_lock:
        fundamentals_refresh_status["running"] = True
        fundamentals_refresh_status["done"]    = 0
        fundamentals_refresh_status["total"]   = len(STOCKS)
        fundamentals_refresh_status["current"] = ""
    print(f"\n{'='*60}")
    print(f"🔄 Fundamentals Refresh (All) started — {len(STOCKS)} stocks")
    print(f"{'='*60}")
    try:
        for sym in STOCKS:
            name = sym.split(":")[1].replace("-EQ", "") if ":" in sym else sym
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
        print(f"\n{'='*60}")
        print(f"✅ Fundamentals refresh (all) complete for {len(STOCKS)} stocks")
        print(f"{'='*60}\n")
    finally:
        with fundamentals_refresh_lock:
            fundamentals_refresh_status["running"] = False
            fundamentals_refresh_status["current"] = ""

# ============================================================
# OI SENTIMENT CLASSIFIER  (ON-DEMAND ONLY)

# ============================================================

OI_ATM_RANGE                       = 5
OI_CE_THRESH                       = 3.0
OI_PE_THRESH                       = 3.0
OI_MAX_PAIN_MIN_DIST_PCT           = 3.0
OI_BULLISH_SCORE_THRESH            = 12.0
OI_BEARISH_SCORE_THRESH            = -12.0
OI_LEAN_SCORE_THRESH               = 5.0
OI_STRONG_BUILD_SCORE              = 25.0
OI_FULL_CHAIN_RATIO_MIN_LAKH       = 0.3
OI_FULL_CHAIN_RATIO_GATE           = 1.3
OI_FULL_CHAIN_RATIO_WEIGHT         = 11.0
OI_FULL_CHAIN_RATIO_CAP            = 18.0
OI_FULL_CHAIN_UNWIND_RATIO_MIN_LAKH = 0.3
OI_FULL_CHAIN_UNWIND_RATIO_GATE     = 1.3
OI_FULL_CHAIN_UNWIND_RATIO_WEIGHT   = 9.0
OI_FULL_CHAIN_UNWIND_RATIO_CAP      = 14.0
OI_ZONE_LAKH_WEIGHT                 = 1.0
OI_ZONE_LAKH_CAP                    = 6.0
OI_ZONE_LAKH_MIN                    = 0.15
OI_STRONG_UNWIND_PCT                = 12.0


def load_oi_from_file():
    global oi_cache
    if not os.path.exists(OI_CACHE_FILE):
        print(f"ℹ️  No saved OI cache found yet: {OI_CACHE_FILE}")
        return
    try:
        with open(OI_CACHE_FILE, "r") as f:
            data = json.load(f)
        with oi_cache_lock:
            oi_cache.update(data)
        print(f"✅ OI cache loaded from file for {len(data)} stocks")
    except Exception as e:
        print(f"⚠ Failed to load OI cache file: {e}")


def save_oi_to_file():
    try:
        with oi_cache_lock:
            data = dict(oi_cache)
        with open(OI_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠ Failed to save OI cache file: {e}")


# ── OI CSV export / import (compact format — matches OI_summary.csv
#    column layout produced by the #modify #condition Main1 script) ──
OI_CSV_FILE = f"{BASE_DIR}/oi_cache.csv"

OI_CSV_COLUMNS = [
    "symbol", "bias", "bias_emoji", "category", "score", "confidence", "pcr", "prev_pcr",
    "support", "resistance", "ce_net_contracts", "pe_net_contracts",
    "ce_net_lakhs", "pe_net_lakhs", "atm_ce_delta_contracts", "atm_pe_delta_contracts",
    "score_breakdown", "fetched_at", "error",
]


def _oi_format_breakdown_text(r):
    """Renders score_breakdown (list of {points,label}) as one readable string,
    same as format_breakdown_text() in the Main1 condition-screener script."""
    parts = [
        f"{item.get('points', 0):+.1f} {item.get('label', '')}"
        for item in (r.get("score_breakdown") or [])
        if isinstance(item, dict)
    ]
    return " | ".join(parts)


def save_oi_to_csv():
    """
    Writes the current oi_cache dict to a COMPACT CSV that mirrors the
    OI_summary.csv produced by the Main1 condition-screener script —
    one row per stock, same column set, instead of the old ~40-column
    dump. Full detail is still kept in oi_cache.json for in-app use.
    """
    try:
        with oi_cache_lock:
            data = dict(oi_cache)
        rows = []
        for symbol, r in data.items():
            if not r or "error" in r:
                row = {c: "" for c in OI_CSV_COLUMNS}
                row["symbol"] = symbol
                row["error"]  = r.get("error", "unknown error") if r else "no data"
                rows.append(row)
                continue

            def _safe_num(v):
                try:
                    return float(v)
                except Exception:
                    return 0

            full_ce_chg = _safe_num(r.get("full_ce_chg", 0) or 0)
            full_pe_chg = _safe_num(r.get("full_pe_chg", 0) or 0)

            row = {
                "symbol":                 symbol,
                "bias":                   r.get("bias", ""),
                "bias_emoji":             r.get("bias_emoji", ""),
                "category":               r.get("category", ""),
                "score":                  r.get("score", ""),
                "confidence":             r.get("confidence", ""),
                "pcr":                    r.get("pcr", ""),
                "prev_pcr":               r.get("prev_pcr", ""),
                "support":                r.get("support", ""),
                "resistance":             r.get("resistance", ""),
                "ce_net_contracts":       full_ce_chg,
                "pe_net_contracts":       full_pe_chg,
                "ce_net_lakhs":           round(full_ce_chg / 100000, 2),
                "pe_net_lakhs":           round(full_pe_chg / 100000, 2),
                "atm_ce_delta_contracts": r.get("ce_oi_chg", ""),
                "atm_pe_delta_contracts": r.get("pe_oi_chg", ""),
                "score_breakdown":        _oi_format_breakdown_text(r),
                "fetched_at":             r.get("fetched_at", ""),
                "error":                  "",
            }
            rows.append(row)
        df = pd.DataFrame(rows, columns=OI_CSV_COLUMNS)
        df.to_csv(OI_CSV_FILE, index=False)
        print(f"✅ OI cache saved to CSV ({len(rows)} stocks) → {OI_CSV_FILE}")
    except Exception as e:
        print(f"⚠ Failed to save OI cache CSV: {e}")


def load_oi_from_csv():
    """
    Loads oi_cache from the compact OI_summary.csv-style CSV (used in
    Old Charts mode). Field names are normalized so the same frontend
    renderOI() JS works whether data came from a fresh fetch or from
    this CSV.
    """
    global oi_cache
    if not os.path.exists(OI_CSV_FILE):
        print(f"ℹ️  No saved OI CSV found yet: {OI_CSV_FILE}")
        return
    try:
        df = pd.read_csv(OI_CSV_FILE, keep_default_na=False)

        def _num(v):
            if v == "" or v is None:
                return None
            try:
                return float(v)
            except Exception:
                return None

        loaded = {}
        for _, row in df.iterrows():
            symbol = row.get("symbol", "")
            if not symbol:
                continue
            if row.get("error", ""):
                loaded[symbol] = {"error": row["error"]}
                continue
            r = {
                "symbol":             symbol,
                "bias":               row.get("bias", ""),
                "bias_emoji":         row.get("bias_emoji", ""),
                "category":           row.get("category", ""),
                "score":              _num(row.get("score", "")),
                "confidence":         _num(row.get("confidence", "")),
                "pcr":                _num(row.get("pcr", "")),
                "prev_pcr":           _num(row.get("prev_pcr", "")),
                "support":            _num(row.get("support", "")),
                "resistance":         _num(row.get("resistance", "")),
                "full_ce_chg":        _num(row.get("ce_net_contracts", "")),
                "full_pe_chg":        _num(row.get("pe_net_contracts", "")),
                "ce_net_lakhs":       _num(row.get("ce_net_lakhs", "")),
                "pe_net_lakhs":       _num(row.get("pe_net_lakhs", "")),
                "ce_oi_chg":          _num(row.get("atm_ce_delta_contracts", "")),
                "pe_oi_chg":          _num(row.get("atm_pe_delta_contracts", "")),
                "score_breakdown_text": row.get("score_breakdown", ""),
                "fetched_at":         row.get("fetched_at", ""),
            }
            loaded[symbol] = r
        with oi_cache_lock:
            oi_cache.update(loaded)
        print(f"✅ OI cache loaded from CSV for {len(loaded)} stocks")
    except Exception as e:
        print(f"⚠ Failed to load OI cache CSV: {e}")


def oi_json_safe(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    return value


def oi_normalize_chain(options_chain):
    df = pd.DataFrame(options_chain)
    rename = {}
    for col in df.columns:
        low = col.lower()
        if "strike" in low and "price" in low:
            rename[col] = "strike_price"
        elif low in ("option_type", "optiontype", "type"):
            rename[col] = "option_type"
        elif low in ("oi", "open_interest", "openinterest"):
            rename[col] = "oi"
        elif "prev" in low and "oi" in low:
            rename[col] = "prev_oi"
        elif low in ("volume", "vol"):
            rename[col] = "volume"
        elif low in ("ltp", "last", "last_price", "lastprice"):
            rename[col] = "ltp"
        elif low in ("bid",):
            rename[col] = "bid"
        elif low in ("ask",):
            rename[col] = "ask"
    df.rename(columns=rename, inplace=True)
    for col in ["strike_price", "oi", "prev_oi", "volume", "ltp"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            df[col] = 0
    return df


def oi_classify_sentiment(symbol, df, spot_price):
    """
    Aggregates OI data across ATM ± OI_ATM_RANGE strikes and classifies
    using a weighted score: ATM-zone signals are the PRIMARY driver,
    full-chain signals are CONFIRMATION only (v3 logic).
    """
    ce = df[df["option_type"].str.upper() == "CE"].copy()
    pe = df[df["option_type"].str.upper() == "PE"].copy()

    if ce.empty or pe.empty:
        return None

    all_strikes = df["strike_price"].dropna().unique()
    atm = min(all_strikes, key=lambda s: abs(s - spot_price))

    sorted_strikes = sorted(all_strikes)
    try:
        atm_idx = sorted_strikes.index(atm)
    except ValueError:
        atm_idx = len(sorted_strikes) // 2

    lo = max(0, atm_idx - OI_ATM_RANGE)
    hi = min(len(sorted_strikes), atm_idx + OI_ATM_RANGE + 1)
    window_strikes = sorted_strikes[lo:hi]

    ce_w = ce[ce["strike_price"].isin(window_strikes)]
    pe_w = pe[pe["strike_price"].isin(window_strikes)]

    total_ce_oi      = ce_w["oi"].sum()
    total_pe_oi      = pe_w["oi"].sum()
    total_ce_prev_oi = ce_w["prev_oi"].sum()
    total_pe_prev_oi = pe_w["prev_oi"].sum()

    ce_oi_chg = total_ce_oi - total_ce_prev_oi
    pe_oi_chg = total_pe_oi - total_pe_prev_oi

    ce_oi_chg_pct = (ce_oi_chg / total_ce_prev_oi * 100) if total_ce_prev_oi else 0.0
    pe_oi_chg_pct = (pe_oi_chg / total_pe_prev_oi * 100) if total_pe_prev_oi else 0.0

    full_ce_oi       = ce["oi"].sum()
    full_pe_oi       = pe["oi"].sum()
    full_ce_prev_oi  = ce["prev_oi"].sum()
    full_pe_prev_oi  = pe["prev_oi"].sum()

    pcr          = round(full_pe_oi  / full_ce_oi,       2) if full_ce_oi      else 0.0
    prev_pcr     = round(full_pe_prev_oi / full_ce_prev_oi, 2) if full_ce_prev_oi else 0.0
    pcr_chg      = round(pcr - prev_pcr, 2)

    full_ce_chg      = full_ce_oi - full_ce_prev_oi
    full_pe_chg      = full_pe_oi - full_pe_prev_oi
    full_ce_chg_pct  = round(full_ce_chg / full_ce_prev_oi * 100, 2) if full_ce_prev_oi else 0.0
    full_pe_chg_pct  = round(full_pe_chg / full_pe_prev_oi * 100, 2) if full_pe_prev_oi else 0.0

    def oi_arrow(chg_pct):
        if chg_pct >= 3:    return f"++ ({chg_pct:+.1f}%)"
        elif chg_pct >= 0.5: return f"+  ({chg_pct:+.1f}%)"
        elif chg_pct <= -3:  return f"-- ({chg_pct:+.1f}%)"
        elif chg_pct <= -0.5: return f"-  ({chg_pct:+.1f}%)"
        else:                return f"~  ({chg_pct:+.1f}%)"

    ce_arrow = oi_arrow(full_ce_chg_pct)
    pe_arrow = oi_arrow(full_pe_chg_pct)

    def pcr_arrow(chg):
        if chg >= 0.05:    return f"^ +{chg:.2f} (Bullish)"
        elif chg <= -0.05: return f"v {chg:.2f} (Bearish)"
        else:              return f"~ {chg:+.2f} (Neutral)"

    pcr_trend = pcr_arrow(pcr_chg)

    atm_pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0.0

    full_ce_vol  = ce["volume"].sum()
    full_pe_vol  = pe["volume"].sum()
    total_ce_vol = full_ce_vol
    total_pe_vol = full_pe_vol
    vol_pcr      = round(full_pe_vol / full_ce_vol, 2) if full_ce_vol else 0.0

    atm_ce = ce[ce["strike_price"] == atm]
    atm_pe = pe[pe["strike_price"] == atm]
    atm_ce_oi  = int(atm_ce["oi"].values[0])      if len(atm_ce) else 0
    atm_pe_oi  = int(atm_pe["oi"].values[0])      if len(atm_pe) else 0
    atm_ce_chg = int(atm_ce["oi"].values[0] - atm_ce["prev_oi"].values[0]) if len(atm_ce) else 0
    atm_pe_chg = int(atm_pe["oi"].values[0] - atm_pe["prev_oi"].values[0]) if len(atm_pe) else 0

    pain = {}
    for s in sorted_strikes:
        ce_pain = ce[ce["strike_price"] >= s]["oi"].sum() * (ce[ce["strike_price"] >= s]["strike_price"] - s).abs().mean() if not ce[ce["strike_price"] >= s].empty else 0
        pe_pain = pe[pe["strike_price"] <= s]["oi"].sum() * (s - pe[pe["strike_price"] <= s]["strike_price"]).abs().mean() if not pe[pe["strike_price"] <= s].empty else 0
        pain[s] = ce_pain + pe_pain
    max_pain_strike = min(pain, key=pain.get) if pain else atm

    max_pain_dist_pct = round((max_pain_strike - spot_price) / spot_price * 100, 2) if spot_price else 0.0
    max_pain_meaningful = abs(max_pain_dist_pct) >= OI_MAX_PAIN_MIN_DIST_PCT

    ce_building  = ce_oi_chg_pct >  OI_CE_THRESH
    pe_building  = pe_oi_chg_pct >  OI_PE_THRESH
    ce_unwinding = ce_oi_chg_pct < -OI_CE_THRESH
    pe_unwinding = pe_oi_chg_pct < -OI_PE_THRESH

    window_diff = pe_oi_chg_pct - ce_oi_chg_pct
    full_diff   = full_pe_chg_pct - full_ce_chg_pct

    score_breakdown = []

    def _add(points, label):
        points = round(float(points), 1)
        if points != 0:
            score_breakdown.append({"points": points, "label": label})
        return points

    score = 0.0

    window_pts = window_diff * 1.3
    if window_diff > 15:
        window_label = "Strong Put Writing (ATM zone)"
    elif window_diff > 3:
        window_label = "Moderate Put Writing (ATM zone)"
    elif window_diff < -15:
        window_label = "Strong Call Writing (ATM zone)"
    elif window_diff < -3:
        window_label = "Moderate Call Writing (ATM zone)"
    else:
        window_label = "Call/Put roughly balanced (ATM zone)"
    score += _add(window_pts, window_label)

    full_pts = full_diff * 0.3
    if full_diff > 0:
        full_label = "Full-chain confirmation (Put-heavy)"
    elif full_diff < 0:
        full_label = "Full-chain confirmation (Call-heavy)"
    else:
        full_label = "Full-chain confirmation (flat)"
    score += _add(full_pts, full_label)

    pcr_pts = pcr_chg * 40
    if pcr_chg > 0:
        pcr_label = "PCR improving"
    elif pcr_chg < 0:
        pcr_label = "PCR weakening"
    else:
        pcr_label = "PCR flat"
    score += _add(pcr_pts, pcr_label)

    if max_pain_meaningful:
        mp_pts = 8 if max_pain_dist_pct > 0 else -8
        mp_label = "Max Pain above spot" if max_pain_dist_pct > 0 else "Max Pain below spot"
        score += _add(mp_pts, mp_label)

    if vol_pcr >= 1.1:
        score += _add(5, "Volume favors Puts")
    elif vol_pcr <= 0.9:
        score += _add(-5, "Volume favors Calls")

    ratio_label = None
    ratio_pts = 0.0
    if full_ce_chg > 0 and full_pe_chg > 0:
        ce_l = full_ce_chg / 100000
        pe_l = full_pe_chg / 100000
        if max(ce_l, pe_l) >= OI_FULL_CHAIN_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l
            if ratio >= OI_FULL_CHAIN_RATIO_GATE:
                ratio_pts = -min(np.log(ratio) * OI_FULL_CHAIN_RATIO_WEIGHT, OI_FULL_CHAIN_RATIO_CAP)
                ratio_label = (
                    f"Full-chain Dual Build-Up is Call-heavy "
                    f"({ce_l:+.2f}L CE vs {pe_l:+.2f}L PE, {ratio:.1f}x)"
                )
            elif ratio <= 1 / OI_FULL_CHAIN_RATIO_GATE:
                inv_ratio = 1 / ratio
                ratio_pts = min(np.log(inv_ratio) * OI_FULL_CHAIN_RATIO_WEIGHT, OI_FULL_CHAIN_RATIO_CAP)
                ratio_label = (
                    f"Full-chain Dual Build-Up is Put-heavy "
                    f"({pe_l:+.2f}L PE vs {ce_l:+.2f}L CE, {inv_ratio:.1f}x)"
                )
    if ratio_label:
        score += _add(ratio_pts, ratio_label)

    unwind_label = None
    unwind_pts = 0.0
    if full_ce_chg < 0 and full_pe_chg < 0:
        ce_l = abs(full_ce_chg) / 100000
        pe_l = abs(full_pe_chg) / 100000
        if max(ce_l, pe_l) >= OI_FULL_CHAIN_UNWIND_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l if pe_l else float("inf")
            if ratio >= OI_FULL_CHAIN_UNWIND_RATIO_GATE:
                unwind_pts = min(np.log(ratio) * OI_FULL_CHAIN_UNWIND_RATIO_WEIGHT, OI_FULL_CHAIN_UNWIND_RATIO_CAP)
                unwind_label = (
                    f"Full-chain Dual Unwinding is Call-heavy "
                    f"({ce_l:.2f}L CE vs {pe_l:.2f}L PE unwound, {ratio:.1f}x) — bullish"
                )
            elif ratio <= 1 / OI_FULL_CHAIN_UNWIND_RATIO_GATE:
                inv_ratio = 1 / ratio
                unwind_pts = -min(np.log(inv_ratio) * OI_FULL_CHAIN_UNWIND_RATIO_WEIGHT, OI_FULL_CHAIN_UNWIND_RATIO_CAP)
                unwind_label = (
                    f"Full-chain Dual Unwinding is Put-heavy "
                    f"({pe_l:.2f}L PE vs {ce_l:.2f}L CE unwound, {inv_ratio:.1f}x) — bearish"
                )
    if unwind_label:
        score += _add(unwind_pts, unwind_label)

    zone_ce_l = ce_oi_chg / 100000
    zone_pe_l = pe_oi_chg / 100000
    zone_label = None
    zone_pts = 0.0
    if max(abs(zone_ce_l), abs(zone_pe_l)) >= OI_ZONE_LAKH_MIN:
        zone_diff_l = zone_pe_l - zone_ce_l
        zone_pts = max(-OI_ZONE_LAKH_CAP, min(OI_ZONE_LAKH_CAP, zone_diff_l * OI_ZONE_LAKH_WEIGHT))
        if zone_pts != 0:
            zone_label = (
                f"ATM±{OI_ATM_RANGE} zone raw flow: CE {zone_ce_l:+.2f}L vs PE {zone_pe_l:+.2f}L "
                f"(institutional tilt {'Put' if zone_diff_l > 0 else 'Call'}-heavy)"
            )
    if zone_label:
        score += _add(zone_pts, zone_label)

    score = round(score, 1)

    if score >= OI_BULLISH_SCORE_THRESH:
        bias = "Bullish"
    elif score >= OI_LEAN_SCORE_THRESH:
        bias = "Bullish Lean"
    elif score <= OI_BEARISH_SCORE_THRESH:
        bias = "Bearish"
    elif score <= -OI_LEAN_SCORE_THRESH:
        bias = "Bearish Lean"
    else:
        bias = "Neutral"

    if bias == "Bullish":
        if pe_building and not ce_building and not ce_unwinding:
            if score >= OI_STRONG_BUILD_SCORE:
                category, category_key, bias_emoji = "Long Build-Up", "long_build_up", "🟢"
            else:
                category, category_key, bias_emoji = "Bullish OI Bias (Fresh Put Writing)", "bullish_put_writing", "🟢"
        elif ce_unwinding and not pe_unwinding:
            if ce_oi_chg_pct <= -OI_STRONG_UNWIND_PCT:
                category, category_key, bias_emoji = "Strong Bullish (Heavy Call Unwinding)", "strong_short_covering", "🟢"
            else:
                category, category_key, bias_emoji = "Short Covering", "short_covering", "🟡"
        elif ce_building and pe_building:
            category, category_key, bias_emoji = "Bullish OI Bias (Dual Build-Up)", "bullish_dual", "🟢"
        else:
            category, category_key, bias_emoji = "Bullish OI Bias", "bullish_generic", "🟢"

    elif bias == "Bullish Lean":
        if pe_building and ce_unwinding:
            category, category_key, bias_emoji = "Bullish Lean (Fresh Put Writing + Call Unwinding)", "bullish_lean_put_write_call_unwind", "🟩"
        elif ce_unwinding and not pe_building and not pe_unwinding:
            category, category_key, bias_emoji = "Bullish Lean (Call Unwinding)", "bullish_lean_call_unwind", "🟩"
        elif pe_building and not ce_building:
            category, category_key, bias_emoji = "Bullish Lean (Fresh Put Writing)", "bullish_lean_put_writing", "🟩"
        elif ce_building and pe_building:
            category, category_key, bias_emoji = "Bullish Lean (Dual Build-Up)", "bullish_lean_dual", "🟩"
        else:
            category, category_key, bias_emoji = "Bullish Lean", "bullish_lean_generic", "🟩"

    elif bias == "Bearish":
        if ce_building and not pe_building and not pe_unwinding:
            if score <= -OI_STRONG_BUILD_SCORE:
                category, category_key, bias_emoji = "Short Build-Up", "short_build_up", "🔴"
            else:
                category, category_key, bias_emoji = "Bearish OI Bias (Fresh Call Writing)", "bearish_call_writing", "🔴"
        elif pe_unwinding and not ce_unwinding:
            if pe_oi_chg_pct <= -OI_STRONG_UNWIND_PCT:
                category, category_key, bias_emoji = "Strong Bearish (Heavy Put Unwinding)", "strong_long_unwinding", "🔴"
            else:
                category, category_key, bias_emoji = "Long Unwinding", "long_unwinding", "🟠"
        elif ce_building and pe_building:
            category, category_key, bias_emoji = "Bearish OI Bias (Dual Build-Up)", "bearish_dual", "🔴"
        else:
            category, category_key, bias_emoji = "Bearish OI Bias", "bearish_generic", "🔴"

    elif bias == "Bearish Lean":
        if ce_building and pe_unwinding:
            category, category_key, bias_emoji = "Bearish Lean (Fresh Call Writing + Put Unwinding)", "bearish_lean_call_write_put_unwind", "🟧"
        elif ce_building and not pe_unwinding and not pe_building:
            category, category_key, bias_emoji = "Bearish Lean (Fresh Call Writing)", "bearish_lean_call_writing", "🟧"
        elif pe_unwinding and not ce_unwinding:
            category, category_key, bias_emoji = "Bearish Lean (Put Unwinding)", "bearish_lean_put_unwind", "🟧"
        elif ce_building and pe_building:
            category, category_key, bias_emoji = "Bearish Lean (Dual Build-Up)", "bearish_lean_dual", "🟧"
        else:
            category, category_key, bias_emoji = "Bearish Lean", "bearish_lean_generic", "🟧"

    else:
        if ce_unwinding and pe_unwinding:
            category, category_key, bias_emoji = "Neutral / Mixed Positioning (Pre-Expiry Unwinding)", "neutral_unwind", "⚪"
        else:
            category, category_key, bias_emoji = "Neutral / Mixed Positioning", "neutral", "⚪"

    reasons = []
    tilt_word = "Put" if window_diff > 0 else ("Call" if window_diff < 0 else "balanced")
    reasons.append(
        f"ATM±{OI_ATM_RANGE} window: Put OI {pe_oi_chg_pct:+.1f}% vs Call OI {ce_oi_chg_pct:+.1f}% "
        f"— net tilt is {tilt_word} ({window_diff:+.1f} pts, primary signal)."
    )
    reasons.append(
        f"Full-chain confirmation: Call OI {full_ce_chg_pct:+.1f}%, Put OI {full_pe_chg_pct:+.1f}% "
        f"(contributes {full_diff * 0.3:+.1f} pts)."
    )
    if ratio_label:
        reasons.append(f"{ratio_label} (contributes {ratio_pts:+.1f} pts).")
    if unwind_label:
        reasons.append(f"{unwind_label} (contributes {unwind_pts:+.1f} pts).")
    if zone_label:
        reasons.append(f"{zone_label} (contributes {zone_pts:+.1f} pts).")
    pcr_word = "improving (bullish)" if pcr_chg > 0 else ("weakening (bearish)" if pcr_chg < 0 else "flat")
    reasons.append(
        f"PCR trend: {prev_pcr:.2f} → {pcr:.2f} ({pcr_chg:+.2f}), {pcr_word}."
    )
    if max_pain_meaningful:
        reasons.append(
            f"Max Pain at ₹{max_pain_strike:.0f} is {abs(max_pain_dist_pct):.1f}% "
            f"{'above' if max_pain_dist_pct > 0 else 'below'} spot — "
            f"{'supportive of upside' if max_pain_dist_pct > 0 else 'gravity pulling price down'}."
        )
    else:
        reasons.append(f"Max Pain at ₹{max_pain_strike:.0f} is close to spot ({max_pain_dist_pct:+.1f}%) — not a deciding factor.")
    if vol_pcr >= 1.1 or vol_pcr <= 0.9:
        reasons.append(f"Volume PCR of {vol_pcr:.2f} confirms {'Put' if vol_pcr > 1 else 'Call'} side dominance in today's activity.")

    if category_key in ("short_covering", "strong_short_covering"):
        reasons.append("Existing Call short positions are being covered rather than fresh Put writing appearing — often sharp but can lack follow-through.")
    elif category_key in ("long_unwinding", "strong_long_unwinding"):
        reasons.append("Existing Put longs are being exited rather than fresh Call writing appearing — confidence fading rather than active bearish bets.")
    elif "dual" in category_key:
        reasons.append("Both Call and Put OI are rising together — expect a volatile session; the score reflects which side currently has the edge.")
    elif category_key.startswith("bullish_lean") or category_key.startswith("bearish_lean"):
        reasons.append("Signal is directional but below the full build-up threshold — treat as an early watchlist hint, not a confirmed setup.")

    reasons.append(f"Weighted score: {score:+.1f} (Lean ±{OI_LEAN_SCORE_THRESH:.0f} / Full ±{OI_BULLISH_SCORE_THRESH:.0f}) → {bias} bias.")

    if abs(atm_ce_chg) > 0 or abs(atm_pe_chg) > 0:
        dominant = "Call" if abs(atm_ce_chg) > abs(atm_pe_chg) else "Put"
        reasons.append(
            f"At ATM strike ₹{atm:.0f}: CE OI chg={atm_ce_chg:+,} | PE OI chg={atm_pe_chg:+,} "
            f"— {dominant} side dominant at the money."
        )

    if total_ce_vol > 0 or total_pe_vol > 0:
        vol_side = "Put" if total_pe_vol > total_ce_vol else "Call"
        reasons.append(
            f"Volume breakdown — CE Vol: {int(total_ce_vol):,} | PE Vol: {int(total_pe_vol):,} "
            f"({vol_side} volume higher)."
        )

    return {
        "symbol"            : symbol,
        "spot"              : spot_price,
        "atm"               : atm,
        "max_pain"          : max_pain_strike,
        "max_pain_dist_pct" : max_pain_dist_pct,
        "max_pain_meaningful": max_pain_meaningful,
        "ce_oi"             : int(total_ce_oi),
        "pe_oi"             : int(total_pe_oi),
        "ce_oi_chg"         : int(ce_oi_chg),
        "pe_oi_chg"         : int(pe_oi_chg),
        "ce_oi_chg_pct"     : round(ce_oi_chg_pct, 2),
        "pe_oi_chg_pct"     : round(pe_oi_chg_pct, 2),
        "atm_ce_oi"         : atm_ce_oi,
        "atm_pe_oi"         : atm_pe_oi,
        "atm_ce_chg"        : atm_ce_chg,
        "atm_pe_chg"        : atm_pe_chg,
        "pcr"               : pcr,
        "prev_pcr"          : prev_pcr,
        "pcr_chg"           : pcr_chg,
        "pcr_trend"         : pcr_trend,
        "atm_pcr"           : atm_pcr,
        "vol_pcr"           : vol_pcr,
        "full_ce_oi"        : int(full_ce_oi),
        "full_pe_oi"        : int(full_pe_oi),
        "full_ce_prev_oi"   : int(full_ce_prev_oi),
        "full_pe_prev_oi"   : int(full_pe_prev_oi),
        "full_ce_chg"       : int(full_ce_chg),
        "full_pe_chg"       : int(full_pe_chg),
        "full_ce_chg_pct"   : full_ce_chg_pct,
        "full_pe_chg_pct"   : full_pe_chg_pct,
        "ce_arrow"          : ce_arrow,
        "pe_arrow"          : pe_arrow,
        "score"             : score,
        "score_breakdown"   : score_breakdown,
        "bias"              : bias,
        "bias_emoji"        : bias_emoji,
        "category"          : category,
        "category_key"      : category_key,
        "reasons"           : reasons,
        "_df"               : df,
    }


def oi_find_highest_addition(df, option_type):
    sub = df[df["option_type"].str.upper() == option_type].copy()
    if sub.empty:
        return None, 0
    sub["chg"] = sub["oi"] - sub["prev_oi"]
    idx = sub["chg"].idxmax()
    row = sub.loc[idx]
    return float(row["strike_price"]), float(row["chg"])


def oi_find_max_oi_strike(df, option_type):
    sub = df[df["option_type"].str.upper() == option_type]
    if sub.empty:
        return None
    idx = sub["oi"].idxmax()
    return float(sub.loc[idx, "strike_price"])


def oi_compute_confidence(r):
    window_diff = r["pe_oi_chg_pct"] - r["ce_oi_chg_pct"]
    full_diff   = r["full_pe_chg_pct"] - r["full_ce_chg_pct"]

    bullish_votes = 0
    bearish_votes = 0
    total_votes   = 0

    for val in (window_diff, full_diff, r["pcr_chg"]):
        total_votes += 1
        if val > 0:
            bullish_votes += 1
        elif val < 0:
            bearish_votes += 1

    if r["max_pain_meaningful"]:
        total_votes += 1
        if r["max_pain_dist_pct"] > 0:
            bullish_votes += 1
        else:
            bearish_votes += 1

    if r["vol_pcr"] >= 1.05 or r["vol_pcr"] <= 0.95:
        total_votes += 1
        if r["vol_pcr"] >= 1.05:
            bullish_votes += 1
        else:
            bearish_votes += 1

    if r["full_ce_chg"] > 0 and r["full_pe_chg"] > 0:
        ce_l = r["full_ce_chg"] / 100000
        pe_l = r["full_pe_chg"] / 100000
        if max(ce_l, pe_l) >= OI_FULL_CHAIN_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l if pe_l else float("inf")
            if ratio >= OI_FULL_CHAIN_RATIO_GATE or ratio <= 1 / OI_FULL_CHAIN_RATIO_GATE:
                total_votes += 1
                if ratio >= OI_FULL_CHAIN_RATIO_GATE:
                    bearish_votes += 1
                else:
                    bullish_votes += 1

    if r["full_ce_chg"] < 0 and r["full_pe_chg"] < 0:
        ce_l = abs(r["full_ce_chg"]) / 100000
        pe_l = abs(r["full_pe_chg"]) / 100000
        if max(ce_l, pe_l) >= OI_FULL_CHAIN_UNWIND_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l if pe_l else float("inf")
            if ratio >= OI_FULL_CHAIN_UNWIND_RATIO_GATE or ratio <= 1 / OI_FULL_CHAIN_UNWIND_RATIO_GATE:
                total_votes += 1
                if ratio >= OI_FULL_CHAIN_UNWIND_RATIO_GATE:
                    bullish_votes += 1
                else:
                    bearish_votes += 1

    zone_ce_l = r["ce_oi_chg"] / 100000
    zone_pe_l = r["pe_oi_chg"] / 100000
    if max(abs(zone_ce_l), abs(zone_pe_l)) >= OI_ZONE_LAKH_MIN:
        total_votes += 1
        if zone_pe_l - zone_ce_l > 0:
            bullish_votes += 1
        else:
            bearish_votes += 1

    if total_votes == 0:
        agree_ratio = 0.5
    elif r["bias"] in ("Bullish", "Bullish Lean"):
        agree_ratio = bullish_votes / total_votes
    elif r["bias"] in ("Bearish", "Bearish Lean"):
        agree_ratio = bearish_votes / total_votes
    else:
        agree_ratio = 1 - (abs(bullish_votes - bearish_votes) / total_votes)

    score_boost = min(abs(r["score"]) * 0.3, 12)
    confidence = 40 + agree_ratio * 45 + score_boost

    if r["bias"] == "Neutral":
        confidence = min(confidence, 65)
    elif r["bias"] in ("Bullish Lean", "Bearish Lean"):
        confidence = min(confidence, 75)

    return int(max(35, min(95, round(confidence))))


def oi_market_positioning_bullets(r):
    key = r["category_key"]
    if key == "long_build_up":
        return ["Fresh Put Writing (Decisive)", "Call Side Muted / Unwinding",
                "PCR Improving" if r["pcr_chg"] > 0 else "PCR Stable", "Support Strengthening"]
    elif key == "bullish_put_writing":
        return ["Fresh Put Writing (Moderate)", "Needs Confirmation",
                "PCR Improving" if r["pcr_chg"] > 0 else "PCR Stable", "Lean Bullish"]
    elif key == "short_build_up":
        return ["Fresh Call Writing (Decisive)", "Put Side Muted / Unwinding",
                "PCR Weakening" if r["pcr_chg"] < 0 else "PCR Stable", "Resistance Strengthening"]
    elif key == "bearish_call_writing":
        return ["Fresh Call Writing (Moderate)", "Needs Confirmation",
                "PCR Weakening" if r["pcr_chg"] < 0 else "PCR Stable", "Lean Bearish"]
    elif key == "short_covering":
        return ["Call Short Covering", "Put OI Relatively Stable", "Possible Sharp Rally", "Watch for Follow-through"]
    elif key == "strong_short_covering":
        return ["Heavy Call Short Covering", "Resistance Clearing Fast", "Put Side Relatively Stable", "Momentum Can Extend Quickly"]
    elif key == "long_unwinding":
        return ["Put Long Unwinding", "Call OI Relatively Steady", "Bullish Confidence Fading", "Support May Weaken"]
    elif key == "strong_long_unwinding":
        return ["Heavy Put Long Unwinding", "Support Eroding Fast", "Call Side Relatively Stable", "Downside Momentum Can Extend"]
    elif key == "bullish_dual":
        return ["Dual OI Build-Up", "Put Writers Have the Edge", "Score Tilts Bullish", "Volatility Expected"]
    elif key == "bearish_dual":
        return ["Dual OI Build-Up", "Call Writers Have the Edge", "Score Tilts Bearish", "Volatility Expected"]
    elif key == "neutral_unwind":
        return ["Both Sides Unwinding", "Likely Pre-Expiry Cleanup", "Await Fresh OI Confirmation"]
    elif key == "bullish_lean_call_unwind":
        return ["Call Writers Retreating", "No Fresh Put Writing Yet", "Directional Hint, Not Confirmed", "Watchlist Candidate"]
    elif key == "bullish_lean_put_write_call_unwind":
        return ["Fresh Put Writing", "Call Writers Retreating Too", "Two-Sided Bullish Confirmation", "Watchlist Candidate"]
    elif key == "bullish_lean_put_writing":
        return ["Early Put Writing", "Below Build-Up Threshold",
                "PCR Improving" if r["pcr_chg"] > 0 else "PCR Stable", "Watchlist Candidate"]
    elif key == "bullish_lean_dual":
        return ["Both Sides Adding, Slight Put Edge", "Not Decisive Yet", "Volatility Likely", "Watchlist Candidate"]
    elif key == "bullish_lean_generic":
        return ["Mild Bullish Tilt", "No Single Dominant Signal", "Watchlist Candidate"]
    elif key == "bearish_lean_call_writing":
        return ["Early Call Writing", "Below Build-Up Threshold",
                "PCR Weakening" if r["pcr_chg"] < 0 else "PCR Stable", "Watchlist Candidate"]
    elif key == "bearish_lean_call_write_put_unwind":
        return ["Fresh Call Writing", "Put Writers Retreating Too", "Two-Sided Bearish Confirmation", "Watchlist Candidate"]
    elif key == "bearish_lean_put_unwind":
        return ["Put Writers Retreating", "No Fresh Call Writing Yet", "Directional Hint, Not Confirmed", "Watchlist Candidate"]
    elif key == "bearish_lean_dual":
        return ["Both Sides Adding, Slight Call Edge", "Not Decisive Yet", "Volatility Likely", "Watchlist Candidate"]
    elif key == "bearish_lean_generic":
        return ["Mild Bearish Tilt", "No Single Dominant Signal", "Watchlist Candidate"]
    else:
        return ["No Clear Directional Edge", "Range-Bound Possibility", "Await Fresh OI Confirmation"]


def oi_conclusion_text(r):
    key = r["category_key"]
    mp_line = ""
    if r["max_pain_meaningful"]:
        mp_line = (
            f" Max Pain sitting {abs(r['max_pain_dist_pct']):.1f}% "
            f"{'above' if r['max_pain_dist_pct'] > 0 else 'below'} spot adds further weight to this view."
        )

    if key == "long_build_up":
        return ("Decisive Put writing near the ATM zone, confirmed across the full chain and an improving PCR, "
                f"points to a genuine Long Build-Up.{mp_line} Watch for fresh Call writing or Put unwinding to "
                "signal this view is losing strength.")
    elif key == "bullish_put_writing":
        return ("Put writing is happening and the score leans bullish, but the signal isn't decisive enough yet "
                "to call this a full Long Build-Up — without price/futures confirmation this reads more as a "
                f"bullish tilt than a committed directional bet.{mp_line}")
    elif key == "short_build_up":
        return ("Decisive Call writing near the ATM zone, confirmed across the full chain and a weakening PCR, "
                f"points to a genuine Short Build-Up.{mp_line} Watch for fresh Put writing or Call unwinding to "
                "signal this view is losing strength.")
    elif key == "bearish_call_writing":
        return ("Call writing is happening and the score leans bearish, but the signal isn't decisive enough yet "
                "to call this a full Short Build-Up — this reads more as resistance forming than a committed "
                f"downside bet.{mp_line}")
    elif key == "short_covering":
        return ("The move higher is being driven by Call short covering rather than fresh Put writing. This can "
                "produce a sharp rally, but sustainability depends on Put writers actively joining in — treat it "
                "as a reaction rather than a confirmed trend reversal until that happens.")
    elif key == "strong_short_covering":
        return (f"Call OI is unwinding sharply (ATM zone {r['ce_oi_chg_pct']:+.1f}%) with the Put side "
                f"comparatively stable — resistance is clearing out fast enough that this reads as a genuine "
                f"Bullish move rather than a tentative one.{mp_line}")
    elif key == "long_unwinding":
        return ("Put long unwinding shows bullish conviction fading even though Call resistance hasn't moved much. "
                "This pattern often precedes consolidation or a mild pullback unless Put writers step back in "
                "aggressively.")
    elif key == "strong_long_unwinding":
        return (f"Put OI is unwinding sharply (ATM zone {r['pe_oi_chg_pct']:+.1f}%) with the Call side "
                f"comparatively stable — support is eroding fast enough that this reads as a genuine "
                f"Bearish move rather than a tentative one.{mp_line}")
    elif key == "bullish_dual":
        return (f"Both Calls and Puts are seeing fresh additions, but the weighted score ({r['score']:+.1f}) tilts "
                f"in favour of Put writers.{mp_line} Expect a volatile session with a mild bullish lean rather than "
                "a clean directional move.")
    elif key == "bearish_dual":
        return (f"Both Calls and Puts are seeing fresh additions, but the weighted score ({r['score']:+.1f}) tilts "
                f"in favour of Call writers.{mp_line} Expect a volatile session with a mild bearish lean rather than "
                "a clean directional move.")
    elif key == "neutral_unwind":
        return ("Positions are being unwound on both sides — most consistent with pre-expiry cleanup or genuine "
                "indecision. Wait for fresh OI build-up before drawing any directional conclusion.")
    elif key == "bullish_lean_call_unwind":
        return ("Call writers are stepping back but fresh Put writing hasn't shown up yet. This is a directional "
                f"hint rather than a confirmed build-up — worth watchlisting for confirmation.{mp_line}")
    elif key == "bullish_lean_put_write_call_unwind":
        return ("Fresh Put writing is showing up at the same time Call writers are retreating — both point the "
                f"same way even though neither is decisive alone yet. Worth watchlisting for confirmation.{mp_line}")
    elif key == "bullish_lean_put_writing":
        return ("Early-stage Put writing is visible near the ATM zone, but the score isn't strong enough yet to "
                f"call this a Long Build-Up. Treat as an early watchlist signal.{mp_line}")
    elif key == "bullish_lean_dual":
        return (f"Both Calls and Puts are seeing some additions with a mild Put edge (score {r['score']:+.1f}). "
                f"Not decisive enough for a full bullish call yet.{mp_line}")
    elif key == "bullish_lean_generic":
        return (f"A mild bullish tilt (score {r['score']:+.1f}) without one clearly dominant signal. "
                f"Worth keeping on the watchlist rather than acting on immediately.{mp_line}")
    elif key == "bearish_lean_call_writing":
        return ("Early-stage Call writing is visible near the ATM zone, but the score isn't strong enough yet to "
                f"call this a Short Build-Up. Treat as an early watchlist signal.{mp_line}")
    elif key == "bearish_lean_call_write_put_unwind":
        return ("Fresh Call writing is showing up at the same time Put writers are retreating — both point the "
                f"same way even though neither is decisive alone yet. Worth watchlisting for confirmation.{mp_line}")
    elif key == "bearish_lean_put_unwind":
        return ("Put writers are stepping back but fresh Call writing hasn't shown up yet. This is a directional "
                f"hint rather than a confirmed build-up — worth watchlisting for confirmation.{mp_line}")
    elif key == "bearish_lean_dual":
        return (f"Both Calls and Puts are seeing some additions with a mild Call edge (score {r['score']:+.1f}). "
                f"Not decisive enough for a full bearish call yet.{mp_line}")
    elif key == "bearish_lean_generic":
        return (f"A mild bearish tilt (score {r['score']:+.1f}) without one clearly dominant signal. "
                f"Worth keeping on the watchlist rather than acting on immediately.{mp_line}")
    else:
        return (f"No signal cluster is strong enough to tilt the score meaningfully (score {r['score']:+.1f}). "
                "Market participants appear to be waiting for a fresh catalyst before committing to either side — "
                "a wait-and-watch approach is advisable until clearer OI trends emerge.")


def oi_compute_card_metrics(r):
    df = r["_df"]

    highest_pe_strike, highest_pe_chg = oi_find_highest_addition(df, "PE")
    highest_ce_strike, highest_ce_chg = oi_find_highest_addition(df, "CE")

    support    = oi_find_max_oi_strike(df, "PE")
    resistance = oi_find_max_oi_strike(df, "CE")

    if r["pe_oi_chg"] > r["ce_oi_chg"]:
        dominant_writers = "✅ Put Writers"
    elif r["ce_oi_chg"] > r["pe_oi_chg"]:
        dominant_writers = "✅ Call Writers"
    else:
        dominant_writers = "⚖️ Mixed / Balanced"

    r["confidence"]            = oi_compute_confidence(r)
    r["highest_pe_add_strike"] = highest_pe_strike
    r["highest_pe_add_chg"]    = highest_pe_chg
    r["highest_ce_add_strike"] = highest_ce_strike
    r["highest_ce_add_chg"]    = highest_ce_chg
    r["support"]               = support
    r["resistance"]            = resistance
    r["dominant_writers"]      = dominant_writers
    r["positioning"]           = oi_market_positioning_bullets(r)
    r["conclusion"]            = oi_conclusion_text(r)
    return r


def fetch_and_classify_oi(name):
    """
    Fetches the live option chain for one stock (by clean name, e.g. 'RELIANCE'),
    classifies sentiment, and returns a JSON-serializable dict (or {"error": ...}).
    Only ever called on-demand from the refresh routes below.
    """
    fno_sym = f"NSE:{name}-EQ"
    try:
        resp = fyers.optionchain({"symbol": fno_sym, "strikecount": 15})
        time.sleep(0.6)

        if not resp or resp.get("s") != "ok":
            err = resp.get("message", str(resp)) if resp else "No response"
            print(f"  ❌ [OI] {name}: Fyers error — {err}")
            return {"error": err}

        data          = resp.get("data", {})
        options_chain = data.get("optionsChain") or data.get("options_chain") or []

        if not options_chain:
            print(f"  ❌ [OI] {name}: Empty option chain from Fyers")
            return {"error": "Empty chain"}

        spot = None
        underlying = data.get("underlyingData") or data.get("ltp_data") or {}
        if isinstance(underlying, dict):
            spot = underlying.get("ltp") or underlying.get("last_price")

        if not spot:
            df_tmp   = pd.DataFrame(options_chain)
            ltp_cols = [c for c in df_tmp.columns if c.lower() in ("ltp", "last", "last_price")]
            sp_cols  = [c for c in df_tmp.columns if "strike" in c.lower() and "price" in c.lower()]
            if sp_cols and ltp_cols:
                df_tmp[sp_cols[0]] = pd.to_numeric(df_tmp[sp_cols[0]], errors="coerce")
                strikes_u = df_tmp[sp_cols[0]].dropna().unique()
                spot      = float(np.median(strikes_u))

        if not spot or spot <= 0:
            df_sp = pd.DataFrame(options_chain)
            for col in df_sp.columns:
                if "strike" in col.lower() and "price" in col.lower():
                    vals = pd.to_numeric(df_sp[col], errors="coerce").dropna()
                    spot = float(vals.median())
                    break

        df = oi_normalize_chain(options_chain)

        if df.empty:
            print(f"  ❌ [OI] {name}: Empty dataframe after normalize")
            return {"error": "Empty after normalize"}

        all_strikes = df["strike_price"].dropna().unique()
        if spot and len(all_strikes):
            atm_check = min(all_strikes, key=lambda s: abs(s - spot))
            if abs(atm_check - spot) / spot > 0.15:
                spot = float(np.median(sorted(all_strikes)))

        result = oi_classify_sentiment(name, df, spot)
        if not result:
            print(f"  ❌ [OI] {name}: Empty CE or PE side in chain")
            return {"error": "Empty CE or PE"}

        result = oi_compute_card_metrics(result)
        result.pop("_df", None)
        result = {k: oi_json_safe(v) for k, v in result.items()}
        result["fetched_at"] = datetime.now(IST).strftime("%H:%M:%S")

        print(f"  📊 [OI] {name}: spot={result.get('spot')} atm={result.get('atm')} "
              f"bias={result.get('bias')} score={result.get('score')} "
              f"category='{result.get('category')}' "
              f"ATM_CE_Δ={result.get('atm_ce_chg'):+,} ATM_PE_Δ={result.get('atm_pe_chg'):+,} "
              f"PCR={result.get('pcr')} (prev {result.get('prev_pcr')})")

        return result

    except Exception as e:
        print(f"  ❌ [OI] {name}: exception — {e}")
        return {"error": str(e)}


def refresh_all_oi_background():
    """Background job for the global 'Refresh OI (All)' button."""
    with oi_refresh_lock:
        oi_refresh_status["running"] = True
        oi_refresh_status["done"]    = 0
        oi_refresh_status["total"]   = len(STOCKS)
        oi_refresh_status["current"] = ""
    print(f"\n{'='*60}")
    print(f"🔄 OI Refresh (All) started — {len(STOCKS)} stocks")
    print(f"{'='*60}")
    try:
        for sym in STOCKS:
            name = sym.split(":")[1].replace("-EQ", "") if ":" in sym else sym
            with oi_refresh_lock:
                oi_refresh_status["current"] = name
            print(f"\n⏳ [{oi_refresh_status['done']+1}/{len(STOCKS)}] Fetching OI for {name}...")
            result = fetch_and_classify_oi(name)
            with oi_cache_lock:
                oi_cache[name] = result
            with oi_refresh_lock:
                oi_refresh_status["done"] += 1
            time.sleep(0.8)
        save_oi_to_file()
        save_oi_to_csv()
        print(f"\n{'='*60}")
        print(f"✅ OI refresh (all) complete for {len(STOCKS)} stocks")
        print(f"{'='*60}\n")
    finally:
        with oi_refresh_lock:
            oi_refresh_status["running"] = False
            oi_refresh_status["current"] = ""

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

def fetch_prev_year_ohlc(symbol):
    current_year = datetime.now().year
    prev_year    = current_year - 1
    data_req = {"symbol": symbol, "resolution": "D", "date_format": "1",
                "range_from": f"{prev_year}-01-01", "range_to": f"{prev_year}-12-31",
                "cont_flag": "1"}
    try:
        response = fyers.history(data_req)
        time.sleep(0.4)
    except Exception:
        return None
    if not response or response.get("s") != "ok" or not response.get("candles"):
        return None
    df = pd.DataFrame(response["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
    for c in ["High","Low","Close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df["High"].max(), df["Low"].min(), float(df["Close"].iloc[-1])

def calc_pivots_1w(symbol):
    result = fetch_prev_year_ohlc(symbol)
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

def fetch_candles_5m(symbol):
    if FIVEM_DATE_MODE == "2":
        start_date, end_date = FIVEM_START_DATE, FIVEM_END_DATE
    else:
        start_date, end_date = get_last_n_trading_days(3)
    data_req = {"symbol": symbol, "resolution": "5", "date_format": "1",
                "range_from": start_date.strftime("%Y-%m-%d"),
                "range_to":   end_date.strftime("%Y-%m-%d"),
                "cont_flag": "1"}
    response = fyers.history(data_req)
    time.sleep(0.4)
    if not response or response.get("s") != "ok" or not response.get("candles"):
        return pd.DataFrame()
    df = pd.DataFrame(response["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
    df["Datetime"] = pd.to_datetime(df["Datetime"], unit="s", utc=True)
    df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
    if FIVEM_DATE_MODE == "2":
        df = filter_trading_hours_manual(df, FIVEM_END_DATE, FIVEM_END_TIME)
    else:
        df = filter_trading_hours(df)
    return df

def fetch_candles_1d(symbol):
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=280)
    data_req = {"symbol": symbol, "resolution": "D", "date_format": "1",
                "range_from": start_dt.strftime("%Y-%m-%d"),
                "range_to":   end_dt.strftime("%Y-%m-%d"),
                "cont_flag": "1"}
    response = fyers.history(data_req)
    time.sleep(0.4)
    if not response or response.get("s") != "ok" or not response.get("candles"):
        return pd.DataFrame()
    df = pd.DataFrame(response["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
    df["Datetime"] = pd.to_datetime(df["Datetime"], unit="s", utc=True)
    df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
    return df.tail(190).reset_index(drop=True)

def fetch_candles_1w(symbol):
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=365)
    data_req = {"symbol": symbol, "resolution": "W", "date_format": "1",
                "range_from": start_dt.strftime("%Y-%m-%d"),
                "range_to":   end_dt.strftime("%Y-%m-%d"),
                "cont_flag": "1"}
    response = fyers.history(data_req)
    time.sleep(0.4)
    if not response or response.get("s") != "ok" or not response.get("candles"):
        return pd.DataFrame()
    df = pd.DataFrame(response["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
    df["Datetime"] = pd.to_datetime(df["Datetime"], unit="s", utc=True)
    df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
    return df.tail(50).reset_index(drop=True)

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

def build_condition_met_flag_1d(symbol, df_labels):
    dates_for_sym = condition_met_map.get(symbol, [])
    dates_set     = set(dates_for_sym)
    flags = []
    for lbl in df_labels:
        try:
            date_str = datetime.strptime(lbl, "%d %b %y").strftime("%Y-%m-%d")
        except Exception:
            date_str = ""
        flags.append(date_str in dates_set)
    return flags, sorted(dates_for_sym, reverse=True)

PIVOT_COLORS = {
    'PP': '#94a3b8',
    'R1': '#4ade80', 'R2': '#22c55e', 'R3': '#16a34a',
    'S1': '#f87171', 'S2': '#ef4444', 'S3': '#dc2626',
}

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

def build_chart_data(symbol, tf):
    if CHART_MODE == "2":
        print(f"⛔ build_chart_data() blocked in Old Charts mode for {symbol} {tf}")
        return {"error": "Old Charts mode — use saved files only"}

    if tf == "5m":
        df        = fetch_candles_5m(symbol)
        label_fmt = "%d %b %H:%M"
    elif tf == "1d":
        df        = fetch_candles_1d(symbol)
        label_fmt = "%d %b %y"
    else:
        df        = fetch_candles_1w(symbol)
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
        pivots = calc_pivots_1w(symbol)
    df = df.reset_index(drop=True)
    candles, ema20, ema10, st_green, st_red, volume, xlabels, xtimes = [], [], [], [], [], [], [], []
    last_st_dir = int(df['ST_dir'].iloc[-1]) if not df.empty else 0
    if tf == "5m":
        today    = df['Datetime'].dt.date.max()
        today_df = df[df['Datetime'].dt.date == today]
        today_open = float(today_df['Open'].iloc[0]) if not today_df.empty else 0
        today_low  = float(today_df['Low'].min())    if not today_df.empty else 0
    else:
        today_open = 0; today_low = 0
    for _, row in df.iterrows():
        xlabels.append(row['Datetime'].strftime(label_fmt))
        xtimes.append(int(row['Datetime'].tz_localize('UTC').timestamp()))
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
    name = symbol.split(":")[1].replace("-EQ","") if ":" in symbol else symbol
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
        condition_met_flag_1d, condition_met_dates = build_condition_met_flag_1d(symbol, xlabels)

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
        "candles": candles, "ema20": ema20, "ema10": ema10,
        "st_green": st_green, "st_red": st_red,
        "volume": volume, "labels": xlabels,"times": xtimes,
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
def fetch_quotes():
    global live_data, last_updated

    if CHART_MODE == "2":
        print("ℹ️  Old Charts mode — fetching end-date quotes from Fyers (one-time).")
        _fetch_end_date_quotes()
        last_updated = f"End date: {_get_end_date_for_fetch()}"
        while True:
            time.sleep(60)

    while True:
        try:
            for sym in STOCKS:
                try:
                    name = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
                    if FIVEM_DATE_MODE == "2":
                        start_date, end_date = FIVEM_START_DATE, FIVEM_END_DATE
                    else:
                        start_date, end_date = get_last_n_trading_days(3)
                    data_req = {
                        "symbol": sym, "resolution": "5", "date_format": "1",
                        "range_from": start_date.strftime("%Y-%m-%d"),
                        "range_to":   end_date.strftime("%Y-%m-%d"),
                        "cont_flag": "1"
                    }
                    resp = fyers.history(data_req)
                    time.sleep(0.4)
                    if not resp or resp.get("s") != "ok" or not resp.get("candles"):
                        continue
                    df = pd.DataFrame(resp["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
                    df["Datetime"] = pd.to_datetime(df["Datetime"], unit="s", utc=True)
                    df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                    for c in ["Open","High","Low","Close","Volume"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
                    df = filter_trading_hours(df)
                    if df.empty: continue
                    if FIVEM_DATE_MODE == "2":
                        current_date = FIVEM_END_DATE
                    else:
                        current_date = df["Datetime"].dt.date.max()
                    current_df = df[df["Datetime"].dt.date == current_date].reset_index(drop=True)
                    prev_dates = sorted([d for d in df["Datetime"].dt.date.unique() if d < current_date])
                    if current_df.empty: continue
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
                    live_data[name] = {
                        "symbol": sym, "name": name,
                        "ltp": round(ltp,2), "open": round(day_open,2),
                        "high": round(day_high,2), "low": round(day_low,2),
                        "prev_close": round(prev_close,2),
                        "change": round(change,2), "pchange": pchange, "volume": volume,
                    }
                except Exception as e:
                    print(f"{name} quote error: {e}")
            last_updated = f"Last 3 Trading Days  {datetime.now(IST).strftime('%d %b %H:%M')}"
        except Exception as e:
            print(f"fetch_quotes error: {e}")
        time.sleep(0.5)

def _fetch_end_date_quotes():
    global live_data, last_updated
    end_date = _get_end_date_for_fetch()
    fetch_start = end_date - timedelta(days=5)
    print(f"📅 Fetching end-date quotes for {end_date} (window from {fetch_start})…")
    for sym in STOCKS:
        try:
            name = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
            data_req = {
                "symbol": sym, "resolution": "5", "date_format": "1",
                "range_from": fetch_start.strftime("%Y-%m-%d"),
                "range_to":   end_date.strftime("%Y-%m-%d"),
                "cont_flag": "1"
            }
            resp = fyers.history(data_req)
            time.sleep(0.4)
            if not resp or resp.get("s") != "ok" or not resp.get("candles"):
                print(f"  ⚠ {name}: no data from Fyers for end date quote")
                continue
            df = pd.DataFrame(resp["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
            df["Datetime"] = pd.to_datetime(df["Datetime"], unit="s", utc=True)
            df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            for c in ["Open","High","Low","Close","Volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
            df = filter_trading_hours(df)
            if df.empty: continue
            end_df = df[df["Datetime"].dt.date == end_date].reset_index(drop=True)
            if end_df.empty:
                print(f"  ⚠ {name}: no candles found for end date {end_date}")
                continue
            ltp      = float(end_df["Close"].iloc[-1])
            day_open = float(end_df["Open"].iloc[0])
            day_high = float(end_df["High"].max())
            day_low  = float(end_df["Low"].min())
            volume   = int(end_df["Volume"].sum())
            prev_dates = sorted([d for d in df["Datetime"].dt.date.unique() if d < end_date])
            if prev_dates:
                prev_df    = df[df["Datetime"].dt.date == prev_dates[-1]].reset_index(drop=True)
                prev_close = float(prev_df["Close"].iloc[-1])
            else:
                prev_close = day_open
            change  = ltp - day_open
            pchange = round((change / day_open) * 100, 2) if day_open else 0
            live_data[name] = {
                "symbol": sym, "name": name,
                "ltp": round(ltp,2), "open": round(day_open,2),
                "high": round(day_high,2), "low": round(day_low,2),
                "prev_close": round(prev_close,2),
                "change": round(change,2), "pchange": pchange, "volume": volume,
            }
            print(f"  ✅ {name}: LTP={ltp} O={day_open} %chg={pchange}%")
            with chart_cache_lock:
                cached_5m = chart_cache["5m"].get(name)
                if cached_5m and "error" not in cached_5m:
                    cached_5m["end_date_ltp"]     = round(ltp, 2)
                    cached_5m["end_date_open"]     = round(day_open, 2)
                    cached_5m["end_date_high"]     = round(day_high, 2)
                    cached_5m["end_date_low"]      = round(day_low, 2)
                    cached_5m["end_date_pchange"]  = pchange
        except Exception as e:
            print(f"  ❌ {name} end-date quote error: {e}")
    last_updated = f"End Date: {end_date}"
    print(f"✅ End-date quotes loaded for {len(live_data)} stocks")

# ── preloading ────────────────────────────────────────────────
TF_LABEL    = {"5m": "5min", "1d": "1D", "1w": "1W"}
RETRY_WAIT  = 10
MAX_RETRIES = 2

def fetch_with_retry(sym, tf, label, context=""):
    name = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            data = build_chart_data(sym, tf)
        except Exception as e:
            data = {"error": str(e)}
        if "error" not in data:
            return name, data
        ctx = f" {context}" if context else ""
        if attempt <= MAX_RETRIES:
            print(f"{name} {label} ERROR: {data['error']} — retrying in {RETRY_WAIT}s "
                  f"(attempt {attempt}/{MAX_RETRIES+1}){ctx}")
            time.sleep(RETRY_WAIT)
        else:
            print(f"{name} {label} ERROR: gave up after {MAX_RETRIES+1} attempts{ctx}")
    return name, data

def _build_drop_details_log():
    from collections import OrderedDict
    lines = [f"# DropDetails log — {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}\n"]
    no_upwick_by_day = {0: [], 1: [], 2: [], 3: []}
    skipped = []
    with chart_cache_lock:
        cache_5m = dict(chart_cache["5m"])
    for sym in STOCKS:
        name = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
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
    for sym in STOCKS:
        name, data = fetch_with_retry(sym, tf, label)
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
        for sym in STOCKS:
            name       = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
            _json_path = os.path.join(_dir, f"{name}.json")
            if os.path.exists(_json_path):
                try:
                    with open(_json_path, "r") as _f:
                        data = json.load(_f)

                    # ── Backward-compat: older saved JSON files (from before
                    # lightweight-charts was added) only have "labels" (display
                    # strings), not "times" (unix timestamps). lightweight-charts
                    # needs "times" to plot anything, so synthesize placeholder
                    # timestamps here if they're missing. These charts will still
                    # render (just with even spacing instead of true time gaps)
                    # until the user hits ↻ to regenerate with real timestamps.
                    if "times" not in data and "labels" in data:
                        n = len(data["labels"])
                        step = 300 if tf == "5m" else 86400  # seconds
                        data["times"] = [i * step for i in range(n)]

                    if tf == "5m" and "candles" in data and "labels" in data:
                        sc_flag = detect_small_candles(data["candles"], data["labels"])
                        sc_recent = -1
                        for _si in range(len(sc_flag)-1, -1, -1):
                            if sc_flag[_si]: sc_recent = _si; break
                        data["small_candle_flag"]       = sc_flag
                        data["small_candle_recent_idx"] = sc_recent
                    if tf == "1d" and "labels" in data:
                        cm_flag, cm_dates = build_condition_met_flag_1d(sym, data["labels"])
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

def regenerate_tf(tf):
    print(f"🔄 Manual {tf} regeneration started")
    for sym in STOCKS:
        name = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
        try:
            data = _build_chart_data_forced(sym, tf)
        except Exception as e:
            data = {"error": str(e)}
        with chart_cache_lock:
            chart_cache[tf][name] = data
        if "error" not in data:
            with refresh_ts_lock:
                refresh_ts[f"{name}_{tf}"] = time.time()
            print(f"✅ {name} {tf} regenerated")
    print(f"✅ Manual {tf} regeneration completed")

def _build_chart_data_forced(symbol, tf):
    end_date_ref = _get_end_date_for_fetch()

    if tf == "5m":
        if CHART_MODE == "2":
            fetch_end   = end_date_ref
            _trading = []
            _d = fetch_end
            while len(_trading) < 3:
                if _d.weekday() < 5:
                    _trading.append(_d)
                _d -= timedelta(days=1)
            fetch_start = _trading[-1]
        else:
            if FIVEM_DATE_MODE == "2":
                fetch_start, fetch_end = FIVEM_START_DATE, FIVEM_END_DATE
            else:
                fetch_start, fetch_end = get_last_n_trading_days(3)
        data_req = {"symbol": symbol, "resolution": "5", "date_format": "1",
                    "range_from": fetch_start.strftime("%Y-%m-%d"),
                    "range_to":   fetch_end.strftime("%Y-%m-%d"),
                    "cont_flag": "1"}
        response = fyers.history(data_req)
        time.sleep(0.4)
        if not response or response.get("s") != "ok" or not response.get("candles"):
            return {"error": "No data"}
        df = pd.DataFrame(response["candles"], columns=["Datetime","Open","High","Low","Close","Volume"])
        df["Datetime"] = pd.to_datetime(df["Datetime"], unit="s", utc=True)
        df["Datetime"] = df["Datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        for c in ["Open","High","Low","Close","Volume"]:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)
        df = filter_trading_hours(df)
        label_fmt = "%d %b %H:%M"
    elif tf == "1d":
        df = fetch_candles_1d(symbol)
        label_fmt = "%d %b %y"
    else:
        df = fetch_candles_1w(symbol)
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
        pivots = calc_pivots_1w(symbol)
    df = df.reset_index(drop=True)
    candles, ema20, ema10, st_green, st_red, volume, xlabels, xtimes = [], [], [], [], [], [], [], []
    last_st_dir = int(df['ST_dir'].iloc[-1]) if not df.empty else 0
    if tf == "5m":
        today    = df['Datetime'].dt.date.max()
        today_df = df[df['Datetime'].dt.date == today]
        today_open = float(today_df['Open'].iloc[0]) if not today_df.empty else 0
        today_low  = float(today_df['Low'].min())    if not today_df.empty else 0
    else:
        today_open = 0; today_low = 0
    for _, row in df.iterrows():
        xlabels.append(row['Datetime'].strftime(label_fmt))
        xtimes.append(int(pd.Timestamp(row['Datetime']).tz_localize('UTC').timestamp()))
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
    name = symbol.split(":")[1].replace("-EQ","") if ":" in symbol else symbol
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
        condition_met_flag_1d, condition_met_dates = build_condition_met_flag_1d(symbol, xlabels)

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
        "candles": candles, "ema20": ema20, "ema10": ema10,
        "st_green": st_green, "st_red": st_red,
        "volume": volume, "labels": xlabels,"times": xtimes,
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

# ── Flask ─────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/api/chart_mode")
def api_chart_mode():
    return jsonify({"mode": CHART_MODE})

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
        fy_symbol = None
        for sym in STOCKS:
            n = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
            if n == name:
                fy_symbol = sym; break
        if not fy_symbol:
            return jsonify({"success": False})
        chart_data = _build_chart_data_forced(fy_symbol, tf)
        with chart_cache_lock:
            chart_cache[tf][name] = chart_data
        with refresh_ts_lock:
            refresh_ts[f"{name}_{tf}"] = time.time()
        if tf == "5m" and "end_date_ltp" in chart_data and chart_data["end_date_ltp"] is not None:
            live_data[name] = {
                "symbol": fy_symbol, "name": name,
                "ltp": chart_data["end_date_ltp"],
                "open": chart_data.get("end_date_open", 0),
                "high": chart_data.get("end_date_high", 0),
                "low": chart_data.get("end_date_low", 0),
                "prev_close": chart_data.get("end_date_open", 0),
                "change": round(chart_data["end_date_ltp"] - chart_data.get("end_date_open", 0), 2),
                "pchange": chart_data.get("end_date_pchange", 0),
                "volume": sum(v["y"] for v in chart_data.get("volume", [])),
            }
        print(f"✅ {name} {tf} manually refreshed")
        return jsonify({"success": True})
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
    names = [s.split(":")[1].replace("-EQ","") if ":" in s else s for s in STOCKS]
    return render_template_string(HTML, stocks=names, chart_mode=CHART_MODE)

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
    names = [s.split(":")[1].replace("-EQ","") if ":" in s else s for s in STOCKS]
    return jsonify({
        "total": len(STOCKS),
        "ready_5m": len(ready_5m), "ready_1d": len(ready_1d), "ready_1w": len(ready_1w),
        "all_names": names, "preload_done": preload_done,
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
    for sym in STOCKS:
        name = sym.split(":")[1].replace("-EQ","") if ":" in sym else sym
        data = cache_1d.get(name)
        if data and "condition_met_dates" in data and data["condition_met_dates"]:
            result[name] = data["condition_met_dates"][0]
        else:
            dates = condition_met_map.get(sym, [])
            result[name] = sorted(dates, reverse=True)[0] if dates else ""
    return jsonify(result)

@app.route("/api/fundamentals")
def api_fundamentals():
    """Read-only lookup — returns cached fundamentals if present, otherwise
    tells the frontend nothing has been fetched yet. Does NOT trigger any
    background fetch on its own; use the ↻ Fund button (single) or
    'Refresh Fundamentals (All)' button (global) to actually fetch."""
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
    """Force a fresh fundamentals fetch for ONE stock (via its own ↻ Fund button)."""
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
    """Kick off a background refresh of fundamentals for ALL stocks
    (via the global 'Refresh Fundamentals (All)' button). Renders
    progressively — each stock is available in the cache as soon as
    its own fetch completes, without waiting for the rest."""
    with fundamentals_refresh_lock:
        if fundamentals_refresh_status["running"]:
            return jsonify({"success": False, "message": "Already running"})
    threading.Thread(target=refresh_all_fundamentals_background, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/fundamentals_refresh_status")
def api_fundamentals_refresh_status():
    with fundamentals_refresh_lock:
        return jsonify(dict(fundamentals_refresh_status))

@app.route("/api/oi")
def api_oi():
    name = request.args.get("symbol", "")
    if not name:
        return jsonify({"error": "no symbol"})
    with oi_cache_lock:
        data = oi_cache.get(name)
    if data is None:
        return jsonify({"not_fetched": True})
    return jsonify(data)

@app.route("/api/oi_all")
def api_oi_all():
    with oi_cache_lock:
        data = dict(oi_cache)
    return jsonify(data)

@app.route("/api/oi_refresh_single", methods=["POST"])
def api_oi_refresh_single():
    payload = request.get_json()
    name = payload.get("symbol", "") if payload else ""
    if not name:
        return jsonify({"success": False})
    print(f"\n🔄 [OI] Single refresh requested: {name}")
    result = fetch_and_classify_oi(name)
    with oi_cache_lock:
        oi_cache[name] = result
    save_oi_to_file()
    save_oi_to_csv()
    return jsonify({"success": "error" not in result, "data": result})

@app.route("/api/oi_refresh_all", methods=["POST"])
def api_oi_refresh_all():
    with oi_refresh_lock:
        if oi_refresh_status["running"]:
            return jsonify({"success": False, "message": "Already running"})
    threading.Thread(target=refresh_all_oi_background, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/oi_refresh_status")
def api_oi_refresh_status():
    with oi_refresh_lock:
        return jsonify(dict(oi_refresh_status))

@app.route("/api/news/<symbol>")
def api_news(symbol):
    """Return cached news for a stock. Triggers a fresh fetch if not in cache
    (only happens when the user expands the news dropdown for that stock)."""
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
    """Force a fresh news fetch for a single stock (via its own ↻ button)."""
    def _bg():
        items = fetch_tv_news(symbol, max_items=10)
        with news_cache_lock:
            news_cache[symbol] = items
        save_news_to_file()
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/news_refresh_all", methods=["POST"])
def api_news_refresh_all():
    """Kick off a background refresh of news for ALL stocks (via the
    global 'Refresh News (All)' button). Renders progressively — each
    stock's news is available in the cache as soon as its own fetch
    completes, without waiting for the rest."""
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
#  HTML Main  (interactive TradingView-style charts via lightweight-charts,
#              all original OI / Fundamentals / News / Sort features kept)
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
    --bg:#f0f2f5; --surface:#ffffff; --border:#dde1ea;
    --accent:#2563eb; --accent2:#059669; --sell:#dc2626;
    --text:#1e293b; --muted:#64748b; --green:#16a34a; --red:#dc2626; --yellow:#d97706;
    --font:'Inter','Segoe UI',system-ui,sans-serif;
    --info-w:200px; --row-sep:#e2e8ef;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html,body { height:100%; background:var(--bg); color:var(--text); font-family:var(--font); font-size:13px; }
  #topbar {
    position:sticky; top:0; z-index:200; background:var(--surface);
    border-bottom:1px solid var(--border); padding:7px 14px;
    display:flex; align-items:center; gap:10px; flex-wrap:wrap;
    box-shadow:0 1px 4px rgba(0,0,0,.07); min-width:max-content;
  }
  #topbar .brand { font-size:15px; font-weight:700; color:var(--accent); letter-spacing:.5px; }
  #topbar .sep   { width:1px; height:20px; background:var(--border); }
  .mode-badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:10px; }
  .mode-old { background:#fef3c7; color:#92400e; border:1px solid #fde68a; }
  .mode-new { background:#dcfce7; color:#14532d; border:1px solid #86efac; }
  .sort-wrap { display:flex; align-items:center; gap:6px; }
  .sort-wrap label { color:var(--muted); font-size:11px; white-space:nowrap; }
  #sortSelect { background:#f8f9fb; color:var(--text); border:1px solid var(--border);
    border-radius:6px; padding:4px 8px; font-size:12px; cursor:pointer; outline:none; }
  .btn { display:inline-flex; align-items:center; gap:5px; padding:5px 11px; border-radius:6px;
    font-size:12px; font-weight:500; border:1px solid transparent; cursor:pointer;
    transition:all .15s; white-space:nowrap; }
  .btn-5m  { background:#eff6ff; color:var(--accent);  border-color:#bfdbfe; }
  .btn-1d  { background:#f0fdf4; color:var(--accent2); border-color:#bbf7d0; }
  .btn-1w  { background:#fffbeb; color:var(--yellow);  border-color:#fde68a; }
  .btn-oi  { background:#f5f3ff; color:#7c3aed;        border-color:#ddd6fe; }
  .btn-fund { background:#ecfeff; color:#0e7490;       border-color:#a5f3fc; }
  .btn-news { background:#fdf2f8; color:#be185d;       border-color:#fbcfe8; }
  .btn:hover { opacity:.8; transform:translateY(-1px); }
  .btn:active { transform:translateY(0); opacity:1; }
  .btn.loading { opacity:.5; pointer-events:none; }
  .btn-sm { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px;
    font-size:10px; font-weight:600; border:1px solid var(--border); background:#f8f9fb;
    color:var(--muted); cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn-sm:hover { border-color:var(--accent); color:var(--accent); background:#eff6ff; }
  .btn-sm.loading { opacity:.45; pointer-events:none; }
  #statusBadge { margin-left:auto; font-size:11px; color:var(--muted); white-space:nowrap; }
  #sizeBar {
    background:#f8f9fb; border-bottom:1px solid var(--border); padding:5px 14px;
    display:flex; align-items:center; gap:16px; flex-wrap:wrap;
    min-width:max-content; position:sticky; top:44px; z-index:190;
  }
  .tf-size-group { display:flex; align-items:center; gap:8px; padding:3px 9px;
    background:var(--surface); border:1px solid var(--border); border-radius:7px; }
  .tf-size-label { font-size:11px; font-weight:700; color:var(--accent); min-width:20px; text-align:center; }
  .size-ctrl { display:flex; align-items:center; gap:4px; }
  .size-ctrl label { color:var(--muted); font-size:10px; white-space:nowrap; }
  .step-btn { width:19px; height:19px; border-radius:4px; border:1px solid var(--border);
    background:#f8f9fb; color:var(--text); font-size:13px; line-height:1; cursor:pointer;
    display:flex; align-items:center; justify-content:center; font-weight:700; transition:all .12s; }
  .step-btn:hover { border-color:var(--accent); color:var(--accent); background:#eff6ff; }
  .size-val { font-size:10px; color:var(--text); min-width:34px; text-align:center;
    background:#f8f9fb; border:1px solid var(--border); border-radius:4px; padding:1px 3px; }
  #loadingOverlay { position:fixed; inset:0; background:rgba(240,242,245,.92);
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    z-index:999; gap:12px; }
  #loadingOverlay.hidden { display:none; }
  .spinner { width:36px; height:36px; border:3px solid var(--border);
    border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #loadMsg  { color:var(--muted); font-size:13px; }
  #loadProg { color:var(--accent); font-size:12px; }
  #colHeader { display:flex; align-items:stretch; background:#f0f2f5;
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
  .stock-row:hover { background:#f8faff; }
  .info-strip { flex:0 0 var(--info-w); min-width:var(--info-w); padding:10px 10px 8px 12px;
    border-right:2px solid var(--border); display:flex; flex-direction:column; gap:3px;
    justify-content:flex-start; background:#fafbfc; position:sticky; left:0; z-index:10; }
  .info-name { font-size:14px; font-weight:700; color:var(--accent); letter-spacing:.3px; }
  .info-ltp  { font-size:13px; font-weight:600; color:var(--text); }
  .info-chg  { font-size:11px; font-weight:600; padding:2px 6px; border-radius:4px;
    display:inline-block; width:fit-content; }
  .chg-up { background:#dcfce7; color:var(--green); }
  .chg-dn { background:#fee2e2; color:var(--red); }
  .info-vol { font-size:9.5px; color:var(--muted); }
  /* ── External links ── */
  .stock-links { display:flex; gap:5px; margin-top:4px; flex-wrap:wrap; }
  .stock-link {
    display:inline-flex; align-items:center; gap:3px;
    font-size:9px; font-weight:600; padding:2px 6px; border-radius:4px;
    text-decoration:none; border:1px solid; transition:all .15s; white-space:nowrap;
  }
  .stock-link:hover { opacity:.75; transform:translateY(-1px); }
  .link-screener { color:#0f766e; background:#f0fdfa; border-color:#99f6e4; }
  .link-tv       { color:#1d4ed8; background:#eff6ff; border-color:#bfdbfe; }
  /* ── Fundamentals ── */

  .signal-Strong   { background:#dcfce7; color:#15803d; border:1px solid #86efac; }
  .signal-Moderate { background:#fef9c3; color:#854d0e; border:1px solid #fde047; }
  .signal-Weak     { background:#ffedd5; color:#9a3412; border:1px solid #fdba74; }
  .signal-Avoid    { background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; }


  .cond-badge { font-size:9px; font-weight:600; padding:1px 6px; border-radius:8px;
    background:#fef3c7; color:var(--yellow); border:1px solid #fde68a;
    display:none; width:fit-content; }

  /* ── Fundamentals / OI section headers (shared style) ── */
  .oi-header {
    display:flex; align-items:center; justify-content:space-between;
    margin-top:5px; border-top:1px solid var(--border); padding-top:4px;
  }
  .oi-header-label {
    font-size:9px; font-weight:700; color:#7c3aed; letter-spacing:.5px; text-transform:uppercase;
  }
  .fund-header-label {
    font-size:9px; font-weight:700; color:#0e7490; letter-spacing:.5px; text-transform:uppercase;
  }

  /* ── News section ── */
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
    background:#f8f9fb; border:1px solid var(--border);
    transition:background .12s;
  }
  .news-item:hover { background:#eff6ff; border-color:#bfdbfe; }
  .news-meta { display:flex; align-items:center; gap:4px; }
  .news-time { font-size:8.5px; color:var(--muted); white-space:nowrap; font-weight:600; }
  .news-provider { font-size:8px; color:#7c3aed; background:#f5f3ff;
    border:1px solid #ddd6fe; border-radius:3px; padding:0 4px; font-weight:600; white-space:nowrap; }
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
  /* ── Charts ── */
  .chart-cell { flex:0 0 auto; border-right:1px solid var(--border); padding:6px 6px 4px;
    display:flex; flex-direction:column; gap:0; background:#fff; }
  .chart-cell:last-child { border-right:none; }
  .cell-label { font-size:9.5px; font-weight:600; letter-spacing:.8px; color:var(--muted);
    text-transform:uppercase; display:flex; align-items:center; justify-content:space-between;
    margin-bottom:3px; gap:4px; }
  .st-dir { font-size:9px; padding:1px 5px; border-radius:3px; font-weight:700; }
  .st-bull { background:#dcfce7; color:var(--green); }
  .st-bear { background:#fee2e2; color:var(--red); }
  .chart-wrap { border:1px solid var(--border); border-radius:4px; overflow:hidden; }
  .chart-pending { display:flex; align-items:center; justify-content:center;
    color:var(--muted); font-size:11px; font-style:italic;
    background:#f8f9fb; border:1px dashed var(--border); border-radius:4px; }
</style>
</head>
<body>

<div id="loadingOverlay">
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
      <option value="noupwick1">NoUpWick1 (Today 9:15 first)</option>
      <option value="noupwick2">NoUpWick2 (Prev Day 9:15 first)</option>
      <option value="noupwick3">NoUpWick3 (Prev-Prev Day 9:15 first)</option>
      <option value="oi_cat">OI Category: Bullish → Bearish</option>
      <option value="atm_ce_asc">ATM CE Δ: Smallest → Largest</option>
      <option value="atm_pe_desc">ATM PE Δ: Largest → Smallest</option>
      <option value="fund_score_desc">Fundamentals: Strong → Avoid</option>
    </select>
  </div>
  <div class="sep"></div>
  <button class="btn btn-5m" id="btn5m" onclick="refreshTF('5m')">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    Refresh 5m
  </button>
  <button class="btn btn-1d" id="btn1d" onclick="refreshTF('1d')">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    Refresh 1D
  </button>
  <button class="btn btn-1w" id="btn1w" onclick="refreshTF('1w')">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    Refresh 1W
  </button>
  <button class="btn btn-oi" id="btnOI" onclick="refreshAllOI()">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    Refresh OI (All)
  </button>
  <button class="btn btn-fund" id="btnFundAll" onclick="refreshAllFundamentals()">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    Refresh Fundamentals (All)
  </button>
  <button class="btn btn-news" id="btnNewsAll" onclick="refreshAllNews()">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
    Refresh News (All)
  </button>
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
      <span class="size-val" id="val-5m-h">300px</span>
      <button class="step-btn" onclick="stepSize('5m','h',30)">+</button></div>
    <button class="btn-sm" onclick="applyAll('5m')">Apply to all 5m</button>
  </div>
  <div class="tf-size-group">
    <span class="tf-size-label" style="color:#059669">1D</span>
    <div class="size-ctrl"><label>Canvas Width</label>
      <button class="step-btn" onclick="stepSize('1d','width',-40)">−</button>
      <span class="size-val" id="val-1d-width">500px</span>
      <button class="step-btn" onclick="stepSize('1d','width',40)">+</button></div>
    <div class="size-ctrl"><label>Canvas Height</label>
      <button class="step-btn" onclick="stepSize('1d','h',-30)">−</button>
      <span class="size-val" id="val-1d-h">300px</span>
      <button class="step-btn" onclick="stepSize('1d','h',30)">+</button></div>
    <button class="btn-sm" onclick="applyAll('1d')">Apply to all 1D</button>
  </div>
  <div class="tf-size-group">
    <span class="tf-size-label" style="color:#d97706">1W</span>
    <div class="size-ctrl"><label>Canvas Width</label>
      <button class="step-btn" onclick="stepSize('1w','width',-40)">−</button>
      <span class="size-val" id="val-1w-width">500px</span>
      <button class="step-btn" onclick="stepSize('1w','width',40)">+</button></div>
    <div class="size-ctrl"><label>Canvas Height</label>
      <button class="step-btn" onclick="stepSize('1w','h',-30)">−</button>
      <span class="size-val" id="val-1w-h">300px</span>
      <button class="step-btn" onclick="stepSize('1w','h',30)">+</button></div>
    <button class="btn-sm" onclick="applyAll('1w')">Apply to all 1W</button>
  </div>
  <span style="font-size:10px;color:var(--muted)">Tip: use Canvas Width/Height to resize the chart area — all candles for that timeframe will auto-fit inside it. You can still scroll / pinch / drag on any chart to zoom & pan further.</span>
</div>

<div id="colHeader">
  <div class="ch-info">Stock</div>
  <div class="ch-tf" style="width:520px">5 Min · 3 Days</div>
  <div class="ch-tf" style="width:520px">1 Day · 6 Mo</div>
  <div class="ch-tf" style="width:520px">1 Week · 1 Yr</div>
</div>

<div id="stockTable"></div>

<script>
const STOCKS      = {{ stocks | tojson }};
const CHART_MODE  = "{{ chart_mode }}";
const TF_LIST     = ['5m','1d','1w'];

const quotesMap  = {};
const condDates  = {};
const chartData  = {};
const fundMap    = {};
const oiMap      = {};
let   sortMode   = 'default';
const chartReg   = {}; // `${name}_${tf}` -> {chart, candleSeries, volSeries, ...}

// sizeState controls the actual chart CANVAS dimensions (width/height).
// Bar spacing is no longer set manually — after every render/resize we call
// chart.timeScale().fitContent() so all candles for that timeframe always
// fit and are visible inside whatever canvas size you choose. Widening the
// canvas (e.g. for 1D, which has more data) simply gives each candle more
// room instead of cutting candles off or requiring scrolling.
const sizeState = {
  '5m': { width:500, h:300 },
  '1d': { width:500, h:300 },
  '1w': { width:500, h:300 },
};

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
    if (!reg) continue;
    reg.chart.resize(sizeState[tf].width, sizeState[tf].h);
    reg.chart.timeScale().fitContent();
  }
  setStatus(`Applied canvas size to all ${tf} charts`);
}

// ── Build external links from stock name ──────────────────────
function makeStockLinks(name) {
  const screenerUrl = `https://www.screener.in/company/${name}/consolidated/`;
  const tvUrl       = `https://in.tradingview.com/symbols/NSE-${name}/news/`;
  return `
    <div class="stock-links">
      <a class="stock-link link-screener" href="${screenerUrl}" target="_blank" rel="noopener">
        📊 Screener
      </a>
      <a class="stock-link link-tv" href="${tvUrl}" target="_blank" rel="noopener">
        📈 TV News
      </a>
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
  // Lazy-fetch: only fetch when first opened
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
      if (attempt < 20) {
        setTimeout(() => fetchNewsForStock(name), 3000);
      } else {
        if (loadEl) loadEl.textContent = 'News unavailable.';
      }
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
  // Remove old items
  list.querySelectorAll('.news-item').forEach(el => el.remove());
  if (!items || items.length === 0) {
    if (loadEl) { loadEl.textContent = 'No news found.'; loadEl.style.display = 'block'; }
    return;
  }
  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'news-item';
    const provHtml = item.provider
      ? `<span class="news-provider">${item.provider}</span>` : '';
    const timeHtml = item.timestamp_ist
      ? `<span class="news-time">${item.timestamp_ist}</span>` : '';
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

// ── Global "Refresh News (All)" button ─────────────────────────
let newsAllPolling = false;
async function refreshAllNews() {
  const btn = document.getElementById('btnNewsAll');
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/news_refresh_all', { method:'POST' });
    const d = await r.json();
    if (d.success === false) {
      setStatus(d.message || 'News refresh already running');
    } else {
      setStatus('News refresh started for all stocks…');
    }
    if (!newsAllPolling) { newsAllPolling = true; pollNewsRefreshStatus(); }
  } catch(e) { setStatus('News refresh error'); btn.classList.remove('loading'); }
}

async function pollNewsRefreshStatus() {
  try {
    const r = await fetch('/api/news_refresh_status');
    const d = await r.json();
    if (d.running) {
      setStatus(`News refresh: ${d.done}/${d.total}${d.current ? ' — ' + d.current : ''}`);
      // Progressive rendering: if a stock's news dropdown is currently open,
      // refresh it in place as soon as new items land in the cache.
      document.querySelectorAll('.news-list.visible').forEach(list => {
        const name = list.id.replace('news-list-', '');
        fetchNewsForStock(name);
      });
      setTimeout(pollNewsRefreshStatus, 2000);
    } else {
      setStatus('News refresh complete ✓');
      document.getElementById('btnNewsAll').classList.remove('loading');
      document.querySelectorAll('.news-list.visible').forEach(list => {
        const name = list.id.replace('news-list-', '');
        fetchNewsForStock(name);
      });
      newsAllPolling = false;
    }
  } catch(e) {
    newsAllPolling = false;
    document.getElementById('btnNewsAll').classList.remove('loading');
  }
}

// ── OI Sentiment rendering (on-demand only) ────────────────────
// Field mapping matches the compact OI_summary.csv column layout
// produced by the #modify #condition Main1 script:
//   bias, bias_emoji, category, score, confidence, pcr, prev_pcr,
//   support, resistance, ce_net_contracts, pe_net_contracts,
//   ce_net_lakhs, pe_net_lakhs, atm_ce_delta_contracts,
//   atm_pe_delta_contracts, score_breakdown
// A freshly-fetched result (from fetch_and_classify_oi) carries the
// richer field names (full_ce_chg, ce_oi_chg, score_breakdown list,
// etc.) — this renderer accepts either shape.
function renderOI(name, d) {
  oiMap[name] = d;
  const el = document.getElementById(`oi-unified-${name}`);
  if (!el) return;
  if (!d || d.not_fetched) {
    el.innerHTML = `<div class="news-loading">Not fetched — click ↻ OI</div>`;
    return;
  }
  if (d.error) {
    el.innerHTML = `<div class="news-loading">Error: ${d.error}</div>`;
    return;
  }
  const bias = d.bias || '—';
  const biasColor = {
    'Bullish':'#15803d', 'Bullish Lean':'#15803d',
    'Bearish':'#991b1b', 'Bearish Lean':'#991b1b',
    'Neutral':'#64748b',
  }[bias] || '#64748b';
  const biasBg = {
    'Bullish':'#dcfce7', 'Bullish Lean':'#dcfce7',
    'Bearish':'#fee2e2', 'Bearish Lean':'#fee2e2',
    'Neutral':'#f1f5f9',
  }[bias] || '#f1f5f9';

  function row(label, val) {
    if (val == null || val === '') return '';
    return `<div style="display:flex;gap:3px;align-items:baseline;font-size:9px;line-height:1.5">
      <span style="color:#64748b;font-weight:600;min-width:56px;flex-shrink:0">${label}</span>
      <span style="color:#1e293b;font-weight:500;word-break:break-word">${val}</span>
    </div>`;
  }

  // CE/PE net flow in lakhs (full chain) — use ce_net_lakhs/pe_net_lakhs
  // if present (CSV-loaded), else derive from full_ce_chg/full_pe_chg
  // (fresh fetch).
  const ceLakhs = (d.ce_net_lakhs != null && d.ce_net_lakhs !== '') ? Number(d.ce_net_lakhs)
                    : (d.full_ce_chg != null ? Math.round((d.full_ce_chg / 100000) * 100) / 100 : null);
  const peLakhs = (d.pe_net_lakhs != null && d.pe_net_lakhs !== '') ? Number(d.pe_net_lakhs)
                    : (d.full_pe_chg != null ? Math.round((d.full_pe_chg / 100000) * 100) / 100 : null);

  function colorNum(val, text) {
    if (val == null) return text;
    const color = val > 0 ? '#16a34a' : (val < 0 ? '#dc2626' : '#1e293b');
    return `<span style="color:${color};font-weight:700">${text}</span>`;
  }

  const ceDelta = (d.ce_oi_chg != null && d.ce_oi_chg !== '')
    ? colorNum(Number(d.ce_oi_chg), `${d.ce_oi_chg > 0 ? '+' : ''}${Number(d.ce_oi_chg).toLocaleString()}`)
    : null;
  const peDelta = (d.pe_oi_chg != null && d.pe_oi_chg !== '')
    ? colorNum(Number(d.pe_oi_chg), `${d.pe_oi_chg > 0 ? '+' : ''}${Number(d.pe_oi_chg).toLocaleString()}`)
    : null;
  const scoreStr = (d.score != null && d.score !== '') ? `${d.score > 0 ? '+' : ''}${d.score}` : null;
  const confStr  = (d.confidence != null && d.confidence !== '') ? `${d.confidence}%` : null;
  const pcrStr   = (d.pcr != null && d.pcr !== '')
                   ? `${d.pcr}${(d.prev_pcr != null && d.prev_pcr !== '') ? ` (prev ${d.prev_pcr})` : ''}`
                   : null;
  const supportStr    = (d.support    != null && d.support    !== '') ? Number(d.support).toFixed(0)    : null;
  const resistanceStr = (d.resistance != null && d.resistance !== '') ? Number(d.resistance).toFixed(0) : null;
  const ceContracts = (d.full_ce_chg != null && d.full_ce_chg !== '') ? Number(d.full_ce_chg) : null;
  const peContracts = (d.full_pe_chg != null && d.full_pe_chg !== '') ? Number(d.full_pe_chg) : null;

  const ceNetStr = ceContracts != null
    ? colorNum(ceContracts, `${ceContracts > 0 ? '+' : ''}${ceContracts.toLocaleString()}`)
    : null;
  const peNetStr = peContracts != null
    ? colorNum(peContracts, `${peContracts > 0 ? '+' : ''}${peContracts.toLocaleString()}`)
    : null;
  // Breakdown: list of {points,label} (fresh fetch) or pre-formatted
  // pipe-joined text (CSV load, matches Main1's "score_breakdown" column)
  let breakdownHtml = '';
  if (Array.isArray(d.score_breakdown) && d.score_breakdown.length) {
    breakdownHtml = d.score_breakdown.map(b =>
      `<div style="font-size:8.5px;color:#475569">${b.points > 0 ? '+' : ''}${b.points}  ${b.label}</div>`
    ).join('');
  } else if (typeof d.score_breakdown === 'string' && d.score_breakdown) {
    breakdownHtml = d.score_breakdown.split(' | ').map(p =>
      `<div style="font-size:8.5px;color:#475569">${p}</div>`
    ).join('');
  } else if (d.score_breakdown_text) {
    breakdownHtml = d.score_breakdown_text.split(' | ').map(p =>
      `<div style="font-size:8.5px;color:#475569">${p}</div>`
    ).join('');
  }

  el.innerHTML = `
    <div style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;
      background:${biasBg};color:${biasColor};border:1px solid ${biasColor}40;
      display:inline-block;margin-bottom:3px">
      ${d.bias_emoji || ''} ${bias}
    </div>
    ${row('Category', d.category || null)}
    ${row('Score', scoreStr)}
    ${row('CE Net', ceNetStr)}
    ${row('PE Net', peNetStr)}
    ${row('ATM CE Δ', ceDelta)}
    ${row('ATM PE Δ', peDelta)}
    ${d.fetched_at ? `<div style="font-size:8px;color:#94a3b8;margin-top:2px">Updated ${d.fetched_at}</div>` : ''}
  `;
}

async function fetchAllOI() {
  try {
    const r = await fetch('/api/oi_all');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) renderOI(name, data);
  } catch(e) {}
}

async function refreshSingleOI(name, btn) {
  btn.classList.add('loading'); btn.textContent = '…';
  try {
    const r = await fetch('/api/oi_refresh_single', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ symbol:name })
    });
    const d = await r.json();
    renderOI(name, d.data || {error:'refresh failed'});
  } catch(e) {
    renderOI(name, {error:'refresh error'});
  }
  btn.classList.remove('loading'); btn.textContent = '↻ OI';
}

let oiPolling = false;
async function refreshAllOI() {
  const btn = document.getElementById('btnOI');
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/oi_refresh_all', { method:'POST' });
    const d = await r.json();
    if (d.success === false) {
      setStatus(d.message || 'OI refresh already running');
    } else {
      setStatus('OI refresh started for all stocks…');
    }
    if (!oiPolling) { oiPolling = true; pollOIRefreshStatus(); }
  } catch(e) { setStatus('OI refresh error'); btn.classList.remove('loading'); }
}

async function pollOIRefreshStatus() {
  try {
    const r = await fetch('/api/oi_refresh_status');
    const d = await r.json();
    await fetchAllOI();
    if (d.running) {
      setStatus(`OI refresh: ${d.done}/${d.total}${d.current ? ' — ' + d.current : ''}`);
      setTimeout(pollOIRefreshStatus, 2000);
    } else {
      setStatus('OI refresh complete ✓');
      document.getElementById('btnOI').classList.remove('loading');
      oiPolling = false;
    }
  } catch(e) {
    oiPolling = false;
    document.getElementById('btnOI').classList.remove('loading');
  }
}

// ── Fundamentals: render whatever is cached on disk. No automatic
//    per-stock fetch fallback — fetching only happens via ↻ Fund
//    (single stock) or "Refresh Fundamentals (All)" (global). ──────
async function fetchAllFundamentals() {
  try {
    const r = await fetch('/api/fundamentals_all');
    const d = await r.json();
    for (const [name, data] of Object.entries(d)) { if (!data.error) renderFundamentals(name, data); }
  } catch(e) {}
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
    if (d.data && !d.data.error) {
      renderFundamentals(name, d.data);
    } else if (el) {
      el.innerHTML = `<div class="news-loading">Error: ${(d.data && d.data.error) || 'fetch failed'}</div>`;
    }
  } catch(e) {
    if (el) el.innerHTML = `<div class="news-loading">Refresh error</div>`;
  }
  btn.classList.remove('loading'); btn.textContent = '↻ Fund';
}

// ── Global "Refresh Fundamentals (All)" button ─────────────────
let fundAllPolling = false;
async function refreshAllFundamentals() {
  const btn = document.getElementById('btnFundAll');
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/fundamentals_refresh_all', { method:'POST' });
    const d = await r.json();
    if (d.success === false) {
      setStatus(d.message || 'Fundamentals refresh already running');
    } else {
      setStatus('Fundamentals refresh started for all stocks…');
    }
    if (!fundAllPolling) { fundAllPolling = true; pollFundRefreshStatus(); }
  } catch(e) { setStatus('Fundamentals refresh error'); btn.classList.remove('loading'); }
}

async function pollFundRefreshStatus() {
  try {
    const r = await fetch('/api/fundamentals_refresh_status');
    const d = await r.json();
    // Progressive rendering: re-pull the whole cache each tick so any
    // stock that just finished shows up immediately, without waiting
    // for the entire batch to complete.
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
  const signalColor = {
    'Strong':   '#15803d',
    'Moderate': '#854d0e',
    'Weak':     '#9a3412',
    'Avoid':    '#991b1b',
  }[signal] || '#64748b';
  const signalBg = {
    'Strong':   '#dcfce7',
    'Moderate': '#fef9c3',
    'Weak':     '#ffedd5',
    'Avoid':    '#fee2e2',
  }[signal] || '#f1f5f9';

  // Build breakdown map for quick verdict lookup
  const bdMap = {};
  (d.breakdown || []).forEach(b => { bdMap[b.metric] = b; });

  function verdict(metric) {
    const b = bdMap[metric];
    if (!b) return '';
    const m = b.display.match(/(✅|⚠|❌|➖)\s*.+$/);
    return m ? ' ' + m[0] : '';
  }

  function fmtGrowth(growth) {
    if (growth == null) return '';
    return ` (${growth > 0 ? '+' : ''}${growth}% YoY)`;
  }

  function row(label, val) {
    if (val == null || val === '—') return '';
    return `<div style="display:flex;gap:3px;align-items:baseline;font-size:9px;line-height:1.5">
      <span style="color:#64748b;font-weight:600;min-width:52px;flex-shrink:0">${label}</span>
      <span style="color:#1e293b;font-weight:500;word-break:break-word">${val}</span>
    </div>`;
  }

  const peVal   = d.pe   != null ? `${Number(d.pe).toFixed(1)}x${verdict('PE')}`   : null;
  const pbVal   = d.pb   != null ? `${Number(d.pb).toFixed(1)}x${verdict('PB')}`   : null;
  const pegVal  = d.peg  != null ? `${Number(d.peg).toFixed(2)}${d.peg_calculated ? ' ~est' : ''}${verdict('PEG')}` : null;
  const roeVal  = d.roe  != null ? `${d.roe}%${verdict('ROE')}`                    : null;
  const deVal   = d.de   != null ? `${Number(d.de).toFixed(2)}x${verdict('D/E')}`  : null;
  const marginVal = d.pm != null ? `${d.pm}%${verdict('Margin')}`                  : null;

  const patVal = d.pat_cr != null
    ? `₹${d.pat_cr}Cr${fmtGrowth(d.pat_growth ?? null)}${verdict('PAT Growth') || verdict('PAT (Qtr)')}`
    : null;

  const revVal = d.rev_cr != null
    ? `₹${d.rev_cr}Cr${fmtGrowth(d.rev_growth ?? null)}${verdict('Rev Growth') || verdict('Revenue')}`
    : null;

  const epsVal = d.eps_trend_signal && d.eps_trend_signal !== 'Insufficient Data'
    ? `${d.eps_trend_signal}${d.eps_cagr != null ? ` (${d.eps_cagr}% CAGR)` : ''}${verdict('EPS Trend')}`
    : null;
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

window.addEventListener('DOMContentLoaded', () => {
  buildRows();

  if (CHART_MODE === '2') {
    setStatus('Old Charts mode — loading saved files…');
  }

  initialBatchRender().then(() => startLivePolling());
  startQuotePolling();
  // Fundamentals and OI are NOT auto-fetched at startup — only whatever is
  // already cached on disk is rendered here. Use the ↻ buttons (per-stock
  // or "Refresh ... (All)") to actually fetch.
  fetchAllFundamentals();
  fetchAllOI();
});

function buildRows() {
  const table = document.getElementById('stockTable');
  table.innerHTML = '';
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
        <div class="oi-header" style="border-top:1px solid var(--border);padding-top:4px;margin-top:5px;">
          <span class="fund-header-label">📊 Fundamentals</span>
          <button class="btn-sm" id="fundbtn-${name}" onclick="refreshSingleFundamentals('${name}', this)">↻ Fund</button>
        </div>
        <div id="fund-unified-${name}" style="margin-top:3px;font-size:9px;line-height:1.5;color:#1e293b;">
          <div class="news-loading">Not fetched — click ↻ Fund</div>
        </div>
        <div class="cond-badge" id="cbadge-${name}"></div>
        <div class="oi-header">
          <span class="oi-header-label">📊 OI Sentiment</span>
          <button class="btn-sm" id="oibtn-${name}" onclick="refreshSingleOI('${name}', this)">↻ OI</button>
        </div>
        <div id="oi-unified-${name}" style="font-size:9px;line-height:1.5;color:#1e293b;margin-bottom:2px;">
          <div class="news-loading">Not fetched — click ↻ OI</div>
        </div>
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
          <div class="chart-pending" style="width:100%;height:100%">Loading…</div>
        </div>`;
      row.appendChild(cell);
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
    layout: { background: { color: '#ffffff' }, textColor: '#475569', fontSize: 10 },
    grid: { vertLines: { color: 'rgba(200,210,230,0.35)' }, horzLines: { color: 'rgba(200,210,230,0.35)' } },
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

  // EMA20 / EMA10
  const ema20Series = chart.addLineSeries({ color:'#f59e0b', lineWidth:1, priceLineVisible:false, lastValueVisible:false });
  const ema10Series = chart.addLineSeries({ color:'#818cf8', lineWidth:1, priceLineVisible:false, lastValueVisible:false });
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
    const pvColors = {PP:'#94a3b8',R1:'#22c55e',R2:'#16a34a',R3:'#15803d',S1:'#ef4444',S2:'#dc2626',S3:'#b91c1c'};
    Object.entries(data.pivots).forEach(([k,v]) => {
      if (v == null) return;
      const pl = candleSeries.createPriceLine({
        price: v, color: pvColors[k] || '#888', lineWidth: 2,
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
  // fitContent(). Increasing "Canvas Width" gives every candle more room and
  // keeps them all visible; decreasing it compresses them but they still all
  // remain in view. Panning beyond the data range is bounded via
  // fixLeftEdge/fixRightEdge above, and you can further zoom/pan manually
  // with scroll/pinch/drag at any time.
  chart.timeScale().fitContent();

  chartReg[key] = { chart, candleSeries, volSeries, ema20Series, ema10Series, stSeriesList, priceLines };
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
      pollSince = Date.now() / 1000;
      await fetchQuotes();
      setStatus(`${name} ${tf} refreshed ✓`);
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
let overlayHidden = false;

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
      if (!overlayHidden && rendered >= 1) {
        overlayHidden = true;
        document.getElementById('loadingOverlay').classList.add('hidden');
      }
      await sleep(0);
    }
    if (!overlayHidden) {
      overlayHidden = true;
      document.getElementById('loadingOverlay').classList.add('hidden');
    }
  } catch(e) { console.error('initialBatchRender error:', e); }
}

async function fetchAndRenderChart(name, tf) {
  try {
    const r = await fetch(`/api/chart?symbol=${encodeURIComponent(name)}&tf=${tf}`);
    const d = await r.json();
    if (d.pending || d.error) return;
    chartData[`${name}_${tf}`] = d;
    renderChart(name, tf, d);
    updateSTDir(name, tf, d);
    if (tf === '5m' && d.end_date_ltp != null) {
      const existing = quotesMap[name] || {};
      if (!existing.ltp) {
        const updated = {
          ...existing,
          ltp:     d.end_date_ltp,
          open:    d.end_date_open,
          high:    d.end_date_high,
          low:     d.end_date_low,
          pchange: d.end_date_pchange ?? existing.pchange,
          change:  d.end_date_ltp != null && d.end_date_open != null
                     ? parseFloat((d.end_date_ltp - d.end_date_open).toFixed(2)) : existing.change,
        };
        quotesMap[name] = updated;
        updateQuoteUI(name, updated);
      }
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
        if (!overlayHidden && renderedSet.size > 0) {
          overlayHidden = true;
          document.getElementById('loadingOverlay').classList.add('hidden');
        }
      }
    } catch(e) {}
    await sleep(1500);
  }
}

function startQuotePolling() {
  fetchQuotes();
  if (CHART_MODE !== '2') {
    setInterval(fetchQuotes, 5000);
  }
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

/**
 * getFirstCandleNoUpwick(name, dayOffset)
 * Returns true if the 9:15am opening candle of the target day has open === high (no upper wick).
 * dayOffset: 0 = latest/current day in 5m data, 1 = previous day, 2 = day before that.
 * Uses chartData[name_5m].labels (format "25 Jun 09:15") and .candles.
 */
function getFirstCandleNoUpwick(name, dayOffset) {
  const data = chartData[`${name}_5m`];
  if (!data || !data.candles || !data.labels) return false;
  const labels  = data.labels;
  const candles = data.candles;
  // Collect unique day prefixes in order of appearance e.g. "25 Jun"
  const seenDays = [];
  const seenSet  = new Set();
  for (const lbl of labels) {
    const day = lbl.slice(0, 6); // "25 Jun"
    if (!seenSet.has(day)) { seenSet.add(day); seenDays.push(day); }
  }
  // dayOffset 0 = last day in data (most recent), 1 = second-last, etc.
  const targetDay = seenDays[seenDays.length - 1 - dayOffset];
  if (!targetDay) return false;
  // Find the FIRST candle of that day (should be 09:15 candle)
  for (let i = 0; i < labels.length; i++) {
    if (labels[i].slice(0, 6) === targetDay) {
      const c = candles[i];
      // No upper wick means open === high
      return c && c.o === c.h;
    }
  }
  return false;
}

// ── OI bias rank for sorting: lower = more bullish ─────────────
const OI_BIAS_RANK = {
  'Bullish': 0,
  'Bullish Lean': 1,
  'Neutral': 2,
  'Bearish Lean': 3,
  'Bearish': 4,
};

// ── Fundamentals signal rank for sorting: lower = stronger ─────
const FUND_SIGNAL_RANK = {
  'Strong': 0,
  'Moderate': 1,
  'Weak': 2,
  'Avoid': 3,
};

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
      case 'noupwick1':
      case 'noupwick2':
      case 'noupwick3': {
        const offset = {'noupwick1':0,'noupwick2':1,'noupwick3':2}[sortMode];
        const fa = getFirstCandleNoUpwick(na, offset);
        const fb = getFirstCandleNoUpwick(nb, offset);
        // stocks WITH no-upwick come first; ties sorted A→Z
        if (fa && !fb) return -1;
        if (!fa && fb) return  1;
        return na.localeCompare(nb);
      }
      case 'oi_cat': {
        // Bullish → Bearish. Stocks with no OI data fetched yet sink to the bottom.
        const oa = oiMap[na], ob = oiMap[nb];
        const va = (oa && !oa.error && !oa.not_fetched);
        const vb = (ob && !ob.error && !ob.not_fetched);
        if (va && !vb) return -1;
        if (!va && vb) return  1;
        if (!va && !vb) return na.localeCompare(nb);
        const ra = OI_BIAS_RANK[oa.bias] ?? 2;
        const rb = OI_BIAS_RANK[ob.bias] ?? 2;
        if (ra !== rb) return ra - rb;
        // tie-break within same bias bucket: higher score = more bullish first
        return (ob.score || 0) - (oa.score || 0);
      }
      case 'atm_ce_asc': {
        const oa = oiMap[na], ob = oiMap[nb];
        const va = (oa && !oa.error && !oa.not_fetched && oa.ce_oi_chg != null && oa.ce_oi_chg !== '');
        const vb = (ob && !ob.error && !ob.not_fetched && ob.ce_oi_chg != null && ob.ce_oi_chg !== '');
        if (va && !vb) return -1;
        if (!va && vb) return  1;
        if (!va && !vb) return na.localeCompare(nb);
        return Number(oa.ce_oi_chg) - Number(ob.ce_oi_chg);
      }

      case 'atm_pe_desc': {
        const oa = oiMap[na], ob = oiMap[nb];
        const va = (oa && !oa.error && !oa.not_fetched && oa.pe_oi_chg != null && oa.pe_oi_chg !== '');
        const vb = (ob && !ob.error && !ob.not_fetched && ob.pe_oi_chg != null && ob.pe_oi_chg !== '');
        if (va && !vb) return -1;
        if (!va && vb) return  1;
        if (!va && !vb) return na.localeCompare(nb);
        return Number(ob.pe_oi_chg) - Number(oa.pe_oi_chg);
      }
      case 'fund_score_desc': {
        // Fundamentals: Strong → Avoid. Missing data sinks to the bottom.
        const fa = fundMap[na], fb = fundMap[nb];
        const va = (fa && !fa.error && fa.signal != null);
        const vb = (fb && !fb.error && fb.signal != null);
        if (va && !vb) return -1;
        if (!va && vb) return  1;
        if (!va && !vb) return na.localeCompare(nb);
        const ra = FUND_SIGNAL_RANK[fa.signal] ?? 2;
        const rb = FUND_SIGNAL_RANK[fb.signal] ?? 2;
        if (ra !== rb) return ra - rb;
        // tie-break within same signal bucket: higher score = stronger first
        return (fb.score || 0) - (fa.score || 0);
      }
      default:            return na.localeCompare(nb);
    }
  });
  rows.forEach(r => table.appendChild(r));
}

async function refreshTF(tf) {
  const btnId = {'5m':'btn5m','1d':'btn1d','1w':'btn1w'}[tf];
  const btn   = document.getElementById(btnId);
  btn.classList.add('loading');
  setStatus(`Refreshing ${tf}…`);
  for (const name of STOCKS) renderedSet.delete(`${name}_${tf}`);
  try {
    await fetch(`/api/refresh_${tf}`, {method:'POST'});
    setTimeout(() => { btn.classList.remove('loading'); setStatus(`${tf} refresh running…`); }, 3000);
    setTimeout(() => setStatus('Ready'), 30000);
  } catch(e) { btn.classList.remove('loading'); setStatus('Refresh error'); }
}

function setStatus(msg) { document.getElementById('statusBadge').textContent = msg; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
</script>
</body>
</html>
"""

# ── startup ───────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"✅ Loaded {len(STOCKS)} stocks")
    print(f"✅ Condition met data: {len(condition_met_map)} stocks")
    print(f"✅ Chart mode: {'Old Charts (file-only, zero API chart calls)' if CHART_MODE == '2' else 'New Charts (live fetch)'}")

    # OI, Fundamentals, and News caches are loaded from their saved files
    # on disk in BOTH New Charts and Old Charts modes. None of them are
    # ever auto-fetched from the internet at startup — that only ever
    # happens when you click a ↻ button (per-stock) or one of the
    # "Refresh ... (All)" buttons in the top bar.
    load_oi_from_csv()
    load_fundamentals_from_file()
    load_news_from_file()
    print("ℹ️  Fundamentals / News / OI caches loaded from file (if available). "
          "Use the ↻ buttons or 'Refresh ... (All)' buttons to fetch fresh data.")

    t1 = threading.Thread(target=fetch_quotes, daemon=True); t1.start()
    t2 = threading.Thread(target=preload_all,  daemon=True); t2.start()
    # NOTE: fundamentals and news are intentionally NOT auto-fetched here.
    # Only chart data (t2) and live quotes (t1) run automatically at startup.

    # subprocess.run(["fuser", "-k", "5000/tcp"], capture_output=True)
    # time.sleep(1)
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
        """Keep reading cloudflared's stdout forever so its pipe buffer never
        fills up and blocks the process. Also grabs the URL the first time
        it appears, and prints everything else for debugging."""
        for line in tunnel_process.stdout:
            if _tunnel_url_holder["url"] is None:
                match = re.search(r"https://[-a-zA-Z0-9]+\.trycloudflare\.com", line)
                if match:
                    _tunnel_url_holder["url"] = match.group(0)
                    print(f"🔗 Tunnel found: {_tunnel_url_holder['url']}")
            print(f"[cloudflared] {line.rstrip()}")


    threading.Thread(target=_drain_tunnel_output, daemon=True).start()

    # Wait up to 20s for the URL to appear
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
                else:
                    print(f"  ⚠ Got status {resp.status_code}")
            except Exception as e:
                print(f"  ⚠ Reachability check error: {type(e).__name__}: {e}")
            if (attempt + 1) % 5 == 0:
                print(f"  … still waiting ({(attempt + 1) * 2}s elapsed)")
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
        time.sleep(60) #interacive charts with timestamps