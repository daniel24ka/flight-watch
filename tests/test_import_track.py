"""tools/import_track.py: track files from elsewhere -> replay fixtures (offline, temporary files)."""
import gzip
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
import import_track  # noqa: E402
import replay  # noqa: E402

T0 = 1_000_000_000  # 2001-09-09T01:46:40Z

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><Placemark>
<gx:Track><when>2001-09-09T01:46:40Z</when><when>2001-09-09T01:46:52Z</when><when>2001-09-09T01:47:04Z</when>
<gx:coord>-74.0 41.0 3048</gx:coord><gx:coord>-74.0 41.02 3000</gx:coord><gx:coord>-74.0 41.04 2950</gx:coord>
</gx:Track></Placemark></Document></kml>"""


class ImportTrack(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def file(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def run_tool(self, *argv):
        out = os.path.join(self.dir.name, "out.json.gz")
        import_track.main([*argv, "--out", out] if "--add-to" not in argv else list(argv))
        with gzip.open(argv[argv.index("--add-to") + 1] if "--add-to" in argv else out, "rt") as f:
            return json.load(f)

    def radar_csv(self, squawk_from=99):
        """Radar-like plots every 12 s heading north at 360 kt, descending 1,000 ft per plot, no
        speeds or rates; the squawk changes at plot `squawk_from`."""
        lines = ["Time (UTC),Latitude,Longitude,Altitude (ft),Squawk"]
        for i in range(10):
            lines.append(f"{T0 + 12 * i},{40 + i * 0.02:.4f},-74.0,{20000 - 1000 * i},"
                         f"{'3020' if i >= squawk_from else '1470'}")
        return self.file("radar.csv", "\n".join(lines) + "\n")

    def test_generic_csv_speeds_tracks_and_rates_worked_out(self):
        fx = self.run_tool(self.radar_csv(), "--callsign", "TST11", "--type", "B762")
        (hexid, ac), = fx["aircraft"].items()
        pts = ac["points"]
        self.assertEqual(len(pts), 10)
        t, lat, lon, alt, gs, track, vrate, flight, squawk, _, category = pts[3]
        self.assertAlmostEqual(gs, 360, delta=5)            # 0.02 deg (1.2 nm) per 12 s
        self.assertAlmostEqual(track, 0, delta=1)
        self.assertAlmostEqual(vrate, -5000, delta=50)      # 1,000 ft per 12 s
        self.assertEqual((pts[0][7], pts[0][8], pts[0][10]), ("TST11", "1470", "A3"))
        self.assertIsNone(flight)                           # only where it changes
        self.assertEqual(ac["t"], "B762")
        self.assertTrue(hexid.startswith("f"))

    def test_squawk_change_reaches_the_monitor(self):
        fx = self.run_tool(self.radar_csv(squawk_from=5), "--callsign", "TST11")
        (hexid, ac), = fx["aircraft"].items()
        self.assertEqual([p[8] for p in ac["points"] if p[8]], ["1470", "3020"])  # only where it changes
        alerts, monitor, _ = replay.run(fx)
        self.assertEqual(monitor.tracks[hexid].squawk, "3020")
        self.assertNotIn("EMERGENCY", [a["kind"] for a in alerts])  # 3020 is no emergency code

    def test_watch_point_is_back_along_the_track(self):
        fx = self.run_tool(self.radar_csv(), "--watch-nm", "5")
        lat, lon = fx["meta"]["airport_coords"]
        self.assertAlmostEqual(lat, 40.1, delta=0.03)       # ~5 nm before the last plot (40.18)
        self.assertEqual(lon, -74.0)
        self.assertEqual(fx["meta"]["start"], T0 + 300)     # the replay's warm-up starts at the first plot

    def test_kml_track_in_metres(self):
        fx = self.run_tool(self.file("t.kml", KML), "--callsign", "TST1", "--lat", "40.5", "--lon", "-74")
        pts = next(iter(fx["aircraft"].values()))["points"]
        self.assertEqual([p[3] for p in pts], [10000, 9843, 9678])
        self.assertEqual(fx["meta"]["airport_coords"], [40.5, -74.0])

    def test_fr24_basic_csv(self):
        text = ("Timestamp,UTC,Callsign,Position,Altitude,Speed,Direction\n"
                f'{T0},2001-09-09T01:46:40Z,TST1,"41.0,-74.0",10000,300,90\n'
                f'{T0 + 10},2001-09-09T01:46:50Z,TST1,"41.0,-73.98",10100,302,91\n')
        fx = self.run_tool(self.file("fr24.csv", text))
        pts = next(iter(fx["aircraft"].values()))["points"]
        self.assertEqual(pts[1][1:6], [41.0, -73.98, 10100, 302.0, 91.0])
        self.assertEqual(pts[1][6], 600)                    # worked out: 100 ft in 10 s

    def test_add_a_second_aircraft(self):
        first = self.run_tool(self.radar_csv(), "--callsign", "TST11", "--hex", "aa0011")
        path = os.path.join(self.dir.name, "out.json.gz")
        both = self.run_tool(self.file("t.kml", KML), "--callsign", "TST175", "--hex", "aa0175",
                             "--route", "KBOS-KLAX", "--add-to", path)
        self.assertEqual(sorted(both["aircraft"]), ["aa0011", "aa0175"])
        self.assertEqual(both["meta"]["focus"], ["aa0011", "aa0175"])
        self.assertEqual(both["meta"]["airport_coords"], first["meta"]["airport_coords"])
        self.assertEqual(both["routes"], {"TST175": "KBOS-KLAX"})


if __name__ == "__main__":
    unittest.main()


class PlotFixture(unittest.TestCase):
    """tools/plot_alert.py --fixture: an incident plotted offline from its fixture and a replay."""
    def test_mu5735_page(self):
        import plot_alert
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.html")
            plot_alert.main(["--fixture", os.path.join(HERE, "data", "mu5735_2022-03-21.json.gz"),
                             "--no-open", "--out", out])
            with open(out, encoding="utf-8") as f:
                page = f.read()
        self.assertIn("MU5735 (B-1791, B738, 780db8) ZPPP", page)
        self.assertIn("Selected: VERTICAL_RATE", page)       # the incident aircraft's first alert
        self.assertIn("km east of XXX", page)                # measured from the fixture's watched point
        self.assertNotIn("Israel (rough)", page)             # far from Israel: no outline
