# SE London Schools Finder — Project Brief

Read this whole file before writing any code. The owner is a coding beginner who wants to learn as we go: explain non-obvious code in comments, and in plain English in your replies. Accuracy matters more than speed. Never invent a figure; flag a gap instead.

## 1. What we are building

A private web app for one household. The user enters any UK postcode (their current home, or a house they are considering) and sees, on a map and in a ranked list, every state primary and secondary school in Southwark, Lambeth and Lewisham, with:

- whether that postcode was **inside or outside** each school's furthest-admitted distance in the most recent admissions round, and by how much;
- a **"how likely" indicator** based on several years of furthest-admitted distances where they exist;
- the school's **Ofsted grade**, its rank among the schools the postcode reaches, and its percentile against all London schools of the same phase;
- independent schools nearby, shown separately with no catchment.

The prototype file `se-london-schools-local.html` in this folder is the **design and logic reference**. Its layout, three-section list (inside / outside / independent), inside-outside margin logic, filters, overlays and copy are the spec. Port it faithfully; do not redesign it. Replace its placeholder `SCHOOLS` array with the real dataset.

## 2. Domain facts — do not override these with general knowledge

**Catchment areas largely do not exist here.** Southwark Council states its community schools do not use designated catchment areas; Lambeth and Lewisham work the same way. Places go by an oversubscription hierarchy: looked-after children, exceptional need, siblings, then distance from home to school. The nearest thing to a catchment is therefore the **distance of the furthest child admitted under the distance criterion in a given year**, which each borough publishes annually.

**Definition used throughout:** a postcode is *inside* a school for year Y if the distance from the postcode to the school is less than or equal to that school's furthest-admitted distance for year Y.

**Distance measurement varies.** Most schools use straight-line distance. Some use shortest walking route (Lambeth's Dunraven School is a known example). Record the method per school (`distanceMethod: "straight" | "walking" | "unknown"`). We compute straight-line (Haversine) only; where a school uses walking distance, show a flag on the card saying the comparison is approximate.

**Exceptions to the distance rule:**
- Faith schools: usually a separate faith-based criterion. Distance still applies to remaining places. Flag `isFaith`.
- Schools with partial lottery, banding or aptitude places (Kingsdale, Haberdashers' Hatcham among them). Flag with `admissionsNote`.
- Single-sex schools: flag `gender`.
- Independent schools: no catchment. Show within the search radius, no circle, no rank. Include fees only if found on the school website; otherwise leave blank.
- A school with a genuinely published priority admission area polygon: store the polygon and use point-in-polygon instead of distance. Expect this to be rare.

**"How likely" bands** (state schools only):
- `likely` — inside in every year on record and at least 2 years of data
- `borderline` — inside in some years, outside in others
- `unlikely` — outside in every year on record
- `single-year` — only one year found; show inside/outside for that year and say "one year of data only"
- `unknown` — no distance data; show the school but no judgement

**Ofsted is mid-transition.** Single-word grades (Outstanding / Good / Requires Improvement / Inadequate) are being replaced from late 2025 by report cards with a five-point scale across several areas. Most schools still carry a legacy grade. Store both forms. For ranking, map to `ofstedNumeric` 1–4 (1 best): legacy grade directly; for a report card use the Quality of Education grade mapped to the nearest equivalent and set `ofstedFormat: "report-card"`. Show the inspection date on every card. A school with no inspection gets `null` and is listed with "Not yet inspected".

## 3. Data sources (verified at project start; re-check each URL)

| Need | Source | Notes |
|---|---|---|
| School register | GIAS: https://get-information-schools.service.gov.uk → Downloads → Establishment data (CSV) | Primary key is **URN**. Filter LA codes 208 Lambeth, 209 Lewisham, 210 Southwark; status Open; phases Primary, Secondary, All-through, Middle deemed primary/secondary. Independents are in the same file (TypeOfEstablishment contains "Independent"). |
| Ofsted grades | Monthly management information: https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes | Download latest file; match by URN. Keep the full London extract for percentiles. |
| Furthest distances, Lewisham | Council distance maps: primary https://lewisham.gov.uk/myservices/education/schools/school-admission/applying-to-start-primary-school/primary-school-admission-distance-map and secondary https://lewisham.gov.uk/myservices/education/schools/school-admission/applying-to-start-secondary-school/school-admission-distance-map | Current year on the page; prior years via the council's archived brochures or web.archive.org snapshots. |
| Furthest distances, Lambeth | Annual "Starting primary / secondary school in Lambeth" brochures and "how offers were made" PDFs on lambeth.gov.uk | Tables of furthest distance per community school, one PDF per year. |
| Furthest distances, Southwark | Annual primary and secondary admissions brochures on services.southwark.gov.uk | Same pattern. Non-community schools' criteria are from page ~27 of the primary brochure. |
| Own-admission-authority schools | Each school's website, "Admissions" page, usually a PDF of admission arrangements and sometimes a table of past cut-off distances | Academies, free schools, foundation, voluntary-aided (faith). Cap effort at a few minutes per school; otherwise ESTIMATED. |
| Postcode to coordinates | https://api.postcodes.io (free, no key) | Runtime lookup from the browser, as in the prototype. Also convert GIAS easting/northing with proj4 (OSGB36 → WGS84) for school positions, and spot-check three against the postcode lookup. |
| Map tiles | OpenStreetMap standard tiles | Acceptable for a private low-traffic tool. If the app is ever shared widely, switch to a free-tier provider such as MapTiler or Stadia. |

Prior-year PDFs: try the council site first, then web.archive.org. Record the year, the source document title and URL for every figure.

## 4. Data confidence — non-negotiable

The owner will not enter data by hand. Every state school carries:

- `distanceConfidence`: `CONFIRMED` (figure found in a council or school document, URL recorded) | `ESTIMATED` (no figure found; default 1287 m primary / 2414 m secondary) | `MISSING` (cannot locate the school at all; keep in CSV, exclude from map)
- `ofstedConfidence`: `CONFIRMED` | `MISSING`

Cards show a green/amber/grey badge for distance confidence, and an ESTIMATED school never shows a "how likely" judgement. `data/schools.csv` must be human-readable and sortable so the owner can audit the amber rows.

## 5. Data model (define once in `scripts/04_merge_and_flag.py` as a documented dict shape, and mirror as a comment at the top of index.html)

```ts
type School = {
  urn: number; name: string; phase: "primary" | "secondary";
  type: string; isIndependent: boolean; isFaith: boolean; gender: "mixed" | "boys" | "girls";
  admissionsNote?: string; laName: string; postcode: string; latitude: number; longitude: number;
  websiteUrl?: string; fees?: string;
  ofstedGrade?: string; ofstedNumeric: 1|2|3|4|null; ofstedFormat: "legacy"|"report-card"|null;
  ofstedInspectionDate?: string; ofstedConfidence: "CONFIRMED"|"MISSING"; londonPercentile: number|null;
  distanceMethod: "straight"|"walking"|"unknown";
  distances: { year: string; metres: number; source: string; sourceUrl: string }[]; // newest first
  priorityArea?: GeoJSON.Polygon;
  distanceConfidence: "CONFIRMED"|"ESTIMATED"|"MISSING";
};
```

## 6. Tech — no Node.js anywhere

- **Data scripts:** Python 3 in `/scripts`, run with `python3`. Libraries: requests, pandas, openpyxl, pdfplumber, pyproj (for OSGB36 → WGS84). Install with `pip3 install -r scripts/requirements.txt`. If the Mac's bundled Python lacks pip, say so and give the one-line fix.
- **The app:** a single static `index.html` modelled directly on `se-london-schools-local.html`, loading `data/schools.json` with `fetch`. Plain HTML, CSS and JavaScript; Leaflet from a CDN for the map; postcodes.io for postcode lookup. No framework, no build step, no server. Keep the whole app readable in one file so the owner can learn from it.
- **Hosting:** GitHub Pages, from the repo's main branch. Open locally by running `python3 -m http.server` in the folder (needed because browsers block `fetch` of local files when opened by double-click).
- No database, no accounts, no analytics.

```
index.html
data/ (schools.json, schools.csv, source-snapshots/)
scripts/ (01_fetch_gias.py, 02_fetch_ofsted.py, 03_scrape_distances.py, 04_merge_and_flag.py, requirements.txt, README.md)
docs/ (PROJECT_BRIEF.md, se-london-schools-local.html)
README.md, HANDOVER.md
```

## 7. Definition of done

- `python3 -m http.server` then http://localhost:8000 loads the map; SE15 3UD returns inside/outside results for at least 20 state schools.
- Every card shows grade with date, inside/outside margin, how-likely band, rank, percentile, confidence badge, and any faith/gender/method flags.
- Catchment overlay toggle and search radius work; layout usable on a phone.
- Live on a GitHub Pages URL; README explains setup, the annual data refresh, and the honest caveat that past distances do not guarantee a place.
