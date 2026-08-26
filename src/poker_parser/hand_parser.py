from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from poker_parser.constants import POSITIONS_EARLY, classify_stake, is_included

HAND_START = re.compile(r"^PokerStars (?:Hand|Game) #", re.MULTILINE)
HEADER = re.compile(
    r"PokerStars (?:Hand|Game) #(\d+):\s+Hold'em No Limit \(([^)]+)\)\s+-\s+"
    r"(\d{4}/\d{2}/\d{2})\s+(\d{1,2}:\d{2}:\d{2})\s+(\w+)"
)
TABLE = re.compile(r"^Table '([^']+)' (\d+)-max Seat #(\d+) is the button", re.MULTILINE)
SEAT = re.compile(r"^Seat (\d+): (.+?) \(\$([\d.]+) in chips\)", re.MULTILINE)
STREET_FLOP = re.compile(r"^\*\*\* FLOP \*\*\* \[(.+?)\]")
STREET_TURN = re.compile(r"^\*\*\* TURN \*\*\* \[(.+?) \[(.+?)\]")
STREET_RIVER = re.compile(r"^\*\*\* RIVER \*\*\* \[(.+?) \[(.+?)\]")
SUMMARY_POT = re.compile(r"^Total pot \$([\d.]+)(?: \| Rake \$([\d.]+))?")
SHOWED = re.compile(r"showed \[(.+?)\]")
ANTE_LINE = re.compile(r"^[^:]+:\s+posts (?:the )?ante \$([\d.]+)", re.MULTILINE)
COLLECTED_INLINE = re.compile(r"^(.+?) collected \$([\d.]+) from (?:side |main )?pot")
SUMMARY_WON = re.compile(r"^Seat \d+: (.+?) showed .+ and won \(\$([\d.]+)\)")
SUMMARY_WON_SIMPLE = re.compile(r"^Seat \d+: (.+?) won \(\$([\d.]+)\)")
SUMMARY_COLLECTED = re.compile(r"^Seat \d+: (.+?) collected \(\$([\d.]+)\)")
RETURNED = re.compile(r"^Uncalled bet \(\$([\d.]+)\) returned to (.+)")
ACTION = re.compile(
    r"^([^:]+): (posts small blind|posts big blind|posts the ante|posts ante|folds|checks|"
    r"calls|raises|bets|shows|mucks hand)(?: \$([\d.]+))?(?: to \$([\d.]+))?"
)


class ParseError(Exception):
    pass


class SkipHand(Exception):
    """Hand intentionally skipped (non-USD, etc.). Not a parse failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class ParsedHand:
    hand: dict
    players: list[dict]
    actions: list[dict]


def split_hands(text: str) -> list[str]:
    starts = [m.start() for m in HAND_START.finditer(text)]
    if not starts:
        return []
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        blocks.append(text[start:end])
    return blocks


def _next_seat_cw(seat: int, max_players: int = 6) -> int:
    return seat + 1 if seat < max_players else 1


def _order_seats_sb_to_btn(dealt_seats: list[int], button_seat: int, max_players: int = 6) -> list[int]:
    if not dealt_seats:
        return []
    seats_set = set(dealt_seats)
    sb = _next_seat_cw(button_seat, max_players)
    while sb not in seats_set:
        sb = _next_seat_cw(sb, max_players)
        if sb == _next_seat_cw(button_seat, max_players):
            break
    ordered: list[int] = []
    current = sb
    for _ in range(len(dealt_seats)):
        while current not in seats_set:
            current = _next_seat_cw(current, max_players)
        ordered.append(current)
        current = _next_seat_cw(current, max_players)
    return ordered


def _assign_positions(ordered_seats: list[int]) -> dict[int, str]:
    n = len(ordered_seats)
    if n == 0:
        return {}
    pos: dict[int, str] = {}
    if n >= 1:
        pos[ordered_seats[0]] = "SB"
    if n >= 2:
        pos[ordered_seats[1]] = "BB"
    early = POSITIONS_EARLY[-(n - 2) :] if n > 2 else []
    for seat, label in zip(ordered_seats[2:], early, strict=False):
        pos[seat] = label
    return pos


def _normalize_action(raw: str) -> str:
    mapping = {
        "posts small blind": "post_sb",
        "posts big blind": "post_bb",
        "posts the ante": "post_ante",
        "posts ante": "post_ante",
        "folds": "fold",
        "checks": "check",
        "calls": "call",
        "raises": "raise",
        "bets": "bet",
        "shows": "show",
        "mucks hand": "muck",
    }
    return mapping.get(raw, raw)


def _clean_summary_name(raw: str) -> str:
    return raw.split(" (")[0].strip()


def _action_investment(action_type: str, amount: float, raw_line: str) -> float:
    if action_type in ("post_sb", "post_bb", "post_ante", "call", "bet"):
        return amount
    if action_type == "raise":
        # "raises $X to $Y" — amount field stores Y (total put in this action)
        return amount
    return 0.0


def _compute_net_won(
    player_names: list[str],
    actions: list[dict],
    block: str,
) -> dict[str, float]:
    collected: dict[str, float] = {n: 0.0 for n in player_names}
    street_invested: dict[str, float] = {n: 0.0 for n in player_names}
    total_invested: dict[str, float] = {n: 0.0 for n in player_names}
    returned: dict[str, float] = {n: 0.0 for n in player_names}
    current_street = "preflop"

    for line in block.splitlines():
        line = line.strip()
        cm = COLLECTED_INLINE.match(line)
        if cm:
            name, amt = cm.group(1).strip(), float(cm.group(2))
            if name in collected:
                collected[name] += amt
        rm = RETURNED.match(line)
        if rm:
            amt, name = float(rm.group(1)), rm.group(2).strip()
            if name in returned:
                returned[name] += amt
        wm = SUMMARY_WON.match(line)
        if wm:
            name, amt = _clean_summary_name(wm.group(1)), float(wm.group(2))
            if name in collected:
                collected[name] += amt
            continue
        wm2 = SUMMARY_WON_SIMPLE.match(line)
        if wm2:
            name, amt = _clean_summary_name(wm2.group(1)), float(wm2.group(2))
            if name in collected:
                collected[name] += amt
            continue
        sm = SUMMARY_COLLECTED.match(line)
        if sm:
            name, amt = _clean_summary_name(sm.group(1)), float(sm.group(2))
            if name in collected:
                collected[name] += amt
            continue

    for a in actions:
        name = a["player_name"]
        if name not in total_invested:
            continue
        street = a["street"]
        if street != current_street and street not in ("showdown",):
            for n in player_names:
                street_invested[n] = 0.0
            current_street = street

        action_type = a["action_type"]
        amount = a["amount"]
        if action_type in ("post_sb", "post_bb", "post_ante", "call", "bet"):
            delta = amount
        elif action_type == "raise":
            # amount is total for this street ("raises $X to $Y" stores Y)
            delta = max(0.0, amount - street_invested[name])
        else:
            delta = 0.0

        if delta > 0:
            street_invested[name] += delta
            total_invested[name] += delta

    net: dict[str, float] = {}
    for name in player_names:
        net[name] = collected[name] + returned[name] - total_invested[name]
    return net


def parse_hand(block: str, *, dataset: str, source_file: str) -> ParsedHand:
    header = HEADER.search(block)
    if not header:
        raise ParseError("missing or malformed header")

    hand_id, stake_raw, date_str, time_str, tz = header.groups()
    is_usd, is_cap, sb, bb = classify_stake(stake_raw.strip())
    if not is_usd:
        raise SkipHand("non_usd")

    ts = datetime.strptime(f"{date_str} {time_str}", "%Y/%m/%d %H:%M:%S")
    year, month = ts.year, ts.month

    table = TABLE.search(block)
    if not table:
        raise ParseError("missing table line")
    table_name, max_players_s, button_seat_s = table.groups()
    max_players = int(max_players_s)
    button_seat = int(button_seat_s)

    hole_idx = block.find("*** HOLE CARDS ***")
    if hole_idx < 0:
        raise ParseError("missing hole cards marker")
    preflop = block[:hole_idx]
    seats = [(int(n), name.strip(), float(stack)) for n, name, stack in SEAT.findall(preflop)]
    if not seats:
        raise ParseError("no seats found")

    is_ante = ANTE_LINE.search(preflop) is not None

    seat_nums = [s[0] for s in seats]
    num_players_dealt = len(seats)
    ordered = _order_seats_sb_to_btn(seat_nums, button_seat, max_players)
    seat_to_position = _assign_positions(ordered)

    included = is_included(is_usd, is_cap, is_ante, year, max_players)
    is_primary = included and num_players_dealt == 6

    lines = block.splitlines()
    street = "preflop"
    board_flop = board_turn = board_river = None
    actions: list[dict] = []
    action_index = 0
    saw_showdown = False
    players_on_flop: set[str] = set()

    for line in lines:
        line = line.strip()
        if line == "*** HOLE CARDS ***":
            street = "preflop"
            continue
        if line.startswith("*** FLOP ***"):
            street = "flop"
            m = STREET_FLOP.match(line)
            if m:
                board_flop = f"[{m.group(1)}]"
            continue
        if line.startswith("*** TURN ***"):
            street = "turn"
            m = STREET_TURN.match(line)
            if m:
                board_turn = m.group(2)
            continue
        if line.startswith("*** RIVER ***"):
            street = "river"
            m = STREET_RIVER.match(line)
            if m:
                board_river = m.group(2)
            continue
        if line == "*** SHOW DOWN ***":
            saw_showdown = True
            street = "showdown"
            continue
        if line.startswith("*** SUMMARY ***"):
            break

        if line.startswith("Uncalled bet") and "returned to" in line:
            continue
        if line.endswith("from pot") and ":" not in line.split()[0]:
            continue

        am = ACTION.match(line)
        if not am:
            continue
        player_name, verb, amt1, amt2 = am.groups()
        amount = 0.0
        if amt2:
            amount = float(amt2)
        elif amt1:
            amount = float(amt1)
        action_type = _normalize_action(verb)
        is_all_in = "all-in" in line.lower()
        actions.append(
            {
                "hand_id": hand_id,
                "action_index": action_index,
                "street": street if street != "showdown" else "river",
                "player_name": player_name.strip(),
                "action_type": action_type,
                "amount": amount,
                "to_call": None,
                "is_all_in": is_all_in,
                "raw_line": line,
            }
        )
        action_index += 1
        if street == "flop" and action_type not in ("fold",):
            players_on_flop.add(player_name.strip())

    last_street = "preflop"
    if board_river:
        last_street = "river"
    elif board_turn:
        last_street = "turn"
    elif board_flop:
        last_street = "flop"
    if saw_showdown:
        last_street = "showdown"

    pot_total = rake = None
    summary_start = block.find("*** SUMMARY ***")
    summary = block[summary_start:] if summary_start >= 0 else ""
    for line in summary.splitlines():
        line = line.strip()
        pm = SUMMARY_POT.match(line)
        if pm:
            pot_total = float(pm.group(1))
            if pm.group(2):
                rake = float(pm.group(2))
            break

    player_names = [name for _, name, _ in seats]
    net_won = _compute_net_won(player_names, actions, block)

    player_results: dict[str, dict] = {
        name: {"showed_hand": False, "hole_card_1": None, "hole_card_2": None} for name in player_names
    }
    for line in summary.splitlines():
        sh = SHOWED.search(line)
        if sh:
            for name in player_names:
                if name in line:
                    cards = sh.group(1).split()
                    if len(cards) >= 2:
                        player_results[name]["hole_card_1"] = cards[0]
                        player_results[name]["hole_card_2"] = cards[1]
                        player_results[name]["showed_hand"] = True

    for a in actions:
        if a["action_type"] == "show" and " [" in a["raw_line"]:
            cards_m = SHOWED.search(a["raw_line"])
            if cards_m:
                cards = cards_m.group(1).split()
                if len(cards) >= 2:
                    pr = player_results.get(a["player_name"])
                    if pr:
                        pr["hole_card_1"] = cards[0]
                        pr["hole_card_2"] = cards[1]
                        pr["showed_hand"] = True

    hand_row = {
        "hand_id": hand_id,
        "dataset": dataset,
        "timestamp": ts,
        "year": year,
        "month": month,
        "stakes_raw": stake_raw.strip(),
        "sb": sb,
        "bb": bb,
        "is_usd": is_usd,
        "is_cap": is_cap,
        "is_ante": is_ante,
        "is_included": included,
        "is_primary": is_primary,
        "table_name": table_name,
        "max_players": max_players,
        "button_seat": button_seat,
        "num_players_dealt": num_players_dealt,
        "num_players_flop": len(players_on_flop) if board_flop else 0,
        "pot_total": pot_total,
        "rake": rake,
        "board_flop": board_flop,
        "board_turn": board_turn,
        "board_river": board_river,
        "last_street": last_street,
        "had_showdown": saw_showdown,
        "source_file": source_file,
    }

    player_rows = []
    for seat_num, name, stack in seats:
        pr = player_results.get(name, {})
        player_rows.append(
            {
                "hand_id": hand_id,
                "player_name": name,
                "seat": seat_num,
                "position": seat_to_position.get(seat_num),
                "stack_start": stack,
                "net_won": net_won.get(name, 0.0),
                "hole_card_1": pr.get("hole_card_1"),
                "hole_card_2": pr.get("hole_card_2"),
                "showed_hand": pr.get("showed_hand", False),
                "is_hero": False,
            }
        )

    return ParsedHand(hand=hand_row, players=player_rows, actions=actions)
