# Handover

A plain-English guide to this project: what every file is, how to refresh the data once a year, and what to do if something breaks. You don't need to know how to code to follow it.

- **Live site:** https://jb5795.github.io/se-london-schools/
- **Code on GitHub:** https://github.com/jb5795/se-london-schools (public)
- **On this Mac:** the `se-london-schools` folder in iCloud Drive

## What every folder and file is

**Top level**

| Item | What it is |
|---|---|
| `index.html` | The app itself, one web page that shows the map and list; it reads `data/schools.json`. |
| `README.md` | The project's front page on GitHub: what it does, its limits, and how to run, refresh and publish it. |
| `HANDOVER.md` | This guide. |
| `START_HERE.txt` | The original note from before anything was built (how to install Claude Code); now out of date and safe to ignore. |
| `.gitignore` | A list of files git should never upload (the large raw downloads and some Mac clutter). |
| `.nojekyll` | An empty file that tells GitHub Pages to publish the files exactly as they are. |
| `.git/` | A hidden folder where git keeps the project's history; never edit or delete it. |

**`data/`**: everything the app shows

| Item | What it is |
|---|---|
| `data/schools.json` | The finished data the app reads, made by script 04; never edit it by hand. |
| `data/schools.csv` | The same data as a spreadsheet (one row per school per phase, one column per year) for checking in Numbers or Excel. |
| `data/source-snapshots/` | The in-between files the scripts make, kept so every figure can be traced back. |
| `data/source-snapshots/gias-cleaned.json` | The school register for the three boroughs: names, types, addresses and map positions (from script 01). |
| `data/source-snapshots/ofsted-london.json` | Ofsted grades for every school in London, used for the "Top X% of London" figure (from script 02). |
| `data/source-snapshots/ofsted-three-boroughs.json` | Ofsted grades for our schools only (from script 02). |
| `data/source-snapshots/distances.json` | Every furthest-admitted distance found, with the exact words it was read from and a link to the document (from script 03). |
| `data/source-snapshots/raw/` | Saved copies of every download (about 300 MB); kept on this Mac only, never uploaded, and safe to delete (the scripts just download again, slowly). |

**`scripts/`**: the four programs that rebuild the data

| Item | What it is |
|---|---|
| `scripts/01_fetch_gias.py` | Downloads the government's register of schools and keeps the ones in Lambeth, Lewisham and Southwark. |
| `scripts/02_fetch_ofsted.py` | Downloads Ofsted's latest grades and matches them to our schools. |
| `scripts/03_scrape_distances.py` | Reads council documents, school websites and the Internet Archive to collect each school's furthest-admitted distance year by year. |
| `scripts/04_merge_and_flag.py` | Combines the three snapshots into `data/schools.json` and `data/schools.csv`, and marks each figure as published or estimated. |
| `scripts/requirements.txt` | The list of add-on libraries the scripts need, which pip installs. |
| `scripts/README.md` | Technical notes on how each script works, for whoever next changes one. |

**`docs/`**: where it all started

| Item | What it is |
|---|---|
| `docs/PROJECT_BRIEF.md` | The original specification: the rules the app follows and where the data comes from. |
| `docs/se-london-schools-local.html` | The original design prototype with made-up data (it has a small bug and doesn't run; `index.html` is the fixed, real version). |

## Refreshing the data once a year

Do this in **May**. By then the councils have published the new distances (secondary schools in March, primary in April). Allow an afternoon: most of it is waiting.

1. **Open Terminal in the project folder.** In Finder, right-click the `se-london-schools` folder and choose **New Terminal at Folder**.
2. **Only on a new or reset Mac:** install the add-ons:
   ```
   pip3 install --only-binary=:all: -r scripts/requirements.txt
   ```
3. **Run the four scripts, one at a time.** Wait for each to finish (the prompt comes back) before starting the next:
   ```
   python3 scripts/01_fetch_gias.py
   python3 scripts/02_fetch_ofsted.py
   python3 scripts/03_scrape_distances.py
   python3 scripts/04_merge_and_flag.py
   ```
   Script 03 is the slow one, possibly a few hours, because it waits politely between requests to the Internet Archive. Leave the Mac awake and plugged in.
4. **Check the results.**
   - Look at the summary script 03 prints. The newest year (e.g. 2027) should appear for most Lambeth, Lewisham and Southwark community schools.
   - Look at the summary script 04 prints. Last time (October 2026) it reported **166 published and 79 estimated**. A big drop in published figures means something went wrong. See "If something breaks".
   - Open `data/schools.csv` and look at the newest year's column.
5. **Try the app on this Mac.** Run `python3 -m http.server` and visit http://localhost:8000. Search SE15 3UD and check that the newest year shows on the cards. Press **Control + C** in Terminal to stop the server.
6. **Publish:**
   ```
   git add -A
   git commit -m "Refresh data for 2027"
   git push
   ```
   Wait two minutes, then open the live site. The "Data last updated" date at the bottom of the list should be today. If it isn't, refresh the page.

Ofsted changes monthly. To update just the grades between yearly refreshes, run scripts 02 and 04, then publish (step 6).

## If something breaks

**The easiest fix:** open Terminal in the project folder, type `claude`, and say:
> Read HANDOVER.md and README.md, then help me fix this: *(paste the error message, or describe what you see)*

Claude Code can read the scripts and logs and make the fix. Common problems:

| What you see | What it means / what to do |
|---|---|
| The page says **"Could not load data/schools.json"** | You opened `index.html` by double-clicking it. Use `python3 -m http.server` and http://localhost:8000 instead. |
| The **map is blank** or grey | Usually no internet connection. The map tiles come from OpenStreetMap, and the map library from a public server. |
| **"Postcode not found"** | Check the postcode for typos. Brand-new postcodes can take a few months to reach postcodes.io. |
| **Script 01 says it couldn't download** | The government site has changed. The script prints steps for downloading the file by hand. Then run `python3 scripts/01_fetch_gias.py --file ~/Downloads/<the file>.csv`. |
| **Script 02 can't find the Ofsted file** | gov.uk has renamed or moved it. Ask Claude Code to update the script. |
| **Script 03 finds far fewer figures than before**, or no new year | A council has moved its page or changed its table layout. That's normal every year or two. Ask Claude Code, and give it the summary the script printed. |
| Script 03 seems **stuck**, or says "429" / "slow down" | The Internet Archive is limiting requests. Leave it running. If you stop it, running it again carries on from its saved copies. |
| **pip fails** while installing | Check you used `--only-binary=:all:`. The map-conversion library (`pyproj`) is pinned to version 3.7.0 because newer versions won't install on this Mac. |
| **`git push` asks for a password** or says "authentication failed" | Sign in again: run `gh auth login --hostname github.com --git-protocol https --web` and follow the code steps. `gh` is installed in `~/.local/bin`. |
| `git push` says **"rejected"** or **"fetch first"** | Something was changed on the GitHub website. Run `git pull`, then `git push`. |
| git shows **strange errors** about the `.git` folder | iCloud Drive sometimes interferes with git. Move the project folder somewhere outside iCloud, such as `~/projects/`, and try again. |
| The **live site hasn't updated** after a push | Wait a few minutes, then hard-refresh (Shift + reload). Check the **Actions** tab on GitHub. A red cross shows the publish failed, and clicking it says why. |
| The **new data looks wrong** after publishing | Nothing is lost: git keeps every version. Ask Claude Code to "put the data back to the previous version and push". |

## Things worth knowing about the data

- **Past distances don't guarantee a place.** The app shows what happened in past years.
- **Amber schools** (79 of 245 in October 2026) have no published figure. They're mostly faith schools and academies that don't publish cut-offs. Their inside or outside result uses a default estimate, and they're not ranked.
- **"All applicants offered"** means everyone who applied got in that year, so there was no furthest distance. It counts as inside. It was common in 2026, as fewer children are starting school.
- **Odd-looking figures:** for example, some Lewisham schools show over 10 km. They're copied exactly as the council published them. This usually means the school didn't fill.
- **Dunraven:** the brief says it uses walking distance, but Lambeth's documents say straight line. The app follows the documents.
- **Banded schools** (Dunraven, Norwood, Lilian Baylis) have a different cut-off for each ability band. The app uses the furthest one.

## Decisions still open

- For banded schools, should the app use the furthest band (as now) or something tighter?
- Ofsted report cards: should "Strong standard" count as Outstanding (1)? Currently it's treated as Good (2).
- Fees for independent schools haven't been collected, so their cards just say "No catchment".
