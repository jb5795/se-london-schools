# Handover notes

A running record of where the project stands, so anyone picking it up (including a future Claude session) knows what's done and what's next.

## Status (17 October 2026)

- Folder structure set up as in `docs/PROJECT_BRIEF.md`, section 6.
- `scripts/01_fetch_gias.py` is done. It writes `data/source-snapshots/gias-cleaned.json` with 257 schools (238 state, 19 independent, as of the 17 Oct 2026 GIAS file). `scripts/02_fetch_ofsted.py` is done. It matched all 238 state schools, and its outputs are `ofsted-london.json` (2,549 London schools) and `ofsted-three-boroughs.json`. `scripts/03_scrape_distances.py` is done (see below). `scripts/04_merge_and_flag.py` is done: `data/schools.json` has 271 records (245 state school phases, 26 independent). `index.html` is built (see "Step 5: the app").
- Decision pending from the owner: report-card "Strong standard" is currently mapped to 2 (Good), the same as "Expected standard". The alternative is to map it to 1.
- `pyproj` is pinned to 3.7.0, because newer versions have no ready-made build for this Mac (Intel, macOS 12). Install the libraries with `pip3 install --only-binary=:all: -r scripts/requirements.txt`.
- All-through schools are split into a primary record and a secondary record (`id` = URN-phase). The app should show an independent all-through school once when both phases are selected.
- Git is initialised on the `main` branch. Nothing has been committed yet.
- Mac setup checked: Python 3.12.2, pip 24.0 and git 2.37.1 all work.

## Step 3: distances (17 October 2026)

- Output is `data/source-snapshots/distances.json`. 166 of 245 school phases are CONFIRMED and 79 are ESTIMATED. All-through schools count twice, once per phase. All of Lambeth is confirmed. The gaps are Lewisham faith and academy primaries (26), Southwark non-community primaries (35) and Southwark secondaries (18). Most of these schools don't publish cut-off distances on their websites.
- 156 confirmed phases have 3 or more years of data. Years run from 2019 to 2026.
- Things the owner should know:
  - **Dunraven:** the brief says it uses walking distance, but Lambeth's documents for Dunraven say "straight line".
  - **Repeated figures:** Lambeth's 2026 primary PDF repeats some 2025 figures exactly. Dunraven primary shows 2492.08 m with the same 243 applications. These figures are kept but flagged with a `note`.
  - **Large Lewisham figures:** some primaries in Lewisham's grids show very large distances, e.g. Launcelot 23,830 m in 2022. These are copied exactly as published. They usually mean the school was undersubscribed.
  - **Not used:** the Lewisham 2018 grid (its columns run together), Lambeth 2018 PDFs (gone, not archived) and Southwark 2018–2021 brochures (damaged archive copies).
  - **Dulwich Hamlet:** it is a junior school, so its figures are for Year 3 entry, not Reception.
  - **Priority areas:** no school published a priority-area map, so `priorityArea` is null everywhere. School-website notes are marked "(from school website; check)" because they're found by keyword.
- Open question for step 4: should a banded school's search use the furthest band (`metres`, current) or the band nearest a typical child?

## Step 5: the app (18 October 2026)

- `index.html` is the prototype with the real data plugged in. It adds:
  - the how-likely band;
  - the inspection date next to the grade;
  - a green, amber or grey confidence badge;
  - a walking-distance flag;
  - an expandable table of distances by year;
  - a "data last updated" line.
- **Prototype bug, fixed:** the prototype's `icon` function was missing a closing `}`, so its script never ran.
- How cards are worked out:
  - A newest year marked "all applicants offered" counts as inside, and that school gets no circle on the map.
  - ESTIMATED schools are placed inside or outside using the default estimate (shown with a dashed circle), but they are **not ranked**.
- Tested in headless Chrome with SE15 3UD, SE22 8QS and SE4 2AA. There were no script errors, and each postcode had 22 or more ranked schools inside.

## Known risks

- The project lives in iCloud Drive. iCloud sometimes interferes with git's hidden `.git` folder. If git starts reporting strange errors, move the project to a folder outside iCloud, such as `~/projects/se-london-schools`.

## Next step

Wait for the owner's testing feedback on `index.html`. Still undecided: which band to use for banded schools, the report-card "Strong standard" mapping, and fees for independent schools.
