"""Frosty's Ledger - a morning newspaper that prints itself.

Reads headlines and the publisher-written summaries from public RSS feeds,
groups the same story across papers, sorts everything into sections and
prints today's edition to index.html (with a dated copy in editions/).

GitHub Actions runs this every morning (.github/workflows/print.yml) and
publishes the result to GitHub Pages. It is safe to run by hand:
    python build_paper.py            build today's edition
    python build_paper.py --open     build, then open it in the browser
    python build_paper.py --no-wait  don't retry if the network is down
    python build_paper.py --if-missing  only print if today's edition doesn't exist yet

Standard library only - nothing to install.
"""
import csv
import gzip
import html
import html.entities
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import cfa_lens

ROOT = Path(__file__).resolve().parent
EDITIONS = ROOT / "editions"
CACHE = ROOT / "cache"
LOGS = ROOT / "logs"
IST = timezone(timedelta(hours=5, minutes=30))

PAPER = "Frosty’s Ledger"
MOTTO = "All the news that’s free to read"
# Where the paper lives online. Absolute links (Google, link previews, the sitemap) start here.
SITE_URL = os.environ.get("SITE_URL", "https://frosty6699.github.io/frostys-ledger/")
AUTHOR = "frosty6699"
REPO_URL = "https://github.com/frosty6699/frostys-ledger"
GOOGLE_VERIFICATION = ""  # the code from Search Console's "HTML tag" option, if you verify that way
DESCRIPTION = ("A free newspaper that prints itself every morning: the day’s top business, markets, "
               "economy, tech and world stories from The Economic Times, Business Standard, Mint, "
               "Reuters, WSJ, FT and more — grouped, ranked and linked to the original.")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
GN = "https://news.google.com/rss/search?q={}&hl=en-IN&gl=IN&ceid=IN:en"

# ---------------------------------------------------------------- sources --
# access: "free"  - open to read
#         "check" - mixed; each article page is checked for a subscriber flag
#         "paid"  - subscriber-only; appears as headline + summary with a lock
# feeds:  (url, section or None to classify by keywords, is a "top stories" feed)
SOURCES = [
    {"key": "ET", "name": "The Economic Times", "short": "ET", "access": "check", "feeds": [
        ("https://economictimes.indiatimes.com/rssfeedstopstories.cms", None, True),
        ("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "markets", False),
        ("https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", "economy", False),
        ("https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms", "companies", False),
        ("https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms", "tech", False),
        ("https://economictimes.indiatimes.com/news/international/rssfeeds/858478126.cms", "world", False)]},
    {"key": "BS", "name": "Business Standard", "short": "BS", "access": "check", "feeds": [
        ("https://www.business-standard.com/rss/home_page_top_stories.rss", None, True),
        ("https://www.business-standard.com/rss/latest.rss", None, False),
        ("https://www.business-standard.com/rss/markets-106.rss", "markets", False),
        ("https://www.business-standard.com/rss/economy-102.rss", "economy", False),
        ("https://www.business-standard.com/rss/companies-101.rss", "companies", False),
        ("https://www.business-standard.com/rss/finance-103.rss", None, False),
        ("https://www.business-standard.com/rss/technology-108.rss", "tech", False)]},
    {"key": "Mint", "name": "Mint", "short": "Mint", "access": "check", "feeds": [
        ("https://www.livemint.com/rss/news", None, True),
        ("https://www.livemint.com/rss/markets", "markets", False),
        ("https://www.livemint.com/rss/economy", "economy", False),
        ("https://www.livemint.com/rss/companies", "companies", False),
        ("https://www.livemint.com/rss/money", None, False),
        ("https://www.livemint.com/rss/technology", "tech", False)]},
    {"key": "BL", "name": "The Hindu BusinessLine", "short": "BusinessLine", "access": "check", "feeds": [
        ("https://www.thehindubusinessline.com/feeder/default.rss", None, True),
        ("https://www.thehindubusinessline.com/markets/feeder/default.rss", "markets", False),
        ("https://www.thehindubusinessline.com/economy/feeder/default.rss", "economy", False),
        ("https://www.thehindubusinessline.com/companies/feeder/default.rss", "companies", False)]},
    {"key": "Hindu", "name": "The Hindu (Business)", "short": "The Hindu", "access": "check", "feeds": [
        ("https://www.thehindu.com/business/feeder/default.rss", None, False)]},
    {"key": "NDTV", "name": "NDTV Profit", "short": "NDTV Profit", "access": "free", "feeds": [
        ("https://feeds.feedburner.com/ndtvprofit-latest", None, False)]},
    {"key": "MC", "name": "Moneycontrol (via Google News)", "short": "Moneycontrol", "access": "free", "gn": True, "feeds": [
        (GN.format("when:1d+site:moneycontrol.com"), None, False)]},
    {"key": "FE", "name": "Financial Express (via Google News)", "short": "Fin. Express", "access": "free", "gn": True, "feeds": [
        (GN.format("when:1d+site:financialexpress.com"), None, False)]},
    {"key": "Reuters", "name": "Reuters (via Google News)", "short": "Reuters", "access": "free", "gn": True, "feeds": [
        (GN.format("when:1d+site:reuters.com"), None, False)]},
    {"key": "CNBC", "name": "CNBC", "short": "CNBC", "access": "check", "feeds": [
        ("https://www.cnbc.com/id/100003114/device/rss/rss.html", None, True),
        ("https://www.cnbc.com/id/10001147/device/rss/rss.html", "companies", False),
        ("https://www.cnbc.com/id/20910258/device/rss/rss.html", "economy", False),
        ("https://www.cnbc.com/id/10000664/device/rss/rss.html", "markets", False),
        ("https://www.cnbc.com/id/100727362/device/rss/rss.html", "world", False),
        ("https://www.cnbc.com/id/19854910/device/rss/rss.html", "tech", False)]},
    {"key": "BBC", "name": "BBC News", "short": "BBC", "access": "free", "feeds": [
        ("https://feeds.bbci.co.uk/news/business/rss.xml", None, False),
        ("https://feeds.bbci.co.uk/news/world/rss.xml", "world", False),
        ("https://feeds.bbci.co.uk/news/technology/rss.xml", "tech", False)]},
    {"key": "Guardian", "name": "The Guardian", "short": "Guardian", "access": "free", "feeds": [
        ("https://www.theguardian.com/uk/business/rss", None, False),
        ("https://www.theguardian.com/business/economics/rss", "economy", False)]},
    {"key": "WSJ", "name": "The Wall Street Journal", "short": "WSJ", "access": "paid", "feeds": [
        ("https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "markets", True),
        ("https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness", "companies", False),
        ("https://feeds.content.dowjones.io/public/rss/socialeconomyfeed", "economy", False),
        ("https://feeds.content.dowjones.io/public/rss/RSSWorldNews", "world", False),
        ("https://feeds.content.dowjones.io/public/rss/RSSWSJD", "tech", False)]},
    {"key": "FT", "name": "Financial Times", "short": "FT", "access": "paid", "feeds": [
        ("https://www.ft.com/rss/home", None, True),
        ("https://www.ft.com/markets?format=rss", "markets", False)]},
    {"key": "Bloomberg", "name": "Bloomberg", "short": "Bloomberg", "access": "paid", "feeds": [
        ("https://feeds.bloomberg.com/markets/news.rss", "markets", False)]},
    {"key": "Economist", "name": "The Economist", "short": "Economist", "access": "paid", "feeds": [
        ("https://www.economist.com/finance-and-economics/rss.xml", "economy", False)]},
    {"key": "NYT", "name": "The New York Times", "short": "NYT", "access": "paid", "feeds": [
        ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", None, False)]},
    {"key": "MW", "name": "MarketWatch", "short": "MarketWatch", "access": "paid", "feeds": [
        ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "markets", False)]},
    # Official releases go to the Regulators' desk, not the news sections.
    {"key": "RBI", "name": "Reserve Bank of India", "short": "RBI", "access": "free", "desk": True, "feeds": [
        ("https://www.rbi.org.in/pressreleases_rss.xml", None, False),
        ("https://www.rbi.org.in/notifications_rss.xml", None, False)]},
    {"key": "Fed", "name": "US Federal Reserve", "short": "Fed", "access": "free", "desk": True, "feeds": [
        ("https://www.federalreserve.gov/feeds/press_all.xml", None, False)]},
]
SRC = {s["key"]: s for s in SOURCES}
# Whose write-up to lead with when several papers carry the same story.
PREFER = ["ET", "BS", "Mint", "BL", "Reuters", "CNBC", "BBC", "Guardian", "Hindu", "NDTV", "FE", "MC"]

TICKERS = [  # label, Yahoo symbol, kind
    ("Nifty 50", "^NSEI", "index"), ("Sensex", "^BSESN", "index"), ("Bank Nifty", "^NSEBANK", "index"),
    ("USD/INR", "INR=X", "fx"), ("Brent", "BZ=F", "usd"), ("Gold", "GC=F", "usd"),
    ("S&P 500", "^GSPC", "index"), ("US 10Y", "^TNX", "yield"),
]

# Your watchlist: label, Yahoo symbol (".NS" = NSE), price kind ("inr" or "usd2"), words that
# mark a headline as being about it. The five banks from the IB project.
WATCHLIST = [
    ("JPMorgan", "JPM", "usd2", [r"jp ?morgan", r"jamie dimon"]),
    ("Goldman Sachs", "GS", "usd2", [r"goldman"]),
    ("Morgan Stanley", "MS", "usd2", [r"morgan stanley"]),
    ("Bank of America", "BAC", "usd2", [r"bank of america", r"\bbofa\b"]),
    ("Citigroup", "C", "usd2", [r"\bciti(group|bank)?\b"]),
]

SECTIONS = [  # id, title, story cards, one-line briefs
    ("markets", "Markets", 7, 10),
    ("economy", "Economy & Policy", 7, 10),
    ("companies", "Companies & Deals", 7, 10),
    ("world", "World", 7, 8),
    ("tech", "Technology", 5, 6),
    ("opinion", "Opinion", 5, 0),
]
SEC_NAME = {k: v for k, v, _, _ in SECTIONS}
SUBS_QUOTA = {"WSJ": 6, "FT": 5, "Bloomberg": 4, "Economist": 4, "NYT": 4, "MW": 4, "premium": 6}

KEYWORDS = {
    "markets": """sensex nifty stock stocks share shares equity equities ipo ipos gmp listing listings bond bonds
        yield yields treasury treasuries rupee forex dollar currency currencies gold silver crude brent oil
        commodity commodities fii fiis fpi fpis dii mutual fund funds sip etf etfs nasdaq dow wall street
        dalal bitcoin crypto rally selloff sell-off derivatives f&o futures midcap smallcap investors
        brokerage target price buy sell trading traders market markets hedge""",
    "economy": """rbi repo monetary inflation cpi wpi gdp fiscal deficit budget gst tax taxes finance ministry
        sitharaman fed federal reserve powell warsh central bank rate hike cut rates interest tariff tariffs
        trade exports imports export import jobs unemployment payrolls employment economy economic recession
        policy government ministry cabinet subsidy pli sebi irdai regulator regulation regulatory niti imf
        world bank reserves liquidity credit msme pmi growth outlook forecast""",
    "companies": """ltd limited company companies firm ceo cfo md chairman board profit profits revenue earnings
        results q1 q2 q3 q4 quarterly acquisition acquire acquires merger deal stake order orders contract
        crore billion million funding startup startups raises valuation layoffs hiring plant factory capacity
        expansion launch launches sales brand retail airline airlines bank banks insurer insurance nbfc
        fintech telecom pharma steel cement auto automaker ev realty hotel hotels conglomerate tata reliance
        adani infosys wipro hdfc icici sbi boeing tesla amazon walmart""",
    "tech": """ai artificial intelligence genai openai chatgpt anthropic nvidia chip chips semiconductor
        semiconductors software saas cloud data centre center cyber cybersecurity hack hacked smartphone
        iphone apple google alphabet microsoft meta 5g quantum robotics tech technology digital""",
    "world": """world global iran israel gaza china chinese xi trump white house russia ukraine putin pakistan
        un unga united nations europe eu uk britain japan war ceasefire sanctions summit g20 g7 brics
        election hormuz houthi middle east nato diplomatic bilateral""",
}
KW = {sec: set(words.split()) for sec, words in KEYWORDS.items()}
SEC_ORDER = ["markets", "economy", "companies", "tech", "world"]

URL_SECTIONS = [("/markets/", "markets"), ("/market/", "markets"), ("/economy/", "economy"),
                ("/companies/", "companies"), ("/industry/", "companies"), ("/world-news/", "world"),
                ("/international/", "world"), ("/world/", "world"), ("/technology/", "tech"),
                ("/tech/", "tech"), ("/info-tech/", "tech")]
BL_CATS = {"markets": "markets", "stocks": "markets", "commodities": "markets", "economy": "economy",
           "agri business": "economy", "money & banking": "economy", "companies": "companies",
           "logistics": "companies", "info-tech": "tech", "science": "tech", "world": "world"}
DROP_CATS = {"pr release", "letters", "visually"}
DROP_PATH = re.compile(r"/(sports?|cricket|entertainment|lifestyle|life-style|astrology|horoscope|photos?|"
                       r"videos?|web-?stories|podcasts?|education|travel|health|food|fashion|books)/")
DROP_TITLE = re.compile(r"horoscope|rashifal|live stream|live score|wordle|crossword|quiz\b|"
                        r"\bpodcast\b|in pics|watch video|photos:|^page \d+ of|\barchives?$|"
                        r"(\bnews\b.*){3}|zodiac|mercury (direct|retrograde)|tarot|numerology|"
                        r"viral video|shocking video|caught on camera", re.I)
OPINION_PATH = re.compile(r"/(opinion|columns?|editorials?|commentisfree|views)/")

STOP = set("""a an the of to in on for and or as at by with from is are was were be been being its it this
    that these those after before over under amid says say said new how why what when where who whom will
    would may might could can up down into than more most here there check live today updates update news
    report reports vs via about against off out not no his her their our your you we they he she i do does
    did has have had just also all any some key top big set get gets amp""".split())

LOCK_SVG = '<svg class="lock" aria-hidden="true"><use href="#lk"/></svg>'


# ---------------------------------------------------------------- helpers --
def log(msg):
    LOGS.mkdir(exist_ok=True)
    line = f"{datetime.now(IST):%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        print(line)
    except Exception:
        pass  # pythonw has no console
    path = LOGS / "build.log"
    lines = path.read_text(encoding="utf-8").splitlines()[-600:] if path.exists() else []
    lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch(url, timeout=20, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "en-IN,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            elif enc == "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (401, 403, 410):  # locked or gone for good; 404s are sometimes a blip
                break
        except Exception as e:  # timeouts, resets, DNS
            last = e
        if attempt + 1 < tries:
            time.sleep(3)
    raise last


def esc(s):
    return html.escape(s or "", quote=True)


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def clean(s):
    s = html.unescape(html.unescape(s or ""))  # a few feeds escape twice
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return d.replace(tzinfo=IST) if d.tzinfo is None else d  # RBI dates carry no zone


def trim_dek(s, title, n=270):
    if not s or s.lower().rstrip(".") == title.lower().rstrip("."):
        return ""
    if s.lower().startswith(title.lower()):
        s = s[len(title):].lstrip(" .:-–—")
    if len(s) < 25:
        return ""
    if len(s) <= n and s[-1] in ".!?”\"'’)":
        return s
    cut = s[:n]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if end >= 100:
        return cut[:end + 1]
    if len(s) > n:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-–") + "…"  # feed cut the sentence short


def better_image(url):
    if not url or re.search(r"/logo/|default|placeholder|1x1|pixel", url, re.I):
        return None
    url = url.replace("&amp;", "&")
    url = re.sub(r"(ichef\.bbci\.co\.uk/(?:ace/standard|news))/\d+/", r"\1/800/", url)
    m = re.match(r"https://img\.etimg\.com/photo/msid-(\d+),imgsize-(\d+)\.cms", url)
    if m:  # ET links full-size originals (often 2-3 MB); ask for a web-sized copy
        url = (f"https://img.etimg.com/thumb/msid-{m.group(1)},width-960,height-540,"
               f"imgsize-{m.group(2)},resizemode-4/photo.jpg")
    return url


TRACKING = re.compile(r"^(utm_\w+|mod|oc|ref|src|cmpid|smid|partner|fbclid|gclid|at_\w+|traffic_source|from)$", re.I)


def url_key(link):
    """Same article reached through different feeds -> same key. Keeps real
    query parameters (RBI's ?prid=) and drops tracking ones (?mod=rss)."""
    u = urllib.parse.urlsplit(link.strip())
    query = sorted((k, v) for k, v in urllib.parse.parse_qsl(u.query) if not TRACKING.match(k))
    host = u.netloc.lower().removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}?{urllib.parse.urlencode(query)}".lower()


def tokens(title):
    t = title.lower().replace("’", "'").replace("u.s.", "us").replace("s&p", "sp")
    t = re.sub(r"'s\b", "", t)
    out = set()
    for w in re.findall(r"[a-z0-9]+", t):
        if w in STOP or (len(w) < 2 and not w.isdigit()):
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def classify(text_title, text_dek):
    title = " " + re.sub(r"[^a-z0-9&\- ]", " ", text_title.lower()) + " "
    dek = " " + re.sub(r"[^a-z0-9&\- ]", " ", text_dek.lower()) + " "
    tw, dw = set(title.split()), set(dek.split())
    best, best_score = None, 0
    for sec in SEC_ORDER:
        score = 2 * len(tw & KW[sec]) + len(dw & KW[sec])
        if score > best_score:
            best, best_score = sec, score
    return best if best_score >= 2 else None


# ------------------------------------------------------------------ feeds --
XML_ENTS = {"amp", "lt", "gt", "quot", "apos"}


def parse_xml(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        pass
    text = data.decode("utf-8", "replace")
    head = text[:3000].lower()
    if "<rss" not in head and "<feed" not in head and "<rdf" not in head:
        raise ValueError("sent a web page instead of a feed")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", lambda m: m.group(0) if m.group(1) in XML_ENTS
                  else "&#%d;" % html.entities.name2codepoint.get(m.group(1), 32), text)
    text = re.sub(r"&(?![A-Za-z][A-Za-z0-9]*;|#\d+;|#x[0-9A-Fa-f]+;)", "&amp;", text)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    return ET.fromstring(text)


def feed_entries(root):
    out = []
    for it in root.iter():
        if local(it.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "desc": "", "date": None, "img": None, "cat": ""}
        for ch in it:
            t, txt = local(ch.tag), (ch.text or "").strip()
            if t == "title":
                d["title"] = txt
            elif t == "link" and not d["link"]:
                d["link"] = txt or ch.get("href", "")
            elif t in ("description", "summary") and not d["desc"]:
                d["desc"] = txt
            elif t in ("pubDate", "published", "updated", "date") and not d["date"]:
                d["date"] = parse_date(txt)
            elif t == "category" and not d["cat"]:
                d["cat"] = clean(txt).lower()
        best_w = -1
        for ch in it.iter():
            t = local(ch.tag)
            if t not in ("content", "thumbnail", "enclosure") or not ch.get("url"):
                continue
            typ, med = (ch.get("type") or "").lower(), (ch.get("medium") or "").lower()
            if typ.startswith(("video", "audio")) or med in ("video", "audio"):
                continue
            if t == "enclosure" and typ and not typ.startswith("image"):
                continue
            w = int(re.sub(r"\D", "", ch.get("width") or "") or 0)
            if w > best_w:
                best_w, d["img"] = w, ch.get("url")
        if not d["img"]:
            m = re.search(r'<img[^>]+src="([^"]+)"', d["desc"])
            d["img"] = m.group(1) if m else None
        out.append(d)
    return out


def load_source(src):
    """Fetch every feed of one publisher. Returns (entries, errors)."""
    entries, errors = [], []
    for url, section, is_top in src["feeds"]:
        try:
            for e in feed_entries(parse_xml(fetch(url))):
                e.update(feed_section=section, top=is_top)
                entries.append(e)
        except Exception as ex:
            errors.append(f"{type(ex).__name__}: {str(ex)[:90]}")
    return src["key"], entries, errors


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text).encode("ascii", "ignore")
                  .decode().lower()).strip("-")


def series_from(result):
    """Daily closes from a Yahoo chart result -> (days since 1970, closes), one per exchange day."""
    offset = result["meta"].get("gmtoffset") or 0
    days, closes = [], []
    for ts, c in zip(result.get("timestamp") or [], result["indicators"]["quote"][0]["close"]):
        if c is None:
            continue
        day = (ts + offset) // 86400  # the exchange's own calendar day
        if days and days[-1] == day:  # Yahoo sometimes repeats today's live bar
            closes[-1] = round(c, 4)
        else:
            days.append(day)
            closes.append(round(c, 4))
    return days, closes


def quote_record(label, kind, sym, days, closes):
    return {"label": label, "kind": kind, "sym": sym, "id": slug(label),
            "url": f"https://finance.yahoo.com/quote/{urllib.parse.quote(sym, safe='')}/",
            "price": float(closes[-1]), "prev": float(closes[-2]), "t": days, "c": closes}


def fetch_quote(ticker):
    """A year of daily closes: the strip shows the last one, the chart panel all of them."""
    label, sym, kind = ticker[:3]
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(sym)}?range=1y&interval=1d")
        days, closes = series_from(json.loads(fetch(url, timeout=12))["chart"]["result"][0])
        if len(closes) < 2:
            return None
        rec = quote_record(label, kind, sym, days, closes)
        if len(ticker) > 3:
            rec["aliases"] = ticker[3]
        return rec
    except Exception:
        return None


NIFTY_LISTS = ["https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
               "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
               "https://archives.nseindia.com/content/indices/ind_nifty50list.csv"]


# What people actually call the long ones.
SHORT_NAMES = {
    "Adani Ports and Special Economic Zone": "Adani Ports", "Apollo Hospitals Enterprise": "Apollo Hospitals",
    "Bharat Electronics": "BEL", "Dr. Reddy's Laboratories": "Dr Reddy's", "HCL Technologies": "HCLTech",
    "HDFC Life Insurance Company": "HDFC Life", "Hindustan Unilever": "HUL", "InterGlobe Aviation": "IndiGo",
    "Jio Financial Services": "Jio Financial", "Kotak Mahindra Bank": "Kotak Bank", "Larsen & Toubro": "L&T",
    "Mahindra & Mahindra": "M&M", "Maruti Suzuki India": "Maruti Suzuki", "Max Healthcare Institute": "Max Healthcare",
    "Nestle India": "Nestlé India", "Oil & Natural Gas Corporation": "ONGC",
    "Power Grid Corporation of India": "Power Grid", "Reliance Industries": "Reliance",
    "SBI Life Insurance Company": "SBI Life", "State Bank of India": "SBI", "Sun Pharmaceutical Industries": "Sun Pharma",
    "Tata Consultancy Services": "TCS", "Tata Consumer Products": "Tata Consumer",
    "Tata Motors Passenger Vehicles": "Tata Motors PV", "Titan Company": "Titan",
}


def short_name(name):
    name = re.sub(r"\s+(Ltd\.?|Limited)$", "", name.strip(), flags=re.I)
    return SHORT_NAMES.get(name) or re.sub(r"\s+(Corporation( of India)?|Company|Institute|Industries)$", "", name)


def nifty50():
    """Official Nifty 50 members {NSE symbol: name}; falls back to the last list that downloaded."""
    path = CACHE / "nifty50.json"
    for url in NIFTY_LISTS:
        try:
            rows = csv.DictReader(io.StringIO(fetch(url, timeout=15, tries=2).decode("utf-8-sig")))
            names = {r["Symbol"].strip(): short_name(r["Company Name"])
                     for r in rows if (r.get("Symbol") or "").strip()}
            if len(names) >= 45:
                CACHE.mkdir(exist_ok=True)
                path.write_text(json.dumps(names, indent=1, ensure_ascii=False), encoding="utf-8")
                return names
        except Exception:
            continue
    try:
        return {s: short_name(n) for s, n in json.loads(path.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def fetch_movers():
    """The Nifty 50's biggest gainers and losers in the latest session."""
    names = nifty50()
    if not names:
        return None
    syms, rows = sorted(names), []
    for i in range(0, len(syms), 10):
        url = ("https://query1.finance.yahoo.com/v7/finance/spark?symbols="
               + ",".join(urllib.parse.quote(s + ".NS") for s in syms[i:i + 10]) + "&range=1y&interval=1d")
        try:
            results = (json.loads(fetch(url, timeout=15)).get("spark") or {}).get("result") or []
        except Exception:
            continue
        for r in results:
            try:
                days, closes = series_from(r["response"][0])
            except Exception:
                continue
            base = r.get("symbol", "").removesuffix(".NS")
            if len(closes) >= 2 and base in names:
                rows.append(quote_record(names[base], "inr", r["symbol"], days, closes))
    if len(rows) < 20:
        return None
    last = max(q["t"][-1] for q in rows)
    rows = [q for q in rows if q["t"][-1] == last]  # only stocks that traded in the latest session
    rows.sort(key=lambda q: q["price"] / q["prev"], reverse=True)
    return {"day": last, "count": len(rows), "gainers": rows[:5], "losers": rows[::-1][:5]}


# --------------------------------------------------------------- paywalls --
def page_locked(item):
    """True if the article page itself says it is for subscribers."""
    url = item["link"]
    if item["src"] == "ET" and "/prime/" in url:
        return True
    try:
        page = fetch(url, timeout=15, tries=1).decode("utf-8", "replace")  # hundreds of these; no retries
    except Exception:
        return False  # can't tell - treat as free
    if item["src"] == "BS":
        m = re.search(r'"isPaid"\s*:\s*"([YN])"', page)
        if m:
            return m.group(1) == "Y"
    m = re.search(r'"isAccessibleForFree"\s*:\s*"?([^",}\s]+)', page)
    return bool(m and "false" in m.group(1).lower())


def check_paywalls(items):
    path = CACHE / "paywall.json"
    CACHE.mkdir(exist_ok=True)
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    now = time.time()
    cache = {u: v for u, v in cache.items() if now - v[1] < 10 * 86400}
    todo = [it for it in items if it["link"] not in cache]
    if todo:
        with ThreadPoolExecutor(16) as ex:
            for it, locked in zip(todo, ex.map(page_locked, todo)):
                cache[it["link"]] = [locked, now]
    for it in items:
        it["locked"] = cache[it["link"]][0]
    path.write_text(json.dumps(cache), encoding="utf-8")
    return len(todo)


# ------------------------------------------------------------- the paper --
def collect(now):
    with ThreadPoolExecutor(20) as ex:
        movers_job = ex.submit(fetch_movers)
        feeds = list(ex.map(load_source, SOURCES))
        quotes = [q for q in ex.map(fetch_quote, TICKERS) if q]
        watch = [q for q in ex.map(fetch_quote, WATCHLIST) if q]
        movers = movers_job.result()
    status, items, desk, seen = {}, [], defaultdict(list), set()
    window = now - timedelta(hours=30)
    raw_count = 0
    for key, entries, errors in feeds:
        src = SRC[key]
        status[key] = {"ok": bool(entries), "errors": errors, "n": 0}
        for e in entries:
            title = clean(e["title"])
            link = (e["link"] or "").strip()
            if not title or not link.startswith("http"):
                continue
            if src.get("gn"):
                title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
            ukey = url_key(link)
            if ukey in seen:
                continue
            seen.add(ukey)
            date = e["date"] or now
            if date > now + timedelta(hours=1):
                date = now
            if src.get("desk"):
                desk[key].append({"title": title, "link": link, "date": date})
                continue
            raw_count += 1
            if date < window:
                continue
            desc = "" if src.get("gn") else trim_dek(clean(e["desc"]), title)
            path = urllib.parse.urlparse(link).path.lower()
            if DROP_PATH.search(path) or DROP_TITLE.search(title) or e["cat"] in DROP_CATS:
                continue
            section, weight = e["feed_section"], 2
            if OPINION_PATH.search(path) or e["cat"] == "opinion":
                section = "opinion"
            elif not section:
                weight = 1
                section = BL_CATS.get(e["cat"]) if key == "BL" else None
                if not section:
                    section = next((s for frag, s in URL_SECTIONS if frag in path), None)
                if not section:
                    section = classify(title, desc)
                if not section:
                    continue  # general news with nothing business about it
            items.append({
                "src": key, "title": title, "link": link, "desc": desc, "date": date,
                "img": better_image(e["img"]), "section": section, "weight": weight,
                "top": e["top"], "paid": src["access"] == "paid", "check": src["access"] == "check",
                "locked": src["access"] == "paid", "tok": tokens(title)})
            status[key]["n"] += 1
    for key in desk:
        desk[key].sort(key=lambda d: d["date"], reverse=True)
    return items, desk, quotes, status, raw_count, {"watch": watch, "movers": movers}


def similar(a, b):
    inter = len(a & b)
    if inter < 2:
        return 0
    jac = inter / len(a | b)
    ovl = inter / min(len(a), len(b))
    if jac >= 0.5 or (inter >= 4 and ovl >= 0.6) or (inter >= 3 and ovl >= 0.8):
        return jac + 0.01 * inter
    return 0


def cluster(items):
    clusters, index = [], defaultdict(set)
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        hits = Counter(cid for tok in it["tok"] for cid in index[tok])
        best, best_sim = None, 0
        for cid, n in hits.items():
            if n < 2:
                continue
            s = max(similar(it["tok"], m["tok"]) for m in clusters[cid])
            if s > best_sim:
                best, best_sim = cid, s
        if best is None:
            best = len(clusters)
            clusters.append([])
        clusters[best].append(it)
        for tok in it["tok"]:
            index[tok].add(best)
    return [{"members": m} for m in clusters]


def score(c, now):
    mem = c["members"]
    free = {m["src"] for m in mem if not m["paid"]}
    paid = {m["src"] for m in mem if m["paid"]}
    newest = max(m["date"] for m in mem)
    age = (now - newest).total_seconds() / 3600
    votes = Counter()
    for m in mem:
        votes[m["section"]] += m["weight"]
    order = SEC_ORDER + ["opinion"]
    c["section"] = max(votes, key=lambda s: (votes[s], -order.index(s)))
    if c["section"] == "opinion" and len(free | paid) > 1:
        rest = Counter({s: v for s, v in votes.items() if s != "opinion"})
        if rest:
            c["section"] = max(rest, key=lambda s: (rest[s], -order.index(s)))
    c["date"] = newest
    c["sources"] = free | paid
    c["score"] = (2.0 * len(free) + 1.2 * len(paid) + (1.0 if any(m["top"] for m in mem) else 0)
                  + 2.0 * max(0.0, 1 - age / 30) + (0.4 if any(m["desc"] for m in mem) else 0))


def finish(c):
    """Pick the free write-up to lead with, the image and the source chips."""
    mem = c["members"]
    free = [m for m in mem if not m["locked"]]
    rank = lambda m: (PREFER.index(m["src"]) if m["src"] in PREFER else 99, -m["date"].timestamp())
    # The headline closest to what everyone else wrote says what the story is.
    central = lambda m: round(sum(len(m["tok"] & o["tok"]) / (len(m["tok"] | o["tok"]) or 1)
                                  for o in mem if o is not m) / max(len(mem) - 1, 1), 1)
    with_dek = sorted((m for m in free if len(m["desc"]) >= 60), key=lambda m: (-central(m), rank(m)))
    c["primary"] = with_dek[0] if with_dek else (sorted(free, key=rank)[0] if free else None)
    lead = c["primary"] or sorted(mem, key=lambda m: -m["date"].timestamp())[0]
    c["img"] = lead["img"] or next((m["img"] for m in mem if m["img"]), None)
    chips, seen = [], set()
    for m in sorted(mem, key=lambda m: (m is not lead, m["locked"], rank(m))):
        if m["src"] not in seen:
            seen.add(m["src"])
            chips.append(m)
    c["chips"] = chips
    c["related"] = []
    c["ptok"] = lead["tok"]
    c["utok"] = set().union(*(m["tok"] for m in mem))


# Daily formats every paper runs; one of each is enough.
SERIES = [(re.compile(p, re.I), key) for p, key in [
    (r"(stock market|share market|sensex|nifty|markets?)\b.*\blive\b|\blive\b.*(sensex|nifty|stock market)", "live"),
    (r"stocks? to (watch|buy|sell)|stocks in (news|focus)|buzzing stocks|stock picks", "watch"),
    (r"(petrol|diesel|fuel) (and diesel )?prices? today", "fuel"),
    (r"(gold|silver) (rate|price)s? today|today'?s? (gold|silver) (rate|price)", "bullion"),
    (r"\bipo\b.*\b(gmp|subscri\w*|allotment|opens|day \d|price band)\b", "ipo"),
]]
# Numbers, units and dates say nothing about whether two stories are the same.
THREAD_IGNORE = set("""cr crore crores lakh rs bn mn billion million trillion pc bps percent per cent day days
    week weeks month months year years quarter today tomorrow yesterday january february march april june july
    august september october november december monday tuesday wednesday thursday friday saturday sunday
    likely amid ahead high higher highest low lower lowest rise rising fall falling jump surge surging gain
    drop hit hits seen see set plan eye look first last next record strong weak major near back fresh deep
    move push pushe focus know need want expert experts analyst analysts rate""".split())


SERIES_NAME = {"live": "Markets Live", "watch": "Stocks to Watch", "fuel": "Fuel Prices",
               "bullion": "Gold & Silver", "ipo": "IPO Watch"}


def series(title):
    return next((key for rx, key in SERIES if rx.search(title)), None)


def thread(clusters, idf):
    """Fold follow-ups of the same news (different angles, different headlines)
    into the strongest story as 'more on this' links. Expects clusters ranked.
    Words most headlines use today (India, stake, Trump...) don't count as
    evidence; rare ones (Sanofi, IRDAI, PMS) do."""
    useful = lambda toks: {t for t in toks if t not in THREAD_IGNORE and not t.isdigit()}
    rare = lambda toks, cut=5.0: {t for t in toks if idf.get(t, 9) >= cut}
    live = [c for c in clusters if c["primary"]]
    for c in live:
        c["series"] = series(c["primary"]["title"])
        c["ptok_t"] = useful(c["ptok"])
        c["urare"] = rare(useful(c["utok"]))
    folded = set()
    for i, a in enumerate(live):
        if id(a) in folded:
            continue
        for b in live[i + 1:]:
            if id(b) in folded:
                continue
            if a["series"] or b["series"]:
                same = a["series"] == b["series"]
            else:
                shared = a["ptok_t"] & b["ptok_t"]
                weight = sum(idf.get(t, 9) for t in shared)
                same_sec = a["section"] == b["section"]
                # headline match: 3+ telling words, or 2 distinctive ones in the same section
                by_headline = rare(shared, 4.0) and weight >= 10 and (
                    len(shared) >= 3 or (same_sec and len(rare(shared)) == 2 == len(shared)))
                # thread match: a distinctive word in common and 3+ across all the coverage
                by_thread = (same_sec and len(shared) >= 2 and rare(shared)
                             and len(a["urare"] & b["urare"]) >= 3)
                same = bool(by_headline or by_thread)
            if same:
                a["related"].append(b)
                folded.add(id(b))
        a["score"] += 0.7 * min(len(a["related"]), 4)
    kept = [c for c in clusters if id(c) not in folded]
    kept.sort(key=lambda c: c["score"], reverse=True)
    return kept


def too_close(a, b, idf):
    """Same broad topic - keeps one event from taking over the front page."""
    topical = lambda toks: {t for t in toks if idf.get(t, 9) >= 2.4 and t not in THREAD_IGNORE}
    shared = len(topical(a["ptok"] & b["ptok"]))
    return shared >= 2 or (shared == 1 and len(topical(a["utok"] & b["utok"])) >= 3)


def assemble(items, now):
    clusters = cluster(items)
    for c in clusters:
        score(c, now)
    clusters.sort(key=lambda c: c["score"], reverse=True)

    # Check the articles that could make the paper for a subscriber flag.
    by_sec = defaultdict(list)
    for c in clusters:
        by_sec[c["section"]].append(c)
    shortlist = {id(c): c for c in clusters[:40]}
    for sec_list in by_sec.values():
        shortlist.update({id(c): c for c in sec_list[:40]})
    to_check = [m for c in shortlist.values() for m in c["members"] if m["check"]]
    checked = check_paywalls(to_check)
    for c in clusters:
        finish(c)
    df = Counter(t for it in items for t in it["tok"])
    idf = {t: math.log(len(items) / n) for t, n in df.items()}
    clusters = thread(clusters, idf)
    by_sec = defaultdict(list)
    for c in clusters:
        by_sec[c["section"]].append(c)

    used, front, sections, per_sec = set(), [], {}, Counter()
    for c in clusters:
        if len(front) == 8:
            break
        if (c["primary"] and c["section"] != "opinion" and per_sec[c["section"]] < 3
                and not any(too_close(c, f, idf) for f in front)):
            front.append(c)
            used.add(id(c))
            per_sec[c["section"]] += 1
    # Lead: the strongest of the top three that has a picture.
    lead_i = next((i for i, c in enumerate(front[:3]) if c["img"]), 0)
    if front:
        front.insert(0, front.pop(lead_i))

    for sec, _, n_cards, n_briefs in SECTIONS:
        pool = [c for c in by_sec.get(sec, []) if c["primary"] and id(c) not in used]
        cards = pool[:n_cards]
        feat = next((i for i, c in enumerate(cards[:4])
                     if c["img"] and c["primary"]["desc"] and not c.get("series")), None)
        if feat:
            cards.insert(0, cards.pop(feat))
        briefs = pool[n_cards:n_cards + n_briefs]
        sections[sec] = (cards, briefs)
        used.update(id(c) for c in cards + briefs)

    subs = defaultdict(list)
    for c in clusters:
        if c["primary"] is None:
            m = sorted(c["members"], key=lambda m: (not m["desc"], -m["date"].timestamp()))[0]
            group = m["src"] if m["paid"] else "premium"
            if len(subs[group]) < SUBS_QUOTA.get(group, 4):
                subs[group].append((m, c))
    return front, sections, subs, len(clusters), checked


# ------------------------------------------------------------------- HTML --
def fmt_time(d, today):
    d = d.astimezone(IST)
    t = d.strftime("%I:%M %p").lstrip("0")
    if d.date() == today:
        return t
    if d.date() == today - timedelta(days=1):
        return "Yesterday " + t
    return f"{d.day} {d:%b}"


def search_text(c):
    p = c["primary"]
    names = " ".join(SRC[m["src"]]["short"] + " " + SRC[m["src"]]["name"] for m in c["chips"])
    more = " ".join(r["primary"]["title"] for r in c.get("related", []))
    return f"{p['title']} {p['desc']} {names} {more}".lower()


def more_html(c, n):
    rel = c.get("related", [])[:n]
    if not rel:
        return ""
    lis = "".join(f'<li><a href="{esc(r["primary"]["link"])}" target="_blank" rel="noopener">'
                  f'{esc(r["primary"]["title"])}</a><span class="ms">{esc(SRC[r["primary"]["src"]]["short"])}'
                  f'</span></li>' for r in rel)
    return f'<ul class="more" aria-label="More on this story">{lis}</ul>'


def meta_html(c, today, max_chips=6):
    chips = []
    for m in c["chips"][:max_chips]:
        s = SRC[m["src"]]
        tip = s["name"] + (" — subscriber article" if m["locked"] else "")
        chips.append(f'<a class="src{" locked" if m["locked"] else ""}" href="{esc(m["link"])}" '
                     f'target="_blank" rel="noopener" title="{esc(tip)}">{esc(s["short"])}'
                     f'{LOCK_SVG if m["locked"] else ""}</a>')
    if len(c["chips"]) > max_chips:
        chips.append(f'<span>+{len(c["chips"]) - max_chips}</span>')
    n = len(c["sources"])
    cov = f'<span class="cov">{n} papers</span>' if n >= 3 else ""
    when = c["primary"]["date"]
    return (f'<p class="meta">{"<span class=sep>·</span>".join(chips)}{cov}'
            f'<time datetime="{when.isoformat()}">{fmt_time(when, today)}</time></p>')


def story_html(c, today, cls="story", img=False, kicker=None, tag="h3", more=2):
    p = c["primary"]
    out = [f'<article class="{cls}" data-s="{esc(search_text(c))}">']
    if img and c["img"]:
        out.append(f'<a class="fig" href="{esc(p["link"])}" target="_blank" rel="noopener" tabindex="-1" '
                   f'aria-hidden="true"><img src="{esc(c["img"])}" alt="" loading="lazy" '
                   f'referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></a>')
    if kicker:
        out.append(f'<p class="kicker">{esc(kicker)}</p>')
    out.append(f'<{tag} class="hl"><a href="{esc(p["link"])}" target="_blank" rel="noopener">'
               f'{esc(p["title"])}</a></{tag}>')
    if p["desc"]:
        out.append(f'<p class="dek">{esc(p["desc"])}</p>')
    out.append(meta_html(c, today))
    out.append(more_html(c, more))
    out.append("</article>")
    return "".join(out)


def brief_html(c, today):
    p = c["primary"]
    return (f'<li data-s="{esc(search_text(c))}"><a href="{esc(p["link"])}" target="_blank" rel="noopener">'
            f'{esc(p["title"])}</a>{meta_html(c, today, max_chips=3)}</li>')


def ticker_html(quotes):
    if not quotes:
        return ""
    cells = ['<span class="tk tk-label">Markets<small>Tap for charts</small></span>']
    for i, q in enumerate(quotes):
        price, prev, kind = q["price"], q["prev"], q["kind"]
        if kind == "yield":
            val, chg = f"{price:.2f}%", f"{abs(price - prev) * 100:.0f} bp"
        else:
            pct = abs(price / prev - 1) * 100 if prev else 0
            val = (f"${price:,.2f}" if kind == "usd" and price < 1000 else f"${price:,.0f}" if kind == "usd"
                   else f"₹{price:.2f}" if kind == "fx" else f"{price:,.2f}")
            chg = f"{pct:.2f}%"
        cls = "up" if price > prev else "down" if price < prev else "flat"
        arrow = "▲" if cls == "up" else "▼" if cls == "down" else "●"
        cells.append(f'<a class="tk" href="#{esc(q["id"])}" data-i="{i}" title="{esc(q["label"])}: one-year chart">'
                     f'<b>{esc(q["label"])}</b><span><span class="v">{val}</span>'
                     f'<span class="{cls}">{arrow} {chg}</span></span></a>')
    return f'<div class="ticker" aria-label="Markets at last close"><div class="ticker-in">{"".join(cells)}</div></div>'


def market_panel_html(quotes):
    """The chart panel the ticker opens; the page script fills it from the data below it."""
    if not quotes:
        return ""
    data = json.dumps([{k: q[k] for k in ("label", "kind", "id", "url", "t", "c")} for q in quotes],
                      separators=(",", ":")).replace("</", "<\\/")
    return f"""<dialog id="mk" class="mk" aria-labelledby="mk-name">
<div class="mk-in">
  <div class="mk-head">
    <div><p class="mk-kicker">Markets · daily closes</p><h2 id="mk-name" class="mk-name" tabindex="-1" autofocus></h2></div>
    <div class="mk-nav"><button type="button" class="mk-btn" data-step="-1" aria-label="Previous market">‹</button><button type="button" class="mk-btn" data-step="1" aria-label="Next market">›</button><button type="button" class="mk-btn" id="mk-x" aria-label="Close">×</button></div>
  </div>
  <div class="mk-hero"><span id="mk-price" class="mk-price"></span><span id="mk-day" class="mk-day"></span></div>
  <p id="mk-asof" class="mk-asof"></p>
  <div class="mk-bar"><div class="mk-range" role="group" aria-label="Time range"><button type="button" data-r="21">1M</button><button type="button" data-r="63">3M</button><button type="button" data-r="126">6M</button><button type="button" data-r="400">1Y</button></div><span id="mk-ret" class="mk-ret"></span></div>
  <div id="mk-chart" class="mk-chart" tabindex="0" role="img"></div>
  <dl id="mk-stats" class="mk-stats"></dl>
  <details class="mk-table"><summary>Show the numbers</summary><table><thead><tr><th>Date</th><th>Close</th><th>Change</th></tr></thead><tbody id="mk-rows"></tbody></table></details>
  <p class="mk-foot">Closing prices from Yahoo Finance. <a id="mk-link" href="#" target="_blank" rel="noopener">More on Yahoo Finance ↗</a></p>
</div>
</dialog>
<script id="mk-data" type="application/json">{data}</script>"""


# --------------------------------------------- watchlist, movers, week ahead --
KIND_ORDER = {"holiday": 0, "policy": 1, "data": 2}


def india_releases(start, end):
    """India's regular data days: CPI on the 12th and WPI on the 14th (next Monday if that falls on a
    weekend); GDP on the last weekday of February, May, August and November."""
    def weekday_from(d):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        prev = datetime(y - (m == 1), (m - 2) % 12 + 1, 1)
        out.append({"date": weekday_from(datetime(y, m, 12).date()), "tag": "IN", "kind": "data",
                    "title": "India inflation (CPI)", "detail": f"{prev:%B} figures · 4 PM IST"})
        out.append({"date": weekday_from(datetime(y, m, 14).date()), "tag": "IN", "kind": "data",
                    "title": "India wholesale prices (WPI)", "detail": f"{prev:%B} figures · 12 noon IST"})
        if m in (2, 5, 8, 11):
            last = datetime(y, m + 1, 1).date() - timedelta(days=1)
            while last.weekday() >= 5:
                last -= timedelta(days=1)
            quarter = {2: "Oct–Dec", 5: "Jan–Mar", 8: "Apr–Jun", 11: "Jul–Sep"}[m]
            out.append({"date": last, "tag": "IN", "kind": "data", "title": "India GDP",
                        "detail": f"{quarter} quarter growth · 4 PM IST"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def week_ahead(today, horizon=7, at_least=3, most=8):
    """The next week's market-moving dates; if it's a quiet week, the next few whenever they are."""
    try:
        fixed = json.loads((ROOT / "calendar.json").read_text(encoding="utf-8"))["events"]
    except Exception:
        fixed = []
    events = [dict(e, date=datetime.strptime(e["date"], "%Y-%m-%d").date()) for e in fixed]
    events += india_releases(today, today + timedelta(days=62))
    upcoming = sorted((e for e in events if e["date"] >= today),
                      key=lambda e: (e["date"], KIND_ORDER.get(e["kind"], 3), e["tag"] != "IN"))
    soon = [e for e in upcoming if e["date"] < today + timedelta(days=horizon)]
    return (soon if len(soon) >= at_least else upcoming[:at_least])[:most]


def watch_news(watch, ranked):
    """For each watchlist company: the best-ranked story that mentions it, and how many do."""
    for q in watch:
        rx = re.compile("|".join(q.get("aliases") or [re.escape(q["label"])]), re.I)
        hits = [c for c in ranked if rx.search(c["primary"]["title"] + " " + c["primary"]["desc"])]
        q["news"], q["mentions"] = (hits[0]["primary"] if hits else None), len(hits)


def price_text(v, kind):
    if kind == "yield":
        return f"{v:.2f}%"
    if kind == "fx":
        return f"₹{v:.2f}"
    if kind == "inr":
        return f"₹{v:,.2f}"
    if kind == "usd2" or (kind == "usd" and v < 1000):
        return f"${v:,.2f}"
    return f"${v:,.0f}" if kind == "usd" else f"{v:,.2f}"


def move_html(price, prev, kind):
    cls = "up" if price > prev else "down" if price < prev else "flat"
    arrow = "▲" if cls == "up" else "▼" if cls == "down" else "●"
    chg = f"{abs(price - prev) * 100:.0f} bp" if kind == "yield" else f"{abs(price / prev - 1) * 100:.2f}%"
    return f'<span class="{cls}">{arrow} {chg}</span>'


def dash_html(ctx):
    """The band under the front page: your watchlist, the Nifty's movers, the week ahead."""
    today, at = ctx["today"], ctx["panel_index"]
    cols = []
    if ctx["watch"]:
        rows = []
        for q in ctx["watch"]:
            news = q.get("news")
            line = ""
            if news:
                more = f'<span class="ms">+{q["mentions"] - 1} more</span>' if q["mentions"] > 1 else ""
                line = (f'<p class="wl-line"><a href="{esc(news["link"])}" target="_blank" rel="noopener">'
                        f'{esc(news["title"])}</a><span class="ms">{esc(SRC[news["src"]]["short"])}</span>{more}</p>')
            rows.append(f'<li data-s="{esc((q["label"] + " " + (news["title"] if news else "")).lower())}">'
                        f'<a class="wl-row" href="#{esc(q["id"])}" data-i="{at[q["sym"]]}">'
                        f'<span class="wl-name">{esc(q["label"])}</span>'
                        f'<span class="wl-px">{price_text(q["price"], q["kind"])}</span>'
                        f'{move_html(q["price"], q["prev"], q["kind"])}</a>{line}</li>')
        quiet = ("" if any(q.get("news") for q in ctx["watch"])
                 else '<p class="wl-quiet">None of these are in today’s headlines.</p>')
        cols.append(f'<div class="dash-col" id="watch" data-sec><header class="sec-head"><h2>Your Watchlist</h2>'
                    f'<span class="sec-note">Tap for charts</span></header><ul class="wl">{"".join(rows)}</ul>'
                    f'{quiet}</div>')
    movers = ctx["movers"]
    if movers:
        session = (datetime(1970, 1, 1) + timedelta(days=movers["day"])).date()
        label = "Today’s session" if session == today else f"{session:%a} {session.day} {session:%b} session"

        def board(qs):
            return "".join(
                f'<li data-s="{esc(q["label"].lower())} nifty movers"><a class="mv-row" href="#{esc(q["id"])}" '
                f'data-i="{at[q["sym"]]}" title="{esc(q["label"])} · {price_text(q["price"], q["kind"])}">'
                f'<span class="mv-name">{esc(q["label"])}</span>{move_html(q["price"], q["prev"], q["kind"])}</a></li>'
                for q in qs)
        cols.append(f'<div class="dash-col" id="movers" data-sec><header class="sec-head"><h2>Nifty Movers</h2>'
                    f'<span class="sec-note">{label}</span></header><div class="mv">'
                    f'<div><h3 class="small-h">Top gainers</h3><ol class="mv-list">{board(movers["gainers"])}</ol></div>'
                    f'<div><h3 class="small-h">Top losers</h3><ol class="mv-list">{board(movers["losers"])}</ol></div>'
                    f'</div></div>')
    if ctx["week"]:
        rows = []
        for e in ctx["week"]:
            d = e["date"]
            when = "Today" if d == today else "Tmrw" if d == today + timedelta(days=1) else f"{d:%a}"
            rows.append(f'<li class="cal-{esc(e["kind"])}" data-s="{esc((e["title"] + " " + e["detail"]).lower())}">'
                        f'<span class="cal-d"><b>{when}</b><span>{d.day}</span><small>{d:%b}</small></span>'
                        f'<span class="cal-t"><b>{esc(e["title"])}</b><small>{esc(e["detail"])}</small></span>'
                        f'<span class="cal-tag">{esc(e["tag"])}</span></li>')
        cols.append(f'<div class="dash-col" id="week" data-sec><header class="sec-head"><h2>The Week Ahead</h2>'
                    f'<span class="sec-note">Times in IST</span></header><ol class="cal">{"".join(rows)}</ol></div>')
    return f'<section class="dash">{"".join(cols)}</section>' if cols else ""


def lens_html(picks):
    """The CFA Lens: today's stories next to the Level I concept each one illustrates."""
    if not picks:
        return ""
    cards = []
    for c, story in picks:
        p = story["primary"]
        cards.append(
            f'<article class="lens-card" data-s="{esc((c["title"] + " " + c["topic"] + " " + p["title"] + " cfa").lower())}">'
            f'<p class="kicker">{esc(c["topic"])}</p><h3 class="lens-h">{esc(c["title"])}</h3>'
            f'<p class="lens-news"><span>In the news</span><a href="{esc(p["link"])}" target="_blank" rel="noopener">'
            f'{esc(p["title"])}</a></p><p class="lens-x">{esc(c["explain"])}</p>'
            f'<p class="lens-angle"><b>Exam angle</b>{esc(c["angle"])}</p></article>')
    return (f'<section class="lens" id="lens" data-sec><header class="sec-head"><h2>The CFA Lens</h2>'
            f'<span class="sec-note">Today’s news, exam-style · CFA Level I</span></header>'
            f'<div class="lens-grid">{"".join(cards)}</div></section>')


# The service worker behind offline mode: pages come from the network when there is one (and are
# saved as they load); without a connection, the last saved copy opens instead.
SERVICE_WORKER = r"""const V = '__VERSION__';
const PAGES = 'ledger-pages', ASSETS = 'ledger-assets-' + V, IMGS = 'ledger-img', FONTS = 'ledger-fonts';
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(PAGES).then(c => c.addAll(['./', 'archive.html'])).catch(() => {}));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('ledger-assets-') && k !== ASSETS).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
function trim(cache, max) {
  cache.keys().then(keys => { if (keys.length > max) cache.delete(keys[0]).then(() => trim(cache, max)); });
}
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req)
      .then(res => { const copy = res.clone(); caches.open(PAGES).then(c => c.put(req, copy)); return res; })
      .catch(() => caches.match(req, {ignoreSearch: true}).then(r => r || caches.match('./'))));
  } else if (url.origin === location.origin) {
    e.respondWith(caches.open(ASSETS).then(c => c.match(req).then(r => r || fetch(req).then(res => {
      if (res.ok) c.put(req, res.clone());
      return res;
    }))));
  } else if (/fonts\.(googleapis|gstatic)\.com$/.test(url.hostname)) {
    e.respondWith(caches.open(FONTS).then(c => c.match(req).then(r => {
      const net = fetch(req).then(res => { c.put(req, res.clone()); return res; }).catch(() => r);
      return r || net;
    })));
  } else if (req.destination === 'image') {
    e.respondWith(caches.open(IMGS).then(c => c.match(req).then(r => r || fetch(req).then(res => {
      c.put(req, res.clone()).then(() => trim(c, 40)).catch(() => {});
      return res;
    }).catch(() => r || Response.error()))));
  }
});
"""


def desk_html(desk, today):
    blocks = []
    for key, n in (("RBI", 6), ("Fed", 4)):
        rows = desk.get(key, [])[:n]
        if not rows:
            continue
        lis = "".join(
            f'<li data-s="{esc((d["title"] + " " + SRC[key]["name"]).lower())}"><a href="{esc(d["link"])}" '
            f'target="_blank" rel="noopener">{esc(d["title"])}</a>'
            f'<p class="meta"><time>{fmt_time(d["date"], today)}</time></p></li>' for d in rows)
        blocks.append(f'<h3 class="desk-h">{esc(SRC[key]["name"])}</h3><ul class="plain">{lis}</ul>')
    return "".join(blocks)


def subs_html(subs, today):
    names = {"premium": "Premium stories from other papers"}
    out = []
    for group in ["WSJ", "FT", "Bloomberg", "Economist", "NYT", "MW", "premium"]:
        rows = subs.get(group)
        if not rows:
            continue
        title = names.get(group) or SRC[group]["name"]
        lis = []
        for m, c in rows:
            s = SRC[m["src"]]
            label = f'<span>{esc(s["short"])}</span><span class="sep">·</span>' if group == "premium" else ""
            dek = f'<p class="dek">{esc(m["desc"])}</p>' if m["desc"] else ""
            lis.append(f'<li data-s="{esc((m["title"] + " " + m["desc"] + " " + s["name"]).lower())}">'
                       f'<a href="{esc(m["link"])}" target="_blank" rel="noopener">{esc(m["title"])}</a>{dek}'
                       f'<p class="meta">{label}<time>{fmt_time(m["date"], today)}</time></p></li>')
        out.append(f'<div class="pub"><h3 class="desk-h">{LOCK_SVG}{esc(title)}</h3>'
                   f'<ul class="plain">{"".join(lis)}</ul></div>')
    return "".join(out)


FONTS = ("https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700&family=Newsreader:"
         "ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;0,6..72,700;1,6..72,400&family=UnifrakturMaguntia"
         "&display=swap")
# Only the letter y, from the sister typeface (see nameplate()).
FONTS_Y = "https://fonts.googleapis.com/css2?family=UnifrakturCook:wght@700&text=y&display=swap"


def nameplate():
    """The masthead name. In Fraktur the lowercase y reads like an "ŋ" to modern eyes,
    so the y alone is set in UnifrakturCook, whose y has a clear descender."""
    return esc(PAPER).replace("y", '<span class="y">y</span>')


def head_html(title, path, prefix, description=DESCRIPTION):
    """Everything in <head>: what Google, link previews and phones read."""
    url = SITE_URL + path
    ld = json.dumps({"@context": "https://schema.org", "@type": "WebSite", "name": PAPER, "url": SITE_URL,
                     "description": DESCRIPTION, "inLanguage": "en-IN",
                     "creator": {"@type": "Person", "name": AUTHOR, "url": f"https://github.com/{AUTHOR}"}},
                    ensure_ascii=False).replace("</", "<\\/")
    verify = (f'<meta name="google-site-verification" content="{esc(GOOGLE_VERIFICATION)}">\n'
              if GOOGLE_VERIFICATION else "")
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{esc(url)}">
{verify}<meta property="og:type" content="website">
<meta property="og:site_name" content="{PAPER}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{SITE_URL}assets/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#f5f0e5" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#151412" media="(prefers-color-scheme: dark)">
<link rel="icon" type="image/png" sizes="32x32" href="{prefix}assets/favicon-32.png">
<link rel="apple-touch-icon" href="{prefix}assets/apple-touch-icon.png">
<link rel="manifest" href="{prefix}assets/manifest.webmanifest">
<meta name="apple-mobile-web-app-title" content="Ledger">
<script type="application/ld+json">{ld}</script>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{FONTS_Y}">
<style>{CSS}</style>
<script>try{{var t=localStorage.getItem("dl-theme");if(t)document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>"""


def render(ctx, prefix, archived):
    today, now = ctx["today"], ctx["now"]
    front, sections = ctx["front"], ctx["sections"]
    nav = [("front", "Front Page")]
    nav += [("watch", "Watchlist")] if ctx["watch"] else []
    nav += [("lens", "CFA Lens")] if ctx["lens"] else []
    short = {"economy": "Economy", "companies": "Companies"}  # the menu has to fit on one line
    nav += [(k, short.get(k, v)) for k, v, _, _ in SECTIONS if sections.get(k, ([], []))[0]]
    nav += [("desk", "Regulators"), ("subs", "Subscriber Desk")]

    body = []
    if front:
        lead = story_html(front[0], today, "story lead", img=True, kicker=SEC_NAME[front[0]["section"]],
                          tag="h2", more=4)
        side = "".join(story_html(c, today, kicker=SEC_NAME[c["section"]]) for c in front[1:4])
        row = "".join(story_html(c, today, img=True, kicker=SEC_NAME[c["section"]]) for c in front[4:8])
        body.append(f'<section id="front" class="front" data-sec><div class="front-top">{lead}'
                    f'<div class="front-side">{side}</div></div><div class="front-row">{row}</div></section>')
    body.append(dash_html(ctx))
    body.append(lens_html(ctx["lens"]))
    for sec, title, _, _ in SECTIONS:
        cards, briefs = sections.get(sec, ([], []))
        if not cards:
            continue
        label = lambda c: SERIES_NAME.get(c.get("series"))
        feat = story_html(cards[0], today, "story feature", img=True, tag="h3", more=3, kicker=label(cards[0]))
        rest = "".join(story_html(c, today, kicker=label(c), more=4 if label(c) else 2) for c in cards[1:])
        br = ""
        if briefs:
            br = (f'<div class="briefs"><h3 class="small-h">Also in {esc(title)}</h3><ul class="brief-list">'
                  f'{"".join(brief_html(c, today) for c in briefs)}</ul></div>')
        count = len(cards) + len(briefs)
        body.append(f'<section id="{sec}" class="sec" data-sec><header class="sec-head"><h2>{esc(title)}</h2>'
                    f'<span class="sec-note">{count} stories</span></header>'
                    f'<div class="sec-body">{feat}{rest}</div>{br}</section>')
    body.append(f'<section class="band"><div id="desk" class="reg" data-sec><header class="sec-head">'
                f'<h2>From the Regulators</h2><span class="sec-note">Official releases</span></header>'
                f'{desk_html(ctx["desk"], today)}</div>'
                f'<div id="subs" class="subs" data-sec><header class="sec-head"><h2>Subscriber Desk</h2>'
                f'<span class="sec-note">{LOCK_SVG} Behind a paywall — headline and summary only</span>'
                f'</header><div class="subs-grid">{subs_html(ctx["subs"], today)}</div></div></section>')

    st = ctx["status"]
    read_ok = [k for k in st if st[k]["ok"]]
    failed = [k for k in st if not st[k]["ok"]]
    src_list = "".join(
        f'<li><span>{esc(SRC[k]["name"])}{LOCK_SVG if SRC[k]["access"] == "paid" else ""}</span>'
        f'<span class="n">{st[k]["n"] or len(ctx["desk"].get(k, []))}</span></li>' for k in read_ok)
    fail_note = ""
    if failed:
        fail_note = ("<p>Didn’t answer this morning: "
                     + ", ".join(esc(SRC[k]["name"]) for k in failed) + ".</p>")
    printed = f"{now:%I:%M %p}".lstrip("0")
    date_long = f"{now:%A}, {now.day} {now:%B %Y}"
    prev_link = (f'<a href="{prefix}editions/{ctx["prev"]}.html">‹ Previous edition</a>'
                 if ctx["prev"] else '<span class="muted">First edition</span>')
    archive_note = ""
    if archived:
        archive_note = (f'<div class="note">You’re reading the edition of {date_long}. '
                        f'<a href="{prefix}index.html">Today’s paper →</a></div>')
    if archived:
        lead = front[0]["primary"]["title"] if front else ""
        head = head_html(f"{PAPER} — {date_long} edition", f"editions/{today.isoformat()}.html", prefix,
                         f"The {date_long} edition of {PAPER}: {lead}, and the day’s other top business, "
                         f"markets and world stories.")
    else:
        head = head_html(f"{PAPER} — India & world business news, every morning", "", prefix)

    return f"""<!doctype html>
<html lang="en">
<head>
{head}
</head>
<body data-built="{now.isoformat()}" data-root="{prefix}"{' data-archived="1"' if archived else ''}>
<svg width="0" height="0" style="position:absolute"><symbol id="lk" viewBox="0 0 16 16"><path d="M5 7V5.2a3 3 0 0 1 6 0V7" fill="none" stroke="currentColor" stroke-width="1.7"/><rect x="3" y="7" width="10" height="8" rx="1.6" fill="currentColor"/></symbol></svg>
<div class="stale" id="stale" hidden></div>
{archive_note}
<header class="mast">
  <div class="mast-top"><span>Vol. I · No. {ctx["no"]}</span><span>{date_long}{" · Evening edition" if ctx["edition"] == "Evening" else ""}</span><span>Printed {printed} IST</span></div>
  <h1 class="nameplate"><a href="{prefix}index.html">{nameplate()}</a></h1>
  <p class="motto">{MOTTO}</p>
  <div class="mast-bottom">{prev_link}<span class="tally">{len(read_ok)} publishers · {ctx["scanned"]:,} stories read · {ctx["printed"]} printed</span><span class="mast-r"><a href="{prefix}archive.html">All editions</a><button id="theme" type="button" aria-label="Switch light or dark">◐</button></span></div>
</header>
{ticker_html(ctx["quotes"])}
<nav class="secnav" aria-label="Sections"><div class="secnav-in"><div class="links">{"".join(f'<a href="#{k}">{esc(v)}</a>' for k, v in nav)}</div>
<label class="search"><span class="sr">Search today’s paper</span><input id="q" type="search" placeholder="Search  /" autocomplete="off"></label><span id="qn" class="qn" aria-live="polite"></span></div></nav>
<main class="wrap">
{"".join(body)}
</main>
<footer class="foot">
  <div class="foot-grid">
    <div><h3 class="small-h">About this paper</h3>
      <p>{PAPER} prints itself every morning at about 6 AM India time. A small program reads the public RSS feeds of 20 publishers, keeps what’s free to read, puts the same story from different papers together, and links every headline to the original article. Nothing here gets around a paywall: locked stories show only the headline and summary the publisher shares for free.</p>
      <p>Headlines, summaries and pictures belong to their publishers. The paper covers the last 30 hours, and links you’ve opened turn grey. Tap any market in the strip at the top for its one-year chart.</p>
      {fail_note}</div>
    <div><h3 class="small-h">Read this morning</h3><ul class="srcs">{src_list}</ul></div>
  </div>
  <p class="colophon">Printed {date_long}, {printed} IST · {ctx["clusters"]:,} distinct stories found · Made by <a href="https://github.com/{AUTHOR}">{AUTHOR}</a> · <a href="{REPO_URL}">How it works</a></p>
</footer>
{market_panel_html(ctx["panel"])}
<script>{JS}</script>
</body>
</html>
"""


def render_archive(index):
    rows, month = [], None
    for date in sorted(index, reverse=True):
        e = index[date]
        d = datetime.strptime(date, "%Y-%m-%d")
        m = f"{d:%B %Y}"
        if m != month:
            rows.append(f'<h2 class="arch-m">{m}</h2>')
            month = m
        rows.append(f'<a class="arch-row" href="editions/{date}.html"><span class="arch-d">{d:%a} {d.day}</span>'
                    f'<span class="arch-l">{esc(e.get("lead", ""))}</span>'
                    f'<span class="arch-n">No. {e.get("no", "")} · {e.get("printed", 0)} stories</span></a>')
    head = head_html(f"{PAPER} — All editions", "archive.html", "",
                     f"Every edition of {PAPER}, the free morning newspaper of business, markets and world news.")
    return f"""<!doctype html>
<html lang="en"><head>
{head}
</head><body>
<header class="mast"><h1 class="nameplate small"><a href="index.html">{nameplate()}</a></h1>
<div class="mast-bottom"><a href="index.html">← Today’s paper</a><span class="tally">All editions</span><span></span></div></header>
<main class="wrap arch">{"".join(rows)}</main></body></html>
"""


def render_sitemap(index):
    """The list of pages Google should know about (submit it once in Search Console)."""
    latest = max(index) if index else datetime.now(IST).date().isoformat()
    urls = [(SITE_URL, latest, "daily", "1.0"), (SITE_URL + "archive.html", latest, "daily", "0.6")]
    urls += [(f"{SITE_URL}editions/{d}.html", d, "never", "0.4") for d in sorted(index, reverse=True)]
    rows = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{m}</lastmod><changefreq>{f}</changefreq>"
                   f"<priority>{p}</priority></url>\n" for u, m, f, p in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{rows}</urlset>\n')


def write(path, text):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main():
    args = set(sys.argv[1:])
    EDITIONS.mkdir(exist_ok=True)
    if "--if-missing" in args and (EDITIONS / f"{datetime.now(IST).date().isoformat()}.html").exists():
        return 0  # the logon / noon catch-up runs: today's paper is already out
    for attempt in range(5):
        now = datetime.now(IST)
        items, desk, quotes, status, raw, extras = collect(now)
        ok = sum(1 for s in status.values() if s["ok"])
        if ok >= 6 or "--no-wait" in args:
            break
        log(f"only {ok} sources answered - network may be down; retrying in 2 minutes")
        time.sleep(120)
    if ok < 6:
        log("gave up: not enough sources answered; kept yesterday's paper")
        return 1

    front, sections, subs, n_clusters, checked = assemble(items, now)
    today = now.date()
    stamp = today.isoformat()
    index_path = EDITIONS / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        index = {}
    earlier = sorted(d for d in index if d < stamp)
    printed = len(front) + sum(len(a) + len(b) for a, b in sections.values())

    # Everything on the page, best first: the watchlist and the CFA Lens look for stories here.
    ranked = front + [c for cards, briefs in sections.values() for c in cards + briefs]
    watch, movers = extras["watch"], extras["movers"]
    watch_news(watch, ranked)
    lens = cfa_lens.pick([(c["primary"]["title"], c["primary"]["desc"], c) for c in ranked])
    # One list behind the chart panel: the strip, then the watchlist, then the movers.
    panel, panel_index = [], {}
    for q in quotes + watch + ((movers["gainers"] + movers["losers"]) if movers else []):
        if q["sym"] not in panel_index:
            panel_index[q["sym"]] = len(panel)
            panel.append(q)
    edition = "Evening" if now.hour * 60 + now.minute >= 15 * 60 + 45 else "Morning"  # after the 3:30 PM close

    ctx = {"now": now, "today": today, "front": front, "sections": sections, "subs": subs, "desk": desk,
           "quotes": quotes, "status": status, "scanned": raw, "printed": printed, "clusters": n_clusters,
           "no": len(earlier) + 1, "prev": earlier[-1] if earlier else None, "edition": edition,
           "watch": watch, "movers": movers, "week": week_ahead(today), "lens": lens,
           "panel": panel, "panel_index": panel_index}

    write(EDITIONS / f"{stamp}.html", render(ctx, "../", archived=True))
    write(ROOT / "index.html", render(ctx, "", archived=False))
    write(ROOT / "sw.js", SERVICE_WORKER.replace("__VERSION__", now.strftime("%Y%m%d%H%M")))
    index[stamp] = {"no": ctx["no"], "printed": printed, "edition": edition,
                    "lead": front[0]["primary"]["title"] if front else ""}
    write(index_path, json.dumps(index, indent=1))
    write(ROOT / "archive.html", render_archive(index))
    write(ROOT / "sitemap.xml", render_sitemap(index))

    failed = [k for k, s in status.items() if not s["ok"]]
    log(f"printed edition No. {ctx['no']} ({edition.lower()}): {printed} stories from {ok}/{len(status)} publishers, "
        f"{raw} read, {n_clusters} distinct, {checked} paywall checks; watchlist {len(watch)}/{len(WATCHLIST)}, "
        f"movers {'from ' + str(movers['count']) + ' stocks' if movers else 'unavailable'}, "
        f"lens {len(lens)}, week ahead {len(ctx['week'])}"
        + (f"; no answer from {', '.join(failed)}" if failed else ""))
    try:
        events = json.loads((ROOT / "calendar.json").read_text(encoding="utf-8"))["events"]
    except Exception:
        events = []
    for what, test in (("US data (BLS)", lambda e: e["tag"] == "US" and e["kind"] == "data"),
                       ("RBI meetings", lambda e: e["title"].startswith("RBI")),
                       ("Fed meetings", lambda e: e["title"].startswith("Fed")),
                       ("NSE holidays", lambda e: e["kind"] == "holiday")):
        last = max((e["date"] for e in events if test(e)), default="")
        if last < (today + timedelta(days=30)).isoformat():
            log(f"   calendar.json: {what} end {last or 'n/a'} - add the next official dates")
    for k, s in status.items():
        if s["errors"]:
            log(f"   {k}: {'; '.join(s['errors'][:2])}")
    if "--open" in args:
        os.startfile(ROOT / "index.html")
    return 0


# -------------------------------------------------------------------- CSS --
CSS = r"""
:root{--bg:#f5f0e5;--paper:#fbf8f1;--ink:#1b1a17;--ink2:#3a3731;--muted:#6d675c;--hair:#d8cfbc;--rule:#1b1a17;
--accent:#9c2b1f;--up:#1c6f45;--down:#ad2a1e;--lock:#86661a;--visited:#7a7468;--wash:rgba(27,26,23,.055);
--serif:"Newsreader",Georgia,"Times New Roman",serif;--sans:"Libre Franklin","Segoe UI",system-ui,sans-serif;
--black:"UnifrakturMaguntia","Old English Text MT",Georgia,serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151412;--paper:#1d1c19;--ink:#ece7dc;
--ink2:#cfc8ba;--muted:#9c9588;--hair:#36332d;--rule:#d9d3c6;--accent:#e58474;--up:#5fc28f;--down:#f08070;
--lock:#d6b15a;--visited:#8d877b;--wash:rgba(236,231,220,.07);color-scheme:dark}}
:root[data-theme="dark"]{--bg:#151412;--paper:#1d1c19;--ink:#ece7dc;--ink2:#cfc8ba;--muted:#9c9588;--hair:#36332d;
--rule:#d9d3c6;--accent:#e58474;--up:#5fc28f;--down:#f08070;--lock:#d6b15a;--visited:#8d877b;--wash:rgba(236,231,220,.07);color-scheme:dark}
*{box-sizing:border-box}
html{scroll-padding-top:56px}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--serif);font-size:17px;line-height:1.45;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:inherit}
a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.hide{display:none!important}
.muted{color:var(--muted)}
.wrap,.mast,.ticker,.foot{max-width:1280px;margin:0 auto;padding-left:16px;padding-right:16px}
@media(min-width:760px){.wrap,.mast,.ticker,.foot,.secnav-in{padding-left:32px;padding-right:32px}}
.stale,.note{background:var(--accent);color:#fff;text-align:center;font:600 13px/1.4 var(--sans);padding:9px 16px}
.note{background:var(--ink);color:var(--bg)}
.note a{color:inherit;margin-left:8px}

/* masthead */
.mast{text-align:center;padding-top:14px}
.mast-top{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 16px;font:500 11px/1.4 var(--sans);
letter-spacing:.09em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--hair);padding-bottom:8px}
.nameplate{font-family:var(--black);font-weight:400;font-size:clamp(44px,9.5vw,112px);line-height:1.02;margin:14px 0 2px}
.nameplate .y{font-family:"UnifrakturCook",var(--black);font-weight:700}
.nameplate.small{font-size:clamp(40px,7vw,72px)}
.nameplate a{text-decoration:none}
.motto{font-style:italic;color:var(--ink2);margin:0 0 12px;font-size:15.5px}
.mast-bottom{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px 16px;
border-top:4px double var(--rule);border-bottom:1px solid var(--rule);padding:7px 0;
font:600 11.5px/1.4 var(--sans);letter-spacing:.07em;text-transform:uppercase}
.mast-bottom a{text-decoration:none}.mast-bottom a:hover{color:var(--accent)}
.tally{color:var(--muted);font-weight:500}
.mast-r{display:flex;gap:14px;align-items:center}
#theme{background:none;border:1px solid var(--hair);color:var(--ink);border-radius:50%;width:26px;height:26px;
font-size:14px;line-height:1;cursor:pointer;padding:0}
@media(max-width:640px){.mast-top span:first-child,.mast-top span:last-child{display:none}.mast-top{justify-content:center}
.tally{order:3;width:100%}}

/* ticker */
.ticker-in{display:flex;overflow-x:auto;scrollbar-width:none;border-bottom:1px solid var(--hair)}
.ticker-in::-webkit-scrollbar{display:none}
@media(max-width:1100px){.ticker-in{-webkit-mask-image:linear-gradient(to right,#000 82%,transparent);
mask-image:linear-gradient(to right,#000 82%,transparent)}}
.tk{flex:1 0 auto;display:flex;flex-direction:column;gap:1px;padding:8px 14px;border-left:1px solid var(--hair);
font:500 13px/1.3 var(--sans);white-space:nowrap;font-variant-numeric:tabular-nums}
.tk:first-child{border-left:0;padding-left:0}
.tk b{font-weight:600;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.tk-label{justify-content:center;font-weight:700;font-size:10px;letter-spacing:.09em;text-transform:uppercase;
color:var(--accent);flex:0 0 auto;line-height:1.25}
.tk .v{font-weight:600;margin-right:6px}
.tk .up,.tk .down,.tk .flat{font-size:12px}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--muted)}
a.tk{text-decoration:none;color:inherit;cursor:pointer;transition:background .15s}
a.tk:hover,a.tk:focus-visible{background:var(--wash)}
a.tk:hover b{color:var(--ink)}
a.tk:focus-visible{outline-offset:-2px}
.tk-label small{display:block;font-size:9px;letter-spacing:.07em;color:var(--muted);font-weight:600;margin-top:1px}

/* market chart panel */
.mk{border:0;padding:0;background:var(--paper);color:var(--ink);width:min(700px,calc(100vw - 24px));
max-height:calc(100vh - 24px);border-radius:8px;box-shadow:0 24px 70px rgba(0,0,0,.35)}
.mk::backdrop{background:rgba(18,16,13,.55)}
.mk-in{padding:18px 22px 16px}
@media(max-width:600px){.mk{width:100vw;max-width:100vw;margin:auto 0 0;border-radius:14px 14px 0 0;max-height:92vh}
.mk-in{padding:16px 16px 20px}}
.mk-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.mk-kicker{font:700 10.5px/1.3 var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin:0 0 3px}
.mk-name{font:700 28px/1.1 var(--serif);margin:0}
.mk-name:focus{outline:none}
.mk-nav{display:flex;gap:6px;flex:0 0 auto}
.mk-btn{width:34px;height:34px;border:1px solid var(--hair);background:none;color:var(--ink);border-radius:50%;
font:500 18px/1 var(--sans);cursor:pointer;display:grid;place-items:center;padding:0}
.mk-btn:hover{background:var(--wash)}
.mk-hero{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;margin:12px 0 2px}
.mk-price{font:600 46px/1 var(--sans);letter-spacing:-.015em}
.mk-day{font:600 14px var(--sans)}
.mk-asof{font:500 11px var(--sans);color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin:4px 0 12px}
.mk-bar{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px 12px;margin:0 0 10px}
.mk-range{display:inline-flex;border:1px solid var(--hair);border-radius:999px;padding:2px}
.mk-range button{font:600 12px var(--sans);letter-spacing:.05em;border:0;background:none;color:var(--muted);
padding:6px 13px;border-radius:999px;cursor:pointer}
.mk-range button:hover{color:var(--ink)}
.mk-range button[aria-pressed="true"]{background:var(--ink);color:var(--paper)}
.mk-ret{font:600 13px var(--sans)}
.mk-chart{position:relative;touch-action:pan-y}
.mk-chart:focus{outline:none}
.mk-chart:focus-visible{outline:2px solid var(--accent);outline-offset:4px;border-radius:2px}
.mk-chart svg{display:block;width:100%;height:auto;overflow:visible}
.mk-grid{stroke:var(--hair);stroke-width:1;shape-rendering:crispEdges}
.mk-tick{font:500 10.5px var(--sans);fill:var(--muted);font-variant-numeric:tabular-nums}
.mk-line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.mk-cross{stroke:var(--muted);stroke-width:1;shape-rendering:crispEdges}
.mk-tip{position:absolute;top:2px;pointer-events:none;background:var(--ink);color:var(--paper);
font:500 11px/1.35 var(--sans);padding:6px 9px;border-radius:4px;white-space:nowrap}
.mk-tip b{display:block;font-size:14px;font-weight:700}
.mk-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 18px;margin:14px 0 10px;padding-top:12px;
border-top:1px solid var(--hair)}
@media(max-width:520px){.mk-stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
.mk-stats dt{font:600 10px/1.3 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.mk-stats dd{margin:3px 0 0;font:600 15px/1.25 var(--sans)}
.mk-stats dd small{display:block;font-weight:500;color:var(--muted);font-size:11px}
.mk-table summary{cursor:pointer;font:600 12px var(--sans);color:var(--ink2);padding:4px 0}
.mk-table table{width:100%;border-collapse:collapse;font:500 12.5px var(--sans);font-variant-numeric:tabular-nums;margin-top:6px}
.mk-table th{font-weight:600;color:var(--muted);font-size:10.5px;letter-spacing:.07em;text-transform:uppercase}
.mk-table th,.mk-table td{text-align:right;padding:5px 6px;border-bottom:1px dotted var(--hair)}
.mk-table th:first-child,.mk-table td:first-child{text-align:left}
.mk-foot{font:500 11.5px/1.5 var(--sans);color:var(--muted);margin:10px 0 0}
.mk-foot a{color:var(--ink)}

/* section nav */
.secnav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--rule)}
.secnav-in{max-width:1280px;margin:0 auto;padding:0 16px;display:flex;align-items:center;gap:14px}
@media(min-width:760px){.secnav-in{padding:0 32px}}
.links{display:flex;gap:18px;overflow-x:auto;scrollbar-width:none;flex:1;min-width:0}
.links::-webkit-scrollbar{display:none}
.links a{font:600 11.5px/1 var(--sans);text-transform:uppercase;letter-spacing:.065em;text-decoration:none;
padding:14px 0 12px;white-space:nowrap;border-bottom:2px solid transparent}
.links a:hover{border-color:var(--accent)}
.search input{font:14px var(--sans);background:var(--paper);border:1px solid var(--hair);color:var(--ink);
padding:6px 10px;border-radius:3px;width:190px}
.qn{font:600 11px var(--sans);color:var(--accent);white-space:nowrap}
.qn:empty{display:none}
@media(max-width:640px){.search input{width:104px}}

/* stories */
.story{margin:0}
.kicker{font:700 10.5px/1.3 var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin:0 0 6px}
.hl{font-family:var(--serif);font-weight:600;font-size:19px;line-height:1.2;margin:0 0 6px;letter-spacing:-.003em}
.hl a{text-decoration:none}
.hl a:hover,.brief-list a:hover,.plain a:hover{text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px}
.hl a:visited,.brief-list a:visited,.plain a:visited{color:var(--visited)}
.dek{margin:0 0 8px;color:var(--ink2);font-size:15.5px;line-height:1.48}
.meta{font:500 10.5px/1.5 var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
display:flex;flex-wrap:wrap;align-items:center;gap:2px 6px;margin:0}
.meta time{white-space:nowrap}
.src{text-decoration:none;color:var(--ink);font-weight:600}
.src:hover{color:var(--accent)}
.src.locked{color:var(--lock)}
.sep{color:var(--hair)}
.cov{color:var(--accent);font-weight:700;margin-left:6px}
.lock{width:.9em;height:.9em;vertical-align:-.1em;margin-left:3px;fill:currentColor}
.more{list-style:none;margin:9px 0 0;padding:8px 0 0;border-top:1px dotted var(--hair)}
.more li{position:relative;padding:3px 0 3px 14px;font-size:14.5px;line-height:1.35}
.more li::before{content:"";position:absolute;left:0;top:.62em;width:6px;height:6px;background:var(--accent)}
.more a{text-decoration:none;font-weight:500}
.more a:hover{text-decoration:underline;text-underline-offset:3px}
.more a:visited{color:var(--visited)}
.ms{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-left:7px}
.fig{display:block;margin:0 0 10px;overflow:hidden;background:var(--hair)}
.fig img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;transition:transform .6s cubic-bezier(.2,.7,.2,1)}
.story:hover .fig img{transform:scale(1.035)}
@media (prefers-reduced-motion:reduce){.fig img{transition:none}.story:hover .fig img{transform:none}}

/* front page */
.front-top{display:grid;gap:22px;padding:22px 0;border-bottom:1px solid var(--rule)}
.lead .hl{font-size:clamp(28px,3.3vw,44px);line-height:1.07;font-weight:700;letter-spacing:-.012em;margin:8px 0 10px}
.lead .dek{font-size:19px;line-height:1.5}
.front-side .story{padding:0 0 16px;margin-bottom:16px;border-bottom:1px solid var(--hair)}
.front-side .story:last-child{border-bottom:0;margin-bottom:0;padding-bottom:0}
.front-side .hl{font-size:22px;line-height:1.16}
.front-row{display:grid;gap:18px;padding:20px 0 22px;border-bottom:4px double var(--rule)}
.front-row .story{padding-top:16px;border-top:1px solid var(--hair)}
.front-row .story:first-child{border-top:0;padding-top:0}
@media(min-width:700px){.front-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 0}
.front-row .story{border-top:0;padding:0 22px}.front-row .story:nth-child(odd){padding-left:0}
.front-row .story:nth-child(even){border-left:1px solid var(--hair);padding-right:0}}
@media(min-width:1080px){.front-row{grid-template-columns:repeat(4,minmax(0,1fr))}
.front-row .story,.front-row .story:nth-child(even){padding:0 20px;border-left:1px solid var(--hair)}
.front-row .story:first-child{padding-left:0;border-left:0}.front-row .story:last-child{padding-right:0}}
@media(min-width:920px){.front-top{grid-template-columns:minmax(0,7fr) minmax(0,4fr);gap:0}
.lead{padding-right:28px;border-right:1px solid var(--hair)}.front-side{padding-left:28px}}

/* sections */
.sec{padding:26px 0 10px;border-bottom:1px solid var(--rule)}
.sec-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 12px;
border-top:4px solid var(--rule);padding-top:8px;margin-bottom:18px}
.sec-head h2{font:700 30px/1.1 var(--serif);letter-spacing:-.01em;margin:0}
.sec-note{font:500 11px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.sec-note .lock{color:var(--lock)}
.sec-body{columns:3 270px;column-gap:44px;column-rule:1px solid var(--hair)}
.sec-body .story{break-inside:avoid;padding-bottom:15px;margin-bottom:15px;border-bottom:1px solid var(--hair)}
.feature .hl{font-size:25px;line-height:1.14;font-weight:700}
.feature .dek{font-size:17px}
.small-h{font:700 11px/1.3 var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
.briefs{margin:10px 0 14px;padding-top:12px;border-top:1px solid var(--hair)}
.brief-list{list-style:none;margin:0;padding:0;columns:2 320px;column-gap:44px;column-rule:1px solid var(--hair)}
.brief-list li{break-inside:avoid;padding:7px 0;border-bottom:1px dotted var(--hair)}
.brief-list li>a{text-decoration:none;font-weight:500;font-size:15.5px;line-height:1.3;display:block;margin-bottom:2px}

/* watchlist · Nifty movers · week ahead */
.dash{display:grid;gap:28px;padding:26px 0 20px;border-bottom:1px solid var(--rule)}
.dash .sec-head h2,.lens .sec-head h2{font-size:25px}
@media(min-width:760px) and (max-width:1079px){.dash{grid-template-columns:repeat(2,minmax(0,1fr));gap:28px 0}
.dash-col:nth-child(odd){padding-right:26px}.dash-col:nth-child(even){padding-left:26px;border-left:1px solid var(--hair)}}
@media(min-width:1080px){.dash{grid-template-columns:repeat(3,minmax(0,1fr));gap:0}
.dash-col{padding:0 24px;border-left:1px solid var(--hair)}.dash-col:first-child{padding-left:0;border-left:0}
.dash-col:last-child{padding-right:0}}
.wl,.mv-list,.cal{list-style:none;margin:0;padding:0}
.wl li{padding:8px 0;border-bottom:1px dotted var(--hair)}
.wl li:last-child,.cal li:last-child{border-bottom:0}
.wl-row{display:flex;align-items:baseline;gap:10px;text-decoration:none;color:inherit;font:600 15px/1.3 var(--sans)}
.wl-name{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wl-px{font-variant-numeric:tabular-nums}
.wl-row .up,.wl-row .down,.wl-row .flat{font-size:12.5px;min-width:66px;text-align:right;font-variant-numeric:tabular-nums}
.wl-row:hover .wl-name,.mv-row:hover .mv-name{text-decoration:underline;text-underline-offset:3px}
.wl-line{margin:3px 0 0;font-size:13.5px;line-height:1.38;color:var(--ink2)}
.wl-line a{text-decoration:none}.wl-line a:hover{text-decoration:underline}
.wl-line a:visited{color:var(--visited)}
.wl-quiet{color:var(--muted);font-style:italic;font-size:14px;margin:8px 0 0}
.mv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px}
.mv-list li{border-bottom:1px dotted var(--hair)}
.mv-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:7px 0;text-decoration:none;
color:inherit;font:600 13.5px/1.3 var(--sans)}
.mv-name{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mv-row .up,.mv-row .down,.mv-row .flat{font-size:12.5px;flex:0 0 auto;font-variant-numeric:tabular-nums}
.cal li{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:12px;align-items:center;padding:8px 0;
border-bottom:1px dotted var(--hair)}
.cal-d{display:flex;flex-direction:column;align-items:center;line-height:1.08;font-family:var(--sans);
border:1px solid var(--hair);border-radius:4px;padding:4px 0 5px;background:var(--paper)}
.cal-d b{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.cal-d span{font-size:18px;font-weight:700}
.cal-d small{font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.cal-t b{display:block;font:600 14.5px/1.25 var(--sans)}
.cal-t small{display:block;font:500 12.5px/1.35 var(--sans);color:var(--muted);margin-top:2px}
.cal-holiday .cal-t b{color:var(--accent)}
.cal-tag{font:700 10px var(--sans);letter-spacing:.08em;color:var(--muted);border:1px solid var(--hair);border-radius:3px;
padding:2px 5px}

/* the CFA Lens */
.lens{padding:26px 0 22px;border-bottom:1px solid var(--rule)}
.lens-grid{display:grid;gap:0}
.lens-card+.lens-card{margin-top:18px;padding-top:18px;border-top:1px solid var(--hair)}
@media(min-width:900px){.lens-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
.lens-card,.lens-card+.lens-card{margin:0;padding:0 22px;border-top:0;border-left:1px solid var(--hair)}
.lens-card:first-child{padding-left:0;border-left:0}.lens-card:last-child{padding-right:0}}
.lens-h{font:700 21px/1.2 var(--serif);margin:0 0 8px}
.lens-news{font:500 13px/1.4 var(--sans);margin:0 0 9px;color:var(--ink2)}
.lens-news span{display:block;font-weight:700;font-size:10px;letter-spacing:.09em;text-transform:uppercase;
color:var(--muted);margin-bottom:2px}
.lens-news a{text-decoration:none}.lens-news a:hover{text-decoration:underline}
.lens-x{margin:0 0 10px;font-size:15.5px;line-height:1.48;color:var(--ink2)}
.lens-angle{margin:0;padding:10px 12px;background:var(--wash);border-left:3px solid var(--accent);
font:500 13.5px/1.45 var(--sans)}
.lens-angle b{display:block;font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--accent);margin-bottom:3px}

/* regulators + subscriber desk */
.band{display:grid;gap:30px;padding:26px 0 18px;border-bottom:4px double var(--rule)}
@media(min-width:960px){.band{grid-template-columns:minmax(0,4fr) minmax(0,8fr);gap:0}
.reg{padding-right:28px;border-right:1px solid var(--hair)}.subs{padding-left:28px}}
.desk-h{font:700 12px/1.3 var(--sans);letter-spacing:.09em;text-transform:uppercase;margin:4px 0 8px;display:flex;align-items:center;gap:6px}
.desk-h .lock{margin:0;color:var(--lock)}
.plain{list-style:none;margin:0 0 18px;padding:0}
.plain li{padding:8px 0;border-bottom:1px dotted var(--hair)}
.plain li:last-child{border-bottom:0}
.plain a{text-decoration:none;font-weight:600;font-size:15.5px;line-height:1.3}
.plain .dek{font-size:14px;margin:3px 0 3px}
.subs-grid{columns:2 290px;column-gap:40px;column-rule:1px solid var(--hair)}
.pub{break-inside:avoid;margin-bottom:10px}

/* footer */
.foot{padding-top:26px;padding-bottom:56px;font:14px/1.6 var(--sans);color:var(--ink2)}
.foot-grid{display:grid;gap:26px}
@media(min-width:860px){.foot-grid{grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:48px}}
.foot p{margin:0 0 10px}
.srcs{list-style:none;margin:0;padding:0;columns:2 210px;column-gap:32px;font-size:13px}
.srcs li{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dotted var(--hair);break-inside:avoid}
.srcs .n{color:var(--muted);font-variant-numeric:tabular-nums}
.srcs .lock{color:var(--lock)}
.colophon{margin-top:22px!important;padding-top:12px;border-top:1px solid var(--hair);font-size:12px;color:var(--muted)}

/* archive */
.arch{padding-bottom:60px}
.arch-m{font:700 26px var(--serif);margin:30px 0 8px;padding-top:8px;border-top:4px solid var(--rule)}
.arch-row{display:grid;grid-template-columns:70px 1fr;gap:2px 16px;padding:11px 0;border-bottom:1px solid var(--hair);text-decoration:none}
.arch-row:hover .arch-l{text-decoration:underline}
.arch-d{font:700 12px/1.6 var(--sans);letter-spacing:.07em;text-transform:uppercase;grid-row:span 2}
.arch-l{font-weight:600;font-size:18px;line-height:1.25}
.arch-n{font:500 11px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
"""

JS = r"""
(function(){
  var root=document.documentElement, body=document.body;
  var tb=document.getElementById('theme');
  if(tb) tb.addEventListener('click',function(){
    var cur=root.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
    var next=cur==='dark'?'light':'dark';
    root.setAttribute('data-theme',next);
    try{localStorage.setItem('dl-theme',next)}catch(e){}
  });
  var built=new Date(body.getAttribute('data-built'));
  var st=document.getElementById('stale');
  if(st && !body.hasAttribute('data-archived') && Date.now()-built.getTime()>26*3600*1000){
    st.textContent='This is the edition of '+built.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'})+
      '. Today’s paper is printed at about 6 AM India time — if it’s later than that, try reloading.';
    st.hidden=false;
  }
  /* Offline mode: a service worker keeps the latest copy of each page you open. */
  var rootPath=body.getAttribute('data-root')||'';
  if('serviceWorker' in navigator && location.protocol==='https:'){
    window.addEventListener('load',function(){navigator.serviceWorker.register(rootPath+'sw.js').catch(function(){})});
  }
  function offline(){
    if(!st) return;
    if(!navigator.onLine){
      st.textContent='You’re offline, so this is the copy saved on your device (printed '+
        built.toLocaleString(undefined,{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})+').';
      st.hidden=false;
    } else if(st.textContent.indexOf('offline')>-1){ st.hidden=true; }
  }
  offline();
  window.addEventListener('offline',offline); window.addEventListener('online',offline);
  var q=document.getElementById('q'), qn=document.getElementById('qn');
  if(!q) return;
  var items=[].slice.call(document.querySelectorAll('[data-s]'));
  var secs=[].slice.call(document.querySelectorAll('[data-sec]'));
  function run(){
    var words=q.value.toLowerCase().trim().split(/\s+/).filter(Boolean), n=0;
    items.forEach(function(el){
      var s=el.getAttribute('data-s'), ok=words.every(function(w){return s.indexOf(w)>-1});
      el.classList.toggle('hide',!ok); if(ok&&words.length) n++;
    });
    secs.forEach(function(sec){
      sec.classList.toggle('hide', words.length>0 && !sec.querySelector('[data-s]:not(.hide)'));
    });
    qn.textContent=words.length?(n+(n===1?' match':' matches')):'';
  }
  q.addEventListener('input',run);
  document.addEventListener('keydown',function(e){
    if(document.querySelector('dialog[open]')) return;
    if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus();}
    else if(e.key==='Escape'&&document.activeElement===q){q.value='';run();q.blur();}
  });
})();

/* Market chart panel: tap a market in the strip -> its daily closes, 1M to 1Y. */
(function(){
  var dlg=document.getElementById('mk'), src=document.getElementById('mk-data');
  if(!dlg||!src) return;
  var Q=JSON.parse(src.textContent), cur=0, range=126;
  var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  var NS='http://www.w3.org/2000/svg', $=function(id){return document.getElementById(id)};
  var SPAN={21:'1 month',63:'3 months',126:'6 months',400:'1 year'};

  function num(v,d){return v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d})}
  function fmt(v,k){
    if(k==='yield') return num(v,2)+'%';
    if(k==='fx') return '₹'+num(v,2);
    if(k==='usd') return '$'+num(v,v<1000?2:0);
    if(k==='usd2') return '$'+num(v,2);
    if(k==='inr') return '₹'+num(v,2);
    return num(v,2);
  }
  function tick(v,k,d){
    return k==='yield'?v.toFixed(d)+'%':(k==='usd'||k==='usd2')?'$'+num(v,d):k==='inr'?'₹'+num(v,d):num(v,d);
  }
  function move(a,b,k){
    var up=a>b, dn=a<b, arrow=up?'▲ ':dn?'▼ ':'● ';
    var t=k==='yield'?Math.abs(Math.round((a-b)*100))+' bp':Math.abs((a/b-1)*100).toFixed(2)+'%';
    return {cls:up?'up':dn?'down':'flat', text:arrow+t};
  }
  var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function day(d,yr){var x=new Date(d*864e5); return x.getUTCDate()+' '+MON[x.getUTCMonth()]+(yr?' '+x.getUTCFullYear():'')}
  function mon(d){return new Date(d*864e5).getUTCMonth()}
  function svgEl(tag,attrs,parent){
    var e=document.createElementNS(NS,tag);
    for(var a in attrs) e.setAttribute(a,attrs[a]);
    parent.appendChild(e); return e;
  }
  function stat(dl,label,value,note,cls){
    var g=document.createElement('div'), dt=document.createElement('dt'), dd=document.createElement('dd'), v=document.createElement('span');
    dt.textContent=label; v.textContent=value; if(cls) v.className=cls; dd.appendChild(v);
    if(note){var s=document.createElement('small'); s.textContent=note; dd.appendChild(s);}
    g.appendChild(dt); g.appendChild(dd); dl.appendChild(g);
  }

  function header(){
    var q=Q[cur], n=q.c.length, last=q.c[n-1], prev=q.c[n-2];
    $('mk-name').textContent=q.label;
    $('mk-price').textContent=fmt(last,q.kind);
    var d=move(last,prev,q.kind), de=$('mk-day');
    de.textContent=d.text+' on the day'; de.className='mk-day '+d.cls;
    $('mk-asof').textContent='As of '+day(q.t[n-1],true);
    $('mk-link').href=q.url;
    var hi=-Infinity, lo=Infinity, hiD=0, loD=0;
    for(var i=0;i<n;i++){ if(q.c[i]>hi){hi=q.c[i];hiD=q.t[i]} if(q.c[i]<lo){lo=q.c[i];loD=q.t[i]} }
    var dl=$('mk-stats'); dl.textContent='';
    stat(dl,'Previous close',fmt(prev,q.kind),day(q.t[n-2],true));
    stat(dl,'52-week high',fmt(hi,q.kind),day(hiD,true));
    stat(dl,'52-week low',fmt(lo,q.kind),day(loD,true));
    var m=move(last,q.c[Math.max(0,n-22)],q.kind); stat(dl,'1 month',m.text,null,m.cls);
    var y=move(last,q.c[0],q.kind); stat(dl,n>=240?'1 year':'Since '+day(q.t[0],true),y.text,null,y.cls);
    var off=q.kind==='yield'?Math.round((hi-last)*100)+' bp':((1-last/hi)*100).toFixed(1)+'%';
    stat(dl,'From the high',last>=hi?'At the high':off+' below',null,null);
  }

  function rows(q,c,t,k){
    var body=$('mk-rows'), pick=[], i;
    body.textContent='';
    if(k<=25){ for(i=k-1;i>=0;i--) pick.push(i); }
    else {
      var monthly=k>70, key=function(j){  /* last close of each month, or of each Monday-to-Friday week */
        var d=new Date(t[j]*864e5); return monthly?d.getUTCFullYear()*12+d.getUTCMonth():Math.floor((t[j]-4)/7)};
      for(i=k-1;i>=0;i--) if(i===k-1||key(i)!==key(i+1)) pick.push(i);
    }
    for(var j=0;j<pick.length;j++){
      var a=pick[j], b=pick[j+1], tr=document.createElement('tr'), ch=b===undefined?null:move(c[a],c[b],q.kind);
      [day(t[a],true),fmt(c[a],q.kind),ch?ch.text:'—'].forEach(function(v,x){
        var td=document.createElement('td'); td.textContent=v; if(x===2&&ch) td.className=ch.cls; tr.appendChild(td);
      });
      body.appendChild(tr);
    }
  }

  function draw(){
    var q=Q[cur], n=q.c.length, k=Math.min(range,n), c=q.c.slice(n-k), t=q.t.slice(n-k);
    var box=$('mk-chart'); box.textContent='';
    var W=Math.max(280,box.clientWidth), H=Math.round(Math.min(260,Math.max(190,W*.42)));
    var L=56, R=10, T=12, B=26, pw=W-L-R, ph=H-T-B;
    var lo=Math.min.apply(null,c), hi=Math.max.apply(null,c), pad=(hi-lo)*.08||Math.abs(hi)*.01||1;
    lo-=pad; hi+=pad;
    var raw=(hi-lo)/4, p=Math.pow(10,Math.floor(Math.log10(raw))), mm=raw/p;
    var step=p*(mm>=7.5?10:mm>=3.5?5:mm>=1.5?2:1), dec=Math.max(0,Math.min(2,-Math.floor(Math.log10(step))));
    var X=function(i){return L+(k<2?pw/2:i*pw/(k-1))}, Y=function(v){return T+(hi-v)/(hi-lo)*ph};
    var svg=svgEl('svg',{viewBox:'0 0 '+W+' '+H,width:W,height:H},box);
    for(var v=Math.ceil(lo/step)*step; v<=hi+1e-9; v+=step){
      var gy=Math.round(Y(v))+.5;
      svgEl('line',{x1:L,x2:W-R,y1:gy,y2:gy,'class':'mk-grid'},svg);
      svgEl('text',{x:L-8,y:gy+3.5,'text-anchor':'end','class':'mk-tick'},svg).textContent=tick(v,q.kind,dec);
    }
    var lastX=-1e9;
    for(var i=0;i<k;i++){
      var lab=null;
      if(k<=25){ if((k-1-i)%5===0) lab=day(t[i]); }
      else if(i>0&&mon(t[i])!==mon(t[i-1])){
        var dt=new Date(t[i]*864e5);
        lab=MON[dt.getUTCMonth()]+(dt.getUTCMonth()===0?' ’'+String(dt.getUTCFullYear()).slice(2):'');
      }
      var x=X(i);
      if(lab&&x-lastX>=44&&x>L+12&&x<W-R-12){
        svgEl('text',{x:x,y:H-7,'text-anchor':'middle','class':'mk-tick'},svg).textContent=lab; lastX=x;
      }
    }
    var up=c[k-1]>=c[0], col=up?'var(--up)':'var(--down)', d='';
    for(i=0;i<k;i++) d+=(i?'L':'M')+X(i).toFixed(1)+' '+Y(c[i]).toFixed(1);
    svgEl('path',{d:d+'L'+X(k-1).toFixed(1)+' '+(T+ph)+'L'+X(0).toFixed(1)+' '+(T+ph)+'Z',style:'fill:'+col+';fill-opacity:.1'},svg);
    var line=svgEl('path',{d:d,'class':'mk-line',style:'stroke:'+col},svg);
    svgEl('circle',{cx:X(k-1),cy:Y(c[k-1]),r:4.5,style:'fill:'+col+';stroke:var(--paper);stroke-width:2'},svg);
    if(!reduce&&line.getTotalLength){
      var len=line.getTotalLength();
      line.style.strokeDasharray=len; line.style.strokeDashoffset=len;
      line.getBoundingClientRect();
      line.style.transition='stroke-dashoffset .7s ease-out'; line.style.strokeDashoffset=0;
      /* if the browser skips the transition (background tab, power saving), still show the line */
      setTimeout(function(){line.style.transition='none'; line.style.strokeDasharray='none'},800);
    }
    var cross=svgEl('line',{y1:T,y2:T+ph,'class':'mk-cross',visibility:'hidden'},svg);
    var dot=svgEl('circle',{r:4.5,visibility:'hidden',style:'fill:'+col+';stroke:var(--paper);stroke-width:2'},svg);
    var hit=svgEl('rect',{x:L-6,y:0,width:pw+12,height:H,fill:'transparent'},svg);
    var tip=document.createElement('div'), tb=document.createElement('b'), ts=document.createElement('span');
    tip.className='mk-tip'; tip.hidden=true; tip.appendChild(tb); tip.appendChild(ts); box.appendChild(tip);
    var idx=k-1;
    function show(j){
      idx=Math.max(0,Math.min(k-1,j)); var x=X(idx), y=Y(c[idx]);
      cross.setAttribute('x1',x); cross.setAttribute('x2',x); cross.setAttribute('visibility','visible');
      dot.setAttribute('cx',x); dot.setAttribute('cy',y); dot.setAttribute('visibility','visible');
      tb.textContent=fmt(c[idx],q.kind); ts.textContent=day(t[idx],true); tip.hidden=false;
      var sx=x*box.clientWidth/W, tw=tip.offsetWidth;
      tip.style.left=Math.max(0,Math.min(box.clientWidth-tw,sx-tw/2))+'px';
    }
    function hide(){cross.setAttribute('visibility','hidden'); dot.setAttribute('visibility','hidden'); tip.hidden=true}
    function at(e){var r=svg.getBoundingClientRect(); return Math.round(((e.clientX-r.left)*W/r.width-L)/(pw/Math.max(1,k-1)))}
    hit.addEventListener('pointermove',function(e){show(at(e))});
    hit.addEventListener('pointerdown',function(e){show(at(e))});
    hit.addEventListener('pointerleave',hide);
    box.onkeydown=function(e){
      if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault(); e.stopPropagation(); show((tip.hidden?k-1:idx)+(e.key==='ArrowLeft'?-1:1))}
      else if(e.key==='Home'){e.preventDefault(); show(0)} else if(e.key==='End'){e.preventDefault(); show(k-1)}
    };
    box.onblur=hide;
    var span=range===400?(n>=240?'1 year':'since '+day(t[0],true)):SPAN[range];
    box.setAttribute('aria-label',q.label+' closing prices over '+span+': '+fmt(c[0],q.kind)+' to '+fmt(c[k-1],q.kind));
    var r=move(c[k-1],c[0],q.kind), re=$('mk-ret');
    re.textContent=r.text+(range===400&&n<240?' ':' over ')+span; re.className='mk-ret '+r.cls;
    rows(q,c,t,k);
  }

  var btns=dlg.querySelectorAll('.mk-range button');
  function setRange(r){
    range=r;
    for(var i=0;i<btns.length;i++) btns[i].setAttribute('aria-pressed',String(+btns[i].getAttribute('data-r')===r));
    draw();
  }
  for(var b=0;b<btns.length;b++) btns[b].addEventListener('click',function(){setRange(+this.getAttribute('data-r'))});

  function open(i){
    cur=(i+Q.length)%Q.length; header();
    if(!dlg.open) dlg.showModal();
    setRange(range);
    try{history.replaceState(null,'','#'+Q[cur].id)}catch(e){}
  }
  document.querySelectorAll('a[data-i]').forEach(function(a){
    a.addEventListener('click',function(e){
      e.preventDefault(); var i=+a.getAttribute('data-i');
      if(dlg.showModal) open(i); else window.open(Q[i].url,'_blank','noopener');
    });
  });
  dlg.querySelectorAll('[data-step]').forEach(function(s){
    s.addEventListener('click',function(){open(cur+(+s.getAttribute('data-step')))});
  });
  $('mk-x').addEventListener('click',function(){dlg.close()});
  dlg.addEventListener('click',function(e){if(e.target===dlg) dlg.close()});
  dlg.addEventListener('close',function(){try{history.replaceState(null,'',location.href.split('#')[0])}catch(e){}});
  dlg.addEventListener('keydown',function(e){
    if((e.key==='ArrowLeft'||e.key==='ArrowRight')&&e.target.id!=='mk-chart') open(cur+(e.key==='ArrowLeft'?-1:1));
  });
  window.addEventListener('resize',function(){if(dlg.open) draw()});
  var h=location.hash.slice(1);
  for(var i=0;i<Q.length;i++) if(Q[i].id===h&&dlg.showModal){open(i); break}
})();
"""

if __name__ == "__main__":
    sys.exit(main())
