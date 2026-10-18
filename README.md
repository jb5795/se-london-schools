# SE London Schools Finder

A web page for one household. Type in any UK postcode (your home, or a house you're thinking of buying or renting) and it shows every state primary and secondary school in Southwark, Lambeth and Lewisham, on a map and in a list. For each school it shows:

- whether that postcode was **inside or outside** the school's furthest-admitted distance in the most recent admissions round, and by how much;
- a **"how likely"** judgement based on every year of distances found (likely, borderline, unlikely, one year only, or unknown);
- the **Ofsted grade** and inspection date, the school's rank among the schools the postcode reaches, and how its grade compares with all London schools of the same phase;
- a **confidence badge**: green means a published figure was found, amber means no figure was found and a default estimate is used instead.

Independent schools nearby are listed separately. They have no admissions distance.

## Please read: what this can and can't tell you

These boroughs don't have fixed catchment areas. Each year a school fills its places by priority (children in care, siblings, and so on), and then by distance from home. The "furthest-admitted distance" is how far away the last child offered a place under the distance rule lived. It changes every year with the number of applicants, siblings and local births.

- **Being inside a past year's distance does not guarantee a place.** The figures describe what happened in past years. They aren't a promise about next year.
- Faith schools, and schools with banding, lotteries or aptitude places, give some places by other rules. Their cards say so, but the details are in each school's admission policy.
- Distances here are straight lines. A few schools measure the walking route instead, so for those the comparison is approximate (their cards say so).
- Amber schools have no published figure. Their inside or outside result compares against a default estimate (1,287 m for primary, 2,414 m for secondary), not real data.
- Every figure links to the council or school document it came from. Before making a decision, check that document and the council's current admissions brochure.

## Running it on your computer

You need Python 3. Macs usually have it; check by typing `python3 --version` in Terminal.

1. Open Terminal in this folder. In Finder, right-click the folder and choose **New Terminal at Folder**.
2. Type this and press Enter:
   ```
   python3 -m http.server
   ```
3. In your web browser, go to **http://localhost:8000**
4. When you've finished, go back to Terminal and press **Control + C**.

Why the server is needed: browsers won't let a page that you open by double-clicking read its data file. The server makes the folder look like a real website.

## Refreshing the data (once a year)

Councils publish new furthest-admitted distances each spring, after offers are made: around March for secondary schools and April for primary schools. Ofsted updates its data monthly. **May** is a good time to refresh.

**One-time setup** on a new computer: install the libraries the scripts use.
```
pip3 install --only-binary=:all: -r scripts/requirements.txt
```
(`--only-binary=:all:` tells pip to use ready-made packages, because building them from source fails on older Macs. If it says `pip3: command not found`, run `python3 -m ensurepip --upgrade` first.)

**Then run the four scripts, in order:**
```
python3 scripts/01_fetch_gias.py
python3 scripts/02_fetch_ofsted.py
python3 scripts/03_scrape_distances.py
python3 scripts/04_merge_and_flag.py
```

| Script | What it does | Time |
|---|---|---|
| `01_fetch_gias.py` | Downloads the government's register of schools (names, types, locations) | about 1 minute |
| `02_fetch_ofsted.py` | Downloads Ofsted's latest grades for every London school | about 1 minute |
| `03_scrape_distances.py` | Collects each school's furthest-admitted distance from council documents, school websites and the Internet Archive | first run about 1–3 hours (it waits politely between requests); later runs reuse saved copies |
| `04_merge_and_flag.py` | Combines everything into `data/schools.json` (for the app) and `data/schools.csv` (for checking) | seconds |

Each script prints a summary when it finishes. Afterwards, open `data/schools.csv` in Numbers or Excel and look over the **ESTIMATED** rows. Those are the schools where no published figure was found.

When councils publish a new year, `03_scrape_distances.py` may need small changes, for example if a council renames a page or changes its table layout. `scripts/README.md` explains how each script works.

## Putting it online (GitHub Pages)

The app is a single page plus data files, so GitHub Pages can host it free.

**Before you start, note:** with a free GitHub account, the code and the website are **public**. Anyone who has the link (or finds it) can see the page. The page contains only public information about schools. It stores nothing about you, and postcodes you type are looked up by postcodes.io and never saved.

**The first time** (one-time setup):
1. Create a GitHub account and an empty repository.
2. Push this folder to it.
3. In the repository's **Settings → Pages**, choose **Deploy from a branch**, branch `main`, folder `/ (root)`.

The site appears at `https://<your-username>.github.io/<repository-name>/` within a few minutes. `HANDOVER.md` has these steps in detail.

**After each refresh**, publish the new data with:
```
git add -A
git commit -m "Refresh data for 2027"
git push
```
GitHub rebuilds the site by itself, usually within a minute or two.

## What's in this folder

| Item | What it is |
|---|---|
| `index.html` | The app: one web page with plain-English comments throughout |
| `data/schools.json` | The data the app reads, made by script 04. Don't edit it by hand |
| `data/schools.csv` | The same data as a spreadsheet, for checking |
| `data/source-snapshots/` | The intermediate files made by scripts 01–03 |
| `scripts/` | The four data scripts. `scripts/README.md` has technical notes |
| `docs/` | The original brief and design prototype |
| `HANDOVER.md` | A plain-English guide to every file, the yearly refresh, and what to do if something breaks |

Data sources: [Get Information About Schools](https://get-information-schools.service.gov.uk), [Ofsted management information](https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes), and the Lambeth, Lewisham and Southwark admissions documents and school websites linked from each figure. Maps © OpenStreetMap contributors. Postcode lookup by [postcodes.io](https://postcodes.io).
