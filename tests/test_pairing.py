from __future__ import annotations

import re

from homeone.homekit.bridge import resolve_pincode


def test_pincode_is_stable_and_private(tmp_path):
    first = resolve_pincode(tmp_path, None)
    second = resolve_pincode(tmp_path, None)
    assert first == second
    assert re.match(r"^\d{3}-\d{2}-\d{3}$", first)
    assert (tmp_path / "pairing.json").stat().st_mode & 0o777 == 0o600


def test_configured_pincode_wins(tmp_path):
    assert resolve_pincode(tmp_path, "031-45-678") == "031-45-678"
