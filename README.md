# Bushfire Air Tracker

A static website (no server, no database) that lists every aircraft in `data/2026_Fire_Season.xlsx`,
shows bases and live ADS-B positions on a map, and sends a phone notification when the NAFC call sign list changes.

```
site/                      the website (this folder is what gets published)
  index.html               the whole app
  data/aircraft.json       generated from the spreadsheet
  data/nafc_callsigns.json created by the watcher on its first run
  photos/                  your photos, named by rego
scripts/build_data.py      spreadsheet + photos -> aircraft.json
scripts/check_callsigns.py NAFC watcher (runs in GitHub Actions)
.github/workflows/         schedule + publishing
```

## Try it on your computer
```
cd site
python3 -m http.server 8000      # then open http://localhost:8000
```

## Put it online (free, GitHub Pages) and turn on alerts
1. Create a GitHub account and a new repository (public is simplest; private also works).
2. Upload this whole folder to it (GitHub's "Add file > Upload files" works, or use git).
3. Repo **Settings > Pages > Build and deployment > Source: GitHub Actions**.
4. Install the free **ntfy** app on your phone and subscribe to a topic name nobody can guess,
   for example `bushfire-air-k3j29x`. Anyone who knows the topic can read it, so make it long.
5. Repo **Settings > Secrets and variables > Actions**:
   - Secrets tab: new secret `NTFY_TOPIC` = your topic name.
   - Variables tab (optional): new variable `SITE_URL` = your site address, so tapping the notification opens it.
6. **Actions** tab > "Watch NAFC call signs and publish site" > **Run workflow**.
   The first run saves a baseline and sends "NAFC watcher started". After that it runs every 2 hours
   (edit the `cron` line in the workflow to change that) and notifies you only when something changes.

If NAFC changes its page layout and the watcher can no longer read it, you get an "NAFC watcher error"
notification rather than silence.

## Updating the data
- Edit the spreadsheet, then run `pip install openpyxl` once and `python3 scripts/build_data.py`.
  It tells you if a base in the sheet has no coordinates; add it to the `BASES` list at the top of the script.
- Photos: save as `site/photos/VH-XXX.jpg` (also .jpeg .png .webp). Extra shots: `VH-XXX-2.jpg`, `VH-XXX-3.jpg`.
  Re-run `build_data.py` so the site knows they exist.
- Base coordinates are approximate (airport reference points or town centres). Fix any that matter in `BASES`.

## Live map notes
The browser asks airplanes.live (and falls back to adsb.lol) for your registrations every 20 seconds.
Those are free community feeds, so please keep the refresh rate modest. Helicopters without ADS-B, or
with it switched off on the ground, will not appear. The green dot bottom-left of the map shows feed status.
