"""Vintage MTGO Community (VMC) Google Sheet data puller and normalizer."""

import argparse
import csv
import datetime
import io
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests
import yaml

from modometa_community_data.common import REPO_ROOT
from modometa_community_data.common import REQUIRED_CHALLENGE_KEYS
from modometa_community_data.common import USER_AGENT
from modometa_community_data.common import (
    dump_challenges_yaml as dump_challenges_yaml_base,
)
from modometa_community_data.common import fetch_sheet_csv
from modometa_community_data.common import parse_date
from modometa_community_data.mtgo import fetch_mtgo_calendar_challenges


logger = logging.getLogger(__name__)

# IMPORTANT: This sheet may change in the future
VMC_MERGE_SHEET_ID = "169L3p4pJrNYsBEpqBNllSwba4_ck7hyoLLqxsjQ5Rfc"
VMC_MERGE_SHEET_URL = (
    f"https://docs.google.com/spreadsheets/d/{VMC_MERGE_SHEET_ID}/edit"
)
VMC_DISCORD_URL = "https://discord.gg/2eVcsjK"
DEFAULT_VMC_TAB = "Standings"


def parse_tab_name(name: str) -> tuple[str, int] | None:
    """Parse merge sheet tab name (e.g. '09/20/2026 (1)' or '10/13/24') into (YYYY-MM-DD, index)."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\s*\((\d+)\))?$", name.strip())
    if not m:
        return None
    month, day, year, extra = m.groups()
    month, day, year = int(month), int(day), int(year)
    if year < 100:
        year += 2000
    try:
        dt = datetime.date(year, month, day)
        idx = int(extra) if extra else 0
        return dt.isoformat(), idx
    except ValueError:
        return None


def fetch_vmc_merge_sheet_mappings(
    session: requests.Session | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch all tab-to-Google-Sheet mappings from the Vintage merge sheet.

    Returns:
        dict mapping date string 'YYYY-MM-DD' to list of tab info dicts:
        [{'gid': str, 'name': str, 'idx': int, 'sheet_id': str | None, 'slug': str | None}]
    """
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    logger.info("Fetching Vintage merge sheet mappings from %s...", VMC_MERGE_SHEET_URL)
    resp = session.get(VMC_MERGE_SHEET_URL, timeout=45)
    resp.raise_for_status()
    html_text = resp.text

    # Extract tabs: list of (gid, tab_name)
    tab_patterns = re.findall(
        r'\[\d+,\d+,\\+"(\d+)\\+",\[\{\\+"1\\+":\[\[0,0,\\+"([^\\+"]+)\\+"',
        html_text,
    )

    # Extract IMPORTRANGE formulas: list of (sheet_id, gid)
    importrange_matches = re.findall(
        r'\[\\+"([a-zA-Z0-9_-]{44})\\+",\\+"(?:Overview|Input)![^\\+"]+\\+"\].*?\[\\+"(\d+)\\+",\d+,\d+\]',
        html_text,
    )
    gid_to_sheet_id: dict[str, str] = {}
    for sid, gid in importrange_matches:
        if gid not in gid_to_sheet_id:
            gid_to_sheet_id[gid] = sid

    tabs_by_date: dict[str, list[dict[str, Any]]] = {}
    for gid, name in tab_patterns:
        parsed = parse_tab_name(name)
        if not parsed:
            continue
        dt_str, idx = parsed
        sid = gid_to_sheet_id.get(gid)
        tab_info = {
            "gid": gid,
            "name": name,
            "idx": idx,
            "sheet_id": sid,
            "url": None,
            "slug": None,
        }
        tabs_by_date.setdefault(dt_str, []).append(tab_info)

    # Sort each date's tabs by sub-index
    for dt_str in tabs_by_date:
        tabs_by_date[dt_str].sort(key=lambda t: t["idx"])

    logger.info(
        "Discovered %d tabs across %d dates in Vintage merge sheet",
        sum(len(v) for v in tabs_by_date.values()),
        len(tabs_by_date),
    )
    return tabs_by_date


def enrich_multi_event_tab_urls(
    tabs_by_date: dict[str, list[dict[str, Any]]],
    year: int | None = None,
    session: requests.Session | None = None,
) -> None:
    """Fetch tab CSV for dates with multiple events to inspect MTGO URL/slug."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    for dt_str, t_list in tabs_by_date.items():
        if year is not None and not dt_str.startswith(str(year)):
            continue
        if len(t_list) <= 1:
            continue

        for t_info in t_list:
            if t_info.get("slug"):
                continue
            gid = t_info["gid"]
            tab_url = f"https://docs.google.com/spreadsheets/d/{VMC_MERGE_SHEET_ID}/gviz/tq?tqx=out:csv&gid={gid}"
            try:
                resp = session.get(tab_url, timeout=30)
                if resp.status_code == 200:
                    reader = csv.reader(io.StringIO(resp.text))
                    for row in list(reader)[:10]:
                        if len(row) >= 3 and "url" in row[1].lower() and row[2].strip():
                            url_val = row[2].strip()
                            t_info["url"] = url_val
                            slug = url_val.split("?")[0].rstrip("/").split("/")[-1]
                            t_info["slug"] = slug
                            break
            except Exception as exc:
                logger.debug("Could not fetch tab CSV for gid %s: %s", gid, exc)


def match_challenges_to_merge_sheet(
    mtgo_events: dict[str, dict[str, Any]],
    tabs_by_date: dict[str, list[dict[str, Any]]],
    year: int | None = None,
    session: requests.Session | None = None,
) -> None:
    """Assign sheet_id from merge sheet tabs to MTGO calendar events."""
    # Pre-fetch tab URLs for multi-event dates
    enrich_multi_event_tab_urls(tabs_by_date, year=year, session=session)

    matched_slugs: set[str] = set()

    # Step 1: Match by exact slug if URL/slug was extracted from tab
    for slug, ev in mtgo_events.items():
        dt = ev.get("date")
        t_list = tabs_by_date.get(dt, []) if dt else []
        for t in t_list:
            if t.get("slug") == slug:
                ev["sheet_id"] = t.get("sheet_id")
                matched_slugs.add(slug)
                break

    # Step 2: Match single-tab and single-event dates
    for slug, ev in mtgo_events.items():
        if slug in matched_slugs:
            continue
        dt = ev.get("date")
        t_list = tabs_by_date.get(dt, []) if dt else []
        events_on_date = [s for s, e in mtgo_events.items() if e.get("date") == dt]
        if len(t_list) == 1 and len(events_on_date) == 1:
            ev["sheet_id"] = t_list[0].get("sheet_id")
            matched_slugs.add(slug)

    # Step 3: Match remaining events on multi-event dates chronologically
    unmatched_by_date: dict[str, list[str]] = defaultdict(list)
    for slug, ev in mtgo_events.items():
        if slug not in matched_slugs:
            dt = ev.get("date")
            if dt:
                unmatched_by_date[dt].append(slug)

    for dt_str, slugs in unmatched_by_date.items():
        t_list = tabs_by_date.get(dt_str, [])
        used_sids = {
            mtgo_events[s]["sheet_id"]
            for s in matched_slugs
            if mtgo_events[s].get("date") == dt_str
        }
        available_tabs = [t for t in t_list if t.get("sheet_id") not in used_sids]

        if len(slugs) == 1 and len(available_tabs) == 1:
            mtgo_events[slugs[0]]["sheet_id"] = available_tabs[0].get("sheet_id")
            matched_slugs.add(slugs[0])
        elif len(slugs) == len(available_tabs):
            # Sort events by scheduled datetime and tabs by sub-index
            sorted_slugs = sorted(
                slugs, key=lambda s: mtgo_events[s].get("datetime", "")
            )
            sorted_tabs = sorted(available_tabs, key=lambda t: t.get("idx", 0))
            for s, t in zip(sorted_slugs, sorted_tabs):
                mtgo_events[s]["sheet_id"] = t.get("sheet_id")
                matched_slugs.add(s)


def parse_vmc_sheet_csv(
    csv_content: str,
    swiss_rounds: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """Parse Vintage MTGO Community 'Standings' CSV into list of rounds, players, and warnings.

    The 'Standings' sheet has:
    - Columns A-L (indices 0..11): Name1, Name2, Win, loss, Winner1, Winner2, Archetype1/2, Subarchetype1/2, Deck1/2
    - Columns N+ (indices 13+): Rank, Name, Record, Bye, followed by round columns:
      Round (Opponent Name), Games (P1 wins), (Opponent wins).

    Args:
        csv_content: Raw CSV string from the Google Sheet tab.
        swiss_rounds: Explicit number of Swiss rounds. If None, deduced from round columns.

    Returns:
        rounds: list of {"RoundName": str, "RoundType": str, "Matches": [...]}
        players: list of {"Player": str, "Archetype": str}
        warnings: list of warning strings for discrepancies
    """
    reader = list(csv.reader(io.StringIO(csv_content)))
    if not reader or not reader[0]:
        return [], [], ["Empty CSV content"]

    header = [h.strip() for h in reader[0]]

    # 1. Map column indices for player archetypes in columns 0..11
    # Typically: Name1(0), Name2(1), Archetype1(6), Archetype2(7), Subarchetype1(8), Subarchetype2(9), Deck1(10), Deck2(11)
    name1_col = 0
    name2_col = 1
    arch1_col = 6
    arch2_col = 7
    subarch1_col = 8
    subarch2_col = 9
    deck1_col = 10
    deck2_col = 11

    for idx, col_name in enumerate(header):
        low = col_name.lower()
        if low == "name1":
            name1_col = idx
        elif low == "name2":
            name2_col = idx
        elif low == "archetype1":
            arch1_col = idx
        elif low == "archetype2":
            arch2_col = idx
        elif low == "subarchetype1":
            subarch1_col = idx
        elif low == "subarchetype2":
            subarch2_col = idx
        elif low == "deck1":
            deck1_col = idx
        elif low == "deck2":
            deck2_col = idx

    # Collect player archetypes: prioritize Subarchetype, fallback to Archetype, then Deck
    player_arch: dict[str, tuple[str, str]] = {}
    for row in reader[1:]:
        if len(row) > max(name1_col, subarch1_col, arch1_col, deck1_col):
            p1 = row[name1_col].strip()
            if p1 and p1.lower() not in ("bye", "none", "drop"):
                sub = row[subarch1_col].strip() if len(row) > subarch1_col else ""
                arch = row[arch1_col].strip() if len(row) > arch1_col else ""
                deck = row[deck1_col].strip() if len(row) > deck1_col else ""
                chosen_arch = sub or arch or deck
                p1_low = p1.lower()
                if p1_low not in player_arch or (
                    not player_arch[p1_low][1] and chosen_arch
                ):
                    player_arch[p1_low] = (p1, chosen_arch)

        if len(row) > max(name2_col, subarch2_col, arch2_col, deck2_col):
            p2 = row[name2_col].strip()
            if p2 and p2.lower() not in ("bye", "none", "drop"):
                sub2 = row[subarch2_col].strip() if len(row) > subarch2_col else ""
                arch2 = row[arch2_col].strip() if len(row) > arch2_col else ""
                deck2 = row[deck2_col].strip() if len(row) > deck2_col else ""
                chosen_arch2 = sub2 or arch2 or deck2
                p2_low = p2.lower()
                if p2_low not in player_arch or (
                    not player_arch[p2_low][1] and chosen_arch2
                ):
                    player_arch[p2_low] = (p2, chosen_arch2)

    # 2. Locate player name column in visible standings (Col 14 / 'Name')
    player_col = 14
    for idx in range(12, min(len(header), 25)):
        if header[idx].lower() == "name":
            player_col = idx
            break

    # Build ordered players list from the standings table
    players: list[dict[str, str]] = []
    seen_players: set[str] = set()

    for row in reader[1:]:
        if len(row) > player_col:
            p = row[player_col].strip()
            if p and p.lower() not in ("bye", "none", "drop"):
                p_low = p.lower()
                if p_low not in seen_players:
                    seen_players.add(p_low)
                    arch = player_arch.get(p_low, (p, ""))[1]
                    players.append({"Player": p, "Archetype": arch})

    # 3. Discover active round columns from header
    # Standard VMC round columns start at or after column 18, stepping by 3
    active_round_cols: list[int] = []
    col_report_counts: dict[int, int] = {}
    for c in range(18, len(header), 3):
        # Round column header is typically 'Round'
        if c < len(header) and "round" in header[c].lower():
            # Check if any row has non-empty opponent data in this column
            cnt = sum(
                1
                for r in reader[1:]
                if len(r) > c
                and r[c].strip()
                and r[c].strip().lower() not in ("bye", "drop", "none")
            )
            if cnt > 0:
                active_round_cols.append(c)
                col_report_counts[c] = cnt

    if not active_round_cols:
        return [], players, ["No active round columns found in Standings sheet"]

    # In VMC sheets:
    # Playoff columns (if active) are typically: Col 24 (QF), Col 21 (SF), Col 18 (Finals)
    # A Top 8 playoff round cannot have more than 8 player reports (4 matches).
    # If a column in [24, 21, 18] has > 8 reports (e.g. 18 matches in Col 24 when finals split), it is Swiss.
    swiss_cols = [
        c
        for c in active_round_cols
        if c >= 27 or (c in (24, 21, 18) and col_report_counts[c] > 8)
    ]
    if swiss_rounds is not None:
        # Use rightmost swiss_rounds columns
        active_swiss_cols = (
            swiss_cols[-swiss_rounds:]
            if len(swiss_cols) >= swiss_rounds
            else swiss_cols
        )
    else:
        active_swiss_cols = swiss_cols

    # Reverse to get chronological Swiss order: Round 1, Round 2, ...
    ordered_swiss_cols = list(reversed(active_swiss_cols))

    # Playoff columns in chronological order: QF (24), SF (21), Finals (18)
    # Must have <= 8 player reports (<= 4 matches)
    ordered_playoff_cols = [
        c for c in [24, 21, 18] if c in active_round_cols and col_report_counts[c] <= 8
    ]

    ordered_round_cols: list[tuple[int, str]] = []
    for idx, c in enumerate(ordered_swiss_cols, 1):
        ordered_round_cols.append((c, "Swiss"))

    for c in ordered_playoff_cols:
        ordered_round_cols.append((c, "Top8"))

    # 4. Extract and reconcile matches for each round
    rounds: list[dict[str, Any]] = []
    warnings: list[str] = []

    for r_idx, (c, r_type) in enumerate(ordered_round_cols, 1):
        r_name = f"Round {r_idx}"
        reports: dict[str, tuple[str, str, int, int, int]] = {}

        for row in reader[1:]:
            if len(row) > max(c + 2, player_col):
                p1 = row[player_col].strip()
                opp = row[c].strip()
                if (
                    not p1
                    or not opp
                    or opp.lower() in ("bye", "drop", "none")
                    or p1.lower() in ("bye", "drop", "none")
                ):
                    continue

                w1_str = row[c + 1].strip() if len(row) > c + 1 else "0"
                w2_str = row[c + 2].strip() if len(row) > c + 2 else "0"

                # Parse game wins/losses
                w1 = int(w1_str) if w1_str.isdigit() else 0
                w2 = int(w2_str) if w2_str.isdigit() else 0
                draws = 0

                reports[p1.lower()] = (p1, opp, w1, w2, draws)

        # Reconcile and deduplicate matches
        matches: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()

        for p1_low, (p1, opp, w1, w2, draws) in reports.items():
            opp_low = opp.lower()
            pair_key = tuple(sorted([p1_low, opp_low]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            # Check reciprocal report
            reciprocal = reports.get(opp_low)
            if reciprocal:
                rec_p, rec_opp, rec_w1, rec_w2, rec_d = reciprocal
                if rec_opp.lower() != p1_low:
                    msg = (
                        f"{r_name} pairing mismatch: {p1} reported playing {opp}, "
                        f"but {opp} reported playing {rec_opp}. Skipping matchup."
                    )
                    warnings.append(msg)
                    logger.warning(msg)
                    seen_pairs.add(tuple(sorted([opp_low, rec_opp.lower()])))
                    continue

                if (rec_w1 != w2) or (rec_w2 != w1) or (rec_d != draws):
                    msg = (
                        f"{r_name} score disagreement: {p1} reported {w1}-{w2}-{draws}, "
                        f"but {opp} reported {rec_w1}-{rec_w2}-{rec_d}. Skipping matchup."
                    )
                    warnings.append(msg)
                    logger.warning(msg)
                    continue

            matches.append(
                {
                    "Player1": p1,
                    "Player2": opp,
                    "Result": f"{w1}-{w2}-{draws}",
                }
            )

        matches.sort(key=lambda m: (m["Player1"].lower(), m["Player2"].lower()))
        rounds.append(
            {
                "RoundName": r_name,
                "RoundType": r_type,
                "Matches": matches,
            }
        )

    # Ensure any player in matches is also present in players list
    for r in rounds:
        for m in r["Matches"]:
            for p in (m["Player1"], m["Player2"]):
                p_low = p.lower()
                if p_low not in seen_players:
                    seen_players.add(p_low)
                    arch = player_arch.get(p_low, (p, ""))[1]
                    players.append({"Player": p, "Archetype": arch})

    return rounds, players, warnings


def process_challenge(
    slug: str,
    meta: dict[str, Any],
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Process a single Vintage challenge entry from YAML mapping."""
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
    out_dir = REPO_ROOT / "datasources" / "vintage-mtgo-community" / year / month
    out_file = out_dir / f"{slug}.json"

    if out_file.exists() and not force and not dry_run:
        logger.debug(
            "Skipping %s: already exists at %s (use --force to overwrite)",
            slug,
            out_file,
        )
        return True

    tab = meta.get("tab") or DEFAULT_VMC_TAB

    logger.info(
        "Processing %s (sheet_id=%s, tab=%s)...",
        slug,
        sheet_id,
        tab,
    )

    try:
        csv_text = fetch_sheet_csv(sheet_id, tab)
    except Exception as exc:
        logger.error("Could not fetch sheet for %s: %s", slug, exc)
        return False

    rounds, players, warnings = parse_vmc_sheet_csv(csv_text)
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
            "Source": "vintage-mtgo-community",
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
    """Format Vintage challenges mapping as clean YAML matching repository conventions."""
    lines = [
        f"# Vintage Challenges {year} - Vintage MTGO Community Google Sheet Mapping",
        "# Generated from official MTGO tournament calendar and Vintage MTGO Community merge sheet.",
        "#",
        "# Schema per entry:",
        "#   date: Tournament date (YYYY-MM-DD)",
        "#   name: Tournament name",
        "#   uri: Official MTGO tournament decklist URL",
        "#   sheet_id: Google Sheet ID (from docs.google.com/spreadsheets/d/<ID>/...) or null",
        '#   tab: Tab name in the Google Sheet (defaults to "Standings")',
        "",
    ]
    return dump_challenges_yaml_base(
        challenges, header_lines=lines, default_tab=DEFAULT_VMC_TAB
    )


def update_yaml_for_year(year: int) -> Path:
    """Create or update vintage_challenges.yaml for year from MTGO calendar and merge sheet."""
    yaml_path = (
        REPO_ROOT
        / "datasources"
        / "vintage-mtgo-community"
        / str(year)
        / "vintage_challenges.yaml"
    )
    existing: dict[str, Any] = {}
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as fp:
            existing = yaml.safe_load(fp) or {}

    calendar_events = fetch_mtgo_calendar_challenges(year, format_name="vintage")

    # Fetch merge sheet mappings and match
    try:
        tabs_by_date = fetch_vmc_merge_sheet_mappings()
        match_challenges_to_merge_sheet(calendar_events, tabs_by_date, year=year)
    except Exception as exc:
        logger.warning(
            "Could not fetch or match merge sheet mappings for %d: %s", year, exc
        )

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
            if not meta.get("sheet_id") and data.get("sheet_id"):
                meta["sheet_id"] = data["sheet_id"]
            elif "sheet_id" not in meta:
                meta["sheet_id"] = None
            if "tab" not in meta:
                meta["tab"] = DEFAULT_VMC_TAB

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


def validate_vintage_challenges_yaml(yaml_path: Path) -> list[str]:
    """Validate a vintage_challenges.yaml file to ensure all entries have required keys."""
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


def validate_all_vintage_challenges_yamls(
    base_dir: Path | None = None,
) -> tuple[int, list[str]]:
    """Find and validate all datasources/vintage-mtgo-community/$YEAR/vintage_challenges.yaml files."""
    if base_dir is None:
        base_dir = REPO_ROOT / "datasources" / "vintage-mtgo-community"

    yaml_paths = [
        p
        for p in sorted(base_dir.glob("*/vintage_challenges.yaml"))
        if p.parent.name.isdigit()
    ]

    if not yaml_paths:
        return 0, [
            f"No vintage_challenges.yaml files found matching {base_dir}/$YEAR/vintage_challenges.yaml"
        ]

    all_errors: list[str] = []
    total_entries = 0

    for ypath in yaml_paths:
        errors = validate_vintage_challenges_yaml(ypath)
        all_errors.extend(errors)
        if not errors:
            with open(ypath, "r", encoding="utf-8") as fp:
                data = yaml.safe_load(fp)
                if isinstance(data, dict):
                    total_entries += len(data)

    return total_entries, all_errors


def main() -> None:
    """CLI entrypoint for pulling Vintage MTGO Community sheets."""
    parser = argparse.ArgumentParser(
        description="Pull and convert Vintage MTGO Community Google Sheets into standard match JSON."
    )
    parser.add_argument(
        "--update-yaml",
        type=int,
        metavar="YEAR",
        help="Create or update vintage_challenges.yaml for the specified YEAR from the MTGO calendar and merge sheet",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate all vintage_challenges.yaml files for required keys ('date', 'name', 'uri')",
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
        total_entries, errors = validate_all_vintage_challenges_yamls()
        if errors:
            for err in errors:
                logger.error(err)
            logger.error(
                "Validation failed with %d error(s) across vintage_challenges.yaml files",
                len(errors),
            )
            sys.exit(1)
        logger.info(
            "Successfully validated %d entries across vintage_challenges.yaml files",
            total_entries,
        )
        return

    if args.update_yaml:
        update_yaml_for_year(args.update_yaml)
        return

    vmc_dir = REPO_ROOT / "datasources" / "vintage-mtgo-community"
    yaml_paths = sorted(vmc_dir.glob("**/vintage_challenges.yaml"))

    if not yaml_paths:
        logger.error("No vintage_challenges.yaml mapping files found in %s", vmc_dir)
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
