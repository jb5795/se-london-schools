# Data scripts

These scripts build the school data the app shows. Run them in order, from the main project folder:

```
python3 scripts/01_fetch_gias.py
python3 scripts/02_fetch_ofsted.py
python3 scripts/03_scrape_distances.py
python3 scripts/04_merge_and_flag.py
```

| Script | What it does |
|---|---|
| `01_fetch_gias.py` | Downloads the government's register of schools |
| `02_fetch_ofsted.py` | Downloads Ofsted grades |
| `03_scrape_distances.py` | Collects each school's furthest-admitted distance, year by year |
| `04_merge_and_flag.py` | Combines it all into `data/schools.json` and `data/schools.csv` |

**Status:** all four scripts work.

## 01_fetch_gias.py notes

- It downloads the GIAS file automatically, by filling in the same form a browser would. If GIAS changes its website and that stops working, the script prints step-by-step instructions for downloading the file by hand. You can also pass a downloaded file directly: `python3 scripts/01_fetch_gias.py --file ~/Downloads/edubasealldata20261017.csv`.
- The raw download (about 62 MB) goes into `data/source-snapshots/raw/`. Git ignores that folder because the file is large and can always be downloaded again. If you run the script twice on the same day, the second run reuses that day's file.
- Output: `data/source-snapshots/gias-cleaned.json`, holding one entry per school plus a record of the source file, what was filtered out, and the postcodes.io spot-check.
- What's kept: open schools in Lambeth, Lewisham and Southwark that are primary, secondary, all-through or middle schools, plus mainstream independent schools. Independent special schools, state special schools, pupil referral units, nurseries and 16+ colleges are left out.
- Independent schools have no phase in GIAS, so their phase comes from their age range. A school counts as primary if it takes children aged 7 or under and keeps them to at least 7. It counts as secondary if its age range includes 13.
- Coordinates: converted from the British National Grid with pyproj, accurate to about 2 m.

## 02_fetch_ofsted.py notes

- It downloads two of Ofsted's monthly "latest inspections" CSVs from the gov.uk page: the **newest** one, and the **31 October 2025** one, the last before report cards. The newest file only covers inspections since September 2019. Many schools' last graded inspection was earlier, so their grade comes from the October 2025 file. The two files agree on every London school that appears in both.
- Outputs: `data/source-snapshots/ofsted-london.json` covers all of London, for percentiles. `data/source-snapshots/ofsted-three-boroughs.json` covers our schools, with `ofstedNumeric`, `ofstedFormat` and `ofstedConfidence`.
- How the grade is chosen:
  1. If there's a report card, use it. Its "Curriculum and teaching" grade stands in for the old Quality of education: Exceptional = 1, Strong standard = 2, Expected standard = 2, Needs attention = 3, Urgent improvement = 4. A category of concern, such as special measures, means 4.
  2. Otherwise use the old overall effectiveness grade (1–4).
  3. Inspections from September 2024 to November 2025 gave no overall grade, so for those, use the Quality of education grade.
  4. If there's no graded inspection at all, the grade is `null` and the card says "Not yet inspected".
- `ofstedConfidence`: CONFIRMED if the school's URN is in Ofsted's data (even with no inspection yet), MISSING if it isn't. Independent schools aren't in this dataset, so for them it's `null`.

## 03_scrape_distances.py notes

- It collects each state school's **furthest-admitted distance** for every year it can find, newest first. Sources are tried in this order:
  1. **Lewisham**: the council's application tables (secondary web page, reception grid PDFs), then its distance maps.
  2. **Lambeth**: the "How offers were made on National Offer Day" PDFs and pages.
  3. **Southwark**: the community primary allocation tables and brochures.
  4. **School websites**: each own-admission-authority school's site, for schools with no council figure. Academies, free schools, foundation and voluntary aided schools set their own admissions.

  Older years come from the Internet Archive (web.archive.org).
- **Slow on purpose.** The Internet Archive blocks fast requests, so the script waits 5 seconds between archive requests and longer if it's told to slow down. A first run takes a few hours. Every download is cached in `data/source-snapshots/raw/distances/`, so a re-run takes minutes. To start completely fresh, delete that folder.
- `python3 scripts/03_scrape_distances.py --councils-only` skips the slow school-website step (useful for testing).
- Output: `data/source-snapshots/distances.json`, keyed by URN. For each phase:
  - `distances`: each entry has the year, metres, source title and URL, and the exact text the figure was read from.
  - `distanceMethod`: straight, walking or unknown.
  - `distanceConfidence`: CONFIRMED, or ESTIMATED with the default 1287 m (primary) / 2414 m (secondary).
  - `lastTriedUrl`.

  Each school also has `admissionsNote` and `priorityArea`.
- **Year** means the intake year: "2026" is places offered in spring 2026 for September 2026. The year is read from inside each document, never from the link to it, because councils sometimes link last year's file. Where a document has no year in it (Lambeth's 2025 primary web pages), the year comes from the page address, and the log says so.
- **"All applicants offered"** is stored as `allApplicantsOffered: true` with `metres: null`.
- **Banded and multi-group schools** (e.g. Dunraven, Norwood, La Retraite) have a cut-off per band or group. `metres` is the furthest of them, and every one is listed under `bands`. A school with two sites (Henry Cavendish) is handled the same way.
- **Lewisham's maps** don't say which year they show. A map version is used only when its community-school figures exactly match a dated council table, and a figure left unchanged from an earlier year isn't counted again.
- **Checking a figure:** each entry's `sourceUrl` and `evidence` show where it came from. Figures are kept exactly as published, even odd ones. Some are flagged with a `note`, for example a figure identical to the year before.
- `nonExactNameMatches` lists every place a document's school name was matched to GIAS by anything other than an exact match, so those can be checked by eye. Names that can't be linked by rule (old names of converted schools, odd spellings) are in the `ALIASES` list near the top of the script.

## 04_merge_and_flag.py notes

- It downloads nothing. It combines the four snapshot files into `data/schools.json` (for the app) and `data/schools.csv` (for checking by hand), and takes a second or two. The record layout is documented at the top of the script.
- **One record per school per phase.** The 7 state all-through schools (and 7 independent ones) get a primary record and a secondary record, because Reception and Year 7 have separate distances. Each record's `id` is the URN plus the phase, e.g. `137093-secondary`.
- `distanceConfidence`: CONFIRMED if step 3 found at least one figure, otherwise ESTIMATED with `estimatedMetres` (1287 primary, 2414 secondary). MISSING would mean a school with no map position; there are none at present. Independent schools have `null` for both confidence fields.
- `londonPercentile` is the share of graded London schools of the same phase with a worse Ofsted grade, plus half of those with the same grade. Every school with the same grade gets the same figure. For primary schools: Outstanding 88.7, Good 40.0, Requires improvement 1.3. The app shows it as "Top (100 − percentile)%".
- The CSV has a column for each intake year. A cell holds metres, "all offered", or is blank. It opens directly in Excel or Numbers.
- `fees` is blank for every independent school, because no step collects fees yet.
