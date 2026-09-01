#modify #condition #Main1 exclude negative oi and rolled expiry
# ============================================================
# COMBINED SCRIPT (UPDATED v3 — ZONE-FIRST SCORING)
#   PART 1 — CONDITION SCREENER
#   PART 2 — OI SENTIMENT CLASSIFIER (runs inline, per stock)
#   FINAL   — Prints OI summary table, saves OI_data.json +

import subprocess
import sys

def ensure(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

ensure("pandas")
ensure("numpy")
ensure("pytz")
ensure("fyers-apiv3", "fyers_apiv3")
ensure("openpyxl")

import os
import re
import ast
import io
import json
import time
import pytz
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
from fyers_apiv3 import fyersModel

# ============================================================
# GOOGLE COLAB PATH SETUP
# ============================================================
BASE_DIR = r"C:\Users\sheik\PycharmProjects\MyProject"
LOG_DIR = f"{BASE_DIR}/logs"

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ------------------------------
# FYERS API SETUP
# ------------------------------
date_to_check = "2026-09-1"

#client_id = "EQ09VHDCAE-100"
client_id = '6KX2O3OQK4-100'

# Token file path
token_file = f"{BASE_DIR}/token.txt"

# ------------------------------
# READ ACCESS TOKEN
# ------------------------------
access_token = ""

if os.path.exists(token_file):

    with open(token_file, "r") as f:
        token_content = f.read().strip()

    if "access_token" in token_content:
        try:
            token_content = token_content.split("=", 1)[1]
            token_content = token_content.strip().strip('"').strip("'")
        except:
            pass

    access_token = token_content.strip()

    print("✅ Access token loaded successfully")

else:
    print("❌ token.txt file not found")
    access_token = ""

# ------------------------------
# CREATE FYERS OBJECT
# ------------------------------
fyers = fyersModel.FyersModel(
    client_id=client_id,
    token=access_token,
    is_async=False,
    log_path=LOG_DIR
)

# ------------------------------
# FUNCTION TO FETCH STOCK DATA
# ------------------------------
def get_stock_data(stock, date_str):

    try:

        date = datetime.strptime(date_str, "%Y-%m-%d")
        next_date = date + timedelta(days=1)

        fy_symbol = (
            stock if stock.startswith("NSE:")
            else f"NSE:{stock.replace('.NS','')}"
        )

        req = {
            "symbol": fy_symbol,
            "resolution": "5",
            "date_format": "1",
            "range_from": date.strftime("%Y-%m-%d"),
            "range_to": next_date.strftime("%Y-%m-%d"),
            "cont_flag": "1"
        }

        # --------------------------------
        # RETRY LOGIC
        # --------------------------------
        response = None


        for attempt in range(3):

            try:

                response = fyers.history(req)
                print(f"DEBUG {stock}: s={response.get('s')}, candle_count={len(response.get('candles', []))}")
                time.sleep(0.4)

                if (
                    response
                    and response.get("s") == "ok"
                    and response.get("candles")
                ):
                    break

            except Exception as e:

                print(f"{stock} | Retry {attempt+1} Error: {e}")

            time.sleep(1.5)

        # --------------------------------
        # NO DATA
        # --------------------------------
        if (
            not response
            or response.get("s") != "ok"
            or not response.get("candles")
        ):

            reason = "No data"

            print(f"{stock} | {reason}")

            return (None,) * 12 + (reason,)

        # ------------------------------
        # CREATE DATAFRAME
        # ------------------------------
        df = pd.DataFrame(
            response["candles"],
            columns=[
                "timestamp",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume"
            ]
        )

        if df.empty:

            reason = "Empty dataframe"

            print(f"{stock} | {reason}")

            return (None,) * 12 + (reason,)

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="s",
            utc=True
        )

        df.set_index("timestamp", inplace=True)

        df.index = df.index.tz_convert("Asia/Kolkata")

        # ------------------------------
        # MARKET HOURS FILTER
        # ------------------------------
        df = df.between_time("09:15", "15:30")

        if len(df) < 3:

            reason = "Not enough intraday candles"

            print(f"{stock} | {reason}")

            return (None,) * 12 + (reason,)

        # ------------------------------
        # BASIC VALUES
        # ------------------------------
        day_open = float(df["Open"].iloc[0])

        first3 = df.iloc[:3]

        if len(first3) < 3:

            reason = "Not enough candles"

            print(f"{stock} | {reason}")

            return (None,) * 12 + (reason,)

        # ------------------------------
        # EXTRACT CANDLES
        # ------------------------------
        O1, H1, L1, C1 = first3.iloc[0][
            ["Open", "High", "Low", "Close"]
        ]

        O2, C2 = first3.iloc[1][["Open", "Close"]]
        O3, C3 = first3.iloc[2][["Open", "Close"]]

        # ------------------------------
        # BODY %
        # ------------------------------
        body1 = (C1 - O1) / O1 * 100
        body2 = (C2 - O2) / O2 * 100
        body3 = (C3 - O3) / O3 * 100

        print(f"{stock} | First Candle Body: {body1:.2f}%")

        # =====================================================
        # DOJI
        # =====================================================
        doji_failed = False

        if abs(body1) <= 0.05:

            print(
                f"{stock} | DOJI MODE | "
                f"Body1={body1:.2f}% | "
                f"Body2={body2:.2f}% | "
                f"Body3={body3:.2f}%"
            )

            if C2 < L1:

                print(f"{stock} | Doji FAIL: C2 below L1")

                doji_failed = True

            elif abs(body2) >= 0.2 and C3 <= O3:

                print(
                    f"{stock} | "
                    f"Doji FAIL: strong C2 but C3 not green"
                )

                doji_failed = True

            if not doji_failed and abs(body2) < 0.2:
                doji_failed = True

            if (
                not doji_failed
                and abs(body2) >= 0.2
                and C3 > O3
            ):

                reason = "Accepted (Doji Rule)"

                print(f"{stock} | {reason}")

                return finalize(
                    stock,
                    reason,
                    C1,
                    body1,
                    body2,
                    C2 - day_open,
                    C3 - day_open
                )

        # =====================================================
        # NO UPWICK MODE
        # =====================================================
        no_upwick_mode = False

        if H1 - max(O1, C1) == 0 and 0 >= body1 >= -0.65:

            print(f"{stock} | NO UPWICK MODE ACTIVE")

            no_upwick_mode = True

        # ------------------------------
        # RULE-1
        # ------------------------------
        if abs(body1) > 0.75:

            reason = f"Rule-1 FAIL: Body {body1:.2f}%"

            print(f"{stock} | {reason}")

            return finalize(
                stock,
                reason,
                C1,
                body1,
                body2,
                C2 - day_open,
                C3 - day_open
            )

        # ------------------------------
        # RULE-2 (WITH % EXCEED INFO)
        # ------------------------------
        if C2 < L1:

            reason = "Rule-2 FAIL: C2 below L1"

            print(f"{stock} | {reason}")

            return finalize(
                stock,
                reason,
                C1,
                body1,
                body2,
                C2 - day_open,
                C3 - day_open
            )

        if C1 < O1:
            low_limit_2 = C1 * 0.9980
            high_limit_2 = O1 * 1.0040
        else:
            high_limit_2 = C1 * 1.0040
            low_limit_2 = O1 * 0.9980

        if C1 < O1 and body1 < -0.50 and not no_upwick_mode:

            cover_level = C1 + (O1 - C1) * 0.35

            if C2 < cover_level:

                reason = "Rule-2 FAIL: 35% pullback not met"

                print(f"{stock} | {reason}")

                return finalize(
                    stock,
                    reason,
                    C1,
                    body1,
                    body2,
                    C2 - day_open,
                    C3 - day_open
                )

        if not (low_limit_2 <= C2 <= high_limit_2):

            if C2 > high_limit_2:

                exceed = (
                    (C2 - high_limit_2)
                    / high_limit_2
                    * 100
                )

                side = "above"

            else:

                exceed = (
                    (C2 - low_limit_2)
                    / low_limit_2
                    * 100
                )

                side = "below"

            reason = (
                f"Rule-2 FAIL: C2 {side} range by {exceed:.2f}% | "
                f"Range=({low_limit_2:.2f}-{high_limit_2:.2f})"
            )

            print(f"{stock} | {reason}")

            return finalize(
                stock,
                reason,
                C1,
                body1,
                body2,
                C2 - day_open,
                C3 - day_open
            )

        # ------------------------------
        # RULE-3
        # ------------------------------
        if C1 < O1:

            low_limit_3 = C1 * 0.9983
            high_limit_3 = O1 * 1.0035

        else:

            high_limit_3 = C1 * 1.0030
            low_limit_3 = O1 * 0.9980

        if not (low_limit_3 <= C3 <= high_limit_3):

            if C3 > high_limit_3:

                exceed = (
                    (C3 - high_limit_3)
                    / high_limit_3
                    * 100
                )

                side = "above"

            else:

                exceed = (
                    (C3 - low_limit_3)
                    / low_limit_3
                    * 100
                )

                side = "below"

            reason = (
                f"Rule-3 FAIL: "
                f"C3 {side} range by {exceed:.2f}%"
            )

            print(f"{stock} | {reason}")

            return finalize(
                stock,
                reason,
                C1,
                body1,
                body2,
                C2 - day_open,
                C3 - day_open
            )

        # ------------------------------
        # ACCEPTED
        # ------------------------------
        reason = "Accepted"

        print(f"{stock} | {reason}")

        return finalize(
            stock,
            reason,
            C1,
            body1,
            body2,
            C2 - day_open,
            C3 - day_open
        )

    except Exception as e:

        reason = f"Error: {e}"

        print(f"{stock} | {reason}")

        return (None,) * 12 + (reason,)

# ------------------------------
# FINALIZE
# ------------------------------
def finalize(
    stock,
    reason,
    C1,
    body1,
    body2,
    diff2,
    diff3
):

    return (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        reason,
        round(body1, 2),
        round(body2, 2),
        round(diff2, 2),
        round(diff3, 2)
    )

# ------------------------------
# STOCK LIST (TRIMMED TO 10 FOR TESTING — update this later)
# ------------------------------

STOCKS =['NSE:360ONE-EQ', 'NSE:ABB-EQ',   'NSE:ABCAPITAL-EQ', 'NSE:ADANIENSOL-EQ',    'NSE:ADANIENT-EQ',  'NSE:ADANIGREEN-EQ',    'NSE:ADANIPORTS-EQ',    'NSE:ADANIPOWER-EQ',    'NSE:ALKEM-EQ', 'NSE:AMBER-EQ', 'NSE:AMBUJACEM-EQ', 'NSE:ANGELONE-EQ',  'NSE:APLAPOLLO-EQ', 'NSE:APOLLOHOSP-EQ',    'NSE:ASHOKLEY-EQ',  'NSE:ASIANPAINT-EQ',    'NSE:ASTRAL-EQ',    'NSE:AUBANK-EQ',    'NSE:AUROPHARMA-EQ',    'NSE:AXISBANK-EQ',  'NSE:BAJAJ-AUTO-EQ',    'NSE:BAJAJFINSV-EQ',    'NSE:BAJAJHLDNG-EQ',    'NSE:BAJFINANCE-EQ',    'NSE:BANDHANBNK-EQ',    'NSE:BANKBARODA-EQ',    'NSE:BANKINDIA-EQ', 'NSE:BDL-EQ',   'NSE:BEL-EQ',   'NSE:BHARATFORG-EQ',    'NSE:BHARTIARTL-EQ',    'NSE:BHEL-EQ',  'NSE:BIOCON-EQ',    'NSE:BLUESTARCO-EQ',    'NSE:BOSCHLTD-EQ',  'NSE:BPCL-EQ',  'NSE:BRITANNIA-EQ', 'NSE:BSE-EQ',   'NSE:CAMS-EQ',  'NSE:CANBK-EQ', 'NSE:CDSL-EQ',  'NSE:CGPOWER-EQ',   'NSE:CHOLAFIN-EQ',  'NSE:CIPLA-EQ', 'NSE:COALINDIA-EQ', 'NSE:COCHINSHIP-EQ',    'NSE:COFORGE-EQ',   'NSE:COLPAL-EQ',    'NSE:CONCOR-EQ',    'NSE:CROMPTON-EQ',  'NSE:CUMMINSIND-EQ',    'NSE:DABUR-EQ', 'NSE:DALBHARAT-EQ', 'NSE:DELHIVERY-EQ', 'NSE:DIVISLAB-EQ',  'NSE:DIXON-EQ', 'NSE:DLF-EQ',   'NSE:DMART-EQ', 'NSE:DRREDDY-EQ',   'NSE:EICHERMOT-EQ', 'NSE:ETERNAL-EQ',   'NSE:EXIDEIND-EQ',  'NSE:FEDERALBNK-EQ',    'NSE:FORCEMOT-EQ',  'NSE:FORTIS-EQ',    'NSE:GAIL-EQ',  'NSE:GLENMARK-EQ',  'NSE:GMRAIRPORT-EQ',    'NSE:GODFRYPHLP-EQ',    'NSE:GODREJCP-EQ',  'NSE:GODREJPROP-EQ',    'NSE:GRASIM-EQ',    'NSE:HAL-EQ',   'NSE:HAVELLS-EQ',   'NSE:HCLTECH-EQ',   'NSE:HDFCAMC-EQ',   'NSE:HDFCBANK-EQ',  'NSE:HDFCLIFE-EQ',  'NSE:HEROMOTOCO-EQ',    'NSE:HINDALCO-EQ',  'NSE:HINDPETRO-EQ', 'NSE:HINDUNILVR-EQ',    'NSE:HINDZINC-EQ',  'NSE:HYUNDAI-EQ',   'NSE:ICICIBANK-EQ', 'NSE:ICICIGI-EQ',   'NSE:ICICIPRULI-EQ',    'NSE:IDEA-EQ',  'NSE:IDFCFIRSTB-EQ',    'NSE:IEX-EQ',   'NSE:INDHOTEL-EQ',  'NSE:INDIANB-EQ',   'NSE:INDIGO-EQ',    'NSE:INDUSINDBK-EQ',    'NSE:INDUSTOWER-EQ',    'NSE:INFY-EQ',  'NSE:INOXWIND-EQ',  'NSE:IOC-EQ',   'NSE:IREDA-EQ', 'NSE:IRFC-EQ',  'NSE:ITC-EQ',   'NSE:JINDALSTEL-EQ',    'NSE:JIOFIN-EQ',    'NSE:JSWENERGY-EQ', 'NSE:JSWSTEEL-EQ',  'NSE:JUBLFOOD-EQ',  'NSE:KALYANKJIL-EQ',    'NSE:KAYNES-EQ',    'NSE:KEI-EQ',   'NSE:KFINTECH-EQ',  'NSE:KOTAKBANK-EQ', 'NSE:KPITTECH-EQ',  'NSE:LAURUSLABS-EQ',    'NSE:LICHSGFIN-EQ', 'NSE:LICI-EQ',  'NSE:LODHA-EQ', 'NSE:LT-EQ',    'NSE:LTF-EQ',   'NSE:LTM-EQ',   'NSE:LUPIN-EQ', 'NSE:M&M-EQ',   'NSE:MANAPPURAM-EQ',    'NSE:MANKIND-EQ',   'NSE:MARICO-EQ',    'NSE:MARUTI-EQ',    'NSE:MAXHEALTH-EQ', 'NSE:MAZDOCK-EQ',   'NSE:MCX-EQ',   'NSE:MFSL-EQ',  'NSE:MOTHERSON-EQ', 'NSE:MOTILALOFS-EQ',    'NSE:MPHASIS-EQ',   'NSE:MUTHOOTFIN-EQ',    'NSE:NAM-INDIA-EQ', 'NSE:NATIONALUM-EQ',    'NSE:NAUKRI-EQ',    'NSE:NBCC-EQ',  'NSE:NESTLEIND-EQ', 'NSE:NHPC-EQ',  'NSE:NMDC-EQ',  'NSE:NTPC-EQ',  'NSE:NUVAMA-EQ',    'NSE:NYKAA-EQ', 'NSE:OBEROIRLTY-EQ',    'NSE:OFSS-EQ',  'NSE:OIL-EQ',   'NSE:ONGC-EQ',  'NSE:PAGEIND-EQ',   'NSE:PATANJALI-EQ', 'NSE:PAYTM-EQ', 'NSE:PERSISTENT-EQ',    'NSE:PETRONET-EQ',  'NSE:PFC-EQ',   'NSE:PGEL-EQ',  'NSE:PHOENIXLTD-EQ',    'NSE:PIDILITIND-EQ',    'NSE:PIIND-EQ', 'NSE:PNB-EQ',   'NSE:PNBHOUSING-EQ',    'NSE:POLICYBZR-EQ', 'NSE:POLYCAB-EQ',   'NSE:POWERGRID-EQ', 'NSE:POWERINDIA-EQ',    'NSE:PREMIERENE-EQ',    'NSE:PRESTIGE-EQ',  'NSE:RBLBANK-EQ',   'NSE:RECLTD-EQ',    'NSE:RELIANCE-EQ',  'NSE:RVNL-EQ',  'NSE:SAIL-EQ',  'NSE:SAMMAANCAP-EQ',    'NSE:SBICARD-EQ',   'NSE:SBILIFE-EQ',   'NSE:SBIN-EQ',  'NSE:SHREECEM-EQ',  'NSE:SHRIRAMFIN-EQ',    'NSE:SIEMENS-EQ',   'NSE:SOLARINDS-EQ', 'NSE:SONACOMS-EQ',  'NSE:SRF-EQ',   'NSE:SUNPHARMA-EQ', 'NSE:SUPREMEIND-EQ',    'NSE:SUZLON-EQ',    'NSE:SWIGGY-EQ',    'NSE:TATACONSUM-EQ',    'NSE:TATAELXSI-EQ', 'NSE:TATAPOWER-EQ', 'NSE:TATASTEEL-EQ', 'NSE:TCS-EQ',   'NSE:TECHM-EQ', 'NSE:TIINDIA-EQ',   'NSE:TITAN-EQ', 'NSE:TMPV-EQ',  'NSE:TORNTPHARM-EQ',    'NSE:TRENT-EQ', 'NSE:TVSMOTOR-EQ',  'NSE:ULTRACEMCO-EQ',    'NSE:UNIONBANK-EQ', 'NSE:UNITDSPR-EQ',  'NSE:UNOMINDA-EQ',  'NSE:UPL-EQ',   'NSE:VBL-EQ',   'NSE:VEDL-EQ',  'NSE:VMM-EQ',   'NSE:VOLTAS-EQ',    'NSE:WAAREEENER-EQ',    'NSE:WIPRO-EQ', 'NSE:YESBANK-EQ',   'NSE:ZYDUSLIFE-EQ',]


# ============================================================
# PART 2 — OI SENTIMENT CLASSIFIER (function definitions only;
#           actually EXECUTED inline inside the Part-1 loop below)
# ============================================================

# ─────────────────────────────────────────────
# CONFIG  (OI-specific; reuses `fyers`, `BASE_DIR`, `LOG_DIR`
#          from above — no separate token/client setup)
# ─────────────────────────────────────────────
JSON_OUT_DIR  = f"{BASE_DIR}/oi_reports"
JSON_OUT_FILE = "OI_data.json"   # fixed filename — overwritten every run

# oi_cache.csv goes straight into BASE_DIR (not oi_reports)
OI_CACHE_CSV_FILE = f"{BASE_DIR}/oi_cache.csv"

# OI_summary.csv gets an IST timestamp in the filename
RUN_TIMESTAMP = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S")
CSV_OUT_FILE  = f"OI_{RUN_TIMESTAMP}summary.csv"   # e.g. OI_20260821_143205summary.csv

os.makedirs(JSON_OUT_DIR, exist_ok=True)

# Number of strikes each side of ATM to aggregate OI over
ATM_RANGE = 5

# % change threshold to count as "significant" building/unwinding
CE_THRESH = 3.0
PE_THRESH = 3.0

# Max Pain is only mentioned/scored if it sits at least this % away from spot
MAX_PAIN_MIN_DIST_PCT = 3.0

# Score thresholds that decide overall bias
BULLISH_SCORE_THRESH = 12.0
BEARISH_SCORE_THRESH = -12.0

LEAN_SCORE_THRESH = 5.0

# Score magnitude needed to call it a full "Build-Up" (vs a softer "OI Bias")
STRONG_BUILD_SCORE = 25.0


# ─────────────────────────────────────────────
FULL_CHAIN_RATIO_MIN_LAKH = 0.3   # ignore dust-level full-chain moves
FULL_CHAIN_RATIO_GATE     = 1.3   # ratio must exceed this before it's "dominant"
FULL_CHAIN_RATIO_WEIGHT   = 11.0  # scales log(ratio) into score points
FULL_CHAIN_RATIO_CAP      = 18.0  # max points this signal can swing the score


FULL_CHAIN_UNWIND_RATIO_MIN_LAKH = 0.3
FULL_CHAIN_UNWIND_RATIO_GATE     = 1.3
FULL_CHAIN_UNWIND_RATIO_WEIGHT   = 9.0
FULL_CHAIN_UNWIND_RATIO_CAP      = 14.0


ZONE_LAKH_WEIGHT = 1.0
ZONE_LAKH_CAP    = 6.0
ZONE_LAKH_MIN    = 0.15   # ignore dust-level zone moves (in lakhs)


STRONG_UNWIND_PCT = 12.0

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# LAST TRADING DAY (skip weekends)
# ─────────────────────────────────────────────
def last_trading_day():
    now = datetime.now(IST)
    # If before market open (9:15 AM) treat as previous day
    if now.hour < 9 or (now.hour == 9 and now.minute < 15):
        now -= timedelta(days=1)
    # Roll back past weekends
    while now.weekday() >= 5:   # 5=Sat, 6=Sun
        now -= timedelta(days=1)
    return now.strftime("%Y-%m-%d"), now.weekday()

# ─────────────────────────────────────────────
# FETCH OPTION CHAIN (live, no timestamp)
# ─────────────────────────────────────────────
def fetch_option_chain(fyers_obj, fno_sym, strike_count=15, expiry_ts=None):
    payload = {
        "symbol"     : fno_sym,
        "strikecount": strike_count
    }
    # If a specific expiry timestamp was resolved upstream (e.g. we rolled
    # forward to next month's contracts because today/tomorrow is expiry),
    # request that expiry explicitly. Otherwise Fyers defaults to nearest.
    if expiry_ts:
        payload["timestamp"] = str(expiry_ts)

    resp = fyers_obj.optionchain(payload)
    time.sleep(0.6)
    return resp

# ─────────────────────────────────────────────
# EXPIRY SELECTION
#   Fetch the list of available expiries for a symbol and pick the one
#   to actually use for OI analysis. If the nearest expiry falls TODAY or
#   TOMORROW, we're right at/near expiry — in that case roll forward and
#   use the NEXT expiry's contracts instead of the about-to-expire ones,
#   since near-expiry OI data gets unreliable/noisy right before rollover.
# ─────────────────────────────────────────────
def get_expiry_list(fyers_obj, fno_sym):
    try:
        resp = fyers_obj.optionchain({
            "symbol"     : fno_sym,
            "strikecount": 1
        })
        time.sleep(0.6)

        if not resp or resp.get("s") != "ok":
            return []

        data = resp.get("data", {})
        expiry_list = data.get("expiryData") or data.get("expiry_data") or []
        return expiry_list

    except Exception:
        return []

def select_target_expiry(expiry_list):

    if not expiry_list:
        return None, None, False

    def _exp_ts(e):
        try:
            return int(e.get("expiry") or e.get("date") or 0)
        except Exception:
            return 0

    sorted_exp = sorted(
        [e for e in expiry_list if _exp_ts(e) > 0],
        key=_exp_ts
    )

    if not sorted_exp:
        return None, None, False

    now_ist       = datetime.now(IST)
    today_date    = now_ist.date()
    tomorrow_date = today_date + timedelta(days=1)

    nearest      = sorted_exp[0]
    nearest_ts   = _exp_ts(nearest)
    nearest_date = datetime.fromtimestamp(nearest_ts, IST).date()

    if nearest_date in (today_date, tomorrow_date) and len(sorted_exp) > 1:
        # Near/at expiry — roll forward to the next expiry's contracts.
        return _exp_ts(sorted_exp[1]), nearest_ts, True

    return nearest_ts, nearest_ts, False

# ─────────────────────────────────────────────
# NORMALIZE OPTION CHAIN DATAFRAME
# ─────────────────────────────────────────────
def normalize_chain(options_chain):
    df = pd.DataFrame(options_chain)

    # Rename columns to standard names
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

# ─────────────────────────────────────────────
# CORE SENTIMENT ENGINE  (weighted scoring)
# ─────────────────────────────────────────────
def classify_sentiment(symbol, df, spot_price):

    # Separate CE and PE
    ce = df[df["option_type"].str.upper() == "CE"].copy()
    pe = df[df["option_type"].str.upper() == "PE"].copy()

    if ce.empty or pe.empty:
        return None

    # ATM strike
    all_strikes = df["strike_price"].dropna().unique()
    atm = min(all_strikes, key=lambda s: abs(s - spot_price))

    # Sort strikes, find ATM index, slice ±ATM_RANGE
    sorted_strikes = sorted(all_strikes)
    try:
        atm_idx = sorted_strikes.index(atm)
    except ValueError:
        atm_idx = len(sorted_strikes) // 2

    lo = max(0, atm_idx - ATM_RANGE)
    hi = min(len(sorted_strikes), atm_idx + ATM_RANGE + 1)
    window_strikes = sorted_strikes[lo:hi]

    ce_w = ce[ce["strike_price"].isin(window_strikes)]
    pe_w = pe[pe["strike_price"].isin(window_strikes)]

    # ── Aggregate across ATM window (for OI change & classification) ──
    total_ce_oi      = ce_w["oi"].sum()
    total_pe_oi      = pe_w["oi"].sum()
    total_ce_prev_oi = ce_w["prev_oi"].sum()
    total_pe_prev_oi = pe_w["prev_oi"].sum()

    ce_oi_chg = total_ce_oi - total_ce_prev_oi
    pe_oi_chg = total_pe_oi - total_pe_prev_oi

    ce_oi_chg_pct = (ce_oi_chg / total_ce_prev_oi * 100) if total_ce_prev_oi else 0.0
    pe_oi_chg_pct = (pe_oi_chg / total_pe_prev_oi * 100) if total_pe_prev_oi else 0.0

    # ── PCR: FULL chain (matches Sensibull / NSE standard) ────────────
    full_ce_oi       = ce["oi"].sum()
    full_pe_oi       = pe["oi"].sum()
    full_ce_prev_oi  = ce["prev_oi"].sum()
    full_pe_prev_oi  = pe["prev_oi"].sum()

    pcr          = round(full_pe_oi  / full_ce_oi,       2) if full_ce_oi      else 0.0
    prev_pcr     = round(full_pe_prev_oi / full_ce_prev_oi, 2) if full_ce_prev_oi else 0.0
    pcr_chg      = round(pcr - prev_pcr, 2)

    # OI trend arrows for full chain CE and PE
    full_ce_chg      = full_ce_oi - full_ce_prev_oi
    full_pe_chg      = full_pe_oi - full_pe_prev_oi
    full_ce_chg_pct  = round(full_ce_chg / full_ce_prev_oi * 100, 2) if full_ce_prev_oi else 0.0
    full_pe_chg_pct  = round(full_pe_chg / full_pe_prev_oi * 100, 2) if full_pe_prev_oi else 0.0

    def oi_arrow(chg_pct):
        if chg_pct >= 3:    return f"++ ({chg_pct:+.1f}%)"   # strong increase
        elif chg_pct >= 0.5: return f"+  ({chg_pct:+.1f}%)"  # mild increase
        elif chg_pct <= -3:  return f"-- ({chg_pct:+.1f}%)"  # strong decrease
        elif chg_pct <= -0.5: return f"-  ({chg_pct:+.1f}%)" # mild decrease
        else:                return f"~  ({chg_pct:+.1f}%)"  # flat

    ce_arrow = oi_arrow(full_ce_chg_pct)
    pe_arrow = oi_arrow(full_pe_chg_pct)

    def pcr_arrow(chg):
        if chg >= 0.05:    return f"^ +{chg:.2f} (Bullish)"
        elif chg <= -0.05: return f"v {chg:.2f} (Bearish)"
        else:              return f"~ {chg:+.2f} (Neutral)"

    pcr_trend = pcr_arrow(pcr_chg)

    # ATM-window PCR — kept for display only, NOT used as a decision override
    atm_pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0.0

    # Volume PCR: full chain
    full_ce_vol  = ce["volume"].sum()
    full_pe_vol  = pe["volume"].sum()
    total_ce_vol = full_ce_vol
    total_pe_vol = full_pe_vol
    vol_pcr      = round(full_pe_vol / full_ce_vol, 2) if full_ce_vol else 0.0

    # ATM row values
    atm_ce = ce[ce["strike_price"] == atm]
    atm_pe = pe[pe["strike_price"] == atm]
    atm_ce_oi  = int(atm_ce["oi"].values[0])      if len(atm_ce) else 0
    atm_pe_oi  = int(atm_pe["oi"].values[0])      if len(atm_pe) else 0
    atm_ce_chg = int(atm_ce["oi"].values[0] - atm_ce["prev_oi"].values[0]) if len(atm_ce) else 0
    atm_pe_chg = int(atm_pe["oi"].values[0] - atm_pe["prev_oi"].values[0]) if len(atm_pe) else 0

    # Max Pain strike (strike where total OI pain to buyers is max)
    pain = {}
    for s in sorted_strikes:
        ce_pain = ce[ce["strike_price"] >= s]["oi"].sum() * (ce[ce["strike_price"] >= s]["strike_price"] - s).abs().mean() if not ce[ce["strike_price"] >= s].empty else 0
        pe_pain = pe[pe["strike_price"] <= s]["oi"].sum() * (s - pe[pe["strike_price"] <= s]["strike_price"]).abs().mean() if not pe[pe["strike_price"] <= s].empty else 0
        pain[s] = ce_pain + pe_pain
    max_pain_strike = min(pain, key=pain.get) if pain else atm

    max_pain_dist_pct = round((max_pain_strike - spot_price) / spot_price * 100, 2) if spot_price else 0.0
    max_pain_meaningful = abs(max_pain_dist_pct) >= MAX_PAIN_MIN_DIST_PCT

    # ── Building / unwinding flags (pattern detection, ATM-window based) ──
    ce_building  = ce_oi_chg_pct >  CE_THRESH
    pe_building  = pe_oi_chg_pct >  PE_THRESH
    ce_unwinding = ce_oi_chg_pct < -CE_THRESH
    pe_unwinding = pe_oi_chg_pct < -PE_THRESH

    # ── WEIGHTED SCORE ──────────────────────────────────────
    window_diff = pe_oi_chg_pct - ce_oi_chg_pct       # ATM-zone build tilt
    full_diff   = full_pe_chg_pct - full_ce_chg_pct   # full-chain confirmation

    score_breakdown = []

    def _add(points, label):
        points = round(float(points), 1)
        if points != 0:
            score_breakdown.append({"points": points, "label": label})
        return points

    score = 0.0

    # 1. PRIMARY signal: ATM-zone Put vs Call OI build tilt.
    #    v3: weight raised 1.0 -> 1.3 (zone is now the primary driver).
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

    # 2. CONFIRMATION signal: full-chain (v3: weight lowered 0.5 -> 0.3,
    #    since the zone above is now the primary decision driver and the
    #    full chain is only used to confirm/dampen it).
    full_pts = full_diff * 0.3
    if full_diff > 0:
        full_label = "Full-chain confirmation (Put-heavy)"
    elif full_diff < 0:
        full_label = "Full-chain confirmation (Call-heavy)"
    else:
        full_label = "Full-chain confirmation (flat)"
    score += _add(full_pts, full_label)

    # 3. PCR TREND (direction), not absolute PCR
    pcr_pts = pcr_chg * 40
    if pcr_chg > 0:
        pcr_label = "PCR improving"
    elif pcr_chg < 0:
        pcr_label = "PCR weakening"
    else:
        pcr_label = "PCR flat"
    score += _add(pcr_pts, pcr_label)

    # 4. Max Pain — only counted if meaningfully away from spot
    if max_pain_meaningful:
        mp_pts = 8 if max_pain_dist_pct > 0 else -8
        mp_label = "Max Pain above spot" if max_pain_dist_pct > 0 else "Max Pain below spot"
        score += _add(mp_pts, mp_label)

    # 5. Volume PCR confirmation
    if vol_pcr >= 1.1:
        score += _add(5, "Volume favors Puts")
    elif vol_pcr <= 0.9:
        score += _add(-5, "Volume favors Calls")


    ratio_label = None
    ratio_pts = 0.0
    if full_ce_chg > 0 and full_pe_chg > 0:
        ce_l = full_ce_chg / 100000
        pe_l = full_pe_chg / 100000
        if max(ce_l, pe_l) >= FULL_CHAIN_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l
            if ratio >= FULL_CHAIN_RATIO_GATE:
                ratio_pts = -min(np.log(ratio) * FULL_CHAIN_RATIO_WEIGHT, FULL_CHAIN_RATIO_CAP)
                ratio_label = (
                    f"Full-chain Dual Build-Up is Call-heavy "
                    f"({ce_l:+.2f}L CE vs {pe_l:+.2f}L PE, {ratio:.1f}x)"
                )
            elif ratio <= 1 / FULL_CHAIN_RATIO_GATE:
                inv_ratio = 1 / ratio
                ratio_pts = min(np.log(inv_ratio) * FULL_CHAIN_RATIO_WEIGHT, FULL_CHAIN_RATIO_CAP)
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
        if max(ce_l, pe_l) >= FULL_CHAIN_UNWIND_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l if pe_l else float("inf")
            if ratio >= FULL_CHAIN_UNWIND_RATIO_GATE:
                unwind_pts = min(np.log(ratio) * FULL_CHAIN_UNWIND_RATIO_WEIGHT, FULL_CHAIN_UNWIND_RATIO_CAP)
                unwind_label = (
                    f"Full-chain Dual Unwinding is Call-heavy "
                    f"({ce_l:.2f}L CE vs {pe_l:.2f}L PE unwound, {ratio:.1f}x) — bullish"
                )
            elif ratio <= 1 / FULL_CHAIN_UNWIND_RATIO_GATE:
                inv_ratio = 1 / ratio
                unwind_pts = -min(np.log(inv_ratio) * FULL_CHAIN_UNWIND_RATIO_WEIGHT, FULL_CHAIN_UNWIND_RATIO_CAP)
                unwind_label = (
                    f"Full-chain Dual Unwinding is Put-heavy "
                    f"({pe_l:.2f}L PE vs {ce_l:.2f}L CE unwound, {inv_ratio:.1f}x) — bearish"
                )
    if unwind_label:
        score += _add(unwind_pts, unwind_label)

    # 7. v3 NEW: ATM-zone raw contract-magnitude tilt (lakhs, NOT %). This
    #    is the primary fix for high-base-OI names where % change
    #    understates a genuinely large institutional move happening
    #    inside the ATM±{ATM_RANGE} zone (e.g. TATAPOWER, RELIANCE, WIPRO).
    zone_ce_l = ce_oi_chg / 100000
    zone_pe_l = pe_oi_chg / 100000
    zone_label = None
    zone_pts = 0.0
    if max(abs(zone_ce_l), abs(zone_pe_l)) >= ZONE_LAKH_MIN:
        zone_diff_l = zone_pe_l - zone_ce_l
        zone_pts = max(-ZONE_LAKH_CAP, min(ZONE_LAKH_CAP, zone_diff_l * ZONE_LAKH_WEIGHT))
        if zone_pts != 0:
            zone_label = (
                f"ATM±{ATM_RANGE} zone raw flow: CE {zone_ce_l:+.2f}L vs PE {zone_pe_l:+.2f}L "
                f"(institutional tilt {'Put' if zone_diff_l > 0 else 'Call'}-heavy)"
            )
    if zone_label:
        score += _add(zone_pts, zone_label)

    score = round(score, 1)

    # ── Bias from combined score ───────────────────────────
    if score >= BULLISH_SCORE_THRESH:
        bias = "Bullish"
    elif score >= LEAN_SCORE_THRESH:
        bias = "Bullish Lean"
    elif score <= BEARISH_SCORE_THRESH:
        bias = "Bearish"
    elif score <= -LEAN_SCORE_THRESH:
        bias = "Bearish Lean"
    else:
        bias = "Neutral"

    # ── Category label: pattern (what's happening) + bias (net effect) ──
    if bias == "Bullish":
        if pe_building and not ce_building and not ce_unwinding:
            if score >= STRONG_BUILD_SCORE:
                category, category_key, bias_emoji = "Long Build-Up", "long_build_up", "🟢"
            else:
                category, category_key, bias_emoji = "Bullish OI Bias (Fresh Put Writing)", "bullish_put_writing", "🟢"
        elif ce_unwinding and not pe_unwinding:
            # v3: upgrade to "Strong Bullish" wording when the zone move
            # is large enough that this isn't just a tentative reaction.
            if ce_oi_chg_pct <= -STRONG_UNWIND_PCT:
                category, category_key, bias_emoji = "Strong Bullish (Heavy Call Unwinding)", "strong_short_covering", "🟢"
            else:
                category, category_key, bias_emoji = "Short Covering", "short_covering", "🟡"
        elif ce_building and pe_building:
            category, category_key, bias_emoji = "Bullish OI Bias (Dual Build-Up)", "bullish_dual", "🟢"
        else:
            category, category_key, bias_emoji = "Bullish OI Bias", "bullish_generic", "🟢"

    elif bias == "Bullish Lean":
        # v3 NEW: two-sided combo — fresh Put writing AND Call unwinding
        # at once is a stronger bullish hint than either alone.
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
            if score <= -STRONG_BUILD_SCORE:
                category, category_key, bias_emoji = "Short Build-Up", "short_build_up", "🔴"
            else:
                category, category_key, bias_emoji = "Bearish OI Bias (Fresh Call Writing)", "bearish_call_writing", "🔴"
        elif pe_unwinding and not ce_unwinding:
            # v3: upgrade to "Strong Bearish" wording when the zone move
            # is large enough that this isn't just a tentative reaction.
            if pe_oi_chg_pct <= -STRONG_UNWIND_PCT:
                category, category_key, bias_emoji = "Strong Bearish (Heavy Put Unwinding)", "strong_long_unwinding", "🔴"
            else:
                category, category_key, bias_emoji = "Long Unwinding", "long_unwinding", "🟠"
        elif ce_building and pe_building:
            category, category_key, bias_emoji = "Bearish OI Bias (Dual Build-Up)", "bearish_dual", "🔴"
        else:
            category, category_key, bias_emoji = "Bearish OI Bias", "bearish_generic", "🔴"

    elif bias == "Bearish Lean":
        # v3 NEW: two-sided combo — fresh Call writing AND Put unwinding
        # at once is a stronger bearish hint than either alone.
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

    # ── Reasons: built dynamically from whichever signals actually fired ──
    reasons = []
    tilt_word = "Put" if window_diff > 0 else ("Call" if window_diff < 0 else "balanced")
    reasons.append(
        f"ATM±{ATM_RANGE} window: Put OI {pe_oi_chg_pct:+.1f}% vs Call OI {ce_oi_chg_pct:+.1f}% "
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

    reasons.append(f"Weighted score: {score:+.1f} (Lean ±{LEAN_SCORE_THRESH:.0f} / Full ±{BULLISH_SCORE_THRESH:.0f}) → {bias} bias.")

    # ── ATM-specific observation ───────────────────────────
    if abs(atm_ce_chg) > 0 or abs(atm_pe_chg) > 0:
        dominant = "Call" if abs(atm_ce_chg) > abs(atm_pe_chg) else "Put"
        reasons.append(
            f"At ATM strike ₹{atm:.0f}: CE OI chg={atm_ce_chg:+,} | PE OI chg={atm_pe_chg:+,} "
            f"— {dominant} side dominant at the money."
        )

    # ── Volume signal ──────────────────────────────────────
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
        "ce_oi_chg"         : int(ce_oi_chg),      # ATM±ATM_RANGE window Δ (contracts)
        "pe_oi_chg"         : int(pe_oi_chg),      # ATM±ATM_RANGE window Δ (contracts)
        "ce_oi_chg_pct"     : round(ce_oi_chg_pct, 2),
        "pe_oi_chg_pct"     : round(pe_oi_chg_pct, 2),
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
        "_df"               : df,   # kept internally for extended-metric calc; stripped before export
    }

# ─────────────────────────────────────────────
# EXTENDED CARD METRICS
# ─────────────────────────────────────────────
def _find_highest_addition(df, option_type):
    """Strike with the largest positive OI addition on one side."""
    sub = df[df["option_type"].str.upper() == option_type].copy()
    if sub.empty:
        return None, 0
    sub["chg"] = sub["oi"] - sub["prev_oi"]
    idx = sub["chg"].idxmax()
    row = sub.loc[idx]
    return float(row["strike_price"]), float(row["chg"])

def _find_max_oi_strike(df, option_type):
    """Strike with the largest total OI on one side (classic support/resistance)."""
    sub = df[df["option_type"].str.upper() == option_type]
    if sub.empty:
        return None
    idx = sub["oi"].idxmax()
    return float(sub.loc[idx, "strike_price"])

def compute_confidence(r):

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

    # Full-chain Dual Build-Up ratio vote
    if r["full_ce_chg"] > 0 and r["full_pe_chg"] > 0:
        ce_l = r["full_ce_chg"] / 100000
        pe_l = r["full_pe_chg"] / 100000
        if max(ce_l, pe_l) >= FULL_CHAIN_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l if pe_l else float("inf")
            if ratio >= FULL_CHAIN_RATIO_GATE or ratio <= 1 / FULL_CHAIN_RATIO_GATE:
                total_votes += 1
                if ratio >= FULL_CHAIN_RATIO_GATE:
                    bearish_votes += 1
                else:
                    bullish_votes += 1

    # v3 NEW: Full-chain Dual UNWINDING ratio vote (mirrors the build-up
    # vote above). Call unwinding faster than Put = bullish vote.
    if r["full_ce_chg"] < 0 and r["full_pe_chg"] < 0:
        ce_l = abs(r["full_ce_chg"]) / 100000
        pe_l = abs(r["full_pe_chg"]) / 100000
        if max(ce_l, pe_l) >= FULL_CHAIN_UNWIND_RATIO_MIN_LAKH:
            ratio = ce_l / pe_l if pe_l else float("inf")
            if ratio >= FULL_CHAIN_UNWIND_RATIO_GATE or ratio <= 1 / FULL_CHAIN_UNWIND_RATIO_GATE:
                total_votes += 1
                if ratio >= FULL_CHAIN_UNWIND_RATIO_GATE:
                    bullish_votes += 1   # Call unwinding faster = bullish
                else:
                    bearish_votes += 1   # Put unwinding faster = bearish

    # v3 NEW: ATM-zone raw contract tilt vote
    zone_ce_l = r["ce_oi_chg"] / 100000
    zone_pe_l = r["pe_oi_chg"] / 100000
    if max(abs(zone_ce_l), abs(zone_pe_l)) >= ZONE_LAKH_MIN:
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
        confidence = min(confidence, 75)  # directional hint, not full confirmation

    return int(max(35, min(95, round(confidence))))

def market_positioning_bullets(r):
    key = r["category_key"]
    if key == "long_build_up":
        return [
            "Fresh Put Writing (Decisive)",
            "Call Side Muted / Unwinding",
            "PCR Improving" if r["pcr_chg"] > 0 else "PCR Stable",
            "Support Strengthening",
        ]
    elif key == "bullish_put_writing":
        return [
            "Fresh Put Writing (Moderate)",
            "Needs Confirmation",
            "PCR Improving" if r["pcr_chg"] > 0 else "PCR Stable",
            "Lean Bullish",
        ]
    elif key == "short_build_up":
        return [
            "Fresh Call Writing (Decisive)",
            "Put Side Muted / Unwinding",
            "PCR Weakening" if r["pcr_chg"] < 0 else "PCR Stable",
            "Resistance Strengthening",
        ]
    elif key == "bearish_call_writing":
        return [
            "Fresh Call Writing (Moderate)",
            "Needs Confirmation",
            "PCR Weakening" if r["pcr_chg"] < 0 else "PCR Stable",
            "Lean Bearish",
        ]
    elif key == "short_covering":
        return [
            "Call Short Covering",
            "Put OI Relatively Stable",
            "Possible Sharp Rally",
            "Watch for Follow-through",
        ]
    elif key == "strong_short_covering":
        return [
            "Heavy Call Short Covering",
            "Resistance Clearing Fast",
            "Put Side Relatively Stable",
            "Momentum Can Extend Quickly",
        ]
    elif key == "long_unwinding":
        return [
            "Put Long Unwinding",
            "Call OI Relatively Steady",
            "Bullish Confidence Fading",
            "Support May Weaken",
        ]
    elif key == "strong_long_unwinding":
        return [
            "Heavy Put Long Unwinding",
            "Support Eroding Fast",
            "Call Side Relatively Stable",
            "Downside Momentum Can Extend",
        ]
    elif key == "bullish_dual":
        return [
            "Dual OI Build-Up",
            "Put Writers Have the Edge",
            "Score Tilts Bullish",
            "Volatility Expected",
        ]
    elif key == "bearish_dual":
        return [
            "Dual OI Build-Up",
            "Call Writers Have the Edge",
            "Score Tilts Bearish",
            "Volatility Expected",
        ]
    elif key == "neutral_unwind":
        return [
            "Both Sides Unwinding",
            "Likely Pre-Expiry Cleanup",
            "Await Fresh OI Confirmation",
        ]
    # ── Lean-tier bullet sets (watchlist-flavoured, not confirmed) ──
    elif key == "bullish_lean_call_unwind":
        return [
            "Call Writers Retreating",
            "No Fresh Put Writing Yet",
            "Directional Hint, Not Confirmed",
            "Watchlist Candidate",
        ]
    elif key == "bullish_lean_put_write_call_unwind":
        return [
            "Fresh Put Writing",
            "Call Writers Retreating Too",
            "Two-Sided Bullish Confirmation",
            "Watchlist Candidate",
        ]
    elif key == "bullish_lean_put_writing":
        return [
            "Early Put Writing",
            "Below Build-Up Threshold",
            "PCR Improving" if r["pcr_chg"] > 0 else "PCR Stable",
            "Watchlist Candidate",
        ]
    elif key == "bullish_lean_dual":
        return [
            "Both Sides Adding, Slight Put Edge",
            "Not Decisive Yet",
            "Volatility Likely",
            "Watchlist Candidate",
        ]
    elif key == "bullish_lean_generic":
        return [
            "Mild Bullish Tilt",
            "No Single Dominant Signal",
            "Watchlist Candidate",
        ]
    elif key == "bearish_lean_call_writing":
        return [
            "Early Call Writing",
            "Below Build-Up Threshold",
            "PCR Weakening" if r["pcr_chg"] < 0 else "PCR Stable",
            "Watchlist Candidate",
        ]
    elif key == "bearish_lean_call_write_put_unwind":
        return [
            "Fresh Call Writing",
            "Put Writers Retreating Too",
            "Two-Sided Bearish Confirmation",
            "Watchlist Candidate",
        ]
    elif key == "bearish_lean_put_unwind":
        return [
            "Put Writers Retreating",
            "No Fresh Call Writing Yet",
            "Directional Hint, Not Confirmed",
            "Watchlist Candidate",
        ]
    elif key == "bearish_lean_dual":
        return [
            "Both Sides Adding, Slight Call Edge",
            "Not Decisive Yet",
            "Volatility Likely",
            "Watchlist Candidate",
        ]
    elif key == "bearish_lean_generic":
        return [
            "Mild Bearish Tilt",
            "No Single Dominant Signal",
            "Watchlist Candidate",
        ]
    else:
        return [
            "No Clear Directional Edge",
            "Range-Bound Possibility",
            "Await Fresh OI Confirmation",
        ]

def conclusion_text(r):
    key = r["category_key"]
    mp_line = ""
    if r["max_pain_meaningful"]:
        mp_line = (
            f" Max Pain sitting {abs(r['max_pain_dist_pct']):.1f}% "
            f"{'above' if r['max_pain_dist_pct'] > 0 else 'below'} spot adds further weight to this view."
        )

    if key == "long_build_up":
        return (
            "Decisive Put writing near the ATM zone, confirmed across the full chain and an improving PCR, "
            f"points to a genuine Long Build-Up.{mp_line} Watch for fresh Call writing or Put unwinding to "
            "signal this view is losing strength."
        )
    elif key == "bullish_put_writing":
        return (
            "Put writing is happening and the score leans bullish, but the signal isn't decisive enough yet "
            "to call this a full Long Build-Up — without price/futures confirmation this reads more as a "
            f"bullish tilt than a committed directional bet.{mp_line}"
        )
    elif key == "short_build_up":
        return (
            "Decisive Call writing near the ATM zone, confirmed across the full chain and a weakening PCR, "
            f"points to a genuine Short Build-Up.{mp_line} Watch for fresh Put writing or Call unwinding to "
            "signal this view is losing strength."
        )
    elif key == "bearish_call_writing":
        return (
            "Call writing is happening and the score leans bearish, but the signal isn't decisive enough yet "
            "to call this a full Short Build-Up — this reads more as resistance forming than a committed "
            f"downside bet.{mp_line}"
        )
    elif key == "short_covering":
        return (
            "The move higher is being driven by Call short covering rather than fresh Put writing. This can "
            "produce a sharp rally, but sustainability depends on Put writers actively joining in — treat it "
            "as a reaction rather than a confirmed trend reversal until that happens."
        )
    elif key == "strong_short_covering":
        return (
            f"Call OI is unwinding sharply (ATM zone {r['ce_oi_chg_pct']:+.1f}%) with the Put side "
            f"comparatively stable — resistance is clearing out fast enough that this reads as a genuine "
            f"Bullish move rather than a tentative one.{mp_line}"
        )
    elif key == "long_unwinding":
        return (
            "Put long unwinding shows bullish conviction fading even though Call resistance hasn't moved much. "
            "This pattern often precedes consolidation or a mild pullback unless Put writers step back in "
            "aggressively."
        )
    elif key == "strong_long_unwinding":
        return (
            f"Put OI is unwinding sharply (ATM zone {r['pe_oi_chg_pct']:+.1f}%) with the Call side "
            f"comparatively stable — support is eroding fast enough that this reads as a genuine "
            f"Bearish move rather than a tentative one.{mp_line}"
        )
    elif key == "bullish_dual":
        return (
            f"Both Calls and Puts are seeing fresh additions, but the weighted score ({r['score']:+.1f}) tilts "
            f"in favour of Put writers.{mp_line} Expect a volatile session with a mild bullish lean rather than "
            "a clean directional move."
        )
    elif key == "bearish_dual":
        return (
            f"Both Calls and Puts are seeing fresh additions, but the weighted score ({r['score']:+.1f}) tilts "
            f"in favour of Call writers.{mp_line} Expect a volatile session with a mild bearish lean rather than "
            "a clean directional move."
        )
    elif key == "neutral_unwind":
        return (
            "Positions are being unwound on both sides — most consistent with pre-expiry cleanup or genuine "
            "indecision. Wait for fresh OI build-up before drawing any directional conclusion."
        )
    # ── Lean-tier conclusions ──
    elif key == "bullish_lean_call_unwind":
        return (
            "Call writers are stepping back but fresh Put writing hasn't shown up yet. This is a directional "
            f"hint rather than a confirmed build-up — worth watchlisting for confirmation.{mp_line}"
        )
    elif key == "bullish_lean_put_write_call_unwind":
        return (
            "Fresh Put writing is showing up at the same time Call writers are retreating — both point the "
            f"same way even though neither is decisive alone yet. Worth watchlisting for confirmation.{mp_line}"
        )
    elif key == "bullish_lean_put_writing":
        return (
            "Early-stage Put writing is visible near the ATM zone, but the score isn't strong enough yet to "
            f"call this a Long Build-Up. Treat as an early watchlist signal.{mp_line}"
        )
    elif key == "bullish_lean_dual":
        return (
            f"Both Calls and Puts are seeing some additions with a mild Put edge (score {r['score']:+.1f}). "
            f"Not decisive enough for a full bullish call yet.{mp_line}"
        )
    elif key == "bullish_lean_generic":
        return (
            f"A mild bullish tilt (score {r['score']:+.1f}) without one clearly dominant signal. "
            f"Worth keeping on the watchlist rather than acting on immediately.{mp_line}"
        )
    elif key == "bearish_lean_call_writing":
        return (
            "Early-stage Call writing is visible near the ATM zone, but the score isn't strong enough yet to "
            f"call this a Short Build-Up. Treat as an early watchlist signal.{mp_line}"
        )
    elif key == "bearish_lean_call_write_put_unwind":
        return (
            "Fresh Call writing is showing up at the same time Put writers are retreating — both point the "
            f"same way even though neither is decisive alone yet. Worth watchlisting for confirmation.{mp_line}"
        )
    elif key == "bearish_lean_put_unwind":
        return (
            "Put writers are stepping back but fresh Call writing hasn't shown up yet. This is a directional "
            f"hint rather than a confirmed build-up — worth watchlisting for confirmation.{mp_line}"
        )
    elif key == "bearish_lean_dual":
        return (
            f"Both Calls and Puts are seeing some additions with a mild Call edge (score {r['score']:+.1f}). "
            f"Not decisive enough for a full bearish call yet.{mp_line}"
        )
    elif key == "bearish_lean_generic":
        return (
            f"A mild bearish tilt (score {r['score']:+.1f}) without one clearly dominant signal. "
            f"Worth keeping on the watchlist rather than acting on immediately.{mp_line}"
        )
    else:
        return (
            f"No signal cluster is strong enough to tilt the score meaningfully (score {r['score']:+.1f}). "
            "Market participants appear to be waiting for a fresh catalyst before committing to either side — "
            "a wait-and-watch approach is advisable until clearer OI trends emerge."
        )

def compute_card_metrics(r):
    df = r["_df"]

    highest_pe_strike, highest_pe_chg = _find_highest_addition(df, "PE")
    highest_ce_strike, highest_ce_chg = _find_highest_addition(df, "CE")

    support    = _find_max_oi_strike(df, "PE")
    resistance = _find_max_oi_strike(df, "CE")

    # Dominant writers via CUMULATIVE OI change (absolute contracts)
    if r["pe_oi_chg"] > r["ce_oi_chg"]:
        dominant_writers = "✅ Put Writers"
    elif r["ce_oi_chg"] > r["pe_oi_chg"]:
        dominant_writers = "✅ Call Writers"
    else:
        dominant_writers = "⚖️ Mixed / Balanced"

    r["confidence"]            = compute_confidence(r)
    r["highest_pe_add_strike"] = highest_pe_strike
    r["highest_pe_add_chg"]    = highest_pe_chg
    r["highest_ce_add_strike"] = highest_ce_strike
    r["highest_ce_add_chg"]    = highest_ce_chg
    r["support"]               = support
    r["resistance"]            = resistance
    r["dominant_writers"]      = dominant_writers
    r["positioning"]           = market_positioning_bullets(r)
    r["conclusion"]            = conclusion_text(r)
    return r

# ─────────────────────────────────────────────
# CARD-STYLE OUTPUT (kept for optional/manual use)
# ─────────────────────────────────────────────
def print_card(r):
    sym_clean = r["symbol"].replace("NSE:", "").replace("-EQ", "").replace("-INDEX", "")

    bias_arrow = "⬆" if "Bullish" in r["bias"] else ("⬇" if "Bearish" in r["bias"] else "➡")
    pcr_arrow  = "↑" if r["pcr_chg"] > 0 else ("↓" if r["pcr_chg"] < 0 else "→")

    ce_lakhs = r["full_ce_chg"] / 100000
    pe_lakhs = r["full_pe_chg"] / 100000

    def fmt_strike(v):
        return f"{v:.0f}" if v is not None else "-"

    print(sym_clean)
    print(f"{r['bias_emoji']} {r['category']}")
    label = "Bull Score" if r["score"] >= 0 else "Bear Score"
    print(f"{label} : {r['score']:+.1f}")
    print()
    for item in r["score_breakdown"]:
        print(f"{item['points']:>+6.1f}  {item['label']}")
    print("-" * 32)
    print(f"{r['score']:>+6.1f}  Final")
    print()
    print(f"Confidence    : {r['confidence']}%")
    print(f"Spot          : {r['spot']:.0f}")
    print(f"ATM           : {r['atm']:.0f}")
    print(f"PCR           : {r['pcr']:.2f} {pcr_arrow} ({r['pcr_chg']:+.2f})")
    print(f"Call OI       : {r['full_ce_chg_pct']:+.1f}%")
    print(f"Put OI        : {r['full_pe_chg_pct']:+.1f}%")
    print("Net OI Flow")
    print(f"CE : {ce_lakhs:+.2f}L")
    print(f"PE : {pe_lakhs:+.2f}L")
    print(f"ATM±{ATM_RANGE} Zone Contracts Added")
    print(f"CE : {r['ce_oi_chg']:+,}")
    print(f"PE : {r['pe_oi_chg']:+,}")
    print("Dominant Writers")
    print(r["dominant_writers"])
    print("Highest Put Addition")
    print(fmt_strike(r["highest_pe_add_strike"]))
    print("Highest Call Addition")
    print(fmt_strike(r["highest_ce_add_strike"]))
    print("Support")
    print(fmt_strike(r["support"]))
    print("Resistance")
    print(fmt_strike(r["resistance"]))
    if r["max_pain_meaningful"]:
        print("Max Pain")
        print(f"{fmt_strike(r['max_pain'])}  ({r['max_pain_dist_pct']:+.1f}% from spot)")
    print("Market Positioning")
    for bullet in r["positioning"]:
        print(f"• {bullet}")
    print("Expected Bias")
    print(f"{bias_arrow} {r['bias']}")
    print("Confidence")
    print(f"{r['confidence']}%")
    print("Conclusion")
    print(r["conclusion"])
    print("━" * 46)

# ─────────────────────────────────────────────
# SUMMARY TABLE
# ─────────────────────────────────────────────
SUMMARY_TABLE_COLUMNS = [
    "symbol", "bias", "bias_emoji", "category", "score", "confidence", "pcr", "prev_pcr",
    "support", "resistance", "ce_net_contracts", "pe_net_contracts",
    "ce_net_lakhs", "pe_net_lakhs", "atm_ce_delta_contracts", "atm_pe_delta_contracts",
    "score_breakdown",
]

def format_breakdown_text(r):
    """Renders score_breakdown as one readable string."""
    parts = [f"{item['points']:+.1f} {item['label']}" for item in r.get("score_breakdown", [])]
    return " | ".join(parts)

def build_summary_table(results):

    rows = []
    for r in results:
        sym  = r["symbol"].replace("NSE:", "").replace("-EQ", "")
        ce_lakhs = r["full_ce_chg"] / 100000
        pe_lakhs = r["full_pe_chg"] / 100000
        rows.append([
            sym,
            r["bias"],
            r["bias_emoji"],
            r["category"],
            r["score"],
            r["confidence"],
            r["pcr"],
            r["prev_pcr"],
            r["support"],
            r["resistance"],
            r["full_ce_chg"],
            r["full_pe_chg"],
            round(ce_lakhs, 2),
            round(pe_lakhs, 2),
            r["ce_oi_chg"],   # ATM±5 CE Δ (contracts)
            r["pe_oi_chg"],   # ATM±5 PE Δ (contracts)
            format_breakdown_text(r),
        ])
    return {"columns": SUMMARY_TABLE_COLUMNS, "rows": rows}

def format_summary_table_lines(results):

    RED   = "\033[91m"
    GREEN = "\033[92m"
    RESET = "\033[0m"

    def color_num(val, width):
        text = f"{val:+,}"
        color = GREEN if val >= 0 else RED
        pad = max(width - len(text), 0)
        return (" " * pad) + f"{color}{text}{RESET}"

    lines = []
    lines.append(
        f"  {'Symbol':<14} | {'CE Net':>14} | {'PE Net':>14} | "
        f"{'ATM±5 CE Δ':>14} | {'ATM±5 PE Δ':>14}"
    )
    lines.append(
        f"  {'-'*14} | {'-'*14} | {'-'*14} | {'-'*14} | {'-'*14}"
    )
    for r in results:
        sym  = r["symbol"].replace("NSE:", "").replace("-EQ", "")
        ce_net_str = color_num(r["full_ce_chg"], 14)
        pe_net_str = color_num(r["full_pe_chg"], 14)
        atm_ce_str = color_num(r["ce_oi_chg"], 14)
        atm_pe_str = color_num(r["pe_oi_chg"], 14)
        lines.append(
            f"  {sym:<14} | {ce_net_str} | {pe_net_str} | {atm_ce_str} | {atm_pe_str}"
        )
    return lines

def print_summary_table(results):
    """Prints the final summary table only."""
    for line in format_summary_table_lines(results):
        print(line)

# ─────────────────────────────────────────────
# JSON EXPORT  (single fixed file: OI_data.json)
# ─────────────────────────────────────────────
def _json_safe(value):
    """Convert numpy/pandas scalar types into plain Python types for json.dump."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    return value

def build_json_report(results, failed, trading_day, console_text):

    clean_results = []
    for r in results:
        rec = {k: v for k, v in r.items() if k != "_df"}
        rec = {k: _json_safe(v) for k, v in rec.items()}
        clean_results.append(rec)

    raw_table = build_summary_table(results)
    summary_table = {
        "columns": raw_table["columns"],
        "rows": [[_json_safe(cell) for cell in row] for row in raw_table["rows"]],
    }
    summary_table_text = "\n".join(format_summary_table_lines(results))

    report = {
        "trading_day"        : trading_day,
        "generated_at"       : datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "stocks"             : clean_results,
        "summary_table"      : summary_table,
        "summary_table_text" : summary_table_text,
        "failed"             : [{"symbol": s, "error": e} for s, e in failed],
        "console_output"     : console_text,
    }
    return report

def save_json_report(report):
    """Always saves to the same fixed filename, overwriting the previous run."""
    filepath = os.path.join(JSON_OUT_DIR, JSON_OUT_FILE)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return filepath

def save_summary_table_csv(results):

    import csv
    table = build_summary_table(results)

    # 1) timestamped copy inside oi_reports/
    filepath = os.path.join(JSON_OUT_DIR, CSV_OUT_FILE)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(table["columns"])
        writer.writerows(table["rows"])

    # 2) fixed-name cache copy directly in BASE_DIR (was missing before)
    try:
        with open(OI_CACHE_CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(table["columns"])
            writer.writerows(table["rows"])
        print(f"📊 oi_cache CSV saved to : {OI_CACHE_CSV_FILE}")
    except Exception as e:
        print(f"⚠ Failed to save oi_cache CSV ({OI_CACHE_CSV_FILE}): {e}")

    return filepath

def process_oi_for_stock(stock, fyers_obj):
    """
    Runs the full OI fetch + classify + card-metrics pipeline for a single
    stock. Returns (result_dict_or_None, failed_tuple_or_None).
    """
    sym_clean = stock.replace("NSE:", "").replace("-EQ", "").replace("-INDEX", "")
    fno_sym   = f"NSE:{sym_clean}-EQ"

    try:
        # Resolve which expiry's contracts to pull. If the nearest expiry
        # is today/tomorrow, this rolls forward to the NEXT month's
        # contracts instead of the about-to-expire ones.
        expiry_list = get_expiry_list(fyers_obj, fno_sym)
        target_expiry_ts, nearest_expiry_ts, rolled_forward = select_target_expiry(expiry_list)

        # ── VALIDATION PRINT: show exactly which expiry is being used ──
        if target_expiry_ts:
            expiry_date_str = datetime.fromtimestamp(target_expiry_ts, IST).strftime("%Y-%m-%d")
            if rolled_forward:
                nearest_date_str = datetime.fromtimestamp(nearest_expiry_ts, IST).strftime("%Y-%m-%d")
                print(f"   📅 {sym_clean}: nearest expiry {nearest_date_str} is today/tomorrow "
                      f"→ rolled forward to {expiry_date_str}")
            else:
                print(f"   📅 {sym_clean}: using expiry {expiry_date_str}")
        else:
            print(f"   📅 {sym_clean}: could not resolve expiry list — using Fyers default (nearest)")

        resp = fetch_option_chain(fyers_obj, fno_sym, strike_count=15, expiry_ts=target_expiry_ts)

        if not resp or resp.get("s") != "ok":
            err = resp.get("message", str(resp)) if resp else "No response"
            return None, (sym_clean, err)

        data          = resp.get("data", {})
        options_chain = data.get("optionsChain") or data.get("options_chain") or []

        if not options_chain:
            return None, (sym_clean, "Empty chain")

        # Get spot price
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

        df = normalize_chain(options_chain)

        if df.empty:
            return None, (sym_clean, "Empty after normalize")

        all_strikes = df["strike_price"].dropna().unique()
        if spot and len(all_strikes):
            atm_check = min(all_strikes, key=lambda s: abs(s - spot))
            if abs(atm_check - spot) / spot > 0.15:
                spot = float(np.median(sorted(all_strikes)))

        result = classify_sentiment(sym_clean, df, spot)
        if result:
            result = compute_card_metrics(result)
            return result, None
        else:
            return None, (sym_clean, "Empty CE or PE")

    except Exception as e:
        return None, (sym_clean, str(e))

# ─────────────────────────────────────────────
# NEGATIVE-LEG FILTER
#   If CE Net, CE ATM, PE Net, and PE ATM are ALL negative numbers,
#   ignore that stock from the matched list — zero is treated as a
#   non-negative (i.e. "positive") value here, not negative.
# ─────────────────────────────────────────────
def all_legs_negative(r):
    legs = [r["full_ce_chg"], r["ce_oi_chg"], r["full_pe_chg"], r["pe_oi_chg"]]
    return all(v < 0 for v in legs)


rows        = []
final_list  = []
oi_results  = []   # OI classification results, filled in AS WE GO
oi_failed   = []   # OI classification failures, filled in AS WE GO

# Capture console output for the JSON report (same as before, but now
# spans the whole combined loop instead of only the Part-2 section)
oi_console_buffer = io.StringIO()

class _Tee:
    """Writes to both the real stdout and an in-memory buffer."""
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
    def flush(self):
        for s in self.streams:
            s.flush()

import sys
_real_stdout = sys.stdout
sys.stdout = _Tee(_real_stdout, oi_console_buffer)

try:
    trading_day, weekday_num = last_trading_day()
    day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday_num]

    for stock in STOCKS:

        print(f"\nProcessing: {stock}")

        out = get_stock_data(stock, date_to_check)
        reason = out[7]

        rows.append({
            "Symbol": stock,
            "Reject Reason": reason,
            "First Body%": out[8],
            "Second Body%": out[9]
        })

        # ------------------------------------------------
        # AS SOON AS ACCEPTED -> RUN OI CLASSIFICATION NOW
        # ------------------------------------------------
        if reason and "Accepted" in reason:

            sym_clean   = stock.replace("NSE:", "").replace("-EQ", "")
            fyers_symbol = f"NSE:{sym_clean}-EQ"
            final_list.append(fyers_symbol)

            print(f"➡️  {sym_clean} accepted — running OI classification now...")

            oi_result, oi_fail = process_oi_for_stock(fyers_symbol, fyers)

            if oi_result:
                oi_results.append(oi_result)
                print(f"✅ {sym_clean} OI classified: {oi_result['bias_emoji']} {oi_result['category']} "
                      f"(score {oi_result['score']:+.1f})")
            else:
                oi_failed.append(oi_fail)
                print(f"⚠️  {sym_clean} OI classification failed: {oi_fail[1]}")

    # ------------------------------
    # RESULT DATAFRAME (screener)
    # ------------------------------
    df = pd.DataFrame(rows)

    print("\n==============================")
    print("ACCEPTED STOCKS")
    print("==============================")

    accepted_df = df[
        df["Reject Reason"].str.contains(
            "Accepted",
            na=False
        )
    ]

    print(accepted_df)

    print("\nFINAL ACCEPTED STOCKS:")
    print(final_list)

    output_file = f"{BASE_DIR}/Stocks.txt"
    output_file = f"{BASE_DIR}/matched_stocks.txt"

    # Save condition: ATM PE Δ must equal Full-chain PE Δ, AND we must
    # NOT be looking at a stock where all four legs (CE Net, CE ATM,
    # PE Net, PE ATM) are negative (zero counts as non-negative, so it
    # does not trigger this exclusion).
    pe_delta_match_list = [
        f"NSE:{r['symbol']}-EQ"
        for r in oi_results
        if r["pe_oi_chg"] == r["full_pe_chg"] and not all_legs_negative(r)
    ]

    try:

        with open(output_file, "w") as f:
            f.write(f"STOCKS={pe_delta_match_list}")

        print(f"\n✅ Matching stocks (ATM PE Δ == Full-chain PE Δ) saved to:")
        print(date_to_check)
        print(output_file)
        print(pe_delta_match_list)

    except Exception as e:

        print("\n❌ Error saving matching stocks:", e)

    # ------------------------------------------------------
    # OI SUMMARY (results were already collected inline above)
    # ------------------------------------------------------
    print(f"\n{'='*60}")
    print("  OI SENTIMENT SUMMARY")
    print(f"  Trading day: {trading_day} ({day_name})")
    print(f"{'='*60}\n")

    # Sorted ascending by ATM±5 CE Δ (most negative CE Δ at top,
    # most positive CE Δ at bottom).
    oi_results.sort(key=lambda r: r["ce_oi_chg"])

    # Console table normally shows ONLY the stocks that matched the
    # PE-delta condition (same set that was saved to matched_stocks.txt).
    # NEW: if NOTHING matched (list is empty), fall back to showing the
    # full OI table (same columns) for ALL accepted stocks instead, so
    # you're never left with a blank screen. CSV/JSON below still get
    # ALL oi_results either way — unchanged.
    if pe_delta_match_list:
        console_table_results = [
            r for r in oi_results
            if f"NSE:{r['symbol']}-EQ" in pe_delta_match_list
        ]
    else:
        console_table_results = oi_results
        print("  ⚠️  No stocks matched the ATM PE Δ == Full-chain PE Δ condition.")
        print("  Showing the OI table for ALL accepted stocks instead:\n")

    if not console_table_results:
        print("  No results to display (no accepted stocks produced OI data).")
    else:
        print_summary_table(console_table_results)

    if oi_failed:
        print(f"\n  ⚠️  Failed: {', '.join(f[0] for f in oi_failed)}")
        for sym, err in oi_failed:
            print(f"     {sym}: {err}")

    print(f"\n{'='*60}\n")

    oi_console_text = oi_console_buffer.getvalue()
    oi_report    = build_json_report(oi_results, oi_failed, trading_day, oi_console_text)
    oi_json_path = save_json_report(oi_report)
    print(f"💾 Full report saved to : {oi_json_path}")

    if oi_results:
        oi_csv_path = save_summary_table_csv(oi_results)
        print(f"📊 Summary table (CSV) saved to : {oi_csv_path}")
    else:
        print("⚠ No oi_results collected this run — oi_cache1.csv and the "
              "timestamped OI summary CSV were NOT written (nothing to save).")

finally:
    sys.stdout = _real_stdout

# ============================================================
# CHART CONFIG DATE INPUT — MOVED TO THE VERY END
#   (asked only after screening + OI classification + summary +
#    JSON/CSV saving are all fully done)
# ============================================================
config_file = f"{BASE_DIR}/chart_config.json"

while True:

    print("\n=== Enter date range for chart generation ===")

    d5_start = input(
        "Enter START date (YYYY-MM-DD): "
    )

    d15_end = input(
        "Enter END date (YYYY-MM-DD): "
    )

    print("\nYou entered:")
    print(f"  Start : {d5_start}")
    print(f"  End   : {d15_end}")

    confirm = input(
        "\nSave these dates? (y/n): "
    ).strip().lower()

    if confirm == "y":

        config = {
            "5m_start": d5_start,
            "15m_end": d15_end,
        }

        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)

        print("\n✅ chart_config.json saved successfully")
        print(f"📂 File location: {config_file}")
        #SamCondition+OI with few columns, sorting oi

        break

    else:

        print("\n❌ Discarded. Enter again...\n")