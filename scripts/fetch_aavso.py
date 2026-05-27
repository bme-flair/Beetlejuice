#!/usr/bin/env python3
"""Fetch Betelgeuse brightness observations from AAVSO and build daily summaries.

Uses only the Python standard library so it can run in GitHub Actions without dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AAVSO_DATA_URL = "https://vsx.aavso.org/index.php"
DEFAULT_TARGET = "alf Ori"
DEFAULT_REQUESTED_BANDS = "Vis,V"
DEFAULT_SUMMARY_BAND = "Vis"
DEFAULT_DELIMITER = "@@@"

DATA_DIR = Path("data")
OBS_JSON = DATA_DIR / "observations.json"
OBS_CSV = DATA_DIR / "observations.csv"
DAILY_JSON = DATA_DIR / "daily_brightness.json"
DEBUG_TXT = DATA_DIR / "aavso_response_debug.txt"

CSV_FIELDS = [
    "source",
    "target",
    "source_observation_id",
    "magnitude_type",
    "observed_at_utc",
    "date_utc",
    "julian_date",
    "magnitude",
    "magnitude_error",
    "band",
    "observer_code",
    "fetched_at_utc",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def datetime_to_julian_date(value: datetime) -> float:
    return value.astimezone(timezone.utc).timestamp() / 86400.0 + 2440587.5


def julian_date_to_datetime(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc).replace(microsecond=0)


def normalize_header(value: str) -> str:
    text = value.strip().lower()
    for old, new in {"#": "", "/": "_", " ": "_", "-": "_", "(": "", ")": "", ".": ""}.items():
        text = text.replace(old, new)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def normalize_band(value: Any) -> str:
    return str(value or "").strip().rstrip(".").upper()


def parse_band_list(value: str) -> set[str]:
    return {normalize_band(part) for part in value.split(",") if part.strip()}


def first_present(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lstrip("<>").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def observation_key(obs: dict[str, Any]) -> str:
    if obs.get("source_observation_id"):
        return f"{obs['source']}:{obs['source_observation_id']}"
    return ":".join(str(obs.get(part) or "") for part in ("source", "target", "julian_date", "magnitude", "band", "observer_code"))


def load_existing_observations() -> list[dict[str, Any]]:
    if not OBS_JSON.exists():
        return []
    with OBS_JSON.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    return list(payload.get("observations", []))


def build_aavso_url(target: str, from_jd: float, to_jd: float, delimiter: str) -> str:
    params = {
        "view": "api.delim",
        "ident": target,
        "fromjd": f"{from_jd:.5f}",
        "tojd": f"{to_jd:.5f}",
        "delimiter": delimiter,
    }
    return f"{AAVSO_DATA_URL}?{urllib.parse.urlencode(params)}"


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Beetlejuice brightness data fetcher (GitHub Actions)",
            "Accept": "text/plain,text/csv,text/html,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def split_candidate_line(line: str, delimiter: str) -> list[str] | None:
    if delimiter in line:
        return [part.strip() for part in line.split(delimiter)]
    if "," in line:
        try:
            return next(csv.reader([line]))
        except csv.Error:
            return None
    if "\t" in line:
        return [part.strip() for part in line.split("\t")]
    return None


def looks_like_header(parts: list[str]) -> bool:
    normalized = {normalize_header(part) for part in parts}
    return "jd" in normalized and "band" in normalized and ("mag" in normalized or "magnitude" in normalized)


def parse_aavso_delimited(
    text: str,
    *,
    delimiter: str,
    target: str,
    fetched_at: str,
    requested_bands: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, str]] = []
    headers: list[str] | None = None
    candidate_lines = 0
    skipped_malformed = 0
    allowed_bands = parse_band_list(requested_bands)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = split_candidate_line(line, delimiter)
        if not parts:
            continue
        candidate_lines += 1

        if looks_like_header(parts):
            headers = [normalize_header(part) for part in parts]
            continue

        if headers and len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
            continue

        skipped_malformed += 1

    observations: list[dict[str, Any]] = []
    skipped_unparsed = 0
    skipped_band = 0

    for row in rows:
        jd = parse_float(first_present(row, ["jd", "julian_date"]))
        magnitude = parse_float(first_present(row, ["mag", "magnitude"]))
        band = str(first_present(row, ["band", "filter"]) or "").strip()

        if allowed_bands and normalize_band(band) not in allowed_bands:
            skipped_band += 1
            continue
        if jd is None or magnitude is None or not band:
            skipped_unparsed += 1
            continue

        observed_at = julian_date_to_datetime(jd)
        observations.append(
            {
                "source": "AAVSO",
                "target": first_present(row, ["starname", "name"]) or target,
                "source_observation_id": str(first_present(row, ["obsid", "id", "id_number"]) or ""),
                "magnitude_type": str(first_present(row, ["mtype", "magnitude_type"]) or ""),
                "observed_at_utc": isoformat_z(observed_at),
                "date_utc": observed_at.date().isoformat(),
                "julian_date": jd,
                "magnitude": magnitude,
                "magnitude_error": parse_float(first_present(row, ["uncert", "uncertainty", "error", "err"])),
                "band": band,
                "observer_code": str(first_present(row, ["by", "observer_code", "observer"]) or ""),
                "fetched_at_utc": fetched_at,
                "raw": row,
            }
        )

    diagnostics = {
        "response_characters": len(text),
        "response_lines": len(text.splitlines()),
        "delimiter_count": text.count(delimiter),
        "candidate_lines": candidate_lines,
        "parsed_rows_before_validation": len(rows),
        "parsed_observations": len(observations),
        "skipped_malformed_lines": skipped_malformed,
        "skipped_unparsed_rows": skipped_unparsed,
        "skipped_wrong_band_rows": skipped_band,
        "header_detected": headers is not None,
        "detected_headers": headers or [],
        "looks_like_html": "<html" in text[:1000].lower(),
    }
    return observations, diagnostics


def merge_observations(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for obs in existing + new:
        merged[observation_key(obs)] = obs
    return sorted(merged.values(), key=lambda item: (item.get("julian_date") or 0, item.get("source_observation_id") or ""))


def build_daily_summary(observations: list[dict[str, Any]], *, target: str, summary_band: str, generated_at: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    wanted = normalize_band(summary_band)
    for obs in observations:
        if normalize_band(obs.get("band", "")) != wanted:
            continue
        grouped[str(obs["date_utc"])].append(obs)

    days: list[dict[str, Any]] = []
    for date_utc in sorted(grouped):
        values = [float(obs["magnitude"]) for obs in grouped[date_utc]]
        days.append(
            {
                "date_utc": date_utc,
                "source": "AAVSO",
                "target": target,
                "band": summary_band,
                "observation_count": len(values),
                "median_magnitude": round(float(statistics.median(values)), 4),
                "mean_magnitude": round(float(statistics.fmean(values)), 4),
                "min_magnitude": round(min(values), 4),
                "max_magnitude": round(max(values), 4),
            }
        )

    return {
        "metadata": {
            "target": target,
            "source": "AAVSO",
            "band": summary_band,
            "calculation": f"Daily median {summary_band}-band magnitude from parsed AAVSO observations. Lower magnitude means brighter.",
            "generated_at_utc": generated_at,
            "day_count": len(days),
            "observation_count": sum(day["observation_count"] for day in days),
        },
        "days": days,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_csv(path: Path, observations: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for obs in observations:
            writer.writerow({field: obs.get(field, "") for field in CSV_FIELDS})


def write_debug(path: Path, *, url: str, diagnostics: dict[str, Any], text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("AAVSO response debug\n")
        handle.write("====================\n\n")
        handle.write(f"URL: {url}\n\n")
        handle.write("Diagnostics:\n")
        handle.write(json.dumps(diagnostics, indent=2, ensure_ascii=False))
        handle.write("\n\nFirst 5000 response characters:\n")
        sample = text[:5000]
        handle.write(sample)
        if len(text) > len(sample):
            handle.write("\n...[truncated]...\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Betelgeuse brightness observations from AAVSO.")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="AAVSO target identifier, default: alf Ori")
    parser.add_argument("--requested-bands", default=DEFAULT_REQUESTED_BANDS, help="Comma-separated AAVSO bands to keep, default: Vis,V")
    parser.add_argument("--summary-band", default=DEFAULT_SUMMARY_BAND, help="Band to use in daily_brightness.json, default: Vis")
    parser.add_argument("--band", dest="legacy_band", default=None, help="Deprecated alias for --requested-bands and --summary-band")
    parser.add_argument("--lookback-days", type=int, default=365, help="Number of days to fetch, default: 365")
    parser.add_argument("--from-jd", type=float, default=None, help="Override start Julian Date")
    parser.add_argument("--to-jd", type=float, default=None, help="Override end Julian Date")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_at = now_utc()
    fetched_at = isoformat_z(run_at)

    requested_bands = args.legacy_band or args.requested_bands
    summary_band = args.legacy_band or args.summary_band

    to_jd = args.to_jd if args.to_jd is not None else datetime_to_julian_date(run_at + timedelta(days=1))
    from_jd = args.from_jd if args.from_jd is not None else datetime_to_julian_date(run_at - timedelta(days=args.lookback_days))

    url = build_aavso_url(args.target, from_jd, to_jd, DEFAULT_DELIMITER)
    print(f"Fetching {args.target} observations from AAVSO data endpoint")
    print(f"Keeping bands: {requested_bands}; summary band: {summary_band}")
    print(f"Range: JD {from_jd:.5f} to {to_jd:.5f}")

    existing = load_existing_observations()
    text = fetch_text(url)
    new, diagnostics = parse_aavso_delimited(text, delimiter=DEFAULT_DELIMITER, target=args.target, fetched_at=fetched_at, requested_bands=requested_bands)
    observations = merge_observations(existing, new)

    observations_payload = {
        "metadata": {
            "target": args.target,
            "source": "AAVSO",
            "requested_bands": requested_bands,
            "summary_band": summary_band,
            "last_fetch_url": url,
            "last_checked_at_utc": fetched_at,
            "new_observations_seen": len(new),
            "observation_count": len(observations),
            "diagnostics": diagnostics,
        },
        "observations": observations,
    }

    write_json(OBS_JSON, observations_payload)
    write_csv(OBS_CSV, observations)
    write_json(DAILY_JSON, build_daily_summary(observations, target=args.target, summary_band=summary_band, generated_at=fetched_at))
    write_debug(DEBUG_TXT, url=url, diagnostics=diagnostics, text=text)

    print(json.dumps(diagnostics, indent=2))
    print(f"Parsed {len(new)} observations in this fetch")
    print(f"Stored {len(observations)} total observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
