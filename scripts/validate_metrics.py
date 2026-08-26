"""Validate all metrics from docs/metric_definitions.md on parsed Parquet.

Computes preflop, postflop, and outcome stats on is_primary corpus,
runs sanity checks (ranges, invariants), and spot-checks known hands.

Usage:
  python scripts/validate_metrics.py
  python scripts/validate_metrics.py --path data/parsed
  python scripts/validate_metrics.py --path data/parsed/sample  # quick dev slice
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed"
REPORTS_DIR = PROJECT_ROOT / "reports"
DATASETS = ("NL50", "NL200", "NL5K")
POSITION_ORDER = ("LJ", "HJ", "CO", "BTN", "SB", "BB")
RFI_POSITIONS = ("LJ", "HJ", "CO", "BTN", "SB")


def parquet_glob(root: Path, table: str) -> str:
    return str(root / table / "**" / "*.parquet").replace("\\", "/")


def parquet_glob_dataset(root: Path, table: str, dataset: str) -> str:
    return str(root / table / f"dataset={dataset}" / "**" / "*.parquet").replace("\\", "/")


def parquet_glob_slice(root: Path, table: str, dataset: str, year: str) -> str:
    return str(root / table / f"dataset={dataset}" / f"year={year}" / "*.parquet").replace("\\", "/")


def list_dataset_years(root: Path, dataset: str) -> list[str]:
    hands_dir = root / "hands" / f"dataset={dataset}"
    if not hands_dir.is_dir():
        return []
    years = []
    for p in hands_dir.iterdir():
        if p.is_dir() and p.name.startswith("year="):
            years.append(p.name.replace("year=", ""))
    return sorted(years)


def clear_duckdb_temp() -> None:
    tmp = PROJECT_ROOT / ".tmp" / "duckdb"
    if not tmp.is_dir():
        return
    for f in tmp.glob("duckdb_temp*"):
        try:
            f.unlink()
        except OSError:
            pass


def configure_duckdb(con: duckdb.DuckDBPyConnection, root: Path | None = None) -> None:
    """Tune DuckDB for large Parquet scans (avoids default temp-dir cap on Windows)."""
    tmp = PROJECT_ROOT / ".tmp" / "duckdb"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory='{tmp.as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET threads=2")
    con.execute("PRAGMA max_temp_directory_size='20GiB'")


def setup_views(con: duckdb.DuckDBPyConnection, hands_g: str, players_g: str, actions_g: str) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW primary_hands AS
        SELECT * FROM read_parquet('{hands_g}') WHERE is_primary;

        CREATE OR REPLACE TEMP VIEW primary_players AS
        SELECT p.*, h.dataset, h.bb
        FROM read_parquet('{players_g}') p
        JOIN primary_hands h ON h.hand_id = p.hand_id
        WHERE p.position IS NOT NULL;

        CREATE OR REPLACE TEMP VIEW primary_actions AS
        SELECT a.*, h.bb, h.dataset, h.num_players_flop,
               h.board_flop, h.board_turn, h.board_river
        FROM read_parquet('{actions_g}') a
        JOIN primary_hands h ON h.hand_id = a.hand_id;

        CREATE OR REPLACE TEMP VIEW preflop AS
        SELECT * FROM primary_actions WHERE street = 'preflop';

        CREATE OR REPLACE TEMP VIEW preflop_raises AS
        SELECT hand_id, action_index, player_name, amount,
               ROW_NUMBER() OVER (PARTITION BY hand_id ORDER BY action_index) AS raise_n
        FROM preflop
        WHERE action_type = 'raise' AND amount > bb;

        CREATE OR REPLACE TEMP VIEW pfa AS
        SELECT hand_id, player_name AS pfa
        FROM (
            SELECT hand_id, player_name,
                   ROW_NUMBER() OVER (PARTITION BY hand_id ORDER BY action_index DESC) AS rn
            FROM preflop
            WHERE action_type = 'raise'
        ) t WHERE rn = 1;

        CREATE OR REPLACE TEMP VIEW first_voluntary AS
        SELECT hand_id, player_name, action_index, action_type, amount, bb,
               ROW_NUMBER() OVER (PARTITION BY hand_id, player_name ORDER BY action_index) AS rn
        FROM preflop
        WHERE action_type NOT IN ('post_sb', 'post_bb', 'post_ante');

        CREATE OR REPLACE TEMP VIEW player_first AS
        SELECT
            fv.hand_id, fv.player_name, fv.action_index, fv.action_type, fv.amount, fv.bb,
            COALESCE(MAX(r.raise_n), 0) AS raises_before,
            NOT COALESCE(BOOL_OR(c.hand_id IS NOT NULL), FALSE) AS is_folded_to
        FROM first_voluntary fv
        LEFT JOIN preflop_raises r
            ON r.hand_id = fv.hand_id AND r.action_index < fv.action_index
        LEFT JOIN preflop c
            ON c.hand_id = fv.hand_id AND c.action_index < fv.action_index
            AND c.action_type IN ('call', 'raise', 'bet')
        WHERE fv.rn = 1
        GROUP BY fv.hand_id, fv.player_name, fv.action_index, fv.action_type, fv.amount, fv.bb;
        """
    )


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def print_table(rows: list[tuple], headers: list[str]) -> None:
    if not rows:
        print("  (no rows)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def metric_vpip_pfr(con: duckdb.DuckDBPyConnection) -> list[Check]:
    checks: list[Check] = []
    rows = con.execute(
        """
        WITH vpip AS (
            SELECT DISTINCT hand_id, player_name FROM preflop
            WHERE action_type IN ('call', 'raise', 'bet')
        ),
        pfr AS (
            SELECT DISTINCT hand_id, player_name FROM preflop
            WHERE action_type = 'raise'
        ),
        pl AS (SELECT dataset, hand_id, player_name FROM primary_players)
        SELECT pl.dataset,
               ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip,
               ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS pfr
        FROM pl
        LEFT JOIN vpip v USING (hand_id, player_name)
        LEFT JOIN pfr f USING (hand_id, player_name)
        GROUP BY pl.dataset ORDER BY pl.dataset
        """
    ).fetchall()
    print("\n=== VPIP / PFR (overall) ===")
    print_table(rows, ["dataset", "vpip", "pfr"])
    for ds, vpip, pfr in rows:
        checks.append(Check(f"{ds} PFR <= VPIP", pfr <= vpip, f"pfr={pfr}, vpip={vpip}"))
        checks.append(Check(f"{ds} VPIP in range", 22 <= vpip <= 38, f"vpip={vpip}"))
        checks.append(Check(f"{ds} PFR in range", 12 <= pfr <= 24, f"pfr={pfr}"))
    return checks


def metric_rfi(con: duckdb.DuckDBPyConnection) -> list[Check]:
    checks: list[Check] = []
    rows = con.execute(
        """
        WITH opps AS (
            SELECT pp.dataset, pp.position, pf.hand_id, pf.player_name
            FROM player_first pf
            JOIN primary_players pp USING (hand_id, player_name)
            WHERE pf.raises_before = 0 AND pp.position IN ('LJ','HJ','CO','BTN','SB')
        ),
        hits AS (
            SELECT pf.hand_id, pf.player_name
            FROM player_first pf
            WHERE pf.raises_before = 0 AND pf.action_type = 'raise' AND pf.amount > pf.bb
        )
        SELECT o.dataset, o.position,
               COUNT(*) AS opps,
               COUNT(*) FILTER (WHERE h.hand_id IS NOT NULL) AS rfi,
               ROUND(100.0 * COUNT(*) FILTER (WHERE h.hand_id IS NOT NULL) / COUNT(*), 2) AS rfi_pct
        FROM opps o
        LEFT JOIN hits h USING (hand_id, player_name)
        GROUP BY o.dataset, o.position
        ORDER BY o.dataset, o.position
        """
    ).fetchall()
    print("\n=== RFI by position ===")
    print_table(rows, ["dataset", "position", "opps", "rfi", "rfi_pct"])

    # BTN should RFI more than LJ within each dataset
    by_ds: dict[str, dict[str, float]] = {}
    for ds, pos, _o, _r, pct in rows:
        by_ds.setdefault(ds, {})[pos] = pct
    for ds, pos_map in by_ds.items():
        if "BTN" in pos_map and "LJ" in pos_map:
            checks.append(Check(
                f"{ds} BTN RFI > LJ RFI",
                pos_map["BTN"] > pos_map["LJ"],
                f"BTN={pos_map['BTN']}, LJ={pos_map['LJ']}",
            ))
    return checks


def metric_rfi_folded_to(con: duckdb.DuckDBPyConnection) -> list[Check]:
    rows = con.execute(
        """
        WITH opps AS (
            SELECT pp.dataset, pp.position, pf.hand_id, pf.player_name
            FROM player_first pf
            JOIN primary_players pp USING (hand_id, player_name)
            WHERE pf.is_folded_to AND pp.position IN ('LJ','HJ','CO','BTN','SB')
        ),
        hits AS (
            SELECT pf.hand_id, pf.player_name
            FROM player_first pf
            WHERE pf.is_folded_to AND pf.action_type = 'raise' AND pf.amount > pf.bb
        )
        SELECT o.dataset, o.position,
               COUNT(*) AS opps,
               COUNT(*) FILTER (WHERE h.hand_id IS NOT NULL) AS rfi_ft,
               ROUND(100.0 * COUNT(*) FILTER (WHERE h.hand_id IS NOT NULL) / COUNT(*), 2) AS pct
        FROM opps o
        LEFT JOIN hits h USING (hand_id, player_name)
        GROUP BY o.dataset, o.position
        ORDER BY o.dataset, o.position
        """
    ).fetchall()
    print("\n=== RFI when folded to (by position) ===")
    print_table(rows, ["dataset", "position", "opps", "rfi_ft", "pct"])
    checks: list[Check] = []
    for ds, pos, _o, _r, pct in rows:
        if pos == "BTN" and pct is not None:
            checks.append(Check(f"{ds} BTN steal attempt reasonable", 30 <= pct <= 75, f"pct={pct}"))
    return checks


def metric_three_four_bet(con: duckdb.DuckDBPyConnection) -> list[Check]:
    checks: list[Check] = []
    rows_3 = con.execute(
        """
        WITH responses AS (
            SELECT pf.hand_id, pf.player_name, pf.action_type
            FROM player_first pf
            WHERE pf.raises_before = 1
              AND pf.player_name <>
                  COALESCE((SELECT player_name FROM preflop_raises r
                            WHERE r.hand_id = pf.hand_id AND r.raise_n = 1), '')
        )
        SELECT pp.dataset,
               COUNT(*) AS three_bet_opps,
               COUNT(*) FILTER (WHERE r.action_type = 'raise') AS three_bets,
               ROUND(100.0 * COUNT(*) FILTER (WHERE r.action_type = 'raise') / COUNT(*), 2) AS three_bet_pct
        FROM responses r
        JOIN primary_players pp USING (hand_id, player_name)
        GROUP BY pp.dataset ORDER BY pp.dataset
        """
    ).fetchall()
    rows_4 = con.execute(
        """
        WITH responses AS (
            SELECT pf.hand_id, pf.player_name, pf.action_type
            FROM player_first pf
            WHERE pf.raises_before = 2
              AND pf.player_name <>
                  COALESCE((SELECT player_name FROM preflop_raises r
                            WHERE r.hand_id = pf.hand_id AND r.raise_n = 2), '')
        )
        SELECT pp.dataset,
               COUNT(*) AS four_bet_opps,
               COUNT(*) FILTER (WHERE r.action_type = 'raise') AS four_bets,
               ROUND(100.0 * COUNT(*) FILTER (WHERE r.action_type = 'raise') / COUNT(*), 2) AS four_bet_pct
        FROM responses r
        JOIN primary_players pp USING (hand_id, player_name)
        GROUP BY pp.dataset ORDER BY pp.dataset
        """
    ).fetchall()
    four_map = {r[0]: r for r in rows_4}
    rows = []
    for r3 in rows_3:
        ds = r3[0]
        r4 = four_map.get(ds, (ds, 0, 0, None))
        rows.append((ds, r3[1], r3[2], r3[3], r4[1], r4[2], r4[3]))
    print("\n=== 3-bet / 4-bet (first response facing raise) ===")
    print_table(rows, ["dataset", "3bet_opps", "3bets", "3bet_pct", "4bet_opps", "4bets", "4bet_pct"])
    for ds, _o, _t, tb_pct, _fo, _f, fb_pct in rows:
        if tb_pct is not None:
            checks.append(Check(f"{ds} 3-bet % reasonable", 3 <= tb_pct <= 18, f"3bet={tb_pct}"))
        if fb_pct is not None:
            checks.append(Check(f"{ds} 4-bet % reasonable", 1 <= fb_pct <= 15, f"4bet={fb_pct}"))
    return checks


def metric_fold_call_vs_reraise(con: duckdb.DuckDBPyConnection) -> list[Check]:
    """Fold/call vs 3-bet (opener) and vs 4-bet (3-bettor)."""
    checks: list[Check] = []
    rows = con.execute(
        """
        WITH opener AS (
            SELECT hand_id, player_name AS opener, action_index AS open_idx
            FROM preflop_raises WHERE raise_n = 1
        ),
        three_bettor AS (
            SELECT hand_id, player_name AS three_bettor, action_index AS three_idx
            FROM preflop_raises WHERE raise_n = 2
        ),
        opener_vs_3bet AS (
            SELECT o.hand_id, o.opener,
                   a.action_type AS response
            FROM opener o
            JOIN three_bettor t USING (hand_id)
            JOIN preflop a ON a.hand_id = o.hand_id AND a.player_name = o.opener
                AND a.action_index > t.three_idx
                AND a.action_index = (
                    SELECT MIN(a2.action_index) FROM preflop a2
                    WHERE a2.hand_id = o.hand_id AND a2.player_name = o.opener
                      AND a2.action_index > t.three_idx
                      AND a2.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
                )
        ),
        four_bettor AS (
            SELECT hand_id, player_name AS four_bettor, action_index AS four_idx
            FROM preflop_raises WHERE raise_n = 3
        ),
        three_vs_4bet AS (
            SELECT t.hand_id, t.three_bettor,
                   a.action_type AS response
            FROM three_bettor t
            JOIN four_bettor f USING (hand_id)
            JOIN preflop a ON a.hand_id = t.hand_id AND a.player_name = t.three_bettor
                AND a.action_index > f.four_idx
                AND a.action_index = (
                    SELECT MIN(a2.action_index) FROM preflop a2
                    WHERE a2.hand_id = t.hand_id AND a2.player_name = t.three_bettor
                      AND a2.action_index > f.four_idx
                      AND a2.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
                )
        ),
        o3 AS (
            SELECT h.dataset,
                   COUNT(*) AS fold_to_3bet_opps,
                   COUNT(*) FILTER (WHERE response = 'fold') AS folds,
                   COUNT(*) FILTER (WHERE response = 'call') AS calls,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE response = 'fold') / COUNT(*), 2) AS fold_pct,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE response = 'call') / COUNT(*), 2) AS call_pct
            FROM opener_vs_3bet ov
            JOIN primary_hands h ON h.hand_id = ov.hand_id
            GROUP BY h.dataset
        ),
        t4 AS (
            SELECT h.dataset,
                   COUNT(*) AS fold_to_4bet_opps,
                   COUNT(*) FILTER (WHERE response = 'fold') AS folds,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE response = 'fold') / COUNT(*), 2) AS fold_pct
            FROM three_vs_4bet tv
            JOIN primary_hands h ON h.hand_id = tv.hand_id
            GROUP BY h.dataset
        )
        SELECT o3.dataset,
               o3.fold_to_3bet_opps, o3.fold_pct AS fold_to_3bet, o3.call_pct AS call_3bet,
               t4.fold_to_4bet_opps, t4.fold_pct AS fold_to_4bet
        FROM o3
        LEFT JOIN t4 USING (dataset)
        ORDER BY o3.dataset
        """
    ).fetchall()
    print("\n=== Fold/call vs 3-bet (opener) / fold vs 4-bet (3-bettor) ===")
    print_table(rows, ["dataset", "f3_opps", "fold_3bet", "call_3bet", "f4_opps", "fold_4bet"])
    for ds, _o, f3, c3, _o4, f4 in rows:
        if f3 is not None:
            checks.append(Check(f"{ds} fold to 3-bet reasonable", 40 <= f3 <= 80, f"fold={f3}"))
        if c3 is not None:
            checks.append(Check(f"{ds} call 3-bet reasonable", 5 <= c3 <= 55, f"call={c3}"))
        if f4 is not None:
            checks.append(Check(f"{ds} fold to 4-bet reasonable", 30 <= f4 <= 90, f"fold={f4}"))
    return checks


def metric_cbet(con: duckdb.DuckDBPyConnection) -> list[Check]:
    checks: list[Check] = []
    rows = con.execute(
        """
        WITH flop_pfa AS (
            SELECT h.hand_id, h.dataset, p.pfa
            FROM primary_hands h
            JOIN pfa p USING (hand_id)
            WHERE h.board_flop IS NOT NULL AND h.num_players_flop >= 2
        ),
        flop_bet AS (
            SELECT DISTINCT a.hand_id
            FROM primary_actions a
            JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
            WHERE a.street = 'flop' AND a.action_type = 'bet'
        ),
        turn_pfa AS (
            SELECT h.hand_id, h.dataset, p.pfa
            FROM primary_hands h
            JOIN pfa p USING (hand_id)
            WHERE h.board_turn IS NOT NULL AND h.num_players_flop >= 2
        ),
        turn_bet AS (
            SELECT DISTINCT a.hand_id
            FROM primary_actions a
            JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
            WHERE a.street = 'turn' AND a.action_type = 'bet'
        ),
        river_pfa AS (
            SELECT h.hand_id, h.dataset, p.pfa
            FROM primary_hands h
            JOIN pfa p USING (hand_id)
            WHERE h.board_river IS NOT NULL AND h.num_players_flop >= 2
        ),
        river_bet AS (
            SELECT DISTINCT a.hand_id
            FROM primary_actions a
            JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
            WHERE a.street = 'river' AND a.action_type = 'bet'
        ),
        flop AS (
            SELECT f.dataset,
                   COUNT(*) AS cbet_opps,
                   COUNT(*) FILTER (WHERE b.hand_id IS NOT NULL) AS cbets,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE b.hand_id IS NOT NULL) / COUNT(*), 2) AS cbet_pct
            FROM flop_pfa f
            LEFT JOIN flop_bet b USING (hand_id)
            GROUP BY f.dataset
        ),
        barrels AS (
            SELECT f.dataset,
                   COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL) AS double_barrels,
                   COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL) AS flop_bettors,
                   COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL AND rb.hand_id IS NOT NULL) AS triple_barrels
            FROM flop_pfa f
            LEFT JOIN flop_bet fb USING (hand_id)
            LEFT JOIN turn_bet tb USING (hand_id)
            LEFT JOIN river_bet rb USING (hand_id)
            GROUP BY f.dataset
        )
        SELECT flop.dataset, flop.cbet_opps, flop.cbets, flop.cbet_pct,
               barrels.flop_bettors, barrels.double_barrels,
               ROUND(100.0 * barrels.double_barrels / NULLIF(barrels.flop_bettors, 0), 2) AS dbl_pct,
               barrels.triple_barrels
        FROM flop
        JOIN barrels USING (dataset)
        ORDER BY flop.dataset
        """
    ).fetchall()
    print("\n=== Flop c-bet / double & triple barrel ===")
    print_table(rows, ["dataset", "cbet_opps", "cbets", "cbet_pct", "flop_bets", "dbl", "dbl_pct", "triple"])
    for ds, _o, _c, cb_pct, _fb, _d, dbl_pct, _t in rows:
        if cb_pct is not None:
            checks.append(Check(f"{ds} flop c-bet reasonable", 45 <= cb_pct <= 85, f"cbet={cb_pct}"))
        if dbl_pct is not None:
            checks.append(Check(f"{ds} double barrel <= c-bet", dbl_pct <= cb_pct, f"dbl={dbl_pct}, cbet={cb_pct}"))
    return checks


def metric_fold_to_cbet(con: duckdb.DuckDBPyConnection) -> list[Check]:
    rows = con.execute(
        """
        WITH flop_pfa_bet AS (
            SELECT a.hand_id, a.action_index AS bet_idx, p.pfa, h.dataset
            FROM primary_actions a
            JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
            JOIN primary_hands h ON h.hand_id = a.hand_id
            WHERE a.street = 'flop' AND a.action_type = 'bet'
        ),
        responses AS (
            SELECT fb.dataset, fb.hand_id, a.player_name, a.action_type
            FROM flop_pfa_bet fb
            JOIN primary_actions a ON a.hand_id = fb.hand_id
                AND a.street = 'flop' AND a.action_index > fb.bet_idx
                AND a.player_name <> fb.pfa
                AND a.action_type IN ('fold', 'call', 'raise')
                AND a.action_index = (
                    SELECT MIN(a2.action_index) FROM primary_actions a2
                    WHERE a2.hand_id = fb.hand_id AND a2.street = 'flop'
                      AND a2.action_index > fb.bet_idx
                      AND a2.player_name <> fb.pfa
                      AND a2.action_type IN ('fold', 'call', 'raise')
                )
        )
        SELECT dataset,
               COUNT(*) AS fold_to_cbet_opps,
               COUNT(*) FILTER (WHERE action_type = 'fold') AS folds,
               ROUND(100.0 * COUNT(*) FILTER (WHERE action_type = 'fold') / COUNT(*), 2) AS fold_pct
        FROM responses
        GROUP BY dataset ORDER BY dataset
        """
    ).fetchall()
    print("\n=== Fold to flop c-bet (first responder) ===")
    print_table(rows, ["dataset", "opps", "folds", "fold_pct"])
    checks: list[Check] = []
    for ds, _o, _f, pct in rows:
        checks.append(Check(f"{ds} fold to c-bet reasonable", 25 <= pct <= 65, f"pct={pct}"))
    return checks


def metric_check_raise(con: duckdb.DuckDBPyConnection) -> list[Check]:
    rows = con.execute(
        """
        WITH street_actions AS (
            SELECT hand_id, street, action_index, player_name, action_type, dataset
            FROM primary_actions
            WHERE street IN ('flop', 'turn', 'river')
              AND action_type IN ('check', 'bet', 'raise', 'call', 'fold')
        ),
        checks AS (
            SELECT hand_id, street, player_name, MIN(action_index) AS check_idx, dataset
            FROM street_actions WHERE action_type = 'check'
            GROUP BY hand_id, street, player_name, dataset
        ),
        opportunities AS (
            SELECT c.dataset, c.street, c.hand_id, c.player_name
            FROM checks c
            WHERE EXISTS (
                SELECT 1 FROM street_actions b
                WHERE b.hand_id = c.hand_id AND b.street = c.street
                  AND b.action_index > c.check_idx AND b.action_type = 'bet'
                  AND b.player_name <> c.player_name
            )
        ),
        xr AS (
            SELECT DISTINCT c.dataset, c.street, c.hand_id, c.player_name
            FROM checks c
            WHERE EXISTS (
                SELECT 1 FROM street_actions b
                WHERE b.hand_id = c.hand_id AND b.street = c.street
                  AND b.action_index > c.check_idx AND b.action_type = 'bet'
                  AND b.player_name <> c.player_name
            )
            AND EXISTS (
                SELECT 1 FROM street_actions r
                WHERE r.hand_id = c.hand_id AND r.street = c.street
                  AND r.player_name = c.player_name AND r.action_type = 'raise'
                  AND r.action_index > c.check_idx
            )
        )
        SELECT o.dataset, o.street,
               COUNT(*) AS opportunities,
               COUNT(*) FILTER (WHERE x.hand_id IS NOT NULL) AS check_raises,
               ROUND(100.0 * COUNT(*) FILTER (WHERE x.hand_id IS NOT NULL) / COUNT(*), 2) AS xr_pct
        FROM opportunities o
        LEFT JOIN xr x USING (hand_id, street, player_name, dataset)
        GROUP BY o.dataset, o.street
        ORDER BY o.dataset, o.street
        """
    ).fetchall()
    print("\n=== Check-raise % (when checked and faced bet) ===")
    print_table(rows, ["dataset", "street", "opps", "check_raises", "xr_pct"])
    checks: list[Check] = []
    flop_rows = [r for r in rows if r[1] == "flop"]
    for ds, _st, _o, _xr, pct in flop_rows:
        checks.append(Check(f"{ds} flop XR% reasonable", 5 <= pct <= 25, f"xr_pct={pct}"))
    total_xr = sum(r[3] for r in rows)
    checks.append(Check("check-raises exist in sample", total_xr > 100, f"total={total_xr}"))
    return checks


def metric_bb100(con: duckdb.DuckDBPyConnection) -> list[Check]:
    checks: list[Check] = []
    zs = con.execute(
        """
        WITH h AS (SELECT hand_id, rake FROM primary_hands),
        p AS (SELECT hand_id, SUM(net_won) AS sum_net FROM primary_players GROUP BY hand_id)
        SELECT
            ROUND(100.0 * COUNT(*) FILTER (WHERE ABS(p.sum_net + COALESCE(h.rake, 0)) > 0.01)
                  / COUNT(*), 2) AS pct_bad_zero_sum
        FROM h JOIN p USING (hand_id)
        """
    ).fetchone()[0]
    print(f"\n=== net_won zero-sum (sum net_won + rake should be 0 per hand) ===")
    print(f"  Hands failing zero-sum: {zs}%")
    checks.append(Check(
        "net_won zero-sum (<5% bad hands)",
        zs < 5.0,
        f"{zs}% bad — re-run parse_sample.py after net_won fix",
    ))

    rows = con.execute(
        """
        WITH hand_rake AS (
            SELECT dataset, hand_id, bb, COALESCE(rake, 0) AS rake,
                   rake > 0 AS is_raked
            FROM primary_hands
        ),
        player_bb AS (
            SELECT dataset,
                   COUNT(DISTINCT hand_id) AS hands,
                   COUNT(*) AS player_hands,
                   ROUND(100.0 * SUM(net_won / bb) / COUNT(*), 2) AS bb100
            FROM primary_players
            GROUP BY dataset
        ),
        rake_bb AS (
            SELECT dataset,
                   ROUND(-100.0 * SUM(rake / bb) / (COUNT(*) * 6.0), 2) AS rake_bb100_equiv,
                   ROUND(100.0 * SUM(CASE WHEN is_raked THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_hands_raked,
                   ROUND(AVG(CASE WHEN is_raked THEN rake / bb END), 3) AS avg_rake_bb_when_raked
            FROM hand_rake
            GROUP BY dataset
        )
        SELECT pb.dataset, pb.hands, pb.player_hands, pb.bb100,
               rb.rake_bb100_equiv, rb.pct_hands_raked, rb.avg_rake_bb_when_raked
        FROM player_bb pb
        JOIN rake_bb rb USING (dataset)
        ORDER BY pb.dataset
        """
    ).fetchall()
    print("\n=== bb/100 (after rake; population sum ~= -rake) ===")
    print("  Standard cash-game stat. More negative = higher rake/BB at that stake.")
    print_table(rows, ["dataset", "hands", "player_hands", "bb100", "rake_equiv", "pct_raked", "avg_rake_bb"])
    if zs < 5.0:
        bb100_map = {r[0]: r[3] for r in rows}
        if all(k in bb100_map for k in ("NL50", "NL200", "NL5K")):
            checks.append(Check(
                "NL50 bb/100 <= NL200 <= NL5K (rake/BB ordering)",
                bb100_map["NL50"] <= bb100_map["NL200"] <= bb100_map["NL5K"],
                f"NL50={bb100_map['NL50']}, NL200={bb100_map['NL200']}, NL5K={bb100_map['NL5K']}",
            ))
        for ds, _h, _ph, bb100, _re, _pr, _arb in rows:
            checks.append(Check(f"{ds} bb/100 in range", -15 <= bb100 <= 2, f"bb/100={bb100}"))
    else:
        print("  (bb/100 checks skipped until net_won zero-sum is fixed and sample re-parsed)")
    return checks


def metric_bb100_population(root: Path, reports_dir: Path) -> list[Check]:
    """Population bb/100 should match rake drag; per-hand net_won should zero-sum with rake."""
    checks: list[Check] = []
    bb100_path = reports_dir / "metrics_bb100.csv"
    if bb100_path.exists():
        bb100_df = pd.read_csv(bb100_path)
        overall = bb100_df[bb100_df["granularity"] == "overall"]
        print("\n=== bb/100 vs rake (population, from reports) ===")
        rows = []
        for _, row in overall.iterrows():
            ds = row["dataset"]
            bb100 = float(row["bb100"])
            rake_bb = float(row["rake_bb100_equiv"]) if pd.notna(row["rake_bb100_equiv"]) else None
            rows.append((ds, bb100, rake_bb))
            if rake_bb is not None:
                checks.append(Check(
                    f"{ds} bb/100 ~ rake cost",
                    abs(bb100 - rake_bb) < 0.5,
                    f"bb100={bb100}, rake_bb100={rake_bb}",
                ))
        print_table(rows, ["dataset", "bb100", "rake_bb100"])

    hands_g = parquet_glob(root, "hands")
    players_g = parquet_glob(root, "players")
    con = duckdb.connect()
    configure_duckdb(con, root)
    rows = con.execute(
        f"""
        WITH hand_totals AS (
            SELECT p.hand_id,
                   SUM(p.net_won) AS hand_net,
                   MAX(h.rake) AS rake,
                   MAX(h.dataset) AS dataset
            FROM read_parquet('{players_g}') p
            JOIN read_parquet('{hands_g}') h ON h.hand_id = p.hand_id
            WHERE h.is_primary
            GROUP BY p.hand_id
        )
        SELECT dataset,
               COUNT(*) AS hands,
               COUNT(*) FILTER (
                   WHERE ABS(hand_net + COALESCE(rake, 0)) > 0.01
               ) AS bad_hands,
               ROUND(100.0 * COUNT(*) FILTER (
                   WHERE ABS(hand_net + COALESCE(rake, 0)) > 0.01
               ) / COUNT(*), 3) AS bad_pct,
               ROUND(SUM(hand_net + COALESCE(rake, 0)), 2) AS total_residual
        FROM hand_totals
        GROUP BY dataset
        ORDER BY dataset
        """
    ).fetchall()
    con.close()
    print("\n=== Per-hand zero-sum (net_won + rake) ===")
    print_table(rows, ["dataset", "hands", "bad_hands", "bad_pct", "total_residual"])
    for ds, _hands, _bad, bad_pct, residual in rows:
        checks.append(Check(
            f"{ds} hand zero-sum (<0.1% bad)",
            bad_pct < 0.1,
            f"bad_pct={bad_pct}%",
        ))
        checks.append(Check(
            f"{ds} total residual small",
            abs(residual) < 1000,
            f"residual={residual}",
        ))
    return checks


def validate_reports(reports_dir: Path) -> list[Check]:
    """Sanity-check exported metrics CSVs (for full corpus after compute_all_metrics)."""
    checks: list[Check] = []
    overall_path = reports_dir / "metrics_overall.csv"
    if not overall_path.exists():
        return [Check("metrics_overall.csv exists", False, str(overall_path))]

    overall = pd.read_csv(overall_path)
    print("\n=== VPIP / PFR (from reports) ===")
    print_table(
        [tuple(r) for r in overall[["dataset", "vpip_pct", "pfr_pct"]].itertuples(index=False, name=None)],
        ["dataset", "vpip", "pfr"],
    )
    for _, row in overall.iterrows():
        ds, vpip, pfr = row["dataset"], row["vpip_pct"], row["pfr_pct"]
        checks.append(Check(f"{ds} PFR <= VPIP", pfr <= vpip, f"pfr={pfr}, vpip={vpip}"))
        checks.append(Check(f"{ds} VPIP in range", 22 <= vpip <= 38, f"vpip={vpip}"))
        checks.append(Check(f"{ds} PFR in range", 12 <= pfr <= 24, f"pfr={pfr}"))

    pos_path = reports_dir / "metrics_by_position.csv"
    if pos_path.exists():
        pos = pd.read_csv(pos_path)
        print("\n=== RFI by position (from reports) ===")
        rfi = pos[pos["position"].isin(RFI_POSITIONS)][
            ["dataset", "position", "rfi_opps", "rfi_count", "rfi_pct"]
        ]
        print_table(
            [tuple(r) for r in rfi.itertuples(index=False, name=None)],
            ["dataset", "position", "opps", "rfi", "rfi_pct"],
        )
        for ds in pos["dataset"].unique():
            sub = pos[(pos["dataset"] == ds) & (pos["position"].isin(["BTN", "LJ"]))]
            btn = sub[sub["position"] == "BTN"]["rfi_pct"]
            lj = sub[sub["position"] == "LJ"]["rfi_pct"]
            if not btn.empty and not lj.empty:
                checks.append(Check(
                    f"{ds} BTN RFI > LJ RFI",
                    float(btn.iloc[0]) > float(lj.iloc[0]),
                    f"BTN={btn.iloc[0]}, LJ={lj.iloc[0]}",
                ))

    preflop_path = reports_dir / "metrics_preflop.csv"
    if preflop_path.exists():
        pf = pd.read_csv(preflop_path)
        print("\n=== 3-bet / 4-bet (from reports) ===")
        cols = [c for c in pf.columns if c in (
            "dataset", "three_bet_opps", "three_bets", "three_bet_pct",
            "four_bet_opps", "four_bets", "four_bet_pct",
        )]
        print_table([tuple(r) for r in pf[cols].itertuples(index=False, name=None)], cols)
        for _, row in pf.iterrows():
            ds = row["dataset"]
            if pd.notna(row.get("three_bet_pct")):
                checks.append(Check(
                    f"{ds} 3-bet % reasonable",
                    3 <= row["three_bet_pct"] <= 18,
                    f"3bet={row['three_bet_pct']}",
                ))
            if pd.notna(row.get("four_bet_pct")):
                checks.append(Check(
                    f"{ds} 4-bet % reasonable",
                    1 <= row["four_bet_pct"] <= 15,
                    f"4bet={row['four_bet_pct']}",
                ))

    postflop_path = reports_dir / "metrics_postflop.csv"
    if postflop_path.exists():
        po = pd.read_csv(postflop_path)
        print("\n=== Flop c-bet (from reports) ===")
        if "cbet_pct" in po.columns:
            print_table(
                [tuple(r) for r in po[["dataset", "cbet_opps", "cbets", "cbet_pct"]].itertuples(index=False, name=None)],
                ["dataset", "cbet_opps", "cbets", "cbet_pct"],
            )
            for _, row in po.iterrows():
                if pd.notna(row.get("cbet_pct")):
                    checks.append(Check(
                        f"{row['dataset']} c-bet % reasonable",
                        40 <= row["cbet_pct"] <= 85,
                        f"cbet={row['cbet_pct']}",
                    ))

    return checks


def spot_checks(con: duckdb.DuckDBPyConnection) -> list[Check]:
    """Known hands from data/parsed/sample/review/."""
    checks: list[Check] = []
    print("\n=== Spot-check hands ===")

    def pfr(hid: str, player: str) -> int:
        r = con.execute(
            """
            SELECT MAX(CASE WHEN action_type = 'raise' THEN 1 ELSE 0 END)
            FROM preflop WHERE CAST(hand_id AS VARCHAR) = ? AND player_name = ?
            """,
            [hid, player],
        ).fetchone()[0]
        return int(r or 0)

    def rfi(hid: str, player: str) -> int:
        r = con.execute(
            """
            SELECT CASE WHEN pf.action_type = 'raise' AND pf.amount > pf.bb AND pf.raises_before = 0
                   THEN 1 ELSE 0 END
            FROM player_first pf
            WHERE CAST(pf.hand_id AS VARCHAR) = ? AND pf.player_name = ?
            """,
            [hid, player],
        ).fetchone()
        return int(r[0] if r else 0)

    def is_pfa(hid: str, player: str) -> int:
        r = con.execute(
            "SELECT CASE WHEN pfa = ? THEN 1 ELSE 0 END FROM pfa WHERE CAST(hand_id AS VARCHAR) = ?",
            [player, hid],
        ).fetchone()
        return int(r[0] if r else 0)

    def cbet_flop(hid: str, player: str) -> int:
        r = con.execute(
            """
            SELECT MAX(CASE WHEN a.action_type = 'bet' THEN 1 ELSE 0 END)
            FROM primary_actions a
            WHERE CAST(a.hand_id AS VARCHAR) = ? AND a.player_name = ? AND a.street = 'flop'
            """,
            [hid, player],
        ).fetchone()[0]
        return int(r or 0)

    def check_raise_flop(hid: str, player: str) -> int:
        r = con.execute(
            """
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM primary_actions c
                WHERE CAST(c.hand_id AS VARCHAR) = ? AND c.player_name = ? AND c.street = 'flop'
                  AND c.action_type = 'check'
            ) AND EXISTS (
                SELECT 1 FROM primary_actions r
                WHERE CAST(r.hand_id AS VARCHAR) = ? AND r.player_name = ? AND r.street = 'flop'
                  AND r.action_type = 'raise'
            ) THEN 1 ELSE 0 END
            """,
            [hid, player, hid, player],
        ).fetchone()[0]
        return int(r or 0)

    spots = [
        ("109431432628", "tomba26", "RFI/PFR/PFA/c-bet", lambda: (
            rfi("109431432628", "tomba26") == 1
            and pfr("109431432628", "tomba26") == 1
            and is_pfa("109431432628", "tomba26") == 1
            and cbet_flop("109431432628", "tomba26") == 1
        )),
        ("109431432628", "fish2013", "call/no PFR/check-raise flop", lambda: (
            pfr("109431432628", "fish2013") == 0
            and check_raise_flop("109431432628", "fish2013") == 1
        )),
        ("109431432628", "Tim0thee", "fold preflop", lambda: pfr("109431432628", "Tim0thee") == 0),
        ("109432020251", "Tim0thee", "BB walk win", lambda: (
            con.execute(
                "SELECT net_won FROM primary_players WHERE CAST(hand_id AS VARCHAR) = '109432020251' AND player_name = 'Tim0thee'"
            ).fetchone()[0] == 25.0
        )),
    ]
    for hid, player, note, fn in spots:
        try:
            ok = bool(fn())
        except Exception as exc:
            ok = False
            note = f"{note} ({exc})"
        status = "OK" if ok else "FAIL"
        print(f"  {status} #{hid} {player}: {note}")
        checks.append(Check(f"spot #{hid} {player}", ok, note))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate all metrics on sample parse")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=REPORTS_DIR,
        help="Reports directory for full-corpus CSV validation",
    )
    parser.add_argument(
        "--skip-spots",
        action="store_true",
        help="Skip known-hand spot checks (recommended on full corpus)",
    )
    args = parser.parse_args()
    root = args.path

    if not (root / "hands").exists():
        print(f"No parsed data at {root}. Run: python scripts/parse_sample.py")
        sys.exit(1)

    hands_g = parquet_glob(root, "hands")
    n_primary = duckdb.connect().execute(
        f"SELECT COUNT(*) FROM read_parquet('{hands_g}') WHERE is_primary"
    ).fetchone()[0]
    use_reports = n_primary > 5_000_000

    print(f"=== Metric validation (is_primary, n={n_primary:,} hands) ===")
    print(f"Path: {root}")
    if use_reports:
        print("Mode: validate exported reports + lightweight Parquet checks\n")

    all_checks: list[Check] = []
    if use_reports:
        all_checks += validate_reports(args.reports_dir)
        all_checks += metric_bb100_population(root, args.reports_dir)
    else:
        players_g = parquet_glob(root, "players")
        actions_g = parquet_glob(root, "actions")
        con = duckdb.connect()
        configure_duckdb(con, root)
        setup_views(con, hands_g, players_g, actions_g)
        all_checks += metric_vpip_pfr(con)
        all_checks += metric_rfi(con)
        all_checks += metric_rfi_folded_to(con)
        all_checks += metric_three_four_bet(con)
        all_checks += metric_fold_call_vs_reraise(con)
        all_checks += metric_cbet(con)
        all_checks += metric_fold_to_cbet(con)
        all_checks += metric_check_raise(con)
        all_checks += metric_bb100(con)
        if not args.skip_spots:
            all_checks += spot_checks(con)

    passed = sum(1 for c in all_checks if c.ok)
    failed = [c for c in all_checks if not c.ok]

    print("\n=== Summary ===")
    print(f"  Checks passed: {passed}/{len(all_checks)}")
    if failed:
        print("  Failed:")
        for c in failed:
            print(f"    - {c.name}: {c.detail}")
        parser_only = all(
            "zero-sum" in c.name
            or "hand zero-sum" in c.name
            or "total residual" in c.name
            or "bb/100 ~ rake" in c.name
            for c in failed
        )
        if parser_only:
            print("\n  Action/street metrics look good. Per-hand net_won may need a parser fix + re-parse.")
        else:
            print("\n  NOT ready for full corpus parse until failures are investigated.")
        sys.exit(1)
    else:
        print("  All checks passed. Metrics look sound.")
        if use_reports:
            print("  Full-corpus reports validated.")
        else:
            print("  Ready to proceed with full corpus parse when you want.")


if __name__ == "__main__":
    main()
