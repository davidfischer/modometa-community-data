"""Common utilities, constants, and helper functions for community data pullers."""

import datetime
import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import requests


logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_USER_AGENT = "MODOMeta-Community-Data-Puller/0.1 (+https://modometa.com)"
USER_AGENT = os.environ.get("USER_AGENT", DEFAULT_USER_AGENT)
REQUIRED_CHALLENGE_KEYS = ("date", "name", "uri")
SCORE_RE = re.compile(r"^(\d+)-(\d+)(?:-(\d+))?$")


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
    m = SCORE_RE.match(cleaned)
    if not m:
        return None
    p1 = int(m.group(1))
    p2 = int(m.group(2))
    draws = int(m.group(3)) if m.group(3) is not None else 0
    return p1, p2, draws


def parse_date(val: Any) -> datetime.date | None:
    """Parse a date value (string or date/datetime object) into datetime.date."""
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return None
        if len(cleaned) >= 10 and cleaned[4] == "-" and cleaned[7] == "-":
            cleaned = cleaned[:10]
        try:
            return datetime.date.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def fetch_sheet_csv(
    sheet_id: str,
    tab: str = "Match Up Input",
    session: requests.Session | None = None,
) -> str:
    """Fetch CSV content from public Google Sheet via gviz endpoint without auth."""
    quoted_tab = urllib.parse.quote(tab)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={quoted_tab}"
    logger.debug("Fetching sheet CSV from: %s", url)

    headers = {
        "User-Agent": USER_AGENT,
    }
    try:
        if session is not None:
            resp = session.get(url, headers=headers, timeout=30)
        else:
            resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.error("Failed to fetch Google Sheet %s tab '%s': %s", sheet_id, tab, exc)
        raise


def dump_challenges_yaml(
    challenges: dict[str, dict[str, Any]],
    header_lines: list[str] | None = None,
    default_tab: str = "Match Up Input",
) -> str:
    """Format challenges mapping as clean YAML matching repository conventions."""
    lines = list(header_lines or [])
    for slug, meta in challenges.items():
        date_val = meta.get("date", "")
        name_val = str(meta.get("name", "")).replace("'", "''")
        uri_val = meta.get("uri", "")
        sheet_id_val = meta.get("sheet_id")
        tab_val = str(meta.get("tab") or default_tab).replace("'", "''")

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
