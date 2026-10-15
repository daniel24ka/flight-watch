# Hijacking / incident flight data: what was found (Oct 2026)

Source list: Wikipedia "List of aircraft hijackings", 2020s section (read 15 Oct 2026; 1980-2019 not
gone through yet). Free ADS-B history (ADS-B Exchange globe_history, used by `tools/capture_incident.py`)
starts **May 2020** (404 before), so older events need a track file from elsewhere (`tools/import_track.py`).

## Captured

| Event | Fixture | Replay alerts on the aircraft |
|---|---|---|
| UA2005, 29/30 May 2026, ORD->MSP, B739 N69818 (hex a94b0a), passenger at cockpit door, **squawk 7500** 01:52:25 UTC, diverted to Madison (landed 02:11) | `tests/data/ua2005_2026-05-30.json.gz` (watched point MSN, 60 nm, 01:40-02:20 UTC, 122 aircraft, route override UAL2005=KORD-KMSP) | EMERGENCY 7500, COURSE_CHANGE, OFF_COURSE. Map: `docs/ua2005_map.html`. **No test written yet** (add a class to `tests/test_replay_incidents.py`; noise on others: 1 OFF_COURSE) |

## Found in ADS-B Exchange, not captured yet

- **Horizon/Alaska 2059**, 23 Oct 2023 UTC, PAE->SFO, E175 N660QX (hex a8b5ad), off-duty pilot pulled the
  engine fire handles. No emergency squawk. At 01:05 UTC, FL310 ~35 nm SSW of Portland, turned back, landed
  PDX 01:26. Command:
  ```bash
  python tools/capture_incident.py --date 2023-10-23 --start 00:50 --end 01:35 --airport PDX --icao KPDX \
      --lat 45.5887 --lon -122.5975 --radius 150 --before-h 0 --after-h 0 --focus a8b5ad \
      --route QXE2059=KPAE-KSFO --name "Horizon 2059 ..." --out tests/data/qx2059_2023-10-23.json.gz
  ```
  Expect OFF_COURSE (bound for SFO, flying away). Watch the noise budget (busy Portland airspace).
- **Frontier 3345**, 31 May / 1 Jun 2026 UTC, SJU->ORD, A21N N660FR (hex a8b4c6), diverted to Miami
  (landed 03:55 UTC). Nothing visible in ADS-B: no squawk, a 40 min coverage gap over the Bahamas, track
  toward Miami within ~30 deg of Chicago until the descent. Not worth a fixture (no monitor could flag it).

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
