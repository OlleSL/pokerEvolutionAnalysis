"""Turn the Pluribus hand histories into one row per decision.

Source: the 10,000 hands Pluribus played against five pros (Brown &
Sandholm, Science 2019), in PHH format from uoftcprg/phh-dataset. Fetch only
that folder with:

  git clone --depth 1 --filter=blob:none --sparse \
      https://github.com/uoftcprg/phh-dataset.git data/external/phh-dataset
  git -C data/external/phh-dataset sparse-checkout set data/pluribus

Each .phh file is one hand (TOML). Seats p1..p6 are SB, BB, LJ, HJ, CO, BTN.
Actions: "pN f" fold, "pN cc" check/call, "pN cbr X" bet/raise *to* X,
"d db CARDS" deals the next street. Every hand starts at 100 bb (10,000
chips, blinds 50/100).

Every player decision is replayed and described with **public information
only** (no hole cards), so the same features can later be computed from the
PokerStars hands, where most hole cards are hidden.

Output: data/external/pluribus_decisions.parquet

Usage:
  python scripts/build_pluribus_decisions.py
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "external" / "phh-dataset" / "data" / "pluribus"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "external" / "pluribus_decisions.parquet"

POSITIONS = ("SB", "BB", "LJ", "HJ", "CO", "BTN")
STREETS = ("preflop", "flop", "turn", "river")
BB = 100


def replay_hand(hand: dict, session: str, path: Path) -> list[dict]:
    """Replay one hand and return a row per player decision."""
    players: list[str] = hand["players"]
    n = len(players)
    stack = list(hand["starting_stacks"])
    committed = [0] * n  # chips put in on the current street
    for i, blind in enumerate(hand["blinds_or_straddles"]):
        committed[i] = blind
        stack[i] -= blind
    pot = sum(committed)
    current_bet = max(committed)
    raises = 1 if current_bet > 0 else 0  # the big blind counts as the first "raise" preflop
    folded = [False] * n
    street = 0
    preflop_aggressor: int | None = None
    actions_this_street = 0
    rows: list[dict] = []

    for action in hand["actions"]:
        parts = action.split()
        if parts[0] == "d":
            if parts[1] == "db":  # next street
                street += 1
                committed = [0] * n
                current_bet = 0
                raises = 0
                actions_this_street = 0
            continue
        seat = int(parts[0][1:]) - 1
        kind = parts[1]
        if kind == "sm":  # show/muck at showdown, not a decision
            continue

        to_call = min(current_bet - committed[seat], stack[seat])
        active = [i for i in range(n) if not folded[i]]
        row = {
            "session": session,
            "hand": f"{session}/{path.stem}",
            "player": players[seat],
            "is_pluribus": players[seat] == "Pluribus",
            "street": STREETS[street],
            "position": POSITIONS[seat],
            "players_left": len(active),
            "raises_before": raises,
            "actions_before": actions_this_street,
            "facing_bet": to_call > 0,
            "pot_bb": pot / BB,
            "to_call_bb": to_call / BB,
            "pot_odds": to_call / (pot + to_call) if to_call > 0 else 0.0,
            "spr": stack[seat] / pot if pot else 0.0,
            "in_position": street > 0 and seat == max(active),  # acts last after the flop
            "was_preflop_aggressor": street > 0 and preflop_aggressor == seat,
        }

        if kind == "f":
            decision, size = "fold", 0.0
            folded[seat] = True
        elif kind == "cc":
            decision = "call" if to_call > 0 else "check"
            size = to_call / pot if to_call > 0 else 0.0
            stack[seat] -= to_call
            committed[seat] += to_call
            pot += to_call
        elif kind == "cbr":
            target = int(parts[2])
            added = target - committed[seat]
            decision = "raise" if current_bet > 0 else "bet"
            size = added / pot  # chips added, as a share of the pot before acting
            stack[seat] -= added
            committed[seat] = target
            pot += added
            current_bet = target
            raises += 1
            if street == 0:
                preflop_aggressor = seat
        else:
            raise ValueError(f"Unknown action {action!r} in {path}")

        row["decision"] = decision
        row["size_pot"] = size
        row["all_in"] = kind in ("cc", "cbr") and stack[seat] == 0
        rows.append(row)
        actions_this_street += 1

    return rows


def build_decisions(source: Path = DEFAULT_SOURCE) -> pd.DataFrame:
    rows: list[dict] = []
    for session_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        for path in sorted(session_dir.glob("*.phh"), key=lambda p: int(p.stem)):
            hand = tomllib.loads(path.read_text(encoding="utf-8"))
            rows.extend(replay_hand(hand, session_dir.name, path))
    return pd.DataFrame(rows)


def main() -> None:
    if not DEFAULT_SOURCE.is_dir():
        print(f"No Pluribus hands at {DEFAULT_SOURCE}. See the module docstring to fetch them.")
        sys.exit(1)
    df = build_decisions()
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(DEFAULT_OUTPUT, index=False)
    print(f"Wrote {DEFAULT_OUTPUT} ({len(df):,} decisions, "
          f"{df.hand.nunique():,} hands, {df.session.nunique()} sessions)")
    print(df.groupby("is_pluribus").decision.value_counts(normalize=True).unstack().round(3))


if __name__ == "__main__":
    main()
