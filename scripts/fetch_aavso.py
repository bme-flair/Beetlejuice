#!/usr/bin/env python3
"""Fetch Betelgeuse brightness observations from AAVSO and build daily summaries.

This script intentionally uses only the Python standard library so it can run in a
plain GitHub Actions environment.
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

AAVSO_URL = "https://www.aavso.org/LCGv2/static.htm"
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

FALLBACK_AAVSO_COLUMNS = [
    "id",
    "magnitude_type",
    "name",
    "jd",
    "utc",
    "magnitude",
    "uncertainty",
    "band",
    "obstype",
    "comp",
    "cmag",
    "comp2_check",
    "kmag",
    "airmass",
    "charts",
    "comment_code",
    "software",
    "transformed",
    "comments",
    "digitizer",
    "ads_reference",
    "observer_code",
    "affiliation",
    "credit",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def datetime_to_julian_date(value: datetime) -> float:
    value = value.astimezone(timezone.utc)
    unix_seconds = value.timestamp()
    return unix_seconds / 86400.0 + 2440587.5


def julian_date_to_datetime(jd: float) -> datetime:
    unix_seconds = (jd - 2440587.5) * 86400.0
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).replace(microsecond=0)


def normalize_header(value: str) -> str:
    text = value.strip().lower()
    replacements = {
        "#": "",
        "/": "_",
        " ": "_",
        "-": "_",
        "(": "",
        ")": "",
        ".": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.lstrip("<>").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_observed_at(utc_value: str | None, jd: float | None) -> datetime | None:
    if utc_value:
        text = utc_value.strip().replace("/", "-").replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    if jd is not None:
        return julian_date_to_datetime(jd)
    return None


def observation_key(obs: dict[str, Any]) -> str:
    if obs.get("source_observation_id"):
        return f"{obs['source']}:{obs['source_observation_id']}"
    return ":".join(
        str(obs.get(part) or "")
        for part in ("source", "target", "julian_date", "magnitude", "band", "observer_code")
    )


def load_existing_observations() -> list[dict[str, Any]]:
    if not OBS_JSON.exists():
        return []
    with OBS_JSON.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    return list(payload.get("observations", []))


def build_aavso_url(target: str, requested_bands: str, from_jd: float, to_jd: float, delimiter: str) -> str:
    params = {
        "DateFormat": "Julian",
        "RequestedBands": requested_bands,
        "Grid": "true",
        "view": "api.delim",
        "ident": target,
        "fromjd": f"{from_jd:.5f}",
        "tojd": f"{to_jd:.5f}",
        "delimiter": delimiter,
    }
    return f"{AAVSO_URL}?{urllib.parse.urlencode(params)}"


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


def looks_like_header(parts: list[str]) -> bool:
    normalized = {normalize_header(part) for part in parts}
    return bool({"jd", "magnitude", "band"}.issubset(normalized) or {"julian_date", "magnitude", "band"}.issubset(normalized))


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


def parse_aavso_delimited(text: str, *, delimiter: str, target: str, fetched_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, str]] = []
    headers: list[str] | None = None
    candidate_lines = 0
    skipped_malformed = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = split_candidate_line(line, delimiter)
        if not parts:
            continue
        candidate_lines += 1
        normalized = [normalize_header(part) for part in parts]

        if looks_like_header(parts):
            headers = normalized
            continue

        if headers and len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
            continue

        if not headers and len(parts) >= 8:
            columns = FALLBACK_AAVSO_COLUMNS[: len(parts)]
            rows.append(dict(zip(columns, parts)))
            continue

        skipped_malformed += 1

    observations: list[dict[str, Any]] = []
    skipped_unparsed = 0
    for row in rows:
        jd = parse_float(row.get("jd") or row.get("julian_date"))
        magnitude = parse_float(row.get("magnitude") or row.get("mag"))
        observed_at = parse_observed_at(row.get("utc") or row.get("date"), jd)
        band = row.get("band") or row.get("filter") or ""

        if jd is None or magnitude is None or observed_at is None or not band:
            skipped_unparsed += 1
            continue

        observations.append(
            {
                "source": "AAVSO",
                "target": row.get("name") or target,
                "source_observation_id": row.get("id") or row.get("id_number") or row.get("obsid") or "",
                "magnitude_type": row.get("magnitude_type") or "",
                "observed_at_utc": isoformat_z(observed_at),
                "date_utc": observed_at.date().isoformat(),
                "julian_date": jd,
                "magnitude": magnitude,
                "magnitude_error": parse_float(row.get("uncertainty") or row.get("error") or row.get("err")),
                "band": band,
                "observer_code": row.get("observer_code") or row.get("observer") or "",
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
        "header_detected": headers is not None,
        "detected_headers": headers or [],
    }
    return observations, diagnostics


def merge_observations(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for obs in existing + new:
        merged[observation_key(obs)] = obs
    return sorted(merged.values(), key=lambda item: (item.get("julian_date") or 0, item.get("source_observation_id") or ""))


def median(values: list[float]) -> float:
    return float(statistics.median(values))


def mean(values: list[float]) -> float:
    return float(statistics.fmean(values))


def build_daily_summary(observations: list[dict[str, Any]], *, target: str, summary_band: str, generated_at: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        if str(obs.get("band", "")).upper() != summary_band.upper():
            continue
        if obs.get("magnitude") is None or not obs.get("date_utc"):
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
                "median_magnitude": round(median(values), 4),
                "mean_magnitude": round(mean(values), 4),
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
    sample = text[:5000]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("AAVSO response debug\n")
        handle.write("====================\n\n")
        handle.write(f"URL: {url}\n\n")
        handle.write("Diagnostics:\n")
        handle.write(json.dumps(diagnostics, indent=2, ensure_ascii=False))
        handle.write("\n\nFirst 5000 response characters:\n")
        handle.write(sample)
        if len(text) > len(sample):
            handle.write("\n...[truncated]...\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Betelgeuse brightness observations from AAVSO.")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="AAVSO target identifier, default: alf Ori")
    parser.add_argument("--requested-bands", default=DEFAULT_REQUESTED_BANDS, help="Comma-separated AAVSO bands to request, default: Vis,V")
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

    url = build_aavso_url(args.target, requested_bands, from_jd, to_jd, DEFAULT_DELIMITER)
    print(f"Fetching {args.target} observations from AAVSO")
    print(f"Requested bands: {requested_bands}; summary band: {summary_band}")
    print(f"Range: JD {from_jd:.5f} to {to_jd:.5f}")

    existing = load_existing_observations()
    text = fetch_text(url)
    new, diagnostics = parse_aavso_delimited(text, delimiter=DEFAULT_DELIMITER, target=args.target, fetched_at=fetched_at)
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
