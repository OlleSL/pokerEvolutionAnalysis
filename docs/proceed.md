# How to Proceed — Audit Summary & Next Steps

**Last updated:** 2026-08-17  
**Status:** Sample parser validated (2026-08-17); full corpus parse not yet started

This document synthesizes the full corpus audit and recommends how to move from raw hand histories to analysis. It complements [`scope.md`](../scope.md), [`outline.md`](../outline.md), [`metric_definitions.md`](metric_definitions.md), and [`parquet_schema.md`](parquet_schema.md).

---

## 1. What we have

| Asset | Detail |
|-------|--------|
| **Datasets** | NL50, NL200, NL5K — PokerStars 6-max NL cash hand histories |
| **Files** | 281,014 `.txt` files (~56 GB extracted) |
| **Year range** | 2010–2025 on disk; **study window 2011–2025** |
| **Scripts** | Audit: `count_hands.py`, `count_players_dealt.py`, … — Parser: `src/poker_parser/`, `parse_sample.py`, `validate_sample_parse.py`, `inspect_sample_parse.py` |
| **Reports** | `reports/hand_counts.json`, `reports/players_dealt_counts.json`, … |
| **Sample parse** | `data/parsed/sample/` — NL50/2018, NL200/2018, NL5K/2014 (~3.6M hands parsed, validated) |
| **Human review** | `data/parsed/sample/review/hands.csv`, `hand_<id>.txt` |

---

## 2. Inclusion filters (locked)

Applied at **parse time**; raw files are never deleted.

| Filter | Rule |
|--------|------|
| Study window | 2011–2025 |
| Currency | USD only (exclude EUR, GBP) |
| Game type | Non-cap only (exclude `$X Cap` tables) |
| Ante games | Excluded (any hand with ante posts) |
| Table size | 6-max tables only |
| Primary analysis | **`num_players_dealt = 6`** — full ring; shorthanded stored but excluded from main positional/matchup stats |

Positions for 6-dealt hands: **LJ, HJ, CO, BTN, SB, BB** (drop from left when fewer than 6 dealt).

---

## 3. Audit numbers

### 3.1 Full corpus scan (`hand_counts.json`)

| | Hands |
|--|-------|
| Total scanned | 50,813,434 |
| USD | 49,797,384 |
| Non-USD (excluded) | 1,016,050 |
| Cap (excluded) | 6,848,189 |
| **Included** (USD, non-cap, 2011–2025) | **41,675,517** |

| Dataset | Included hands |
|---------|----------------|
| NL50 | 19,852,160 |
| NL200 | 17,761,448 |
| NL5K | 3,843,640 |

### 3.2 Players dealt (`players_dealt_counts.json`)

| | Hands | % of parent |
|--|-------|-------------|
| All hands (any filter) | 50,571,121 | — |
| Exactly 6 dealt | 26,882,327 | 53.2% of all |
| Included | 41,457,248 | — |
| **Included + 6 dealt** | **23,051,665** | **55.6% of included** |

| Dataset | Included | Included + 6 dealt | % 6-dealt of included |
|---------|----------|---------------------|------------------------|
| NL50 | 19,852,160 | 11,456,668 | 57.7% |
| NL200 | 17,761,448 | 10,161,510 | 57.2% |
| NL5K | 3,843,640 | 1,433,487 | 37.3% |

**Primary analysis corpus (pre-ante audit): ~23.1M hands** (included + exactly 6 players dealt, **before ante exclusion**).

> **2026-08-17 parser update:** Ante games are now excluded at parse time. Full-corpus primary count will drop — see §3.7 for sample-validated impact. Pre-ante audit totals in §3.1–3.2 remain useful for raw volume; use `is_included` / `is_primary` from parsed Parquet for analysis denominators.

> Small discrepancy (~218k hands) between audit scripts likely comes from hand-boundary or year-assignment differences. Reconcile during full parse QA.

### 3.3 Dealt-count distribution (included hands)

Roughly **half** of included hands are not full 6-dealt rings:

| Players dealt | Included hands (approx.) |
|---------------|--------------------------|
| 5 | ~13.1M |
| 4 | ~4.7M |
| 3 | ~2.7M |
| 2 | ~2.2M |
| 6 | **23.1M** |

This is expected: 6-max tables frequently start shorthanded. The 6-dealt filter is intentional for clean positional analysis.

### 3.4 Included + 6-dealt by year (primary corpus)

| Year | NL50 | NL200 | NL5K | **Total** |
|------|------|-------|------|-----------|
| 2011 | 737k | 602k | 130k | 1.47M |
| 2012 | 445k | 519k | 140k | 1.10M |
| 2013 | 671k | 435k | 281k | 1.39M |
| 2014 | 558k | 505k | 248k | 1.31M |
| 2015 | 496k | 658k | 149k | 1.30M |
| 2016 | 778k | 677k | 101k | 1.56M |
| 2017 | 688k | 629k | 107k | 1.42M |
| 2018 | 681k | 623k | 84k | 1.39M |
| 2019 | 692k | 528k | 69k | 1.29M |
| 2020 | 735k | 726k | 70k | 1.53M |
| 2021 | 840k | 788k | 16k | 1.64M |
| 2022 | 874k | 751k | **5k** | 1.63M |
| 2023 | 845k | 740k | **10k** | 1.59M |
| 2024 | 1.20M | 944k | **10k** | 2.15M |
| 2025 | 1.22M | 1.04M | **15k** | 2.27M |

### 3.5 Heatmaps

| File | Shows |
|------|-------|
| `reports/coverage_heatmap_total.png` | All hands by dataset × year |
| `reports/coverage_heatmap_included.png` | Included hands (USD, non-cap, 2011–2025) |
| `reports/coverage_heatmap_included_6_dealt.png` | Primary analysis corpus (included + 6 dealt) |

Regenerate: `python scripts/plot_coverage_heatmap.py`

### 3.6 Overnight audits (completed 2026-08-11)

| Audit | Key result |
|-------|------------|
| **Unique players** (`player_counts.json`) | 272,663 unique names in included hands; ~801 avg hands/player corpus-wide |
| **Stake strings** (`stake_strings.json`) | 2,747 unique strings; 2,733 malformed headers flagged; cap/EUR/GBP counts match inclusion audit |
| **Duplicate hands** (`duplicate_hands.json`) | 423,002 duplicate Hand # IDs (~424k extra occurrences); **0 cross-dataset**; NL5K accounts for 407k |
| **Monthly counts** (`hand_counts_monthly.json`, `.png`) | 41,675,517 included hands by dataset × month |

Dedupe before full parse recommended for NL5K (within-file and within-dataset duplicates dominate).

### 3.7 Sample parse validation (2026-08-17)

Full re-parse of three slices with updated parser (`parse_sample.py --clear`):

| Slice | Parsed | Included | Primary (6-dealt) | Ante excluded | Skipped non-USD |
|-------|-------:|---------:|------------------:|--------------:|----------------:|
| NL50/2018 | 1,429,291 | 1,413,295 | **674,696** | 15,996 | 46,244 |
| NL200/2018 | 1,228,042 | 1,191,720 | **603,583** | 36,322 | 103,592 |
| NL5K/2014 | 906,714 | **310,115** | **149,483** | **301,913** | — |

**Parser quality:**
- Parse errors: **9,452** (all duplicate Hand # IDs — expected in NL200 re-exports)
- EUR/GBP: silently skipped (no longer logged as errors)
- VPIP on included sample: NL50 **29.5%**, NL200 **29.6%**, NL5K **31.9%**
- `net_won` computed from collected + returned − invested

**NL5K ante impact (2014 sample):** ante games were **~44% of USD hands** and **~40% of primary hands**. NL5K included dropped from ~612k → **310k**; primary from ~248k → **149k**.

**Estimated full-corpus primary after ante exclusion:** ~**22M hands** (down from ~23.1M pre-ante audit). NL5K primary drops from ~1.43M to roughly **~850k–900k** (estimate from 2014 ratio).

**Review parsed hands without opening Parquet:**
```powershell
python scripts/inspect_sample_parse.py --primary-only --limit 10
```
Open `data/parsed/sample/review/hands.csv` and `hand_<id>.txt` in Cursor.

---

## 4. Key findings

### 4.1 NL50 and NL200 are healthy

Both stakes have **~500k–1.2M included-6-dealt hands per year** across the full study window. Recent years (2024–2025) show a jump in 6-dealt share (~75–86% vs ~47–58% earlier), suggesting fuller tables or sampling bias in newer exports — worth noting in methods but not blocking.

### 4.2 NL5K is the constraint

Four compounding filters shrink NL5K dramatically:

1. **Cap exclusion** — ~51% of NL5K hands are cap games; removing them cuts included volume roughly in half.
2. **Ante exclusion** — ~40–44% of remaining USD NL5K hands (sample: 2014); much rarer at NL50/NL200 (~2–3%).
3. **6-dealt filter** — only ~37% of included NL5K hands are full rings (vs ~57% at lower stakes).
4. **Volume collapse post-2020** — included NL5K drops from ~70k/year (2019–2020) to **~5–15k/year** (2021–2025).

**NL5K primary (included + 6-dealt + non-ante) by era (approx., pre-full-parse):**

| Era | Approx. primary hands/year | Notes |
|-----|---------------------------|-------|
| 2011–2014 | ~80k–170k | Best NL5K coverage; 2014 sample = 149k primary |
| 2015–2020 | ~40k–90k | Usable but thinner after cap + ante filters |
| 2021–2025 | ~3k–10k | Too thin for year-level NL5K trends |

### 4.3 Implication for research questions

- **RQ1 (population evolution)** — Strong at NL50/NL200 for all years; NL5K reliable roughly **2011–2020**.
- **RQ2 (cross-stake comparison)** — Feasible 2011–2020; post-2020 NL5K comparisons are underpowered.
- **RQ3 (positional/matchup)** — Solid on 23M-hand primary corpus; NL5K positional stats need pooling across years post-2018.
- **RQ5 (survivorship)** — Unaffected by 6-dealt filter if computed on all included hands per player.
- **RQ6 (temporal events)** — COVID-era NL5K signal is essentially absent in primary corpus.

---

## 5. Locked vs open decisions

### Locked

| Decision | Choice |
|----------|--------|
| Study window | 2011–2025 |
| Currency | USD only |
| Cap games | Excluded |
| Ante games | Excluded |
| Table type | 6-max |
| Primary analysis filter | `num_players_dealt = 6` |
| Metric definitions | See `metric_definitions.md` |
| Storage format | Parquet → DuckDB (see `parquet_schema.md`) |
| Positions (6-dealt) | LJ, HJ, CO, BTN, SB, BB |

### Open — decide before full parse

| Question | Options | Recommendation |
|----------|---------|----------------|
| **NL5K time scope** | (A) Keep all years; (B) Primary analysis 2011–2020; (C) Drop NL5K from year-level trends post-2020 | **(B)** — report NL5K 2021+ as appendix only |
| **NL5K cap games** | Keep excluded (locked) or revisit | **Keep excluded** |
| **NL5K ante games** | Keep excluded (locked) | **Keep excluded** — ~44% of USD NL5K in 2014 sample |
| **Subsampling NL50/NL200** | Random sample to match NL5K volume per year | **Defer** — only needed if cross-stake year comparisons require balanced N; not needed for population trends within stake |
| **Shorthanded hands** | Exclude from primary stats (locked); optional secondary analysis? | Store `num_players_dealt`; run optional 5-dealt sensitivity later |
| **All-in EV** | Compute or skip initially | **Skip initially** — add in Phase 5 if RQ4 needs it |
| **Multiway c-bet denominators** | HU only vs all spots | Lock during VPIP validation (see metric_definitions.md) |
| **Hand-count reconciliation** | Audit vs parser gaps | Sample NL50/2018 primary matches audit exactly; NL5K primary −40% due to ante (expected) |

---

## 6. Recommended next steps (priority order)

### Phase A — Sample parser ✅ (complete 2026-08-17)

- [x] Build `src/poker_parser/` + `scripts/parse_sample.py`
- [x] Parse NL50/2018, NL200/2018, NL5K/2014
- [x] Exclude non-USD, cap, ante; flags `is_included`, `is_primary`, `is_ante`
- [x] VPIP sanity check (~29–32% on sample)
- [x] `net_won` implemented
- [ ] Optional: spot-check 10–20 hands in `review/` vs raw `.txt` (your call)
- [ ] Lock multiway c-bet denominator rule in `metric_definitions.md`

### Phase B — Metric validation (you are here)

**Goal:** Confirm one metric end-to-end before full parse.

1. **VPIP in DuckDB** — extend `validate_sample_parse.py` or write `scripts/metrics/vpip.sql`
   - Filter: `WHERE is_primary`
   - By dataset, year, position
   - Document edge cases (walks, dead blinds, ante-free confirmed)

2. **Add PFR + RFI** — same pattern once VPIP checks out

3. **Decide NL5K scope** — recommend primary cross-stake analysis **2011–2020**; 2021+ appendix only

### Phase C — Full parse

4. **Generalize to `parse_corpus.py`** — partition by `dataset` / `year`
   - Expect long runtime (hours to days); log progress per partition
   - Write `parse_errors.jsonl` for duplicate IDs only (non-USD skipped silently)

5. **Load into DuckDB**
   - Create views: `hands_included`, `hands_primary` (included + 6-dealt)
   - Index/join on `hand_id`

### Phase D — EDA & analysis prep

6. **Dataset overview (outline Phase 4)**
   - Regenerate heatmaps from parsed data (sanity check)
   - Player counts, hands per player distribution
   - Confirm NL5K year coverage matches audit

7. **Lock NL5K scope decision** (Section 5 above) and document in `scope.md`

8. **Core analysis (RQ1–RQ6)** per `outline.md` — start with RQ1 population trends at NL50/NL200

### Deferred

- Literature review (user deferred)
- All-in EV computation
- Player archetype clustering
- Full survivorship models

---

## 7. Practical notes

### Scripts reference

```powershell
# Inspect parsed sample (readable CSV/txt — not binary Parquet)
python scripts/parse_sample.py --clear          # re-parse sample slices
python scripts/validate_sample_parse.py         # counts + VPIP
python scripts/inspect_sample_parse.py --primary-only --limit 10

# After full corpus parse — export all metrics to reports/
python scripts/compute_all_metrics.py --path data/parsed
python scripts/compute_all_metrics.py --path data/parsed/sample --json   # test on sample

# Regenerate heatmaps (raw audit)
python scripts/plot_coverage_heatmap.py
```

### Environment

- Python 3.12, venv at `.venv/`
- Dependencies: `requirements.txt` (pandas, pyarrow, duckdb, matplotlib, jupyter, tqdm)

### What not to do yet

- Do **not** full-parse the 56 GB corpus before VPIP/PFR validation on sample
- Do **not** delete or move raw data
- Do **not** commit raw hand histories or parsed Parquet to git (should stay gitignored)

---

## 8. Summary recommendation

**Sample parser is validated.** Next: lock VPIP (and PFR) in DuckDB on the sample, then full parse.

**Corpus after all filters (estimate):** ~**22M primary hands** (USD, non-cap, non-ante, 6-dealt). NL50 and NL200 remain strong across 2011–2025. NL5K is viable for cross-stake work through ~2020 but is **much thinner** after cap + ante + 6-dealt filters (~149k primary in 2014 alone vs ~248k pre-ante). Treat NL5K 2021+ as appendix-only for year-level trends.

**Your immediate checklist:**
1. Open `data/parsed/sample/review/hands.csv` — scan flags and stakes
2. Open 2–3 `hand_<id>.txt` files — confirm actions/positions/`net_won` look right
3. Approve NL5K scope (2011–2020 primary) in `scope.md` if you agree
4. Ask to build VPIP-by-position SQL / notebook on sample Parquet
5. Then full parse (~days of compute)

---

## Related documents

| Document | Purpose |
|----------|---------|
| [`scope.md`](../scope.md) | Project scope, RQs, inclusion rules |
| [`outline.md`](../outline.md) | Full study outline + work tracker |
| [`metric_definitions.md`](metric_definitions.md) | Exact metric rules (VPIP, PFR, RFI, c-bet, …) |
| [`parquet_schema.md`](parquet_schema.md) | Parser output schema |
| [`reports/hand_counts.json`](../reports/hand_counts.json) | Full inclusion audit |
| [`reports/players_dealt_counts.json`](../reports/players_dealt_counts.json) | 6-dealt audit |
