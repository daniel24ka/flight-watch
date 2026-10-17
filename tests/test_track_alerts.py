"""tools/track_alerts.py: our alerts on one flight's readsb trace - the procedure tools/squawk_cases.py and
docs/flight_map.html ("analyse track", flight_watch.py under Pyodide) share. Here on FZ1073's captured
track (tests/data/fz1073_2026-09-30.json.gz) turned back into a readsb trace file."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import replay  # noqa: E402
import track_alerts  # noqa: E402

FZ1073 = "8965d1"


def readsb_trace(points: list, day0: float) -> dict:
    """Fixture points [t, lat, lon, alt, gs, track, vrate, flight, squawk, emergency, category] as a
    trace_full file: rows [dt, lat, lon, alt, gs, track, flags, rate, {flight, squawk, ...}]."""
    rows, state = [], {}
    for t, lat, lon, alt, gs, trk, vr, flight, squawk, emergency, category in points:
        for k, v in (("flight", flight), ("squawk", squawk), ("emergency", emergency), ("category", category)):
            if v is not None:
                state[k] = v
        rows.append([t - day0, lat, lon, alt, gs, trk, 0, vr, dict(state)])
    return {"icao": FZ1073, "r": "A6-FKF", "t": "B38M", "timestamp": day0, "trace": rows}


class TrackAlertsFZ1073(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx = replay.load(os.path.join(HERE, "data", "fz1073_2026-09-30.json.gz"))
        day0 = fx["meta"]["start"] - fx["meta"]["start"] % 86400
        tr = readsb_trace(fx["aircraft"][FZ1073]["points"], day0)
        half = len(tr["trace"]) // 2  # two files (e.g. trace_full + trace_recent) overlapping, as the page gets them
        a = {**tr, "trace": tr["trace"][:half + 20]}
        b = {**tr, "timestamp": day0 + 100, "trace": [[r[0] - 100, *r[1:]] for r in tr["trace"][half:]]}
        ends = [{"code": "DXB", "icao": "OMDB", "lat": 25.2528, "lon": 55.3644, "name": "Dubai"},
                {"code": "TLV", "icao": "LLBG", "lat": 32.0114, "lon": 34.8867, "name": "Tel Aviv"}]
        cls.got = track_alerts.analyze([b, a], FZ1073, 1790746294, ends, "FZ1073")  # 05:31:34Z, the 7700

    def test_the_dive_and_the_squawks(self):
        kinds = [a["kind"] for a in self.got["alerts"]]
        self.assertIn("VERTICAL_RATE", kinds)
        self.assertEqual(kinds.count("EMERGENCY"), 2, kinds)  # 7700, then 7500
        dive = next(a for a in self.got["alerts"] if a["kind"] == "VERTICAL_RATE")
        self.assertLess(dive["time"], 1790746294)  # before the squawk
        self.assertTrue({"near", "DXB", "TLV"} >= {a["watched_from"] for a in self.got["alerts"]})

    def test_no_trace_or_no_flight_then(self):
        self.assertIn("error", track_alerts.analyze([], FZ1073, 1790746294))
        self.assertIn("error", track_alerts.analyze([readsb_trace([[1790746294, 30, 38, 15000, 400, 300, 0,
                                                                    "FDB1073", None, None, None]], 1790726400)],
                                                    FZ1073, 1790746294 + 7200))


if __name__ == "__main__":
    unittest.main()
