# Beetlejuice Brightness Tracker

A small web app that collects daily brightness data for Betelgeuse, also known as Alpha Orionis. The AAVSO identifier used by the fetcher is `alf Ori`.

## Data source

The first version uses AAVSO data loaded through the VSX/AAVSO delimited endpoint used by the AAVSO static light-curve page. The fetcher requests observations for `alf Ori`, keeps visual and V-band rows, stores the raw parsed observations, and builds a daily summary from the selected summary band.

AAVSO data URL pattern used by the script:

```text
https://vsx.aavso.org/index.php?view=api.delim&ident=alf+Ori&fromjd=...&tojd=...&delimiter=%40%40%40
```

## Data files

- `data/observations.json` - raw parsed observations plus metadata.
- `data/observations.csv` - spreadsheet-friendly copy of the raw parsed observations.
- `data/daily_brightness.json` - one daily summary row per UTC date.
- `data/brightness_plot.svg` - static graph generated with R and ggplot2.

The daily summary currently uses median `Vis` magnitude for each date, because Betelgeuse has many visual observations. In astronomy, lower magnitude values mean the star is brighter.

## Updating the data

The GitHub Action in `.github/workflows/update-data.yml` runs once per day and can also be started manually from the Actions tab. It fetches observations, regenerates the JSON/CSV files, generates the SVG graph with R, and commits any changes.

To run the data fetch locally:

```bash
python scripts/fetch_aavso.py --lookback-days 365 --target "alf Ori" --requested-bands "Vis,V" --summary-band "Vis"
```

To regenerate the graph locally, install `ggplot2` in R and run:

```bash
Rscript scripts/plot_brightness.R data/observations.csv data/brightness_plot.svg Vis
```

The Python fetcher uses only the Python standard library. The graph script requires R and `ggplot2`.

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
