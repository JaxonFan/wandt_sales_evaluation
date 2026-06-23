# W&T Sales Associate Scorecard — App Specification

> The W&T adaptation of the Coreline scorecard. Metric prototyped and validated in
> `wandt_metric_design.ipynb` (reusable `compute_wandt()`); attribution from
> `wandt_sales_associate_revenue.ipynb`. Reference implementation to mirror:
> `coreline_sales_associates_evaluation/app/` (`engine.py`, `service.py`,
> `models.py`, `main.py`, `load_history.py`) and `coreline.../app_spec.md`.
>
> Scope: the **5 W&T sales reps** — An Cao & Vanessa Wu (full-time), Garmi Mei,
> Ting Ting, Wendy Ye (part-time). Managers (Cindy Chan, Morgan Wu, Tina Ni),
> inactive (MT/PJ), and N/A are **excluded everywhere**. Source = W&T item-level
> invoice export with `Batch Number`.

---

## 0. What's different from Coreline (read this first)
W&T shares Coreline's *self-comparison* philosophy — every account judged against
its **own** baseline, size- and market-adjusted, with manager overrides — with
these changes:

| Topic | Coreline | W&T |
|---|---|---|
| Who is scored | 6 China-sales associates | the **5 sales reps** only; the roster `Role` column drives this (managers excluded) |
| Pay measure | profit$ with a margin floor | **profit$ (`Extended Price − Extended Cost`), no margin floor** (reps don't set margin) |
| YoY fairness | profit vs baseline (+ optional vs-market) | **headline = market-adjusted profit** (neutralizes the management-set, floor-wide margin regime) + **real (volume) growth shown beside it** |
| Limited stock | not modeled | **per-period manager-supplied constrained items**, removed from current **and** baseline |
| Acquisition | decision-support only | **in the incentive** — a continuous ramp bonus |
| Growth vs contribution | Defend 60% | **Grow-heavy** (Defend ~35%) |
| Data grain | order totals | **item-level** → volume-vs-price decomposition |
| Calendar | 28-day buckets assumed clean | **explicit day-of-week + holiday normalization** |

> **Data note:** the source docx says weekend sales are *higher*; the data shows the
> **opposite** (Sun/Sat ≈ 0.4%/2.8% of a week vs ~17–22% per weekday). The
> normalization is needed either way; confirm the wording.

---

## 1. What the system does (one paragraph)
Every 4 weeks, for each of the 5 sales reps, it computes one pay number — **profit
on the accounts they touched, vs each account's own calendar-normalized baseline,
divided by how the whole rep book moved (market drift)** — plus a forward-looking
**acquisition ramp bonus** and a **Grow-weighted** bonus split. It is fair by
construction: each account is judged against its own history; big accounts aren't
punished for natural reversion; the calendar (weekends/holidays) can't distort it;
a management-set, floor-wide margin change washes out in the market adjustment;
supply-constrained items are removed symmetrically; and the manager can flag
declining/closed accounts with one click.

---

## 2. Data flow
```
Item-level invoice export (daily/period)         Manager inputs (app)
  SOP type/number, item#, qty, unit/ext price,    • per-period constrained items (+ auto-detect)
  unit/ext cost, customer#, document date,        • account status (Normal/Exempt/Rebaseline)
  batch number                                    • closure confirmations / vacation log
        │                                                   │
        ▼                                                   │
  Attribute batch → roster; KEEP ONLY the 5 sales reps      │
  Account = Customer Number ; line_profit = ext price-cost  │
        ▼                                                   ▼
  Remove constrained items (symmetric: current + baseline windows)
        ▼
  Calendar-normalize (whole-week windows; selling-day-equivalents; holiday blocks)
        ▼
  Period engine  compute_wandt()  (every 4-week period)
   • baseline ladder: new / provisional (prior quarter) / mature (YoY), by history length
   • baseline = own profit, capacity-scaled, size-de-trended (relative-to-market)
   • market drift = median recent/baseline profit ; headline = profit ÷ market drift
   • credit each rep their own profit → work-share targets
   • new accounts → acquisition ramp ; ramping (新接手) → reward-only
   • apply manager flags (Exempt / Rebaseline / closure)
        ▼
  Scorecards (read)            Manager review screen (read/write)
```

---

## 3. The metric (engine logic — prototyped in `wandt_metric_design.ipynb`)
For a 4-week review date, window = **trailing 13 weeks**, whole-week aligned;
baseline window = the same 13 weeks **52 weeks (364 days)** earlier.

### 3.1 Attribution & scope
`Batch Number[:2]` → roster `Batch Initial`; free-text variants (`MORGANW`,
`TINAN`, `VANESSAW`) → `Other Names`. Account = `Customer Number`. **Filter to reps
whose `Role` ∈ {full time sales, part time sales} and `Status` = Active** — managers
/ inactive / N/A are dropped from the whole dataset (baselines, shares, market).

### 3.2 Pay measure = profit dollars
`line_profit = Extended Price − Extended Cost` (cost 100% populated). Within a day
margin is uniform across reps (verified: item-level margin std ≈ 0 → no rep edge,
fair), but it swings day-to-day (±~4.6pp), so profit — not raw revenue — is the
time-consistent measure. **No margin floor** (reps don't set margin).

### 3.3 Limited-stock exclusion (symmetric)
Manager supplies, **per period**, the supply-constrained items.
`exclude_constrained_items()` is applied to each window the engine builds → the
**same items drop from current and baseline**, so a "couldn't ship it" drop is never
charged to the rep. `detect_constrained_candidates()` proposes items (high revenue
share, volatile monthly quantity); the manager confirms. Auto-detect is support only.

### 3.4 Calendar normalization
- **Whole-week alignment** (baseline exactly 52 weeks back → identical weekday counts).
- **Selling-day-equivalents:** weekday weight `f_d` = its share of a normal week's
  revenue; window **capacity** = `Σ f_{dow(day)}`, holidays down-weighted; baseline
  scaled by `capacity_recent / capacity_baseline`.
- **Holiday pull-forward:** holiday weeks compared as a **±N-day block anchored to
  the holiday event** (pull-forward conserves the weekly total; the weekday drifts
  year to year — e.g. Christmas 2024 Wed → 2025 Thu).

### 3.5 Baseline ladder (by history length — NOT "no year-ago = new")
| Tier | Condition | Baseline | Status |
|------|-----------|----------|--------|
| **New** | first order < ~13 weeks ago | none → **acquisition** | `new` |
| **Provisional / ramping** | ≥13 wk history, no clean year-ago window | own **prior quarter** | `scored` (badge) |
| **Mature** | ≥52 wk history | **year-over-year** | `scored` |

### 3.6 Size de-trend & market drift (the YoY fairness mechanism)
- **Size de-trend:** scale each account's target by its baseline-size decile's
  typical move **relative to the overall market** (`decile median ÷ overall median`)
  — captures size reversion only, without double-counting the market move.
- **Market drift:** overall median recent/baseline profit.
- **Headline `profit_vs_market_pct` = (actual ÷ target) ÷ market_drift − 1.** A
  floor-wide margin change set by management hits every account → moves market drift
  → **cancels** (verified: 0pp change under a uniform 20% margin cut). Only beating
  /lagging the book counts.

### 3.7 Work-share & targets
`work_share = rep profit on account ÷ account's total profit` (sums to 1 → coverage
self-weights). `profit_target = work_share × baseline_profit × size_factor`.

### 3.8 Real (volume) growth — the inflation lens
The **PVM bridge** splits each account's change into `volume_dollars` (Δqty ×
baseline price) and `price_dollars` (qty × Δprice); they reconcile to the revenue
change. **`real_growth_pct`** (volume) is shown **beside** the headline so "bought
less but paid more (inflation)" cases are visible. Explicit price-elasticity
modeling is a **documented future enhancement** — for now handled by the market
adjustment (common demand softening cancels) + this volume diagnostic + manager
judgment.

### 3.9 新接手 · 只奖不罚 (ramping)
Familiarity index (handled the account ≥ `familiar_min_weeks` distinct weeks of the
prior year **and** last touch within `familiar_max_gap_weeks`). Unfamiliar →
`ramping`: volume growth counts toward Grow; decline does not count against them.

---

## 4. Incentives (forward-looking)
- **Defend / Grow (fixed bonus pool).** Split `defend_pct` (~0.35, Grow-heavy) /
  Grow. Defend ∝ profit retained; Grow ∝ scored profit above target + ramping
  **volume** upside.
- **Acquire (continuous ramp bonus).** A new account (no baseline / first-seen after
  launch) earns `acquisition_pct × its profit` (commission-style, no threshold) for
  the first **`acquisition_ramp_periods` (~1 quarter)**, then graduates into the
  Defend/Grow book. No one-time spiff — rewards landing *and* keeping; self-corrects
  on churn.
- **Churn.** Mostly uncontrollable; no automatic penalty (optional manager-confirmed
  behavior-churn fine).

Default dials: `window_weeks=13`, `provisional_min_weeks=13`, `defend_pct=0.35`,
`acquisition_pct=0.02`, `acquisition_ramp_periods=3`, `holiday_weight=0.0`,
`familiar_min_weeks=4`, `familiar_max_gap_weeks=26`.

---

## 5. Manager review screen
Per-account table, decliners surfaced first. Controls:

| Status | Effect | Use for |
|---|---|---|
| **Normal** (default) | scored vs baseline target | the vast majority |
| **Exempt** | dropped this period | closure / genuine collapse / vacation |
| **Rebaseline** | target reset to a system-proposed recent run-rate the manager confirms/nudges | permanent downsizing |

New for W&T:
- **Per-period constrained-item entry** (with auto-detect suggestions) — applied
  symmetrically to current + baseline.
- **Closure confirmation** — the system surfaces accounts silent beyond ~3× their
  **own** order cadence (`flag_silent_accounts`); the manager confirms closed →
  Exempt going forward, excluded from churn. A mid-window closure shows as a profit
  drop until confirmed, which is why the silence detector surfaces it fast.
- **Acquisition-ramp review** — new accounts and their ramp bonus.

Rules unchanged from Coreline: default Normal; every override logged
(who/when/note/call?); exempting an unusually high profit share flags the period.

---

## 6. Cadence & payout
Review/pay every **4-week period**; score on the **trailing-13-week** profit number
(market-adjusted). A single period is too noisy.

---

## 7. Engine / model deltas for the port (build guidance)
Mirror Coreline's structure with these changes:
- **`engine.py`** → replace `compute()` with `compute_wandt()` from the notebook:
  **profit** basis; input is **item-level**; headline **market-adjusted** with size
  de-trend relative-to-market; add `exclude_constrained_items`, calendar capacity,
  `price_volume_mix_bridge`, the new/provisional/mature ladder, acquisition ramp,
  Grow-heavy split. **No margin floor.**
- **`models.py`** → account = **`Customer`**. Line table keeps `item_number, qty,
  unit_price, extended_price, unit_cost, extended_cost, batch_number,
  customer_number, document_date` (**keep cost — needed for profit**). Add: `Role`
  on the associate/roster, `ConstrainedItem(period_id, item_number)`,
  `DowWeight(dow, weight)` + holiday calendar, `AcquisitionAward`, and a
  closure/exempt flag per account.
- **`service.py`** → keep the 28-day period grid, caching, override plumbing; swap
  the engine call and the bonus allocator (3-way Defend/Grow/Acquire); add the
  `flag_silent_accounts` decision-support query.
- **`load_history.py`** → ingest the two item-level XLSX files; attribute via
  `resolve_associate`; **filter to the 5 sales reps via `Role`**; account = Customer.
- **UI** → constrained-item entry, closure-candidate review, acquisition-ramp panel,
  and the headline (profit vs market) with real-growth beside it.

---

## 8. Known limitations / future
- **Weekend direction** in the source docx is inverted vs the data — confirm.
- **Acquisition figures are illustrative** until the go-forward new-account feed and
  ramp tracking are live.
- **Constrained-item list** is manager-supplied per period; auto-detect is a
  suggestion aid only.
- **Price elasticity / inflation:** no explicit model now (noisy with 2 yrs of
  data); a coarse category-level elasticity is a future enhancement.
- **Customer-name encoding:** the ERP's non-English names carry some encoding noise
  (cosmetic; account IDs are exact).
