"""Compute all key metrics from parsed Parquet and export to reports/.

Runs DuckDB queries (same logic as validate_metrics.py) on the is_primary corpus,
writes structured CSV outputs plus metrics_summary.json, and prints a summary table.

Usage:
  python scripts/compute_all_metrics.py
  python scripts/compute_all_metrics.py --path data/parsed/sample
  python scripts/compute_all_metrics.py --path data/parsed --json
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_metrics import (  # noqa: E402
    DATASETS,
    clear_duckdb_temp,
    configure_duckdb,
    list_dataset_years,
    parquet_glob,
    parquet_glob_dataset,
    parquet_glob_slice,
    print_table,
    setup_views,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed"
REPORTS_DIR = PROJECT_ROOT / "reports"
CHECKPOINT_ROOT = PROJECT_ROOT / ".tmp" / "metrics_checkpoint"
POSITION_ORDER = ("LJ", "HJ", "CO", "BTN", "SB", "BB")

SQL_OVERALL = """
WITH vpip AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type IN ('call', 'raise', 'bet')
),
pfr AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type = 'raise'
),
pl AS (SELECT dataset, hand_id, player_name FROM primary_players)
SELECT
    pl.dataset,
    COUNT(DISTINCT pl.hand_id) AS hands,
    COUNT(*) AS player_hands,
    COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) AS vpip_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip_pct,
    COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) AS pfr_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS pfr_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2)
        - ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip_pfr_gap_pp
FROM pl
LEFT JOIN vpip v USING (hand_id, player_name)
LEFT JOIN pfr f USING (hand_id, player_name)
GROUP BY pl.dataset
ORDER BY pl.dataset
"""

SQL_BY_POSITION = """
WITH vpip AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type IN ('call', 'raise', 'bet')
),
pfr AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type = 'raise'
),
rfi_opps AS (
    SELECT pp.dataset, pp.position, pf.hand_id, pf.player_name
    FROM player_first pf
    JOIN primary_players pp USING (hand_id, player_name)
    WHERE pf.raises_before = 0 AND pp.position IN ('LJ','HJ','CO','BTN','SB')
),
rfi_hits AS (
    SELECT pf.hand_id, pf.player_name
    FROM player_first pf
    WHERE pf.raises_before = 0 AND pf.action_type = 'raise' AND pf.amount > pf.bb
),
rfi_ft_opps AS (
    SELECT pp.dataset, pp.position, pf.hand_id, pf.player_name
    FROM player_first pf
    JOIN primary_players pp USING (hand_id, player_name)
    WHERE pf.is_folded_to AND pp.position IN ('LJ','HJ','CO','BTN','SB')
),
rfi_ft_hits AS (
    SELECT pf.hand_id, pf.player_name
    FROM player_first pf
    WHERE pf.is_folded_to AND pf.action_type = 'raise' AND pf.amount > pf.bb
),
pl AS (
    SELECT dataset, position, hand_id, player_name
    FROM primary_players
    WHERE position IS NOT NULL
)
SELECT
    pl.dataset,
    pl.position,
    COUNT(*) AS player_hands,
    COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) AS vpip_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip_pct,
    COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) AS pfr_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS pfr_pct,
    COUNT(*) FILTER (WHERE ro.hand_id IS NOT NULL) AS rfi_opps,
    COUNT(*) FILTER (WHERE rh.hand_id IS NOT NULL) AS rfi_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE rh.hand_id IS NOT NULL)
          / NULLIF(COUNT(*) FILTER (WHERE ro.hand_id IS NOT NULL), 0), 2) AS rfi_pct,
    COUNT(*) FILTER (WHERE rfo.hand_id IS NOT NULL) AS rfi_folded_to_opps,
    COUNT(*) FILTER (WHERE rfh.hand_id IS NOT NULL) AS rfi_folded_to_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE rfh.hand_id IS NOT NULL)
          / NULLIF(COUNT(*) FILTER (WHERE rfo.hand_id IS NOT NULL), 0), 2) AS rfi_folded_to_pct
FROM pl
LEFT JOIN vpip v USING (hand_id, player_name)
LEFT JOIN pfr f USING (hand_id, player_name)
LEFT JOIN rfi_opps ro USING (hand_id, player_name)
LEFT JOIN rfi_hits rh USING (hand_id, player_name)
LEFT JOIN rfi_ft_opps rfo USING (hand_id, player_name)
LEFT JOIN rfi_ft_hits rfh USING (hand_id, player_name)
GROUP BY pl.dataset, pl.position
ORDER BY pl.dataset, pl.position
"""

SQL_BY_YEAR = """
WITH vpip AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type IN ('call', 'raise', 'bet')
),
pfr AS (
    SELECT DISTINCT hand_id, player_name FROM preflop WHERE action_type = 'raise'
),
three_bet AS (
    SELECT pf.hand_id, pf.player_name, pf.action_type
    FROM player_first pf
    WHERE pf.raises_before = 1
      AND pf.player_name <>
          COALESCE((SELECT player_name FROM preflop_raises r
                    WHERE r.hand_id = pf.hand_id AND r.raise_n = 1), '')
),
cbet AS (
    SELECT h.hand_id, h.dataset, h.year
    FROM primary_hands h
    JOIN pfa p USING (hand_id)
    WHERE h.board_flop IS NOT NULL AND h.num_players_flop >= 2
),
cbet_hit AS (
    SELECT DISTINCT a.hand_id
    FROM primary_actions a
    JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
    WHERE a.street = 'flop' AND a.action_type = 'bet'
),
pl AS (
    SELECT h.dataset, h.year, p.hand_id, p.player_name, h.bb, p.net_won
    FROM primary_hands h
    JOIN primary_players p ON p.hand_id = h.hand_id
)
SELECT
    pl.dataset,
    pl.year,
    COUNT(DISTINCT pl.hand_id) AS hands,
    COUNT(*) AS player_hands,
    ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS pfr_pct,
    COUNT(*) FILTER (WHERE tb.hand_id IS NOT NULL) AS three_bet_opps,
    ROUND(100.0 * COUNT(*) FILTER (WHERE tb.action_type = 'raise')
          / NULLIF(COUNT(*) FILTER (WHERE tb.hand_id IS NOT NULL), 0), 2) AS three_bet_pct,
    COUNT(*) FILTER (WHERE c.hand_id IS NOT NULL) AS cbet_opps,
    ROUND(100.0 * COUNT(*) FILTER (WHERE ch.hand_id IS NOT NULL)
          / NULLIF(COUNT(*) FILTER (WHERE c.hand_id IS NOT NULL), 0), 2) AS cbet_pct,
    ROUND(100.0 * SUM(pl.net_won / pl.bb) / COUNT(*), 2) AS bb100
FROM pl
LEFT JOIN vpip v USING (hand_id, player_name)
LEFT JOIN pfr f USING (hand_id, player_name)
LEFT JOIN three_bet tb USING (hand_id, player_name)
LEFT JOIN cbet c ON c.hand_id = pl.hand_id
LEFT JOIN cbet_hit ch ON ch.hand_id = pl.hand_id
GROUP BY pl.dataset, pl.year
ORDER BY pl.dataset, pl.year
"""

SQL_PREFLOP = """
WITH three_bet AS (
    SELECT pf.hand_id, pf.player_name, pf.action_type
    FROM player_first pf
    WHERE pf.raises_before = 1
      AND pf.player_name <>
          COALESCE((SELECT player_name FROM preflop_raises r
                    WHERE r.hand_id = pf.hand_id AND r.raise_n = 1), '')
),
four_bet AS (
    SELECT pf.hand_id, pf.player_name, pf.action_type
    FROM player_first pf
    WHERE pf.raises_before = 2
      AND pf.player_name <>
          COALESCE((SELECT player_name FROM preflop_raises r
                    WHERE r.hand_id = pf.hand_id AND r.raise_n = 2), '')
),
opener AS (
    SELECT hand_id, player_name AS opener, action_index AS open_idx
    FROM preflop_raises WHERE raise_n = 1
),
three_bettor AS (
    SELECT hand_id, player_name AS three_bettor, action_index AS three_idx
    FROM preflop_raises WHERE raise_n = 2
),
opener_vs_3bet AS (
    SELECT o.hand_id, o.opener, a.action_type AS response
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
    SELECT t.hand_id, t.three_bettor, a.action_type AS response
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
tb AS (
    SELECT h.dataset,
           COUNT(*) AS three_bet_opps,
           COUNT(*) FILTER (WHERE tb.action_type = 'raise') AS three_bets,
           ROUND(100.0 * COUNT(*) FILTER (WHERE tb.action_type = 'raise') / COUNT(*), 2) AS three_bet_pct
    FROM three_bet tb
    JOIN primary_hands h ON h.hand_id = tb.hand_id
    GROUP BY h.dataset
),
fb AS (
    SELECT h.dataset,
           COUNT(*) AS four_bet_opps,
           COUNT(*) FILTER (WHERE fb.action_type = 'raise') AS four_bets,
           ROUND(100.0 * COUNT(*) FILTER (WHERE fb.action_type = 'raise') / COUNT(*), 2) AS four_bet_pct
    FROM four_bet fb
    JOIN primary_hands h ON h.hand_id = fb.hand_id
    GROUP BY h.dataset
),
o3 AS (
    SELECT h.dataset,
           COUNT(*) AS fold_to_3bet_opps,
           COUNT(*) FILTER (WHERE response = 'fold') AS fold_to_3bet_count,
           ROUND(100.0 * COUNT(*) FILTER (WHERE response = 'fold') / COUNT(*), 2) AS fold_to_3bet_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE response = 'call') / COUNT(*), 2) AS call_3bet_pct
    FROM opener_vs_3bet ov
    JOIN primary_hands h ON h.hand_id = ov.hand_id
    GROUP BY h.dataset
),
t4 AS (
    SELECT h.dataset,
           COUNT(*) AS fold_to_4bet_opps,
           COUNT(*) FILTER (WHERE response = 'fold') AS fold_to_4bet_count,
           ROUND(100.0 * COUNT(*) FILTER (WHERE response = 'fold') / COUNT(*), 2) AS fold_to_4bet_pct
    FROM three_vs_4bet tv
    JOIN primary_hands h ON h.hand_id = tv.hand_id
    GROUP BY h.dataset
)
SELECT
    tb.dataset,
    tb.three_bet_opps, tb.three_bets, tb.three_bet_pct,
    fb.four_bet_opps, fb.four_bets, fb.four_bet_pct,
    o3.fold_to_3bet_opps, o3.fold_to_3bet_count, o3.fold_to_3bet_pct, o3.call_3bet_pct,
    t4.fold_to_4bet_opps, t4.fold_to_4bet_count, t4.fold_to_4bet_pct
FROM tb
LEFT JOIN fb USING (dataset)
LEFT JOIN o3 USING (dataset)
LEFT JOIN t4 USING (dataset)
ORDER BY tb.dataset
"""

SQL_POSTFLOP = """
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
turn_bet AS (
    SELECT DISTINCT a.hand_id
    FROM primary_actions a
    JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
    WHERE a.street = 'turn' AND a.action_type = 'bet'
),
river_bet AS (
    SELECT DISTINCT a.hand_id
    FROM primary_actions a
    JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
    WHERE a.street = 'river' AND a.action_type = 'bet'
),
flop_cbet AS (
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
           COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL) AS flop_bettors,
           COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL) AS double_barrels,
           ROUND(100.0 * COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL)
                 / NULLIF(COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL), 0), 2) AS double_barrel_pct,
           COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL AND rb.hand_id IS NOT NULL) AS triple_barrels
    FROM flop_pfa f
    LEFT JOIN flop_bet fb USING (hand_id)
    LEFT JOIN turn_bet tb USING (hand_id)
    LEFT JOIN river_bet rb USING (hand_id)
    GROUP BY f.dataset
),
flop_pfa_bet AS (
    SELECT a.hand_id, a.action_index AS bet_idx, p.pfa, h.dataset
    FROM primary_actions a
    JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
    JOIN primary_hands h ON h.hand_id = a.hand_id
    WHERE a.street = 'flop' AND a.action_type = 'bet'
),
fold_to_cbet AS (
    SELECT fb.dataset,
           COUNT(*) AS fold_to_cbet_opps,
           COUNT(*) FILTER (WHERE a.action_type = 'fold') AS fold_to_cbet_count,
           ROUND(100.0 * COUNT(*) FILTER (WHERE a.action_type = 'fold') / COUNT(*), 2) AS fold_to_cbet_pct
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
    GROUP BY fb.dataset
),
street_actions AS (
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
xr_opps AS (
    SELECT c.dataset, c.street, c.hand_id, c.player_name
    FROM checks c
    WHERE EXISTS (
        SELECT 1 FROM street_actions b
        WHERE b.hand_id = c.hand_id AND b.street = c.street
          AND b.action_index > c.check_idx AND b.action_type = 'bet'
          AND b.player_name <> c.player_name
    )
),
xr_hits AS (
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
),
check_raise AS (
    SELECT o.dataset, o.street,
           COUNT(*) AS check_raise_opps,
           COUNT(*) FILTER (WHERE x.hand_id IS NOT NULL) AS check_raises,
           ROUND(100.0 * COUNT(*) FILTER (WHERE x.hand_id IS NOT NULL) / COUNT(*), 2) AS check_raise_pct
    FROM xr_opps o
    LEFT JOIN xr_hits x USING (hand_id, street, player_name, dataset)
    GROUP BY o.dataset, o.street
),
check_raise_pivot AS (
    SELECT dataset,
           MAX(CASE WHEN street = 'flop' THEN check_raise_opps END) AS check_raise_flop_opps,
           MAX(CASE WHEN street = 'flop' THEN check_raises END) AS check_raise_flop_count,
           MAX(CASE WHEN street = 'flop' THEN check_raise_pct END) AS check_raise_flop_pct,
           MAX(CASE WHEN street = 'turn' THEN check_raise_opps END) AS check_raise_turn_opps,
           MAX(CASE WHEN street = 'turn' THEN check_raises END) AS check_raise_turn_count,
           MAX(CASE WHEN street = 'turn' THEN check_raise_pct END) AS check_raise_turn_pct,
           MAX(CASE WHEN street = 'river' THEN check_raise_opps END) AS check_raise_river_opps,
           MAX(CASE WHEN street = 'river' THEN check_raises END) AS check_raise_river_count,
           MAX(CASE WHEN street = 'river' THEN check_raise_pct END) AS check_raise_river_pct
    FROM check_raise
    GROUP BY dataset
)
SELECT
    fc.dataset,
    fc.cbet_opps, fc.cbets, fc.cbet_pct,
    b.flop_bettors, b.double_barrels, b.double_barrel_pct, b.triple_barrels,
    ftc.fold_to_cbet_opps, ftc.fold_to_cbet_count, ftc.fold_to_cbet_pct,
    cr.check_raise_flop_opps, cr.check_raise_flop_count, cr.check_raise_flop_pct,
    cr.check_raise_turn_opps, cr.check_raise_turn_count, cr.check_raise_turn_pct,
    cr.check_raise_river_opps, cr.check_raise_river_count, cr.check_raise_river_pct
FROM flop_cbet fc
JOIN barrels b USING (dataset)
JOIN fold_to_cbet ftc USING (dataset)
LEFT JOIN check_raise_pivot cr USING (dataset)
ORDER BY fc.dataset
"""

SQL_BB100_OVERALL = """
WITH hand_rake AS (
    SELECT dataset, hand_id, bb, COALESCE(rake, 0) AS rake, rake > 0 AS is_raked
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
SELECT
    'overall' AS granularity,
    pb.dataset,
    CAST(NULL AS INTEGER) AS year,
    pb.hands,
    pb.player_hands,
    pb.bb100,
    rb.rake_bb100_equiv,
    rb.pct_hands_raked,
    rb.avg_rake_bb_when_raked
FROM player_bb pb
JOIN rake_bb rb USING (dataset)
ORDER BY pb.dataset
"""

SQL_BB100_BY_YEAR = """
SELECT
    'year' AS granularity,
    h.dataset,
    h.year,
    COUNT(DISTINCT h.hand_id) AS hands,
    COUNT(*) AS player_hands,
    ROUND(100.0 * SUM(p.net_won / h.bb) / COUNT(*), 2) AS bb100,
    CAST(NULL AS DOUBLE) AS rake_bb100_equiv,
    CAST(NULL AS DOUBLE) AS pct_hands_raked,
    CAST(NULL AS DOUBLE) AS avg_rake_bb_when_raked
FROM primary_hands h
JOIN primary_players p ON p.hand_id = h.hand_id
GROUP BY h.dataset, h.year
ORDER BY h.dataset, h.year
"""


def sort_by_position(df):
    pos_order = {p: i for i, p in enumerate(POSITION_ORDER)}
    df = df.copy()
    df["_pos_ord"] = df["position"].map(pos_order)
    return df.sort_values(["dataset", "_pos_ord"]).drop(columns=["_pos_ord"])


def _pct(num: pd.Series, den: pd.Series) -> pd.Series:
    mask = den.fillna(0) > 0
    out = pd.Series(pd.NA, index=num.index, dtype="Float64")
    out.loc[mask] = (100.0 * num.loc[mask] / den.loc[mask]).round(2)
    return out


def aggregate_overall(parts: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat(parts, ignore_index=True)
    agg = df.groupby("dataset", as_index=False).agg(
        hands=("hands", "sum"),
        player_hands=("player_hands", "sum"),
        vpip_count=("vpip_count", "sum"),
        pfr_count=("pfr_count", "sum"),
    )
    agg["vpip_pct"] = _pct(agg["vpip_count"], agg["player_hands"])
    agg["pfr_pct"] = _pct(agg["pfr_count"], agg["player_hands"])
    agg["vpip_pfr_gap_pp"] = (agg["vpip_pct"] - agg["pfr_pct"]).round(2)
    return agg.sort_values("dataset")


def aggregate_by_position(parts: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat(parts, ignore_index=True)
    agg = df.groupby(["dataset", "position"], as_index=False).agg(
        player_hands=("player_hands", "sum"),
        vpip_count=("vpip_count", "sum"),
        pfr_count=("pfr_count", "sum"),
        rfi_opps=("rfi_opps", "sum"),
        rfi_count=("rfi_count", "sum"),
        rfi_folded_to_opps=("rfi_folded_to_opps", "sum"),
        rfi_folded_to_count=("rfi_folded_to_count", "sum"),
    )
    agg["vpip_pct"] = _pct(agg["vpip_count"], agg["player_hands"])
    agg["pfr_pct"] = _pct(agg["pfr_count"], agg["player_hands"])
    agg["rfi_pct"] = _pct(agg["rfi_count"], agg["rfi_opps"])
    agg["rfi_folded_to_pct"] = _pct(agg["rfi_folded_to_count"], agg["rfi_folded_to_opps"])
    return agg.sort_values(["dataset", "position"])


def aggregate_preflop(parts: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat(parts, ignore_index=True)
    # Reconstruct call counts from pct when older checkpoints lack call_3bet_count
    if "call_3bet_count" not in df.columns and "call_3bet_pct" in df.columns:
        df = df.copy()
        df["call_3bet_count"] = (
            df["call_3bet_pct"].fillna(0) * df["fold_to_3bet_opps"].fillna(0) / 100.0
        ).round()
    sum_cols = [
        "three_bet_opps", "three_bets", "four_bet_opps", "four_bets",
        "fold_to_3bet_opps", "fold_to_3bet_count", "fold_to_4bet_opps", "fold_to_4bet_count",
    ]
    if "call_3bet_count" in df.columns:
        sum_cols.append("call_3bet_count")
    agg = df.groupby("dataset", as_index=False)[sum_cols].sum()
    agg["three_bet_pct"] = _pct(agg["three_bets"], agg["three_bet_opps"])
    agg["four_bet_pct"] = _pct(agg["four_bets"], agg["four_bet_opps"])
    agg["fold_to_3bet_pct"] = _pct(agg["fold_to_3bet_count"], agg["fold_to_3bet_opps"])
    agg["fold_to_4bet_pct"] = _pct(agg["fold_to_4bet_count"], agg["fold_to_4bet_opps"])
    if "call_3bet_count" in agg.columns:
        agg["call_3bet_pct"] = _pct(agg["call_3bet_count"], agg["fold_to_3bet_opps"])
    return agg.sort_values("dataset")


def aggregate_postflop(parts: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat(parts, ignore_index=True)
    sum_cols = [c for c in df.columns if c != "dataset" and not c.endswith("_pct")]
    agg = df.groupby("dataset", as_index=False)[sum_cols].sum()
    pct_pairs = [
        ("cbet_pct", "cbets", "cbet_opps"),
        ("double_barrel_pct", "double_barrels", "flop_bettors"),
        ("fold_to_cbet_pct", "fold_to_cbet_count", "fold_to_cbet_opps"),
        ("check_raise_flop_pct", "check_raise_flop_count", "check_raise_flop_opps"),
        ("check_raise_turn_pct", "check_raise_turn_count", "check_raise_turn_opps"),
        ("check_raise_river_pct", "check_raise_river_count", "check_raise_river_opps"),
    ]
    for pct, num, den in pct_pairs:
        if num in agg.columns and den in agg.columns:
            agg[pct] = _pct(agg[num], agg[den])
    return agg.sort_values("dataset")


def compute_bb100_table(root: Path, year_parts: list[pd.DataFrame]) -> pd.DataFrame:
    year_df = pd.concat(year_parts, ignore_index=True) if year_parts else pd.DataFrame()
    overall_rows = []
    if not year_df.empty:
        for ds, g in year_df.groupby("dataset"):
            player_hands = int(g["player_hands"].sum())
            hands = int(g["hands"].sum())
            bb_sum = (g["bb100"].astype(float) * g["player_hands"].astype(float) / 100.0).sum()
            overall_rows.append({
                "granularity": "overall",
                "dataset": ds,
                "year": pd.NA,
                "hands": hands,
                "player_hands": player_hands,
                "bb100": round(100.0 * bb_sum / player_hands, 2) if player_hands else pd.NA,
                "rake_bb100_equiv": pd.NA,
                "pct_hands_raked": pd.NA,
                "avg_rake_bb_when_raked": pd.NA,
            })
    overall_df = pd.DataFrame(overall_rows)

    hands_g = parquet_glob(root, "hands")
    con = duckdb.connect()
    rake_df = con.execute(
        f"""
        SELECT dataset,
               ROUND(-100.0 * SUM(COALESCE(rake, 0) / bb) / (COUNT(*) * 6.0), 2) AS rake_bb100_equiv,
               ROUND(100.0 * SUM(CASE WHEN COALESCE(rake, 0) > 0 THEN 1 ELSE 0 END) / COUNT(*), 1)
                   AS pct_hands_raked,
               ROUND(AVG(CASE WHEN COALESCE(rake, 0) > 0 THEN rake / bb END), 3) AS avg_rake_bb_when_raked
        FROM read_parquet('{hands_g}')
        WHERE is_primary
        GROUP BY dataset
        """
    ).fetchdf()
    con.close()
    if not overall_df.empty and not rake_df.empty:
        overall_df = overall_df.drop(
            columns=["rake_bb100_equiv", "pct_hands_raked", "avg_rake_bb_when_raked"]
        )
        overall_df = overall_df.merge(rake_df, on="dataset", how="left")

    if year_df.empty:
        return overall_df
    return pd.concat([overall_df, year_df], ignore_index=True)


def write_outputs(con: duckdb.DuckDBPyConnection, out_dir: Path, write_json: bool) -> dict[str, Path]:
    """Run each query on one DuckDB connection (single dataset or full corpus)."""
    outputs: dict[str, Path] = {}
    queries = {
        "metrics_overall.csv": SQL_OVERALL,
        "metrics_by_position.csv": SQL_BY_POSITION,
        "metrics_by_year.csv": SQL_BY_YEAR,
        "metrics_preflop.csv": SQL_PREFLOP,
        "metrics_postflop.csv": SQL_POSTFLOP,
    }

    for filename, sql in queries.items():
        df = con.execute(sql).df()
        if filename == "metrics_by_position.csv":
            df = sort_by_position(df)
        path = out_dir / filename
        df.to_csv(path, index=False)
        outputs[filename] = path
        if write_json:
            json_path = path.with_suffix(".json")
            df.to_json(json_path, orient="records", indent=2)
            outputs[json_path.name] = json_path

    bb100_overall = con.execute(SQL_BB100_OVERALL).df()
    bb100_year = con.execute(SQL_BB100_BY_YEAR).df()
    bb100 = pd.concat([bb100_overall, bb100_year], ignore_index=True)
    bb100_path = out_dir / "metrics_bb100.csv"
    bb100.to_csv(bb100_path, index=False)
    outputs["metrics_bb100.csv"] = bb100_path
    if write_json:
        bb100_json = bb100_path.with_suffix(".json")
        bb100.to_json(bb100_json, orient="records", indent=2)
        outputs[bb100_json.name] = bb100_json

    return outputs


def _run_queries(
    root: Path,
    hands_g: str,
    players_g: str,
    actions_g: str,
    queries: dict[str, str],
) -> dict[str, pd.DataFrame]:
    """One fresh DuckDB connection per query; clears temp spill files afterward."""
    out: dict[str, pd.DataFrame] = {}
    for name, sql in queries.items():
        con = duckdb.connect()
        configure_duckdb(con, root)
        setup_views(con, hands_g, players_g, actions_g)
        out[name] = con.execute(sql).df()
        con.close()
        clear_duckdb_temp()
    return out


def _run_slice_queries(
    root: Path,
    hands_g: str,
    players_g: str,
    actions_g: str,
    queries: dict[str, str],
) -> dict[str, pd.DataFrame]:
    """Prefer one connection per slice; fall back to per-query on resource errors."""
    try:
        con = duckdb.connect()
        configure_duckdb(con, root)
        setup_views(con, hands_g, players_g, actions_g)
        out = {name: con.execute(sql).df() for name, sql in queries.items()}
        con.close()
        clear_duckdb_temp()
        return out
    except duckdb.Error:
        return _run_queries(root, hands_g, players_g, actions_g, queries)


def checkpoint_dir(root: Path) -> Path:
    return CHECKPOINT_ROOT / root.name


def slice_checkpoint_path(ckpt: Path, dataset: str, year: str) -> Path:
    return ckpt / f"dataset={dataset}_year={year}.pkl"


def load_slice_checkpoint(ckpt: Path, dataset: str, year: str) -> dict[str, pd.DataFrame] | None:
    path = slice_checkpoint_path(ckpt, dataset, year)
    if not path.exists():
        return None
    with path.open("rb") as f:
        return pickle.load(f)


def save_slice_checkpoint(
    ckpt: Path, dataset: str, year: str, data: dict[str, pd.DataFrame]
) -> None:
    ckpt.mkdir(parents=True, exist_ok=True)
    with slice_checkpoint_path(ckpt, dataset, year).open("wb") as f:
        pickle.dump(data, f)


def write_outputs_chunked(
    root: Path,
    out_dir: Path,
    write_json: bool,
    *,
    resume: bool = True,
) -> dict[str, Path]:
    """Process dataset/year slices separately to stay within memory and disk limits."""
    slice_queries = {
        "metrics_overall.csv": SQL_OVERALL,
        "metrics_by_position.csv": SQL_BY_POSITION,
        "metrics_by_year.csv": SQL_BY_YEAR,
        "metrics_preflop.csv": SQL_PREFLOP,
        "metrics_postflop.csv": SQL_POSTFLOP,
        "metrics_bb100_by_year": SQL_BB100_BY_YEAR,
    }
    parts: dict[str, list[pd.DataFrame]] = {k: [] for k in slice_queries}
    ckpt = checkpoint_dir(root)
    skipped = 0

    for ds in DATASETS:
        years = list_dataset_years(root, ds)
        if not years:
            print(f"  Skip {ds} (no partitions)")
            continue
        for year in years:
            if resume:
                cached = load_slice_checkpoint(ckpt, ds, year)
                if cached is not None:
                    print(f"  {ds}/{year} (cached)", flush=True)
                    for name, df in cached.items():
                        parts[name].append(df)
                    skipped += 1
                    continue
            print(f"  {ds}/{year}...", flush=True)
            hands_y = parquet_glob_slice(root, "hands", ds, year)
            players_y = parquet_glob_slice(root, "players", ds, year)
            actions_y = parquet_glob_slice(root, "actions", ds, year)
            chunk = _run_slice_queries(root, hands_y, players_y, actions_y, slice_queries)
            save_slice_checkpoint(ckpt, ds, year, chunk)
            for name, df in chunk.items():
                parts[name].append(df)

    if skipped:
        print(f"\n  Resumed {skipped} cached slice(s) from {ckpt}")

    combined = {
        "metrics_overall.csv": aggregate_overall(parts["metrics_overall.csv"]),
        "metrics_by_position.csv": sort_by_position(aggregate_by_position(parts["metrics_by_position.csv"])),
        "metrics_by_year.csv": pd.concat(parts["metrics_by_year.csv"], ignore_index=True),
        "metrics_preflop.csv": aggregate_preflop(parts["metrics_preflop.csv"]),
        "metrics_postflop.csv": aggregate_postflop(parts["metrics_postflop.csv"]),
    }
    bb100 = compute_bb100_table(root, parts["metrics_bb100_by_year"])

    outputs: dict[str, Path] = {}
    for filename, df in combined.items():
        path = out_dir / filename
        df.to_csv(path, index=False)
        outputs[filename] = path
        if write_json and not df.empty:
            json_path = path.with_suffix(".json")
            df.to_json(json_path, orient="records", indent=2)
            outputs[json_path.name] = json_path

    bb100_path = out_dir / "metrics_bb100.csv"
    bb100.to_csv(bb100_path, index=False)
    outputs["metrics_bb100.csv"] = bb100_path
    if write_json and not bb100.empty:
        bb100_json = bb100_path.with_suffix(".json")
        bb100.to_json(bb100_json, orient="records", indent=2)
        outputs[bb100_json.name] = bb100_json

    return outputs


def collect_metadata(con: duckdb.DuckDBPyConnection, root: Path, outputs: dict[str, Path]) -> dict:
    counts = con.execute(
        """
        SELECT dataset, year, COUNT(*) AS primary_hands
        FROM primary_hands
        GROUP BY dataset, year
        ORDER BY dataset, year
        """
    ).fetchdf()

    total_primary = int(con.execute("SELECT COUNT(*) FROM primary_hands").fetchone()[0])
    total_included = int(
        con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{parquet_glob(root, 'hands')}') WHERE is_included"
        ).fetchone()[0]
    )

    by_dataset = (
        counts.groupby("dataset")["primary_hands"].sum().astype(int).to_dict()
        if not counts.empty
        else {}
    )

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "path": str(root.resolve()),
        "corpus": "is_primary",
        "total_primary_hands": total_primary,
        "total_included_hands": total_included,
        "primary_hands_by_dataset": by_dataset,
        "primary_hands_by_dataset_year": counts.to_dict(orient="records"),
        "output_files": {k: str(v.resolve()) for k, v in sorted(outputs.items())},
    }


def collect_metadata_light(root: Path, outputs: dict[str, Path], n_primary: int) -> dict:
    """Hand counts from Parquet only — no heavy metric views."""
    hands_g = parquet_glob(root, "hands")
    con = duckdb.connect()
    counts = con.execute(
        f"""
        SELECT dataset, year, COUNT(*) AS primary_hands
        FROM read_parquet('{hands_g}')
        WHERE is_primary
        GROUP BY dataset, year
        ORDER BY dataset, year
        """
    ).fetchdf()
    total_included = int(
        con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{hands_g}') WHERE is_included"
        ).fetchone()[0]
    )
    con.close()
    by_dataset = (
        counts.groupby("dataset")["primary_hands"].sum().astype(int).to_dict()
        if not counts.empty
        else {}
    )
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "path": str(root.resolve()),
        "corpus": "is_primary",
        "total_primary_hands": n_primary,
        "total_included_hands": total_included,
        "primary_hands_by_dataset": by_dataset,
        "primary_hands_by_dataset_year": counts.to_dict(orient="records"),
        "output_files": {k: str(v.resolve()) for k, v in sorted(outputs.items())},
    }


def print_preview_from_csv(out_dir: Path) -> None:
    overall_path = out_dir / "metrics_overall.csv"
    if overall_path.exists():
        df = pd.read_csv(overall_path)
        print("=== Overall (VPIP / PFR) ===")
        cols = [c for c in df.columns if c in (
            "dataset", "hands", "player_hands", "vpip_count", "vpip_pct",
            "pfr_count", "pfr_pct", "gap_pp",
        )]
        print_table([tuple(row) for row in df[cols].itertuples(index=False, name=None)], cols)

    preflop_path = out_dir / "metrics_preflop.csv"
    if preflop_path.exists():
        df = pd.read_csv(preflop_path)
        print("\n=== Preflop (3-bet / fold to 3-bet) ===")
        cols = [c for c in df.columns if c in (
            "dataset", "three_bet_opps", "three_bets", "three_bet_pct",
            "four_bet_opps", "four_bets", "four_bet_pct",
            "fold_to_3bet_opps", "fold_to_3bet_count", "fold_to_3bet_pct",
            "fold_to_4bet_opps", "fold_to_4bet_count", "fold_to_4bet_pct",
        )]
        if cols:
            print_table([tuple(row) for row in df[cols].itertuples(index=False, name=None)], cols)

    bb100_path = out_dir / "metrics_bb100.csv"
    if bb100_path.exists():
        df = pd.read_csv(bb100_path)
        overall = df[df["year"].isna()] if "year" in df.columns else df
        print("\n=== bb/100 (overall) ===")
        for _, row in overall.iterrows():
            print(f"  {row['dataset']}: {int(row['hands']):,} hands, bb/100={row['bb100']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute all key metrics on parsed Parquet and export to reports/"
    )
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Parsed Parquet root")
    parser.add_argument(
        "--output-dir", type=Path, default=REPORTS_DIR, help="Directory for CSV/JSON outputs"
    )
    parser.add_argument("--json", action="store_true", help="Also write JSON alongside each CSV")
    parser.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Delete cached year slices before running",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Recompute all year slices even if checkpoint exists",
    )
    args = parser.parse_args()
    root: Path = args.path
    out_dir: Path = args.output_dir

    if not (root / "hands").exists():
        print(f"No parsed data at {root}. Run parse_corpus.py or parse_sample.py first.")
        sys.exit(1)

    hands_g = parquet_glob(root, "hands")
    n_primary_quick = duckdb.connect().execute(
        f"SELECT COUNT(*) FROM read_parquet('{hands_g}') WHERE is_primary"
    ).fetchone()[0]

    use_chunked = n_primary_quick > 5_000_000
    print(f"=== Compute all metrics (is_primary, n={n_primary_quick:,} hands) ===")
    print(f"Path: {root.resolve()}")
    if use_chunked:
        print("Mode: per-stake/year queries (large corpus)\n")
    else:
        print("Mode: single pass\n")

    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clear_checkpoint:
        ckpt = checkpoint_dir(root)
        if ckpt.exists():
            import shutil
            shutil.rmtree(ckpt)
            print(f"Cleared checkpoint: {ckpt}\n")

    if use_chunked:
        outputs = write_outputs_chunked(
            root, out_dir, args.json, resume=not args.no_resume
        )
        n_primary = n_primary_quick
        summary = collect_metadata_light(root, outputs, n_primary)
        summary_path = out_dir / "metrics_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        outputs["metrics_summary.json"] = summary_path
        print_preview_from_csv(out_dir)
    else:
        con = duckdb.connect()
        configure_duckdb(con, root)
        players_g = parquet_glob(root, "players")
        actions_g = parquet_glob(root, "actions")
        setup_views(con, hands_g, players_g, actions_g)
        n_primary = con.execute("SELECT COUNT(*) FROM primary_hands").fetchone()[0]
        outputs = write_outputs(con, out_dir, args.json)
        summary = collect_metadata(con, root, outputs)
        summary_path = out_dir / "metrics_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        outputs["metrics_summary.json"] = summary_path

        overall = con.execute(SQL_OVERALL).fetchall()
        print("=== Overall (VPIP / PFR) ===")
        print_table(
            overall,
            ["dataset", "hands", "player_hands", "vpip_count", "vpip_pct", "pfr_count", "pfr_pct", "gap_pp"],
        )

        preflop = con.execute(SQL_PREFLOP).fetchall()
        print("\n=== Preflop (3-bet / fold to 3-bet) ===")
        print_table(
            preflop,
            ["dataset", "3b_opps", "3b", "3b_pct", "4b_opps", "4b", "4b_pct",
             "f3_opps", "f3", "f3_pct", "call3", "f4_opps", "f4", "f4_pct"],
        )

        postflop_rows = con.execute(
            """
            WITH flop_pfa AS (
                SELECT h.hand_id, h.dataset, p.pfa
                FROM primary_hands h JOIN pfa p USING (hand_id)
                WHERE h.board_flop IS NOT NULL AND h.num_players_flop >= 2
            ),
            flop_bet AS (
                SELECT DISTINCT a.hand_id FROM primary_actions a
                JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
                WHERE a.street = 'flop' AND a.action_type = 'bet'
            )
            SELECT f.dataset,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE b.hand_id IS NOT NULL) / COUNT(*), 2) AS cbet_pct
            FROM flop_pfa f LEFT JOIN flop_bet b USING (hand_id)
            GROUP BY f.dataset ORDER BY f.dataset
            """
        ).fetchall()
        print("\n=== Postflop (flop c-bet %) ===")
        print_table(postflop_rows, ["dataset", "cbet_pct"])

        bb100_overall = con.execute(SQL_BB100_OVERALL).fetchall()
        print("\n=== bb/100 (overall) ===")
        for row in bb100_overall:
            ds, hands, ph, bb100_val = row[1], row[3], row[4], row[5]
            print(f"  {ds}: {hands:,} hands, bb/100={bb100_val}")

    print("\n=== Outputs ===")
    for name in sorted(outputs):
        print(f"  {outputs[name]}")
    print(f"\nDone. {n_primary:,} primary hands processed.")


if __name__ == "__main__":
    main()
