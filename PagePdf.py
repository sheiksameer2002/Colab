import sys
import subprocess
import importlib.util
import time


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure(pkg, import_name=None):
    if importlib.util.find_spec(import_name or pkg) is None:
        log(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])


ensure("playwright")
subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"],
                      stdout=subprocess.DEVNULL)

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:5000/"
OUT = "dashboard_full.pdf"
VIEWPORT = {"width": 1920, "height": 1080}   # layout width of the dashboard
PAGE_H = 1600        # height of each PDF page in CSS px (keep under ~19000)
SETTLE_MS = 1000     # wait after loading the copy
FULL_WIDTH = False   # True = print ALL columns (ignores your sideways scroll position)

HIDE_SCROLLBARS_CSS = """
*::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
* { scrollbar-width: none !important; }
"""

# runs in the VISIBLE browser: snapshot DOM, canvases -> images, no scripts
SNAPSHOT_JS = """
() => {
  // find the vertical scroller and mark it
  let best = document.scrollingElement, bestExtra = best.scrollHeight - best.clientHeight;
  for (const el of document.querySelectorAll('*')) {
    const oy = getComputedStyle(el).overflowY;
    if (!/(auto|scroll)/.test(oy)) continue;
    const extra = el.scrollHeight - el.clientHeight;
    if (el.clientHeight > 200 && extra > bestExtra) { best = el; bestExtra = extra; }
  }
  document.querySelectorAll('[data-pdf-scroller]').forEach(e => e.removeAttribute('data-pdf-scroller'));
  best.setAttribute('data-pdf-scroller', '1');
  best.setAttribute('data-pdf-left', String(best.scrollLeft));

  const clone = document.documentElement.cloneNode(true);
  const srcCanvases = document.querySelectorAll('canvas');
  const dstCanvases = clone.querySelectorAll('canvas');
  srcCanvases.forEach((c, i) => {
    try {
      const img = document.createElement('img');
      img.src = c.toDataURL('image/png');
      const r = c.getBoundingClientRect();
      img.style.width = (c.style.width || r.width + 'px');
      img.style.height = (c.style.height || r.height + 'px');
      img.style.display = getComputedStyle(c).display;
      dstCanvases[i].replaceWith(img);
    } catch (e) {}
  });
  clone.querySelectorAll('script').forEach(s => s.remove());
  const base = document.createElement('base');
  base.href = location.href;
  clone.querySelector('head').prepend(base);
  return '<!DOCTYPE html>' + clone.outerHTML;
}
"""

# runs in the HEADLESS copy: expand scroller to full height (and width if asked)
EXPAND_JS = """
(fullWidth) => {
  const sc = document.querySelector('[data-pdf-scroller]');
  const set = (el, k, v) => el.style.setProperty(k, v, 'important');
  if (sc && sc !== document.scrollingElement) {
    const fullH = sc.scrollHeight, fullW = sc.scrollWidth;
    set(sc, 'height', fullH + 'px');
    set(sc, 'max-height', 'none');
    set(sc, 'min-height', fullH + 'px');
    set(sc, 'flex', '0 0 auto');
    set(sc, 'overflow-y', 'hidden');
    if (fullWidth) {
      set(sc, 'width', fullW + 'px');
      set(sc, 'max-width', 'none');
      set(sc, 'overflow-x', 'visible');
    } else {
      sc.scrollLeft = parseFloat(sc.getAttribute('data-pdf-left') || '0');
    }
    let p = sc.parentElement;
    while (p) {
      set(p, 'height', 'auto');
      set(p, 'max-height', 'none');
      set(p, 'overflow', 'visible');
      if (fullWidth) { set(p, 'width', 'auto'); set(p, 'max-width', 'none'); }
      p = p.parentElement;
    }
  }
  return {
    w: Math.ceil(document.documentElement.scrollWidth),
    h: Math.ceil(document.documentElement.scrollHeight)
  };
}
"""

with sync_playwright() as p:
    # ---- visible browser: you set everything up ----
    log("Launching browser...")
    browser = p.chromium.launch(headless=False)
    page = browser.new_context(viewport=VIEWPORT).new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)

    log("Browser is open. Set charts + scroll sideways exactly how you want.")
    input(">>> Press ENTER here when ready to save the PDF... ")

    log("Step 1/3: snapshotting current page state...")
    html = page.evaluate(SNAPSHOT_JS)
    browser.close()

    # ---- headless browser: print to vector PDF ----
    log("Step 2/3: loading snapshot in headless browser...")
    hb = p.chromium.launch(headless=True)
    hp = hb.new_context(viewport=VIEWPORT).new_page()
    hp.emulate_media(media="screen")
    hp.set_content(html, wait_until="load", timeout=60000)
    hp.add_style_tag(content=HIDE_SCROLLBARS_CSS)
    dims = hp.evaluate(EXPAND_JS, FULL_WIDTH)
    hp.wait_for_timeout(SETTLE_MS)
    dims = hp.evaluate("""() => ({w: Math.ceil(document.documentElement.scrollWidth),
                                  h: Math.ceil(document.documentElement.scrollHeight)})""")
    log(f"   content size {dims['w']}x{dims['h']}px")

    log("Step 3/3: printing PDF...")
    hp.add_style_tag(content=f"@page {{ size: {dims['w']}px {PAGE_H}px; margin: 0; }}"
                             "html, body { margin: 0 !important; }")
    hp.pdf(path=OUT, prefer_css_page_size=True, print_background=True)
    hb.close()

log(f"Done. Saved {OUT}")
