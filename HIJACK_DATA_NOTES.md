# Hijacking / incident flight data: what was found (Oct 2026)

Source list: Wikipedia "List of aircraft hijackings", 2020s section (read 15 Oct 2026; 1980-2019 not
gone through yet). Free ADS-B history (ADS-B Exchange globe_history, used by `tools/capture_incident.py`)
starts **May 2020** (404 before), so older events need a track file from elsewhere (`tools/import_track.py`).

## Captured (tests in `tests/test_replay_incidents.py`; maps in `docs/`, not linked from `incidents.html`)

| Event | Fixture | Replay alerts on the aircraft | Map |
|---|---|---|---|
| UA2005, 30 May 2026 UTC, ORD->MSP, B739 N69818, passenger at cockpit door, **squawk 7500** 01:52:25, landed Madison 02:11 | `ua2005_2026-05-30` (MSN, 60 nm, 122 aircraft) | EMERGENCY 7500, COURSE_CHANGE, OFF_COURSE | `ua2005_map.html` |
| Horizon 2059, 23 Oct 2023 UTC, PAE->SFO, E175 N660QX, off-duty pilot pulled the fire handles; no squawk; turned back at FL310 01:05, landed PDX 01:26 | `qx2059_2023-10-23` (PDX, 150 nm, 246 aircraft) | COURSE_CHANGE + OFF_COURSE at 01:10 | `qx2059_map.html` |
| Frontier 3345, 1 Jun 2026 UTC, SJU->ORD, A21N N660FR, passenger tried the exit door and cockpit, landed Miami 03:55 | `f93345_2026-06-01` (MIA, 100 nm, 181 aircraft) | **none** - invisible in ADS-B (no squawk, Miami within ~30 deg of Chicago's direction). A recorded limit | none (`plot_alert.py --fixture` needs an alert on the aircraft) |
| ET302, 10 Mar 2019 (Flightradar24 granular CSV, already in the repo) | `et302_2019-03-10` | LOST_CONTACT only: the data ends at 05:41, 8,600 ft, ~3 min before the crash | `et302_map.html` |

Noise: Horizon 2059's replay has 15 alerts on other aircraft, 7 of them high priority, mostly from today's
route data applied to 2023 callsigns (ASA1383 taken as a Portland arrival landed at Seattle). Old captures
need routes of the day; only the incident flight's route is overridden.

## Not available (no ADS-B coverage found)

- **Air India AI2379**, 4 Aug 2026, A320neo VT-EXO (hex 800cc8), 300 ft drop ~03:00 UTC over the Andaman Sea:
  ADS-B Exchange's trace starts 03:25 over the Bay of Bengal, after the drop. Only Flightradar24 (satellite) may have it.
- **Tropic Air 711**, 17 Apr 2025, C208 V3-HIG, Belize, ~14:30-16:12 UTC: no low traffic heard over Belize.
- **Cessna 172 to Vancouver**, 15 Jul 2025 ~20:30 UTC (Victoria Flying Club): no ADS-B aircraft found
  that went from Victoria to YVR; probably no ADS-B out. (Checked C-GWCG, C-FJEN, C-GMAZ, C-FMCN: not it.)
- **Samaritan's Purse C208**, 2 Dec 2025, South Sudan: not checked (no coverage expected).
- **Ryan Air Services C208**, 7 Jul 2021, Aniak, Alaska (low altitude): not checked.
- **Azerbaijan Airlines 8243**, 25 Dec 2024: ADS-B Exchange lost it at 04:16 UTC over Dagestan.

## Needs a file you download (Flightradar24 blocks the cloud sandbox)

- **Lion Air JT610** (29 Oct 2018) and **Ethiopian ET302** (10 Mar 2019): Flightradar24 blog posts offer
  the **granular** CSV. Then:
  ```bash
  python tools/import_track.py FILE.csv --name "..." --reg PK-LQP --type B38M --route WIII-WIPK \
      --out tests/data/jt610_2018-10-29.json.gz
  ```

## Tips

- Find an aircraft: `capture_incident.py --date D --start HH:MM --end HH:MM --find CALLSIGN`. Keep the window
  short: each 30 min of history is ~20 MB.
- Small aircraft often send no callsign: list everything in a lat/lon box from the heatmaps (the helper used
  was a short script around `capture_incident.heatmap`).
- Wikipedia from the sandbox needs a User-Agent header (else HTTP 429).
- Next: go through Wikipedia's 2010s-1980s entries; only pre-2020 events with a published track file are usable.
