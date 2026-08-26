from app.services.monitor import check_url


def test_check_google():

    result = check_url(
        "https://google.com"
    )

    assert "is_up" in result
    assert "status_code" in result
    assert "response_time_ms" in result
    assert "error" in result


def test_invalid_url():

    result = check_url(
        "http://this-domain-should-not-exist-123456789.com"
    )

    assert result["is_up"] is False