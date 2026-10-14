#!/usr/bin/env python3
"""Turn one aircraft's track from a file into a replay fixture (tests/replay.py), or add it to one.

For incidents the ADS-B Exchange history (tools/capture_incident.py) does not cover: before 2020,
places without receivers, or data published by others. Readers (picked from the content):

  fr24        Flightradar24 "granular" CSV (time,,hex,callsign,latitude,longitude,no_position,
              altitude,squawk,speed,track,vspeed) as posted with its incident reports
  fr24-basic  Flightradar24 CSV download (Timestamp,UTC,Callsign,Position,Altitude,Speed,Direction)
  kml         Google Earth tracks: gx:Track (<when> + <gx:coord>) or time-stamped Placemarks
              (FlightAware's export and most compilations); KML altitudes are metres
  csv         any CSV with a header naming time (epoch s or ISO 8601, UTC) and lat/lon, optionally
              alt (ft; --alt-unit m), gs/speed (kt), track/heading, vrate/vspeed (ft/min), squawk,
              callsign/flight. Radar plots and flight-recorder read-outs fit here.

Ground speed, track and vertical rate missing from the file are worked out from the positions
around each one (radar data has none). A transponder switched off is simply where the points stop
or pause, as the feed would show it; a squawk change is a `squawk` column value from that row on.

The watched point (the "airport" of the replay) is --airport with --lat / --lon, else a made-up
point --watch-nm (60) back along the track from its last position, so the end of the flight is
inside the circle but not taken for a landing at the airport.

Examples:
  python tools/import_track.py granular.csv --name "MU5735 dive" --type B738 --reg B-1791 \\
      --out tests/data/mu5735_2022-03-21.json.gz
  python tools/import_track.py ua175.csv --hex aa1750 --add-to tests/data/911.json.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import flight_watch as fw  # noqa: E402

FIELDS = ["t", "lat", "lon", "alt", "gs", "track", "vrate", "flight", "squawk", "emergency", "category"]
FT_PER_M = 3.28084
NAMES = {  # generic CSV: column -> our field (lower case, spaces / units stripped)
    "t": "t", "time": "t", "timestamp": "t", "epoch": "t", "utc": "t", "datetime": "t",
    "lat": "lat", "latitude": "lat", "lon": "lon", "lng": "lon", "long": "lon", "longitude": "lon",
    "alt": "alt", "altitude": "alt", "alt_baro": "alt", "gs": "gs", "speed": "gs", "groundspeed": "gs",
    "track": "track", "heading": "track", "direction": "track", "course": "track",
    "vrate": "vrate", "vspeed": "vrate", "vs": "vrate", "baro_rate": "vrate", "verticalrate": "vrate",
    "squawk": "squawk", "callsign": "flight", "flight": "flight", "hex": "hex", "icao": "hex", "icao24": "hex",
}


def when(text: str) -> float:
    """Epoch seconds from epoch digits or an ISO-like UTC time ("2022-03-21 05:16:42Z.802" too)."""
    text = text.strip()
    if re.fullmatch(r"\d{9,}(\.\d+)?", text):
        return float(text)
    text = re.sub(r"Z\.(\d+)$", r".\1", text).replace("Z", "").replace("T", " ")
    dt = datetime.fromisoformat(text)
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def num(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def read_fr24(rows: list[list[str]]) -> tuple[list[dict], dict]:
    pts, info = [], {}
    for r in rows[1:]:
        if len(r) < 12 or r[6] != "P":  # no_position: N = a report without a position
            continue
        info.setdefault("hex", r[2].lower().removeprefix("0x"))
        pts.append({"t": float(r[1]) if r[1] else when(r[0]), "lat": float(r[4]), "lon": float(r[5]),
                    "alt": num(r[7]), "gs": num(r[9]), "track": num(r[10]), "vrate": num(r[11]),
                    "squawk": r[8] if r[8] not in ("", "0") else None, "flight": r[3] or None})
    return pts, info


def read_fr24_basic(rows: list[list[str]]) -> tuple[list[dict], dict]:
    pts = []
    for r in rows[1:]:
        if len(r) < 7 or "," not in r[3]:
            continue
        lat, lon = (float(x) for x in r[3].split(","))
        pts.append({"t": float(r[0]), "lat": lat, "lon": lon, "alt": num(r[4]), "gs": num(r[5]),
                    "track": num(r[6]), "flight": r[2] or None})
    return pts, {}


def read_csv(rows: list[list[str]], alt_unit: str) -> tuple[list[dict], dict]:
    cols = {}
    for i, name in enumerate(rows[0]):
        key = re.sub(r"\(.*?\)|\[.*?\]|[\s_-]+", "", name.lower()) or name.lower()
        key = {"altbaro": "alt_baro", "barorate": "baro_rate"}.get(key, key)
        if NAMES.get(key) and NAMES[key] not in cols:
            cols[NAMES[key]] = i
    if not {"t", "lat", "lon"} <= set(cols):
        sys.exit(f"the CSV header needs time, lat and lon columns (has: {', '.join(rows[0])})")
    pts, info = [], {}
    for r in rows[1:]:
        get = {k: (r[i].strip() if i < len(r) else "") for k, i in cols.items()}
        if not get["lat"] or not get["lon"] or not get["t"]:
            continue
        if get.get("hex"):
            info.setdefault("hex", get["hex"].lower().removeprefix("0x"))
        alt = num(get.get("alt"))
        if alt is not None and alt_unit == "m":
            alt *= FT_PER_M
        pts.append({"t": when(get["t"]), "lat": float(get["lat"]), "lon": float(get["lon"]), "alt": alt,
                    "gs": num(get.get("gs")), "track": num(get.get("track")), "vrate": num(get.get("vrate")),
                    "squawk": get.get("squawk") or None, "flight": get.get("flight") or None})
    return pts, info


def read_kml(text: str) -> tuple[list[dict], dict]:
    root = ET.fromstring(text)
    tag = lambda e: e.tag.rsplit("}", 1)[-1]  # noqa: E731 - namespaces differ between exporters
    pts = []
    for el in root.iter():
        if tag(el) == "Track":  # gx:Track: <when> elements, then as many <gx:coord>
            whens = [c.text for c in el if tag(c) == "when"]
            coords = [c.text.split() for c in el if tag(c) == "coord"]
            for w, c in zip(whens, coords):
                pts.append({"t": when(w), "lon": float(c[0]), "lat": float(c[1]),
                            "alt": float(c[2]) * FT_PER_M if len(c) > 2 else None})
        elif tag(el) == "Placemark":
            w = next((x.text for x in el.iter() if tag(x) == "when"), None)
            c = next((x.text for x in el.iter() if tag(x) == "coordinates"), None)
            if w and c and len(c.split()) == 1:  # a point, not the drawn line
                c = c.strip().split(",")
                pts.append({"t": when(w), "lon": float(c[0]), "lat": float(c[1]),
                            "alt": float(c[2]) * FT_PER_M if len(c) > 2 else None})
    return pts, {}


def read(path: str, kind: str, alt_unit: str) -> tuple[list[dict], dict, str]:
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    if kind == "auto":
        head = text.lstrip()[:400].lower()
        kind = ("kml" if head.startswith("<") else "fr24" if head.startswith("time,,hex") else
                "fr24-basic" if head.startswith("timestamp,utc,callsign,position") else "csv")
    if kind == "kml":
        pts, info = read_kml(text)
    else:
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
        pts, info = {"fr24": read_fr24, "fr24-basic": read_fr24_basic}.get(kind, lambda r: read_csv(r, alt_unit))(rows)
    # 0,0 is "no position" in some exports (FR24's JT610 CSV: 37 of 836 rows), not a place in the
    # Gulf of Guinea: the map drew jumps there and back across the world
    pts = [p for p in pts if (p["lat"], p["lon"]) != (0, 0)]
    pts = sorted({p["t"]: p for p in pts}.values(), key=lambda p: p["t"])  # one report per time
    # an exact earlier position again after the aircraft had moved on is an old one re-sent with a new
    # time (JT610: one point 11 times over 46 s), not a jump back: drawn as spikes, alerted as a jump
    seen, kept = set(), []
    for p in pts:
        at = (p["lat"], p["lon"])
        if at in seen and (not kept or at != (kept[-1]["lat"], kept[-1]["lon"])):
            continue
        seen.add(at)
        kept.append(p)
    pts = kept
    return pts, info, kind


def derive(pts: list[dict], span: float = 20) -> None:
    """Fill in missing gs (kt), track (deg) and vrate (ft/min) from the positions about `span` s
    around each point (a radar sweep is ~5-12 s; ADS-B rows are noisier one by one)."""
    for i, p in enumerate(pts):
        j = i
        while j > 0 and p["t"] - pts[j - 1]["t"] <= span / 2:
            j -= 1
        k = i
        while k < len(pts) - 1 and pts[k + 1]["t"] - p["t"] <= span / 2:
            k += 1
        if j == k:  # a lone point: its nearest neighbour, if close enough to mean anything
            k = i + 1 if i + 1 < len(pts) and pts[i + 1]["t"] - p["t"] <= 120 else i
            j = i - 1 if k == i and i > 0 and p["t"] - pts[i - 1]["t"] <= 120 else j
        a, b = pts[j], pts[k]
        dt = b["t"] - a["t"]
        if dt <= 0:
            continue
        if p.get("gs") is None:
            p["gs"] = round(fw.haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"]) / dt * 3600, 1)
        if p.get("track") is None and (a["lat"], a["lon"]) != (b["lat"], b["lon"]):
            p["track"] = round(fw.bearing(a["lat"], a["lon"], b["lat"], b["lon"]), 1)
        if p.get("vrate") is None and a.get("alt") is not None and b.get("alt") is not None:
            p["vrate"] = round((b["alt"] - a["alt"]) / dt * 60)


def rows(pts: list[dict], category: str | None) -> list[list]:
    """Fixture rows; flight / squawk / category only where they change (as capture_incident.py)."""
    out, state = [], {}
    for p in pts:
        alt = p.get("alt")
        changed = {k: p.get(k) for k in ("flight", "squawk") if p.get(k) and state.get(k) != p.get(k)}
        if not out and category:
            changed["category"] = category
        state.update(changed)
        out.append([round(p["t"], 1), round(p["lat"], 5), round(p["lon"], 5),
                    "ground" if alt is not None and alt <= 0 and (p.get("gs") or 0) < 50
                    else None if alt is None else int(round(alt)),
                    None if p.get("gs") is None else round(p["gs"], 1),
                    None if p.get("track") is None else round(p["track"] % 360, 1),
                    None if p.get("vrate") is None else int(round(p["vrate"])),
                    *(changed.get(k) for k in ("flight", "squawk", "emergency", "category"))])
    return out


def watch_point(pts: list[dict], nm: float) -> tuple[float, float]:
    """The position `nm` back along the track from its end (the first one, if it is shorter)."""
    flown = 0.0
    for p, q in zip(reversed(pts[:-1]), reversed(pts[1:])):
        flown += fw.haversine_nm(p["lat"], p["lon"], q["lat"], q["lon"])
        if flown >= nm:
            return round(p["lat"], 4), round(p["lon"], 4)
    return round(pts[0]["lat"], 4), round(pts[0]["lon"], 4)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("track", help="the track file (CSV or KML)")
    p.add_argument("--format", default="auto", choices=("auto", "fr24", "fr24-basic", "kml", "csv"))
    p.add_argument("--alt-unit", default="ft", choices=("ft", "m"), help="altitude unit of a generic CSV")
    p.add_argument("--hex", help="ICAO address (default: the file's, else made up from --callsign)")
    p.add_argument("--callsign", help="callsign for rows without one")
    p.add_argument("--reg", help="registration")
    p.add_argument("--type", help="ICAO type code, e.g. B738")
    p.add_argument("--category", default="A3", help="ADS-B emitter category (A3: large airliner)")
    p.add_argument("--start", help="drop points before this UTC time (ISO)")
    p.add_argument("--end", help="drop points after this UTC time (ISO)")
    p.add_argument("--airport", default="XXX", help="code of the watched point (any)")
    p.add_argument("--icao", help="its ICAO code, when it is a real airport (routes)")
    p.add_argument("--lat", type=float, help="watched point (default: --watch-nm back along the track)")
    p.add_argument("--lon", type=float)
    p.add_argument("--watch-nm", type=float, default=60)
    p.add_argument("--radius", type=float, default=250, help="nm around the watched point (max 250)")
    p.add_argument("--route", help="ICAO-ICAO the flight was on, e.g. ZPPP-ZGGG")
    p.add_argument("--name", default="")
    p.add_argument("--source", help="where the data came from (kept in the fixture)")
    p.add_argument("--add-to", metavar="FIXTURE", help="add the aircraft to this fixture (rewritten in place)")
    p.add_argument("--out", help="fixture to write (.json.gz)")
    a = p.parse_args(argv)
    if not (a.out or a.add_to):
        p.error("--out or --add-to is required")

    pts, info, kind = read(a.track, a.format, a.alt_unit)
    if a.start:
        pts = [q for q in pts if q["t"] >= when(a.start)]
    if a.end:
        pts = [q for q in pts if q["t"] <= when(a.end)]
    if len(pts) < 2:
        sys.exit(f"{a.track}: fewer than 2 positions read ({kind})")
    for q in pts:
        q["flight"] = q.get("flight") or a.callsign
    derive(pts)
    callsign = next((q["flight"] for q in pts if q.get("flight")), None)
    made_up = "f" + hashlib.sha1((callsign or a.track).encode()).hexdigest()[:5]  # stable between runs
    hexid = (a.hex or info.get("hex") or made_up).lower()
    aircraft = {"r": a.reg, "t": a.type, "points": rows(pts, a.category)}
    source = a.source or f"{os.path.basename(a.track)} ({kind})"

    if a.add_to:
        with gzip.open(a.add_to, "rt", encoding="utf-8") as f:
            fixture = json.load(f)
        m = fixture["meta"]
        m["start"], m["end"] = min(m["start"], pts[0]["t"] + 300), max(m["end"], pts[-1]["t"] + 300)
        m["focus"] = sorted(set(m["focus"]) | {hexid})
        m["source"] = f"{m['source']}; {source}"
        out = a.add_to
    else:
        lat, lon = (a.lat, a.lon) if a.lat is not None and a.lon is not None else watch_point(pts, a.watch_nm)
        day = datetime.fromtimestamp(pts[0]["t"], timezone.utc)
        m = {"name": a.name, "airport": a.airport.upper(), "airport_coords": [lat, lon], "date": f"{day:%Y-%m-%d}",
             # the replay starts 5 min before the first point (warm-up) and runs 5 min past the last
             "start": pts[0]["t"] + 300, "end": pts[-1]["t"] + 300, "radius": min(a.radius, 250),
             "focus": [hexid], "step": 0, "source": source, "point_fields": FIELDS}
        if a.icao:
            m["icao"] = a.icao.upper()
        fixture = {"meta": m, "board": [], "aircraft": {}, "routes": {}, "airports": {}}
        out = a.out
    fixture["aircraft"][hexid] = aircraft
    if a.route and callsign:
        fixture["routes"][callsign] = a.route.upper()
        fixture["meta"].setdefault("route_overrides", {})[callsign] = a.route.upper()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(fixture, f, separators=(",", ":"))
    far = max(fw.haversine_nm(*fixture["meta"]["airport_coords"], q["lat"], q["lon"]) for q in pts) \
        if "airport_coords" in fixture["meta"] else None
    print(f"wrote {out}: {hexid} {callsign or '?'}, {len(pts)} points ({kind}), "
          f"{datetime.fromtimestamp(pts[0]['t'], timezone.utc):%Y-%m-%d %H:%M:%S}-"
          f"{datetime.fromtimestamp(pts[-1]['t'], timezone.utc):%H:%M:%S} UTC"
          + (f", up to {far:.0f} nm from the watched point" if far is not None else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
