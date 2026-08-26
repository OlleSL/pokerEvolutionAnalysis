from __future__ import annotations

STUDY_YEARS = frozenset(range(2011, 2026))
DATASETS = ("NL50", "NL200", "NL5K")
POSITIONS_EARLY = ("LJ", "HJ", "CO", "BTN")


def classify_stake(stake_raw: str) -> tuple[bool, bool, float | None, float | None]:
    """Return is_usd, is_cap, sb, bb from stakes string like '$0.25/$0.50 USD'."""
    s = stake_raw.lower()
    is_usd = "usd" in s and "eur" not in s and "gbp" not in s
    is_cap = "cap" in s
    sb = bb = None
    import re

    m = re.search(r"\$([\d.]+)/\$([\d.]+)", stake_raw)
    if m:
        sb, bb = float(m.group(1)), float(m.group(2))
    return is_usd, is_cap, sb, bb


def is_included(is_usd: bool, is_cap: bool, is_ante: bool, year: int, max_players: int | None) -> bool:
    return is_usd and not is_cap and not is_ante and year in STUDY_YEARS and max_players == 6
