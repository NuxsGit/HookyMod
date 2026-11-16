"""Windows lifetime-pipe polling must never leave a pending stdin read."""
from __future__ import annotations

import ctypes
import sys
import types
from ctypes import wintypes
from unittest.mock import Mock, patch

import pytest

from hookycreator import desktop


@pytest.mark.parametrize("end", ["closed", "data"])
def test_windows_pipe_is_polled_without_a_blocking_read(end: str) -> None:
    stream = Mock()
    stream.fileno.return_value = 4
    get_file_type = Mock(return_value=3)  # FILE_TYPE_PIPE
    states = iter([(True, 0), (end == "data", 1 if end == "data" else 0)])

    def peek(handle, buffer, size, read, available, remaining):
        success, count = next(states)
        ctypes.cast(available, ctypes.POINTER(wintypes.DWORD))[0] = count
        return success

    peek_pipe = Mock(side_effect=peek)
    kernel = types.SimpleNamespace(GetFileType=get_file_type, PeekNamedPipe=peek_pipe)
    msvcrt = types.SimpleNamespace(get_osfhandle=Mock(return_value=123))
    with (patch.object(sys, "platform", "win32"), patch.object(sys, "stdin", stream),
          patch.dict(sys.modules, {"msvcrt": msvcrt}),
          patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
          patch.object(desktop.time, "sleep") as sleep):
        desktop._wait_for_parent_close()
    stream.buffer.read.assert_not_called()
    msvcrt.get_osfhandle.assert_called_once_with(4)
    get_file_type.assert_called_once_with(123)
    assert peek_pipe.call_count == 2
    sleep.assert_called_once_with(0.1)


def test_windows_non_pipe_preserves_read_behavior() -> None:
    stream = Mock()
    stream.fileno.return_value = 4
    kernel = types.SimpleNamespace(GetFileType=Mock(return_value=2), PeekNamedPipe=Mock())
    msvcrt = types.SimpleNamespace(get_osfhandle=Mock(return_value=123))
    with (patch.object(sys, "platform", "win32"), patch.object(sys, "stdin", stream),
          patch.dict(sys.modules, {"msvcrt": msvcrt}),
          patch.object(ctypes, "WinDLL", return_value=kernel, create=True)):
        desktop._wait_for_parent_close()
    stream.buffer.read.assert_called_once_with(1)
    kernel.PeekNamedPipe.assert_not_called()
