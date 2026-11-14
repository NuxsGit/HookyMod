#!/usr/bin/env python3
"""Expose pytest JUnit failures as GitHub check annotations, including on Windows.

The original pytest step determines the job result; this reporting step must not
hide or replace it. No reports are created when pytest never started.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def report(path: Path) -> None:
    if not path.is_file():
        print(f"No pytest report at {path}.")
        return
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        print(f"::warning::{_escape(f'Cannot read pytest report: {exc}')}")
        return
    for case in root.iter("testcase"):
        for outcome in case:
            if outcome.tag not in ("failure", "error"):
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            details = outcome.text or outcome.get("message", "No details supplied.")
            # GitHub limits annotation size; keep at most 12,000 characters.
            if len(details) > 12000:
                details = details[:12000] + "\n[truncated]"
            print(f"::error::{_escape(name + chr(10) + details)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: report_pytest_failures.py REPORT.xml")
    report(Path(sys.argv[1]))
