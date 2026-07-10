# W&T Sales Associate Scorecard — App Specification

> The W&T adaptation of the Coreline scorecard, redesigned June 2026 into the direct three-piece model.
> Live engine: `app/engine.py::compute_period_bonus`. Full mechanics + rationale in
> `wandt_incentive_design.md` (source of truth); rep-facing plain English in `wandt_bonus_explainer.md`.
>
> Scope: the **5 W&T sales reps** — An Cao & Vanessa Wu (full-time), Garmi Mei,
> Ting Ting, Wendy Ye (part-time). Managers (Cindy Chan, Morgan Wu, Tina Ni),
> inactive (MT/PJ), and N/A are **excluded everywhere**. Source = W&T item-level
> invoice export with `Batch Number`.

---

## 0. What's different from Coreline (read this first)
W&T shares Coreline's *self-comparison* philosophy — every account judged against
its **own** baseline, size-de-trended, with manager overrides — with
these changes:

| Topic | Coreline | W&T |
|---|---|---|
| Who is scored | 6 China-sales associates | the **5 sales reps** only; the roster `Role` column drives this (managers excluded) |
| Pay measure | profit$ with a margin floor | **three direct pieces on revenue** (Contribution + Growth + Acquisition); margin handled inside the growth bar, not by scoring profit |
| YoY fairness | profit vs baseline (+ optional vs-market) | **growth = revenue above a cost-adjusted YoY bar × the size-band "typical move"** (subsumes market tide + inflation); gated by quarter-profit + annual-revenue checks |
| Limited stock | not modeled | **per-period manager-supplied constrained items**, removed from **Growth only** (both windows) |
| Acquisition | decision-support only | **in the incentive** — a flat $50/$100/$150 by size, paid once at the quarter mark |
| Growth vs contribution | Defend 60% | **no pool** — each piece is its own direct formula |
| Data grain | order totals | **item-level** (line-item contribution + new-product attribution) |
| Calendar | 28-day buckets assumed clean | whole-week-aligned windows + CNY alignment for the growth comparison |

> **Data note:** the source docx says weekend sales are *higher*; the data shows the
> **opposite** (Sun/Sat ≈ 0.4%/2.8% of a week vs ~17–22% per weekday). The
> normalization is needed either way; confirm the wording.

---

## 1. What the system does (one paragraph)
Every 4 weeks, for each of the 5 sales reps, it computes a bonus made of **three direct, self-computable
pieces** (no pool, no peer ranking): **Contribution** = line items placed × $0.10; **Growth** = 1% of every
revenue dollar above a fair target (the account's cost-adjusted same-weeks-last-year bar, or a glide/
provisional bar for newer accounts, lifted by the typical move of accounts its size); and **Acquisition** =
a flat $50/$100/$150 by size, paid once when a self-acquired new account lands. Growth is protected by two
gates — a quarter **profit** redline and an **annual** reality-check (must be genuinely up year-over-year) —
plus a big-jump review that withholds one-time windfalls. It is fair by construction: each account is judged
against its own history; big accounts aren't punished for natural reversion; passing higher costs through
isn't growth; supply-constrained items are removed from the growth comparison symmetrically; and the manager
sets the final award. **Detailed mechanics live in `wandt_incentive_design.md` (source of truth) and the
rep-facing `wandt_bonus_explainer.md`.**

---

## 2. Data flow
```
Item-level invoice export (daily/period)         Manager inputs (app)
  SOP type/number, item#, qty, unit/ext price,    • per-period constrained items (+ auto-detect)
  unit/ext cost, customer#, document date,        • exempt-from-growth / self-acquired / jump-release
  batch number                                    • featured new products / closure confirmations
        │                                                   │
        ▼                                                   │
  Attribute batch → roster; KEEP ONLY the 5 sales reps      │
  Account = Customer Number ; line_profit = ext price-cost  │
        ▼                                                   ▼
  Period engine  compute_period_bonus()  (every 4-week period, via run_period_bonus)
   • Contribution: line items placed this period × item_rate ($0.10)
   • Growth: recent revenue vs target (cost-adjusted YoY bar, or glide/provisional for newer accounts,
     × size-band "typical move"); constrained items excluded from GROWTH only; gated by quarter-profit
     redline + annual reality-check; big jumps (≥2×) withheld for /jumps review
   • Acquisition: flat $50/$100/$150 by size, once, for a self-acquired new account at its ~quarter mark
   • baseline ladder by history length: mature (YoY) / provisional (prior quarter) / glide (own run-rate)
   • apply manager flags (Exempt-from-growth / self-acquired / jump-release / constrained)
        ▼
  Per-rep scorecards + per-account detail (read)   Manager review screens (read/write)
```

---

## 3. The metric (live engine = `compute_period_bonus`)
> Full mechanics, rationale, and the failure modes each piece fixes are in
> **`wandt_incentive_design.md`** (the maintained source of truth). This is the summary.

The bonus is **three direct pieces** per 4-week period — no pool, no peer ranking, all on **revenue**
(margin is handled inside the growth bar, not by scoring profit):

### 3.1 Attribution & scope (unchanged)
`Batch Number[:2]` → roster `Batch Initial`; free-text variants (`MORGANW`, `TINAN`, `VANESSAW`) →
`Other Names`. Account = `Customer Number`. **Filter to reps whose `Role` ∈ {full time sales, part time
sales} and `Status` = Active** — managers / inactive / N/A are dropped everywhere.

### 3.2 Contribution
Line items the rep placed **this period** × `item_rate` ($0.10). Rewards the day-to-day work; encourages
cross-sell (more/richer orders). Constrained items are **kept** here (the rep shipped those lines).

### 3.3 Growth
`max(0, recent_revenue − target) × growth_payout_rate` (1%), measured over `growth_window_weeks` (4 wks).
Target = the account's **cost-adjusted same-weeks-last-year** bar (mature), or its **glide** run-rate
(newer/level-shifted, EWMA `glide_alpha`=0.30) or **provisional** prior-quarter bar, **× the size-band
"typical move"** (which subsumes market tide + inflation). Protections:
- **Big-jump review:** recent ≥ `jump_multiple` (2×) its normal level → the over-bar windfall is withheld
  for the manager's `/jumps` page (release if rep-won).
- **Quarter-profit redline gate:** no growth if trailing-13-wk **profit** < 95% of last year (shown to reps
  as a revenue redline = today's cost + 95% of last-year quarter profit).
- **Annual reality-check gate:** no growth unless trailing-52-wk **revenue** ≥ 95% of the cost-adjusted
  prior year (stops flat/oscillating accounts harvesting up-swings).
- **CNY alignment** for periods near Lunar New Year; **constrained items excluded from GROWTH only**.

### 3.4 Baseline ladder (by history length)
| Tier | Condition | Bar |
|------|-----------|-----|
| **Mature** | ≥1 yr with a representative year-ago window | cost-adjusted YoY × size-band move |
| **Glide** | level-shifted / no reliable year-ago | own recent run-rate (EWMA), size-band lifted |
| **Provisional** | history but no clean year-ago | own prior quarter × company seasonal swing |
| **New (<~1 quarter)** | first order recent | no growth — items + acquisition only |
| **Annual** | median **or** mean order gap ≥ 4 wks | rolling 12-mo vs prior 12-mo, paid once a year |

### 3.5 Acquisition
A self-acquired new account pays a **flat bonus by size — $50/$100/$150** (small/medium/large by annualized
first-quarter revenue), paid **once at the ~quarter mark**, to the rep with the most of its revenue. Default
is **Assigned** (no landing bonus) until the manager confirms **Self-acquired** on the New-accounts page.

---

## 4. Manager controls (all write to the live engine)
| Control | Page | Effect |
|---|---|---|
| **Exempt** an account | rep detail | removes it from **Growth only** (line items/acquisition untouched) |
| **Self-acquired** confirm | `/acquisitions` | releases the flat acquisition bonus (default = Assigned) |
| **Big-jump** release/withhold | `/jumps` | pay or withhold a doubling's windfall |
| **Featured** new product | `/products` | its revenue counts toward growth, ramping 20%→100% over `new_product_weeks` (13) |
| **Constrained** items | `/constrained` | excluded from the growth comparison (both windows); any period via prev/next |
| **Closures** | `/closures` | confirm a silent account closed → exempt going forward |
| **Award / fine** | rep detail | the manager sets the final $ (defaults to the suggested total) + optional churn fine |
| **Settings** | `/settings` | every dial; changes recompute immediately |

Every override is logged (who/when/note). The system **suggests**; the manager sets the final award.

## 5. Cadence & payout
Review/pay every **4-week period**. Growth uses trailing windows (smooths lumps); the annual track pays once
a year. Contribution & acquisition are current-period.

---

## 6. Code map (current implementation)
- **`app/engine.py`** → `compute_period_bonus()` (the live three-piece engine) + `compute_annual_review()`
  (the annual track). Pure functions over the item-level DataFrame; helpers `_cost_adjusted_baseline`,
  `_cost_inflation_factor`, `_size_band_factors`, `_glide_levels`, `cny_aligned_offset_days`,
  `exclude_constrained_items`. (The legacy profit-pool `compute_wandt` was removed July 2026.)
- **`app/service.py`** → `run_period_bonus()` (glue: loads lines, pulls dials via `_dials`, and the manager
  sets exempt/self-acquired/jump-release/constrained/featured) + `compute_rep_goal` (rep dashboard) +
  `flag_silent_accounts` (closure candidates). 28-day period grid + line-df caching.
- **`app/models.py`** → `SalesLine` (item-level facts), `Associate`, `User`, `Period`, `ManagerAction`
  (exempt / jump-release), `AcquisitionReview`, `NewProductReview`, `ConstrainedItem`, `Setting`, `Award`.
- **`app/config.py`** → `DEFAULTS` dials (item_rate, growth_payout_rate, glide_alpha, the quarter/annual
  gate floors, new_product_weeks, acq flats, …); overridable via the `settings` table.
- **`app/load_history.py`** → ingests the two item-level XLSX files; attributes via `resolve_associate`;
  filters to the sales reps via `Role`; account = Customer Number.

---

## 7. Known limitations / future
- **Weekend direction** in the source docx is inverted vs the data — confirm.
- **Acquisition figures are illustrative** until the go-forward new-account feed and
  ramp tracking are live.
- **Constrained-item list** is manager-supplied per period; auto-detect is a
  suggestion aid only.
- **Price elasticity / inflation:** no explicit model now (noisy with 2 yrs of
  data); a coarse category-level elasticity is a future enhancement.
- **Customer-name encoding:** the ERP's non-English names carry some encoding noise
  (cosmetic; account IDs are exact).
