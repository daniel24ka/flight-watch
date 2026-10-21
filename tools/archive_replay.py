#!/usr/bin/env python3
"""Replay the monitor's own past alerts on today's code, to measure a change before it goes live.

adsb.lol answers its trace files with a browser check since 18 Oct 2026, but its daily archive is plain HTTPS on
GitHub (github.com/adsblol/globe_history_YYYY, one release per day, ~3.5 GB of tar parts, ODbL). This tool streams a
day's tar once and keeps only the traces of the aircraft that alerted that day (nothing big is written), then
replays each alerted flight through tools/track_alerts.py - the same procedure as the map's "analyse track" - and
compares, per alert kind, what was alerted then (alerts.jsonl) with what the checkout raises now.

  python tools/archive_replay.py alerts.jsonl                     # every day in the file
  python tools/archive_replay.py alerts.jsonl --day 2026-10-18 --kinds SHARP_TURN,COURSE_CHANGE
  python tools/archive_replay.py alerts.jsonl --telegram-only     # only the kinds --telegram-skip lets through

Traces are cached in --cache (default archive_traces/, git-ignored), so a second run of the same day is quick.
A one-aircraft replay does not see the rest of the sky nor the board: MASS_SILENCE, GPS_SPOOFING / GPS_DEGRADED
(several aircraft) and the board-based kinds are counted as "not replayable".
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import io
import json
import os
import sys
import tarfile
import time
import urllib.request
from collections import Counter, defaultdict
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

RELEASE = ("https://github.com/adsblol/globe_history_{y}/releases/download/v{d}-planes-readsb-prod-0/"
           "v{d}-planes-readsb-prod-0.tar.{part}")
NOT_REPLAYABLE = {"MASS_SILENCE", "GPS_SPOOFING", "GPS_DEGRADED", "CONTACT_RESTORED", "DIVERSION", "RETURNED", "TURNING_BACK"}


class Parts:
    """the release's tar parts (.aa, .ab, ...) read as one stream"""

    def __init__(self, day: str):
        self.day, self.idx, self.cur = day.replace("-", "."), 0, None

    def read(self, n=-1):
        while True:
            if self.cur is None:
                if self.idx >= 10:
                    return b""
                url = RELEASE.format(y=self.day[:4], d=self.day, part="a" + "abcdefghij"[self.idx])
                self.idx += 1
                try:
                    self.cur = urllib.request.urlopen(url, timeout=120)
                except Exception:
                    return b""   # no more parts
            b = self.cur.read(n)
            if b:
                return b
            self.cur = None


def fetch_day(day: str, hexes: set, cache: str) -> int:
    """stream the day's archive once, keep the wanted aircraft's traces in `cache`; returns how many were found"""
    absent = os.path.join(cache, f"{day}_not_in_archive.txt")   # looked for once already: not streamed again
    known_absent = set(open(absent).read().split()) if os.path.exists(absent) else set()
    want = {h for h in hexes if h not in known_absent and not os.path.exists(os.path.join(cache, f"{day}_{h}.json"))}
    if not want:
        return 0
    os.makedirs(cache, exist_ok=True)
    got, t0 = 0, time.time()
    try:
        tf = tarfile.open(fileobj=Parts(day), mode="r|")
    except tarfile.ReadError:   # the day's release is published the next day
        print(f"  {day}: no archive yet (published a day later) - its alerts are skipped", file=sys.stderr)
        return 0
    with tf:
        for m in tf:
            if not m.isfile() or "trace_full_" not in m.name:
                continue
            h = m.name.rsplit("trace_full_", 1)[1].split(".")[0].lower()
            if h in want:
                with open(os.path.join(cache, f"{day}_{h}.json"), "wb") as f:
                    f.write(tf.extractfile(m).read())
                got += 1
                if got == len(want):
                    break
    missing = {h for h in want if not os.path.exists(os.path.join(cache, f"{day}_{h}.json"))}
    if missing:
        with open(absent, "a") as f:
            f.write("\n".join(sorted(missing)) + "\n")
    print(f"  {day}: {got} of {len(want)} traces from the archive ({time.time() - t0:.0f} s)", file=sys.stderr)
    return got


def load_traces(cache: str, hexid: str) -> list[dict]:
    out = []
    for name in sorted(os.listdir(cache)):
        if name.endswith(f"_{hexid}.json"):
            raw = open(os.path.join(cache, name), "rb").read()
            out.append(json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("alerts", help="the monitor's alerts.jsonl (or a day's file)")
    ap.add_argument("--day", action="append", help="only this UTC day (YYYY-MM-DD); repeatable")
    ap.add_argument("--kinds", default="", help="only these alert kinds (comma-separated)")
    ap.add_argument("--telegram-only", action="store_true", help="only alerts of kinds the default --telegram-skip sends")
    ap.add_argument("--cache", default="archive_traces")
    ap.add_argument("--out", default="", help="write every flight's row (then / now) as JSON here")
    a = ap.parse_args(argv)
    import logging
    logging.disable(logging.CRITICAL)
    import flight_watch as fw
    import track_alerts
    skip = set(fw.parse_args([]).telegram_skip.split(",")) if a.telegram_only else set()
    kinds = {k.strip().upper() for k in a.kinds.split(",") if k.strip()}
    recs = []
    with open(a.alerts, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = calendar.timegm(time.strptime(r["time"], "%Y-%m-%dT%H:%M:%SZ"))
            day = r["time"][:10]
            if (a.day and day not in a.day) or (kinds and r["kind"] not in kinds) or r["kind"] in skip or not r.get("hex"):
                continue
            recs.append({**r, "t": t, "day": day})
    if not recs:
        sys.exit("no alerts to replay")
    for day in sorted({r["day"] for r in recs}):
        fetch_day(day, {r["hex"].lower() for r in recs if r["day"] == day}, a.cache)
    # one replay per flight (aircraft + leg): "one alert, then quiet" rules must see the flight's earlier alerts
    flights = {}
    for r in sorted(recs, key=lambda r: r["t"]):
        traces = load_traces(a.cache, r["hex"].lower())
        tr = track_alerts.join(traces)
        leg = track_alerts.leg_at(tr, r["t"]) if tr else None
        if not leg:
            continue
        f = flights.setdefault((r["hex"], leg["start"]), {"hex": r["hex"], "t": r["t"], "traces": traces, "then": []})
        f["then"].append(r["kind"])
    then, now, rows = Counter(), Counter(), []
    for (hexid, _), f in flights.items():
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                an = track_alerts.analyze(f["traces"], hexid.lower(), f["t"])
        except Exception as e:
            an = {"error": f"{type(e).__name__}: {e}"}
        if an.get("error"):
            continue
        got = [x["kind"] for x in an["alerts"] if x["kind"] != "CONTACT_RESTORED"]
        then.update(k for k in f["then"] if k not in NOT_REPLAYABLE)
        now.update(k for k in got if k not in NOT_REPLAYABLE and (not kinds or k in kinds) and k not in skip)
        rows.append({"hex": hexid, "then": f["then"], "now": got})
    print(f"{len(flights)} flights replayed ({len(rows)} with a trace leg)")
    print(f"{'kind':<16}{'then':>6}{'now':>6}")
    for k in sorted(set(then) | set(now), key=lambda k: -then[k]):
        print(f"{k:<16}{then[k]:>6}{now[k]:>6}")
    print(f"{'total':<16}{sum(then.values()):>6}{sum(now.values()):>6}   (not replayable: {', '.join(sorted(NOT_REPLAYABLE))})")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
