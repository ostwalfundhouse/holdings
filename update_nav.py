"""Update every client data.json with the latest NAVs from AMFI.

Covers the original page (data.json, a flat "holdings" list) and every client
page under p/<id>/data.json (a list of "investors", each with "holdings").

Sources: AMFI's mutual fund NAV file (NAVAll.txt) and SIF NAV files.
Rules: match each holding by AMFI scheme code AND ISIN; update only when AMFI
shows a later NAV date; skip any move larger than 10%.
"""
import csv
import glob
import io
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

MF_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
SIF_URLS = [
    "https://portal.amfiindia.com/spages/SIF_NAVAll.txt",
    "https://portal.amfiindia.com/spages/SIF_NAVInterval.txt",
]
SIF_FALLBACK_CSV = "https://sif.tigzig.com/sif_meta.csv"  # third-party copy of AMFI SIF data
DATE_FMT = "%d-%b-%Y"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_amfi(text):
    """Return {(code, isin): (nav, date)} from an AMFI semicolon-separated file."""
    out = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) != 8:
            continue
        code = parts[0]
        if not (code.isdigit() or code.startswith("SIF-")):
            continue
        try:
            nav, date = float(parts[6]), datetime.strptime(parts[7], DATE_FMT)
        except ValueError:
            continue
        for isin in (parts[1], parts[2]):
            if isin and isin != "-":
                out[(code, isin)] = (nav, date)
    return out


def parse_sif_fallback(text):
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            out[("SIF-" + row["scheme_code"].strip(), row["isin"].strip())] = (
                float(row["nav"]), datetime.strptime(row["nav_date"], "%Y-%m-%d"))
        except (KeyError, ValueError):
            continue
    return out


def load_navs():
    navs = {}
    for url in [MF_URL] + SIF_URLS:
        try:
            got = parse_amfi(fetch(url))
            print(f"::notice::Read {len(got)} NAVs from {url}")
            for k, v in got.items():
                if k not in navs or v[1] > navs[k][1]:
                    navs[k] = v
        except Exception as exc:  # noqa: BLE001
            print("Could not read", url, "-", exc)
    try:
        for k, v in parse_sif_fallback(fetch(SIF_FALLBACK_CSV)).items():
            navs.setdefault(k, v)
    except Exception as exc:  # noqa: BLE001
        print("Could not read SIF fallback -", exc)
    return navs


def holdings_of(data):
    if "holdings" in data:
        yield from data["holdings"]
    for inv in data.get("investors", []):
        yield from inv["holdings"]


def update_file(path, navs):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    changed = False
    for h in holdings_of(data):
        key = (h["amfiCode"], h["isin"])
        if key not in navs:
            print(f'::warning::{path}: {h["name"]} ({key}) not found in any NAV source.')
            continue
        nav, date = navs[key]
        current = datetime.strptime(h["navDate"], DATE_FMT)
        if date <= current:
            continue
        if abs(nav - h["nav"]) / h["nav"] > 0.10:
            print(f'::warning::{path}: {h["name"]} NAV {nav} moves more than 10% from {h["nav"]}; skipped.')
            continue
        h["nav"], h["navDate"] = nav, date.strftime(DATE_FMT)
        changed = True
        print(f'::notice::{path}: {h["name"]} -> {nav} as of {h["navDate"]}')
    if changed:
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        data["updatedOn"] = ist.strftime(DATE_FMT)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return changed


def main(*paths):
    paths = list(paths) or ["data.json"] + sorted(glob.glob("p/*/data.json"))
    navs = load_navs()
    if not navs:
        print("::error::No NAV source could be read; nothing changed.")
        return 1
    for p in paths:
        update_file(p, navs)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
