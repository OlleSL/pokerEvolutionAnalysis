"""Export extended metrics at dataset × year grain for Power BI.

Adds / fixes what compute_all_metrics.py does not fully cover:
  - Preflop (3-bet/4-bet/fold/call) by year — with working call_3bet_pct
  - Postflop (c-bet, barrels, river c-bet, fold-to-cbet, XR) by year
  - Matchups: BTN open → BB, SB first-in (raise/limp/fold), BB defense
  - 3-bet sizing (average size in BB)
  - VPIP/PFR/RFI by position × year

Usage:
  python scripts/compute_extended_metrics.py --path data/parsed --json
  python scripts/compute_extended_metrics.py --path data/parsed --clear-checkpoint
"""

from __future__ import annotations

import argparse
import json
import pickle
import shutil
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
    parquet_glob_slice,
    setup_views,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed"
REPORTS_DIR = PROJECT_ROOT / "reports"
CHECKPOINT_ROOT = PROJECT_ROOT / ".tmp" / "extended_metrics_checkpoint"

SQL_PREFLOP_YEAR = """
WITH three_bet AS (
    SELECT pf.hand_id, pf.player_name, pf.action_type
    FROM player_first pf
    WHERE pf.raises_before = 1
      AND pf.player_name <> COALESCE((
          SELECT player_name FROM preflop_raises r
          WHERE r.hand_id = pf.hand_id AND r.raise_n = 1
      ), '')
),
four_bet AS (
    SELECT pf.hand_id, pf.player_name, pf.action_type
    FROM player_first pf
    WHERE pf.raises_before = 2
      AND pf.player_name <> COALESCE((
          SELECT player_name FROM preflop_raises r
          WHERE r.hand_id = pf.hand_id AND r.raise_n = 2
      ), '')
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
    SELECT o.hand_id, a.action_type AS response
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
    SELECT t.hand_id, a.action_type AS response
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
    SELECT h.dataset, h.year,
           COUNT(*) AS three_bet_opps,
           COUNT(*) FILTER (WHERE t.action_type = 'raise') AS three_bets
    FROM three_bet t
    JOIN primary_hands h ON h.hand_id = t.hand_id
    GROUP BY h.dataset, h.year
),
fb AS (
    SELECT h.dataset, h.year,
           COUNT(*) AS four_bet_opps,
           COUNT(*) FILTER (WHERE t.action_type = 'raise') AS four_bets
    FROM four_bet t
    JOIN primary_hands h ON h.hand_id = t.hand_id
    GROUP BY h.dataset, h.year
),
o3 AS (
    SELECT h.dataset, h.year,
           COUNT(*) AS fold_to_3bet_opps,
           COUNT(*) FILTER (WHERE response = 'fold') AS fold_to_3bet_count,
           COUNT(*) FILTER (WHERE response = 'call') AS call_3bet_count
    FROM opener_vs_3bet ov
    JOIN primary_hands h ON h.hand_id = ov.hand_id
    GROUP BY h.dataset, h.year
),
t4 AS (
    SELECT h.dataset, h.year,
           COUNT(*) AS fold_to_4bet_opps,
           COUNT(*) FILTER (WHERE response = 'fold') AS fold_to_4bet_count
    FROM three_vs_4bet tv
    JOIN primary_hands h ON h.hand_id = tv.hand_id
    GROUP BY h.dataset, h.year
)
SELECT
    tb.dataset,
    tb.year,
    tb.three_bet_opps,
    tb.three_bets,
    ROUND(100.0 * tb.three_bets / NULLIF(tb.three_bet_opps, 0), 2) AS three_bet_pct,
    fb.four_bet_opps,
    fb.four_bets,
    ROUND(100.0 * fb.four_bets / NULLIF(fb.four_bet_opps, 0), 2) AS four_bet_pct,
    o3.fold_to_3bet_opps,
    o3.fold_to_3bet_count,
    ROUND(100.0 * o3.fold_to_3bet_count / NULLIF(o3.fold_to_3bet_opps, 0), 2) AS fold_to_3bet_pct,
    o3.call_3bet_count,
    ROUND(100.0 * o3.call_3bet_count / NULLIF(o3.fold_to_3bet_opps, 0), 2) AS call_3bet_pct,
    t4.fold_to_4bet_opps,
    t4.fold_to_4bet_count,
    ROUND(100.0 * t4.fold_to_4bet_count / NULLIF(t4.fold_to_4bet_opps, 0), 2) AS fold_to_4bet_pct
FROM tb
LEFT JOIN fb USING (dataset, year)
LEFT JOIN o3 USING (dataset, year)
LEFT JOIN t4 USING (dataset, year)
ORDER BY tb.dataset, tb.year
"""

SQL_POSTFLOP_YEAR = """
WITH flop_pfa AS (
    SELECT h.hand_id, h.dataset, h.year, p.pfa
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
river_pfa AS (
    SELECT h.hand_id, h.dataset, h.year
    FROM primary_hands h
    JOIN pfa p USING (hand_id)
    WHERE h.board_river IS NOT NULL
),
flop_pfa_bet AS (
    SELECT a.hand_id, a.action_index AS bet_idx, p.pfa, h.dataset, h.year
    FROM primary_actions a
    JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
    JOIN primary_hands h ON h.hand_id = a.hand_id
    WHERE a.street = 'flop' AND a.action_type = 'bet'
),
fold_rows AS (
    SELECT fb.dataset, fb.year, a.action_type AS response
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
),
ftc AS (
    SELECT dataset, year,
           COUNT(*) AS fold_to_cbet_opps,
           COUNT(*) FILTER (WHERE response = 'fold') AS fold_to_cbet_count
    FROM fold_rows
    GROUP BY dataset, year
),
rc AS (
    SELECT rp.dataset, rp.year,
           COUNT(*) AS river_cbet_opps,
           COUNT(*) FILTER (WHERE rb.hand_id IS NOT NULL) AS river_cbets
    FROM river_pfa rp
    LEFT JOIN river_bet rb USING (hand_id)
    GROUP BY rp.dataset, rp.year
),
street_actions AS (
    SELECT a.hand_id, a.street, a.action_index, a.player_name, a.action_type, h.dataset, h.year
    FROM primary_actions a
    JOIN primary_hands h USING (hand_id)
    WHERE a.street = 'flop'
      AND a.action_type IN ('check', 'bet', 'raise', 'call', 'fold')
),
checks AS (
    SELECT hand_id, player_name, MIN(action_index) AS check_idx, dataset, year
    FROM street_actions WHERE action_type = 'check'
    GROUP BY hand_id, player_name, dataset, year
),
xr_opps AS (
    SELECT c.dataset, c.year, c.hand_id, c.player_name
    FROM checks c
    WHERE EXISTS (
        SELECT 1 FROM street_actions b
        WHERE b.hand_id = c.hand_id
          AND b.action_index > c.check_idx AND b.action_type = 'bet'
          AND b.player_name <> c.player_name
    )
),
xr_hits AS (
    SELECT DISTINCT o.dataset, o.year, o.hand_id, o.player_name
    FROM xr_opps o
    JOIN street_actions r ON r.hand_id = o.hand_id
        AND r.player_name = o.player_name AND r.action_type = 'raise'
),
xr AS (
    SELECT o.dataset, o.year,
           COUNT(*) AS check_raise_flop_opps,
           COUNT(*) FILTER (WHERE x.hand_id IS NOT NULL) AS check_raise_flop_count
    FROM xr_opps o
    LEFT JOIN xr_hits x USING (dataset, year, hand_id, player_name)
    GROUP BY o.dataset, o.year
),
core AS (
    SELECT
        f.dataset,
        f.year,
        COUNT(*) AS cbet_opps,
        COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL) AS cbets,
        COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL) AS flop_bettors,
        COUNT(*) FILTER (WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL) AS double_barrels,
        COUNT(*) FILTER (
            WHERE fb.hand_id IS NOT NULL AND tb.hand_id IS NOT NULL AND rb.hand_id IS NOT NULL
        ) AS triple_barrels
    FROM flop_pfa f
    LEFT JOIN flop_bet fb USING (hand_id)
    LEFT JOIN turn_bet tb USING (hand_id)
    LEFT JOIN river_bet rb USING (hand_id)
    GROUP BY f.dataset, f.year
)
SELECT
    c.dataset,
    c.year,
    c.cbet_opps,
    c.cbets,
    ROUND(100.0 * c.cbets / NULLIF(c.cbet_opps, 0), 2) AS cbet_pct,
    c.flop_bettors,
    c.double_barrels,
    ROUND(100.0 * c.double_barrels / NULLIF(c.flop_bettors, 0), 2) AS double_barrel_pct,
    c.triple_barrels,
    ROUND(100.0 * c.triple_barrels / NULLIF(c.double_barrels, 0), 2) AS triple_barrel_pct,
    rc.river_cbet_opps,
    rc.river_cbets,
    ROUND(100.0 * rc.river_cbets / NULLIF(rc.river_cbet_opps, 0), 2) AS river_cbet_pct,
    ftc.fold_to_cbet_opps,
    ftc.fold_to_cbet_count,
    ROUND(100.0 * ftc.fold_to_cbet_count / NULLIF(ftc.fold_to_cbet_opps, 0), 2) AS fold_to_cbet_pct,
    xr.check_raise_flop_opps,
    xr.check_raise_flop_count,
    ROUND(100.0 * xr.check_raise_flop_count / NULLIF(xr.check_raise_flop_opps, 0), 2) AS check_raise_flop_pct
FROM core c
LEFT JOIN rc USING (dataset, year)
LEFT JOIN ftc USING (dataset, year)
LEFT JOIN xr USING (dataset, year)
ORDER BY c.dataset, c.year
"""

SQL_MATCHUPS_YEAR = """
WITH btn_opens AS (
    SELECT r.hand_id, r.action_index AS open_idx, h.dataset, h.year
    FROM preflop_raises r
    JOIN primary_players pp ON pp.hand_id = r.hand_id AND pp.player_name = r.player_name
    JOIN primary_hands h ON h.hand_id = r.hand_id
    WHERE r.raise_n = 1 AND pp.position = 'BTN'
),
bb_vs_btn AS (
    SELECT o.dataset, o.year, a.action_type AS bb_response
    FROM btn_opens o
    JOIN primary_players bb ON bb.hand_id = o.hand_id AND bb.position = 'BB'
    JOIN preflop a ON a.hand_id = o.hand_id AND a.player_name = bb.player_name
        AND a.action_index > o.open_idx
        AND a.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        AND a.action_index = (
            SELECT MIN(a2.action_index) FROM preflop a2
            WHERE a2.hand_id = o.hand_id AND a2.player_name = bb.player_name
              AND a2.action_index > o.open_idx
              AND a2.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        )
),
sb_first AS (
    SELECT h.dataset, h.year,
           CASE
               WHEN pf.action_type = 'raise' AND pf.amount > pf.bb THEN 'raise'
               WHEN pf.action_type = 'call' THEN 'limp'
               WHEN pf.action_type = 'fold' THEN 'fold'
               ELSE 'other'
           END AS sb_action
    FROM player_first pf
    JOIN primary_players pp ON pp.hand_id = pf.hand_id AND pp.player_name = pf.player_name
    JOIN primary_hands h ON h.hand_id = pf.hand_id
    WHERE pp.position = 'SB' AND pf.is_folded_to
),
any_opens AS (
    SELECT r.hand_id, r.action_index AS open_idx, h.dataset, h.year
    FROM preflop_raises r
    JOIN primary_players pp ON pp.hand_id = r.hand_id AND pp.player_name = r.player_name
    JOIN primary_hands h ON h.hand_id = r.hand_id
    WHERE r.raise_n = 1 AND pp.position IN ('LJ', 'HJ', 'CO', 'BTN', 'SB')
),
bb_defense AS (
    SELECT o.dataset, o.year, a.action_type AS bb_response
    FROM any_opens o
    JOIN primary_players bb ON bb.hand_id = o.hand_id AND bb.position = 'BB'
    JOIN preflop a ON a.hand_id = o.hand_id AND a.player_name = bb.player_name
        AND a.action_index > o.open_idx
        AND a.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        AND a.action_index = (
            SELECT MIN(a2.action_index) FROM preflop a2
            WHERE a2.hand_id = o.hand_id AND a2.player_name = bb.player_name
              AND a2.action_index > o.open_idx
              AND a2.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        )
),
btn_bb AS (
    SELECT dataset, year,
           COUNT(*) AS btn_bb_opps,
           COUNT(*) FILTER (WHERE bb_response = 'fold') AS btn_bb_fold,
           COUNT(*) FILTER (WHERE bb_response = 'call') AS btn_bb_call,
           COUNT(*) FILTER (WHERE bb_response = 'raise') AS btn_bb_three_bet,
           ROUND(100.0 * COUNT(*) FILTER (WHERE bb_response = 'fold') / COUNT(*), 2) AS btn_bb_fold_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE bb_response = 'call') / COUNT(*), 2) AS btn_bb_call_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE bb_response = 'raise') / COUNT(*), 2) AS btn_bb_three_bet_pct
    FROM bb_vs_btn
    GROUP BY dataset, year
),
sb_fi AS (
    SELECT dataset, year,
           COUNT(*) AS sb_first_in_opps,
           COUNT(*) FILTER (WHERE sb_action = 'raise') AS sb_raise,
           COUNT(*) FILTER (WHERE sb_action = 'limp') AS sb_limp,
           COUNT(*) FILTER (WHERE sb_action = 'fold') AS sb_fold,
           ROUND(100.0 * COUNT(*) FILTER (WHERE sb_action = 'raise') / COUNT(*), 2) AS sb_raise_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE sb_action = 'limp') / COUNT(*), 2) AS sb_limp_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE sb_action = 'fold') / COUNT(*), 2) AS sb_fold_pct
    FROM sb_first
    GROUP BY dataset, year
),
bb_def AS (
    SELECT dataset, year,
           COUNT(*) AS bb_defense_opps,
           COUNT(*) FILTER (WHERE bb_response = 'fold') AS bb_fold,
           COUNT(*) FILTER (WHERE bb_response = 'call') AS bb_call,
           COUNT(*) FILTER (WHERE bb_response = 'raise') AS bb_three_bet,
           ROUND(100.0 * COUNT(*) FILTER (WHERE bb_response = 'fold') / COUNT(*), 2) AS bb_fold_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE bb_response = 'call') / COUNT(*), 2) AS bb_call_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE bb_response = 'raise') / COUNT(*), 2) AS bb_three_bet_pct
    FROM bb_defense
    GROUP BY dataset, year
)
SELECT
    COALESCE(b.dataset, s.dataset, d.dataset) AS dataset,
    COALESCE(b.year, s.year, d.year) AS year,
    b.btn_bb_opps, b.btn_bb_fold, b.btn_bb_call, b.btn_bb_three_bet,
    b.btn_bb_fold_pct, b.btn_bb_call_pct, b.btn_bb_three_bet_pct,
    s.sb_first_in_opps, s.sb_raise, s.sb_limp, s.sb_fold,
    s.sb_raise_pct, s.sb_limp_pct, s.sb_fold_pct,
    d.bb_defense_opps, d.bb_fold, d.bb_call, d.bb_three_bet,
    d.bb_fold_pct, d.bb_call_pct, d.bb_three_bet_pct
FROM btn_bb b
FULL OUTER JOIN sb_fi s USING (dataset, year)
FULL OUTER JOIN bb_def d USING (dataset, year)
ORDER BY dataset, year
"""

SQL_THREEBET_SIZE_YEAR = """
WITH three_bet_actions AS (
    SELECT pf.amount / NULLIF(pf.bb, 0) AS size_bb, h.dataset, h.year
    FROM player_first pf
    JOIN primary_hands h ON h.hand_id = pf.hand_id
    WHERE pf.raises_before = 1
      AND pf.action_type = 'raise'
      AND pf.amount > pf.bb
      AND pf.player_name <> COALESCE((
          SELECT player_name FROM preflop_raises r
          WHERE r.hand_id = pf.hand_id AND r.raise_n = 1
      ), '')
)
SELECT
    dataset,
    year,
    COUNT(*) AS three_bet_count,
    ROUND(AVG(size_bb), 2) AS avg_3bet_size_bb,
    ROUND(MEDIAN(size_bb), 2) AS median_3bet_size_bb,
    ROUND(AVG(CASE WHEN size_bb < 8 THEN size_bb END), 2) AS avg_3bet_size_bb_excl_shove
FROM three_bet_actions
GROUP BY dataset, year
ORDER BY dataset, year
"""

SQL_POSITION_YEAR = """
WITH vpip AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type IN ('call', 'raise', 'bet')
),
pfr AS (
    SELECT DISTINCT hand_id, player_name FROM preflop WHERE action_type = 'raise'
),
rfi_opps AS (
    SELECT pp.hand_id, pp.player_name
    FROM player_first pf
    JOIN primary_players pp USING (hand_id, player_name)
    WHERE pf.raises_before = 0 AND pp.position IN ('LJ','HJ','CO','BTN','SB')
),
rfi_hits AS (
    SELECT pf.hand_id, pf.player_name
    FROM player_first pf
    WHERE pf.raises_before = 0 AND pf.action_type = 'raise' AND pf.amount > pf.bb
)
SELECT
    h.dataset,
    h.year,
    pp.position,
    COUNT(*) AS player_hands,
    COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) AS vpip_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip_pct,
    COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) AS pfr_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS pfr_pct,
    COUNT(*) FILTER (WHERE ro.hand_id IS NOT NULL) AS rfi_opps,
    COUNT(*) FILTER (WHERE rh.hand_id IS NOT NULL) AS rfi_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE rh.hand_id IS NOT NULL)
          / NULLIF(COUNT(*) FILTER (WHERE ro.hand_id IS NOT NULL), 0), 2) AS rfi_pct
FROM primary_hands h
JOIN primary_players pp ON pp.hand_id = h.hand_id
LEFT JOIN vpip v ON v.hand_id = pp.hand_id AND v.player_name = pp.player_name
LEFT JOIN pfr f ON f.hand_id = pp.hand_id AND f.player_name = pp.player_name
LEFT JOIN rfi_opps ro ON ro.hand_id = pp.hand_id AND ro.player_name = pp.player_name
LEFT JOIN rfi_hits rh ON rh.hand_id = pp.hand_id AND rh.player_name = pp.player_name
WHERE pp.position IS NOT NULL
GROUP BY h.dataset, h.year, pp.position
ORDER BY h.dataset, h.year, pp.position
"""


def checkpoint_dir(root: Path) -> Path:
    return CHECKPOINT_ROOT / root.name


def slice_ckpt(ckpt: Path, dataset: str, year: str) -> Path:
    return ckpt / f"dataset={dataset}_year={year}.pkl"


def _run_slice(
    root: Path, hands_g: str, players_g: str, actions_g: str, queries: dict[str, str]
) -> dict[str, pd.DataFrame]:
    try:
        con = duckdb.connect()
        configure_duckdb(con, root)
        setup_views(con, hands_g, players_g, actions_g)
        out = {name: con.execute(sql).df() for name, sql in queries.items()}
        con.close()
        clear_duckdb_temp()
        return out
    except duckdb.Error:
        out: dict[str, pd.DataFrame] = {}
        for name, sql in queries.items():
            con = duckdb.connect()
            configure_duckdb(con, root)
            setup_views(con, hands_g, players_g, actions_g)
            out[name] = con.execute(sql).df()
            con.close()
            clear_duckdb_temp()
        return out


def write_csv(df: pd.DataFrame, path: Path, write_json: bool) -> dict[str, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    outputs = {path.name: path}
    if write_json and not df.empty:
        j = path.with_suffix(".json")
        df.to_json(j, orient="records", indent=2)
        outputs[j.name] = j
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Extended metrics (dataset × year)")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--clear-checkpoint", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    root: Path = args.path
    out_dir: Path = args.output_dir

    if not (root / "hands").exists():
        print(f"No parsed data at {root}")
        sys.exit(1)

    ckpt = checkpoint_dir(root)
    if args.clear_checkpoint and ckpt.exists():
        shutil.rmtree(ckpt)
        print(f"Cleared checkpoint: {ckpt}\n")

    queries = {
        "metrics_preflop_by_year.csv": SQL_PREFLOP_YEAR,
        "metrics_postflop_by_year.csv": SQL_POSTFLOP_YEAR,
        "metrics_matchups_by_year.csv": SQL_MATCHUPS_YEAR,
        "metrics_3bet_sizing_by_year.csv": SQL_THREEBET_SIZE_YEAR,
        "metrics_by_position_year.csv": SQL_POSITION_YEAR,
    }
    parts: dict[str, list[pd.DataFrame]] = {k: [] for k in queries}
    resume = not args.no_resume
    skipped = 0

    print("=== Extended metrics (dataset × year) ===")
    print(f"Path: {root.resolve()}\n")

    for ds in DATASETS:
        years = list_dataset_years(root, ds)
        if not years:
            print(f"  Skip {ds}")
            continue
        for year in years:
            path = slice_ckpt(ckpt, ds, year)
            if resume and path.exists():
                with path.open("rb") as f:
                    chunk = pickle.load(f)
                print(f"  {ds}/{year} (cached)", flush=True)
                for name, df in chunk.items():
                    parts[name].append(df)
                skipped += 1
                continue
            print(f"  {ds}/{year}...", flush=True)
            chunk = _run_slice(
                root,
                parquet_glob_slice(root, "hands", ds, year),
                parquet_glob_slice(root, "players", ds, year),
                parquet_glob_slice(root, "actions", ds, year),
                queries,
            )
            ckpt.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                pickle.dump(chunk, f)
            for name, df in chunk.items():
                parts[name].append(df)

    if skipped:
        print(f"\n  Resumed {skipped} cached slice(s)")

    outputs: dict[str, Path] = {}
    pos_order = {p: i for i, p in enumerate(("LJ", "HJ", "CO", "BTN", "SB", "BB"))}
    for filename, frames in parts.items():
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if filename == "metrics_by_position_year.csv" and not df.empty:
            df = df.assign(_ord=df["position"].map(pos_order)).sort_values(
                ["dataset", "year", "_ord"]
            ).drop(columns=["_ord"])
        elif not df.empty:
            sort_cols = [c for c in ("dataset", "year") if c in df.columns]
            df = df.sort_values(sort_cols)
        outputs.update(write_csv(df, out_dir / filename, args.json))

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "path": str(root.resolve()),
        "output_files": {k: str(v.resolve()) for k, v in sorted(outputs.items())},
        "notes": [
            "All tables are dataset x year (position tables also include position).",
            "BTN-BB: BB first response facing BTN open raise.",
            "SB first-in: SB raise/limp/fold when folded to.",
            "BB defense: BB fold/call/3-bet vs any open (LJ-SB).",
            "River c-bet: PFA bets river when hand reaches river.",
            "Triple barrel: PFA bets flop+turn+river (stricter chain).",
        ],
    }
    summary_path = out_dir / "metrics_extended_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    outputs["metrics_extended_summary.json"] = summary_path

    print("\n=== Outputs ===")
    for name in sorted(outputs):
        print(f"  {outputs[name]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
