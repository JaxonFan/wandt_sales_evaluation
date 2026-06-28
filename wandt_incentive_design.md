# W&T Sales Incentive — Proposed Design

## The idea in one line
Reps get a **base salary** plus a **bonus made of three simple pieces** — *sell items, grow your book, land new accounts* — where every piece is a plain "you did X, so you earned $Y" formula. No pool to split, no ranking against coworkers, so any rep can calculate their own number.

## Why this shape
We want four things at once: reward day-to-day **contribution**, push **growth**, pull for **new accounts**, and keep it **understandable**. The big distributors (Sysco, US Foods) solve this with "earn a base on your whole book, a higher rate on growth, and an elevated rate on new accounts during a ramp." We've adapted that, with each piece expressed as a direct formula the rep can see.

## The three pieces

### 1. Contribution — paid per item placed
A small set amount for **every line-item the rep writes this period** (a dial the manager sets). This rewards the actual work — and because more items come from both *more orders* and *richer orders*, it quietly encourages **cross-selling** (getting an account to add items). Salary already covers the bulk of "servicing the book," so this piece is intentionally modest.
*Example: 1,015 items × $0.20 = **$203**.*

### 2. Growth — beat a fair target, measured over a trailing quarter
A rep's target is set per account and shown as **one number** with a plain breakdown:

> **target = your accounts' cost-adjusted last-year baseline (today's cost + last-year profit) × the typical move for accounts that size**

and the rep earns **a cut of every dollar above it**. Holding the base book is already paid by salary +
contribution, so we don't pay twice. Key properties (each fixes a real failure mode we hit and measured):

- **4-week window (= the pay period), vs the same 4 weeks last year.** Measuring per period (rather than a
  rolling quarter) keeps it intuitive for reps ("you beat your bar this month") and, crucially, **surfaces
  one-time jumps to the manager**: a $60k month on a $30k bar reads as 2× and trips review, whereas a quarter
  window would dilute that same order below the review line and auto-pay it. The cost — bumpier period-to-period
  checks — is contained by the glide bar and the jump review below.
- **De-trended by account size (this replaces hand-set tiers AND the inflation/market adjustments).** We
  group accounts into size bands and use each band's **actual typical year-over-year move** as the bar.
  That number already contains the market tide *and* price inflation, so a rep earns only for **beating
  what accounts like theirs are doing** — not for riding a company-wide +50% year. (Down markets lower the
  bar too.) This killed the big period-to-period swings we were seeing.
- **Glide bar (the primary bar) — rewards *continuing* to grow, never overpays an elevated account.** An
  account's bar **follows its own recent run-rate and slides up as it grows** (a recursive moving average,
  speed `glide_alpha` ≈ 0.20 / ~a quarter of memory), lifted by a **cross-account, size-banded seasonal
  factor** (how accounts its size are moving this period vs their own normal — handles holidays/CNY without a
  lumpy per-account last-year window). A rep earns by pushing an account *above* its recent self, so a one-time
  jump pays for a quarter then the bar catches up — you have to **keep growing** to keep earning. `min_baseline_ratio`
  (≈ 0.80) governs the handful of accounts with a genuinely representative same-4-weeks-last-year window, which
  still use **last-year × size-band** instead. This fixed both the +3000% near-zero-baseline artifact *and* the
  "overpay an elevated account for a year" problem of pure year-over-year.
- **Jump review (no dollar floor).** A single-period **doubling** — `recent ≥ jump_multiple`(2)`× its bar`, i.e.
  100%+ over — is itself the anomaly (a 10× is almost always the *customer* expanding, not the rep), so the
  **entire over-bar amount is withheld** and the account is put on the manager's `/jumps` page. **Any** doubling
  qualifies regardless of size (no minimum), and nothing is silently withheld — only flagged accounts are held.
  The manager releases the windfall in full if the rep genuinely won it; ordinary (sub-double) growth pays through.
- **Lunar New Year:** any period within ~3 weeks of CNY is auto-aligned to *last year's* CNY (a moving
  holiday) so the spike lines up on both sides.
- **Accounts without a full year of history** are never compared to a fake $0 baseline (which used to
  double-pay a new account as acquisition *and* "growth"). New accounts earn **acquisition** for their
  first quarter; then **provisional** (their own prior quarter × the company's seasonal swing) until they
  have a year; then year-over-year. Too-sparse accounts earn line-item contribution only.
There is **no stretch hurdle** — once a rep covers today's cost and last year's profit, every dollar above earns.
Each rep's roster data (hours, salary, role) is a managed reference record on the **`/reps`** page, with a change
history — it does not feed the bonus.

### Cost-protected growth bar (2026 update)
Growth stays **revenue-based**, but the year-ago bar = **(last-year cost × a company cost-inflation factor)
+ last-year profit** — "cover today's cost of last year's basket and still clear last year's profit." The
factor is a matched-item Laspeyres (same basket repriced at today's cost, ~1.06). The size-tier de-trend is
computed **on this cost-adjusted baseline**, which strips cost OUT of the de-trend (its factor falls ~0.87→0.83
= /1.06), so the de-trend reflects only the *real* market move while cost is handled precisely per account. Net:
a rep who merely passes higher costs through earns ~0 growth. `growth_payout_rate` is **3%** (was 4.5%).

### 3. Acquisition — a **size-tiered flat bonus**, paid once when a self-acquired account lands
A self-acquired new account pays a **flat bonus by size** when it lands — **$100 / $200 / $300** for
small / medium / large (by annualized revenue: <$15k / $15–65k / >$65k). Flat (not a % of revenue) so the
reward is for *winning* the account, not its size — landing a small shop and a big one take similar effort.
**Manager review (default = Assigned):** new accounts default to **Assigned** — they earn line items now
and provisional growth once they have a quarter of history, but **no 1% share**. On the *New accounts*
page the manager confirms the ones the rep actually **won** as **Self-acquired**, which releases the
1% revenue share for the account's first ~quarter. (Assigned ≠ self-acquired: no acquisition credit.)

**Exempt:** the manager can mark any account **Exempt** for a period (e.g. it closed or collapsed). Exempt
removes it from **Growth only** — its drop no longer drags the rep's growth down — while line items and any
acquisition credit are untouched.

## What the associate sees
```
A rep — this period
   Grow your book (last 3 months)   $620,000  vs target $590,000  (above — earning)
     target $590k = cost-adjusted last year (today's cost + last-year profit) × your size tier's real move
   Your bonus:
     Sell line items   ~800 lines × $0.10                     = $80
     Grow your book    beating your $590k target              = $1,350
     New accounts      1% revenue share on new accounts       = $120
     ─────────────────────────────────────────────────────────
     Total                                                    ≈ $1,550
```
*(Illustrative. Team total lands ~$3,000/period; dials tunable in Settings. See `wandt_bonus_explainer.md`
for the rep-facing plain-English version.)*
*(Illustrative, latest 4-week period; team total lands ~$3,000. Dials are tunable in Settings.)*

Each rep also gets a **"where am I vs my target"** dashboard: one target number, how far along they are, whether they're ahead or behind a **calendar-aware pace line** (it allows for slow weekends, so a quiet Sunday doesn't read as "behind"), a "to finish, ~$X/selling-day" guide, the three bonus lines as a running tally, the new accounts still earning for them, and a short **accounts-to-watch** list (customers that have gone quieter than usual — worth a call).

## How it handles the tricky cases
- **A star already running a big book full-out** — earns the item credit on all their work and the growth cut on anything above target; the target itself is large because their book is large, so they're never zeroed for "no room to grow."
- **A rep who was slacking and picks up the pace** — only earns above their *own* target, so getting back to their normal level is no windfall.
- **Big vs. many-small books** — items reward the grind of many accounts; growth tiers keep big-account targets realistic.
- **New reps / new territory** — nothing here punishes a thin early book; acquisition is reward-only.
- **Part-timers** — growth target scaled to hours; items and acquisition need no adjustment.
- **Finding customers is slow** — acquisition has no quota; a dry period costs nothing.

## Dials the manager sets (all editable in the app)
- Contribution: **$ per line item** (≈ $0.10)
- Growth: measurement window (4 wks), # size bands (5), **cost-inflation window** (13 wks), **payout per $ above target** (3%), glide/jump dials
- Acquisition: **size-tiered flat bonus** ($100/$200/$300 by size), "new" window (**1 quarter**)
*(Calibrated so total bonus lands ~$3,000/period across the team; tune to your budget.)*

*(Numbers are illustrative starting points — set them to land at your intended bonus budget, with acquisition weighted as you like.)*

## One honest trade-off
To keep it understandable, growth is measured against **your own last year**, not adjusted for market-wide swings. If the whole market booms or slumps, the manager nudges the growth-% dials that period rather than the system doing it automatically. We can layer a market adjustment back in later if it's worth the added complexity.

## Lineage
This mirrors how Sysco / US Foods pay — a base on the book, an accelerator on growth, an elevated rate on new accounts during a ramp, and a salary guarantee for new reps — adapted to W&T (margins are set centrally, so we work in sales dollars, and "items placed" stands in for the distributors' drop-size / penetration metrics).
