"""
Step 4: merge the three snapshots into the files the app reads.

Reads (all made by steps 1 to 3, in data/source-snapshots/):
    gias-cleaned.json            the school register: names, types, locations
    ofsted-three-boroughs.json   each school's Ofsted grade
    ofsted-london.json           every London school's grade, for percentiles
    distances.json               furthest-admitted distances, year by year

Writes:
    data/schools.json   what index.html loads
    data/schools.csv    the same data flattened into a spreadsheet for checking by hand

Run from the main project folder:  python3 scripts/04_merge_and_flag.py

-----------------------------------------------------------------------------
THE SCHOOL RECORD (docs/PROJECT_BRIEF.md, section 5)
-----------------------------------------------------------------------------
data/schools.json looks like this:

    {
      "buildDate": "2026-10-18",            # the day this script ran
      "sources":   {...},                   # titles, URLs and dates of every input
      "estimateDefaults": {"primary": 1287, "secondary": 2414},
      "schools":   [School, School, ...]
    }

There is one School per school *per phase*. An all-through school (Reception
to Year 11) admits separately at Reception and at Year 7, with different
distances, so it gets two records: one "primary" and one "secondary". That is
why `id` exists: `urn` alone is no longer unique.

    School = {
      "id":            "137093-secondary",  # urn + phase, unique
      "urn":           137093,              # GIAS's school number
      "name":          "Dunraven School",
      "phase":         "primary" | "secondary",
      "type":          "Academy converter", # GIAS's type of establishment
      "isIndependent": False,
      "isFaith":       False,
      "gender":        "mixed" | "boys" | "girls",
      "admissionsNote": "Ability banding ..." | None,   # lottery, banding, aptitude etc.
      "laName":        "Lambeth" | "Lewisham" | "Southwark",
      "postcode":      "SW16 2QB",
      "latitude":      51.43,               # WGS84, converted from GIAS's grid figures
      "longitude":     -0.12,
      "websiteUrl":    "https://..." | None,
      "fees":          None,                # independents only; not collected yet

      "ofstedGrade":          "Outstanding" | "Curriculum and teaching: Expected standard" | None,
      "ofstedNumeric":        1 | 2 | 3 | 4 | None,   # 1 is best; None = not yet inspected
      "ofstedFormat":         "legacy" | "report-card" | None,
      "ofstedInspectionDate": "2024-06-12" | None,
      "ofstedWebLink":        "http://www.ofsted.gov.uk/..." | None,
      "ofstedConfidence":     "CONFIRMED" | "MISSING" | None,   # None for independents
      "londonPercentile":     0-100 | None, # higher is better; see london_percentiles()

      "distanceMethod":  "straight" | "walking" | "unknown",
      "distances": [                        # newest first; empty when ESTIMATED
        {
          "year":      "2026",              # intake year (September 2026 entry)
          "metres":    654.28 | None,       # None only when allApplicantsOffered
          "allApplicantsOffered": False,    # True = everyone who applied got in,
                                            #   so there was no furthest distance
          "source":    "Lambeth Council: how offers were made ...",
          "sourceUrl": "https://...",
          # optional extras, only present when they apply:
          "bands":     [...],               # banded/two-site schools: every cut-off;
                                            #   metres is the furthest of them
          "note":      "identical to the 2025 figure ...",
          "conflicts": [...],               # a different figure for the same year
                                            #   found in another source
        }, ...
      ],
      "estimatedMetres":    None | 1287 | 2414,  # set only when ESTIMATED
      "priorityArea":       None,           # GeoJSON polygon if a school publishes one
      "distanceConfidence": "CONFIRMED" | "ESTIMATED" | "MISSING" | None,  # None for independents
    }

Confidence rules (brief, section 4):
  distanceConfidence
    CONFIRMED  a figure was found in a council or school document (URL recorded)
    ESTIMATED  no figure found; the app uses estimatedMetres and shows no
               "how likely" judgement
    MISSING    the school cannot be placed on the map (no coordinates); it stays
               in the CSV but the app must leave it off the map
  ofstedConfidence
    CONFIRMED  the school's URN is in Ofsted's data (even if not yet inspected)
    MISSING    it isn't
  Independent schools have neither: no admissions distance and no state Ofsted
  data, so both are None.

The "how likely" band is not stored: it depends on the postcode typed in, so
index.html works it out from `distances`.
"""

import csv
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "source-snapshots"
OUT_JSON = ROOT / "data" / "schools.json"
OUT_CSV = ROOT / "data" / "schools.csv"

GRADE_WORDS = {1: "Outstanding", 2: "Good", 3: "Requires improvement", 4: "Inadequate"}


def load(name):
    path = SNAP / name
    if not path.exists():
        sys.exit(f"Missing {path}. Run the earlier scripts first (see scripts/README.md).")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def london_percentiles(london_schools):
    """For each phase, turn an Ofsted grade into a London percentile.

    The percentile is the share of graded London schools of the same phase that
    this grade beats, counting half of the schools with the same grade (the
    usual "percentile rank" rule for ties). Most London schools are Good, so
    every Good primary gets the same figure; the number cannot split schools
    with the same grade, and we don't pretend it can.

    The app shows it as "Top (100 - percentile)%", as the prototype does.

    Returns {"primary": {1: 81.5, 2: 31.2, ...}, "secondary": {...}} plus the
    counts used, so the method can be checked.
    """
    table, counts = {}, {}
    for phase, ofsted_phase in (("primary", "Primary"), ("secondary", "Secondary")):
        # Special schools, nurseries and alternative provision are left out:
        # they are not what the app compares. Ofsted lists all-through schools
        # as "Secondary".
        grades = [s["ofstedNumeric"] for s in london_schools
                  if s["ofstedPhase"] == ofsted_phase and s["ofstedNumeric"] is not None]
        n = len(grades)
        per_grade = {g: grades.count(g) for g in (1, 2, 3, 4)}
        table[phase] = {}
        for g in (1, 2, 3, 4):
            worse = sum(per_grade[w] for w in (1, 2, 3, 4) if w > g)  # 4 is worst
            table[phase][g] = round(100 * (worse + per_grade[g] / 2) / n, 1)
        counts[phase] = {"gradedSchools": n, "byGrade": per_grade}
    return table, counts


def clean_distance(entry):
    """Copy one distance entry, dropping the long 'evidence' text.

    The evidence (the exact words a figure was read from) stays in
    distances.json for checking; the app doesn't need it.
    """
    out = {k: entry[k] for k in ("year", "metres", "allApplicantsOffered", "source", "sourceUrl")}
    for extra in ("bands", "note", "conflicts"):
        if entry.get(extra):
            out[extra] = entry[extra]
    return out


def build_records(gias, ofsted, dist, pct_table):
    ofsted_by_urn = {s["urn"]: s for s in ofsted["schools"]}
    dist_by_urn = dist["schools"]  # keys are URNs as strings (JSON keys must be text)
    problems = []
    records = []

    for g in gias["schools"]:
        urn = g["urn"]
        o = ofsted_by_urn.get(urn)
        d = dist_by_urn.get(str(urn))
        placed = g.get("latitude") is not None and g.get("longitude") is not None

        for phase in g["phases"]:
            r = {
                "id": f"{urn}-{phase}",
                "urn": urn,
                "name": g["name"],
                "phase": phase,
                "type": g["type"],
                "isIndependent": g["isIndependent"],
                "isFaith": g["isFaith"],
                "gender": g["gender"],
                "admissionsNote": d["admissionsNote"] if d else None,
                "laName": g["laName"],
                "postcode": g["postcode"],
                "latitude": g["latitude"],
                "longitude": g["longitude"],
                "websiteUrl": g.get("websiteUrl"),
                "fees": None,
            }

            # ---- Ofsted ----
            if g["isIndependent"]:
                r.update(ofstedGrade=None, ofstedNumeric=None, ofstedFormat=None,
                         ofstedInspectionDate=None, ofstedWebLink=None,
                         ofstedConfidence=None, londonPercentile=None)
            elif o is None or o.get("ofstedConfidence") != "CONFIRMED":
                r.update(ofstedGrade=None, ofstedNumeric=None, ofstedFormat=None,
                         ofstedInspectionDate=None, ofstedWebLink=None,
                         ofstedConfidence="MISSING", londonPercentile=None)
                problems.append(f"{g['name']} ({urn}): no Ofsted record")
            else:
                n = o["ofstedNumeric"]
                r.update(ofstedGrade=o["ofstedGrade"], ofstedNumeric=n,
                         ofstedFormat=o["ofstedFormat"],
                         ofstedInspectionDate=o["ofstedInspectionDate"],
                         ofstedWebLink=o.get("ofstedWebLink"),
                         ofstedConfidence="CONFIRMED",
                         londonPercentile=pct_table[phase][n] if n else None)

            # ---- Distances ----
            if g["isIndependent"]:
                r.update(distanceMethod="unknown", distances=[], estimatedMetres=None,
                         priorityArea=None, distanceConfidence=None)
            else:
                p = (d or {}).get("phases", {}).get(phase)
                if p is None:
                    # Step 3 should have covered every state school phase.
                    problems.append(f"{g['name']} ({urn}) {phase}: not in distances.json")
                    p = {"distances": [], "distanceMethod": "unknown",
                         "distanceConfidence": "ESTIMATED",
                         "estimatedMetres": dist["estimateDefaults"][phase]}
                distances = sorted((clean_distance(e) for e in p["distances"]),
                                   key=lambda e: e["year"], reverse=True)
                conf = "CONFIRMED" if distances else "ESTIMATED"
                if conf != p["distanceConfidence"]:
                    problems.append(f"{g['name']} {phase}: step 3 said {p['distanceConfidence']}, "
                                    f"but has {len(distances)} figures; using {conf}")
                if not placed:
                    conf = "MISSING"
                r.update(distanceMethod=p["distanceMethod"], distances=distances,
                         estimatedMetres=(dist["estimateDefaults"][phase]
                                          if conf == "ESTIMATED" else None),
                         priorityArea=d.get("priorityArea") if d else None,
                         distanceConfidence=conf)
            if not placed:
                problems.append(f"{g['name']} ({urn}): no coordinates")
            records.append(r)

    # Same order as the CSV: borough, phase, name.
    records.sort(key=lambda r: (r["isIndependent"], r["laName"], r["phase"], r["name"]))
    return records, problems


def write_csv(records, path):
    """One row per school phase, one column per intake year.

    A year cell holds the furthest distance in metres, or "all offered" when
    everyone who applied got a place, or is blank when no figure was found.
    Banded schools show the furthest band, and the bands column lists them all.
    """
    years = sorted({e["year"] for r in records for e in r["distances"]}, reverse=True)
    cols = ["id", "urn", "name", "phase", "laName", "type", "isIndependent", "isFaith",
            "gender", "postcode", "latitude", "longitude",
            "ofstedGrade", "ofstedNumeric", "ofstedFormat", "ofstedInspectionDate",
            "ofstedConfidence", "londonPercentile",
            "distanceConfidence", "distanceMethod", "yearsOfData", "estimatedMetres"]
    cols += [f"distance_{y}" for y in years]
    cols += ["bands", "notes", "conflictingYears", "admissionsNote",
             "newestSourceUrl", "websiteUrl"]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        # utf-8-sig adds a marker that tells Excel the file is UTF-8, so names
        # like "St Mary's" with curly apostrophes display properly.
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in records:
            row = {c: r.get(c) for c in cols}
            row["yearsOfData"] = len(r["distances"])
            for e in r["distances"]:
                row[f"distance_{e['year']}"] = ("all offered" if e["allApplicantsOffered"]
                                               else e["metres"])
            row["bands"] = "; ".join(
                f"{e['year']}: " + ", ".join(str(b.get("metres")) for b in e["bands"])
                for e in r["distances"] if e.get("bands"))
            row["notes"] = "; ".join(f"{e['year']}: {e['note']}"
                                     for e in r["distances"] if e.get("note"))
            row["conflictingYears"] = ", ".join(e["year"] for e in r["distances"]
                                                if e.get("conflicts"))
            row["newestSourceUrl"] = r["distances"][0]["sourceUrl"] if r["distances"] else None
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return years


def print_summary(records):
    """One table: rows are phase + type, columns are grade and confidence counts."""
    heads = ["phase", "type", "n", "Out", "Good", "RI", "Inad", "none",
             "dist CONF", "dist EST", "dist MISS", "ofst CONF", "ofst MISS"]
    groups = {}
    for r in records:
        groups.setdefault((r["phase"], r["type"]), []).append(r)

    def row_for(label_phase, label_type, rs):
        c = lambda f: sum(1 for r in rs if f(r))
        state = [r for r in rs if not r["isIndependent"]]
        return [label_phase, label_type, len(rs),
                c(lambda r: r["ofstedNumeric"] == 1), c(lambda r: r["ofstedNumeric"] == 2),
                c(lambda r: r["ofstedNumeric"] == 3), c(lambda r: r["ofstedNumeric"] == 4),
                c(lambda r: r["ofstedNumeric"] is None),
                *(sum(1 for r in state if r["distanceConfidence"] == k)
                  for k in ("CONFIRMED", "ESTIMATED", "MISSING")),
                *(sum(1 for r in state if r["ofstedConfidence"] == k)
                  for k in ("CONFIRMED", "MISSING"))]

    rows = []
    for phase in ("primary", "secondary"):
        keys = sorted(k for k in groups if k[0] == phase)
        for k in keys:
            rows.append(row_for(phase, k[1], groups[k]))
        rows.append(row_for(phase, "(all)", [r for r in records if r["phase"] == phase]))
        rows.append(None)  # blank line between phases
    rows.append(row_for("both", "(all)", records))

    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows if r)) for i, h in enumerate(heads)]
    line = lambda cells: "  ".join(str(c).ljust(widths[i]) if i < 2 else str(c).rjust(widths[i])
                                   for i, c in enumerate(cells))
    print(line(heads))
    print(line(["-" * w for w in widths]))
    for r in rows:
        print("" if r is None else line(r))
    print("\nGrades: Out = Outstanding (1), RI = Requires improvement (3), Inad = Inadequate (4),"
          "\nnone = not yet inspected or independent. Confidence counts are state schools only;"
          "\nindependent schools have no distance or Ofsted confidence.")


def main():
    gias = load("gias-cleaned.json")
    ofsted = load("ofsted-three-boroughs.json")
    london = load("ofsted-london.json")
    dist = load("distances.json")

    pct_table, pct_counts = london_percentiles(london["schools"])
    records, problems = build_records(gias, ofsted, dist, pct_table)

    build_date = datetime.date.today().isoformat()
    out = {
        "buildDate": build_date,
        "sources": {
            "register": {**gias["source"], "snapshot": gias["generatedAt"]},
            "ofsted": {**ofsted["sources"], "snapshot": ofsted["generatedAt"],
                       "reportCardMapping": london["reportCardMapping"],
                       "reportCardAnchor": london["reportCardAnchor"]},
            "distances": {"snapshot": dist["generatedAt"], "yearMeaning": dist["yearMeaning"]},
            "londonPercentile": {
                "method": "Share of graded London schools of the same phase with a worse "
                          "Ofsted grade, plus half of those with the same grade.",
                **pct_counts, "percentileByGrade": pct_table},
        },
        "estimateDefaults": dist["estimateDefaults"],
        "schools": records,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    years = write_csv(records, OUT_CSV)

    state = [r for r in records if not r["isIndependent"]]
    print(f"Wrote {OUT_JSON.relative_to(ROOT)}: {len(records)} records "
          f"({len(state)} state school phases, {len(records) - len(state)} independent), "
          f"buildDate {build_date}")
    print(f"Wrote {OUT_CSV.relative_to(ROOT)}: one row per record, distance columns "
          f"{years[0]}–{years[-1]}")
    print("\nLondon percentile by grade (higher is better):")
    for phase in ("primary", "secondary"):
        c = pct_counts[phase]
        print(f"  {phase}: " + ", ".join(f"{GRADE_WORDS[g]} {pct_table[phase][g]}"
                                         for g in (1, 2, 3, 4))
              + f"  (from {c['gradedSchools']} graded London schools: {c['byGrade']})")
    print()
    print_summary(records)
    if problems:
        print(f"\nPROBLEMS ({len(problems)}):")
        for p in problems:
            print("  " + p)
    else:
        print("\nNo problems: every state school phase had Ofsted and distance data to merge.")
    print("\nFirst three records:")
    for r in records[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
