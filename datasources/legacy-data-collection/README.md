# Legacy Data Collection Project (LDC) Datasource

This directory contains converted match datasets and configuration mappings for the **Legacy Data Collection Project** (LDC).

---

## About the Legacy Data Collection Project

The [Legacy Data Collection Project](https://patreon.com/legacydatacollection) is a grassroots community initiative organized by Joe Dyer (@volrathxp) and community volunteers.

Because Daybreak Games / Wizards of the Coast only publishes Top 32 standings and bracket playoff matches for MTGO Challenges (omitting Swiss rounds), the LDC painstakingly captures head-to-head match results for major Legacy events.

- **Discord**: [LDC Discord Server](https://discord.gg/frG2n4p6e9)
- **Support**: [LDC Patreon](http://patreon.com/legacydatacollection)

Special thanks to all contributors and players who record and submit match data!

---

## Directory Structure

```text
legacy-data-collection/
├── README.md
├── scripts/
│   └── pull_matches.py           # Generic runner for all years
└── 2026/
    ├── legacy_challenges.yaml    # Mapping of MTGO challenge slugs to Google Sheet IDs
    └── 01/ ... 12/               # Monthly directories containing converted JSON match files
```

---

## Mapping Configuration Format (`legacy_challenges.yaml`)

Each challenge entry is keyed by its canonical MTGO tournament slug (matching `modometa-mtgo-data`):

```yaml
legacy-challenge-32-2026-09-1312854093:
  date: "2026-09-13"
  name: "Legacy Challenge 32"
  uri: "https://www.mtgo.com/decklist/legacy-challenge-32-2026-09-1312854093"
  sheet_id: "1t_QLcXWxhJjKs9QlswfKpRLdukqIegos6gIDhyWk2rE"
  tab: "Match Up Input"       # Optional, defaults to "Match Up Input"
```

When a Google Sheet has not yet been identified or recorded for a challenge, `sheet_id` is set to `null`.

---

## Converted JSON Format

The data puller fetches the CSV from the Google Sheet, extracts all Swiss and Top 8 playoff rounds, performs symmetric deduplication and reconciliation, and outputs standard JSON files:

```json
{
  "Tournament": {
    "Id": "legacy-challenge-32-2026-09-1312854093",
    "Date": "2026-09-13",
    "Name": "Legacy Challenge 32",
    "PlayerCount": 82,
    "Source": "legacy-data-collection",
    "SheetId": "1t_QLcXWxhJjKs9QlswfKpRLdukqIegos6gIDhyWk2rE"
  },
  "Players": [
    {
      "Player": "Imperia86",
      "Archetype": "Boros Energy"
    }
  ],
  "Rounds": [
    {
      "RoundName": "Round 1",
      "RoundType": "Swiss",
      "Matches": [
        {
          "Player1": "Imperia86",
          "Player2": "Peppe",
          "Result": "2-1-0"
        }
      ]
    }
  ]
}
```

---

## Usage

To create or update the challenge mapping for a given year from the official MTGO tournament calendar:

```bash
uv run pull-ldc --update-yaml 2026
```

To pull data for all mapped sheets:

```bash
uv run pull-ldc
```

To pull/force re-ingestion of recent challenges on or after a start date:

```bash
uv run pull-ldc --start-date 2026-09-10 --force
```

To pull a single challenge:

```bash
uv run pull-ldc --slug legacy-challenge-32-2026-09-1312854093
```
