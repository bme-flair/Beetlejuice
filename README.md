# Beetlejuice Brightness Tracker

A small web app that collects daily brightness data for Betelgeuse, also known as Alpha Orionis or `alpha Ori` in AAVSO queries.

## Data source

The first version uses the AAVSO Light Curve Generator static API-style URL. The fetcher requests V-band observations for `alpha Ori`, stores the raw parsed observations, and builds a daily summary.

AAVSO URL pattern used by the script:

```text
https://www.aavso.org/LCGv2/static.htm?DateFormat=Julian&RequestedBands=V&Grid=true&view=api.delim&ident=alpha+Ori&fromjd=...&tojd=...&delimiter=%40%40%40
```

## Data files

- `data/observations.json` - raw parsed observations plus metadata.
- `data/observations.csv` - spreadsheet-friendly copy of the raw parsed observations.
- `data/daily_brightness.json` - one daily summary row per UTC date.

The daily summary uses median V-band magnitude for each date. In astronomy, lower magnitude values mean the star is brighter.

## Updating the data

The GitHub Action in `.github/workflows/update-data.yml` runs once per day and can also be started manually from the Actions tab.

To run it locally:

```bash
python scripts/fetch_aavso.py --lookback-days 365
```

The script uses only the Python standard library.

## Viewing the app locally

Run a simple local web server from the repository root:

```bash
python -m http.server 8000
```

Then open:

```text
http://localhost:8000/
```

## Notes

Betelgeuse has seasonal visibility gaps, so the app may not receive a new observation every calendar day. The fetcher keeps historical data and recomputes the daily summary from all stored observations.
