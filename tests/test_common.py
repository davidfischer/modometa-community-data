import datetime
from unittest.mock import MagicMock
from unittest.mock import patch

from modometa_community_data.common import deduce_swiss_rounds
from modometa_community_data.common import dump_challenges_yaml
from modometa_community_data.common import fetch_sheet_csv
from modometa_community_data.common import parse_date
from modometa_community_data.common import parse_match_result


def test_deduce_swiss_rounds_bounds():
    assert deduce_swiss_rounds(None) == 7
    assert deduce_swiss_rounds(0) == 3
    assert deduce_swiss_rounds(8) == 3
    assert deduce_swiss_rounds(9) == 4
    assert deduce_swiss_rounds(16) == 4
    assert deduce_swiss_rounds(17) == 5
    assert deduce_swiss_rounds(32) == 5
    assert deduce_swiss_rounds(33) == 6
    assert deduce_swiss_rounds(64) == 6
    assert deduce_swiss_rounds(65) == 7
    assert deduce_swiss_rounds(128) == 7
    assert deduce_swiss_rounds(129) == 8
    assert deduce_swiss_rounds(226) == 8
    assert deduce_swiss_rounds(227) == 9
    assert deduce_swiss_rounds(500) == 9


def test_parse_match_result():
    assert parse_match_result("2-0") == (2, 0, 0)
    assert parse_match_result("2-1") == (2, 1, 0)
    assert parse_match_result("1-1-1") == (1, 1, 1)
    assert parse_match_result("0-2") == (0, 2, 0)
    assert parse_match_result("bye") is None
    assert parse_match_result("drop") is None
    assert parse_match_result("dq") is None
    assert parse_match_result("split") is None
    assert parse_match_result("") is None
    assert parse_match_result("invalid") is None


def test_parse_date():
    assert parse_date("2026-09-13") == datetime.date(2026, 9, 13)
    assert parse_date("2026-09-13T18:00:00Z") == datetime.date(2026, 9, 13)
    assert parse_date(datetime.date(2026, 9, 13)) == datetime.date(2026, 9, 13)
    assert parse_date(datetime.datetime(2026, 9, 13, 10, 0)) == datetime.date(
        2026, 9, 13
    )
    assert parse_date("not-a-date") is None
    assert parse_date("") is None
    assert parse_date(None) is None


def test_dump_challenges_yaml():
    challenges = {
        "slug-1": {
            "date": "2026-09-13",
            "name": "Challenge 32",
            "uri": "https://www.mtgo.com/slug-1",
            "sheet_id": "sid_123",
            "tab": "Custom Tab",
        },
        "slug-2": {
            "date": "2026-09-14",
            "name": "Challenge 64",
            "uri": "https://www.mtgo.com/slug-2",
            "sheet_id": None,
        },
    }
    header = ["# Test Header", ""]
    yaml_str = dump_challenges_yaml(
        challenges, header_lines=header, default_tab="Default Tab"
    )
    assert "# Test Header" in yaml_str
    assert "slug-1:" in yaml_str
    assert "sheet_id: 'sid_123'" in yaml_str
    assert "tab: 'Custom Tab'" in yaml_str
    assert "slug-2:" in yaml_str
    assert "sheet_id: null" in yaml_str
    assert "tab: 'Default Tab'" in yaml_str


def test_fetch_sheet_csv():
    mock_resp = MagicMock()
    mock_resp.text = "col1,col2\nval1,val2"
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp) as mock_get:
        content = fetch_sheet_csv("sheet123", tab="Standings")
        assert content == "col1,col2\nval1,val2"
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert "sheet123" in args[0]
        assert "Standings" in args[0]
