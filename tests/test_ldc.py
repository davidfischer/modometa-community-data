import datetime
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import yaml

from modometa_community_data.ldc import USER_AGENT
from modometa_community_data.ldc import deduce_swiss_rounds
from modometa_community_data.ldc import dump_challenges_yaml
from modometa_community_data.ldc import fetch_mtgo_calendar_challenges
from modometa_community_data.ldc import fetch_sheet_csv
from modometa_community_data.ldc import main
from modometa_community_data.ldc import parse_date
from modometa_community_data.ldc import parse_ldc_sheet_csv
from modometa_community_data.ldc import parse_match_result
from modometa_community_data.ldc import process_challenge
from modometa_community_data.ldc import update_yaml_for_year


def test_deduce_swiss_rounds():
    assert deduce_swiss_rounds(None) == 7
    assert deduce_swiss_rounds(8) == 3
    assert deduce_swiss_rounds(16) == 4
    assert deduce_swiss_rounds(32) == 5
    assert deduce_swiss_rounds(64) == 6
    assert deduce_swiss_rounds(82) == 7
    assert deduce_swiss_rounds(128) == 7
    assert deduce_swiss_rounds(200) == 8
    assert deduce_swiss_rounds(300) == 9


def test_parse_match_result():
    assert parse_match_result("2-1") == (2, 1, 0)
    assert parse_match_result("2-0") == (2, 0, 0)
    assert parse_match_result("0-2") == (0, 2, 0)
    assert parse_match_result("1-1-1") == (1, 1, 1)
    assert parse_match_result("2-0-1") == (2, 0, 1)
    assert parse_match_result("Bye") is None
    assert parse_match_result("bye") is None
    assert parse_match_result("Drop") is None
    assert parse_match_result("") is None
    assert parse_match_result("invalid") is None


def test_parse_ldc_sheet_csv_symmetric():
    sample_csv = """Player,Archetype,Round 1,,Round 2,,Round 3,
Alice,Aggro,Bob,2-1,Charlie,2-0,Dave,2-1
Bob,Control,Alice,1-2,Dave,2-0,Charlie,0-2
Charlie,Combo,Dave,2-0,Alice,0-2,Bob,2-0
Dave,Midrange,Charlie,0-2,Bob,0-2,Alice,1-2
"""
    rounds, players, warnings = parse_ldc_sheet_csv(sample_csv, swiss_rounds=2)
    assert len(warnings) == 0
    assert len(players) == 4
    assert players[0] == {"Player": "Alice", "Archetype": "Aggro"}
    assert players[1] == {"Player": "Bob", "Archetype": "Control"}
    assert players[2] == {"Player": "Charlie", "Archetype": "Combo"}
    assert players[3] == {"Player": "Dave", "Archetype": "Midrange"}

    # Store everything: swiss and top8 rounds
    assert len(rounds) == 3

    # Round 1 is Swiss
    r1 = rounds[0]
    assert r1["RoundName"] == "Round 1"
    assert r1["RoundType"] == "Swiss"
    assert len(r1["Matches"]) == 2
    alice_match = next(m for m in r1["Matches"] if m["Player1"] == "Alice")
    assert alice_match["Player2"] == "Bob"
    assert alice_match["Result"] == "2-1-0"

    # Round 2 is Swiss
    r2 = rounds[1]
    assert r2["RoundName"] == "Round 2"
    assert r2["RoundType"] == "Swiss"

    # Round 3 is Top8 (beyond swiss_rounds=2)
    r3 = rounds[2]
    assert r3["RoundName"] == "Round 3"
    assert r3["RoundType"] == "Top8"


def test_parse_ldc_sheet_csv_conflict_warning():
    # Alice claims she beat Bob 2-0, but Bob also claims he beat Alice 2-0
    conflict_csv = """Player,Archetype,Round 1,
Alice,Aggro,Bob,2-0
Bob,Control,Alice,2-0
"""
    rounds, players, warnings = parse_ldc_sheet_csv(conflict_csv)
    assert len(warnings) == 1
    assert "score disagreement" in warnings[0]
    assert len(rounds) == 0  # Conflicting match was dropped
    assert len(players) == 2


def test_parse_ldc_sheet_csv_opponent_mismatch():
    # Alice claims she played Bob, but Bob claims he played Charlie
    mismatch_csv = """Player,Archetype,Round 1,
Alice,Aggro,Bob,2-0
Bob,Control,Charlie,2-0
"""
    rounds, players, warnings = parse_ldc_sheet_csv(mismatch_csv)
    assert len(warnings) == 1
    assert "pairing mismatch" in warnings[0]
    assert len(rounds) == 0
    assert len(players) == 2


def test_parse_ldc_sheet_csv_deduce_top8_bracket():
    # 10 players, Round 1 has 5 matches (Swiss), Round 2 (QF: 4 matches), Round 3 (SF: 2 matches), Round 4 (Finals: 1 match)
    csv_text = """Player,Archetype,Round 1,,Round 2,,Round 3,,Round 4,
P1,A,P2,2-0,P2,2-0,P3,2-0,P5,2-0
P2,B,P1,0-2,P1,0-2,,,,
P3,C,P4,2-0,P4,2-0,P1,0-2,,
P4,D,P3,0-2,P3,0-2,,,,
P5,E,P6,2-0,P6,2-0,P7,2-0,P1,0-2
P6,F,P5,0-2,P5,0-2,,,,
P7,G,P8,2-0,P8,2-0,P5,0-2,,
P8,H,P7,0-2,P7,0-2,,,,
P9,I,P10,2-0,,,,,,
P10,J,P9,0-2,,,,,,
"""
    rounds, players, warnings = parse_ldc_sheet_csv(csv_text)
    assert len(players) == 10
    assert players[0] == {"Player": "P1", "Archetype": "A"}
    assert len(rounds) == 4
    # Round 1 has 5 matches (>4 matches) -> Swiss
    assert rounds[0]["RoundName"] == "Round 1"
    assert rounds[0]["RoundType"] == "Swiss"
    assert len(rounds[0]["Matches"]) == 5

    # Round 2 has 4 matches (QF) -> Top8
    assert rounds[1]["RoundName"] == "Round 2"
    assert rounds[1]["RoundType"] == "Top8"
    assert len(rounds[1]["Matches"]) == 4

    # Round 3 has 2 matches (SF) -> Top8
    assert rounds[2]["RoundName"] == "Round 3"
    assert rounds[2]["RoundType"] == "Top8"
    assert len(rounds[2]["Matches"]) == 2

    # Round 4 has 1 match (Finals) -> Top8
    assert rounds[3]["RoundName"] == "Round 4"
    assert rounds[3]["RoundType"] == "Top8"
    assert len(rounds[3]["Matches"]) == 1


def test_process_challenge_missing_name_skipped(caplog):
    meta = {
        "sheet_id": "test_sheet_id",
        "date": "2026-09-13",
    }
    result = process_challenge("test-slug", meta)
    assert result is False
    assert "missing name in metadata" in caplog.text


def test_dump_challenges_yaml():
    challenges = {
        "legacy-challenge-32-2025-01-041234": {
            "date": "2025-01-04",
            "name": "Legacy Challenge 32",
            "uri": "https://www.mtgo.com/decklist/legacy-challenge-32-2025-01-041234",
            "sheet_id": None,
            "tab": "Match Up Input",
        },
        "legacy-showcase-challenge-2025-01-055678": {
            "date": "2025-01-05",
            "name": "Legacy Showcase Challenge",
            "uri": "https://www.mtgo.com/decklist/legacy-showcase-challenge-2025-01-055678",
            "sheet_id": "test_sheet_123",
            "tab": "Match Up Input",
        },
    }
    yaml_text = dump_challenges_yaml(challenges, 2025)
    assert "# Legacy Challenges 2025 - LDC Google Sheet Mapping" in yaml_text
    assert "sheet_id: null" in yaml_text
    assert "sheet_id: 'test_sheet_123'" in yaml_text
    # Round-trip check
    loaded = yaml.safe_load(yaml_text)
    assert loaded == challenges


def test_fetch_mtgo_calendar_challenges():
    sample_html = """
    <ul>
        <li class="decklists-item">
            <a href="/decklist/legacy-challenge-32-2025-01-041234">
                <div><h3>Legacy Challenge 32</h3></div>
                <time datetime="2025-01-04T18:00:00Z">January 4, 2025</time>
            </a>
        </li>
        <li class="decklists-item">
            <a href="/decklist/legacy-showcase-challenge-2025-01-055678">
                <div><h3>Legacy Showcase Challenge</h3></div>
                <time datetime="2025-01-05T18:00:00Z">January 5, 2025</time>
            </a>
        </li>
        <li class="decklists-item">
            <a href="/decklist/legacy-league-2025-01-069999">
                <div><h3>Legacy League</h3></div>
                <time datetime="2025-01-06T18:00:00Z">January 6, 2025</time>
            </a>
        </li>
        <li class="decklists-item">
            <a href="/decklist/modern-challenge-32-2025-01-078888">
                <div><h3>Modern Challenge 32</h3></div>
                <time datetime="2025-01-07T18:00:00Z">January 7, 2025</time>
            </a>
        </li>
        <li class="decklists-item">
            <a href="/decklist/legacy-challenge-32-2024-12-310000">
                <div><h3>Legacy Challenge 32</h3></div>
                <time datetime="2024-12-31T18:00:00Z">December 31, 2024</time>
            </a>
        </li>
    </ul>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://www.mtgo.com/decklists/2025/01"
    mock_resp.text = sample_html

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    events = fetch_mtgo_calendar_challenges(2025, session=mock_session)
    assert len(events) == 2
    assert "legacy-challenge-32-2025-01-041234" in events
    assert "legacy-showcase-challenge-2025-01-055678" in events
    assert events["legacy-challenge-32-2025-01-041234"]["date"] == "2025-01-04"
    assert (
        events["legacy-challenge-32-2025-01-041234"]["uri"]
        == "https://www.mtgo.com/decklist/legacy-challenge-32-2025-01-041234"
    )
    assert events["legacy-challenge-32-2025-01-041234"]["sheet_id"] is None


def test_update_yaml_for_year(tmp_path):
    mock_events = {
        "slug-1": {
            "date": "2025-01-01",
            "name": "Legacy Challenge 32",
            "uri": "https://www.mtgo.com/decklist/slug-1",
            "sheet_id": None,
            "tab": "Match Up Input",
        },
        "slug-2": {
            "date": "2025-01-02",
            "name": "Legacy Challenge 32",
            "uri": "https://www.mtgo.com/decklist/slug-2",
            "sheet_id": None,
            "tab": "Match Up Input",
        },
    }

    with (
        patch("modometa_community_data.ldc.REPO_ROOT", tmp_path),
        patch(
            "modometa_community_data.ldc.fetch_mtgo_calendar_challenges",
            return_value=mock_events,
        ),
    ):
        out_path = update_yaml_for_year(2025)
        assert out_path.exists()
        loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert len(loaded) == 2
        assert "slug-1" in loaded
        assert "slug-2" in loaded

        # Test updating with existing sheet_id preserved
        loaded["slug-1"]["sheet_id"] = "user_provided_sheet_id"
        out_path.write_text(yaml.dump(loaded), encoding="utf-8")

        mock_events["slug-3"] = {
            "date": "2025-01-03",
            "name": "Legacy Challenge 32",
            "uri": "https://www.mtgo.com/decklist/slug-3",
            "sheet_id": None,
            "tab": "Match Up Input",
        }

        update_yaml_for_year(2025)
        updated_loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert len(updated_loaded) == 3
        # Ensure user_provided_sheet_id was preserved
        assert updated_loaded["slug-1"]["sheet_id"] == "user_provided_sheet_id"
        assert updated_loaded["slug-3"]["sheet_id"] is None


def test_main_update_yaml(monkeypatch):
    with patch("modometa_community_data.ldc.update_yaml_for_year") as mock_update:
        monkeypatch.setattr("sys.argv", ["pull-ldc", "--update-yaml", "2025"])
        main()
        mock_update.assert_called_once_with(2025)


def test_fetch_sheet_csv_uses_user_agent():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "test,csv\n1,2"
    with patch("requests.get", return_value=mock_resp) as mock_get:
        fetch_sheet_csv("test_id", "Match Up Input")
        mock_get.assert_called_once()
        headers = mock_get.call_args[1].get("headers", {})
        assert headers.get("User-Agent") == USER_AGENT


def test_parse_date():
    assert parse_date("2026-09-13") == datetime.date(2026, 9, 13)
    assert parse_date("2026-09-13T18:00:00Z") == datetime.date(2026, 9, 13)
    assert parse_date(datetime.date(2026, 9, 13)) == datetime.date(2026, 9, 13)
    assert parse_date(datetime.datetime(2026, 9, 13, 10, 0)) == datetime.date(
        2026, 9, 13
    )
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("not-a-date") is None


def test_main_start_date_filter(monkeypatch):
    challenges = {
        "slug-old": {
            "date": "2026-09-01",
            "name": "Old Challenge",
            "uri": "https://www.mtgo.com/slug-old",
            "sheet_id": "sheet_old",
            "tab": "Match Up Input",
        },
        "slug-new": {
            "date": "2026-09-15",
            "name": "New Challenge",
            "uri": "https://www.mtgo.com/slug-new",
            "sheet_id": "sheet_new",
            "tab": "Match Up Input",
        },
    }

    processed_slugs = []

    def mock_process(slug, meta, dry_run=False, force=False):
        processed_slugs.append(slug)
        return True

    monkeypatch.setattr(
        "sys.argv",
        ["pull-ldc", "--start-date", "2026-09-10", "--force"],
    )

    with (
        patch("pathlib.Path.glob", return_value=[MagicMock()]),
        patch("builtins.open", MagicMock()),
        patch("yaml.safe_load", return_value=challenges),
        patch(
            "modometa_community_data.ldc.process_challenge", side_effect=mock_process
        ),
    ):
        main()

    assert processed_slugs == ["slug-new"]


def test_main_start_date_invalid(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pull-ldc", "--start-date", "invalid-date"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2


def test_main_slug_before_start_date(monkeypatch):
    challenges = {
        "slug-1": {
            "date": "2026-09-01",
            "name": "Challenge 1",
            "uri": "https://www.mtgo.com/slug-1",
            "sheet_id": "sheet_1",
        }
    }
    monkeypatch.setattr(
        "sys.argv",
        ["pull-ldc", "--slug", "slug-1", "--start-date", "2026-09-10"],
    )
    with (
        patch("pathlib.Path.glob", return_value=[MagicMock()]),
        patch("builtins.open", MagicMock()),
        patch("yaml.safe_load", return_value=challenges),
        patch("modometa_community_data.ldc.process_challenge") as mock_process,
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 0
    mock_process.assert_not_called()
