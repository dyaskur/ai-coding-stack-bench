from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

SLUG_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
_SLUG_RE = re.compile(SLUG_PATTERN)


def is_slug(value: str) -> bool:
    return bool(_SLUG_RE.match(value))


class StrictModel(BaseModel):
    """Base for user-authored config: unknown keys are errors, not silently ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
