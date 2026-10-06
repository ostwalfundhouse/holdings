"""Update data.json with the latest NAVs from AMFI's official SIF NAV file.

Rules: match each holding by AMFI scheme code AND ISIN; update only when AMFI
shows a later NAV date; skip any move larger than 10%.
"""
import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

AMFI_URLS = [
    "https://portal.amfiindia.com/spages/SIF_NAVInterval.txt",
    "https://portal.amfiindia.com/spages/SIF_NAVAll.txt",
]
FALLBACK_CSV = "https://sif.tigzig.com/sif_meta.csv"  # third-party copy of AMFI data
DATE_FMT = "%d-%b-%Y"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_amfi(text):
    """Return {(code, isin): (nav, date)} from AMFI's semicolon-separated file."""
    out = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) != 8 or not parts[0].startswith("SIF-"):
            continue
        try:
            out[(parts[0], parts[1])] = (float(parts[6]), datetime.strptime(parts[7], DATE_FMT))
        except ValueError:
            continue
    return out


def parse_fallback(text):
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            out[("SIF-" + row["scheme_code"].strip(), row["isin"].strip())] = (
                float(row["nav"]), datetime.strptime(row["nav_date"], "%Y-%m-%d"))
        except (KeyError, ValueError):
            continue
    return out


def latest_navs(wanted):
    for url in AMFI_URLS:
        try:
            navs = parse_amfi(fetch(url))
            if all(k in navs for k in wanted):
                print("::notice::NAV source:", url)
                return navs
            print("Missing holdings in", url)
        except Exception as exc:  # noqa: BLE001
            print("Could not read", url, "-", exc)
    try:
        navs = parse_fallback(fetch(FALLBACK_CSV))
        if all(k in navs for k in wanted):
            print("::notice::NAV source (fallback copy of AMFI data):", FALLBACK_CSV)
            return navs
    except Exception as exc:  # noqa: BLE001
        print("Could not read fallback -", exc)
    return None


def main(path="data.json"):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    wanted = [(h["amfiCode"], h["isin"]) for h in data["holdings"]]
    navs = latest_navs(wanted)
    if navs is None:
        print("::error::No NAV source could be read; nothing changed.")
        return 1
    changed = False
    for h in data["holdings"]:
        nav, date = navs[(h["amfiCode"], h["isin"])]
        current = datetime.strptime(h["navDate"], DATE_FMT)
        if date <= current:
            print(f'::notice::{h["name"]}: no newer NAV (source has {nav} as of {date:%d-%b-%Y}).')
            continue
        if abs(nav - h["nav"]) / h["nav"] > 0.10:
            print(f'::warning::{h["name"]}: NAV {nav} moves more than 10% from {h["nav"]}; skipped.')
            continue
        h["nav"], h["navDate"] = nav, date.strftime(DATE_FMT)
        changed = True
        print(f'::notice::{h["name"]}: updated to {nav} as of {h["navDate"]}; value {h["units"] * nav:,.2f}')
    if changed:
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        data["updatedOn"] = ist.strftime(DATE_FMT)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
