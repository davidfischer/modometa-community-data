"""MTGO official web scrapers and tournament calendar helpers."""

import logging
import time
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from modometa_community_data.common import USER_AGENT


logger = logging.getLogger(__name__)

MTGO_CALENDAR_URL = "https://www.mtgo.com/decklists/{year}/{month:02d}"


def fetch_mtgo_calendar_challenges(
    year: int,
    session: requests.Session | None = None,
    format_name: str = "legacy",
) -> dict[str, dict[str, Any]]:
    """Fetch Challenge events for a year from the official MTGO calendar.

    Args:
        year: Tournament calendar year (e.g. 2025, 2026).
        session: Optional requests.Session instance to reuse connection pooling.
        format_name: Name of MTG format to filter ('legacy', 'vintage', etc.).

    Returns:
        Dictionary mapping tournament slug to tournament metadata.
    """
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    events: dict[str, dict[str, Any]] = {}
    logger.info("Fetching MTGO calendar for %d (%s)...", year, format_name)

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
            title_lower = title.lower()

            if format_name == "legacy":
                if not ("legacy" in title_lower and "challenge" in title_lower):
                    continue
                default_tab = "Match Up Input"
            elif format_name == "vintage":
                if not (
                    "vintage" in title_lower
                    and any(
                        k in title_lower
                        for k in ("challenge", "qualifier", "showcase", "championship")
                    )
                ):
                    continue
                default_tab = "Standings"
            else:
                if format_name.lower() not in title_lower:
                    continue
                default_tab = "Match Up Input"

            a = item.select_one("a")
            t = item.select_one("time")
            if not a or not t:
                continue

            href = a.get("href", "")
            raw_datetime = t.get("datetime") or ""
            date_str = raw_datetime[:10]
            if not date_str.startswith(str(year)):
                continue

            slug = href.split("?")[0].rstrip("/").split("/")[-1]
            uri = urllib.parse.urljoin("https://www.mtgo.com", href)

            events[slug] = {
                "date": date_str,
                "datetime": raw_datetime,
                "name": title,
                "uri": uri,
                "sheet_id": None,
                "tab": default_tab,
            }

    logger.info(
        "Discovered %d %s event(s) for %d from MTGO calendar",
        len(events),
        format_name.capitalize(),
        year,
    )
    return events
