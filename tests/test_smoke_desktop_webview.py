"""Only read-only WebDriver queries may recover from a transient disconnect."""
from __future__ import annotations

from http.client import RemoteDisconnected
from unittest.mock import Mock, patch

import pytest

from tools.smoke_desktop_webview import Driver, WebDriverError


def driver_with_script(*results):
    driver = object.__new__(Driver)
    driver.script = Mock(side_effect=results)
    return driver


def test_read_only_query_recovers_without_losing_false_values():
    driver = driver_with_script(RemoteDisconnected("disconnected"), False)
    with patch("tools.smoke_desktop_webview.time.sleep") as sleep:
        assert driver.query("return false", "argument") is False
    assert driver.script.call_count == 2
    assert all(call.args == ("return false", "argument") for call in driver.script.call_args_list)
    sleep.assert_called_once_with(0.5)


def test_read_only_query_raises_on_persistent_disconnect():
    error = RemoteDisconnected("still disconnected")
    driver = driver_with_script(error, error, error)
    with patch("tools.smoke_desktop_webview.time.sleep") as sleep:
        with pytest.raises(RemoteDisconnected) as exc:
            driver.query("return true")
    assert exc.value is error
    assert driver.script.call_count == 3
    assert sleep.call_count == 2


def test_read_only_query_does_not_retry_webdriver_errors():
    error = WebDriverError("invalid script")
    driver = driver_with_script(error)
    with patch("tools.smoke_desktop_webview.time.sleep") as sleep:
        with pytest.raises(WebDriverError) as exc:
            driver.query("invalid script")
    assert exc.value is error
    assert driver.script.call_count == 1
    sleep.assert_not_called()


def test_click_is_not_replayed_after_a_disconnect():
    driver = object.__new__(Driver)
    driver.session = "session"
    driver.call = Mock(side_effect=[
        {"element-6066-11e4-a52e-4f735466cecf": "element"},
        RemoteDisconnected("disconnected after click"),
    ])
    with pytest.raises(RemoteDisconnected):
        driver.click("#button")
    assert driver.call.call_count == 2
