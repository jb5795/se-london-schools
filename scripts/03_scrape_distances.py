"""
Step 3: collect each state school's furthest-admitted distance, year by year.

The "furthest-admitted distance" is how far from the school the last child
offered a place under the distance criterion lived. Councils publish it each
year after National Offer Day. This script gathers every year it can find,
from these sources in this order (docs/PROJECT_BRIEF.md, section 3):

  1. Lewisham: "last year's applications and appeals" pages (secondary),
     the reception "grid" PDFs (primary), and the secondary distance map.
  2. Lambeth: "How offers were made on National Offer Day" documents
     (one PDF per secondary school; four combined PDFs for primary).
  3. Southwark: the community primary allocation tables (inside the primary
     brochures and as separate files).
  4. Own-admission-authority schools still without a figure: the school's own
     website (admissions pages and PDFs), capped at a few minutes per school.
  5. web.archive.org: earlier years of all the council documents above.
     Steps 1 to 3 automatically include archived copies.

How years are labelled
  "2026" means places offered in spring 2026 for children starting in
  September 2026. Each figure's year is read from the document itself (e.g.
  "Secondary Transfer 2026", "allocated on 2 March 2026", "for September 2026"),
  never just from the web page that links to it. Pages sometimes link last
  year's file.

"All applicants offered"
  When a school had places for everyone who applied, councils print "All
  applicants offered a place" instead of a distance. We record that as
  allApplicantsOffered: true with metres: null. Any postcode was "inside" that
  year.

Output: data/source-snapshots/distances.json, keyed by URN.
Every source file is saved under data/source-snapshots/raw/distances/ (ignored by git).

Run from the main project folder (after step 1):
    python3 scripts/03_scrape_distances.py
This takes a long time, mostly because web.archive.org must be asked slowly.
Re-runs are much faster: every download is cached.
"""

import datetime
import difflib
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import openpyxl
import pdfplumber
import requests

# ---------- settings ----------

PROJECT = Path(__file__).resolve().parent.parent
SNAPSHOTS = PROJECT / "data" / "source-snapshots"
CACHE = SNAPSHOTS / "raw" / "distances"           # every downloaded source; ignored by git
GIAS_FILE = SNAPSHOTS / "gias-cleaned.json"
OUTPUT = SNAPSHOTS / "distances.json"

# The brief's defaults when no figure can be found (1287 m = 0.8 mi, 2414 m = 1.5 mi).
ESTIMATE_METRES = {"primary": 1287, "secondary": 2414}

# School types that set their own admission rules ("own admission authority").
# For these, the council may not publish figures, so we also try the school's website.
OWN_AUTHORITY_TYPES = ("Academy", "Free school", "Foundation school", "Voluntary aided", "City technology")

WEBSITE_SECONDS = 180      # time cap per school website (the brief: "a few minutes")
WEBSITE_MAX_REQUESTS = 15  # and a cap on pages/files fetched per school

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

START = time.time()


def log(msg):
    """Print a progress line with elapsed minutes, so long runs are easy to follow."""
    print(f"[{(time.time() - START) / 60:5.1f} min] {msg}", flush=True)


# =====================================================================
# 1. Fetching, with a cache and polite pauses
# =====================================================================

class Fetcher:
    """Downloads files once and keeps them in CACHE, so re-runs don't hit websites again.

    web.archive.org limits how fast you can ask it for pages. It returns
    429 "too many requests" or refuses connections. So we wait a few seconds
    between archive requests and back off longer when it pushes back.
    """

    def __init__(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.index_file = CACHE / "index.json"
        self.index = json.loads(self.index_file.read_text()) if self.index_file.exists() else {}
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.last_request = defaultdict(float)
        self.requests = 0

    def _save_index(self):
        self.index_file.write_text(json.dumps(self.index, indent=0))

    def get(self, url, timeout=60):
        """Return the file's bytes, or None if it can't be fetched."""
        key = hashlib.sha1(url.encode()).hexdigest()
        if key in self.index:
            entry = self.index[key]
            if entry.get("status") == 200:
                return (CACHE / entry["file"]).read_bytes()
            if entry.get("status") in (404, 410):
                return None                           # known missing; don't ask again
        host = urlparse(url).netloc
        gap = 5.0 if "archive.org" in host else 1.0
        for attempt in range(5):
            wait = self.last_request[host] + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last_request[host] = time.time()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=timeout)
            except requests.RequestException as e:
                if "archive.org" not in host:
                    self.index[key] = {"url": url, "status": "error", "error": str(e)[:200]}
                    self._save_index()
                    return None
                time.sleep(30 * (attempt + 1))       # the archive refused us; wait and retry
                continue
            if r.status_code in (429, 503, 502, 504) and attempt < 4:
                time.sleep(30 * (attempt + 1))
                continue
            if r.status_code != 200:
                self.index[key] = {"url": url, "status": r.status_code}
                self._save_index()
                return None
            ext = ".pdf" if r.content[:4] == b"%PDF" else (".xlsx" if r.content[:2] == b"PK" else ".html")
            name = key + ext
            (CACHE / name).write_bytes(r.content)
            self.index[key] = {"url": url, "status": 200, "file": name,
                               "fetched": datetime.datetime.now().isoformat(timespec="seconds")}
            self._save_index()
            return r.content
        return None

    def wayback(self, url, timestamp):
        """A raw archived copy ('id_' means without the archive's own toolbar added)."""
        return self.get(f"https://web.archive.org/web/{timestamp}id_/{url}")

    def cdx(self, url, match="exact", collapse="timestamp:6"):
        """Ask the archive which snapshots it holds. Returns [(timestamp, original_url)]."""
        params = {"url": url, "matchType": match, "output": "json", "filter": "statuscode:200",
                  "fl": "timestamp,original", "collapse": collapse}
        q = "https://web.archive.org/cdx/search/cdx?" + "&".join(f"{k}={requests.utils.quote(v, safe=':/')}"
                                                                 for k, v in params.items())
        body = self.get(q, timeout=180)
        if not body or not body.strip():
            return []
        try:
            rows = json.loads(body)
        except ValueError:
            return []
        return [(r[0], r[1]) for r in rows[1:]]


def live_or_archived(fetch, url):
    """Try the live URL. If it's gone, use the newest archived copy. Returns (bytes, url_used)."""
    b = fetch.get(url)
    if b:
        return b, url
    b = fetch.get(f"https://web.archive.org/web/2026id_/{url}")   # the archive picks the nearest copy
    return (b, f"https://web.archive.org/web/2026/{url}") if b else (None, url)


# =====================================================================
# 2. Turning files into text
# =====================================================================

def pdf_text(data):
    """Return (lines, tables) from a PDF, or (None, None) if it's broken.
    Archived PDFs are sometimes cut short, so we check."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            lines, tables = [], []
            for page in pdf.pages:
                lines += (page.extract_text() or "").splitlines()
                tables += page.extract_tables() or []
            return lines, tables
    except Exception:
        return None, None


def html_text(data):
    """Return (lines, tables) from a web page, with one line per paragraph or table row."""
    h = data.decode("utf-8", "replace")
    m = re.search(r"<main\b|id=\"reading-area\"", h)
    if m:
        h = h[m.start():]
    h = re.sub(r"<(script|style|nav|header|footer)\b.*?</\1>", " ", h, flags=re.S | re.I)
    tables = []
    for tab in re.findall(r"<table\b.*?</table>", h, flags=re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr\b.*?</tr>", tab, flags=re.S | re.I):
            cells = [clean_cell(c) for c in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", tr, flags=re.S | re.I)]
            rows.append(cells)
        tables.append(rows)
    h = re.sub(r"</t[hd]>", " ", h, flags=re.I)
    h = re.sub(r"<br\s*/?>|</(p|tr|li|h\d|div|dt|dd|table|section)>", "\n", h, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", h)
    lines = [clean_cell(l) for l in text.split("\n")]
    return [l for l in lines if l], tables


def xlsx_text(data):
    """Return (lines, tables) from an Excel file: one line per spreadsheet row."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines, tables = [], []
    for ws in wb.worksheets:
        rows = [["" if v is None else str(v).strip() for v in row] for row in ws.iter_rows(values_only=True)]
        tables.append(rows)
        lines += [" ".join(c for c in row if c) for row in rows]
    return lines, tables


def docx_text(data):
    """Return (lines, tables) from a Word file: one line per paragraph or table row.
    (Schools often publish admissions policies as Word documents.)"""
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:tc>", " ", xml)                       # table cells -> spaces
    xml = re.sub(r"</w:p>|</w:tr>|<w:br/>", "\n", xml)        # paragraphs and rows -> lines
    lines = [clean_cell(l) for l in re.sub(r"<[^>]+>", "", xml).split("\n")]
    return [l for l in lines if l], []


def clean_cell(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace("&#160;", " ").replace("&amp;", "&").replace("&#39;", "'")
    s = s.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip()


def to_lines(data):
    if not data:
        return None, None
    if data[:4] == b"%PDF":
        return pdf_text(data)
    if data[:2] == b"PK":
        # Both Excel (.xlsx) and Word (.docx) files are zip archives; look inside to tell them apart.
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
            if "word/document.xml" in names:
                return docx_text(data)
            return xlsx_text(data)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError):
            return None, None
    return html_text(data)


# =====================================================================
# 3. Matching school names in documents to GIAS schools
# =====================================================================

DROP_ALWAYS = {"the", "school", "schools", "primary", "academy", "college", "and", "nursery", "unit",
               "community", "free", "of", "a", "site", "secondary", "lewisham", "southwark", "lambeth"}
# (We keep "girls"/"boys": Harris Girls' and Harris Boys' Academy East Dulwich differ only by that word.)
DROP_LOOSE = {"ce", "catholic", "church", "england"}

# Names in council documents that no spelling rule can link to the GIAS name:
# old names of schools that have since converted, and odd spellings. Keys are norm() output.
ALIASES = {
    "london nautical": 150889,      # "The London Nautical School", now London Nautical, City of London Academy
    "lark hall": 100576,            # Lambeth writes "Lark Hall"; GIAS says "Larkhall Primary Campus"
    # Lambeth puts part of these names in brackets, which norm() drops:
    "christ church ce": 145614,     # "Christ Church (Streatham) Church of England Primary School"
    "st johns ce": 100615,          # "St John's (Angell Town) Church of England Primary School"
    "immanuel st andrews ce": 100633,  # "Immanuel and St Andrew's": GIAS has "St Andrew" without the 's
    "wyvil": 100591,                # GIAS adds "and Resource Bases for Speech, Language ..."
}


def norm(name, strict=True):
    """Simplify a school name so different spellings compare equal, e.g.
    "St Saviour's RC Primary School" and "St Saviour's Catholic Primary" -> "st saviours catholic".
    strict=True keeps church markers (ce/catholic), which tell apart e.g.
    St Andrew's Catholic and St Andrew's CE primaries."""
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)                  # drop "(primary)", "(Balham site)" etc.
    s = s.replace("&", " and ").replace("’", "'").replace("`", "'")
    s = re.sub(r"'s\b", "s", s).replace("'", "")
    s = re.sub(r"\bsaint\b|\bst\.", "st", s)
    s = re.sub(r"church of england|\bc of e\b|\bcofe\b|\bc\.e\.|\bce\b", " ce ", s)
    s = re.sub(r"roman catholic|\brc\b|\br\.c\.", " catholic ", s)
    s = re.sub(r"\binfants\b", "infant", s)
    s = re.sub(r"\bjuniors\b", "junior", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    drop = DROP_ALWAYS | (set() if strict else DROP_LOOSE)
    return " ".join(t for t in s.split() if t not in drop)


class Schools:
    """Looks up GIAS schools by the names used in council documents."""

    def __init__(self, gias):
        self.all = [s for s in gias if not s["isIndependent"]]
        self.by_urn = {s["urn"]: s for s in self.all}
        self.match_log = {}            # (borough, raw name) -> (urn, method), for auditing

    def match(self, raw, borough, phase=None, allow_fuzzy=True):
        """Return the URN for a name found in a document, or None.
        Only schools in the same borough (and phase, if known) are considered."""
        key = (borough, raw, phase)
        if key in self.match_log:
            return self.match_log[key][0]
        pool = [s for s in self.all if s["laName"] == borough and (phase is None or phase in s["phases"])]
        found, how = None, None
        alias = ALIASES.get(norm(raw, True))
        if alias in self.by_urn and self.by_urn[alias] in pool:
            found, how = self.by_urn[alias], "alias"
        for strict in (True, False):
            if found:
                break
            n = norm(raw, strict)
            if not n:
                continue
            hits = [s for s in pool if norm(s["name"], strict) == n]
            if len(hits) == 1:
                found, how = hits[0], "exact" if strict else "loose"
                break
            if len(hits) > 1:
                break
        if not found and allow_fuzzy:
            n = set(norm(raw, True).split())
            if n:
                # Every word in the document's name appears in the GIAS name, or the reverse
                # (e.g. "Haberdashers' Hatcham College" vs "Haberdashers' Aske's Hatcham College").
                subset = [s for s in pool if n <= set(norm(s["name"], True).split())
                          or set(norm(s["name"], True).split()) <= n and len(norm(s["name"], True).split()) >= 2]
                if len(subset) == 1:
                    found, how = subset[0], "subset"
                else:
                    scored = sorted(((difflib.SequenceMatcher(None, norm(raw, True), norm(s["name"], True)).ratio(), s)
                                     for s in pool), key=lambda x: -x[0])
                    if scored and scored[0][0] >= 0.9 and (len(scored) == 1 or scored[1][0] < 0.85):
                        found, how = scored[0][1], f"fuzzy {scored[0][0]:.2f}"
        self.match_log[key] = (found["urn"] if found else None, how)
        return found["urn"] if found else None


# =====================================================================
# 4. Reading figures out of text
# =====================================================================

NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
ALL_OFFERED = re.compile(r"all (?:applicants|places|children)(?: were)? (?:offered|received a place)|"
                         r"\ball offered\b|all applicants received", re.I)
LABEL = re.compile(r"(?:furthest|last|final|max(?:imum)?\.?)\b[^\n]{0,60}?distance|"
                   r"distance\b[^\n]{0,45}?\b(?:last|furthest|final)\b", re.I)
YEAR_HEADING = re.compile(r"(?:Secondary Transfer|for September|Offers for September|"
                          r"allocated on \d{1,2} \w+|National Offer Day \(\d{1,2} \w+)\s+(20\d\d)", re.I)


def to_float(s):
    return float(s.replace(",", ""))


def plausible(m):
    """A furthest distance in metres: more than 30 m and less than 40 km."""
    return 30 <= m <= 40000


def method_from(text):
    t = text.lower()
    if "walk" in t:
        return "walking"
    if "straight" in t or "crow" in t:
        return "straight"
    return None


def read_section(lines):
    """Find the distance information in one school's part of a document.

    Returns a list of findings, each {kind, metres, label, line, method}:
      kind "labelled": a line such as "Last distance offered on NOD 3123.01".
      kind "table":    a row of numbers with one distance (e.g. one per ability band).
      kind "all":      "All applicants offered a place".
    The caller assigns years (some documents hold several years).
    """
    found = []
    for i, line in enumerate(lines):
        if ALL_OFFERED.search(line):
            found.append({"kind": "all", "metres": None, "label": line[:120], "i": i, "method": None})
            continue
        if re.search(r"divide by|sat the test|score", line, re.I):
            continue                                  # test-score arithmetic, not distances
        m = LABEL.search(line)
        if m:
            rest = line[m.end():]
            if not re.search(NUM, rest) and i + 1 < len(lines):
                rest = lines[i + 1]                   # the number sometimes wraps to the next line
            rest = re.sub(r"\(.*?\)", " ", rest)      # drop "(under criteria 5)" etc.
            unit_miles = re.search(r"\bmiles?\b", rest, re.I)
            nums = [to_float(n) for n in re.findall(NUM, rest)]
            nums = [n for n in nums if n >= 30 or (unit_miles and n > 0)]
            if nums:
                metres, note = nums[0], None
                if unit_miles:
                    if nums[0] < 30:
                        metres = round(nums[0] * 1609.344, 1)
                    else:
                        note = "source says 'miles' but the figure can only be metres"
                if plausible(metres):
                    found.append({"kind": "labelled", "metres": metres, "label": line[:160], "i": i,
                                  "method": method_from(line), "note": note})
                continue
        # Table rows: numbers with 2–3 decimal places are distances in these documents.
        # Counts of children are whole numbers, so they're ignored.
        decimals = [to_float(n) for n in re.findall(r"(?<![\d.])(\d{1,3}(?:,\d{3})*\.\d{2,3}|\d+\.\d{2,3})(?![\d.])", line)]
        decimals = [d for d in decimals if plausible(d)]
        for d in decimals:
            found.append({"kind": "table", "metres": d, "label": line[:160], "i": i, "method": method_from(line)})
    return found


def doc_year(lines):
    """The intake year a document is about, read from its own text.
    Looks for the first matching statement near the top of the document."""
    head = "\n".join(lines[:15])
    # The offer-day date comes first: Lambeth's 2023 London Nautical PDF is headed
    # "Secondary Transfer 2022 National Offer Day (01/03/23)", and the date is the right one.
    for pat in [r"National Offer Day \((\d\d)/(\d\d)/(\d{2}|\d{4})\)", r"Secondary Transfer\s+(20\d\d)",
                r"allocated on \d{1,2} \w+ (20\d\d)", r"National Offer Day \(\d{1,2} \w+ (20\d\d)\)",
                r"for September (20\d\d)", r"(20\d\d) application and appeal rates",
                r"admissions for (20\d\d)\s*/\s*\d\d", r"(20\d\d)\s*-\s*\d\d intake", r"(20\d\d)/\d\d reception",
                r"[Rr]eception (20\d\d)"]:
        m = re.search(pat, head, re.I)
        if m:
            if len(m.groups()) == 3:
                return int(m.group(3)) if len(m.group(3)) == 4 else 2000 + int(m.group(3))
            return int(m.group(1))
    return None


def year_markers(lines):
    """Line numbers where a document starts talking about a different year,
    e.g. "Offers for September 2023". Used to split multi-year documents."""
    marks = []
    for i, line in enumerate(lines):
        m = YEAR_HEADING.search(line)
        if m:
            marks.append((i, int(m.group(1))))
    # Only a document that names two or more years is a multi-year document. A single
    # heading (possibly a typo, as in the London Nautical 2023 PDF) is left to doc_year().
    return marks if len({y for _, y in marks}) >= 2 else []


def year_for_line(i, marks, default):
    y = default
    for j, yr in marks:
        if j <= i:
            y = yr
    return y


# =====================================================================
# 5. The record of everything found
# =====================================================================

class Findings:
    """Collects observations: one figure, for one school, phase and year, from one source."""

    def __init__(self, schools):
        self.schools = schools
        self.obs = []                       # list of dicts
        self.tried = defaultdict(list)      # urn -> URLs tried
        self.notes = defaultdict(set)       # urn -> admissions notes
        self.method_evidence = defaultdict(list)
        self.unmatched = defaultdict(set)   # source -> names we couldn't match
        self.band_ranges = defaultdict(dict)  # urn -> {year: (lowest, highest) band cut-off}

    def add(self, urn, phase, year, metres, all_offered, source, url, rank, label, method=None,
            bands=None, note=None):
        """rank: how much we trust the source (1 = council document, 2 = council map,
        3 = school website). Used when two sources disagree about the same year."""
        s = self.schools.by_urn[urn]
        if phase not in s["phases"]:
            return
        self.obs.append({"urn": urn, "phase": phase, "year": str(year), "metres": metres,
                         "allApplicantsOffered": all_offered, "source": source, "sourceUrl": url,
                         "rank": rank, "evidence": label, "method": method, "bands": bands, "note": note})


def record_section(F, urn, phase, findings, marks, default_year, source, url, rank):
    """Turn read_section() output for one school into observations, grouped by year."""
    by_year = defaultdict(list)
    for f in findings:
        y = year_for_line(f["i"], marks, default_year)
        if y:
            by_year[y].append(f)
    for y, fs in by_year.items():
        labelled = [f for f in fs if f["kind"] == "labelled"]
        table = [f for f in fs if f["kind"] == "table"]
        alls = [f for f in fs if f["kind"] == "all"]
        method = next((f["method"] for f in fs if f["method"]), None)
        note = next((f.get("note") for f in fs if f.get("note")), None)
        if labelled:
            # Several labelled figures (e.g. Catholic and other applicants): keep them all;
            # metres is the furthest, which is the brief's definition.
            best = max(labelled, key=lambda f: f["metres"])
            bands = [{"label": f["label"], "metres": f["metres"]} for f in labelled] if len(labelled) > 1 else None
            F.add(urn, phase, y, best["metres"], False, source, url, rank, best["label"], method, bands, note)
        elif table:
            best = max(table, key=lambda f: f["metres"])
            bands = [{"label": f["label"], "metres": f["metres"]} for f in table] if len(table) > 1 else None
            if bands:
                F.band_ranges[urn][int(y)] = (min(f["metres"] for f in table), best["metres"])
            F.add(urn, phase, y, best["metres"], False, source, url, rank, best["label"], method, bands, note)
        elif alls:
            F.add(urn, phase, y, None, True, source, url, rank, alls[0]["label"], method)


def split_sections(lines, F, borough, phase):
    """Split a multi-school document into (urn, lines) sections, using lines
    that are a school's name as the start of each section."""
    heads = []
    for i, line in enumerate(lines):
        if len(line) > 90 or re.search(r"\d{3,}", line):
            continue
        urn = F.schools.match(line, borough, phase, allow_fuzzy=False)
        if urn:
            heads.append((i, urn))
    sections = []
    for k, (i, urn) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else min(len(lines), i + 80)
        sections.append((urn, lines[i + 1:end], i + 1))
    return sections


def read_table_rows(tables, lines, F, borough, phase):
    """For documents laid out as one row per school (name, then numbers).

    Reads the tables pdfplumber found and also plain text lines that start with a
    school name, because PDF tables sometimes lose rows at a page break (Lambeth's
    community-school PDFs drop Jessop that way).
    Returns {urn: (metres or None, all_offered, row_text, bands or None)}. bands is
    set when one school has rows for two sites, e.g. Henry Cavendish (Balham site)
    and (Streatham site); metres is then the larger figure."""
    rows = []
    for t in tables or []:
        for row in t:
            cells = [clean_cell(c) for c in row if c is not None and clean_cell(c)]
            if len(cells) >= 2:
                rows.append(cells)
    for line in lines:
        m = re.match(r"([A-Za-z][^\d]*?)\s+(\d.*)$", line)
        # A real row has at least two numbers after the name (PAN, applications, ...).
        if m and len(re.findall(r"\d+", m.group(2))) >= 2:
            rows.append([m.group(1)] + m.group(2).split())
    per_urn = defaultdict(dict)     # urn -> {name as written: (metres, all_offered, text)}
    for cells in rows:
        urn = F.schools.match(cells[0], borough, phase)
        if not urn:
            continue
        site = cells[0].lower().strip()
        if site in per_urn[urn]:
            continue                 # same row seen in both the table and the text
        text = " ".join(cells[1:])
        if ALL_OFFERED.search(text):
            per_urn[urn][site] = (None, True, " | ".join(cells)[:160])
            continue
        dec = [to_float(c) for c in cells[1:] if re.fullmatch(r"\d{1,3}(?:,\d{3})*\.\d{1,3}|\d+\.\d{1,3}", c)]
        dec = [d for d in dec if plausible(d)]
        if len(dec) == 1:
            per_urn[urn][site] = (dec[0], False, " | ".join(cells)[:160])
    out = {}
    for urn, sites in per_urn.items():
        vals = list(sites.values())
        if not vals:
            continue
        if len(vals) == 1:
            out[urn] = vals[0] + (None,)
            continue
        ms = [v[0] for v in vals if v[0] is not None]
        bands = [{"label": v[2], "metres": v[0], "allApplicantsOffered": v[1]} for v in vals]
        out[urn] = (max(ms) if ms else None, not ms, " / ".join(v[2] for v in vals)[:300], bands)
        F.notes[urn].add("Admits separately to more than one site; the cut-off distance differs by site")
    return out


# =====================================================================
# 6. Lewisham
# =====================================================================

LEW = "https://lewisham.gov.uk/myservices/education/schools/school-admission/"
LEW_SEC_PAGE = LEW + "applying-to-start-secondary-school/last-year-s-number-of-applications-and-appeals-for-secondary-school-places"
LEW_PRI_PAGE = LEW + "applying-to-start-primary-school/last-year-s-number-of-application-and-appeals-for-primary-school-places"
LEW_SEC_MAP = LEW + "applying-to-start-secondary-school/school-admission-distance-map"
LEW_PRI_MAP = LEW + "applying-to-start-primary-school/primary-school-admission-distance-map"
OFFER_DAY = {"secondary": (3, 1), "primary": (4, 16)}   # (month, day): roughly when offers are made


def snapshot_date(ts):
    return datetime.date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))


def feasible(year, phase, seen):
    """A figure for intake YEAR can't appear on a page before that year's offer day."""
    m, d = OFFER_DAY[phase]
    return datetime.date(year, m, d) <= seen


def snapshots(fetch, url):
    """All archived copies (about one a month), plus the live page, oldest first."""
    rows = fetch.cdx(url.replace("https://", ""), "exact", "timestamp:6")
    out = [(ts, f"https://web.archive.org/web/{ts}id_/{url}") for ts, _ in rows]
    out.append((datetime.date.today().strftime("%Y%m%d"), url))
    return out


def lewisham_secondary_pages(fetch, F):
    """The secondary 'applications and appeals' page lists each community school's
    'Home-to-school distance of last place offered'. We read every archived version.
    A version keeps its figures until the council updates it, so we date each
    distinct set of figures carefully (see feasible())."""
    log("Lewisham secondary: applications and appeals page (live + archive)")
    prev_sig, prev_year = None, 0
    dated = {}          # urn -> {year: metres}, used later to date the map
    for ts, url in snapshots(fetch, LEW_SEC_PAGE):
        data = fetch.get(url)
        lines, _ = to_lines(data)
        if not lines:
            continue
        text = " ".join(lines)
        sections = split_sections(lines, F, "Lewisham", "secondary")
        results = {}
        for urn, sec, start in sections:
            fs = read_section(sec)
            if fs:
                results[urn] = fs
        sig = json.dumps({u: [(f["kind"], f["metres"]) for f in fs] for u, fs in sorted(results.items())})
        if not results or sig == prev_sig:
            continue                                  # nothing new since the last version
        seen = snapshot_date(ts)
        stated = {int(y) for y in re.findall(r"(?:numbers for|allocated[^.]{0,30}?|places on \d{1,2} \w+)\s(20\d\d)", text)}
        stated = {y for y in stated if feasible(y, "secondary", seen)}
        year = max(stated) if stated else None
        if not year or year <= prev_year:
            log(f"  {ts}: figures changed but the year is unclear (stated {sorted(stated)}); skipped")
            prev_sig = sig
            continue
        source = f"Lewisham Council: last year's applications and appeals for secondary school places ({year})"
        for urn, fs in results.items():
            record_section(F, urn, "secondary", fs, [], year, source, url, 1)
            for f in fs:
                if f["kind"] in ("labelled", "table"):
                    dated.setdefault(urn, {})[year] = f["metres"]
        log(f"  {ts}: {len(results)} schools, year {year}")
        prev_sig, prev_year = sig, year
    return dated


def lewisham_primary_grids(fetch, F):
    """Primary figures are in a PDF ('reception grid') linked from the primary
    'applications and appeals' page. Each year's PDF has a different name, so we
    collect links from every archived version of the page and read each PDF once."""
    log("Lewisham primary: reception grid PDFs (live + archive)")
    pdf_links = {}
    for ts, url in snapshots(fetch, LEW_PRI_PAGE):
        data = fetch.get(url)
        if not data:
            continue
        for href in re.findall(r'href="([^"]+?(?:\.pdf|\.ashx)[^"]*)"', data.decode("utf-8", "replace"), re.I):
            href = re.sub(r"^/web/\d+(id_)?/", "", href)
            full = urljoin("https://lewisham.gov.uk/", href.split("?")[0])
            pdf_links.setdefault(full, ts)
    dated = {}
    for link, ts in pdf_links.items():
        data, used = live_or_archived(fetch, link)
        if not data or data[:4] != b"%PDF":
            data = fetch.wayback(link, ts)
            used = f"https://web.archive.org/web/{ts}/{link}"
        lines, tables = to_lines(data)
        if not lines:
            log(f"  could not read {link}")
            continue
        year = doc_year(lines)
        if not year:
            log(f"  no year found in {link}; skipped")
            continue
        rows = read_table_rows(tables, lines, F, "Lewisham", "primary")
        source = f"Lewisham Council: reception applications and offers grid ({year})"
        for urn, (metres, all_off, text, bands) in rows.items():
            F.add(urn, "primary", year, metres, all_off, source, used, 1, text, "straight", bands)
            if metres:
                dated.setdefault(urn, {})[year] = metres
        log(f"  {link.rsplit('/', 1)[-1]}: year {year}, {len(rows)} schools")
    return dated


def lewisham_maps(fetch, F, dated, map_url, phase):
    """The distance maps include academies and faith schools that the tables
    leave out. But the maps don't say which year they show, and the primary map
    was not updated every year. So a map version is used only if at least one of
    its community-school figures exactly matches a dated council figure, and all
    such matches agree on the year."""
    log(f"Lewisham {phase}: distance map (live + archive)")
    prev_sig = None
    shown = {}          # urn -> (metres, year) last recorded from the map
    for ts, url in snapshots(fetch, map_url):
        data = fetch.get(url)
        if not data:
            continue
        m = re.search(r"maps\['[^']+'\] = (\{.*?\});\s*\n", data.decode("utf-8", "replace"), re.S)
        if not m:
            continue
        try:
            items = [it for layer in json.loads(m.group(1))["layers"] for it in layer["items"]]
        except (ValueError, KeyError):
            continue
        figures = {}
        for it in items:
            title = clean_cell(it.get("title", ""))
            if "special school" in title.lower():
                continue
            ph = "primary" if "(primary" in title.lower() else "secondary" if "(secondary" in title.lower() else phase
            if ph != phase:
                continue
            urn = F.schools.match(title, "Lewisham", phase)
            if not urn:
                if it.get("admissionDistance") is not None:
                    F.unmatched["Lewisham map"].add(title)
                continue
            if it.get("admissionDistance") is not None or it.get("admissionAllPlaces"):
                figures[urn] = (it.get("admissionDistance"), bool(it.get("admissionAllPlaces")), title)
        sig = json.dumps(sorted((k, v[0], v[1]) for k, v in figures.items()))
        if not figures or sig == prev_sig:
            continue
        prev_sig = sig
        years = {y for urn, (metres, _, _) in figures.items() for y, v in dated.get(urn, {}).items()
                 if metres is not None and abs(v - metres) < 0.01}
        if len(years) != 1:
            log(f"  map version {ts}: cannot date it (matches {sorted(years) or 'no dated figures'}); not used")
            continue
        year = years.pop()
        source = f"Lewisham Council: {phase} school admission distance map ({year}, dated by matching council figures)"
        added, stale = 0, 0
        for urn, (metres, all_off, title) in figures.items():
            # The council doesn't update every school on the map each year. A figure identical
            # to the one an earlier version showed for an earlier year was carried over, so it
            # is not evidence for this year (e.g. Prendergast Vale 14055.37 on the 2025 and 2026 maps).
            before = shown.get(urn)
            if metres is not None and before and before[0] == metres and before[1] < year:
                stale += 1
                continue
            shown[urn] = (metres, year)
            # When the map says all applicants were offered, its distance is just the furthest
            # child admitted, not a cut-off; the brief stores that case as metres = null.
            F.add(urn, phase, year, None if all_off else metres, all_off, source, url, 2,
                  f"{title}: {metres} m" + (" (all applicants offered)" if all_off else ""), "straight")
            added += 1
        log(f"  map version {ts}: year {year}, {added} schools"
            + (f" ({stale} unchanged from an earlier year, not counted)" if stale else ""))


# =====================================================================
# 7. Lambeth
# =====================================================================

LAM = "https://www.lambeth.gov.uk/schools-and-education/school-admissions-and-appeals/"


def lambeth_listing_pages(fetch):
    """Every 'How offers were made' page: live ones linked from the admissions
    pages, plus older ones the archive knows about."""
    pages = set()
    for idx in ["primary-school-admissions", "secondary-school-admissions"]:
        data = fetch.get(LAM + idx)
        if data:
            for href in re.findall(r'href="([^"]*how-offers-were-made[^"]*)"', data.decode("utf-8", "replace")):
                pages.add(urljoin(LAM, href))
    for ts, orig in fetch.cdx("www.lambeth.gov.uk/schools-and-education/school-admissions-and-appeals/",
                              "prefix", "urlkey"):
        if "how-offers-were-made" in orig and "?" not in orig:
            pages.add(re.sub(r"^http://", "https://", orig.replace(":443", "")))
    return sorted(pages)


def lambeth(fetch, F):
    log("Lambeth: 'How offers were made' documents (live + archive)")
    pages = lambeth_listing_pages(fetch)
    docs = {}       # document URL -> phase
    for page in pages:
        phase = "primary" if "primary" in page else "secondary" if "secondary" in page else None
        if not phase:
            continue
        versions = [(None, page)] + [(ts, f"https://web.archive.org/web/{ts}id_/{page}")
                                     for ts, _ in fetch.cdx(page.replace("https://", ""), "exact", "timestamp:4")]
        for ts, url in versions:
            data = fetch.get(url)
            if not data:
                continue
            html = data.decode("utf-8", "replace")
            links = re.findall(r'href="([^"]+\.pdf)"', html, re.I)
            # 2025 primary was published as web sub-pages instead of PDFs.
            links += [h for h in re.findall(r'href="([^"]+)"', html)
                      if "how-offers-were-made" in h and h.rstrip("/").split("/")[-1].startswith("lambeth-")]
            for href in links:
                href = re.sub(r"^(https?://web\.archive\.org)?/web/\d+(id_)?/", "", href)
                full = urljoin("https://www.lambeth.gov.uk/", href).replace(":443", "").replace("http://", "https://")
                docs.setdefault(full, phase)
    log(f"  {len(pages)} listing pages, {len(docs)} documents")
    for url, phase in sorted(docs.items()):
        data, used = live_or_archived(fetch, url)
        lines, tables = to_lines(data)
        if not lines:
            log(f"  could not read {url}")
            continue
        year = doc_year(lines)
        if not year:
            # The 2025 primary results are web pages whose text never states the year;
            # it is only in the page address ("...national-offer-day-16-april-2025/...").
            m = re.search(r"national-offer-day-\d{1,2}-[a-z]+-(20\d\d)/", url)
            if m:
                year = int(m.group(1))
                log(f"  {url.rsplit('/', 1)[-1]}: year {year} taken from the page address (the text has none)")
        if not year:
            log(f"  no year found in {url.rsplit('/', 1)[-1]}; skipped")
            continue
        source = f"Lambeth Council: how offers were made on National Offer Day ({phase}, {year})"
        marks = year_markers(lines)
        found = 0
        # One-row-per-school tables (community schools).
        rows = read_table_rows(tables, lines, F, "Lambeth", phase)
        for urn, (metres, all_off, text, bands) in rows.items():
            F.add(urn, phase, year, metres, all_off, source, used, 1, text, "straight", bands)
            found += 1
        # Section-per-school documents (secondary PDFs, academies, VA, foundation).
        sections = split_sections(lines, F, "Lambeth", phase)
        if not sections and phase == "secondary":
            # A one-school PDF: the school's name is usually in the first few lines.
            for line in lines[:6]:
                urn = F.schools.match(line, "Lambeth", phase)
                if urn:
                    sections = [(urn, lines, 0)]
                    break
        if not sections and phase == "secondary":
            # A few PDFs never name the school (e.g. ElmgreenNOD2022.pdf). Fall back to the
            # file name, but only when exactly one school's distinctive word is in it.
            fname = re.sub(r"[^a-z]", "", unquote(url.rsplit("/", 1)[-1]).lower())
            hits = {s["urn"] for s in F.schools.all if s["laName"] == "Lambeth" and phase in s["phases"]
                    for w in norm(s["name"]).split() if len(w) >= 6 and w in fname}
            if len(hits) == 1:
                sections = [(hits.pop(), lines, 0)]
        for urn, sec, start in sections:
            if urn in rows:
                continue
            fs = read_section(sec)
            for f in fs:
                f["i"] += start
            if fs:
                record_section(F, urn, phase, fs, marks, year, source, used, 1)
                found += 1
                text = " ".join(sec)
                if re.search(r"\bband", text, re.I) and re.search(r"ability|test", text, re.I):
                    F.notes[urn].add("Ability banding (a test sorts applicants into bands)")
                if re.search(r"walking", text, re.I):
                    F.method_evidence[urn].append(("walking", used))
        log(f"  {phase} {year}: {found} schools from {url.rsplit('/', 1)[-1][:70]}")


# =====================================================================
# 8. Southwark
# =====================================================================

def southwark_documents(fetch):
    """Southwark's community primary allocation tables: the current files from
    the live admissions page, plus older brochures and tables the archive holds."""
    docs = set()
    page = fetch.get("https://www.southwark.gov.uk/schools-and-education/school-admissions/primary-admissions/admissions-criteria-primary-schools")
    if page:
        for href in re.findall(r'href="([^"]+\.(?:pdf|xlsx))"', page.decode("utf-8", "replace"), re.I):
            if re.search(r"allocation|brochure", href, re.I):
                docs.add(urljoin("https://www.southwark.gov.uk/", href))
    pattern = re.compile(r"(primary[^/]*brochure|starting-primary|applying-for-primary|allocation-of-community|"
                         r"places-by-criteria)", re.I)
    for prefix in ["www.southwark.gov.uk/assets/attach/", "services.southwark.gov.uk/assets/attach/",
                   "www.southwark.gov.uk/sites/default/files/"]:
        for ts, orig in fetch.cdx(prefix, "prefix", "urlkey"):
            if pattern.search(orig) and "?" not in orig and re.search(r"\.(pdf|xlsx)$", orig, re.I):
                docs.add(orig.replace("http://", "https://").replace(":443", ""))
    return sorted(docs)


def southwark(fetch, F):
    """Rows look like: 'Alfred Salter 60 177 0 0 0 22 0 0 38 1277 0 N/A'. The
    furthest distance is the 10th number. The columns are PAN, applications, outside
    age group, SEN, LAC, siblings, medical/social, staff, distance (count), furthest
    distance, appeals heard, successful appeals."""
    log("Southwark: community primary allocation tables (live + archive)")
    for url in southwark_documents(fetch):
        def readable(data):
            """Text lines, or None for a broken file or an error page instead of the PDF."""
            if url.lower().endswith(".pdf") and (data or b"")[:4] != b"%PDF":
                return None
            return to_lines(data)[0]
        data, used = live_or_archived(fetch, url)
        lines = readable(data)
        if not lines:
            # The archive's newest copy can be cut short; older captures are sometimes whole.
            for ts, _ in fetch.cdx(url.replace("https://", ""), "exact", "digest")[-3:]:
                data = fetch.wayback(url, ts)
                lines = readable(data)
                if lines:
                    used = f"https://web.archive.org/web/{ts}/{url}"
                    break
        if not lines:
            log(f"  could not read {url.rsplit('/', 1)[-1]} (missing or a damaged archive copy)")
            continue
        # Find the table heading, which also states the year. It can wrap onto a second
        # line, so each line is read together with the next. (The brochure's contents page
        # says "(September 2020)" in brackets for the 2021 table, so brackets don't count.)
        start, year = None, None
        for i, line in enumerate(lines):
            pair = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            m = re.search(r"allocated at\s+Southwark community schools for September (20\d\d)|"
                          r"Sept(?:ember)?[- ](20\d\d)[- ]Allocation", pair, re.I)
            if m:
                start, year = i, int(m.group(1) or m.group(2))
                break
        if year is None:
            m = re.search(r"Sept(?:ember)?-?(20\d\d)", url) or re.search(r"(20\d\d)\.(?:pdf|xlsx)$", url)
            if m and any(re.search(r"furthest", l, re.I) for l in lines):
                year = int(m.group(1))
                start = 0
        if year is None:
            log(f"  {url.rsplit('/', 1)[-1]}: no allocation table with a year found")
            continue
        header = " ".join(lines[start:start + 8])
        if not re.search(r"furthest", header, re.I):
            # Some brochures carry the table on a later page; look for the header anywhere.
            idx = [i for i, l in enumerate(lines) if re.search(r"furthest", l, re.I)]
            if not idx:
                log(f"  {url.rsplit('/', 1)[-1]}: no 'furthest distance' table found")
                continue
            start = idx[0]
        source = f"Southwark Council: allocation of community primary school places by criteria (September {year})"
        n = 0
        for line in lines[start:start + 80]:
            m = re.match(r"([A-Za-z][A-Za-z' .&-]+?)\s+(\d+\*?)\s+(.*)$", line)
            if not m:
                continue
            urn = F.schools.match(m.group(1), "Southwark", "primary")
            if not urn:
                continue
            rest = m.group(3)
            if ALL_OFFERED.search(rest):
                F.add(urn, "primary", year, None, True, source, used, 1, line, "straight")
                n += 1
                continue
            nums = re.findall(r"\d+", rest.replace("*", ""))
            # nums starts at 'applications' (PAN was captured separately), so furthest is nums[8].
            if len(nums) >= 10:
                furthest, count, pan = int(nums[8]), int(nums[7]), int(m.group(2).rstrip("*"))
                if plausible(furthest) and count <= pan:
                    F.add(urn, "primary", year, float(furthest), False, source, used, 1, line, "straight")
                    n += 1
        log(f"  September {year}: {n} schools from {url.rsplit('/', 1)[-1]}")


# =====================================================================
# 9. School websites (own-admission-authority schools with nothing yet)
# =====================================================================

SITE_DISTANCE = re.compile(
    r"(?:furthest|last|final|maximum)\s+(?:distance|child|pupil|applicant|place|offer)[^.\n]{0,120}?"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(km|kilometres|metres|meters|m|miles?)\b", re.I)
NOTE_PATTERNS = [
    (r"random allocation|lottery|ballot", "Partial lottery (random allocation) for some places"),
    (r"fair banding|ability band|banding test|banding assessment", "Banded admissions"),
    (r"aptitude", "Some places by aptitude (e.g. music)"),
    (r"nodal point", "Distance measured from nodal point(s), not the school gate"),
    (r"priority (?:admission )?area|catchment area|inner (?:area|zone)|feeder", "Priority area or feeder schools mentioned"),
]


YEAR_IN = re.compile(r"\b(20[12]\d)(?:\s*[/-]\s*\d{2,4})?\b")
UNIT_NUM = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(km|kilometres|metres|meters|m|miles?)\b", re.I)


def to_metres(value, unit):
    unit = unit.lower()
    return value * 1000 if unit.startswith("k") else value * 1609.344 if unit.startswith("mile") else value


def site_list_figures(lines):
    """Figures laid out as lists or tables rather than sentences. Returns [(year, metres, evidence, context)].

    Layout 1, a heading then dated lines (The Belham, Prendergast Ladywell):
        Distance of the furthest address offered a place ... in previous years:
        September 2025: 430 meters            or    Entry in September 2026
                                                    1158.4 metres
    Layout 2, table cells with no unit, dated by a heading above (All Saints' Blackheath):
        Admissions data for pupils starting Reception in 2024 ... Furthest distance offered = 556.41
    """
    out = []
    for i, line in enumerate(lines):
        if not re.search(r"furthest|last (?:child|place|offer|distance)", line, re.I):
            continue
        # Layout 2: "Furthest distance offered = 556.41" (unit optional).
        m = re.search(r"furthest distance (?:offered|admitted)\s*[=:]\s*(\d+(?:\.\d+)?)\s*(metres|meters|mtrs|m)?\b", line, re.I)
        if m:
            for j in range(i - 1, max(-1, i - 80), -1):
                h = re.search(r"(?:starting|entry|intake|admissions? data|admitted|offers?)\b[^\n]{0,40}?\b(20[12]\d)\b", lines[j], re.I)
                if h:
                    out.append((int(h.group(1)), float(m.group(1)), line[:160], lines[j] + " " + line))
                    break
            continue
        # Layout 1: a heading line, then up to 25 lines of year + figure.
        if UNIT_NUM.search(line):
            continue                     # a sentence with its own figure is read by SITE_DISTANCE
        year, year_line = None, -9
        for j in range(i + 1, min(len(lines), i + 26)):
            ys = YEAR_IN.findall(lines[j])
            if ys:
                year, year_line = int(ys[-1]), j
            nm = UNIT_NUM.search(lines[j])
            # A list item is short; a long line is a sentence (e.g. a priority-area radius).
            if nm and year and j - year_line <= 1 and len(lines[j]) <= 60:
                out.append((year, to_metres(to_float(nm.group(1)), nm.group(2)),
                            (lines[year_line] + " " + lines[j] if year_line != j else lines[j])[:160],
                            line + " " + lines[j]))
                year = None              # each year line is used once
    return out


def website_search(fetch, F, school):
    """Look for published cut-off distances on the school's own website.
    Strict on purpose: a figure counts only if a sentence names a furthest/last
    distance with a unit (metres/km/miles) and a year appears within it or just before."""
    urn, phase, url = school["urn"], school["phases"], school.get("websiteUrl")
    if not url:
        return
    deadline = time.time() + WEBSITE_SECONDS
    budget = [WEBSITE_MAX_REQUESTS]
    host = urlparse(url).netloc.replace("www.", "")

    def get(u):
        if budget[0] <= 0 or time.time() > deadline:
            return None
        budget[0] -= 1
        F.tried[urn].append(u)
        return fetch.get(u, timeout=30)

    home = get(url)
    if not home:
        return
    queue, seen = [], set()

    def links(data, base):
        html = data.decode("utf-8", "replace")
        out = []
        for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
            full = urljoin(base, href.strip())
            if host not in urlparse(full).netloc:
                continue
            label = clean_cell(label).lower()
            score = sum(w in (full.lower() + " " + label) for w in
                        ["admission", "allocation", "offer", "distance", "arrangement", "policy", "oversubscription"])
            if score and full not in seen:
                out.append((score + (2 if full.lower().endswith(".pdf") else 0), full))
        return sorted(out, reverse=True)

    queue += links(home, url)
    texts = []
    while queue and budget[0] > 0 and time.time() < deadline:
        _, u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        data = get(u)
        if not data:
            continue
        lines, _ = to_lines(data)
        if lines:
            texts.append((u, "\n".join(lines), lines))
        if data[:4] != b"%PDF" and len(seen) < 6:
            queue += links(data, u)
            queue.sort(reverse=True)

    for u, text, lines in texts:
        # Lists and tables: group by year; several figures in one year (e.g. foundation and
        # open places) are kept as bands, and metres is the furthest.
        per_year = defaultdict(list)
        for year, metres, evidence, context in site_list_figures(lines):
            if plausible(metres) and year <= datetime.date.today().year:
                per_year[year].append((round(metres, 1), evidence, context))
        for year, figs in per_year.items():
            phases = phase
            if len(phase) > 1:
                near = (u + " " + " ".join(c for _, _, c in figs)).lower()
                phases = (["primary"] if "reception" in near else []) + \
                         (["secondary"] if re.search(r"year.?7|secondary", near) else [])
                if len(phases) != 1:
                    continue
            figs = list({f[0]: f for f in figs}.values())          # drop exact repeats
            best = max(figs, key=lambda f: f[0])
            bands = [{"label": e, "metres": m} for m, e, _ in figs] if len(figs) > 1 else None
            for ph in phases:
                F.add(urn, ph, year, best[0], False, f"School website: {school['name']}", u, 3,
                      best[1], method_from(" ".join(c for _, _, c in figs)), bands)
        for m in SITE_DISTANCE.finditer(text):
            context = text[max(0, m.start() - 300): m.end() + 60]
            # Only years written before (or inside) the figure's sentence; "2025/26" counts as 2025.
            years = re.findall(r"\b(20[12]\d)(?:\s*[/-]\s*\d{2,4})?\b", text[max(0, m.start() - 300): m.end()])
            if not years:
                continue
            if re.search(r"transport|travel assistance|bus pass|statutory walking", context, re.I):
                continue                   # "maximum distance of 3 miles" for free transport, not a cut-off
            value, unit = to_float(m.group(1)), m.group(2).lower()
            metres = value * 1000 if unit.startswith("k") else value * 1609.344 if unit.startswith("mile") else value
            if not plausible(metres):
                continue
            year = int(years[-1])          # the nearest year before the figure
            if year > datetime.date.today().year:
                continue                   # e.g. a "2027/28 policy" heading: no offers made yet, so not a result
            phases = phase
            if len(phase) > 1:
                # All-through school: the figure must say which entry point it is about.
                near = context.lower()
                phases = (["primary"] if "reception" in near else []) + \
                         (["secondary"] if re.search(r"year 7|secondary", near) else [])
                if len(phases) != 1:
                    continue
            for ph in phases:
                F.add(urn, ph, year, round(metres, 1), False, f"School website: {school['name']}", u, 3,
                      clean_cell(text[m.start(): m.end()])[:160], method_from(context))
        for pat, note in NOTE_PATTERNS:
            mm = re.search(pat, text, re.I)
            if mm and not re.search(r"\b(no|not|does not use|without)\b[^.]{0,30}$", text[max(0, mm.start() - 40): mm.start()], re.I):
                F.notes[urn].add(note + " (from school website; check)")
        if re.search(r"walking (?:route|distance)", text, re.I):
            F.method_evidence[urn].append(("walking", u))


# =====================================================================
# 10. Putting it together
# =====================================================================

def merge(F, gias):
    """One entry per school: per phase, one figure per year (best source wins), newest first."""
    out = {}
    by_key = defaultdict(list)
    for o in F.obs:
        by_key[(o["urn"], o["phase"], o["year"])].append(o)
    for s in gias:
        if s["isIndependent"]:
            continue
        urn = s["urn"]
        entry = {"urn": urn, "name": s["name"], "laName": s["laName"], "type": s["type"],
                 "phases": {}, "admissionsNote": None, "priorityArea": None}
        for ph in s["phases"]:
            years = sorted({k[2] for k in by_key if k[0] == urn and k[1] == ph}, reverse=True)
            dist = []
            for y in years:
                obs = sorted(by_key[(urn, ph, y)], key=lambda o: o["rank"])
                best = obs[0]
                rec = {"year": y, "metres": best["metres"], "allApplicantsOffered": best["allApplicantsOffered"],
                       "source": best["source"], "sourceUrl": best["sourceUrl"], "evidence": best["evidence"]}
                if best["bands"]:
                    rec["bands"] = best["bands"]
                if best["note"]:
                    rec["note"] = best["note"]
                # Did another source give a different figure for the same year?
                others = {(o["metres"], o["allApplicantsOffered"]) for o in obs[1:]
                          if (o["metres"], o["allApplicantsOffered"]) != (best["metres"], best["allApplicantsOffered"])}
                if others:
                    rec["conflicts"] = [{"metres": o["metres"], "allApplicantsOffered": o["allApplicantsOffered"],
                                         "source": o["source"], "sourceUrl": o["sourceUrl"]} for o in obs[1:]
                                        if (o["metres"], o["allApplicantsOffered"]) in others]
                dist.append(rec)
            # Lambeth's 2026 PDF repeats some 2025 figures exactly (Dunraven primary 2492.08 m, with
            # the same 243 applications). We keep what the source says but flag it for checking.
            for newer, older in zip(dist, dist[1:]):
                if newer["metres"] is not None and newer["metres"] == older["metres"] \
                        and int(newer["year"]) == int(older["year"]) + 1:
                    newer["note"] = ((newer.get("note") + "; ") if newer.get("note") else "") + \
                        f"identical to the {older['year']} figure in the source; possibly not updated by the publisher"
            # Method: what the newest labelled figure says ("2492.08 metres (straight line)") wins;
            # otherwise any mention of a walking route in the school's documents; otherwise unknown.
            stated = [o for o in sorted(F.obs, key=lambda o: o["year"], reverse=True)
                      if o["urn"] == urn and o["phase"] == ph and o["method"]]
            walking_mentioned = any(m == "walking" for m, _ in F.method_evidence.get(urn, []))
            method = stated[0]["method"] if stated else "walking" if walking_mentioned else "unknown"
            confirmed = bool(dist)
            entry["phases"][ph] = {
                "distances": dist,
                "distanceMethod": method,
                "distanceConfidence": "CONFIRMED" if confirmed else "ESTIMATED",
                "estimatedMetres": None if confirmed else ESTIMATE_METRES[ph],
                "lastTriedUrl": (F.tried[urn][-1] if F.tried.get(urn) else None),
            }
        notes = sorted(F.notes.get(urn, []))
        if F.band_ranges.get(urn):
            y = max(F.band_ranges[urn])
            lo, hi = F.band_ranges[urn][y]
            notes.append(f"Banded or multi-group admissions: the cut-off distance differs by band "
                         f"(in {y}: {lo:.0f}–{hi:.0f} m; distances[].metres is the furthest band)")
        entry["admissionsNote"] = "; ".join(notes) if notes else None
        out[str(urn)] = entry
    return out


def main():
    gias = json.loads(GIAS_FILE.read_text(encoding="utf-8"))["schools"]
    schools = Schools(gias)
    F = Findings(schools)
    fetch = Fetcher()
    log(f"{len(schools.all)} state schools to cover")

    # 1. Lewisham
    dated_sec = lewisham_secondary_pages(fetch, F)
    dated_pri = lewisham_primary_grids(fetch, F)
    lewisham_maps(fetch, F, dated_sec, LEW_SEC_MAP, "secondary")
    lewisham_maps(fetch, F, dated_pri, LEW_PRI_MAP, "primary")
    # 2. Lambeth
    lambeth(fetch, F)
    # 3. Southwark
    southwark(fetch, F)

    # 4. School websites, for own-admission-authority schools still without any figure.
    have = {(o["urn"], o["phase"]) for o in F.obs}
    todo = [s for s in schools.all
            if any((s["urn"], ph) not in have for ph in s["phases"])
            and any(t.lower() in (s["type"] or "").lower() for t in OWN_AUTHORITY_TYPES)]
    log(f"School websites: {len(todo)} own-admission-authority schools still have no figure")
    if "--councils-only" in sys.argv:          # for testing: skip the slow website step
        log("  skipped (--councils-only)")
        todo = []
    for k, s in enumerate(todo, 1):
        before = len(F.obs)
        try:
            website_search(fetch, F, s)
        except Exception as e:                       # one broken website must not stop the run
            log(f"  {s['name']}: error {type(e).__name__}: {str(e)[:80]}")
        got = len(F.obs) - before
        log(f"  [{k}/{len(todo)}] {s['name']} ({s['laName']}): "
            f"{got} figure(s) from {len(F.tried.get(s['urn'], []))} pages")

    # Record the council URL tried for schools that never reached the website step.
    council_page = {"Lewisham": {"primary": LEW_PRI_PAGE, "secondary": LEW_SEC_PAGE},
                    "Lambeth": {"primary": LAM + "primary-school-admissions", "secondary": LAM + "secondary-school-admissions"},
                    "Southwark": {"primary": "https://www.southwark.gov.uk/schools-and-education/school-admissions/primary-admissions/admissions-criteria-primary-schools",
                                  "secondary": "https://www.southwark.gov.uk/schools-and-education/school-admissions/secondary-admissions"}}
    for s in schools.all:
        if not F.tried.get(s["urn"]):
            F.tried[s["urn"]].append(council_page[s["laName"]][s["phases"][0]])

    result = merge(F, gias)
    matches = [{"borough": b, "nameInDocument": raw, "phase": ph, "urn": urn, "method": how}
               for (b, raw, ph), (urn, how) in schools.match_log.items() if urn and how != "exact"]
    OUTPUT.write_text(json.dumps({
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "yearMeaning": "Intake year: places offered in spring of that year for September entry.",
        "estimateDefaults": ESTIMATE_METRES,
        "nonExactNameMatches": matches,
        "schools": result,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    summary(result, fetch)
    log(f"Saved {OUTPUT.relative_to(PROJECT)}")


def summary(result, fetch):
    rows = [(e["laName"], ph, p) for e in result.values() for ph, p in e["phases"].items()]
    print("\n=== CONFIRMED vs ESTIMATED (one row per school phase; all-through schools count twice) ===")
    print(f"{'Borough':<10} {'Phase':<10} {'CONFIRMED':>9} {'ESTIMATED':>9}")
    for b in ["Lambeth", "Lewisham", "Southwark"]:
        for ph in ["primary", "secondary"]:
            c = sum(1 for r in rows if r[0] == b and r[1] == ph and r[2]["distanceConfidence"] == "CONFIRMED")
            e = sum(1 for r in rows if r[0] == b and r[1] == ph and r[2]["distanceConfidence"] == "ESTIMATED")
            print(f"{b:<10} {ph:<10} {c:>9} {e:>9}")
    tc = sum(r[2]["distanceConfidence"] == "CONFIRMED" for r in rows)
    print(f"{'Total':<21} {tc:>9} {len(rows) - tc:>9}")

    print("\n=== Years of data per CONFIRMED school phase ===")
    counts = defaultdict(int)
    for r in rows:
        n = len(r[2]["distances"])
        if n:
            counts["3+" if n >= 3 else str(n)] += 1
    for k in ["1", "2", "3+"]:
        print(f"  {k} year(s): {counts[k]}")
    all_years = sorted({d["year"] for r in rows for d in r[2]["distances"]})
    print(f"  Years covered: {', '.join(all_years)}")
    n_all = sum(1 for r in rows for d in r[2]["distances"] if d["allApplicantsOffered"])
    n_m = sum(1 for r in rows for d in r[2]["distances"] if d["metres"] is not None and not d["allApplicantsOffered"])
    print(f"  Figures: {n_m} distances, {n_all} 'all applicants offered'")

    print("\n=== ESTIMATED schools (no figure found) and the last URL tried ===")
    for e in sorted(result.values(), key=lambda e: (e["laName"], e["name"])):
        for ph, p in e["phases"].items():
            if p["distanceConfidence"] == "ESTIMATED":
                print(f"  {e['laName']:<9} {ph:<9} {e['name']} (URN {e['urn']}, {e['type']})\n"
                      f"            last tried: {p['lastTriedUrl']}")
    print(f"\nRequests made this run: {fetch.requests}")


if __name__ == "__main__":
    main()
