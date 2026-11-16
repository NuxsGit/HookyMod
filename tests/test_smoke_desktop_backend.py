"""The packaged-backend smoke must handle only safely retryable busy imports."""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import Mock, patch

import pytest

from tools.smoke_desktop_backend import IMPORT_ROUTES, post_json


ORIGIN = "http://127.0.0.1:12345"


def http_error(route: str, status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ORIGIN + route, status, "test", {"Retry-After": "2"}, io.BytesIO())


@pytest.mark.parametrize("route", sorted(IMPORT_ROUTES))
def test_busy_import_retries_identical_unprocessed_request(route: str) -> None:
    opener = Mock()
    opener.open.side_effect = [http_error(route, 429), io.BytesIO(b'{"ok": true}')]
    with patch("tools.smoke_desktop_backend.time.sleep") as sleep:
        assert post_json(opener, ORIGIN, route, {"data": "x"}) == {"ok": True}
    sleep.assert_called_once_with(2)
    assert opener.open.call_count == 2
    first = opener.open.call_args_list[0].args[0]
    second = opener.open.call_args_list[1].args[0]
    assert first is second and first.get_method() == "POST"
    assert first.full_url == ORIGIN + route
    assert first.get_header("Origin") == ORIGIN
    assert json.loads(first.data) == {"data": "x"}


@pytest.mark.parametrize(("route", "status"), [
    ("/api/model/import", 400),
    ("/api/texture/export", 429),
])
def test_other_errors_are_not_retried(route: str, status: int) -> None:
    opener = Mock()
    error = http_error(route, status)
    opener.open.side_effect = error
    with patch("tools.smoke_desktop_backend.time.sleep") as sleep:
        with pytest.raises(urllib.error.HTTPError) as exc:
            post_json(opener, ORIGIN, route, {})
    assert exc.value is error
    assert opener.open.call_count == 1
    sleep.assert_not_called()
    error.close()


def test_busy_import_retry_is_bounded() -> None:
    route = "/api/model/import"
    errors = [http_error(route, 429) for _ in range(3)]
    opener = Mock()
    opener.open.side_effect = errors
    with patch("tools.smoke_desktop_backend.time.sleep") as sleep:
        with pytest.raises(urllib.error.HTTPError) as exc:
            post_json(opener, ORIGIN, route, {})
    assert exc.value is errors[-1]
    assert opener.open.call_count == 3
    assert sleep.call_count == 2
    errors[-1].close()
