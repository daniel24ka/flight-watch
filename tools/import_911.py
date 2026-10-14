#!/usr/bin/env python3
"""Turn a 9/11 flight of the 11-septembre.com Google Earth file (911maps.kmz) into a replay fixture.

The file has, per flight folder ("American Airlines 77", "United Airlines 93"):
  - "<flight> Ground Projection": a path of one point per second with altitude (m) and no times,
    apparently the NTSB flight path study from the flight data recorder;
  - radar returns (time-stamped Placemarks, ~12 s apart, per radar site): time, position, altitude,
    and the transponder code (`M3: 6553`) in the description; primary returns (transponder off) have
    no code. Their times are US Eastern (EDT, UTC-4) although they are written with a Z.

The path is timed from the radar returns: the nearest path point of each return, a straight line of
time against point number (AA77: 1.000 s a point, UA93: 1.006, ~10 s scatter), shifted so that the
last point is at the impact time (9/11 Commission: AA77 09:37:46, UA93 10:03:11 EDT; the fit alone
ends 18 s / 8 s earlier). Each point takes the transponder code of the radar returns around it.
The switch-off (the last coded return before the first silence of over a minute) is kept in the
fixture's meta `events`, which tools/plot_alert.py draws on the map with the alerts.

The path is written as a CSV and read by tools/import_track.py, which works out speed, track and
vertical rate. With --until-transponder-off the track ends where the code is last seen, as a feed
would have shown it (ADS-B, like the transponder, can be switched off from the cockpit).

Examples:
  python tools/import_911.py 911maps.kmz AA77 --out tests/data/aa77_2001-09-11.json.gz
  python tools/import_911.py 911maps.kmz UA93 --out tests/data/ua93_2001-09-11.json.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import import_track  # noqa: E402

NS = {"k": "http://www.opengis.net/kml/2.2"}
EDT = timedelta(hours=4)
# folder, callsign, registration, type, route, watched airport (code, ICAO, lat, lon), impact (EDT), name
FLIGHTS = {
    "AA77": ("American Airlines 77", "AAL77", "N644AA", "B752", "KIAD-KLAX", ("IAD", "KIAD", 38.9445, -77.4558),
             "09:37:46", "American Airlines 77 (N644AA) Washington Dulles: hijacked, turned back, flown into the Pentagon"),
    "UA93": ("United Airlines 93", "UAL93", "N591UA", "B752", "KEWR-KSFO", ("PIT", "KPIT", 40.4915, -80.2329),
             "10:03:11", "United Airlines 93 (N591UA) Newark: hijacked, turned back, crashed near Shanksville"),
}


def name(e) -> str:
    n = e.find("k:name", NS)
    return re.sub("<[^>]+>", "", n.text or "").strip() if n is not None else ""


def load(path: str) -> ET.Element:
    if path.endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            return ET.fromstring(z.read("doc.kml"))
    return ET.parse(path).getroot()


def returns(folder: ET.Element) -> list[tuple[float, float, float, str | None]]:
    """The radar returns: (UTC epoch, lon, lat, transponder code or None). The code is in the
    description, or only in the radar's folder name ("3743/ZDC": AA77's last 6 min before it went off)."""
    out = []
    in_folder = {p: re.match(r"(\d{4})/", name(f)) for f in folder.iter() for p in f.findall("k:Placemark", NS)}
    for p in folder.iter("{%s}Placemark" % NS["k"]):
        w, co = p.find("k:TimeStamp/k:when", NS), p.find(".//k:Point/k:coordinates", NS)
        if w is None or co is None:
            continue
        d = p.find("k:description", NS)
        code = re.search(r"M3:\s*(\d{4})", re.sub("<[^>]+>", " ", d.text or "") if d is not None else "")
        lon, lat = map(float, co.text.strip().split(",")[:2])
        t = datetime.fromisoformat(w.text.replace("Z", "")).replace(tzinfo=timezone.utc) + EDT
        code = code or in_folder.get(p)
        out.append((t.timestamp(), lon, lat, code.group(1) if code else None))
    return sorted(out)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kml", help="911maps.kmz (or its doc.kml)")
    p.add_argument("flight", choices=sorted(FLIGHTS))
    p.add_argument("--until-transponder-off", action="store_true",
                   help="end the track where the transponder code is last seen")
    p.add_argument("--out", required=True, help="fixture to write (.json.gz)")
    a = p.parse_args(argv)
    folder_name, callsign, reg, typ, route, (code, icao, lat, lon), impact, title = FLIGHTS[a.flight]

    root = load(a.kml)
    folder = next(f for f in root.iter() if f.tag.split("}")[-1] in ("Folder", "Document") and name(f) == folder_name)
    line = next(q for q in folder.iter("{%s}Placemark" % NS["k"]) if "Ground Projection" in name(q))
    path = [tuple(map(float, x.split(",")[:3])) for x in line.find(".//k:coordinates", NS).text.split()]
    radar = returns(folder)

    # time against point number: least squares over the returns within 1.5 km of the path
    cos = math.cos(math.radians(path[0][1]))
    pairs = []
    for t, x, y, _ in radar:
        i = min(range(len(path)), key=lambda k: ((path[k][0] - x) * cos) ** 2 + (path[k][1] - y) ** 2)
        if math.hypot((path[i][0] - x) * cos, path[i][1] - y) * 111_000 < 1500:
            pairs.append((i, t))
    mi, mt = (sum(v) / len(pairs) for v in zip(*pairs))
    slope = sum((i - mi) * (t - mt) for i, t in pairs) / sum((i - mi) ** 2 for i, _ in pairs)
    end = (datetime.fromisoformat(f"2001-09-11T{impact}").replace(tzinfo=timezone.utc) + EDT).timestamp()
    fit_end = mt + slope * (len(path) - 1 - mi)
    times = [end - slope * (len(path) - 1 - i) for i in range(len(path))]
    print(f"{a.flight}: {len(path)} path points, {len(radar)} radar returns ({len(pairs)} matched), "
          f"{slope:.3f} s a point; the fit ends {end - fit_end:+.0f} s from the impact time", file=sys.stderr)

    # transponder code: that of the coded returns within 30 s, none once only primary returns remain.
    # Switched off = the first silence of over a minute in the coded returns (UA93's code shows again
    # on 6 returns at 10:01-10:02 EDT, 21 min later, after the 9/11 Commission's switch-off at 09:41)
    coded = [(t, c) for t, _, _, c in radar if c]
    last_code = next((t for (t, _), (u, _) in zip(coded, coded[1:]) if u - t > 60), coded[-1][0])
    print(f"transponder last seen {datetime.fromtimestamp(last_code - EDT.total_seconds(), timezone.utc):%H:%M:%S} EDT",
          file=sys.stderr)
    rows = []
    for (x, y, z), t in zip(path, times):
        if a.until_transponder_off and t > last_code:
            break
        near = [c for tc, c in coded if abs(tc - t) <= 30 and tc <= last_code + 30]
        rows.append([f"{t:.1f}", f"{y:.5f}", f"{x:.5f}", round(z / 0.3048), near[0] if near else "", callsign])

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, f"{a.flight.lower()}_path.csv")
        with open(src, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "lat", "lon", "alt", "squawk", "callsign"])
            w.writerows(rows)
        import_track.main([src, "--name", title + (" (until the transponder was switched off)"
                                                  if a.until_transponder_off else ""),
                           "--reg", reg, "--type", typ, "--route", route, "--airport", code, "--icao", icao,
                           "--lat", str(lat), "--lon", str(lon), "--source",
                           "911maps.kmz (11-septembre.com): NTSB flight path study, timed from FAA / NEADS radar",
                           "--out", a.out])
    off = min(range(len(path)), key=lambda i: abs(times[i] - last_code))
    with gzip.open(a.out, "rt", encoding="utf-8") as f:
        fixture = json.load(f)
    fixture["meta"]["events"] = [{
        "time": datetime.fromtimestamp(last_code, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "TRANSPONDER_OFF", "lat": round(path[off][1], 5), "lon": round(path[off][0], 5),
        "alt": round(path[off][2] / 0.3048),
        "message": f"transponder code {next(c for t, c in reversed(coded) if t <= last_code)} last received "
                   "(FAA radar); from here the aircraft sent nothing, primary radar returns only"}]
    with gzip.open(a.out, "wt", encoding="utf-8") as f:
        json.dump(fixture, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
