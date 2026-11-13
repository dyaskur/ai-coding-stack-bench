"""Parse JUnit XML acceptance results into quality metrics."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class JUnitSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tests: int
    failures: int
    errors: int
    skipped: int
    failed_cases: list[str]

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped

    @property
    def failed(self) -> int:
        return self.failures + self.errors


def parse_junit(path: Path) -> JUnitSummary:
    """Count results from ``<testcase>`` elements (robust to missing suite totals)."""
    root = ET.parse(path).getroot()
    tests = failures = errors = skipped = 0
    failed_cases: list[str] = []
    for case in root.iter("testcase"):
        tests += 1
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        if case.find("failure") is not None:
            failures += 1
            failed_cases.append(name)
        elif case.find("error") is not None:
            errors += 1
            failed_cases.append(name)
        elif case.find("skipped") is not None:
            skipped += 1
    return JUnitSummary(
        tests=tests, failures=failures, errors=errors, skipped=skipped, failed_cases=failed_cases
    )
