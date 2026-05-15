"""Authentication helpers shared by the web app."""

from __future__ import annotations

import re


def normalize_owner_id(username: str) -> str:
    """Return a stable filesystem-safe owner id for a display username."""
    username = (username or "").strip()
    owner = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", username)
    owner = re.sub(r"_+", "_", owner).strip("_")
    return owner or "default_user"
