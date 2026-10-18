"""
Step 1: download the school register from GIAS (Get Information About Schools).

What this script does, in order:
  1. Gets the full GIAS "Establishment fields CSV" (every school in England).
     It downloads it automatically. If that fails, it tells you how to download
     it by hand, and you can pass the file in with --file.
  2. Keeps only open schools in Lambeth (208), Lewisham (209) and Southwark (210)
     that are primary, secondary, all-through or middle schools, plus
     mainstream independent schools.
  3. Converts each school's grid reference (easting/northing, the British
     National Grid used by Ordnance Survey) to latitude/longitude (what web maps use).
  4. Spot-checks three schools against postcodes.io.
  5. Saves data/source-snapshots/gias-cleaned.json and prints a summary.

Run from the main project folder:
    python3 scripts/01_fetch_gias.py
    python3 scripts/01_fetch_gias.py --file ~/Downloads/edubasealldata20261017.csv
"""

import argparse
import datetime
import io
import json
import math
import re
import sys
import time
import warnings
import zipfile
from pathlib import Path

import pandas as pd
import requests
from pyproj.transformer import TransformerGroup

# ---------- settings ----------

PROJECT = Path(__file__).resolve().parent.parent   # the main project folder
SNAPSHOTS = PROJECT / "data" / "source-snapshots"
RAW_DIR = SNAPSHOTS / "raw"                        # big raw downloads; ignored by git
OUTPUT = SNAPSHOTS / "gias-cleaned.json"

GIAS = "https://get-information-schools.service.gov.uk"
LA_CODES = {"208": "Lambeth", "209": "Lewisham", "210": "Southwark"}

# GIAS phase names we keep, and which of our phases each one counts as.
# All-through schools take children at both Reception and Year 7, so they get both.
PHASES = {
    "Primary": ["primary"],
    "Middle deemed primary": ["primary"],
    "Secondary": ["secondary"],
    "Middle deemed secondary": ["secondary"],
    "All-through": ["primary", "secondary"],
}

# Religious character values that mean "not a faith school".
NOT_FAITH = {"Does not apply", "None", ""}

MANUAL_HELP = f"""
Automatic download failed. To download the file by hand:
  1. Open {GIAS}/Downloads in your browser.
  2. Under "Establishment downloads" > "All establishment data", tick
     "Establishment fields CSV". Leave everything else unticked.
  3. Click "Download selected files". Wait for "Your extract is ready",
     then click the "Results.zip" button.
  4. Double-click the zip in your Downloads folder to unzip it. Inside is
     a file named like edubasealldata20261017.csv.
  5. Move that .csv file into this folder:
       {RAW_DIR}
  6. Run this script again. It will find the file there automatically.
"""


# ---------- step 1: get the raw CSV ----------

def download_gias(dest_dir: Path) -> Path:
    """Download the latest GIAS establishment CSV, the same way a browser would.

    The GIAS site has no direct link. You tick a box and submit a form, the site
    builds the file in the background, the page keeps checking until it's ready,
    and then a second form hands you a zip. We repeat those steps here.
    """
    s = requests.Session()  # a Session keeps cookies between requests, like a browser does
    s.headers["User-Agent"] = "se-london-schools data script (private, non-commercial)"

    page = s.get(f"{GIAS}/Downloads", timeout=60)
    page.raise_for_status()
    html = page.text

    def hidden(name, text):
        # Read the value of a hidden form field, e.g. the anti-forgery token.
        m = re.search(rf'name="{re.escape(name)}" type="hidden" value="([^"]*)"', text)
        if not m:
            raise RuntimeError(f"GIAS page layout has changed: no field {name!r}")
        return m.group(1)

    # Find the "Establishment fields CSV" entry (tag all.edubase.data) and its index on the form.
    m = re.search(r'name="Downloads\[(\d+)\]\.Tag" type="hidden" value="all\.edubase\.data"', html)
    if not m:
        raise RuntimeError("GIAS page layout has changed: Establishment fields CSV not found")
    i = m.group(1)
    form = {
        "__RequestVerificationToken": hidden("__RequestVerificationToken", html),
        "Skip": "",
        "SearchType": "Latest",
        "FilterDate.Day": hidden("FilterDate.Day", html),
        "FilterDate.Month": hidden("FilterDate.Month", html),
        "FilterDate.Year": hidden("FilterDate.Year", html),
        f"Downloads[{i}].Tag": "all.edubase.data",
        f"Downloads[{i}].FileGeneratedDate": hidden(f"Downloads[{i}].FileGeneratedDate", html),
        f"Downloads[{i}].Selected": "true",
    }
    print("Asking GIAS to prepare the file…")
    r = s.post(f"{GIAS}/Downloads/Collate", data=form, timeout=60)
    r.raise_for_status()
    job_id = r.url.rstrip("/").rsplit("/", 1)[-1]

    # Check every 2 seconds until the site says the file is ready (give up after 5 minutes).
    for _ in range(150):
        status = s.get(f"{GIAS}/Downloads/GenerateAjax/{job_id}", timeout=60).json()
        if isinstance(status, str):        # the site sends JSON wrapped in a string
            status = json.loads(status)
        if status.get("status"):
            break
        time.sleep(2)
    else:
        raise RuntimeError("GIAS took too long to prepare the file")

    ready = s.get(GIAS + status["redirect"], timeout=60).text
    extract_form = {k: hidden(k, ready) for k in ["__RequestVerificationToken", "id", "path", "returnSource"]}
    print("Downloading (about 8 MB zipped)…")
    z = s.post(f"{GIAS}/Downloads/Download/Extract", data=extract_form, timeout=600)
    z.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"Expected one CSV in the zip, found {zf.namelist()}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        zf.extract(csv_names[0], dest_dir)
        return dest_dir / csv_names[0]


def find_raw_csv(cli_file: str | None) -> Path:
    """Work out which raw CSV to use: --file, else today's download, else download now."""
    if cli_file:
        p = Path(cli_file).expanduser()
        if not p.exists():
            sys.exit(f"File not found: {p}")
        return p

    today = datetime.date.today().strftime("%Y%m%d")
    existing = RAW_DIR / f"edubasealldata{today}.csv"
    if existing.exists():
        print(f"Using today's file, already downloaded: {existing.name}")
        return existing

    try:
        return download_gias(RAW_DIR)
    except Exception as e:  # any failure, so fall back to a manual download
        # If the user has put a file in the folder by hand, use the newest one.
        manual = sorted(RAW_DIR.glob("edubasealldata*.csv"))
        if manual:
            print(f"Download failed ({e}); using the newest file in raw/: {manual[-1].name}")
            return manual[-1]
        print(f"Error: {e}")
        sys.exit(MANUAL_HELP)


# ---------- step 2: filter ----------

def independent_phases(low, high) -> list[str]:
    """Independent schools are listed with phase "Not applicable", so we
    use their age range. Primary: takes children aged 7 or under and keeps
    them to at least 7 (so a 2–7 pre-prep counts). Secondary: covers age 13.
    """
    phases = []
    if pd.notna(low) and pd.notna(high):
        low, high = int(low), int(high)
        if low <= 7 and high >= 7:
            phases.append("primary")
        if low <= 13 <= high:
            phases.append("secondary")
    return phases


def filter_schools(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Keep the schools we want. Return them plus a count of what was dropped and why."""
    dropped = {}
    df = df[df["LA (code)"].isin(LA_CODES)]
    in_boroughs = len(df)

    closed = df["EstablishmentStatus (name)"] != "Open"
    dropped["not open (closed or proposed)"] = int(closed.sum())
    df = df[~closed].copy()

    type_name = df["TypeOfEstablishment (name)"].fillna("")
    df["isIndependent"] = type_name.str.contains("independent", case=False)

    # Independent special schools serve children with specific special needs
    # and don't admit from the general public, so they are out of scope.
    indep_special = df["isIndependent"] & type_name.str.contains("special", case=False)
    dropped["independent special school"] = int(indep_special.sum())
    df = df[~indep_special].copy()

    # Work out phases: state schools from the GIAS phase, independents from age range.
    df["phases"] = [
        independent_phases(r["StatutoryLowAge"], r["StatutoryHighAge"]) if r["isIndependent"]
        else PHASES.get(r["PhaseOfEducation (name)"], [])
        for _, r in df.iterrows()
    ]
    no_phase = df["phases"].str.len() == 0
    # Count the dropped ones by their GIAS phase, e.g. Nursery, 16 plus, Not applicable (special schools, PRUs).
    for ph, n in df.loc[no_phase, "PhaseOfEducation (name)"].fillna("blank").value_counts().items():
        dropped[f"phase '{ph}'"] = int(n)
    df = df[~no_phase].copy()

    return df, {"in_three_boroughs": in_boroughs, "dropped": dropped}


# ---------- step 3: coordinates ----------

def make_transformer():
    """Build the OSGB36 (British National Grid, EPSG:27700) to WGS84 (EPSG:4326) converter.

    pyproj lists the possible conversion methods, best first. The most accurate
    one (OSTN15, about 0.1 m) needs an extra grid file we don't have installed,
    so we take the best one that works offline. It is accurate to about 2 m,
    which is plenty when admission distances run to hundreds of metres.
    always_xy=True means we pass (easting, northing) and get back (longitude, latitude).
    """
    # pyproj warns that the OSTN15 grid is missing. We know, and accept 2 m, so hide that warning.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Best transformation is not available")
        group = TransformerGroup("EPSG:27700", "EPSG:4326", always_xy=True)
    t = group.transformers[0]
    return t, f"{t.description} (stated accuracy {t.accuracy} m)"


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Straight-line distance in metres between two lat/lon points (same formula as the app)."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# ---------- step 4: tidy into our shape ----------

def clean(value):
    """Turn pandas' 'missing' marker (NaN) into None, and trim whitespace."""
    if pd.isna(value):
        return None
    return str(value).strip() or None


def to_record(r, transformer) -> dict:
    """One school row from GIAS, reshaped into the fields our data model needs.
    Ofsted, distances and confidence labels are added by later scripts."""
    lat = lon = None
    if pd.notna(r["Easting"]) and pd.notna(r["Northing"]):
        lon, lat = transformer.transform(float(r["Easting"]), float(r["Northing"]))
        lat, lon = round(lat, 6), round(lon, 6)   # 6 decimal places is about 10 cm

    religion = clean(r["ReligiousCharacter (name)"]) or ""
    gender = (clean(r["Gender (name)"]) or "").lower()

    website = clean(r["SchoolWebsite"])
    if website and not website.lower().startswith(("http://", "https://")):
        website = "https://" + website   # GIAS often stores bare "www.example.sch.uk"

    return {
        "urn": int(r["URN"]),
        "name": clean(r["EstablishmentName"]),
        # Our phase(s). All-through schools have both; step 4 decides how to show them.
        "phases": r["phases"],
        "giasPhase": clean(r["PhaseOfEducation (name)"]),
        "type": clean(r["TypeOfEstablishment (name)"]),
        "typeGroup": clean(r["EstablishmentTypeGroup (name)"]),
        "isIndependent": bool(r["isIndependent"]),
        "isFaith": religion not in NOT_FAITH,
        "religiousCharacter": religion if religion not in NOT_FAITH else None,
        "gender": gender if gender in ("mixed", "boys", "girls") else None,
        "admissionsPolicy": clean(r["AdmissionsPolicy (name)"]),   # e.g. Selective / Non-selective
        "statutoryLowAge": int(r["StatutoryLowAge"]) if pd.notna(r["StatutoryLowAge"]) else None,
        "statutoryHighAge": int(r["StatutoryHighAge"]) if pd.notna(r["StatutoryHighAge"]) else None,
        "laCode": int(r["LA (code)"]),
        "laName": LA_CODES[r["LA (code)"]],
        "postcode": clean(r["Postcode"]),
        "easting": int(float(r["Easting"])) if pd.notna(r["Easting"]) else None,
        "northing": int(float(r["Northing"])) if pd.notna(r["Northing"]) else None,
        "latitude": lat,
        "longitude": lon,
        "websiteUrl": website,
    }


# ---------- step 5: spot-check ----------

def spot_check(schools: list[dict], transformer) -> list[dict]:
    """Compare three schools (one per borough) with postcodes.io.

    Two separate measurements:
      - school_to_postcode_m: our school position vs the centre of its postcode.
        These are never identical (a postcode covers a whole street or block),
        so tens of metres is normal. Hundreds would suggest a wrong location.
      - conversion_error_m: postcodes.io gives both the grid reference and the
        lat/lon of each postcode centre. We convert its grid reference ourselves
        and compare with its lat/lon. This tests our conversion on its own.
    """
    results = []
    for la in ["Lambeth", "Lewisham", "Southwark"]:
        # Lowest-URN state school in each borough with a position, so the choice is repeatable.
        candidates = [s for s in schools if s["laName"] == la and not s["isIndependent"]
                      and s["latitude"] is not None and s["postcode"]]
        s = min(candidates, key=lambda x: x["urn"])
        try:
            j = requests.get(f"https://api.postcodes.io/postcodes/{requests.utils.quote(s['postcode'])}",
                             timeout=30).json()
        except requests.RequestException as e:
            results.append({"name": s["name"], "postcode": s["postcode"], "error": str(e)})
            continue
        if j.get("status") != 200:
            results.append({"name": s["name"], "postcode": s["postcode"], "error": j.get("error", "not found")})
            continue
        p = j["result"]
        our_lon, our_lat = transformer.transform(p["eastings"], p["northings"])
        results.append({
            "name": s["name"], "laName": la, "postcode": s["postcode"],
            "school_to_postcode_m": round(haversine_m(s["latitude"], s["longitude"], p["latitude"], p["longitude"]), 1),
            "conversion_error_m": round(haversine_m(our_lat, our_lon, p["latitude"], p["longitude"]), 1),
        })
    return results


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="use this GIAS CSV instead of downloading")
    args = ap.parse_args()

    raw = find_raw_csv(args.file)
    # GIAS files use Windows encoding (cp1252), not UTF-8. dtype=str stops pandas
    # guessing types, so e.g. LA code "208" stays as text and URNs don't become decimals.
    df = pd.read_csv(raw, encoding="cp1252", dtype=str, low_memory=False)
    print(f"Read {len(df):,} establishments from {raw.name}")

    kept, filter_log = filter_schools(df)
    transformer, transform_desc = make_transformer()
    schools = sorted((to_record(r, transformer) for _, r in kept.iterrows()), key=lambda s: s["urn"])
    checks = spot_check(schools, transformer)

    # The file's date is in its name, e.g. edubasealldata20261017.csv.
    m = re.search(r"(\d{8})", raw.name)
    file_date = datetime.datetime.strptime(m.group(1), "%Y%m%d").date().isoformat() if m else None

    out = {
        "source": {
            "title": "Get Information About Schools: Establishment fields CSV",
            "url": f"{GIAS}/Downloads",
            "file": raw.name,
            "fileDate": file_date,
            "licence": "Open Government Licence v3.0",
        },
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "coordinateConversion": transform_desc,
        "filter": filter_log,
        "spotCheck": checks,
        "schools": schools,
    }
    OUTPUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print_summary(schools, filter_log, checks, transform_desc)
    print(f"\nSaved {len(schools)} schools to {OUTPUT.relative_to(PROJECT)}")


def print_summary(schools, filter_log, checks, transform_desc):
    print("\n=== GIAS summary ===")
    print(f"Establishments in the three boroughs (any status): {filter_log['in_three_boroughs']}")
    print("Dropped:")
    for reason, n in filter_log["dropped"].items():
        print(f"  {n:4d}  {reason}")
    print(f"Kept: {len(schools)}")

    # One row per school per phase, so all-through schools count in both columns.
    rows = [{"Borough": s["laName"], "Sector": "Independent" if s["isIndependent"] else "State", "Phase": ph}
            for s in schools for ph in s["phases"]]
    table = pd.crosstab([pd.Series([r["Borough"] for r in rows], name="Borough"),
                         pd.Series([r["Sector"] for r in rows], name="Sector")],
                        pd.Series([r["Phase"] for r in rows], name="Phase"), margins=True, margins_name="Total")
    print("\nSchools by borough, sector and phase (all-through and independent")
    print("schools covering both phases are counted once in each column):")
    print(table.to_string())

    both = [s for s in schools if len(s["phases"]) == 2]
    print(f"\nCovering both phases: {sum(not s['isIndependent'] for s in both)} state all-through, "
          f"{sum(s['isIndependent'] for s in both)} independent")
    print(f"Faith schools: {sum(s['isFaith'] for s in schools)}  |  "
          f"single-sex: {sum(s['gender'] in ('boys', 'girls') for s in schools)}  |  "
          f"selective: {sum(s['admissionsPolicy'] == 'Selective' for s in schools)}")

    no_pos = [s for s in schools if s["latitude"] is None]
    print(f"No grid reference (cannot be placed on the map): {len(no_pos)}")
    for s in no_pos:
        print(f"  - {s['name']} (URN {s['urn']}, {s['laName']})")
    no_gender = [s for s in schools if s["gender"] is None]
    if no_gender:
        print(f"Gender not recorded: {', '.join(s['name'] for s in no_gender)}")

    print(f"\nCoordinate conversion: {transform_desc}")
    print("Spot-check against postcodes.io:")
    for c in checks:
        if "error" in c:
            print(f"  {c['name']} ({c['postcode']}): could not check: {c['error']}")
        else:
            print(f"  {c['name']} ({c['laName']}, {c['postcode']}):\n"
                  f"      school is {c['school_to_postcode_m']} m from its postcode centre; "
                  f"conversion error {c['conversion_error_m']} m")


if __name__ == "__main__":
    main()
