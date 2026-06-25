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

> **target = your accounts' sales the same 3 months last year × the typical move for accounts that size × (1 + a small stretch)**

and the rep earns **a cut of every dollar above it**. Holding the base book is already paid by salary +
contribution, so we don't pay twice. Key properties (each fixes a real failure mode we hit and measured):

- **Trailing quarter, not a single 4-week window.** Sales are lumpy; one big/slow month would whipsaw a
  single-period comparison. Measuring the rolling 3 months (vs the same 3 months last year) is steady.
- **De-trended by account size (this replaces hand-set tiers AND the inflation/market adjustments).** We
  group accounts into size bands and use each band's **actual typical year-over-year move** as the bar.
  That number already contains the market tide *and* price inflation, so a rep earns only for **beating
  what accounts like theirs are doing** — not for riding a company-wide +50% year. (Down markets lower the
  bar too.) This killed the big period-to-period swings we were seeing.
- **Per-account cap + review.** A single account that explodes (e.g. a 25× one-time jump) counts toward
  growth only up to ~2× its target; the excess is **held back and flagged on the manager's review page**
  (real? one-time order? reassignment?), so one lucky account can't make or break a rep's bonus.
- **Lunar New Year:** any period within ~3 weeks of CNY is auto-aligned to *last year's* CNY (a moving
  holiday) so the spike lines up on both sides.
- **Accounts without a full year of history** are never compared to a fake $0 baseline (which used to
  double-pay a new account as acquisition *and* "growth"). New accounts earn **acquisition** for their
  first quarter; then **provisional** (their own prior quarter × the company's seasonal swing) until they
  have a year; then year-over-year. Too-sparse accounts earn line-item contribution only.
**Part-time reps:** their growth *stretch* is scaled to the hours they work (≈ half), so a part-timer with a small book gets a lighter, fair target — but the **same payout rate** per dollar grown.

### 3. Acquisition — an elevated 1% revenue share on new accounts
A new account (first order within the last ~quarter) pays the rep an **elevated 1% of its revenue**
each period it's "new," then it graduates into the normal book. Simple, and bigger accounts pay
more (it's a % of their revenue). No quota — a quiet period costs nothing.
**Manager review:** new accounts sometimes "fly in" or get randomly assigned. The manager can mark
any new account **"not rep-won (inbound)"** on the *New accounts* page → it stops paying acquisition
(it just counts toward line items until it has history).

## What the associate sees
```
A rep — this period
   Grow your book (last 3 months)   $620,000  vs target $590,000  (above — earning)
     target $590k = last year $520k + size/market lift +10% + stretch +3%
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
- Growth: measurement window (13 wks), # size bands (5), **stretch above your band** (≈ +3%), **payout per $ above target** (≈ 4.5%), **per-account cap** (≈ 2× target), **review threshold** (≈ $20k), **part-time factor** (≈ 0.5)
- Acquisition: **revenue share** (≈ 1%), "new" window (**1 quarter**)
*(Calibrated so total bonus lands ~$3,000/period across the team; tune to your budget.)*

*(Numbers are illustrative starting points — set them to land at your intended bonus budget, with acquisition weighted as you like.)*

## One honest trade-off
To keep it understandable, growth is measured against **your own last year**, not adjusted for market-wide swings. If the whole market booms or slumps, the manager nudges the growth-% dials that period rather than the system doing it automatically. We can layer a market adjustment back in later if it's worth the added complexity.

## Lineage
This mirrors how Sysco / US Foods pay — a base on the book, an accelerator on growth, an elevated rate on new accounts during a ramp, and a salary guarantee for new reps — adapted to W&T (margins are set centrally, so we work in sales dollars, and "items placed" stands in for the distributors' drop-size / penetration metrics).
