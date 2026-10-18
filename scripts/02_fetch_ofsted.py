"""
Step 2: download Ofsted inspection outcomes and work out each school's grade.

Why two files?
  Ofsted publishes a "latest inspections" CSV every month, one row per school.
  Since the new report cards arrived (November 2025), the current file only
  covers inspections from September 2019 onwards. Many good and outstanding
  schools haven't had a graded inspection since before 2019. They've only had
  shorter "ungraded" checks, which keep the existing grade. For those, the old
  grade is missing from the current file.

  The October 2025 file, the last one before the change, lists the latest
  graded inspection for every school under any framework. No old-style graded
  inspections have happened since, so that file holds each school's final
  old-style grade. We combine:
    - the CURRENT file: report cards, recent graded inspections, ungraded checks,
      and the list of schools open today
    - the OCTOBER 2025 file: the last old-style graded inspection, however old

What this script produces:
  data/source-snapshots/ofsted-london.json
      Every state-funded school in London (all 33 boroughs) with its grades.
      We need all of London for the percentile comparison later.
  data/source-snapshots/ofsted-three-boroughs.json
      Our GIAS schools (from step 1) with ofstedNumeric, ofstedFormat and
      ofstedConfidence, as defined in docs/PROJECT_BRIEF.md section 2.

Run from the main project folder (after step 1):
    python3 scripts/02_fetch_ofsted.py
"""

import datetime
import json
import re
import sys
from pathlib import Path

import pandas as pd
import requests

# ---------- settings ----------

PROJECT = Path(__file__).resolve().parent.parent
SNAPSHOTS = PROJECT / "data" / "source-snapshots"
RAW_DIR = SNAPSHOTS / "raw"                          # ignored by git
GIAS_FILE = SNAPSHOTS / "gias-cleaned.json"          # output of step 1
OUT_LONDON = SNAPSHOTS / "ofsted-london.json"
OUT_OURS = SNAPSHOTS / "ofsted-three-boroughs.json"

PAGE = "https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes"
PAGE_API = PAGE.replace("www.gov.uk/", "www.gov.uk/api/content/")   # same page as machine-readable JSON

# The last file before report cards. Its title on the page:
LEGACY_TITLE = "Management information - state-funded schools - latest inspections as at 31 October 2025"

# London borough LA codes: 201 City of London to 213 Westminster (inner),
# 301 Barking and Dagenham to 320 Waltham Forest (outer).
LONDON_LA = set(range(201, 214)) | set(range(301, 321))

LEGACY_WORDS = {1: "Outstanding", 2: "Good", 3: "Requires improvement", 4: "Inadequate"}

# Report cards grade each area on a five-point scale. The brief says to map
# them to the nearest old grade, and the descriptions match up like this:
#   Exceptional        -> 1 (Outstanding: the old grade for exceptional schools)
#   Strong standard    -> 2 (Good: above the expected standard, but not exceptional)
#   Expected standard  -> 2 (Good: the standard all schools should meet)
#   Needs attention    -> 3 (Requires improvement)
#   Urgent improvement -> 4 (Inadequate)
# We also keep the original five-point grade, so this can be revisited.
REPORT_CARD_TO_NUMERIC = {
    "Exceptional": 1, "Strong standard": 2, "Expected standard": 2,
    "Needs attention": 3, "Urgent improvement": 4,
}
# The report-card area we treat as the equivalent of the old "Quality of
# education". Report cards don't have that heading. "Curriculum and teaching"
# covers the same ground: what is taught, and how well.
REPORT_CARD_ANCHOR = "Curriculum and teaching"
REPORT_CARD_AREAS = [
    "Inclusion", "Curriculum and teaching", "Achievement", "Attendance and behaviour",
    "Personal development and wellbeing", "Early years (where applicable)",
    "Post-16 provision (where applicable)", "Leadership and governance",
]


# ---------- downloading ----------

def find_files() -> tuple[dict, dict]:
    """Ask gov.uk for the page's attachment list. Return the newest
    'latest inspections' CSV and the October 2025 one."""
    data = requests.get(PAGE_API, timeout=60).json()
    latest, legacy = None, None
    for a in data["details"]["attachments"]:
        title = " ".join(a.get("title", "").split())   # tidy stray spaces
        if not (title.startswith("Management information - state-funded schools - latest inspections")
                and a.get("url", "").endswith(".csv")):
            continue
        if title == LEGACY_TITLE:
            legacy = {"title": title, "url": a["url"]}
        # The date is at the end of the title, e.g. "... as at 30 Sep 2026".
        m = re.search(r"(\d{1,2}) (\w+) (\d{4})$", title)
        if not m:
            continue
        try:
            # Titles mix "Sep" and "September", so use only the first 3 letters of the month.
            when = datetime.datetime.strptime(f"{m[1]} {m[2][:3]} {m[3]}", "%d %b %Y")
        except ValueError:
            continue
        if latest is None or when > latest["asAt"]:
            latest = {"title": title, "url": a["url"], "asAt": when}
    if not latest or not legacy:
        sys.exit("Could not find the Ofsted files on the gov.uk page. Has the page layout changed?\n" + PAGE)
    latest["asAt"] = latest["asAt"].date().isoformat()
    return latest, legacy


def download(url: str) -> Path:
    """Download a file into raw/ unless we already have it."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / url.rsplit("/", 1)[-1]
    if dest.exists():
        print(f"  already downloaded: {dest.name}")
        return dest
    print(f"  downloading {dest.name}")
    r = requests.get(url, timeout=600)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def read_csv(path: Path) -> pd.DataFrame:
    # Ofsted CSVs use Windows encoding (cp1252). Treat "NULL" text as missing.
    return pd.read_csv(path, encoding="cp1252", dtype=str, low_memory=False, na_values=["NULL"])


# ---------- small helpers ----------

def val(row, col):
    """Cell value as tidy text, or None if blank or the column doesn't exist."""
    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    return str(v).strip() or None


def iso_date(text):
    """Ofsted dates are day/month/year, e.g. 13/01/2026. Turn them into 2026-01-13."""
    if not text:
        return None
    return datetime.datetime.strptime(text, "%d/%m/%Y").date().isoformat()


def grade_1_to_4(text):
    """Old-style grades are '1' to '4'. Anything else ('Not judged', '9' = not applicable) gives None."""
    return int(text) if text in ("1", "2", "3", "4") else None


# ---------- working out the grade for one school ----------

def legacy_inspection(cur, old):
    """The school's last old-style graded inspection, or None.

    Two possible sources: the current file's OEIF columns (Sept 2019 onwards)
    and the October 2025 file (any framework). Use whichever is more recent.
    """
    candidates = []
    if val(cur, "Inspection number of latest OEIF graded inspection"):
        candidates.append({
            "inspectionNumber": val(cur, "Inspection number of latest OEIF graded inspection"),
            "date": iso_date(val(cur, "Inspection start date of latest OEIF graded inspection")),
            "publicationDate": iso_date(val(cur, "Publication date of latest OEIF graded inspection")),
            "overallEffectiveness": val(cur, "Latest OEIF overall effectiveness"),
            "qualityOfEducation": val(cur, "Latest OEIF quality of education"),
            "fromPredecessor": val(cur, "Does the latest OEIF graded inspection relate to the URN of the current school?") == "No",
            "file": "current",
        })
    if old is not None and val(old, "Inspection number of latest graded inspection"):
        candidates.append({
            "inspectionNumber": val(old, "Inspection number of latest graded inspection"),
            "date": iso_date(val(old, "Inspection start date")),
            "publicationDate": iso_date(val(old, "Publication date")),
            "overallEffectiveness": val(old, "Overall effectiveness"),
            "qualityOfEducation": val(old, "Quality of education"),
            "fromPredecessor": val(old, "Does the latest graded inspection relate to the URN of the current school?") == "No",
            "file": "october-2025",
        })
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["date"] or "")


def report_card(cur):
    """The school's latest report-card inspection (November 2025 onwards), or None."""
    if not val(cur, REPORT_CARD_ANCHOR):
        return None
    return {
        "inspectionNumber": val(cur, "Inspection number of latest full inspection"),
        "inspectionType": val(cur, "Inspection type"),
        "date": iso_date(val(cur, "Inspection start date")),
        "publicationDate": iso_date(val(cur, "Publication date")),
        "fromPredecessor": val(cur, "Does the latest full inspection relate to the URN of the current school?") == "No",
        "categoryOfConcern": val(cur, "Category of concern"),
        "safeguarding": val(cur, "Safeguarding standards"),
        "grades": {area: val(cur, area) for area in REPORT_CARD_AREAS},
    }


def derive(rc, legacy):
    """Apply section 2 of the brief to produce ofstedNumeric, ofstedFormat,
    ofstedGrade (the text shown on the card) and ofstedInspectionDate."""
    if rc:
        # A report card is always newer than any old-style grade, so it wins.
        anchor = rc["grades"][REPORT_CARD_ANCHOR]
        numeric = REPORT_CARD_TO_NUMERIC.get(anchor)
        grade = f"{REPORT_CARD_ANCHOR}: {anchor}"
        # A school placed in special measures or "requires significant
        # improvement" is the report-card equivalent of Inadequate.
        if rc["categoryOfConcern"]:
            numeric = 4
            grade += f" (category of concern: {rc['categoryOfConcern']})"
        return {"ofstedNumeric": numeric, "ofstedFormat": "report-card",
                "ofstedGrade": grade, "ofstedInspectionDate": rc["date"]}

    if legacy:
        overall = grade_1_to_4(legacy["overallEffectiveness"])
        if overall:
            return {"ofstedNumeric": overall, "ofstedFormat": "legacy",
                    "ofstedGrade": LEGACY_WORDS[overall], "ofstedInspectionDate": legacy["date"]}
        # Graded inspections from Sept 2024 to Nov 2025 gave no overall grade,
        # only area grades. The brief ranks on Quality of education, so use that.
        qoe = grade_1_to_4(legacy["qualityOfEducation"])
        if qoe:
            return {"ofstedNumeric": qoe, "ofstedFormat": "legacy",
                    "ofstedGrade": f"{LEGACY_WORDS[qoe]} (quality of education; no overall grade given)",
                    "ofstedInspectionDate": legacy["date"]}

    return {"ofstedNumeric": None, "ofstedFormat": None,
            "ofstedGrade": "Not yet inspected", "ofstedInspectionDate": None}


def build_record(cur, old) -> dict:
    """One school's Ofsted details, in our shape."""
    rc = report_card(cur)
    legacy = legacy_inspection(cur, old)
    ungraded = None
    if val(cur, "Latest ungraded inspection number"):
        ungraded = {
            "date": iso_date(val(cur, "Date of latest ungraded inspection")),
            "outcome": val(cur, "Ungraded inspection overall outcome"),
        }
    return {
        "urn": int(cur["URN"]),
        "name": val(cur, "School name"),
        "laCode": int(cur["LAESTAB"][:3]),
        "localAuthority": val(cur, "Local authority"),
        "ofstedPhase": val(cur, "Ofsted phase"),
        "type": val(cur, "Type of education"),
        "webLink": val(cur, "Web Link"),
        **derive(rc, legacy),
        "reportCard": rc,
        "legacyInspection": legacy,
        "latestUngraded": ungraded,
    }


# ---------- main ----------

def main():
    print("Finding the Ofsted files on gov.uk…")
    latest, legacy_file = find_files()
    print(f"  current: {latest['title']}")
    print(f"  legacy:  {legacy_file['title']}")
    cur_df = read_csv(download(latest["url"]))
    old_df = read_csv(download(legacy_file["url"]))

    # Keep London. The first 3 digits of LAESTAB are the LA code.
    cur_df["laCode"] = cur_df["LAESTAB"].str[:3].astype(int)
    london = cur_df[cur_df["laCode"].isin(LONDON_LA)]
    print(f"\nCurrent file: {len(cur_df):,} schools in England, {len(london):,} in London "
          f"({london['laCode'].nunique()} London LA codes)")

    old_by_urn = {r["URN"]: r for _, r in old_df.iterrows()}
    records = [build_record(r, old_by_urn.get(r["URN"])) for _, r in london.iterrows()]
    records.sort(key=lambda r: r["urn"])
    by_urn = {r["urn"]: r for r in records}

    sources = {
        "current": {"title": latest["title"], "url": latest["url"], "asAt": latest["asAt"]},
        "legacy": {"title": legacy_file["title"], "url": legacy_file["url"],
                   "note": "Used only for each school's last old-style graded inspection"},
        "page": PAGE,
        "licence": "Open Government Licence v3.0",
    }
    OUT_LONDON.write_text(json.dumps({
        "sources": sources,
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "reportCardMapping": REPORT_CARD_TO_NUMERIC,
        "reportCardAnchor": REPORT_CARD_ANCHOR,
        "schools": records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # ----- match our three-borough schools -----
    gias = json.loads(GIAS_FILE.read_text(encoding="utf-8"))["schools"]
    ours = []
    for s in gias:
        o = by_urn.get(s["urn"])
        if s["isIndependent"]:
            # Independent schools aren't in Ofsted's state-funded data (many are
            # inspected by ISI instead). The brief shows them with no grade.
            conf = None
        elif o:
            conf = "CONFIRMED"   # found in Ofsted's data, even if not yet inspected
        else:
            conf = "MISSING"
        ours.append({
            "urn": s["urn"], "name": s["name"], "laName": s["laName"], "phases": s["phases"],
            "isIndependent": s["isIndependent"],
            "ofstedNumeric": o["ofstedNumeric"] if o else None,
            "ofstedFormat": o["ofstedFormat"] if o else None,
            "ofstedGrade": o["ofstedGrade"] if o else None,
            "ofstedInspectionDate": o["ofstedInspectionDate"] if o else None,
            "ofstedConfidence": conf,
            "ofstedFromPredecessor": bool(o and ((o["reportCard"] or {}).get("fromPredecessor")
                                                 or (o["legacyInspection"] or {}).get("fromPredecessor"))),
            "ofstedWebLink": o["webLink"] if o else None,
        })
    OUT_OURS.write_text(json.dumps({
        "sources": sources,
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "schools": ours,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print_summary(records, ours)
    print(f"\nSaved {len(records):,} London schools to {OUT_LONDON.relative_to(PROJECT)}")
    print(f"Saved {len(ours)} of our schools to {OUT_OURS.relative_to(PROJECT)}")


def print_summary(london, ours):
    def table(rows, title):
        df = pd.DataFrame(rows)
        df["format"] = df["ofstedFormat"].fillna("none")
        df["grade"] = df["ofstedNumeric"].map(LEGACY_WORDS).fillna("Not yet inspected")
        order = list(LEGACY_WORDS.values()) + ["Not yet inspected"]
        t = pd.crosstab(pd.Categorical(df["grade"], order), df["format"], margins=True, margins_name="Total",
                        rownames=["ofstedNumeric"], colnames=["ofstedFormat"])
        print(f"\n{title}\n{t.to_string()}")

    table(london, "ALL LONDON (state-funded, incl. special/AP/nursery): grade by format")

    state = [o for o in ours if not o["isIndependent"]]
    matched = [o for o in state if o["ofstedConfidence"] == "CONFIRMED"]
    print(f"\nOUR THREE BOROUGHS: {len(state)} state schools; {len(matched)} found in Ofsted data, "
          f"{len(state) - len(matched)} missing. {len(ours) - len(state)} independents (not in this dataset).")
    table(matched, "Our state schools: grade by format")

    no_overall = [o for o in matched if o["ofstedGrade"] and "no overall grade" in o["ofstedGrade"]]
    print(f"\nGraded on quality of education because the inspection gave no overall grade: {len(no_overall)}")
    pred = [o for o in matched if o["ofstedFromPredecessor"]]
    print(f"Grade comes from a predecessor school (e.g. before becoming an academy): {len(pred)}")

    old = sorted((o for o in matched if o["ofstedFormat"] == "legacy"), key=lambda o: o["ofstedInspectionDate"])
    if old:
        print(f"Oldest graded inspection behind a grade: {old[0]['ofstedInspectionDate']} ({old[0]['name']})")
        pre2019 = sum(o["ofstedInspectionDate"] < "2019-09-01" for o in old)
        print(f"Grades from inspections before September 2019: {pre2019}")

    print("\nOur state schools NOT found in Ofsted data (ofstedConfidence = MISSING):")
    missing = [o for o in state if o["ofstedConfidence"] == "MISSING"]
    for o in missing:
        print(f"  - {o['name']} (URN {o['urn']}, {o['laName']})")
    if not missing:
        print("  none")
    print("\nOur state schools found but with no graded inspection (shown as 'Not yet inspected'):")
    uninspected = [o for o in matched if o["ofstedNumeric"] is None]
    for o in uninspected:
        print(f"  - {o['name']} (URN {o['urn']}, {o['laName']})")
    if not uninspected:
        print("  none")


if __name__ == "__main__":
    main()
