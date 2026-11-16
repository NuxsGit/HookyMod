"""Écritures atomiques face aux collisions Windows et aux vraies erreurs."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hookymod.util import write_atomic


def windows_error(code):
    error = PermissionError("verrouillage temporaire simulé")
    error.winerror = code
    return error


class AtomicWriteTests(unittest.TestCase):
    def test_retries_transient_windows_replace_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "fichier"
            target.write_bytes(b"ancien")
            real_replace = os.replace
            errors = [windows_error(5), windows_error(32), windows_error(33)]
            def flaky(source, destination):
                if errors:
                    raise errors.pop(0)
                return real_replace(source, destination)
            with mock.patch("hookymod.util.os.replace", side_effect=flaky) as replace, \
                 mock.patch("hookymod.util.time.sleep") as sleep:
                write_atomic(target, b"nouveau")
            self.assertEqual(replace.call_count, 4)
            self.assertEqual(sleep.call_count, 3)
            self.assertEqual(target.read_bytes(), b"nouveau")
            self.assertEqual([path.name for path in target.parent.iterdir()], ["fichier"])

    def test_permanent_errors_leave_original_and_remove_temporary_files(self):
        for error in (PermissionError("pas de droits"), windows_error(5)):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "fichier"
                target.write_bytes(b"ancien")
                with mock.patch("hookymod.util.os.replace", side_effect=error) as replace, \
                     mock.patch("hookymod.util.time.sleep"):
                    with self.assertRaises(PermissionError):
                        write_atomic(target, b"nouveau")
                if getattr(error, "winerror", None) is None:
                    self.assertEqual(replace.call_count, 1)
                else:
                    self.assertGreater(replace.call_count, 1)
                    self.assertLessEqual(replace.call_count, 10)
                self.assertEqual(target.read_bytes(), b"ancien")
                self.assertEqual([path.name for path in target.parent.iterdir()], ["fichier"])

    def test_same_destination_replacements_are_serialised_in_process(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "fichier"
            real_replace = os.replace
            guard = threading.Lock()
            active = 0
            maximum = 0
            def monitored(source, destination):
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    time.sleep(0.005)
                    return real_replace(source, destination)
                finally:
                    with guard:
                        active -= 1
            with mock.patch("hookymod.util.os.replace", side_effect=monitored):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda n: write_atomic(target, str(n).encode()), range(16)))
            self.assertEqual(maximum, 1)
            self.assertIn(target.read_bytes(), [str(n).encode() for n in range(16)])
            self.assertEqual([path.name for path in target.parent.iterdir()], ["fichier"])

    def test_different_destinations_are_not_globally_serialised(self):
        with tempfile.TemporaryDirectory() as folder:
            barrier = threading.Barrier(2, timeout=5)
            real_replace = os.replace
            def simultaneous(source, destination):
                barrier.wait()
                return real_replace(source, destination)
            with mock.patch("hookymod.util.os.replace", side_effect=simultaneous):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(write_atomic, Path(folder) / name, b"ok")
                               for name in ("a", "b")]
                    for future in futures:
                        future.result(timeout=6)
            self.assertEqual((Path(folder) / "a").read_bytes(), b"ok")
            self.assertEqual((Path(folder) / "b").read_bytes(), b"ok")


if __name__ == "__main__":
    unittest.main()
