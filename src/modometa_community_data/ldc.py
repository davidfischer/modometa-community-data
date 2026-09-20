"""Legacy Data Collection (LDC) Google Sheet data puller and normalizer."""

import argparse
import csv
import datetime
import io
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

SCORE_RE = re.compile(r"^(\d+)-(\d+)(?:-(\d+))?$")
ROUND_HEADER_RE = re.compile(r"(?i)^round\s*(\d+)")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MTGO_CALENDAR_URL = "https://www.mtgo.com/decklists/{year}/{month:02d}"
REQUIRED_CHALLENGE_KEYS = ("date", "name", "uri")
DEFAULT_USER_AGENT = "MODOMeta-Community-Data-Puller/0.1 (+https://modometa.com)"
USER_AGENT = os.environ.get("USER_AGENT", DEFAULT_USER_AGENT)


def deduce_swiss_rounds(player_count: int | None) -> int:
    """Determine standard MTG Swiss rounds from player attendance (MTR Appendix E)."""
    if player_count is None:
        return 7
    if player_count <= 8:
        return 3
    elif player_count <= 16:
        return 4
    elif player_count <= 32:
        return 5
    elif player_count <= 64:
        return 6
    elif player_count <= 128:
        return 7
    elif player_count <= 226:
        return 8
    else:
        return 9


def parse_match_result(score_str: str) -> tuple[int, int, int] | None:
    """Parse match result score into (p1_wins, p2_wins, draws) or None if invalid."""
    if not score_str:
        return None
    cleaned = score_str.strip()
    if cleaned.lower() in {"bye", "drop", "dq", "split"}:
        return None
    match = SCORE_RE.match(cleaned)
    if not match:
        return None
    w1 = int(match.group(1))
    w2 = int(match.group(2))
    draws = int(match.group(3)) if match.group(3) else 0
    return w1, w2, draws


def parse_date(date_val: Any) -> datetime.date | None:
    """Parse date value from string or date/datetime object into datetime.date."""
    if isinstance(date_val, datetime.datetime):
        return date_val.date()
    if isinstance(date_val, datetime.date):
        return date_val
    if isinstance(date_val, str):
        cleaned = date_val.strip()
        if not cleaned:
            return None
        if len(cleaned) >= 10 and cleaned[4] == "-" and cleaned[7] == "-":
            cleaned = cleaned[:10]
        try:
            return datetime.date.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def fetch_sheet_csv(sheet_id: str, tab: str = "Match Up Input") -> str:
    """Fetch CSV content from public Google Sheet via gviz endpoint without auth."""
    quoted_tab = urllib.parse.quote(tab)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={quoted_tab}"
    logger.debug("Fetching sheet CSV from: %s", url)

    headers = {
        "User-Agent": USER_AGENT,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.error("Failed to fetch Google Sheet %s tab '%s': %s", sheet_id, tab, exc)
        raise


def parse_ldc_sheet_csv(
    csv_content: str,
    swiss_rounds: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """Parse LDC 'Match Up Input' CSV into list of rounds, players, and warnings.

    Args:
        csv_content: Raw CSV string from the Google Sheet tab.
        swiss_rounds: Explicit number of Swiss rounds. If None, deduced from data.

    Returns:
        rounds: list of {"RoundName": str, "RoundType": str, "Matches": [...]}
        players: list of {"Player": str, "Archetype": str}
        warnings: list of warning strings for discrepancies

    Note:
        Uses index-based `csv.reader` instead of `csv.DictReader` because LDC
        sheets use paired columns where score columns have empty headers ('').
        `csv.DictReader` would collide duplicate empty keys and overwrite scores
        across rounds.
    """
    reader = csv.reader(io.StringIO(csv_content))
    raw_header = next(reader, None)
    if not raw_header:
        return [], [], ["Empty CSV content"]

    header = [h.strip() for h in raw_header]

    # Discover round columns: pairs of (opponent_col, score_col)
    round_col_pairs: list[tuple[int, str, int, int]] = []
    for col_idx in range(2, len(header), 2):
        col_title = header[col_idx]
        match = ROUND_HEADER_RE.match(col_title)
        if match:
            r_num = int(match.group(1))
            round_col_pairs.append((r_num, f"Round {r_num}", col_idx, col_idx + 1))

    if not round_col_pairs:
        return [], [], ["No round columns found matching 'Round <N>' in CSV header"]

    reports: dict[tuple[str, int], tuple[str, str, int, int, int]] = {}
    players: list[dict[str, str]] = []
    seen_players: set[str] = set()
    rows = list(reader)

    for row in rows:
        if not row:
            continue
        p1 = row[0].strip()
        if not p1:
            continue

        p1_lower = p1.lower()
        if p1_lower not in seen_players:
            seen_players.add(p1_lower)
            archetype = row[1].strip() if len(row) > 1 else ""
            players.append({"Player": p1, "Archetype": archetype})

        for r_num, _, opp_col, score_col in round_col_pairs:
            if opp_col >= len(row):
                continue
            opp = row[opp_col].strip()
            score = row[score_col].strip() if score_col < len(row) else ""
            if not opp or opp.lower() in {"bye", "none", "drop"}:
                continue

            parsed = parse_match_result(score)
            if not parsed:
                continue

            w1, w2, draws = parsed
            reports[(p1.lower(), r_num)] = (p1, opp, w1, w2, draws)

    # Reconcile and deduplicate matches
    rounds_dict: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen_pairs: set[tuple[tuple[str, str], int]] = set()
    warnings: list[str] = []

    for (p1_lower, r_num), (p1, opp, w1, w2, draws) in reports.items():
        opp_lower = opp.lower()
        pair_key = (tuple(sorted([p1_lower, opp_lower])), r_num)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        # Check reciprocal report from opponent
        reciprocal = reports.get((opp_lower, r_num))
        if reciprocal:
            rec_p, rec_opp, rec_w1, rec_w2, rec_d = reciprocal
            if rec_opp.lower() != p1_lower:
                msg = (
                    f"Round {r_num} pairing mismatch: {p1} reported playing {opp}, "
                    f"but {opp} reported playing {rec_opp}. Skipping matchup."
                )
                warnings.append(msg)
                logger.warning(msg)
                seen_pairs.add((tuple(sorted([opp_lower, rec_opp.lower()])), r_num))
                continue

            if (rec_w1 != w2) or (rec_w2 != w1) or (rec_d != draws):
                msg = (
                    f"Round {r_num} score disagreement: {p1} reported {w1}-{w2}-{draws}, "
                    f"but {opp} reported {rec_w1}-{rec_w2}-{rec_d}. Skipping matchup."
                )
                warnings.append(msg)
                logger.warning(msg)
                continue

        rounds_dict[r_num].append(
            {
                "Player1": p1,
                "Player2": opp,
                "Result": f"{w1}-{w2}-{draws}",
            }
        )

    # Append any opponents from matches who didn't have their own row
    for r_num in sorted(rounds_dict.keys()):
        for m in rounds_dict[r_num]:
            for p in (m["Player1"], m["Player2"]):
                p_lower = p.lower()
                if p_lower not in seen_players:
                    seen_players.add(p_lower)
                    players.append({"Player": p, "Archetype": ""})

    # Determine Swiss rounds cutoff if not explicitly provided
    if swiss_rounds is not None:
        cutoff = swiss_rounds
    else:
        sorted_rnums = sorted(rounds_dict.keys())
        # Check if last 3 rounds represent a Top 8 playoff (QF <= 4 matches, SF <= 2 matches, Finals <= 1 match)
        if (
            len(sorted_rnums) >= 4
            and len(rounds_dict[sorted_rnums[-1]]) <= 1
            and len(rounds_dict[sorted_rnums[-2]]) <= 2
            and len(rounds_dict[sorted_rnums[-3]]) <= 4
            and len(rounds_dict[sorted_rnums[-4]]) > 4
        ):
            cutoff = sorted_rnums[-4]
        else:
            cutoff = deduce_swiss_rounds(len(players) if players else None)

    # Build sorted rounds list
    rounds: list[dict[str, Any]] = []
    for r_num in sorted(rounds_dict.keys()):
        matches = rounds_dict[r_num]
        matches.sort(key=lambda m: (m["Player1"].lower(), m["Player2"].lower()))
        round_type = "Swiss" if r_num <= cutoff else "Top8"
        rounds.append(
            {
                "RoundName": f"Round {r_num}",
                "RoundType": round_type,
                "Matches": matches,
            }
        )

    return rounds, players, warnings


def process_challenge(
    slug: str,
    meta: dict[str, Any],
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Process a single challenge entry from YAML mapping."""
    sheet_id = meta.get("sheet_id")
    if not sheet_id:
        logger.debug("Skipping %s: no sheet_id provided", slug)
        return False

    name = meta.get("name")
    if not name:
        logger.warning("Skipping %s: missing name in metadata", slug)
        return False

    date_str = str(meta.get("date") or "")
    if not date_str:
        logger.error("Skipping %s: missing date in metadata", slug)
        return False

    year = date_str[:4]
    month = date_str[5:7] if len(date_str) >= 7 else "00"
    out_dir = REPO_ROOT / "datasources" / "legacy-data-collection" / year / month
    out_file = out_dir / f"{slug}.json"

    if out_file.exists() and not force and not dry_run:
        logger.debug(
            "Skipping %s: already exists at %s (use --force to overwrite)",
            slug,
            out_file,
        )
        return True

    tab = meta.get("tab") or "Match Up Input"

    logger.info(
        "Processing %s (sheet_id=%s)...",
        slug,
        sheet_id,
    )

    try:
        csv_text = fetch_sheet_csv(sheet_id, tab)
    except Exception as exc:
        logger.error("Could not fetch sheet for %s: %s", slug, exc)
        return False

    rounds, players, warnings = parse_ldc_sheet_csv(csv_text)
    player_count = len(players) if players else None

    total_matches = sum(len(r["Matches"]) for r in rounds)

    if total_matches == 0:
        logger.warning("No valid matches extracted for %s", slug)
        return False

    payload = {
        "Tournament": {
            "Id": slug,
            "Date": date_str,
            "Name": name,
            "PlayerCount": player_count,
            "Source": "legacy-data-collection",
            "SheetId": sheet_id,
        },
        "Players": players,
        "Rounds": rounds,
    }

    if dry_run:
        logger.info(
            "[DRY-RUN] Extracted %d players, %d rounds and %d matches for %s (warnings: %d)",
            len(players),
            len(rounds),
            total_matches,
            slug,
            len(warnings),
        )
        return True

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
        fp.write("\n")

    logger.info(
        "Successfully saved %d players, %d matches across %d rounds to %s",
        len(players),
        total_matches,
        len(rounds),
        out_file,
    )
    return True


def dump_challenges_yaml(challenges: dict[str, dict[str, Any]], year: int) -> str:
    """Format challenges mapping as clean YAML matching repository conventions."""
    lines = [
        f"# Legacy Challenges {year} - LDC Google Sheet Mapping",
        "# Generated from official MTGO tournament calendar.",
        "#",
        "# Schema per entry:",
        "#   date: Tournament date (YYYY-MM-DD)",
        "#   name: Tournament name",
        "#   uri: Official MTGO tournament decklist URL",
        "#   sheet_id: Google Sheet ID (from docs.google.com/spreadsheets/d/<ID>/...) or null",
        '#   tab: Tab name in the Google Sheet (defaults to "Match Up Input")',
        "",
    ]
    for slug, meta in challenges.items():
        date_val = meta.get("date", "")
        name_val = str(meta.get("name", "")).replace("'", "''")
        uri_val = meta.get("uri", "")
        sheet_id_val = meta.get("sheet_id")
        tab_val = str(meta.get("tab") or "Match Up Input").replace("'", "''")

        lines.append(f"{slug}:")
        lines.append(f"  date: '{date_val}'")
        lines.append(f"  name: '{name_val}'")
        lines.append(f"  uri: '{uri_val}'")
        if sheet_id_val is None:
            lines.append("  sheet_id: null")
        else:
            sheet_id_str = str(sheet_id_val).replace("'", "''")
            lines.append(f"  sheet_id: '{sheet_id_str}'")
        lines.append(f"  tab: '{tab_val}'")
        lines.append("")

    return "\n".join(lines)


def fetch_mtgo_calendar_challenges(
    year: int,
    session: requests.Session | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch all Legacy Challenge events for a year from the official MTGO calendar."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    events: dict[str, dict[str, Any]] = {}
    logger.info("Fetching MTGO calendar for %d...", year)

    for month in range(1, 13):
        url = MTGO_CALENDAR_URL.format(year=year, month=month)
        logger.debug("Fetching calendar page: %s", url)
        html_text = None
        for attempt in range(3):
            try:
                resp = session.get(url, timeout=45)
                # Future months redirect to /decklists with 302
                if resp.status_code != 200 or f"/{year}/{month:02d}" not in resp.url:
                    break
                html_text = resp.text
                break
            except Exception as exc:
                if attempt == 2:
                    logger.warning("Failed to fetch %s after 3 attempts: %s", url, exc)
                time.sleep(1 * (attempt + 1))

        if not html_text:
            continue

        soup = BeautifulSoup(html_text, "html.parser")
        for item in soup.select("li.decklists-item"):
            h3 = item.select_one("h3")
            if not h3:
                continue
            title = h3.text.strip()

            # Legacy only since this is Legacy Data Collection Project
            if not ("legacy" in title.lower() and "challenge" in title.lower()):
                continue

            a = item.select_one("a")
            t = item.select_one("time")
            if not a or not t:
                continue

            href = a.get("href", "")
            date_str = (t.get("datetime") or "")[:10]
            if not date_str.startswith(str(year)):
                continue

            slug = href.split("?")[0].rstrip("/").split("/")[-1]
            uri = urllib.parse.urljoin("https://www.mtgo.com", href)

            events[slug] = {
                "date": date_str,
                "name": title,
                "uri": uri,
                "sheet_id": None,
                "tab": "Match Up Input",
            }

    logger.info(
        "Discovered %d Legacy Challenge event(s) for %d from MTGO calendar",
        len(events),
        year,
    )
    return events


def update_yaml_for_year(year: int) -> Path:
    """Create or update legacy_challenges.yaml for year from MTGO calendar."""
    yaml_path = (
        REPO_ROOT
        / "datasources"
        / "legacy-data-collection"
        / str(year)
        / "legacy_challenges.yaml"
    )
    existing: dict[str, Any] = {}
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as fp:
            existing = yaml.safe_load(fp) or {}

    calendar_events = fetch_mtgo_calendar_challenges(year)

    new_count = 0
    for slug, data in calendar_events.items():
        if slug not in existing:
            existing[slug] = data
            new_count += 1
        else:
            meta = existing[slug]
            for k in ("date", "name", "uri"):
                if not meta.get(k):
                    meta[k] = data[k]
            if "sheet_id" not in meta:
                meta["sheet_id"] = None
            if "tab" not in meta:
                meta["tab"] = "Match Up Input"

    sorted_slugs = sorted(
        existing.keys(),
        key=lambda s: (str(existing[s].get("date") or ""), s),
    )
    sorted_challenges = {s: existing[s] for s in sorted_slugs}

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    content = dump_challenges_yaml(sorted_challenges, year)
    yaml_path.write_text(content, encoding="utf-8")

    logger.info(
        "Saved %d challenges to %s (%d newly added)",
        len(sorted_challenges),
        yaml_path,
        new_count,
    )
    return yaml_path


def validate_legacy_challenges_yaml(yaml_path: Path) -> list[str]:
    """Validate a legacy_challenges.yaml file to ensure all entries have required keys.

    Required keys per entry: ('date', 'name', 'uri').

    Args:
        yaml_path: Path to the YAML file to validate.

    Returns:
        A list of error message strings, or an empty list if validation succeeds.
    """
    if not yaml_path.is_file():
        return [f"{yaml_path}: File not found"]

    try:
        with open(yaml_path, "r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
    except Exception as exc:
        return [f"{yaml_path}: Failed to parse YAML: {exc}"]

    if data is None:
        return [f"{yaml_path}: File is empty"]

    if not isinstance(data, dict):
        return [f"{yaml_path}: Top-level content must be a mapping of slugs to entries"]

    errors: list[str] = []
    for slug, entry in data.items():
        if not isinstance(entry, dict):
            errors.append(
                f"{yaml_path}: Entry '{slug}' must be a mapping, got {type(entry).__name__}"
            )
            continue

        for key in REQUIRED_CHALLENGE_KEYS:
            if key not in entry:
                errors.append(
                    f"{yaml_path}: Entry '{slug}' is missing required key '{key}'"
                )
            elif entry[key] is None or (
                isinstance(entry[key], str) and not entry[key].strip()
            ):
                errors.append(
                    f"{yaml_path}: Entry '{slug}' has empty required key '{key}'"
                )

    return errors


def validate_all_legacy_challenges_yamls(
    base_dir: Path | None = None,
) -> tuple[int, list[str]]:
    """Find and validate all datasources/legacy-data-collection/$YEAR/legacy_challenges.yaml files.

    Args:
        base_dir: Base directory for legacy-data-collection. Defaults to
            REPO_ROOT / 'datasources' / 'legacy-data-collection'.

    Returns:
        A tuple of (total_entries_validated, errors_list).
    """
    if base_dir is None:
        base_dir = REPO_ROOT / "datasources" / "legacy-data-collection"

    yaml_paths = [
        p
        for p in sorted(base_dir.glob("*/legacy_challenges.yaml"))
        if p.parent.name.isdigit()
    ]

    if not yaml_paths:
        return 0, [
            f"No legacy_challenges.yaml files found matching {base_dir}/$YEAR/legacy_challenges.yaml"
        ]

    all_errors: list[str] = []
    total_entries = 0

    for ypath in yaml_paths:
        errors = validate_legacy_challenges_yaml(ypath)
        all_errors.extend(errors)
        if not errors:
            with open(ypath, "r", encoding="utf-8") as fp:
                data = yaml.safe_load(fp)
                if isinstance(data, dict):
                    total_entries += len(data)

    return total_entries, all_errors


def main() -> None:
    """CLI entrypoint for pulling Legacy Data Collection sheets."""
    parser = argparse.ArgumentParser(
        description="Pull and convert Legacy Data Collection Google Sheets into standard match JSON."
    )
    parser.add_argument(
        "--update-yaml",
        type=int,
        metavar="YEAR",
        help="Create or update legacy_challenges.yaml for the specified YEAR from the MTGO calendar",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate all legacy_challenges.yaml files for required keys ('date', 'name', 'uri')",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only process tournaments on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--slug", type=str, default=None, help="Process a single tournament slug"
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing output JSON files"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview output without writing files"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose debug logging"
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    start_date: datetime.date | None = None
    if args.start_date:
        start_date = parse_date(args.start_date)
        if start_date is None:
            parser.error(
                f"Invalid --start-date '{args.start_date}': must be in YYYY-MM-DD format."
            )

    if args.validate:
        total_entries, errors = validate_all_legacy_challenges_yamls()
        if errors:
            for err in errors:
                logger.error(err)
            logger.error(
                "Validation failed with %d error(s) across legacy_challenges.yaml files",
                len(errors),
            )
            sys.exit(1)
        logger.info(
            "Successfully validated %d entries across legacy_challenges.yaml files",
            total_entries,
        )
        return

    if args.update_yaml:
        update_yaml_for_year(args.update_yaml)
        return

    ldc_dir = REPO_ROOT / "datasources" / "legacy-data-collection"
    yaml_paths = sorted(ldc_dir.glob("**/legacy_challenges.yaml"))

    if not yaml_paths:
        logger.error("No legacy_challenges.yaml mapping files found in %s", ldc_dir)
        sys.exit(1)

    challenges_map: dict[str, Any] = {}
    for ypath in yaml_paths:
        with open(ypath, "r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
            challenges_map.update(data)

    if args.slug:
        if args.slug not in challenges_map:
            logger.error("Slug '%s' not found in any mapping file", args.slug)
            sys.exit(1)
        meta = challenges_map[args.slug]
        if start_date:
            tourney_date = parse_date(meta.get("date"))
            if tourney_date is not None and tourney_date < start_date:
                logger.info(
                    "Skipping %s: tournament date %s is before start date %s",
                    args.slug,
                    tourney_date,
                    start_date,
                )
                sys.exit(0)
        success = process_challenge(
            args.slug,
            meta,
            dry_run=args.dry_run,
            force=args.force,
        )
        sys.exit(0 if success else 1)

    # Process all entries with a sheet_id
    mapped_count = 0
    success_count = 0
    for slug, meta in challenges_map.items():
        if start_date:
            tourney_date = parse_date(meta.get("date"))
            if tourney_date is None:
                logger.warning(
                    "Skipping %s: invalid or missing date '%s' with --start-date active",
                    slug,
                    meta.get("date"),
                )
                continue
            if tourney_date < start_date:
                continue

        if meta.get("sheet_id"):
            mapped_count += 1
            if process_challenge(
                slug,
                meta,
                dry_run=args.dry_run,
                force=args.force,
            ):
                success_count += 1

    logger.info(
        "Done. Processed %d/%d mapped challenges across %d mapping file(s).",
        success_count,
        mapped_count,
        len(yaml_paths),
    )


if __name__ == "__main__":
    main()
