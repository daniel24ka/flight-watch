"""Our alerts on one flight's track: the readsb trace replayed through the real Monitor (tests/replay.py).

One procedure for every place that analyses a track after the fact:
- tools/squawk_cases.py (the AeroSignals squawks, from ADS-B Exchange's history);
- docs/flight_map.html with `analyze` / "analyse track": the same files run in the browser under Pyodide
  on the trace the page drew (adsb.lol's), loaded from the repository (raw.githubusercontent.com, or
  tools/serve.py's /src/ locally), so the page never has its own copy of the checks.

The flight is the leg around time `t` (airborne between ground stops, as capture_incident.flights), watched
three times: from its origin and from its destination as if each were our airport (TLV with its board;
elsewhere followed from the start, as the monitor follows TLV's board flights anywhere), and from the point at
`t` (followed too; the circle is at most 250 nm). Alerts of one kind within 3 min of each other are one.
Nothing here reads the network: traces and airports are given.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import capture_incident as ci  # noqa: E402
import flight_watch as fw  # noqa: E402
import replay  # noqa: E402
import track_segments  # noqa: E402

DEDUP_S = 180


def join(traces: list[dict]) -> dict | None:
    """readsb traces of one aircraft (trace_full / trace_recent / history days) as one, timestamp 0."""
    rows, info = [], {}
    for d in traces:
        if not d or not d.get("trace"):
            continue
        info = {k: d.get(k) or info.get(k) for k in ("icao", "r", "t", "desc", "dbFlags")}
        rows += [[d["timestamp"] + p[0], *p[1:]] for p in d["trace"]]
    if not rows:
        return None
    rows.sort(key=lambda p: p[0])
    out = [p for i, p in enumerate(rows) if not i or p[0] > rows[i - 1][0]]
    return {**info, "timestamp": 0, "trace": out}


def leg_at(tr: dict, t: float, slack: float = 1800) -> dict | None:
    """The leg around t (or the nearest within `slack` s)."""
    off = lambda f: max(f["start"] - t, t - f["end"], 0)  # noqa: E731
    legs = sorted(ci.flights(tr), key=off)
    return legs[0] if legs and off(legs[0]) <= slack else None


def position_at(tr: dict, t: float) -> list:
    return min((p for p in tr["trace"] if p[1] is not None), key=lambda p: abs(tr["timestamp"] + p[0] - t))


def replay_watch(tr: dict, hexid: str, leg: dict, callsign: str | None, ends: list[dict],
                 home: dict | None, direction: str = "", flight: str = "") -> list[dict]:
    """Our alerts on the flight watched from `home` ({code, icao, lat, lon, radius?, label?}) as its
    departure ("D"), arrival ("A") or neither ("") - tests/replay.py with this one aircraft."""
    t0, t1 = leg["start"], leg["end"] + 900
    # (trace_points keeps 10 min before t0; 2 min is enough before a take-off, and more took in the aircraft's
    # previous landing: TO789 "ORY arrival on the ground 241 nm away" at Norwich, before its flight began)
    pts = ci.trace_points(tr, t0 + 480, t1, 5)
    routes, airports = {}, {}
    if callsign and len(ends) == 2:
        routes[callsign] = f"{ends[0]['icao']}-{ends[1]['icao']}"
    for e in ends:
        airports[e["icao"]] = [e["lat"], e["lon"], e.get("code", ""), e.get("name", "")]
    meta = {"name": callsign or hexid, "airport": home["code"], "start": t0, "end": t1,
            "radius": home.get("radius", 250), "focus": [hexid], "step": 5}
    if home["code"] != "TLV":
        # the monitor follows its airport's flights anywhere (found on the TLV board); elsewhere there is no
        # board, so the flight is followed from the start - or a diversion far from the destination is never
        # seen (LX16 ZRH -> JFK landed at Bangor, ~330 nm out; the circle is at most 250 nm)
        meta["follow"] = [hexid]
    if home["code"] not in fw.AIRPORTS:
        meta.update(airport_coords=[home["lat"], home["lon"]], icao=home["icao"])
    board = []
    m = re.fullmatch(r"([A-Z0-9]{2})(\d{1,4})", flight or "")
    if home["code"] == "TLV" and m and direction:
        other = ends[-1 if direction == "D" else 0].get("code", "?") if len(ends) == 2 else "?"
        when = datetime.fromtimestamp(leg["start" if direction == "D" else "end"], replay_tz()).strftime("%Y-%m-%dT%H:%M")
        board.append({"CHOPER": m.group(1), "CHFLTN": m.group(2), "CHAORD": direction, "CHSTOL": when,
                      "CHPTOL": when, "CHLOC1": other, "CHLOC1D": other,
                      "CHRMINE": "DEPARTED" if direction == "D" else "ON TIME"})
    fixture = {"meta": meta, "board": board, "routes": routes, "airports": airports,
               "aircraft": {hexid: {"r": tr.get("r"), "t": tr.get("t"), "desc": tr.get("desc"), "dbFlags": tr.get("dbFlags"),
                                    "points": pts}}}
    alerts, _, _ = replay.run(fixture)
    return [{**a, "watched_from": home.get("label", home["code"])} for a in alerts if a["hex"] == hexid]


def replay_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Jerusalem")


def analyze(traces: list[dict], hexid: str, t: float, ends: list[dict] | None = None, flight: str = "") -> dict:
    """Our alerts on the flight of aircraft `hexid` around time t.

    traces: readsb trace JSONs of the aircraft (any mix of days / full / recent); ends: [origin,
    destination] as {code (IATA), icao, lat, lon, name}, or fewer when unknown; flight: the IATA flight
    number (board row when an end is TLV). Returns {leg: [start, end], callsign, alerts: [...]} with
    each alert {time (epoch), kind, message, priority, watched_from}, and `segments`: the leg's position jumps,
    steep altitude changes, altitude data glitches and gaps (track_segments.segments: drawn as the incident
    maps draw them), or {error}."""
    tr = join(traces)
    if not tr:
        return {"error": "no trace"}
    leg = leg_at(tr, t)
    if not leg:
        return {"error": "no flight within 30 min of that time in the trace"}
    ends = [e for e in (ends or []) if e and e.get("icao") and e.get("lat") is not None]
    p = position_at(tr, min(max(t, leg["start"]), leg["end"]))
    watches = [({"code": "SQK", "icao": "ZZZZ", "lat": p[1], "lon": p[2], "radius": 250, "label": "near"}, "")]
    if len(ends) == 2:
        watches += [(dict(ends[0]), "D"), (dict(ends[1]), "A")]
    alerts = []
    for home, direction in watches:
        alerts += replay_watch(tr, hexid, leg, leg.get("callsign"), ends if len(ends) == 2 else [], home, direction, flight)
    last, keep = {}, []
    for a in sorted(alerts, key=lambda a: a["time"]):
        at = datetime.fromisoformat(a["time"].replace("Z", "+00:00")).timestamp()
        if at - last.get(a["kind"], -1e18) > DEDUP_S:
            last[a["kind"]] = at
            keep.append({"time": at, "kind": a["kind"], "message": a["message"], "priority": a["priority"],
                         "watched_from": a["watched_from"]})
    air = [p for p in track_segments.points(tr) if leg["start"] - 60 <= p["t"] <= leg["end"] + 60
           and p["alt"] is not None and p["lat"] is not None]
    segs = track_segments.segments(air, bool((tr.get("dbFlags") or 0) & 1))
    return {"leg": [leg["start"], leg["end"]], "callsign": leg.get("callsign"), "alerts": keep, "segments": segs,
            "last": leg["last"], "reg": tr.get("r"), "type": tr.get("t"), "desc": tr.get("desc")}
