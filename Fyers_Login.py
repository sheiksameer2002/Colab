# ============================================================
# Fyers Auto Login
# ============================================================
import subprocess
import sys

def _ensure(pip_name, import_name=None):
    """Install pip_name if import_name (or pip_name) isn't importable."""
    import_name = import_name or pip_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"📦 Installing missing package: {pip_name} ...")
        subprocess.run([sys.executable, "-m", "pip", "install", pip_name, "-q"], check=True)

_ensure("fyers-apiv3", "fyers_apiv3")
_ensure("pyotp", "pyotp")

import requests
import pyotp
import os
from time import sleep
from datetime import datetime
from fyers_apiv3 import fyersModel

# ─── YOUR CREDENTIALS ───────────────────────────────────────
CLIENT_ID    = '6KX2O3OQK4-100'
SECRET_KEY   = 'MQ132H8RJS'
REDIRECT_URI = 'https://www.google.com/'

FYERS_ID     = 'YS09945'
TOTP_SECRET  = 'NPXD6XPIINGYFXDLIAQAJ7O3KTGX7URU'   # base32 key from Fyers 2FA setup
PIN          = '8978'                    # your 4-digit PIN
# ────────────────────────────────────────────────────────────

BASE_DIR   = r'C:\Users\sheik\PycharmProjects\MyProject'
TOKEN_PATH = os.path.join(BASE_DIR, 'token.txt')

URL_SEND_OTP   = 'https://api-t2.fyers.in/vagator/v2/send_login_otp'
URL_VERIFY_OTP = 'https://api-t2.fyers.in/vagator/v2/verify_otp'
URL_VERIFY_PIN = 'https://api-t2.fyers.in/vagator/v2/verify_pin'
URL_TOKEN      = 'https://api-t1.fyers.in/api/v3/token'

APP_ID   = CLIENT_ID.split('-')[0]
APP_TYPE = '100'

def auto_login():
    if datetime.now().second % 30 > 27:
        sleep(4)

    # Step 1: Send OTP
    r1 = requests.post(URL_SEND_OTP, json={'fy_id': FYERS_ID, 'app_id': '2'}).json()
    assert r1.get('s') == 'ok', f"OTP send failed: {r1}"

    # Step 2: Verify TOTP
    r2 = requests.post(URL_VERIFY_OTP, json={
        'request_key': r1['request_key'],
        'otp': pyotp.TOTP(TOTP_SECRET).now()
    }).json()
    assert r2.get('s') == 'ok', f"OTP verify failed: {r2}"

    # Step 3: Verify PIN
    r3 = requests.post(URL_VERIFY_PIN, json={
        'request_key': r2['request_key'],
        'identity_type': 'pin',
        'identifier': PIN
    }).json()
    assert r3.get('s') == 'ok', f"PIN verify failed: {r3}"

    # Step 4: Get auth_code
    ses = requests.Session()
    ses.headers.update({'authorization': f"Bearer {r3['data']['access_token']}"})

    r4 = ses.post(URL_TOKEN, json={
        'fyers_id': FYERS_ID,
        'app_id': APP_ID,
        'redirect_uri': REDIRECT_URI,
        'appType': APP_TYPE,
        'code_challenge': '',
        'state': 'auto',
        'scope': '',
        'nonce': '',
        'response_type': 'code',
        'create_cookie': True
    }).json()
    assert r4.get('s') == 'ok', f"Auth token failed: {r4}"

    auth_code = r4['Url'].split('auth_code=')[1].split('&')[0]

    # Step 5: Exchange for access_token
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type='code',
        grant_type='authorization_code'
    )
    session.set_token(auth_code)
    resp = session.generate_token()

    access_token = resp.get('access_token')
    assert access_token, f"Access token exchange failed: {resp}"

    # Step 6: Save to token.txt
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(TOKEN_PATH, 'w') as f:
        f.write(f'access_token = "{access_token}"')

    print(f'✅ Token saved to {TOKEN_PATH}')
    print(f'access_token = "{access_token}"')
    return access_token

access_token = auto_login()