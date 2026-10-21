"""The track's segments that are not plain flight: position jumps, steep altitude changes, altitude data
glitches and gaps without positions - drawn in their own colours by tools/plot_alert.py (the incident maps)
and by docs/flight_map.html, which gets them from tools/track_alerts.py run in the browser (Pyodide), so both
pages draw them by the same rules. Plain Python and flight_watch only: no plotting, no network.

Times in texts are T() placeholders, formatted by the page in the zone the viewer picks.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import flight_watch as fw  # noqa: E402

GAP = 120          # longer without positions is a gap, not a jump (as in flight_watch.check_jump)
FT, KT, NM = 0.3048, 1.852, 1.852   # -> m, km/h, km


def T(t: float, f: str = "hms") -> str:
    """A time for the page: formatted in the browser in the zone the viewer picks (Israel by
    default, UTC, their own). f: hm, hms, dhm (date and time), z (zone name)."""
    return f"⟦{f}:{t:.3f}⟧"



def points(tr: dict) -> list[dict]:
    """Trace rows -> dicts. Row: [dt, lat, lon, alt|"ground"|None, gs, track, flags, vrate,
    {details}|None, source, geometric alt, geometric rate, ...]; details (callsign, squawk) appear
    only when they change. Flags: 1 stale position, 2 new leg, 4 vertical rate is geometric, 8 the
    altitude is geometric (GPS): readsb fills in the GPS altitude when the barometric one is missing.
    That is converted to barometric with the nearest offset seen (4XDAN, 10 Oct 2026: GPS 575 ft
    higher, one GPS value among barometric ones read as a 575-ft step in half a second)."""
    rows = tr.get("trace", [])
    offsets = [(r[0], r[10] - r[3]) for r in rows if len(r) > 10 and isinstance(r[10], (int, float))
               and isinstance(r[3], (int, float)) and isinstance(r[6], int) and not r[6] & 8]
    out, state = [], {}
    for p in rows:
        ex = p[8] if len(p) > 8 and isinstance(p[8], dict) else {}
        alt, geo = p[3], isinstance(p[6], int) and p[6] & 8 and isinstance(p[3], (int, float))
        if geo:
            near = min(offsets, key=lambda o: abs(o[0] - p[0]), default=None)
            alt = round(alt - near[1]) if near and abs(near[0] - p[0]) <= 600 else None
        for k in ("flight", "squawk"):
            if ex.get(k):
                state[k] = str(ex[k]).strip()
        if isinstance(ex.get("nic"), int):  # the aircraft's position integrity (0: it distrusts it)
            state["nic"] = ex["nic"]
        src = p[9] if len(p) > 9 and p[9] else ex.get("type") or "?"
        out.append({"t": tr["timestamp"] + p[0], "lat": p[1], "lon": p[2],
                    "alt": 0 if p[3] == "ground" else alt, "ground": p[3] == "ground", "geo": bool(geo),
                    "gs": p[4], "track": p[5], "new_leg": bool(p[6] & 2) if isinstance(p[6], int) else False,
                    "vrate": p[7], "src": "mlat" if "mlat" in src else "adsb" if "adsb" in src else src,
                    **state})
    return out



def jumps(air: list[dict], max_speed: float, gap_speed: float) -> dict[int, str]:
    """Hops (index of their end point -> description) that break flight_watch's POSITION_JUMP rules:
    too fast between close positions, reappearing too far after a gap, one position off and back."""
    def hop(i):
        p, q = air[i - 1], air[i]
        return q["t"] - p["t"], fw.haversine_nm(p["lat"], p["lon"], q["lat"], q["lon"])
    out = {}
    for i in range(1, len(air)):
        dt, d = hop(i)
        if dt <= 0 or d < 2:
            continue
        kt, at = d / (dt / 3600), T(air[i]["t"])
        if dt <= GAP and kt > max_speed:
            out[i] = f"jump: {d * NM:.1f} km in {dt:.0f} s (~{kt * KT:,.0f} km/h) at {at}"
        elif dt > GAP and kt > gap_speed:
            out[i] = (f"reappeared {d * NM:.1f} km away after {dt / 60:.1f} min without position "
                      f"(~{kt * KT:,.0f} km/h) at {at}")
        elif i >= 2 and i - 1 not in out:
            a, b, c = air[i - 2], air[i - 1], air[i]
            span, ab = c["t"] - a["t"], hop(i - 1)[1]
            ac = fw.haversine_nm(a["lat"], a["lon"], c["lat"], c["lon"])
            if span > 0 and min(ab, d) >= 5 and (ab + d) / (span / 3600) > gap_speed \
                    and ac / (span / 3600) <= gap_speed:
                info = (f"one position {min(ab, d) * NM:.1f} km off the track and back "
                        f"(~{(ab + d) / (span / 3600) * KT:,.0f} km/h via it) at {T(b['t'])}")
                out[i - 1] = out[i] = info
    return out


STEEP = 8000     # ft/min: altitude changing faster than this between positions (flight_watch --vrate)
STEEP_FT = 300   # ...by at least this much (25-ft steps a fraction of a second apart are not)
PURPLE = [155, 48, 217]
# A steep change is a data glitch when the altitudes moved over 3x faster than the aircraft's own
# vertical rate said while that stayed normal (<= 12,000 ft/min). 4XDAN, 10 Oct 2026: 10,325 -> 9,400 ft
# in 0.4 s (readsb holding a stale altitude after a reception gap) while it reported a steady -8,192;
# FZ1073's wobble is real - its own rate swung to +22,976 / -19,968 ft/min.
GLITCH_RATE, GLITCH_X, GLITCH_RGB = 12000, 3, [214, 112, 214]


def steep(air: list[dict]) -> tuple[set, list[dict]]:
    """Altitude changing faster than airliners fly (a wobble, a dive, a glitch): the hops (index of
    their end point) and clusters of them (hops <= 10 s apart) with a short description."""
    hops = set()
    for i in range(1, len(air)):
        dt, dz = air[i]["t"] - air[i - 1]["t"], air[i]["alt"] - air[i - 1]["alt"]
        if 0 < dt <= GAP and abs(dz) >= STEEP_FT and abs(dz) / dt * 60 > STEEP:
            hops.add(i)
    clusters = []
    for i in sorted(hops):
        if clusters and air[i - 1]["t"] - air[clusters[-1]["end"]]["t"] <= 10:
            clusters[-1]["end"] = i
        else:
            clusters.append({"start": i - 1, "end": i})
    for c in clusters:
        alts = [air[k]["alt"] for k in range(c["start"], c["end"] + 1)]
        turns = [alts[0]] + [b for a, b, d in zip(alts, alts[1:], alts[2:]) if (b - a) * (d - b) < 0] + [alts[-1]]
        rate = max(abs(air[k]["alt"] - air[k - 1]["alt"]) / max(air[k]["t"] - air[k - 1]["t"], 0.1) * 60
                   for k in range(c["start"] + 1, c["end"] + 1) if k in hops)
        dt = air[c["end"]]["t"] - air[c["start"]]["t"]
        c["t"] = air[c["start"]]["t"]
        own = [abs(air[k]["vrate"]) for k in range(max(0, c["start"] - 1), min(len(air), c["end"] + 2))
               if isinstance(air[k]["vrate"], int)]
        c["glitch"] = bool(own) and max(own) <= GLITCH_RATE and rate > GLITCH_X * max(max(own), 2000)
        c["text"] = (f"altitude {' → '.join(f'{a * FT:,.0f}' for a in turns)} m in {dt:.0f} s "
                     f"(up to {rate * FT / 60:,.0f} m/s) at {T(c['t'])}")
        if c["glitch"]:
            c["text"] = (f"altitude data glitch: {c['text'][9:]}, while the aircraft's own vertical rate "
                         f"stayed within {max(own) * FT / 60:,.0f} m/s")
    return hops, clusters



def segments(air: list[dict], military: bool = False, max_speed: float = 1200, gap_speed: float = 750) -> list[dict]:
    """The hops between consecutive positions of `air` (points() with an altitude) that are a position jump,
    a steep altitude change, an altitude data glitch or a gap: {i (index of its end), kind, text, a, b}, a / b
    the hop's ends as {t, lat, lon, alt}. A hop that is a jump is not also steep (as plot_alert draws them)."""
    if military:
        gap_speed = max_speed  # as the monitor: jets may really be that fast
    bad = jumps(air, max_speed, gap_speed)
    vert, clusters = steep(air)
    end = lambda p: {k: p[k] for k in ("t", "lat", "lon", "alt")}  # noqa: E731
    out = []
    for i in range(1, len(air)):
        p, q = air[i - 1], air[i]
        if i in bad:
            kind, text = "jump", bad[i]
        elif i in vert:
            c = next(c for c in clusters if c["start"] < i <= c["end"])
            kind, text = ("glitch" if c["glitch"] else "steep"), c["text"]
        elif q["t"] - p["t"] > GAP:
            d, dt = fw.haversine_nm(p["lat"], p["lon"], q["lat"], q["lon"]), q["t"] - p["t"]
            kind, text = "gap", (f"no positions {T(p['t'])}-{T(q['t'])} ({dt / 60:.1f} min): "
                                 f"{d * NM:.0f} km, average {d / (dt / 3600) * KT:,.0f} km/h")
        else:
            continue
        out.append({"i": i, "kind": kind, "text": text, "a": end(p), "b": end(q)})
    return out
