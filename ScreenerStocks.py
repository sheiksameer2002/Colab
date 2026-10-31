import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time

# ── Credentials ──────────────────────────────────────────────────────────────
EMAIL    = "sameersheik446@gmail.com"
PASSWORD = "Sameer@123"

# ── URLs ──────────────────────────────────────────────────────────────────────
BASE_URL  = "https://www.screener.in"
LOGIN_URL = "https://www.screener.in/login/"

# ── ✅ Just paste your Screener URL here (no page number needed) ──────────────
SCREENER_URL = "https://www.screener.in/screen/raw/?sort=debt+to+equity&order=desc&source_id=&query=Market+Capitalization+%3E+900+AND%0D%0AMarket+Capitalization+%3C+80000+AND%0D%0ACurrent+price+%3C%3D550+AND%0D%0ACurrent+price+%3E%3D50+AND%0D%0ADebt+to+equity+%3C+1.11+AND%0D%0AVolume+1week+average+%3E+100000%0D%0A"

# ── Script directory (for saving output files alongside this .py file) ───────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Build page URL by appending limit & page ─────────────────────────────────
def build_url(base, page_num):
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}limit=50&page={page_num}"

# ── Session setup ─────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": BASE_URL,
})

# ── Step 1: Get CSRF token ────────────────────────────────────────────────────
print("Fetching login page...")
resp = session.get(LOGIN_URL)
soup = BeautifulSoup(resp.text, "html.parser")
csrf = soup.find("input", {"name": "csrfmiddlewaretoken"})["value"]
print("CSRF token obtained.")

# ── Step 2: Login ─────────────────────────────────────────────────────────────
print("Logging in...")
login_data = {
    "csrfmiddlewaretoken": csrf,
    "username": EMAIL,
    "password": PASSWORD,
    "next": "/",
}
resp = session.post(LOGIN_URL, data=login_data, headers={"Referer": LOGIN_URL})
print("Login done. Starting scrape...\n")

# ── Step 3: Scrape pages ──────────────────────────────────────────────────────
STOCKS       = []
all_rows     = []
column_names = []

for page_num in range(1, 15):  # pages 1 to 14
    url = build_url(SCREENER_URL, page_num)
    print(f"Fetching page {page_num} → {url}")

    resp = session.get(url)
    time.sleep(1.5)  # polite delay
    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Get column headers on first page only ─────────────────────────────────
    if page_num == 1:
        thead = soup.select_one("table thead tr")
        if thead:
            column_names = [th.get_text(strip=True) for th in thead.find_all("th")]
            print(f"Columns: {column_names}")

    # ── Get table rows ─────────────────────────────────────────────────────────
    rows = soup.select("table tbody tr")

    if not rows:
        print(f"No rows found on page {page_num}. All pages done!")
        break

    print(f"  → {len(rows)} stocks found on page {page_num}")

    for row in rows:
        cols = row.find_all("td")
        row_data = [c.get_text(strip=True) for c in cols]
        if not row_data:
            continue

        stock_url = ""
        try:
            link = cols[1].find("a")
            if link and "/company/" in link.get("href", ""):
                href = link["href"]
                stock_url = BASE_URL + href if href.startswith("/") else href
                symbol = href.split("/company/")[-1].strip("/").split("/")[0].upper()
                # Skip numeric-only symbols like 534120
                if not symbol.isdigit():
                    STOCKS.append(f"NSE:{symbol}-EQ")
        except Exception:
            pass

        row_data.append(stock_url)
        all_rows.append(row_data)

print(f"\nScraping complete! Total pages fetched: {page_num}")

# ── Step 4: Save to Excel (next to script) ────────────────────────────────────
data = pd.DataFrame(all_rows)

if column_names and not data.empty:
    col_headers = column_names + ["Stock URL"]
    while len(col_headers) < data.shape[1]:
        col_headers.append(f"Extra_{len(col_headers)}")
    data.columns = col_headers[:data.shape[1]]

excel_output = os.path.join(SCRIPT_DIR, "screener_data_mobile.xlsx")
data.to_excel(excel_output, index=False)
print(f"✅ Excel saved to {excel_output}")

# ── Step 5: Sort alphabetically + remove duplicates ──────────────────────────
STOCKS = sorted(set(STOCKS))
print(f"\nTotal stocks found: {len(STOCKS)}")

# ── Step 6: Save to txt in both formats (next to script) ─────────────────────
txt_output = os.path.join(SCRIPT_DIR, "AllStocks.txt")

# Format 1: NSE:SYMBOL-EQ
stocks_line = "STOCKS=[" + ",".join(f"'{s}'" for s in STOCKS) + "]"

# Format 2: SYMBOL.NS  (Yahoo Finance style)
ystocks = [s.replace("NSE:", "").replace("-EQ", "") + ".NS" for s in STOCKS]
ystocks_line = "YSTOCKS=[" + ",".join(f"'{s}'" for s in ystocks) + "]"

with open(txt_output, "w") as f:
    f.write(stocks_line + "\n\n")
    f.write(ystocks_line)

print(f"✅ Saved to {txt_output}")
print(f"\n── STOCKS LIST ──")
print(stocks_line)
print(f"\n── YSTOCKS LIST ──")
print(ystocks_line)