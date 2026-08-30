# OPEN-BELOW-PREVIOUS-DAY-LOW CHECKER — YFINANCE + NSE FALLBACK
# gap down 10ema code with accepting above day supertrend false with 10ema column
import os
import warnings

if not hasattr(warnings, "_original_showwarning"):
    warnings._original_showwarning = warnings.showwarning


def _filtered_showwarning(message, category, filename, lineno, file=None, line=None):
    # Silently swallow the jupyter_client utcnow() DeprecationWarning specifically;
    # let every other warning behave exactly as it normally would.
    if category is DeprecationWarning and "utcnow" in str(message):
        return
    warnings._original_showwarning(message, category, filename, lineno, file, line)


warnings.showwarning = _filtered_showwarning

# Keep this too as a first line of defense — harmless, still helps for
# anything emitted before IPython resets its filters on the next cell.
warnings.simplefilter("ignore", category=DeprecationWarning)

import subprocess
import sys
import importlib

def ensure_packages(packages):
    for pkg_name, import_name in packages.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            print(f"📦 Installing missing package: {pkg_name}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name, "--quiet"])

ensure_packages({
    "yfinance": "yfinance",
    "pandas_market_calendars": "pandas_market_calendars",
    "jugaad-data": "jugaad_data",
    "fyers-apiv3": "fyers_apiv3",
    "openpyxl": "openpyxl",
})

import time
import random
import threading
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import pandas_market_calendars as mcal
from jugaad_data.nse import stock_df
from fyers_apiv3 import fyersModel
from concurrent.futures import ThreadPoolExecutor, as_completed

import logging
logging.captureWarnings(False)  # undo any hijack of warnings.showwarning done during imports
warnings.showwarning = _filtered_showwarning
warnings.filterwarnings("ignore", category=DeprecationWarning, module="jupyter_client.session")

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN_TIME = dtime(9, 15, 0)
MARKET_CLOSE_TIME = dtime(15, 30, 0)

BASE_DIR = r"C:\Users\sheik\PycharmProjects\MyProject"
os.makedirs(BASE_DIR, exist_ok=True)

# ── Fyers client (used as the primary fallback for recent missing sessions) ──
FYERS_CLIENT_ID = "6KX2O3OQK4-100"
_token_file = f"{BASE_DIR}/token.txt"
_access_token = ""
if os.path.exists(_token_file):
    with open(_token_file, "r") as f:
        _token_content = f.read().strip()
    if "access_token" in _token_content:
        try:
            _token_content = _token_content.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    _access_token = _token_content.strip()

_log_dir = f"{BASE_DIR}/logs/"
os.makedirs(_log_dir, exist_ok=True)

FYERS_AVAILABLE = bool(_access_token)
fyers = fyersModel.FyersModel(
    client_id=FYERS_CLIENT_ID,
    token=_access_token,
    is_async=False,
    log_path=_log_dir,
) if FYERS_AVAILABLE else None

if not FYERS_AVAILABLE:
    print("⚠️ No Fyers access_token found at token.txt — Fyers fallback disabled, jugaad-data only")

# Real NSE trading calendar — no manual holiday list needed
NSE_CAL = mcal.get_calendar("NSE")


def get_ist_now():
    return datetime.now(IST)


def is_trading_day(check_date):
    """True if check_date is a genuine NSE trading day, per the real exchange calendar."""
    d = pd.Timestamp(check_date).date()
    sched = NSE_CAL.schedule(start_date=d, end_date=d)
    return not sched.empty


def is_market_open(ist_now=None):
    if ist_now is None:
        ist_now = get_ist_now()
    if not is_trading_day(ist_now.date()):
        return False
    return MARKET_OPEN_TIME <= ist_now.time() <= MARKET_CLOSE_TIME


# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
OUTPUT_FILE = f"{BASE_DIR}/1hour_Condition_Met_Stocks.xlsx"
TXT_FILE = f"{BASE_DIR}/matched_stocks1.txt"

LOOKBACK_SESSIONS = 30
FETCH_CALENDAR_DAYS = 1300
EMA_LENGTH = 10
SUPERTREND_PERIOD = 14
SUPERTREND_MULTIPLIER = 2
MAX_WORKERS = 4

# How many recent calendar days to check/backfill via NSE fallback
FALLBACK_LOOKBACK_DAYS = 10

# NEW: breakout confirmation no longer requires a strict close ABOVE PrevDay
# High. A later close that lands within this % below PrevDay High still
# counts as a confirmed breakout (e.g. 0.004 = 0.4%).
BREAKOUT_TOLERANCE_PCT = 0.0152

# NEW: only keep rows in the final output whose FinalAcceptedDate falls
# within this many days of today. Everything is still computed/logged
# exactly as before — this only filters what gets saved to Excel/TXT.
RECENT_ACCEPTED_LOOKBACK_DAYS = 7

# ─────────────────────────────────────────
# RATE LIMIT HANDLING (yfinance 429 protection)
# ─────────────────────────────────────────
MIN_REQUEST_INTERVAL = 0.8
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 5

_rate_lock = threading.Lock()
_last_request_time = [0.0]


def _rate_limited_wait():
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time[0]
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time[0] = time.time()


def fetch_with_retry(func, symbol, max_retries=MAX_RETRIES, base_delay=BASE_BACKOFF_SECONDS):
    for attempt in range(max_retries):
        _rate_limited_wait()
        try:
            return func()
        except Exception as e:
            msg = str(e)
            is_rate_limit = ("Too Many Requests" in msg) or ("Rate limited" in msg) or ("429" in msg)
            if is_rate_limit and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 2)
                print(f"⏳ {symbol} → rate limited, retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"{symbol} → exceeded max retries")


# ─────────────────────────────────────────
# STOCK LIST (trimmed to 20 for testing — add the rest back later)
# ─────────────────────────────────────────
#YSTOCKS=['AARTIIND.NS','ACI.NS','ADANIPOWER.NS','AEGISVOPAK.NS','AEQUS.NS','AEROENTER.NS','AEROFLEX.NS','AEROPLANE.NS','AFCONS.NS','AGARWALEYE.NS','AGIIL.NS','AIIL.NS','ALEMBICLTD.NS','ALGOQUANT.NS','AMAGI.NS','AMBUJACEM.NS','ANDHRAPAP.NS','ANDHRSUGAR.NS','APOLLO.NS','APOLLOTYRE.NS','ARFIN.NS','ARIS.NS','ARKADE.NS','ARSSBL.NS','ARVIND.NS','ASHAPURMIN.NS','ASHOKA.NS','ASIANENE.NS','AVANTEL.NS','AWL.NS','BAJAJCON.NS','BAJEL.NS','BECTORFOOD.NS','BEL.NS','BELRISE.NS','BEPL.NS','BERGEPAINT.NS','BHEL.NS','BIOCON.NS','BIRET.NS','BLACKBUCK.NS','BLEL.NS','BLKASHYAP.NS','BLS.NS','BLSE.NS','BLUEJET.NS','BLUSPRING.NS','BODALCHEM.NS','BOMDYEING.NS','BOROLTD.NS','BORORENEW.NS','BPCL.NS','BRIGHOTEL.NS','BSOFT.NS','CAMLINFINE.NS','CAMPUS.NS','CANHLIFE.NS','CAPACITE.NS','CASTROLIND.NS','CEIGALL.NS','CHAMBLFERT.NS','CIEINDIA.NS','CLSEL.NS','CMPDI.NS','CMRGREEN.NS','CMSINFO.NS','COALINDIA.NS','COHANCE.NS','CONCOR.NS','CONFIPET.NS','CROMPTON.NS','CUPID.NS','DABUR.NS','DALMIASUG.NS','DAMCAPITAL.NS','DBREALTY.NS','DCXINDIA.NS','DECNGOLD.NS','DELHIVERY.NS','DELTACORP.NS','DHAMPURSUG.NS','DIGITIDE.NS','DWARKESH.NS','DYCL.NS','EIEL.NS','ELECON.NS','ELECTCAST.NS','ELLEN.NS','EMAMILTD.NS','EMBDL.NS','EMMVEE.NS','ENGINERSIN.NS','EPACK.NS','EPACKPEB.NS','EPL.NS','ETERNAL.NS','EXIDEIND.NS','FCL.NS','FILATEX.NS','FINPIPE.NS','FIRSTCRY.NS','FMGOETZE.NS','FSL.NS','GAEL.NS','GAIL.NS','GAJA.NS','GANDHAR.NS','GARUDA.NS','GATEWAY.NS','GCSL.NS','GENESYS.NS','GEOJITFSL.NS','GHCLTEXTIL.NS','GICRE.NS','GIPCL.NS','GKENERGY.NS','GKSL.NS','GMDCLTD.NS','GNFC.NS','GODIGIT.NS','GOKULAGRO.NS','GOLDIAM.NS','GOODLUCK.NS','GPIL.NS','GPPL.NS','GRAUWEIL.NS','GREAVESCOT.NS','GRMOVER.NS','GROWW.NS','GSFC.NS','GUJENERGY.NS','GUJTHEM.NS','GULPOLY.NS','HAPPSTMNDS.NS','HDFCLIFE.NS','HEMIPROP.NS','HEXT.NS','HFCL.NS','HINDCOPPER.NS','HINDOILEXP.NS','HINDPETRO.NS','HITECH.NS','HONASA.NS','HUBTOWN.NS','ICICIPRULI.NS','ICIL.NS','IEX.NS','IFCI.NS','IGIL.NS','IGL.NS','IMAGICAA.NS','INDGN.NS','INDOBORAX.NS','INDSWFTLAB.NS','INDUSTOWER.NS','INOXGREEN.NS','INOXWIND.NS','IOC.NS','IOLCP.NS','IRCON.NS','IRCTC.NS','IRISDOREME.NS','ITC.NS','ITCHOTELS.NS','IVALUE.NS','IXIGO.NS','JAIBALAJI.NS','JAINREC.NS','JAMNAAUTO.NS','JAYKAY.NS','JAYNECOIND.NS','JINDALSAW.NS','JIOFIN.NS','JKPAPER.NS','JKTYRE.NS','JSLL.NS','JSWCEMENT.NS','JSWINFRA.NS','JTLIND.NS','JWL.NS','JYOTHYLAB.NS','KALAMANDIR.NS','KCP.NS','KEC.NS','KIRIINDUS.NS','KLBRENG-B.NS','KNACK.NS','KNRCON.NS','KPEL.NS','KRBL.NS','KROSS.NS','KRT.NS','KUSUMGAR.NS','LALITHAA.NS','LATENTVIEW.NS','LCL.NS','LLOYDSENGG.NS','LLOYDSENT.NS','LOTUSDEV.NS','LTFOODS.NS','LXCHEM.NS','MANALIPETC.NS','MANINFRA.NS','MANYAVAR.NS','MARATHON.NS','MARINE.NS','MARKSANS.NS','MARSONS.NS','MEDIASSIST.NS','MEESHO.NS','MIDHANI.NS','MMTC.NS','MOBIKWIK.NS','MOIL.NS','MOL.NS','MOREPENLAB.NS','MOSCHIP.NS','MOTHERSON.NS','MUNJALAU.NS','MVKAGRO.NS','NACLIND.NS','NATIONALUM.NS','NAVA.NS','NAZARA.NS','NBCC.NS','NCC.NS','NEWGEN.NS','NIITLTD.NS','NITCO.NS','NMDC.NS','NOCIL.NS','NXST.NS','NYKAA.NS','OAL.NS','OIL.NS','OMNI.NS','ONEPOINT.NS','ONGC.NS','ONIXSOLAR.NS','OPTIEMUS.NS','ORIENTHOT.NS','OSWALPUMPS.NS','PACEDIGITK.NS','PARACABLES.NS','PARAGMILK.NS','PARKHOSPS.NS','PATANJALI.NS','PETRONET.NS','PGEL.NS','PGINVIT.NS','PINELABS.NS','PNCINFRA.NS','PNGJL.NS','POCL.NS','PPLPHARMA.NS','PRAJIND.NS','PRECWIRE.NS','PREMIERPOL.NS','PRINCEPIPE.NS','PTC.NS','PWL.NS','QUADFUTURE.NS','QUESS.NS','RAILTEL.NS','RALLIS.NS','RAMBHAJO.NS','RATNAVEER.NS','RCF.NS','REDINGTON.NS','REDTAPE.NS','REFEX.NS','RELIGARE.NS','RELINFRA.NS','RGL.NS','RICOAUTO.NS','RIIT.NS','ROLEXRINGS.NS','ROUTE.NS','RSL.NS','RVNL.NS','SAIL.NS','SAIPARENT.NS','SAMBHV.NS','SAMHI.NS','SANDUMA.NS','SANGHVIMOV.NS','SAREGAMA.NS','SBIFUNDS.NS','SCI.NS','SDBL.NS','SEIL.NS','SETL.NS','SHADOWFAX.NS','SHAKTIPUMP.NS','SHANKESH.NS','SHANTIGEAR.NS','SHANTIGOLD.NS','SHIPROCKET.NS','SHK.NS','SHRINGARMS.NS','SHRIRAMPPS.NS','SILVERTUC.NS','SIRCA.NS','SKIPPER.NS','SKMEGGPROD.NS','SOMANYCERA.NS','SONATSOFTW.NS','SPARC.NS','SPIC.NS','SPORTKING.NS','SSWL.NS','STALLION.NS','SUNSHINE.NS','SUNTECK.NS','SURYAROSNI.NS','SUVEN.NS','SWANCORP.NS','SWIGGY.NS','SYNGENE.NS','TANLA.NS','TARIL.NS','TATASTEEL.NS','TCC.NS','TECHNOCRAF.NS','TEMBO.NS','TENNIND.NS','TEXRAIL.NS','TFCILTD.NS','THELEELA.NS','THEMISMED.NS','THOMASCOOK.NS','THYROCARE.NS','TI.NS','TIMETECHNO.NS','TMCV.NS','TMPV.NS','TNPETRO.NS','TRANSRAILL.NS','TRITURBINE.NS','TRIVENI.NS','TURTLEMINT.NS','UDS.NS','UPL.NS','URBANCO.NS','USHAMART.NS','UTLSOLAR.NS','UTTAMSUGAR.NS','VBL.NS','VEDL.NS','VERANDA.NS','VGUARD.NS','VIDYAWIRES.NS','VIKRAMSOLR.NS','VIKRAN.NS','VINCOFE.NS','VIYASH.NS','VMM.NS','VOEPL.NS','WAKEFIT.NS','WALCHANNAG.NS','WEBELSOLAR.NS','WELSPLSOL.NS','WELSPUNLIV.NS','WIPRO.NS','XTRANET.NS','YATRA.NS','ZAGGLE.NS','ZEEL.NS','ZENSARTECH.NS','ZYDUSWELL.NS']
YSTOCKS=['AARTIIND.NS','ACI.NS','ADANIPOWER.NS','AEGISVOPAK.NS','AEQUS.NS','AEROENTER.NS','AEROFLEX.NS','AEROPLANE.NS','AFCONS.NS','AGARWALEYE.NS','AGIIL.NS','AIIL.NS','ALEMBICLTD.NS','ALGOQUANT.NS','AMAGI.NS','AMBUJACEM.NS','ANDHRAPAP.NS','ANDHRSUGAR.NS','APOLLO.NS','APOLLOTYRE.NS']


def convert_to_fyers(symbol):
    if symbol.endswith(".NS"):
        return f"NSE:{symbol.replace('.NS', '')}-EQ"
    return symbol


# Built once from YSTOCKS — e.g. "DANISH.NS" -> "NSE:DANISH-EQ".
# Every Fyers call below looks up this map instead of converting on the fly.
FYERS_SYMBOL_MAP = {s: convert_to_fyers(s) for s in YSTOCKS}


# ─────────────────────────────────────────
# FALLBACK FETCH #1 — FYERS (primary, has today's session, needs valid token)
# ─────────────────────────────────────────
# Symbols Fyers has already rejected as invalid this run — skip retrying them
_FYERS_INVALID_SYMBOLS = set()


def fetch_missing_from_fyers(symbol, missing_dates):
    if not FYERS_AVAILABLE:
        return pd.DataFrame()

    fyers_symbol = FYERS_SYMBOL_MAP.get(symbol, convert_to_fyers(symbol))  # e.g. "DANISH.NS" -> "NSE:DANISH-EQ"

    if fyers_symbol in _FYERS_INVALID_SYMBOLS:
        print(f"⏭️ {symbol} → skipping Fyers ({fyers_symbol} already flagged invalid), going to jugaad/NSE")
        return pd.DataFrame()

    start = min(missing_dates)
    end = max(missing_dates)
    try:
        resp = fyers.history(data={
            "symbol": fyers_symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": start.strftime("%Y-%m-%d"),
            "range_to": end.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        })

        if not isinstance(resp, dict) or resp.get("s") != "ok":
            code = resp.get("code") if isinstance(resp, dict) else None
            if code == -300:
                # Genuinely not in Fyers' symbol master (delisted/renamed/BSE-only) —
                # not a formatting issue, symbol sent was fyers_symbol above.
                print(f"⚠️ {symbol} → Fyers doesn't recognize {fyers_symbol} (code -300), won't retry this run")
                _FYERS_INVALID_SYMBOLS.add(fyers_symbol)
            else:
                print(f"⚠️ {symbol} → Fyers fallback error for {fyers_symbol}: {resp}")
            return pd.DataFrame()

        candles = resp.get("candles", [])
        if not candles:
            print(f"⚠️ {symbol} → Fyers fallback returned 0 candles for {fyers_symbol}")
            return pd.DataFrame()

        raw = pd.DataFrame(candles, columns=["Epoch", "Open", "High", "Low", "Close", "Volume"])
        raw["Datetime"] = (
            pd.to_datetime(raw["Epoch"], unit="s", utc=True)
            .dt.tz_convert(IST)
            .dt.tz_localize(None)
            .dt.normalize()
        )
        raw = raw[["Datetime", "Open", "High", "Low", "Close", "Volume"]]

        print(f"✅ {symbol} → Fyers fallback returned {len(raw)} row(s):")
        for _, r in raw.iterrows():
            print(f"    {r['Datetime'].date()} O={r['Open']:.2f} H={r['High']:.2f} L={r['Low']:.2f} C={r['Close']:.2f} V={int(r['Volume'])}")

        return raw
    except Exception as e:
        print(f"⚠️ {symbol} → Fyers fallback fetch failed: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# FALLBACK FETCH #2 — jugaad-data / NSE (secondary, older gaps only)
# ─────────────────────────────────────────
def fetch_missing_from_jugaad(symbol, missing_dates):
    base = symbol.replace(".NS", "")
    start = pd.Timestamp(min(missing_dates)).date()
    end = pd.Timestamp(max(missing_dates)).date()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            raw = stock_df(symbol=base, from_date=start, to_date=end, series="EQ")
        if raw is None or raw.empty:
            return pd.DataFrame()
        raw = raw.rename(columns={
            "DATE": "Datetime", "OPEN": "Open", "HIGH": "High",
            "LOW": "Low", "CLOSE": "Close", "VOLUME": "Volume",
        })
        dt_series = pd.to_datetime(raw["Datetime"])
        if dt_series.dt.tz is not None:
            dt_series = dt_series.dt.tz_localize(None)
        raw["Datetime"] = dt_series
        raw = raw[["Datetime", "Open", "High", "Low", "Close", "Volume"]]

        print(f"✅ {symbol} → jugaad/NSE fallback returned {len(raw)} row(s):")
        for _, r in raw.iterrows():
            print(f"    {r['Datetime'].date()} O={r['Open']:.2f} H={r['High']:.2f} L={r['Low']:.2f} C={r['Close']:.2f} V={int(r['Volume'])}")

        return raw
    except Exception as e:
        print(f"⚠️ {symbol} → jugaad/NSE fallback fetch failed: {e}")
        return pd.DataFrame()


def fill_missing_trading_days(symbol, df, start_date, end_date):

    cal_end = min(end_date.date(), datetime.now().date())
    sched = NSE_CAL.schedule(start_date=start_date.date(), end_date=cal_end)
    expected_days = set(sched.index.date)
    have_days = set(pd.to_datetime(df["Datetime"]).dt.date) if not df.empty else set()

    cutoff = datetime.now().date() - timedelta(days=FALLBACK_LOOKBACK_DAYS)
    missing = sorted(pd.Timestamp(d).date() for d in (expected_days - have_days) if pd.Timestamp(d).date() >= cutoff)
    if not missing:
        return df, []

    print(f"🔁 {symbol} → yfinance missing {len(missing)} recent session(s): {missing}")
    merged = df

    fb1 = fetch_missing_from_fyers(symbol, missing)
    if not fb1.empty:
        merged = pd.concat([merged, fb1], ignore_index=True)
        merged = merged.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)

    still_missing = sorted(d for d in (expected_days - set(pd.to_datetime(merged["Datetime"]).dt.date)) if d >= cutoff) if not merged.empty else missing
    if still_missing:
        print(f"🔁 {symbol} → still missing {len(still_missing)} after Fyers, trying jugaad/NSE for: {still_missing}")
        fb2 = fetch_missing_from_jugaad(symbol, still_missing)
        if not fb2.empty:
            merged = pd.concat([merged, fb2], ignore_index=True)
            merged = merged.drop_duplicates(subset="Datetime").sort_values("Datetime").reset_index(drop=True)

    final_missing = sorted(d for d in (expected_days - set(pd.to_datetime(merged["Datetime"]).dt.date)) if d >= cutoff) if not merged.empty else missing
    if final_missing:
        print(f"⚠️ {symbol} → still missing after both fallbacks: {final_missing}")
    else:
        print(f"✅ {symbol} → all recent sessions now present after fallback merge")

    return merged, final_missing


# ─────────────────────────────────────────
# FETCH — DAILY CANDLES (yfinance, backfilled from Fyers/NSE if needed)
# ─────────────────────────────────────────
def fetch_yfinance(symbol):
    """NEW: returns (df, final_missing) — final_missing is the list of
    trading-day dates that could not be fetched from ANY source (yfinance +
    Fyers + jugaad/NSE), so downstream code can confirm data completeness."""
    try:
        end_date = datetime.now() + timedelta(days=1)
        start_date = end_date - timedelta(days=FETCH_CALENDAR_DAYS)

        def _do_fetch():
            return yf.Ticker(symbol).history(
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=False,
            )

        df = fetch_with_retry(_do_fetch, symbol)
        if df is None or df.empty:
            df = pd.DataFrame()
        else:
            df = df.reset_index()
            if "Datetime" not in df.columns and "Date" in df.columns:
                df = df.rename(columns={"Date": "Datetime"})
            df = df[["Datetime", "Open", "High", "Low", "Close", "Volume"]].dropna()
            dt_series = pd.to_datetime(df["Datetime"])
            if dt_series.dt.tz is not None:
                dt_series = dt_series.dt.tz_localize(None)
            df["Datetime"] = dt_series

        df, final_missing = fill_missing_trading_days(symbol, df, start_date, end_date)

        if df.empty:
            print(f"⚠️ No data at all (yfinance + NSE) for {symbol}")
        return df, final_missing
    except Exception as e:
        print(f"❌ fetch failed for {symbol}: {e}")
        return pd.DataFrame(), []


def fetch_live_ltp(symbol):
    """Real-time tick via fast_info — daily 'Close' for today is often stale intraday."""
    try:
        def _do_fetch():
            fi = yf.Ticker(symbol).fast_info
            for key in ("last_price", "lastPrice", "regularMarketPrice"):
                try:
                    val = fi[key]
                    if val is not None:
                        return float(val)
                except Exception:
                    continue
            return None
        return fetch_with_retry(_do_fetch, symbol)
    except Exception as e:
        print(f"⚠️ {symbol} → live LTP fetch failed: {e}")
        return None


def process_symbol(symbol):
    df, final_missing = fetch_yfinance(symbol)
    if df.empty:
        return []
    market_open_now = is_market_open()
    live_ltp = fetch_live_ltp(symbol) if market_open_now else None
    return evaluate_conditions(df, symbol, live_ltp, market_open_now, missing_dates=final_missing)


# ─────────────────────────────────────────
# SUPERTREND (Wilder ATR) — used on WEEKLY candles (event confirmation)
# and on DAILY candles (current-trend / rescue check)
# ─────────────────────────────────────────
def compute_supertrend(ohlc, period=14, multiplier=2):
    df = ohlc.copy()
    high, low, close = df['High'], df['Low'], df['Close']
    prev_close = close.shift(1)

    tr = pd.concat([
        (high - low), (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = pd.Series(index=df.index, dtype='float64')
    if len(tr) >= period:
        atr.iloc[period - 1] = tr.iloc[0:period].mean()
        for i in range(period, len(tr)):
            atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period

    hl2 = (high + low) / 2
    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr

    final_upperband = pd.Series(index=df.index, dtype='float64')
    final_lowerband = pd.Series(index=df.index, dtype='float64')
    supertrend = pd.Series(index=df.index, dtype='float64')
    direction = pd.Series(index=df.index, dtype='float64')
    start = period - 1

    for i in range(len(df)):
        if i < start:
            continue
        if i == start:
            final_upperband.iloc[i] = upperband.iloc[i]
            final_lowerband.iloc[i] = lowerband.iloc[i]
            direction.iloc[i] = 1 if close.iloc[i] >= final_lowerband.iloc[i] else -1
            supertrend.iloc[i] = final_lowerband.iloc[i] if direction.iloc[i] == 1 else final_upperband.iloc[i]
            continue

        final_upperband.iloc[i] = min(upperband.iloc[i], final_upperband.iloc[i - 1]) if close.iloc[i - 1] <= final_upperband.iloc[i - 1] else upperband.iloc[i]
        final_lowerband.iloc[i] = max(lowerband.iloc[i], final_lowerband.iloc[i - 1]) if close.iloc[i - 1] >= final_lowerband.iloc[i - 1] else lowerband.iloc[i]

        prev_dir = direction.iloc[i - 1]
        direction.iloc[i] = (-1 if close.iloc[i] < final_lowerband.iloc[i] else 1) if prev_dir == 1 else (1 if close.iloc[i] > final_upperband.iloc[i] else -1)
        supertrend.iloc[i] = final_lowerband.iloc[i] if direction.iloc[i] == 1 else final_upperband.iloc[i]

    df['ATR'] = atr
    df['Supertrend'] = supertrend
    df['ST_Direction'] = direction
    return df


def confirm_supertrend_direction(st_df):

    df = st_df.copy()
    n = len(df)
    raw_dir = df['ST_Direction'].values
    closes = df['Close'].values
    lows = df['Low'].values
    highs = df['High'].values
    confirmed = [None] * n

    valid_positions = [i for i in range(n) if pd.notna(raw_dir[i])]
    if not valid_positions:
        df['ST_Direction_Confirmed'] = confirmed
        return df

    start = valid_positions[0]
    confirmed[start] = raw_dir[start]

    i = start + 1
    while i < n:
        prev_conf = confirmed[i - 1]
        raw = raw_dir[i]

        if pd.isna(raw):
            confirmed[i] = prev_conf
            i += 1
            continue

        if raw == prev_conf:
            confirmed[i] = prev_conf
            i += 1
            continue

        # ── Potential flip starts here — i is the "break candle" ──
        break_idx = i
        break_level = lows[break_idx] if raw == -1 else highs[break_idx]

        confirmed[break_idx] = prev_conf  # pending — hold old color for now
        j = break_idx + 1
        resolved_dir = None

        while j < n:
            raw_j = raw_dir[j]
            if pd.isna(raw_j):
                confirmed[j] = prev_conf
                j += 1
                continue

            if raw_j != raw:
                # raw trend reverted back to the old direction before confirming
                # → abandon the break entirely, whole pending stretch stays prev_conf
                resolved_dir = prev_conf
                break

            # still in the candidate new direction — check against the
            # ORIGINAL break candle's level (not a rolling one)
            if raw == -1 and closes[j] < break_level:
                resolved_dir = -1
                break
            elif raw == 1 and closes[j] > break_level:
                resolved_dir = 1
                break
            else:
                confirmed[j] = prev_conf  # still pending, keep checking against same level
                j += 1

        if resolved_dir is not None:
            for k in range(break_idx, j + 1):
                confirmed[k] = resolved_dir
            i = j + 1
        else:
            # ran out of data before confirming or reverting — stays pending to the end
            i = n

    df['ST_Direction_Confirmed'] = confirmed
    return df


def get_weekly_supertrend_row(weekly_st, target_date):
    valid_idx = weekly_st.index[weekly_st.index <= target_date]
    if len(valid_idx) == 0:
        return None
    return weekly_st.loc[valid_idx[-1]]


# ─────────────────────────────────────────
# CONDITION CHECKER
# ─────────────────────────────────────────
def evaluate_conditions(df, name, live_ltp=None, market_open_now=None, missing_dates=None):
    results = []
    found = False

    if market_open_now is None:
        market_open_now = is_market_open()

    # NEW: data-completeness confirmation string, driven by whatever
    # fetch_yfinance()/fill_missing_trading_days() could NOT source from
    # yfinance, Fyers, or jugaad/NSE combined.
    missing_dates = missing_dates or []
    if missing_dates:
        data_status = f"⚠️ missing {len(missing_dates)} session(s): {missing_dates}"
    else:
        data_status = "no missing data ✅"

    ist_now = get_ist_now()
    today_norm = pd.Timestamp(ist_now.date())

    df = df.copy()
    df['Date'] = pd.to_datetime(df['Datetime']).dt.normalize()

    daily = df.groupby('Date').agg(
        Open=('Open', 'first'), High=('High', 'max'),
        Low=('Low', 'min'), Close=('Close', 'last'), Volume=('Volume', 'sum')
    ).reset_index().sort_values('Date').reset_index(drop=True)

    daily['Volume'] = daily['Volume'].fillna(0)
    daily = daily[daily['Volume'] > 0].reset_index(drop=True)

    # Drop anything that isn't a real NSE trading day, per the exchange calendar
    daily = daily[daily['Date'].apply(is_trading_day)].reset_index(drop=True)

    if len(daily) < 3:
        return results

    last_available_date = daily['Date'].iloc[-1]
    days_stale = (today_norm - last_available_date).days
    if days_stale > 7:
        print(f"⚠️ {name} → data stale ({last_available_date.date()}, {days_stale}d old) — skipping")
        return results

    last_row_is_today = last_available_date == today_norm
    today_still_forming = last_row_is_today and market_open_now

    # 10 EMA on daily LOW — exclude today's still-forming candle while market is open
    daily_closed = daily.iloc[:-1] if (today_still_forming and len(daily) > 1) else daily
    daily_closed = daily_closed.copy()
    daily_closed['EMA10_Low'] = daily_closed['Low'].ewm(span=EMA_LENGTH, adjust=False, min_periods=EMA_LENGTH).mean()

    current_ema_ref = daily_closed['EMA10_Low'].iloc[-1] if len(daily_closed) > 0 else None
    ema_ref_date = daily_closed['Date'].iloc[-1] if len(daily_closed) > 0 else None

    if not market_open_now:
        current_price = daily['Close'].iloc[-1]
        price_source = "today's close (market closed)"
    elif live_ltp is not None:
        current_price = live_ltp
        price_source = "live LTP"
    else:
        current_price = daily['Close'].iloc[-1]
        price_source = "last close (live fetch failed)"

    weekly = daily.set_index('Date')[['Open', 'High', 'Low', 'Close']].resample('W-FRI').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()

    weekly_st = compute_supertrend(weekly, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER) if len(weekly) >= SUPERTREND_PERIOD else None
    if weekly_st is not None:
        weekly_st = confirm_supertrend_direction(weekly_st)  # filter false weekly ST breakouts


    daily_st = (
        compute_supertrend(
            daily_closed.set_index('Date')[['Open', 'High', 'Low', 'Close']],
            SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER
        )
        if len(daily_closed) >= SUPERTREND_PERIOD else None
    )
    if daily_st is not None:
        daily_st = confirm_supertrend_direction(daily_st)  # filter false daily ST breakouts

    current_st_val, current_st_color, current_above_st = None, "NA", False
    if daily_st is not None and not daily_st.empty:
        last_daily_st_row = daily_st.iloc[-1]
        if pd.notna(last_daily_st_row['Supertrend']) and pd.notna(last_daily_st_row['ST_Direction_Confirmed']):
            current_st_val = last_daily_st_row['Supertrend']
            current_st_color = "Green" if last_daily_st_row['ST_Direction_Confirmed'] == 1 else "Red"
            current_above_st = last_daily_st_row['ST_Direction_Confirmed'] == 1

    current_passes_ema = pd.notna(current_ema_ref) and current_price > current_ema_ref
    crossed_label = "LTP crossed 10EMA ✅" if current_passes_ema else "LTP NOT crossed 10EMA ❌"
    ema_ref_str = f"{current_ema_ref:.2f}" if pd.notna(current_ema_ref) else "NA"
    current_st_str = f"{current_st_val:.2f}({current_st_color})" if current_st_val is not None else "NA"
    above_st_label = "LTP above Daily ST ✅" if current_above_st else "LTP NOT above Daily ST ❌"

    # NOTE: stock-level EMA gate removed — we go straight into the gap-down
    # condition loop below, where per-event EMA/Supertrend checks still apply.
    print(
        f"▶️ {name} → LTP={current_price:.2f} ({price_source}) | "
        f"10EMA(Low) as of {ema_ref_date.date() if ema_ref_date is not None else 'NA'}={ema_ref_str} | {crossed_label} | "
        f"CurrentDailyST={current_st_str} | {above_st_label} | DataCheck: {data_status}"
    )

    # Causal (no future leakage) 10EMA(Low) series, for per-event checks
    daily['EMA10_Low_Full'] = daily['Low'].ewm(span=EMA_LENGTH, adjust=False, min_periods=EMA_LENGTH).mean()
    # Causal 20EMA(Low) series, used only for the post-acceptance integrity check
    daily['EMA20_Low_Full'] = daily['Low'].ewm(span=20, adjust=False, min_periods=20).mean()
    start_idx = max(1, len(daily) - LOOKBACK_SESSIONS)

    for i in range(start_idx, len(daily)):
        today_row = daily.iloc[i]
        prev_row = daily.iloc[i - 1]
        today_date = today_row['Date']
        prev_low = prev_row['Low']
        prev_close, prev_open = prev_row['Close'], prev_row['Open']
        prev_color = "Green" if prev_close > prev_open else "Red"
        prev2_color = "NA"
        if i - 2 >= 0:
            p2 = daily.iloc[i - 2]
            prev2_color = "Green" if p2['Close'] > p2['Open'] else "Red"

        # Color of the condition (gap-down) candle itself
        condition_candle_color = "Green" if today_row['Close'] > today_row['Open'] else "Red"

        if today_row['Open'] > prev_low:
            continue

        gap = round(prev_low - today_row['Open'], 2)

        # Own-day EMA check for this specific event
        if today_date == last_available_date and today_still_forming:
            event_price, event_ema, event_ema_date = current_price, current_ema_ref, ema_ref_date
        else:
            event_price = today_row['Close']
            event_ema = daily.loc[i, 'EMA10_Low_Full']
            event_ema_date = today_date

        event_passes_ema = pd.notna(event_ema) and event_price > event_ema
        event_ema_str = f"{event_ema:.2f}" if pd.notna(event_ema) else "NA"


        if condition_candle_color == "Red":
            if today_date == last_available_date and today_still_forming:
                ema_ref_price = current_price  # live LTP stands in for today's still-forming close
            else:
                ema_ref_price = today_row['Close']
        else:  # Green
            ema_ref_price = today_row['Open']

        ema_distance_pct = round((ema_ref_price / event_ema - 1) * 100, 2) if pd.notna(event_ema) else None

        # Fallback: own-day weekly Supertrend colour
        event_st_val, event_st_color, event_passes_st = None, "NA", False
        if not event_passes_ema and weekly_st is not None:
            wk_row = get_weekly_supertrend_row(weekly_st, today_date)
            if wk_row is not None and pd.notna(wk_row['Supertrend']) and pd.notna(wk_row['ST_Direction_Confirmed']):
                event_st_val = wk_row['Supertrend']
                event_st_color = "Green" if wk_row['ST_Direction_Confirmed'] == 1 else "Red"
                event_passes_st = event_st_color == "Green"

        event_st_info = f"WeeklyST={event_st_val:.2f}({event_st_color})" if event_st_val is not None else "WeeklyST=NA"
        event_confirmed = event_passes_ema or event_passes_st

        if event_passes_ema:
            ema_pct = round((event_price / event_ema - 1) * 100, 2)
            confirmed_by = f"own-day 10EMA(Low) ({ema_pct:+.2f}%)"
        elif event_passes_st:
            st_pct = round((event_price / event_st_val - 1) * 100, 2)
            confirmed_by = f"own-day Weekly Supertrend ({st_pct:+.2f}%)"
        else:
            confirmed_by = None


        weekly_breakout_confirmed = False
        weekly_breakout_date = None
        weekly_breakout_close = None
        prev_week_high = None

        if not event_confirmed:
            later_weeks = weekly.index[weekly.index >= today_date]
            if len(later_weeks) > 0:
                event_week_pos = weekly.index.get_loc(later_weeks[0])
                if event_week_pos >= 1:
                    prev_week_high = weekly['High'].iloc[event_week_pos - 1]
                    for k in range(event_week_pos + 1, len(weekly)):
                        wk_close = weekly['Close'].iloc[k]
                        if wk_close > prev_week_high:
                            weekly_breakout_confirmed = True
                            weekly_breakout_date = weekly.index[k]
                            weekly_breakout_close = wk_close
                            break

            if weekly_breakout_confirmed:
                event_confirmed = True
                weekly_breakout_pct = round((weekly_breakout_close / prev_week_high - 1) * 100, 2)
                confirmed_by = (
                    f"weekly breakout above prev week high on {weekly_breakout_date.date()} "
                    f"({weekly_breakout_pct:+.2f}%)"
                )

        if not event_confirmed:
            print(f"⛔ {name} → {today_date.date()} gap-down REJECTED | Price={event_price:.2f} vs EMA={event_ema_str} | {event_st_info}")
            continue


        breakout_threshold = prev_row['High'] * (1 - BREAKOUT_TOLERANCE_PCT)
        breakout_confirmed, breakout_date, breakout_close = False, None, None
        for j in range(i + 1, len(daily)):
            later_row = daily.iloc[j]
            if later_row['Close'] >= breakout_threshold:
                breakout_confirmed = True
                breakout_date = later_row['Date']
                breakout_close = later_row['Close']
                break

        if not breakout_confirmed:
            print(
                f"⛔ {name} → {today_date.date()} confirmed, but price never recovered within "
                f"{BREAKOUT_TOLERANCE_PCT*100:.2f}% of PrevDay High={prev_row['High']:.2f} — skipping"
            )
            continue

        breakout_pct_vs_high = round((breakout_close / prev_row['High'] - 1) * 100, 2)

        # Whether the confirming candle closed strictly above PrevDay High,
        # or only landed within the accepted tolerance band below it.
        if breakout_close > prev_row['High']:
            tag = f"Closed ABOVE ({breakout_pct_vs_high:+.2f}%)"
        else:
            tag = f"Closed NEAR BY ({breakout_pct_vs_high:+.2f}%)"


        stage2_icon = "✅" if (event_passes_ema or event_passes_st) else "📌"
        print(
            f"{stage2_icon} {name} → Event on {today_date.date()} | Confirmed by: {confirmed_by} | "
            f"Breakout: {tag} PrevDay High on {breakout_date.date()} | "
            f"EMA Distance ({'Close' if condition_candle_color == 'Red' else 'Open'} vs EMA)="
            f"{ema_distance_pct if ema_distance_pct is not None else 'NA'}% | "
            f"Candle={condition_candle_color}"
        )


        stage2_date = today_date if (event_passes_ema or event_passes_st) else weekly_breakout_date
        final_accepted_date = max(stage2_date, breakout_date)
        # CHANGED: FINAL ACCEPTED print now includes the data-completeness
        # confirmation (data_status), computed once above from
        # fetch_yfinance()/fill_missing_trading_days() — e.g.
        # "GNFC.NS → FINAL ACCEPTED on 2026-07-27 | Data: no missing data ✅"
        print(f"🏁 {name} → FINAL ACCEPTED on {final_accepted_date.date()} | Data: {data_status}")

        close_never_below_ema = True
        for k in range(i + 1, len(daily)):
            ema_chk = daily['EMA20_Low_Full'].iloc[k]
            close_chk = daily['Close'].iloc[k]
            if pd.notna(ema_chk) and close_chk < ema_chk:
                close_never_below_ema = False
                break
        close_never_below_ema_label = "Yes" if close_never_below_ema else "No"

        found = True
        results.append({
            "StockName": name,
            "ConditionMetDate": today_date.date(),
            "PriceGapDifference": gap,
            "Previous2DaysCandleColor": prev2_color,
            "PreviousDayCandleColor": prev_color,
            "ConditionCandleColor": condition_candle_color,
            "CurrentLTP": round(current_price, 2),
            "Current10EMA_Low": round(current_ema_ref, 2) if pd.notna(current_ema_ref) else None,
            "LTPCrossed10EMA": current_passes_ema,
            # CHANGED: current (latest, not event-day) DAILY Supertrend context —
            # used by filter_recent_accepted() to rescue stale-but-still-trending rows.
            "CurrentDailySupertrend": round(current_st_val, 2) if current_st_val is not None else None,
            "CurrentDailySupertrendColor": current_st_color,
            "CurrentAboveSupertrend": current_above_st,
            "EventCandlePrice": round(event_price, 2),
            "EventDay10EMA_Low": round(event_ema, 2) if pd.notna(event_ema) else None,
            "EMADistancePct": ema_distance_pct,
            "EMADistanceBasis": "Close" if condition_candle_color == "Red" else "Open",
            "ConfirmedBy": confirmed_by,
            "PrevDayHigh": round(prev_row['High'], 2),
            "BreakoutDate": breakout_date.date(),
            "BreakoutClose": round(breakout_close, 2),
            "BreakoutPctVsPrevHigh": breakout_pct_vs_high,
            "BreakoutType": "Closed Above" if breakout_close > prev_row['High'] else f"Closed Near By ({BREAKOUT_TOLERANCE_PCT*100:.2f}%)",
            "WeeklyFallbackUsed": weekly_breakout_confirmed,
            "WeeklyFallbackDate": weekly_breakout_date.date() if weekly_breakout_confirmed else None,
            "WeeklyFallbackClose": round(weekly_breakout_close, 2) if weekly_breakout_confirmed else None,
            "PrevWeekHigh": round(prev_week_high, 2) if weekly_breakout_confirmed else None,
            "FinalAcceptedDate": final_accepted_date.date(),
            # NEW COLUMN — see comment block above
            "CloseNeverBelow10EMA": close_never_below_ema_label,
            # NEW COLUMN — data-completeness confirmation for this stock's fetch
            "DataMissingSessions": ", ".join(str(d) for d in missing_dates) if missing_dates else "None",
        })

    if not found:
        print(f"❌ {name} → No condition met")

    return results


def filter_recent_accepted(results):
    if not results:
        return results
    cutoff_date = (get_ist_now().date() - timedelta(days=RECENT_ACCEPTED_LOOKBACK_DAYS))

    kept = []
    dropped = 0
    rescued = 0
    for r in results:
        if r["FinalAcceptedDate"] >= cutoff_date:
            kept.append(r)
        elif r.get("CurrentAboveSupertrend"):
            r["KeptReason"] = "Stale accepted date, but LTP still above Daily Supertrend"
            kept.append(r)
            rescued += 1
        else:
            dropped += 1

    if dropped:
        print(f"🧹 Filtered out {dropped} row(s) with FinalAcceptedDate older than {cutoff_date} "
              f"and LTP no longer above Daily Supertrend")
    if rescued:
        print(f"🛟 Rescued {rescued} row(s) older than {cutoff_date} — LTP still above Daily Supertrend")
    return kept


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def run_all():
    results = []
    total = len(YSTOCKS)
    done = 0

    print(f"\n===== PROCESSING {total} STOCKS ({MAX_WORKERS} in parallel) =====")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_symbol = {executor.submit(process_symbol, s): s for s in YSTOCKS}
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            done += 1
            try:
                results.extend(future.result())
            except Exception as e:
                print(f"❌ {symbol} → error: {e}")
                continue
            print(f"[{done}/{total}] {symbol} done")

    # Recent-accepted filter + Daily Supertrend-based rescue for older rows
    results = filter_recent_accepted(results)

    if results:
        out = pd.DataFrame(results)
        out["StockName"] = out["StockName"].apply(convert_to_fyers)
        out = out.sort_values(by="StockName").reset_index(drop=True)
        out.to_excel(OUTPUT_FILE, index=False)
        print(f"\n✅ Saved → {OUTPUT_FILE}")

        unique = sorted(out["StockName"].unique())
        print("\n📋 STOCKS =", unique)
        with open(TXT_FILE, "w") as f:
            f.write("STOCKS = [" + ",".join(f"'{s}'" for s in unique) + ",]")
        print(f"📄 Saved TXT → {TXT_FILE}")
    else:
        print("\n❌ No stocks met condition (within recent lookback window, or rescued via Daily Supertrend)")


logging.captureWarnings(False)
warnings.showwarning = _filtered_showwarning

run_all()  # gap down 10ema code with recent accepted stocks new supertrend save