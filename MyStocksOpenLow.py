# OPEN-BELOW-PREVIOUS-DAY-LOW CHECKER — YFINANCE + NSE FALLBACK
# gap down 10ema code with accepting above day supertrend false with 10ema column
# + DeliveryPct on FinalAcceptedDate ONLY when that date is the Supertrend RED -> GREEN flip candle
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
    "requests": "requests",
})

import io
import time
import random
import threading
import requests
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

LOOKBACK_SESSIONS = 160
FETCH_CALENDAR_DAYS = 1300
EMA_LENGTH = 10
SUPERTREND_PERIOD = 14
SUPERTREND_MULTIPLIER = 2
MAX_WORKERS = 4

# How many recent calendar days to check/backfill via NSE fallback
FALLBACK_LOOKBACK_DAYS = 15

# Breakout confirmation, two paths (no weekly Supertrend / weekly high fallback anymore):
#   - Event candle passed its own-day 10EMA(Low)  → a later close within this % below
#     PrevDay High still counts (e.g. 0.0052 = 0.52%).
#   - Event candle did NOT pass own-day 10EMA(Low) → a later daily close must be
#     strictly ABOVE PrevDay High, no tolerance.
BREAKOUT_TOLERANCE_PCT = 0.0152

# only keep rows in the final output whose FinalAcceptedDate falls
# within this many days of today. Everything is still computed/logged
# exactly as before — this only filters what gets saved to Excel/TXT.
RECENT_ACCEPTED_LOOKBACK_DAYS = 120

# number of candles (including the FinalAcceptedDate candle itself)
# used for the Prev5Close and Prev5EMALines columns.
PREV_CANDLES_WINDOW = 5

# after FinalAcceptedDate, entry = NEXT candle's open. We then check
# whether price hit +9% or -9% (from that entry price) first.
ENTRY_MOVE_PCT = 0.09

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

YSTOCKS=['AARTIDRUGS.NS','AARTIIND.NS','ACI.NS','ADVENZYMES.NS','AEGISVOPAK.NS','AEQUS.NS','AEROENTER.NS','AEROFLEX.NS','AEROPLANE.NS','AFCONS.NS','AGARWALEYE.NS','AGIIL.NS','AIIL.NS','AJMERA.NS','ALEMBICLTD.NS','ALGOQUANT.NS','AMAGI.NS','ANANTRAJ.NS','ANDHRAPAP.NS','ANDHRSUGAR.NS','AONESTEELS.NS','APCOTEXIND.NS','APOLLO.NS','APOLLOPIPE.NS','APOLLOTYRE.NS','ARCIL.NS','ARFIN.NS','ARIHANTCAP.NS','ARIS.NS','ARKADE.NS','ARSSBL.NS','ARTEMISMED.NS','ARVIND.NS','ASHAPURMIN.NS','ASHOKA.NS','ASIANENE.NS','ATGL.NS','AURIONPRO.NS','AURUM.NS','AVANTEL.NS','AVTNPL.NS','AWL.NS','AYMSYNTEX.NS','BAJAJCON.NS','BAJAJELEC.NS','BAJEL.NS','BALAJITELE.NS','BALUFORGE.NS','BANSALWIRE.NS','BECTORFOOD.NS','BELRISE.NS','BEPL.NS','BERGEPAINT.NS','BHAGERIA.NS','BHARATSE.NS','BIKAJI.NS','BIOCON.NS','BIRET.NS','BIRLACABLE.NS','BLEL.NS','BLKASHYAP.NS','BLS.NS','BLSE.NS','BLUEJET.NS','BLUSPRING.NS','BODALCHEM.NS','BOMDYEING.NS','BONDADA.NS','BOROLTD.NS','BORORENEW.NS','BRIGADE.NS','BRIGHOTEL.NS','BSOFT.NS','BUILDPRO.NS','CAMLINFINE.NS','CAMPUS.NS','CANHLIFE.NS','CAPACITE.NS','CAPILLARY.NS','CAPINVIT.NS','CARRARO.NS','CASTROLIND.NS','CCAVENUE.NS','CEIGALL.NS','CELLO.NS','CENTENKA.NS','CHAMBLFERT.NS','CIEINDIA.NS','CMPDI.NS','CMRGREEN.NS','CMSINFO.NS','COHANCE.NS','CONCOR.NS','CONFIPET.NS','CORDELIA.NS','CRAMC.NS','CRIZAC.NS','CROMPTON.NS','CUPID.NS','DABUR.NS','DALMIASUG.NS','DAMCAPITAL.NS','DBREALTY.NS','DCAL.NS','DCXINDIA.NS','DECNGOLD.NS','DEEDEV.NS','DEEPA.NS','DELHIVERY.NS','DELTACORP.NS','DHAMPURSUG.NS','DIGITIDE.NS','DYCL.NS','E2E.NS','EIEL.NS','EIHOTEL.NS','EKC.NS','ELECON.NS','ELECTCAST.NS','ELGIEQUIP.NS','ELLEN.NS','EMAMILTD.NS','EMMVEE.NS','EMUDHRA.NS','ENGINERSIN.NS','EPACK.NS','EPACKPEB.NS','EPL.NS','EUREKAFORB.NS','EUROPRATIK.NS','EXICOM.NS','EXIDEIND.NS','FDC.NS','FEDDERSHOL.NS','FILATEX.NS','FINPIPE.NS','FIRSTCRY.NS','FIVESTAR.NS','FSL.NS','FUSIONCX.NS','GAEL.NS','GAJA.NS','GANDHAR.NS','GARUDA.NS','GENESYS.NS','GENUSPOWER.NS','GEOJITFSL.NS','GHCL.NS','GHCLTEXTIL.NS','GICRE.NS','GIPCL.NS','GKENERGY.NS','GKSL.NS','GLASSWALL.NS','GMDCLTD.NS','GNA.NS','GNFC.NS','GNRL.NS','GOCOLORS.NS','GODAVARIB.NS','GODIGIT.NS','GOKULAGRO.NS','GOLDIAM.NS','GOODLUCK.NS','GPIL.NS','GPPL.NS','GPTINFRA.NS','GRAUWEIL.NS','GREAVESCOT.NS','GREENPANEL.NS','GREENPLY.NS','GRMOVER.NS','GSFC.NS','GSPCROP.NS','GSPLTRANS.NS','GUFICBIO.NS','GUJENERGY.NS','GUJTHEM.NS','GYFTR.NS','HAPPSTMNDS.NS','HDFIRE.NS','HEGAM.NS','HEIDELBERG.NS','HEMIPROP.NS','HERITGFOOD.NS','HEROMOTORS.NS','HEXT.NS','HFCL.NS','HIKAL.NS','HINDCOPPER.NS','HINDOILEXP.NS','HITECH.NS','HLEGLAS.NS','HONASA.NS','HPL.NS','HSCL.NS','HUBTOWN.NS','HUHTAMAKI.NS','ICICILIFE.NS','ICIL.NS','IEX.NS','IFCI.NS','IGIL.NS','IGL.NS','IIFLCAPS.NS','INA.NS','INDIACEM.NS','INDIAGLYCO.NS','INDOCO.NS','INDRAMEDCO.NS','INDSWFTLAB.NS','INDUSINVIT.NS','INFOBEAN.NS','INOXGREEN.NS','INOXWIND.NS','INTELLECT.NS','IOLCP.NS','IONEXCHANG.NS','IRCON.NS','IRCTC.NS','IRISDOREME.NS','ITCHOTELS.NS','ITI.NS','IVALUE.NS','IXIGO.NS','JAGRAN.NS','JAGSNPHARM.NS','JAIBALAJI.NS','JAICORPLTD.NS','JAINREC.NS','JAMNAAUTO.NS','JASH.NS','JAYBARMARU.NS','JAYKAY.NS','JAYNECOIND.NS','JINDALSAW.NS','JINDRILL.NS','JKIL.NS','JKLAKSHMI.NS','JKPAPER.NS','JKTYRE.NS','JLHL.NS','JMFINANCIL.NS','JSLL.NS','JSWCEMENT.NS','JSWDULUX.NS','JSWINFRA.NS','JTLIND.NS','JUNIPER.NS','JWL.NS','JYOTHYLAB.NS','KALAMANDIR.NS','KALYANKJIL.NS','KANSAINER.NS','KARAMTARA.NS','KEC.NS','KIRIINDUS.NS','KIRLFER.NS','KIRLPNU.NS','KISSHT.NS','KLBRENG-B.NS','KMCSHIL.NS','KNACK.NS','KNRCON.NS','KOLTEPATIL.NS','KOPRAN.NS','KPEL.NS','KPITTECH.NS','KRBL.NS','KROSS.NS','KRT.NS','KUSUMGAR.NS','LALITHAA.NS','LAOPALA.NS','LATENTVIEW.NS','LCCPROJECT.NS','LCL.NS','LEAPIND.NS','LINCOLN.NS','LLOYDSENGG.NS','LLOYDSENT.NS','LOTUSDEV.NS','LTFOODS.NS','LUMINO.NS','LXCHEM.NS','MAHLIFE.NS','MAHLOG.NS','MAHSEAMLES.NS','MANAKCOAT.NS','MANALIPETC.NS','MANINFRA.NS','MARINE.NS','MARKSANS.NS','MARSONS.NS','MASTERTR.NS','MBEL.NS','MEDIASSIST.NS','MENONBE.NS','METROPOLIS.NS','MIDHANI.NS','MINDSPACE.NS','MMTC.NS','MOBIKWIK.NS','MODISONLTD.NS','MOIL.NS','MOL.NS','MOREPENLAB.NS','MOSCHIP.NS','MPIMANIPAL.NS','MRPL.NS','MSTCLTD.NS','MUNJALAU.NS','NACLIND.NS','NATIONALUM.NS','NAVA.NS','NAVKARCORP.NS','NAVNETEDUL.NS','NAZARA.NS','NBCC.NS','NCC.NS','NELCAST.NS','NEWGEN.NS','NHIT.NS','NIACL.NS','NIITLTD.NS','NIITMTS.NS','NITCO.NS','NIVABUPA.NS','NMDC.NS','NOCIL.NS','NRBBEARING.NS','NUVOCO.NS','NXST.NS','OIL.NS','ONEPOINT.NS','ORIANA.NS','ORIENTCABL.NS','ORIENTCEM.NS','ORIENTELEC.NS','ORIENTHOT.NS','OSWALPUMPS.NS','PACEDIGITK.NS','PANAMAPET.NS','PANORAMA.NS','PARACABLES.NS','PARAGMILK.NS','PARKHOSPS.NS','PARKHOTELS.NS','PASHUPATI.NS','PATANJALI.NS','PDSL.NS','PENIND.NS','PETRONET.NS','PGEL.NS','PGINVIT.NS','PINELABS.NS','PLATIND.NS','PNCINFRA.NS','POCL.NS','POWERICA.NS','PPLPHARMA.NS','PRAJIND.NS','PRAKASH.NS','PRANAV.NS','PRECWIRE.NS','PREMIERPOL.NS','PRINCEPIPE.NS','PTC.NS','PWL.NS','QUADFUTURE.NS','QUESS.NS','RAILTEL.NS','RAJOOENG.NS','RALLIS.NS','RAMBHAJO.NS','RAMCOSYS.NS','RAMKY.NS','RATNAVEER.NS','RAYMONDLSL.NS','RAYMONDREL.NS','RCF.NS','REDINGTON.NS','REDTAPE.NS','REFEX.NS','RELAXO.NS','RELIGARE.NS','RENTOMOJO.NS','RESPONIND.NS','RGL.NS','RHIM.NS','RICOAUTO.NS','RIIT.NS','RIR.NS','RITES.NS','ROLEXRINGS.NS','ROTO.NS','ROUTE.NS','RSL.NS','RSYSTEMS.NS','RUPA.NS','RUSTOMJEE.NS','RVNL.NS','SAIL.NS','SAIPARENT.NS','SAKSOFT.NS','SAMBHV.NS','SAMHI.NS','SANDHAR.NS','SANDUMA.NS','SANGHVIMOV.NS','SANSTAR.NS','SAPPHIRE.NS','SARDAEN.NS','SAREGAMA.NS','SCI.NS','SDBL.NS','SEIL.NS','SENCO.NS','SERVOTECH.NS','SESHAPAPER.NS','SETL.NS','SFL.NS','SHADOWFAX.NS','SHAKTIPUMP.NS','SHANKESH.NS','SHANTIGOLD.NS','SHAREINDIA.NS','SHIPROCKET.NS','SHK.NS','SHRINGARMS.NS','SHRIRAMPPS.NS','SHUKRAPHAR.NS','SILVERTUC.NS','SIRCA.NS','SKIPPER.NS','SKMEGGPROD.NS','SMSPHARMA.NS','SMT.NS','SOLARWORLD.NS','SONATSOFTW.NS','SPARC.NS','SPIC.NS','SPMLINFRA.NS','SPORTKING.NS','SRM.NS','SSWL.NS','STALLION.NS','STARCEMENT.NS','STARHEALTH.NS','STYL.NS','SULA.NS','SUMICHEM.NS','SUNFLAG.NS','SUNSHINE.NS','SUNTECK.NS','SURYAROSNI.NS','SUVEN.NS','SWANCORP.NS','SWIGGY.NS','SYMPHONY.NS','SYNGENE.NS','TALBROAUTO.NS','TARIL.NS','TCIEXP.NS','TECHNOCRAF.NS','TEMBO.NS','TEMPSENS.NS','TENNIND.NS','TEXINFRA.NS','TEXRAIL.NS','TFCILTD.NS','TGVSL.NS','THOMASCOOK.NS','THYROCARE.NS','TI.NS','TIMETECHNO.NS','TNPETRO.NS','TRANSRAILL.NS','TRITURBINE.NS','TRIVENI.NS','TRIVENIPT.NS','TRUALT.NS','TURTLEMINT.NS','UDS.NS','UNITEDPOLY.NS','UPL.NS','URBANCO.NS','USHAMART.NS','UTLSOLAR.NS','UTTAMSUGAR.NS','V2RETAIL.NS','VAIBHAVGBL.NS','VALIANTORG.NS','VARMORA.NS','VGUARD.NS','VIDYAWIRES.NS','VIKRAMSOLR.NS','VIKRAN.NS','VIMTALABS.NS','VINCOFE.NS','VIYASH.NS','VMARCIND.NS','VMM.NS','VRLLOG.NS','VSSL.NS','VSTIND.NS','VTL.NS','WAKEFIT.NS','WALCHANNAG.NS','WCIL.NS','WEBELSOLAR.NS','WELSPUNLIV.NS','XTRANET.NS','YATRA.NS','ZAGGLE.NS','ZEEL.NS','ZENSARTECH.NS','ZETWERK.NS']



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
    """returns (df, final_missing) — final_missing is the list of
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
# SUPERTREND (Wilder ATR) — used on DAILY candles (current-trend / rescue check)
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
    """
    Asymmetric confirmation (used for the DAILY Supertrend):

      - Flip to GREEN (raw == 1): accepted IMMEDIATELY, no confirmation wait.
      - Flip to RED   (raw == -1): held as pending (keeps old confirmed color)
        until a later close actually breaks below the break-candle's low —
        same lagged/confirmation logic as before, but ONLY for downward flips.
    """
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

        # ── Flip detected ──
        if raw == 1:
            # Flip to GREEN → accept immediately, no confirmation wait
            confirmed[i] = 1
            i += 1
            continue

        # raw == -1 → Flip to RED → apply confirmation-lag logic (as before)
        break_idx = i
        break_level = lows[break_idx]  # only the "flip to red" case reaches here now

        confirmed[break_idx] = prev_conf  # pending — hold old (green) color for now
        j = break_idx + 1
        resolved_dir = None

        while j < n:
            raw_j = raw_dir[j]
            if pd.isna(raw_j):
                confirmed[j] = prev_conf
                j += 1
                continue

            if raw_j == 1:
                # raw trend reverted back to green before the red confirmed
                # → abandon the red break entirely, snap straight to green
                resolved_dir = 1
                break

            # still raw red — check against the ORIGINAL break candle's low
            if closes[j] < break_level:
                resolved_dir = -1
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


# ─────────────────────────────────────────
# DAILY SUPERTREND ON A SPECIFIC DATE (e.g. the FinalAcceptedDate candle)
# ─────────────────────────────────────────
def get_daily_st_on_date(st_df, target_date):
    """
    Returns (supertrend_value, real_color, adjusted_color) for the daily candle on
    target_date.
      real_color     → raw Supertrend direction (what a chart indicator shows)
      adjusted_color → the confirmation-adjusted colour (confirm_supertrend_direction)
    """
    if st_df is None or st_df.empty:
        return None, "NA", "NA"
    ts = pd.Timestamp(target_date)
    if ts not in st_df.index:
        return None, "NA", "NA"
    row = st_df.loc[ts]
    val = round(float(row['Supertrend']), 2) if pd.notna(row['Supertrend']) else None
    raw_dir, conf_dir = row['ST_Direction'], row['ST_Direction_Confirmed']
    real = "NA" if pd.isna(raw_dir) else ("Green" if raw_dir == 1 else "Red")
    adj = "NA" if (conf_dir is None or pd.isna(conf_dir)) else ("Green" if conf_dir == 1 else "Red")
    return val, real, adj


# ─────────────────────────────────────────
# NEW: DID THE (REAL / RAW) DAILY SUPERTREND FLIP RED -> GREEN ON THIS CANDLE?
# ─────────────────────────────────────────
def is_st_red_to_green_flip(st_df, target_date):
    """
    True only when the candle on target_date has REAL Supertrend GREEN and the
    candle right before it had REAL Supertrend RED (i.e. the flip candle itself).
    If Supertrend was already green on the previous candle → False.
    """
    if st_df is None or st_df.empty:
        return False
    ts = pd.Timestamp(target_date)
    if ts not in st_df.index:
        return False
    pos = st_df.index.get_loc(ts)
    if pos == 0:
        return False
    cur_dir = st_df['ST_Direction'].iloc[pos]
    prev_dir = st_df['ST_Direction'].iloc[pos - 1]
    return bool(pd.notna(cur_dir) and pd.notna(prev_dir) and prev_dir == -1 and cur_dir == 1)


# ─────────────────────────────────────────
# NEW: NSE DELIVERY % (from NSE's security-wise bhavcopy, one CSV per trading day)
# ─────────────────────────────────────────
# yfinance / Fyers / jugaad stock_df don't carry delivery data. NSE publishes it in
# sec_bhavdata_full_DDMMYYYY.csv (column DELIV_PER). One file covers ALL stocks for
# that date, so we download each date once and cache it for the whole run.
_BHAV_CACHE = {}
_bhav_lock = threading.Lock()
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}


def _load_bhavcopy(date_obj):
    """Returns a DataFrame (SYMBOL, SERIES, DELIV_PER, ...) for the date, or an empty DF."""
    d = pd.Timestamp(date_obj).date()
    with _bhav_lock:
        if d in _BHAV_CACHE:
            return _BHAV_CACHE[d]

        url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        bhav = pd.DataFrame()
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=_NSE_HEADERS, timeout=20)
                if resp.status_code == 200 and resp.text.strip():
                    bhav = pd.read_csv(io.StringIO(resp.text))
                    bhav.columns = [c.strip() for c in bhav.columns]
                    for c in ("SYMBOL", "SERIES"):
                        if c in bhav.columns:
                            bhav[c] = bhav[c].astype(str).str.strip()
                    break
                elif resp.status_code == 404:
                    break  # not published (yet) for that date — no point retrying
            except Exception as e:
                print(f"⚠️ bhavcopy {d} → attempt {attempt + 1} failed: {e}")
            time.sleep(1.5)

        if bhav.empty:
            print(f"⚠️ bhavcopy {d} → not available (delivery % will be blank for that date)")
        _BHAV_CACHE[d] = bhav
        return bhav


def get_delivery_pct(symbol, date_obj):
    """Delivery % (DELIV_PER) of the EQ series for `symbol` on `date_obj`, or None."""
    bhav = _load_bhavcopy(date_obj)
    if bhav.empty or "DELIV_PER" not in bhav.columns:
        return None
    base = symbol.replace(".NS", "")
    row = bhav[(bhav["SYMBOL"] == base) & (bhav["SERIES"] == "EQ")]
    if row.empty:
        return None
    val = pd.to_numeric(row["DELIV_PER"].iloc[0], errors="coerce")
    return round(float(val), 2) if pd.notna(val) else None


# ─────────────────────────────────────────
# PREV-5-CANDLE EMA STATS (for Prev5Close / Prev5EMALines columns)
# ─────────────────────────────────────────
def compute_prev5_ema_stats(daily, accepted_date, window=PREV_CANDLES_WINDOW):
    """
    Looks at the last `window` daily candles ENDING ON (and including) the
    FinalAcceptedDate candle.

    Returns (close_count, ema_lines_label):

      close_count      → how many of those candles CLOSED above the 10 EMA
                         (10 EMA of daily Low — same line used everywhere else
                         in this script).

      ema_lines_label  → "Touched"     if EVERY candle in the window touched
                                         BOTH the 10 EMA line and the 20 EMA
                                         line (wick included, i.e. Low <= EMA <= High)
                         "Not touched" if even ONE candle failed to touch one
                                         line or both lines.
    """
    matches = daily.index[daily['Date'] == pd.Timestamp(accepted_date)]
    if len(matches) == 0:
        return None, "NA"

    end_pos = matches[0]
    start_pos = max(0, end_pos - window + 1)
    win = daily.iloc[start_pos:end_pos + 1]

    close_count = 0
    all_touched = True

    for _, c in win.iterrows():
        ema10 = c['EMA10_Low_Full']
        ema20 = c['EMA20_Low_Full']

        # Close above 10 EMA?
        if pd.notna(ema10) and c['Close'] > ema10:
            close_count += 1

        # Touched BOTH lines (wick included)?
        if pd.isna(ema10) or pd.isna(ema20):
            all_touched = False
            continue
        touched_10 = c['Low'] <= ema10 <= c['High']
        touched_20 = c['Low'] <= ema20 <= c['High']
        if not (touched_10 and touched_20):
            all_touched = False

    return close_count, ("Touched" if all_touched else "Not touched")


# ─────────────────────────────────────────
# ENTRY MOVE CHECK (UpDownMove column)
# ─────────────────────────────────────────
def _days_txt(n):
    return f"{n} day" if n == 1 else f"{n} days"


def compute_entry_move(daily, accepted_date, move_pct=ENTRY_MOVE_PCT):
    """
    Entry = OPEN of the candle right AFTER the FinalAcceptedDate candle.
    From that entry price we look for the first +9% (High >= entry*1.09)
    and the first -9% (Low <= entry*0.91), using each candle's intraday High / Low.
    Day 1 = the entry candle itself.

    Returns (label, entry_date, entry_price, up_hit_date, down_hit_date):

      label is one of
        "UP in N days"                               → +9% hit first
        "DOWN in N days, recovered in M days"        → -9% hit first, then +9% (from entry)
                                                       was hit M trading days AFTER the down hit
        "DOWN in N days, not recovered yet"          → -9% hit first, +9% not hit yet
        "BOTH hit same day (N), order unclear"       → daily candle touched both levels
        "No 9% move yet (N days)"                    → neither level hit so far
        "Pending - no entry candle yet"              → accepted candle is the latest candle

      entry_date / entry_price / up_hit_date / down_hit_date are returned so the
      Excel sheet can show exactly what the 9% was measured from (audit columns).
    """
    matches = daily.index[daily['Date'] == pd.Timestamp(accepted_date)]
    if len(matches) == 0:
        return "NA", None, None, None, None

    entry_pos = matches[0] + 1
    if entry_pos >= len(daily):
        return "Pending - no entry candle yet", None, None, None, None

    entry_date = daily['Date'].iloc[entry_pos].date()
    entry_price = float(daily['Open'].iloc[entry_pos])
    up_level = entry_price * (1 + move_pct)
    down_level = entry_price * (1 - move_pct)
    EPS = 1e-9  # float safety: e.g. 100*1.09 = 109.00000000000001, so a High of exactly 109.00 must still count

    up_day, down_day = None, None
    up_hit_date, down_hit_date = None, None
    for k in range(entry_pos, len(daily)):
        day_n = k - entry_pos + 1
        if up_day is None and daily['High'].iloc[k] >= up_level - EPS:
            up_day = day_n
            up_hit_date = daily['Date'].iloc[k].date()
        if down_day is None and daily['Low'].iloc[k] <= down_level + EPS:
            down_day = day_n
            down_hit_date = daily['Date'].iloc[k].date()
        if up_day is not None and down_day is not None:
            break  # both levels found — nothing more to scan

    total_days = len(daily) - entry_pos
    details = (entry_date, round(entry_price, 2), up_hit_date, down_hit_date)

    if up_day is None and down_day is None:
        return (f"No {move_pct*100:.0f}% move yet ({_days_txt(total_days)})",) + details

    if up_day is not None and down_day is not None and up_day == down_day:
        return (f"BOTH hit same day ({_days_txt(up_day)}), order unclear",) + details

    if down_day is None or (up_day is not None and up_day < down_day):
        return (f"UP in {_days_txt(up_day)}",) + details

    # down hit first
    if up_day is None:
        return (f"DOWN in {_days_txt(down_day)}, not recovered yet",) + details
    return (f"DOWN in {_days_txt(down_day)}, recovered in {_days_txt(up_day - down_day)}",) + details


# ─────────────────────────────────────────
# CONDITION CHECKER
# ─────────────────────────────────────────
def evaluate_conditions(df, name, live_ltp=None, market_open_now=None, missing_dates=None):
    results = []
    found = False

    if market_open_now is None:
        market_open_now = is_market_open()

    # data-completeness confirmation string, driven by whatever
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

    # NOTE: weekly candles / weekly Supertrend fallback REMOVED — event acceptance
    # is purely daily (own-day 10EMA(Low) path + strict daily-breakout path).

    daily_st = (
        compute_supertrend(
            daily_closed.set_index('Date')[['Open', 'High', 'Low', 'Close']],
            SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER
        )
        if len(daily_closed) >= SUPERTREND_PERIOD else None
    )
    if daily_st is not None:
        daily_st = confirm_supertrend_direction(daily_st)  # filter false daily ST breakouts (asymmetric)

    current_st_val, current_st_color, current_above_st = None, "NA", False
    if daily_st is not None and not daily_st.empty:
        last_daily_st_row = daily_st.iloc[-1]
        if pd.notna(last_daily_st_row['Supertrend']) and pd.notna(last_daily_st_row['ST_Direction_Confirmed']):
            current_st_val = last_daily_st_row['Supertrend']
            current_st_color = "Green" if last_daily_st_row['ST_Direction_Confirmed'] == 1 else "Red"
            current_above_st = last_daily_st_row['ST_Direction_Confirmed'] == 1

    # REAL (raw, unadjusted) daily Supertrend colour of the latest closed candle —
    # straight from compute_supertrend()'s ST_Direction, NOT the lag-adjusted
    # ST_Direction_Confirmed used for CurrentDailySupertrendColor / rescue logic above.
    current_st_real_color = "NA"
    if daily_st is not None and not daily_st.empty:
        _raw_dir_last = daily_st.iloc[-1]['ST_Direction']
        if pd.notna(_raw_dir_last):
            current_st_real_color = "Green" if _raw_dir_last == 1 else "Red"

    current_passes_ema = pd.notna(current_ema_ref) and current_price > current_ema_ref
    crossed_label = "LTP crossed 10EMA ✅" if current_passes_ema else "LTP NOT crossed 10EMA ❌"
    ema_ref_str = f"{current_ema_ref:.2f}" if pd.notna(current_ema_ref) else "NA"
    current_st_str = f"{current_st_val:.2f}({current_st_color})" if current_st_val is not None else "NA"
    above_st_label = "LTP above Daily ST ✅" if current_above_st else "LTP NOT above Daily ST ❌"

    # NOTE: stock-level EMA gate removed — we go straight into the gap-down
    # condition loop below, where the per-event own-day 10EMA check + daily breakout still apply.
    print(
        f"▶️ {name} → LTP={current_price:.2f} ({price_source}) | "
        f"10EMA(Low) as of {ema_ref_date.date() if ema_ref_date is not None else 'NA'}={ema_ref_str} | {crossed_label} | "
        f"CurrentDailyST={current_st_str} | {above_st_label} | DataCheck: {data_status}"
    )

    # Causal (no future leakage) 10EMA(Low) series, for per-event checks
    daily['EMA10_Low_Full'] = daily['Low'].ewm(span=EMA_LENGTH, adjust=False, min_periods=EMA_LENGTH).mean()
    # Causal 20EMA(Low) series, used for the post-acceptance integrity check
    # AND for the Prev5EMALines touch check
    daily['EMA20_Low_Full'] = daily['Low'].ewm(span=20, adjust=False, min_periods=20).mean()
    # Supertrend over the FULL daily history so we can read its colour on the
    # FinalAcceptedDate candle of each event (the "Current..." columns only describe
    # the LATEST candle, not the accepted date).
    daily_st_full = None
    if len(daily) >= SUPERTREND_PERIOD:
        daily_st_full = compute_supertrend(
            daily.set_index('Date')[['Open', 'High', 'Low', 'Close']],
            SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER
        )
        daily_st_full = confirm_supertrend_direction(daily_st_full)
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

        # Acceptance is purely daily (no weekly Supertrend / weekly high fallback), and the
        # acceptance candle must also have REAL daily Supertrend GREEN (see scan below):
        #   PATH A — event candle's price is above its own-day 10EMA(Low):
        #            breakout needs a later close >= PrevDay High minus the tolerance band.
        #   PATH B — event candle's price is NOT above its own-day 10EMA(Low):
        #            still accepted through daily breakout, but a later close must be
        #            strictly ABOVE PrevDay High (no tolerance).
        if event_passes_ema:
            ema_pct = round((event_price / event_ema - 1) * 100, 2)
            confirmed_by = f"own-day 10EMA(Low) ({ema_pct:+.2f}%)"
            breakout_threshold = prev_row['High'] * (1 - BREAKOUT_TOLERANCE_PCT)
            use_tolerance = True
        else:
            confirmed_by = f"daily close above PrevDay High (own-day 10EMA={event_ema_str} not passed, strict)"
            breakout_threshold = prev_row['High']
            use_tolerance = False

        # The acceptance candle must satisfy BOTH:
        #   (1) the price condition (close within tolerance of / above PrevDay High — per path above)
        #   (2) REAL (raw, unadjusted) daily Supertrend == GREEN on that same candle
        # If the price condition is met while Supertrend is still red, that candle is NOT accepted;
        # we keep scanning and accept the first later candle that meets the price condition
        # while Supertrend is green.
        breakout_confirmed, breakout_date, breakout_close = False, None, None
        first_price_met_date = None   # first candle that met the price condition (any Supertrend colour)
        st_blocked_dates = []         # candles that met the price condition but real Supertrend was red
        for j in range(i + 1, len(daily)):
            later_row = daily.iloc[j]
            price_ok = (later_row['Close'] >= breakout_threshold) if use_tolerance else (later_row['Close'] > breakout_threshold)
            if not price_ok:
                continue

            if first_price_met_date is None:
                first_price_met_date = later_row['Date']

            st_dir_j = daily_st_full['ST_Direction'].iloc[j] if daily_st_full is not None else float('nan')
            if pd.notna(st_dir_j) and st_dir_j == 1:
                breakout_confirmed = True
                breakout_date = later_row['Date']
                breakout_close = later_row['Close']
                break

            st_blocked_dates.append(later_row['Date'].date())

        if not breakout_confirmed:
            if st_blocked_dates:
                print(
                    f"⛔ {name} → {today_date.date()} price condition met on {st_blocked_dates[0]} "
                    f"({len(st_blocked_dates)} candle(s)) but real Supertrend was RED on all of them — not accepted"
                )
            elif use_tolerance:
                print(
                    f"⛔ {name} → {today_date.date()} confirmed, but price never recovered within "
                    f"{BREAKOUT_TOLERANCE_PCT*100:.2f}% of PrevDay High={prev_row['High']:.2f} — skipping"
                )
            else:
                print(
                    f"⛔ {name} → {today_date.date()} gap-down REJECTED | Price={event_price:.2f} vs EMA={event_ema_str} "
                    f"and no close above PrevDay High={prev_row['High']:.2f}"
                )
            continue

        if st_blocked_dates:
            print(
                f"⏳ {name} → {today_date.date()} price condition first met on {first_price_met_date.date()} "
                f"but real Supertrend was RED — accepted later on {breakout_date.date()} when Supertrend was GREEN"
            )

        breakout_pct_vs_high = round((breakout_close / prev_row['High'] - 1) * 100, 2)

        # Whether the confirming candle closed strictly above PrevDay High,
        # or only landed within the accepted tolerance band below it.
        if breakout_close > prev_row['High']:
            tag = f"Closed ABOVE ({breakout_pct_vs_high:+.2f}%)"
        else:
            tag = f"Closed NEAR BY ({breakout_pct_vs_high:+.2f}%)"


        stage2_icon = "✅" if event_passes_ema else "📌"
        print(
            f"{stage2_icon} {name} → Event on {today_date.date()} | Confirmed by: {confirmed_by} | "
            f"Breakout: {tag} PrevDay High on {breakout_date.date()} | "
            f"EMA Distance ({'Close' if condition_candle_color == 'Red' else 'Open'} vs EMA)="
            f"{ema_distance_pct if ema_distance_pct is not None else 'NA'}% | "
            f"Candle={condition_candle_color}"
        )


        # PATH A: confirmed on the condition candle itself; PATH B: only the breakout close
        # confirms it. Either way breakout_date >= condition date, so max() gives the later one.
        stage2_date = today_date if event_passes_ema else breakout_date
        final_accepted_date = max(stage2_date, breakout_date)
        print(
            f"🏁 {name} → FINAL ACCEPTED on {final_accepted_date.date()} and "
            f"condition date: {today_date.date()} | Data: {data_status}"
        )

        close_never_below_ema = True
        for k in range(i + 1, len(daily)):
            ema_chk = daily['EMA20_Low_Full'].iloc[k]
            close_chk = daily['Close'].iloc[k]
            if pd.notna(ema_chk) and close_chk < ema_chk:
                close_never_below_ema = False
                break
        close_never_below_ema_label = "Yes" if close_never_below_ema else "No"

        # last 5 candles ending on (and including) the FinalAcceptedDate candle
        #   prev5_close_count → how many closed above the 10 EMA
        #   prev5_ema_lines   → "Touched" only if all 5 candles touched BOTH 10 & 20 EMA
        #                       (wick included), otherwise "Not touched"
        prev5_close_count, prev5_ema_lines = compute_prev5_ema_stats(daily, final_accepted_date)
        print(
            f"📊 {name} → Prev{PREV_CANDLES_WINDOW} candles up to {final_accepted_date.date()} | "
            f"Closed above 10EMA: {prev5_close_count}/{PREV_CANDLES_WINDOW} | EMA lines: {prev5_ema_lines}"
        )

        # entry at next candle's open after FinalAcceptedDate → +9% or -9% first?
        (up_down_move, entry_date_val, entry_price_val,
         up_hit_date_val, down_hit_date_val) = compute_entry_move(daily, final_accepted_date)

        # Supertrend on the FinalAcceptedDate candle itself
        acc_st_val, acc_st_real_color, acc_st_adj_color = get_daily_st_on_date(daily_st_full, final_accepted_date)

        # NEW: was the FinalAcceptedDate candle the REAL Supertrend RED -> GREEN flip candle?
        # Only then do we fetch delivery % (otherwise stays blank — e.g. ST was already green).
        st_flip_on_accepted = is_st_red_to_green_flip(daily_st_full, final_accepted_date)
        delivery_pct_val = None
        if st_flip_on_accepted:
            delivery_pct_val = get_delivery_pct(name, final_accepted_date)
            print(
                f"🔄 {name} → Supertrend flipped RED→GREEN on FinalAcceptedDate {final_accepted_date.date()} | "
                f"Delivery% = {delivery_pct_val if delivery_pct_val is not None else 'NA'}"
            )

        # EntryPrice / ExitPrice only for trades that ended in profit:
        #   - "UP in N days"                         → exit at the +9% level
        #   - "DOWN in N days, recovered in M days"  → exit at the +9% level (after recovering)
        # Everything else (down not recovered, no move yet, both same day, pending) stays blank.
        is_up_or_recovered = up_down_move.startswith("UP in") or ", recovered in" in up_down_move
        if is_up_or_recovered and entry_price_val is not None:
            trade_entry_price = entry_price_val
            trade_exit_price = round(entry_price_val * (1 + ENTRY_MOVE_PCT), 2)
        else:
            trade_entry_price = None
            trade_exit_price = None

        # how many trading days (candles) before the FinalAcceptedDate the
        # condition (gap-down) candle formed — e.g. "10 days ago", "1 day ago".
        accepted_pos_list = daily.index[daily['Date'] == pd.Timestamp(final_accepted_date)]
        if len(accepted_pos_list) > 0:
            formation_days = int(accepted_pos_list[0] - i)
            if formation_days <= 0:
                condition_formation_label = "Same day"
            elif formation_days == 1:
                condition_formation_label = "1 day ago"
            else:
                condition_formation_label = f"{formation_days} days ago"
        else:
            condition_formation_label = "NA"

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
            # current (latest, not event-day) DAILY Supertrend context —
            # used by filter_recent_accepted() to rescue stale-but-still-trending rows.
            "CurrentDailySupertrend": round(current_st_val, 2) if current_st_val is not None else None,
            "CurrentDailySupertrendColor": current_st_color,
            # real/raw daily Supertrend colour (no confirmation adjustment)
            "CurrentDailySupertrendRealColor": current_st_real_color,
            # Supertrend as of the FinalAcceptedDate candle (value, real/raw colour, adjusted colour)
            "AcceptedDateSupertrend": acc_st_val,
            "AcceptedDateSTRealColor": acc_st_real_color,
            "AcceptedDateSTColor": acc_st_adj_color,
            # NEW COLUMN — "Yes" only if FinalAcceptedDate candle is the real Supertrend RED→GREEN flip candle
            "AcceptedDateSTFlipRedToGreen": "Yes" if st_flip_on_accepted else "No",
            # NEW COLUMN — NSE delivery % on FinalAcceptedDate; filled ONLY on the RED→GREEN flip, else blank
            "DeliveryPct": delivery_pct_val,
            "CurrentAboveSupertrend": current_above_st,
            "EventCandlePrice": round(event_price, 2),
            "EventDay10EMA_Low": round(event_ema, 2) if pd.notna(event_ema) else None,
            "EMADistancePct": ema_distance_pct,
            "EMADistanceBasis": "Close" if condition_candle_color == "Red" else "Open",
            "ConfirmedBy": confirmed_by,
            "PrevDayHigh": round(prev_row['High'], 2),
            # first candle that met the price condition, even if Supertrend was red then
            "FirstPriceMetDate": first_price_met_date.date() if first_price_met_date is not None else None,
            "BreakoutDate": breakout_date.date(),
            "BreakoutClose": round(breakout_close, 2),
            "BreakoutPctVsPrevHigh": breakout_pct_vs_high,
            "BreakoutType": "Closed Above" if breakout_close > prev_row['High'] else f"Closed Near By ({BREAKOUT_TOLERANCE_PCT*100:.2f}%)",
            "FinalAcceptedDate": final_accepted_date.date(),
            "CloseNeverBelow10EMA": close_never_below_ema_label,
            # data-completeness confirmation for this stock's fetch
            "DataMissingSessions": ", ".join(str(d) for d in missing_dates) if missing_dates else "None",
            # number of candles (out of the last 5, accepted candle included) that closed above 10 EMA
            "Prev5Close": prev5_close_count,
            # "Touched" if all 5 candles touched BOTH 10 & 20 EMA (wick included), else "Not touched"
            "Prev5EMALines": prev5_ema_lines,
            # entry = next candle open after FinalAcceptedDate; which 9% level hit first
            "UpDownMove": up_down_move,
            # AUDIT COLUMNS — what the 9% was measured from, and when each level was touched
            "UpDownEntryDate": entry_date_val,
            # EntryPrice / ExitPrice — only filled for UP or recovered rows (exit = entry +9%)
            "EntryPrice": trade_entry_price,
            "ExitPrice": trade_exit_price,
            "Up9HitDate": up_hit_date_val,
            "Down9HitDate": down_hit_date_val,
            # trading days between the condition candle and the FinalAcceptedDate candle
            "ConditionFormationDays": condition_formation_label,
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