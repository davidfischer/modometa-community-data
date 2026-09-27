# MODOMeta Community Data 🌐

A curated repository of crowdsourced, community-contributed metagame data, match histories, and player tournament reports across competitive Magic: The Gathering formats.

This repository serves as a companion to [`modometa-mtgo-data`](https://github.com/davidfischer/modometa-mtgo-data). While `modometa-mtgo-data` strictly archives official Daybreak/MTGO tournament results and published top decklists, `modometa-community-data` hosts external, community-maintained datasets (such as Swiss round matchup sheets) converted into standardized JSON structures.

Current data sources:

- **Legacy Data Collection:** The [Legacy Data Collection Project](https://patreon.com/legacydatacollection) painstakingly collect match head-to-head metadata from most Legacy challenges in the Swiss rounds while the official data only reports Top 8 rounds. Read more about the LDC and the parts used with MODOMeta [here](./datasources/legacy-data-collection/README.md).
- **Vintage MTGO Community:** The [Vintage MTGO Community](https://discord.gg/2eVcsjK) collects match head-to-head metadata from Vintage challenge Swiss rounds in a similar way. Read more about the VMC and the parts used with MODOMeta [here](./datasources/vintage-mtgo-community/README.md).

---

## Directory Structure

```text
modometa-community-data/
├── datasources/
│   ├── legacy-data-collection/     # Legacy Data Collection (LDC) Google Sheets
│   │   ├── README.md               # Datasource documentation and community credits
│   │   ├── scripts/                # Utility scripts for fetching & processing data
│   │   └── 2026/
│   │       ├── legacy_challenges.yaml # Slug-to-Google-Sheet mapping
│   │       ├── scripts/            # Year-specific runner scripts
│   │       ├── 01/ ... 12/         # Converted monthly match JSON files
│   │       └── ...
│   └── vintage-mtgo-community/     # Vintage MTGO Community (VMC) Google Sheets
│       ├── README.md               # Datasource documentation and community credits
│       ├── scripts/                # Utility scripts for fetching & processing data
│       └── 2026/
│           ├── vintage_challenges.yaml # Slug-to-Google-Sheet mapping
│           └── 01/ ... 12/         # Converted monthly match JSON files
├── src/
│   └── modometa_community_data/    # Python package & parser engine
│       ├── __init__.py
│       ├── ldc.py                  # LDC Google Sheet parser & CLI tool
│       └── vmc.py                  # VMC Google Sheet parser & CLI tool
├── tests/                          # Test suite
└── pyproject.toml
```

---

## Setup & Workflow

This project is managed with [`uv`](https://docs.astral.sh/uv/) and targets Python >= 3.14.

### 1. Install dependencies
```bash
uv sync
```

### 2. Pull Community Match Data
You can pull data or update challenge mappings using the CLI tools:

#### Legacy Data Collection (LDC)
```bash
# Create or update legacy_challenges.yaml for a year from the official MTGO calendar
uv run pull-ldc --update-yaml 2026

# Pull all mapped challenges where JSON has not yet been generated
uv run pull-ldc

# Pull/force re-ingestion of challenges on or after a start date
uv run pull-ldc --start-date 2026-09-10 --force

# Pull a specific challenge by slug
uv run pull-ldc --slug legacy-challenge-32-2026-09-1312854093

# Perform a dry-run without writing files
uv run pull-ldc --slug legacy-challenge-32-2026-09-1312854093 --dry-run
```

#### Vintage MTGO Community (VMC)
```bash
# Create or update vintage_challenges.yaml for a year from the MTGO calendar and merge sheet
uv run pull-vmc --update-yaml 2026

# Pull all mapped challenges where JSON has not yet been generated
uv run pull-vmc

# Pull/force re-ingestion of challenges on or after a start date
uv run pull-vmc --start-date 2026-09-10 --force

# Pull a specific challenge by slug
uv run pull-vmc --slug vintage-challenge-32-2026-09-2012854520

# Perform a dry-run without writing files
uv run pull-vmc --slug vintage-challenge-32-2026-09-2012854520 --dry-run
```

### 3. Run Quality Checks
```bash
uv run pre-commit run --all-files
uv run pytest
uv run pull-ldc --validate
uv run pull-vmc --validate
```
