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
from collections import defaultdict
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
# Per-stock summary: accepted + rejected dates together
SUMMARY_FILE = f"{BASE_DIR}/stock_date_summary.txt"

LOOKBACK_SESSIONS = 160
FETCH_CALENDAR_DAYS = 1300
EMA_LENGTH = 10
SUPERTREND_PERIOD = 14
SUPERTREND_MULTIPLIER = 2
MAX_WORKERS = 4

# How many recent calendar days to check/backfill via NSE fallback
FALLBACK_LOOKBACK_DAYS = 15

# Breakout confirmation no longer requires a strict close ABOVE PrevDay
# High. A later close that lands within this % below PrevDay High still
# counts as a confirmed breakout (e.g. 0.004 = 0.4%).
BREAKOUT_TOLERANCE_PCT = 0.0058

# Only keep rows in the final output whose FinalAcceptedDate falls
# within this many days of today. Everything is still computed/logged
# exactly as before — this only filters what gets saved to Excel/TXT.
RECENT_ACCEPTED_LOOKBACK_DAYS = 120

# Previous-week same-day check settings
# Minimum number of candles that must close above the 10 or 20 EMA between
# the previous-week reference candle and the accepted candle.
MIN_CANDLES_ABOVE_EMA = 3
# False → count only candles strictly BETWEEN the reference candle and the
#         accepted candle (both end candles excluded).
# True  → also count the reference candle and the accepted candle themselves.
COUNT_BOUNDARY_CANDLES = True

# ── NEW: TRADE SIMULATION SETTINGS ──
# Entry = OPEN of the next trading session after FinalAcceptedDate.
# Then walk forward candle by candle (starting from the entry day itself)
# until price moves +TARGET_PCT (TG) or -STOP_PCT (SL) from the entry price.
TARGET_PCT = 0.09   # +9%
STOP_PCT = 0.09     # -9%
# If a single DAILY candle touches BOTH TG and SL, daily data can't tell
# which came first. "SL" = conservative (assume SL hit first), "TG" = optimistic.
SAME_DAY_BOTH_HIT_ASSUME = "SL"

# ─────────────────────────────────────────
# REJECTED-EVENT TRACKER (thread-safe, filled while scanning)
# ─────────────────────────────────────────
REJECTED_EVENTS = []
_rejected_lock = threading.Lock()


def record_rejection(stock, condition_date, stage, reason, accepted_date=None, trade_note=None):
    """Remember every rejected gap-down event so it can be shown in the final summary.
    trade_note (optional) = TG/SL outcome text, used for rows that were accepted
    but then filtered out (e.g. Recent-date filter)."""
    with _rejected_lock:
        REJECTED_EVENTS.append({
            "StockName": stock,
            "ConditionMetDate": condition_date,
            "Stage": stage,
            "Reason": reason,
            "AcceptedDate": accepted_date,
            "TradeNote": trade_note,
        })


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
    """Returns (df, final_missing) — final_missing is the list of
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
    """
    Asymmetric confirmation (applies to BOTH daily and weekly Supertrend,
    since both call sites route through this same function):

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


def get_weekly_supertrend_row(weekly_st, target_date):
    valid_idx = weekly_st.index[weekly_st.index <= target_date]
    if len(valid_idx) == 0:
        return None
    return weekly_st.loc[valid_idx[-1]]


# ─────────────────────────────────────────
# PREVIOUS-WEEK SAME-DAY CHECK + CANDLES-ABOVE-EMA CHECK
# ─────────────────────────────────────────
def check_prev_week_same_day(daily, accepted_date):
    """
    Runs AFTER a stock's FinalAcceptedDate is known.

    Step 1 — reference candle:
        Take the same weekday one week before the accepted date (accepted
        Monday -> previous Monday). If that day has no candle (holiday /
        missing), use the next available trading day (e.g. Tuesday).
        (a) Ref candle CLOSED above 10EMA(Low)                      -> pass
        (b) Ref candle closed BELOW 10EMA -> fallback test:
              - candle must have OPENED below the 10 or 20 EMA, and
              - candle range (wick included) may touch at most ONE of the
                two EMA lines. Touching BOTH lines -> reject.

    Step 2 — between the ref candle and the accepted candle, at least
        MIN_CANDLES_ABOVE_EMA candles must have closed above the 10 or 20 EMA
        (i.e. above at least one of the two lines).

    Returns (passed: bool, info: dict, reason: str)
    """
    info = {"ref_date": None, "ref_check": None, "candles_above": None}

    accepted_ts = pd.Timestamp(accepted_date).normalize()
    acc_match = daily.index[daily['Date'] == accepted_ts]
    if len(acc_match) == 0:
        return False, info, "accepted date not found in daily data"
    acc_idx = acc_match[0]

    # ── Step 1: find the previous-week same-day reference candle ──
    target = accepted_ts - timedelta(days=7)
    ref_candidates = daily.index[(daily['Date'] >= target) & (daily['Date'] < accepted_ts)]
    if len(ref_candidates) == 0:
        return False, info, f"no previous-week candle found on/after {target.date()}"
    ref_idx = ref_candidates[0]  # same day if present, else next trading day
    ref = daily.loc[ref_idx]
    ref_date = ref['Date']
    info["ref_date"] = ref_date.date()

    e10 = ref['EMA10_Low_Full']
    e20 = ref['EMA20_Low_Full']
    if pd.isna(e10) or pd.isna(e20):
        return False, info, f"EMA not available on ref candle {ref_date.date()}"

    if ref['Close'] > e10:
        info["ref_check"] = f"Ref {ref_date.date()} closed above 10EMA"
    else:
        opened_below = ref['Open'] < max(e10, e20)  # opened below the 10 or 20 EMA line
        touches = [name for name, e in (("10EMA", e10), ("20EMA", e20)) if ref['Low'] <= e <= ref['High']]

        if not opened_below:
            return False, info, (
                f"ref {ref_date.date()} closed below 10EMA and did NOT open below 10/20EMA "
                f"(Open={ref['Open']:.2f}, 10EMA={e10:.2f}, 20EMA={e20:.2f})"
            )
        if len(touches) >= 2:
            return False, info, (
                f"ref {ref_date.date()} closed below 10EMA and touched BOTH EMA lines "
                f"(H={ref['High']:.2f}, L={ref['Low']:.2f}, 10EMA={e10:.2f}, 20EMA={e20:.2f})"
            )
        touch_txt = touches[0] if touches else "no EMA line"
        info["ref_check"] = f"Ref {ref_date.date()} fallback OK (opened below, touched {touch_txt})"

    # ── Step 2: count candles closing above 10 or 20 EMA between ref and accepted ──
    if COUNT_BOUNDARY_CANDLES:
        segment = daily.loc[ref_idx:acc_idx]
    else:
        segment = daily.loc[ref_idx + 1: acc_idx - 1]

    lower_ema = segment[['EMA10_Low_Full', 'EMA20_Low_Full']].min(axis=1, skipna=False)
    above_mask = segment['Close'] > lower_ema  # above at least one of the two lines
    candles_above = int(above_mask.sum())
    info["candles_above"] = candles_above

    if candles_above < MIN_CANDLES_ABOVE_EMA:
        return False, info, (
            f"only {candles_above} candle(s) closed above 10/20EMA between "
            f"{ref_date.date()} and {accepted_ts.date()} (need {MIN_CANDLES_ABOVE_EMA})"
        )

    return True, info, "ok"


# ─────────────────────────────────────────
# NEW: TRADE SIMULATION (entry next-day open, first to hit +TG% / -SL%)
# ─────────────────────────────────────────
def simulate_trade(daily, accepted_date):
    """
    Entry  = OPEN of the first trading session AFTER accepted_date.
    Then walk forward candle by candle (starting with the entry candle itself)
    and see which gets touched first:
        TG = entry * (1 + TARGET_PCT)   -> candle High >= TG
        SL = entry * (1 - STOP_PCT)     -> candle Low  <= SL
    If one daily candle touches BOTH, the order is unknowable from daily data,
    so SAME_DAY_BOTH_HIT_ASSUME decides (default: SL first, conservative).

    Returns dict:
        status      : "TG Hit" | "SL Hit" | "Waiting for TG/SL Hit" | "Waiting for entry"
        entry_date, entry_price, tg_price, sl_price, result_date, text
    """
    out = {
        "status": None, "entry_date": None, "entry_price": None,
        "tg_price": None, "sl_price": None, "result_date": None, "text": None,
    }

    acc_ts = pd.Timestamp(accepted_date).normalize()
    after = daily[daily['Date'] > acc_ts]

    # Accepted on the latest available candle → entry session hasn't started yet
    if after.empty:
        out["status"] = "Waiting for entry"
        out["text"] = "waiting for entry (next session open)"
        return out

    entry_row = after.iloc[0]
    entry_price = float(entry_row['Open'])
    tg_price = entry_price * (1 + TARGET_PCT)
    sl_price = entry_price * (1 - STOP_PCT)

    out["entry_date"] = entry_row['Date'].date()
    out["entry_price"] = round(entry_price, 2)
    out["tg_price"] = round(tg_price, 2)
    out["sl_price"] = round(sl_price, 2)

    for _, row in after.iterrows():
        hit_tg = row['High'] >= tg_price
        hit_sl = row['Low'] <= sl_price

        if hit_tg and hit_sl:
            both_note = f" (same-day TG+SL, {SAME_DAY_BOTH_HIT_ASSUME} assumed)"
            if SAME_DAY_BOTH_HIT_ASSUME == "TG":
                out["status"] = "TG Hit"
                out["result_date"] = row['Date'].date()
                out["text"] = f"TG Hit on {row['Date'].date()}{both_note}"
            else:
                out["status"] = "SL Hit"
                out["result_date"] = row['Date'].date()
                out["text"] = f"SL Hit on {row['Date'].date()}{both_note}"
            return out

        if hit_tg:
            out["status"] = "TG Hit"
            out["result_date"] = row['Date'].date()
            out["text"] = f"TG Hit on {row['Date'].date()}"
            return out

        if hit_sl:
            out["status"] = "SL Hit"
            out["result_date"] = row['Date'].date()
            out["text"] = f"SL Hit on {row['Date'].date()}"
            return out

    out["status"] = "Waiting for TG/SL Hit"
    out["text"] = "waiting for TG/SL Hit"
    return out


# ─────────────────────────────────────────
# CONDITION CHECKER
# ─────────────────────────────────────────
def evaluate_conditions(df, name, live_ltp=None, market_open_now=None, missing_dates=None):
    results = []
    found = False

    if market_open_now is None:
        market_open_now = is_market_open()

    # Data-completeness confirmation string, driven by whatever
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
        weekly_st = confirm_supertrend_direction(weekly_st)  # filter false weekly ST breakouts (asymmetric)


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
    # Causal 20EMA(Low) series, used for the post-acceptance integrity check
    # AND the previous-week same-day check
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


        # Fallback uses a normal DAILY close above the condition candle's
        # previous-day high (prev_row['High']), NOT the weekly candle high.
        # Strict "above" here; the near-by tolerance only applies to the later
        # breakout check below.
        daily_fallback_confirmed = False
        daily_fallback_date = None
        daily_fallback_close = None

        if not event_confirmed:
            for j in range(i + 1, len(daily)):
                later_row = daily.iloc[j]
                if later_row['Close'] > prev_row['High']:
                    daily_fallback_confirmed = True
                    daily_fallback_date = later_row['Date']
                    daily_fallback_close = later_row['Close']
                    break

            if daily_fallback_confirmed:
                event_confirmed = True
                daily_fallback_pct = round((daily_fallback_close / prev_row['High'] - 1) * 100, 2)
                confirmed_by = (
                    f"daily close above PrevDay High ({prev_row['High']:.2f}) on "
                    f"{daily_fallback_date.date()} ({daily_fallback_pct:+.2f}%)"
                )

        if not event_confirmed:
            print(f"⛔ {name} → {today_date.date()} gap-down REJECTED | Price={event_price:.2f} vs EMA={event_ema_str} | {event_st_info} | no later daily close above PrevDay High={prev_row['High']:.2f}")
            record_rejection(
                name, today_date.date(), "Gap-down not confirmed",
                f"price {event_price:.2f} vs 10EMA {event_ema_str}, {event_st_info}, "
                f"no later close above PrevDay High {prev_row['High']:.2f}"
            )
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
            record_rejection(
                name, today_date.date(), "No breakout",
                f"confirmed by {confirmed_by}, but price never closed within "
                f"{BREAKOUT_TOLERANCE_PCT*100:.2f}% of PrevDay High {prev_row['High']:.2f}"
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


        # Stage-2 date comes from the daily fallback date when own-day EMA / weekly ST didn't confirm
        stage2_date = today_date if (event_passes_ema or event_passes_st) else daily_fallback_date
        final_accepted_date = max(stage2_date, breakout_date)

        # ── Previous-week same-day reference candle + candles-above-EMA check ──
        pw_passed, pw_info, pw_reason = check_prev_week_same_day(daily, final_accepted_date)
        if not pw_passed:
            print(
                f"⛔ {name} → accepted on {final_accepted_date.date()} and condition date: {today_date.date()} but REJECTED by "
                f"prev-week same-day check: {pw_reason}"
            )
            record_rejection(
                name, today_date.date(), "Prev-week check", pw_reason,
                accepted_date=final_accepted_date.date()
            )
            continue

        print(
            f"🗓️ {name} → prev-week check passed | {pw_info['ref_check']} | "
            f"{pw_info['candles_above']} candle(s) closed above 10/20EMA between "
            f"{pw_info['ref_date']} and {final_accepted_date.date()}"
        )

        # FINAL ACCEPTED print includes the data-completeness confirmation (data_status),
        # computed once above from fetch_yfinance()/fill_missing_trading_days() — e.g.
        # "GNFC.NS → FINAL ACCEPTED on 2026-07-27 | Data: no missing data ✅"
        print(f"🏁 {name} → FINAL ACCEPTED on {final_accepted_date.date()} and condition date: {today_date.date()} | Data: {data_status}")

        # ── NEW: trade simulation — entry next-day open, first to hit +9% / -9% ──
        trade = simulate_trade(daily, final_accepted_date)
        if trade["entry_date"] is not None:
            print(
                f"💹 {name} → Entry {trade['entry_date']} @ {trade['entry_price']:.2f} | "
                f"TG={trade['tg_price']:.2f} (+{TARGET_PCT*100:.0f}%) SL={trade['sl_price']:.2f} (-{STOP_PCT*100:.0f}%) | "
                f"{trade['text']}"
            )
        else:
            print(f"💹 {name} → {trade['text']}")

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
            # Current (latest, not event-day) DAILY Supertrend context —
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
            # Daily fallback columns (replace the old weekly fallback columns)
            "DailyFallbackUsed": daily_fallback_confirmed,
            "DailyFallbackDate": daily_fallback_date.date() if daily_fallback_confirmed else None,
            "DailyFallbackClose": round(daily_fallback_close, 2) if daily_fallback_confirmed else None,
            "FinalAcceptedDate": final_accepted_date.date(),
            "CloseNeverBelow10EMA": close_never_below_ema_label,
            "DataMissingSessions": ", ".join(str(d) for d in missing_dates) if missing_dates else "None",
            # Previous-week same-day check results
            "PrevWeekRefDate": pw_info["ref_date"],
            "PrevWeekRefCheck": pw_info["ref_check"],
            "CandlesAboveEMACount": pw_info["candles_above"],
            # NEW: trade simulation results (entry = next session open after FinalAcceptedDate)
            "EntryDate": trade["entry_date"],
            "EntryPrice": trade["entry_price"],
            "TargetPrice": trade["tg_price"],
            "StopPrice": trade["sl_price"],
            "TradeStatus": trade["status"],
            "TradeResultDate": trade["result_date"],
            "TradeSummary": trade["text"],
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
            # Filtered-out rows still carry their TG/SL outcome into the summary
            record_rejection(
                r["StockName"], r["ConditionMetDate"], "Recent-date filter",
                f"accepted date {r['FinalAcceptedDate']} older than {cutoff_date} "
                f"and LTP not above Daily Supertrend",
                accepted_date=r["FinalAcceptedDate"],
                trade_note=r.get("TradeSummary")
            )

    if dropped:
        print(f"🧹 Filtered out {dropped} row(s) with FinalAcceptedDate older than {cutoff_date} "
              f"and LTP no longer above Daily Supertrend")
    if rescued:
        print(f"🛟 Rescued {rescued} row(s) older than {cutoff_date} — LTP still above Daily Supertrend")
    return kept


# ─────────────────────────────────────────
# PER-STOCK SUMMARY — ACCEPTED + REJECTED DATES TOGETHER (with TG/SL outcome)
# ─────────────────────────────────────────
def build_stock_date_summary(accepted_rows, rejected_rows):
    """
    One line per stock:
      {NSE:XYZ-EQ: ✅ Met -> Accepted (RefDate), TG Hit on date | ❌ Met -> Accepted [stage] reason, SL Hit on date}  # N accepted, M rejected
    Only stocks that had at least one gap-down event appear.
    """
    accepted_by = defaultdict(list)
    for r in accepted_rows:
        accepted_by[convert_to_fyers(r["StockName"])].append(r)

    rejected_by = defaultdict(list)
    for r in rejected_rows:
        rejected_by[convert_to_fyers(r["StockName"])].append(r)

    lines = []
    for stock in sorted(set(accepted_by) | set(rejected_by)):
        acc = sorted(accepted_by.get(stock, []), key=lambda r: (r["ConditionMetDate"], r["FinalAcceptedDate"]))
        rej = sorted(rejected_by.get(stock, []), key=lambda r: (r["ConditionMetDate"], r["Stage"]))

        acc_txt = ", ".join(
            f"{r['ConditionMetDate']} -> {r['FinalAcceptedDate']} ({r['PrevWeekRefDate']}), "
            f"{r.get('TradeSummary') or 'NA'}"
            for r in acc
        )
        rej_txt = ", ".join(
            f"{r['ConditionMetDate']}"
            f"{' -> ' + str(r['AcceptedDate']) if r['AcceptedDate'] else ''} "
            f"[{r['Stage']}] {r['Reason']}"
            f"{', ' + r['TradeNote'] if r.get('TradeNote') else ''}"
            for r in rej
        )

        sections = []
        if acc_txt:
            sections.append(f"✅ {acc_txt}")
        if rej_txt:
            sections.append(f"❌ {rej_txt}")

        lines.append(f"{{{stock}: {' | '.join(sections)}}}  # {len(acc)} accepted, {len(rej)} rejected")
    return lines


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

    # Final per-stock summary — accepted AND rejected dates, with TG/SL outcome (prints even if nothing was accepted)
    summary_lines = build_stock_date_summary(results, REJECTED_EVENTS)
    if summary_lines:
        print("\n🧾 STOCK DATE SUMMARY")
        print("   ✅ ConditionMetDate -> FinalAcceptedDate (PrevWeekRefDate), TG/SL status  |  ❌ ConditionMetDate -> AcceptedDate [stage] reason")
        for line in summary_lines:
            print(line)
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(summary_lines))
        print(f"📄 Saved summary → {SUMMARY_FILE}")
    else:
        print("\n🧾 STOCK DATE SUMMARY → no gap-down events found for any stock")


logging.captureWarnings(False)
warnings.showwarning = _filtered_showwarning

run_all()