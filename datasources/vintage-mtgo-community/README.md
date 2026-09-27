# Vintage MTGO Community (VMC) Datasource

This directory contains match datasets and configuration mappings for the **Vintage MTGO Community**.

---

## About the Vintage MTGO Community

The Vintage MTGO Community is a dedicated player-driven initiative collecting match-level results, archetypes, and tournament records for Magic Online Vintage events.

Because Daybreak Games / Wizards of the Coast only publishes Top 32 standings and bracket playoff matches for MTGO Challenges (omitting Swiss round pairings and match scores), the Vintage MTGO Community crowdsources and compiles head-to-head match results for Vintage Challenges and major events.

- **Discord**: [Vintage MTGO Community Discord](https://discord.gg/2eVcsjK)
- **Merge Sheet**: [Vintage Challenges Merge Sheet](https://docs.google.com/spreadsheets/d/169L3p4pJrNYsBEpqBNllSwba4_ck7hyoLLqxsjQ5Rfc/edit)

Special thanks to [@iamactuallylvl1](https://linktr.ee/iamactuallylvl1) and all contributors and players who record and submit match data!

---

## Directory Structure

```text
vintage-mtgo-community/
├── README.md
├── scripts/
│   └── pull_matches.py           # Generic runner for all years
└── 2026/
    ├── vintage_challenges.yaml   # Mapping of MTGO challenge slugs to Google Sheet IDs
    └── 01/ ... 12/               # Monthly directories containing converted JSON match files
```

---

## Mapping Configuration Format (`vintage_challenges.yaml`)

Each challenge entry is keyed by its canonical MTGO tournament slug (matching [`modometa-mtgo-data`](https://github.com/davidfischer/modometa-mtgo-data) data):

```yaml
vintage-challenge-32-2026-09-2012854520:
  date: "2026-09-20"
  name: "Vintage Challenge 32"
  uri: "https://www.mtgo.com/decklist/vintage-challenge-32-2026-09-2012854520"
  sheet_id: "1EWSE2ZjNAmVhFATAku9LWvVldETKz8_s1cawsysbaPo"
  tab: "Standings"       # Optional, defaults to "Standings"
```

When a Google Sheet has not yet been identified or recorded for a challenge, `sheet_id` is set to `null`. The Vintage MTGO Community ["Merge Sheet"](https://docs.google.com/spreadsheets/d/169L3p4pJrNYsBEpqBNllSwba4_ck7hyoLLqxsjQ5Rfc/edit) has links between the challenge and the challenge-specific Google Sheet with match head-to-head data.

---

## Converted JSON Format

The data puller fetches the CSV from the Google Sheet's `Standings` tab, extracts all Swiss and playoff rounds, performs symmetric deduplication and reconciliation, and outputs standard JSON files:

```json
{
  "Tournament": {
    "Id": "vintage-challenge-32-2026-09-2012854520",
    "Date": "2026-09-20",
    "Name": "Vintage Challenge 32",
    "PlayerCount": 43,
    "Source": "vintage-mtgo-community",
    "SheetId": "1EWSE2ZjNAmVhFATAku9LWvVldETKz8_s1cawsysbaPo"
  },
  "Players": [
    {
      "Player": "trogdor1396",
      "Archetype": "Jewel Shops"
    }
  ],
  "Rounds": [
    {
      "RoundName": "Round 1",
      "RoundType": "Swiss",
      "Matches": [
        {
          "Player1": "AFX",
          "Player2": "Harvey_Specter",
          "Result": "2-0-0"
        }
      ]
    }
  ]
}
```

---

## Usage

To create or update the challenge mapping for a given year from the official MTGO tournament calendar and the Vintage MTGO Community merge sheet:

```bash
# This can be quite slow (a minute or two)
# It pulls data from both the MTGO.com event calendar
# and tries to reconcile it with the merge sheet
uv run pull-vmc --update-yaml 2026
```

To pull data for all mapped sheets:

```bash
uv run pull-vmc
```

To pull/force re-ingestion of recent challenges on or after a start date:

```bash
uv run pull-vmc --start-date 2026-09-10 --force
```

To pull a single challenge:

```bash
uv run pull-vmc --slug vintage-challenge-32-2026-09-2012854520
```

To validate all YAML mapping files:

```bash
uv run pull-vmc --validate
```

---

## Known Discrepancies

There's a few known discrepancies from official MTGO.com tournament data.

* Official tournament results from MTGO always show 2-0 or 2-1 for a match win.
  VMC data occasionally shows 1-0 indicating possibly a concession or prize split.
* Official tournament results always show the player's recorded name.
  VMC data occasionally uses a player's alias instead (`DB_LvL1` vs `IamActuallyLvL1`).
* Occasionally the VMC data cuts off partway through the Top 8 rounds (eg. `vintage-challenge-32-2025-09-0612813566`).
  This may be due to a prize split or a data error.
