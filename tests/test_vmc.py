from unittest.mock import MagicMock
from unittest.mock import patch

import yaml

from modometa_community_data.vmc import dump_challenges_yaml
from modometa_community_data.vmc import main
from modometa_community_data.vmc import match_challenges_to_merge_sheet
from modometa_community_data.vmc import parse_tab_name
from modometa_community_data.vmc import parse_vmc_sheet_csv
from modometa_community_data.vmc import process_challenge
from modometa_community_data.vmc import update_yaml_for_year


def test_parse_tab_name():
    assert parse_tab_name("09/20/2026 (1)") == ("2026-09-20", 1)
    assert parse_tab_name("09/20/2026") == ("2026-09-20", 0)
    assert parse_tab_name("10/13/24") == ("2024-10-13", 0)
    assert parse_tab_name("1/5/2025 (2)") == ("2025-01-05", 2)
    assert parse_tab_name("Overall") is None
    assert parse_tab_name("Merge") is None
    assert parse_tab_name("invalid") is None
    assert parse_tab_name("") is None


def test_dump_challenges_yaml():
    challenges = {
        "vintage-challenge-32-2026-01-0112828149": {
            "date": "2026-01-01",
            "name": "Vintage Challenge 32",
            "uri": "https://www.mtgo.com/decklist/vintage-challenge-32-2026-01-0112828149",
            "sheet_id": None,
            "tab": "Standings",
        },
        "vintage-challenge-32-2026-09-2012854520": {
            "date": "2026-09-20",
            "name": "Vintage Challenge 32",
            "uri": "https://www.mtgo.com/decklist/vintage-challenge-32-2026-09-2012854520",
            "sheet_id": "test_sheet_123",
            "tab": "Standings",
        },
    }
    yaml_text = dump_challenges_yaml(challenges, 2026)
    assert (
        "# Vintage Challenges 2026 - Vintage MTGO Community Google Sheet Mapping"
        in yaml_text
    )
    assert "sheet_id: null" in yaml_text
    assert "sheet_id: 'test_sheet_123'" in yaml_text
    assert "tab: 'Standings'" in yaml_text
    # Round-trip check
    loaded = yaml.safe_load(yaml_text)
    assert loaded == challenges


def test_parse_vmc_sheet_csv_symmetric():
    # 4 players: Alice, Bob, Charlie, Dave
    # Swiss round 1 (col 27), Playoff round (col 18 - Finals)
    header = [
        "Name1",
        "Name2",
        "Win ",
        "loss",
        "Winner1",
        "Winner2",
        "Archetype1",
        "Archetype2",
        "Subarchetype1",
        "Subarchetype2",
        "Deck1",
        "Deck2",
        "",
        "Rank",
        "Name",
        "Record",
        "",
        "Bye",
        "Round",
        "Games",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Round",
        "Games",
        "",
    ]
    # Col 18 is Finals, Col 27 is Swiss Round 1
    # Col 0..11 has match rows
    # Col 14 has player standings
    row1 = [
        "Alice",
        "Bob",
        "2",
        "1",
        "1",
        "0",
        "Shops",
        "Aggro",
        "Jewel Shops",
        "Initiative",
        "",
        "",
        "",
        "1",
        "Alice",
        "2",
        "0",
        "",
        "Bob",
        "2",
        "1",
        "",
        "",
        "",
        "",
        "",
        "",
        "Bob",
        "2",
        "0",
    ]
    row2 = [
        "Bob",
        "Alice",
        "1",
        "2",
        "0",
        "1",
        "Aggro",
        "Shops",
        "Initiative",
        "Jewel Shops",
        "",
        "",
        "",
        "2",
        "Bob",
        "1",
        "1",
        "",
        "Alice",
        "1",
        "2",
        "",
        "",
        "",
        "",
        "",
        "",
        "Alice",
        "0",
        "2",
    ]
    row3 = [
        "Charlie",
        "Dave",
        "2",
        "0",
        "1",
        "0",
        "Lurrus",
        "Bazaar",
        "UB Lurrus Control",
        "Dredge",
        "",
        "",
        "",
        "3",
        "Charlie",
        "1",
        "1",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Dave",
        "2",
        "0",
    ]
    row4 = [
        "Dave",
        "Charlie",
        "0",
        "2",
        "0",
        "1",
        "Bazaar",
        "Lurrus",
        "Dredge",
        "UB Lurrus Control",
        "",
        "",
        "",
        "4",
        "Dave",
        "0",
        "2",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Charlie",
        "0",
        "2",
    ]

    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(row1)
    writer.writerow(row2)
    writer.writerow(row3)
    writer.writerow(row4)

    rounds, players, warnings = parse_vmc_sheet_csv(output.getvalue())
    assert len(warnings) == 0
    assert len(players) == 4
    assert players[0] == {"Player": "Alice", "Archetype": "Jewel Shops"}
    assert players[1] == {"Player": "Bob", "Archetype": "Initiative"}
    assert players[2] == {"Player": "Charlie", "Archetype": "UB Lurrus Control"}
    assert players[3] == {"Player": "Dave", "Archetype": "Dredge"}

    assert len(rounds) == 2
    # Round 1 is Swiss (from col 27)
    r1 = rounds[0]
    assert r1["RoundName"] == "Round 1"
    assert r1["RoundType"] == "Swiss"
    assert len(r1["Matches"]) == 2
    alice_match = next(m for m in r1["Matches"] if m["Player1"] == "Alice")
    assert alice_match["Player2"] == "Bob"
    assert alice_match["Result"] == "2-0-0"

    # Round 2 is Top8 (from col 18 - Finals)
    r2 = rounds[1]
    assert r2["RoundName"] == "Round 2"
    assert r2["RoundType"] == "Top8"
    assert len(r2["Matches"]) == 1
    assert r2["Matches"][0]["Player1"] == "Alice"
    assert r2["Matches"][0]["Player2"] == "Bob"
    assert r2["Matches"][0]["Result"] == "2-1-0"


def test_parse_vmc_sheet_csv_conflict_warning():
    # Alice reports 2-0 against Bob, but Bob reports 2-0 against Alice
    header = [
        "Name1",
        "Name2",
        "Win ",
        "loss",
        "Winner1",
        "Winner2",
        "Archetype1",
        "Archetype2",
        "Subarchetype1",
        "Subarchetype2",
        "Deck1",
        "Deck2",
        "",
        "Rank",
        "Name",
        "Record",
        "",
        "Bye",
        "Round",
        "Games",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Round",
        "Games",
        "",
    ]
    row1 = [
        "Alice",
        "Bob",
        "2",
        "0",
        "1",
        "0",
        "Shops",
        "Aggro",
        "Jewel Shops",
        "Initiative",
        "",
        "",
        "",
        "1",
        "Alice",
        "1",
        "0",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Bob",
        "2",
        "0",
    ]
    row2 = [
        "Bob",
        "Alice",
        "2",
        "0",
        "1",
        "0",
        "Aggro",
        "Shops",
        "Initiative",
        "Jewel Shops",
        "",
        "",
        "",
        "2",
        "Bob",
        "1",
        "0",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Alice",
        "2",
        "0",
    ]
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(row1)
    writer.writerow(row2)

    rounds, players, warnings = parse_vmc_sheet_csv(output.getvalue())
    assert len(warnings) == 1
    assert "score disagreement" in warnings[0]
    assert len(rounds) == 1
    assert len(rounds[0]["Matches"]) == 0  # Conflicting match dropped


def test_parse_vmc_sheet_csv_pairing_mismatch():
    # Alice reports playing Bob, but Bob reports playing Charlie
    header = [
        "Name1",
        "Name2",
        "Win ",
        "loss",
        "Winner1",
        "Winner2",
        "Archetype1",
        "Archetype2",
        "Subarchetype1",
        "Subarchetype2",
        "Deck1",
        "Deck2",
        "",
        "Rank",
        "Name",
        "Record",
        "",
        "Bye",
        "Round",
        "Games",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Round",
        "Games",
        "",
    ]
    row1 = [
        "Alice",
        "Bob",
        "2",
        "0",
        "1",
        "0",
        "Shops",
        "Aggro",
        "Jewel Shops",
        "Initiative",
        "",
        "",
        "",
        "1",
        "Alice",
        "1",
        "0",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Bob",
        "2",
        "0",
    ]
    row2 = [
        "Bob",
        "Charlie",
        "2",
        "0",
        "1",
        "0",
        "Aggro",
        "Lurrus",
        "Initiative",
        "UB Lurrus Control",
        "",
        "",
        "",
        "2",
        "Bob",
        "1",
        "0",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "Charlie",
        "2",
        "0",
    ]
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(row1)
    writer.writerow(row2)

    rounds, players, warnings = parse_vmc_sheet_csv(output.getvalue())
    assert len(warnings) == 1
    assert "pairing mismatch" in warnings[0]
    assert len(rounds) == 1
    assert len(rounds[0]["Matches"]) == 0


def test_match_challenges_to_merge_sheet():
    mtgo_events = {
        "slug-single": {
            "date": "2026-09-19",
            "datetime": "2026-09-19T17:00:00Z",
            "name": "Vintage Challenge 32",
            "uri": "https://www.mtgo.com/slug-single",
            "sheet_id": None,
        },
        "slug-multi-early": {
            "date": "2026-09-20",
            "datetime": "2026-09-20T07:00:00Z",
            "name": "Vintage Challenge 16",
            "uri": "https://www.mtgo.com/slug-multi-early",
            "sheet_id": None,
        },
        "slug-multi-late": {
            "date": "2026-09-20",
            "datetime": "2026-09-20T20:30:00Z",
            "name": "Vintage Challenge 32",
            "uri": "https://www.mtgo.com/slug-multi-late",
            "sheet_id": None,
        },
    }

    tabs_by_date = {
        "2026-09-19": [
            {
                "gid": "111",
                "name": "09/19/2026",
                "idx": 0,
                "sheet_id": "sheet_19",
                "slug": None,
            }
        ],
        "2026-09-20": [
            {
                "gid": "222",
                "name": "09/20/2026",
                "idx": 0,
                "sheet_id": "sheet_20_early",
                "slug": "slug-multi-early",
            },
            {
                "gid": "333",
                "name": "09/20/2026 (1)",
                "idx": 1,
                "sheet_id": "sheet_20_late",
                "slug": "slug-multi-late",
            },
        ],
    }

    match_challenges_to_merge_sheet(mtgo_events, tabs_by_date, year=2026)
    assert mtgo_events["slug-single"]["sheet_id"] == "sheet_19"
    assert mtgo_events["slug-multi-early"]["sheet_id"] == "sheet_20_early"
    assert mtgo_events["slug-multi-late"]["sheet_id"] == "sheet_20_late"


def test_process_challenge_missing_name_skipped(caplog):
    meta = {
        "sheet_id": "test_sheet_id",
        "date": "2026-09-20",
    }
    result = process_challenge("test-slug", meta)
    assert result is False
    assert "missing name in metadata" in caplog.text


def test_update_yaml_for_year(tmp_path):
    mock_events = {
        "slug-1": {
            "date": "2026-01-01",
            "datetime": "2026-01-01T15:00:00Z",
            "name": "Vintage Challenge 32",
            "uri": "https://www.mtgo.com/decklist/slug-1",
            "sheet_id": None,
            "tab": "Standings",
        }
    }
    mock_tabs = {
        "2026-01-01": [
            {
                "gid": "123",
                "name": "01/01/2026",
                "idx": 0,
                "sheet_id": "sheet_found",
                "slug": "slug-1",
            }
        ]
    }

    with (
        patch("modometa_community_data.vmc.REPO_ROOT", tmp_path),
        patch(
            "modometa_community_data.vmc.fetch_mtgo_calendar_challenges",
            return_value=mock_events,
        ),
        patch(
            "modometa_community_data.vmc.fetch_vmc_merge_sheet_mappings",
            return_value=mock_tabs,
        ),
        patch("modometa_community_data.vmc.enrich_multi_event_tab_urls"),
    ):
        out_path = update_yaml_for_year(2026)
        assert out_path.exists()
        loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert len(loaded) == 1
        assert loaded["slug-1"]["sheet_id"] == "sheet_found"


def test_main_cli_update_yaml(monkeypatch):
    with patch("modometa_community_data.vmc.update_yaml_for_year") as mock_update:
        monkeypatch.setattr("sys.argv", ["pull-vmc", "--update-yaml", "2026"])
        main()
        mock_update.assert_called_once_with(2026)


def test_main_cli_start_date_filter(monkeypatch):
    challenges = {
        "slug-old": {
            "date": "2026-09-01",
            "name": "Old Challenge",
            "uri": "https://www.mtgo.com/slug-old",
            "sheet_id": "sheet_old",
            "tab": "Standings",
        },
        "slug-new": {
            "date": "2026-09-15",
            "name": "New Challenge",
            "uri": "https://www.mtgo.com/slug-new",
            "sheet_id": "sheet_new",
            "tab": "Standings",
        },
    }

    processed_slugs = []

    def mock_process(slug, meta, dry_run=False, force=False):
        processed_slugs.append(slug)
        return True

    monkeypatch.setattr(
        "sys.argv",
        ["pull-vmc", "--start-date", "2026-09-10", "--force"],
    )

    with (
        patch("pathlib.Path.glob", return_value=[MagicMock()]),
        patch("builtins.open", MagicMock()),
        patch("yaml.safe_load", return_value=challenges),
        patch(
            "modometa_community_data.vmc.process_challenge", side_effect=mock_process
        ),
    ):
        main()

    assert processed_slugs == ["slug-new"]


def test_parse_vmc_sheet_csv_col24_swiss():
    import csv
    import io

    # Construct a header with col 18, 21, 24, 27
    header = [""] * 30
    header[14] = "Name"
    header[18] = "Round"
    header[19] = "Games"
    header[21] = "Round"
    header[22] = "Games"
    header[24] = "Round"
    header[25] = "Games"
    header[27] = "Round"
    header[28] = "Games"

    # In Col 24, let's put 10 players (> 8 reports, meaning 5 matches) -> should be Swiss
    # In Col 21, let's put 8 players (4 matches) -> should be Top 8 QF
    # In Col 18, let's put 4 players (2 matches) -> should be Top 8 SF
    rows = []
    for i in range(1, 11):
        r = [""] * 30
        p_name = f"Player{i}"
        r[14] = p_name
        # col 24 pairing (i pairs with 11-i)
        opp24 = f"Player{11 - i}"
        r[24] = opp24
        r[25] = "2" if i % 2 == 1 else "0"
        r[26] = "0" if i % 2 == 1 else "2"

        if i <= 8:
            opp21 = f"Player{9 - i}"
            r[21] = opp21
            r[22] = "2" if i % 2 == 1 else "0"
            r[23] = "0" if i % 2 == 1 else "2"

        if i <= 4:
            opp18 = f"Player{5 - i}"
            r[18] = opp18
            r[19] = "2" if i % 2 == 1 else "0"
            r[20] = "0" if i % 2 == 1 else "2"

        rows.append(r)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow(r)

    rounds, players, warnings = parse_vmc_sheet_csv(out.getvalue())
    assert len(rounds) == 3
    # Round 1 is Col 24 (Swiss, 5 matches)
    assert rounds[0]["RoundName"] == "Round 1"
    assert rounds[0]["RoundType"] == "Swiss"
    assert len(rounds[0]["Matches"]) == 5

    # Round 2 is Col 21 (Top 8, 4 matches)
    assert rounds[1]["RoundName"] == "Round 2"
    assert rounds[1]["RoundType"] == "Top8"
    assert len(rounds[1]["Matches"]) == 4

    # Round 3 is Col 18 (Top 8, 2 matches)
    assert rounds[2]["RoundName"] == "Round 3"
    assert rounds[2]["RoundType"] == "Top8"
    assert len(rounds[2]["Matches"]) == 2
