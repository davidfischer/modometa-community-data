from unittest.mock import MagicMock

from modometa_community_data.mtgo import fetch_mtgo_calendar_challenges


def test_fetch_mtgo_calendar_challenges_vintage():
    html_content = """
    <html>
      <body>
        <ul class="decklists">
          <li class="decklists-item">
            <h3>Vintage Challenge 32</h3>
            <a href="/decklist/vintage-challenge-32-2025-01-0412726588">Decklist</a>
            <time datetime="2025-01-04T18:00:00Z">Jan 4, 2025</time>
          </li>
          <li class="decklists-item">
            <h3>Legacy Challenge 32</h3>
            <a href="/decklist/legacy-challenge-32-2025-01-0412726589">Decklist</a>
            <time datetime="2025-01-04T18:00:00Z">Jan 4, 2025</time>
          </li>
        </ul>
      </body>
    </html>
    """

    mock_resp_jan = MagicMock()
    mock_resp_jan.status_code = 200
    mock_resp_jan.url = "https://www.mtgo.com/decklists/2025/01"
    mock_resp_jan.text = html_content

    mock_resp_other = MagicMock()
    mock_resp_other.status_code = 200
    mock_resp_other.url = "https://www.mtgo.com/decklists/2025/02"
    mock_resp_other.text = "<html><body></body></html>"

    mock_session = MagicMock()

    def get_side_effect(url, *args, **kwargs):
        if "2025/01" in url:
            return mock_resp_jan
        return mock_resp_other

    mock_session.get.side_effect = get_side_effect

    events = fetch_mtgo_calendar_challenges(
        2025, session=mock_session, format_name="vintage"
    )

    assert "vintage-challenge-32-2025-01-0412726588" in events
    assert "legacy-challenge-32-2025-01-0412726589" not in events
    event = events["vintage-challenge-32-2025-01-0412726588"]
    assert event["name"] == "Vintage Challenge 32"
    assert event["date"] == "2025-01-04"
    assert event["tab"] == "Standings"
