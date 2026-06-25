# W&T Sales Incentive — Proposed Design

## The idea in one line
Reps get a **base salary** plus a **bonus made of three simple pieces** — *sell items, grow your book, land new accounts* — where every piece is a plain "you did X, so you earned $Y" formula. No pool to split, no ranking against coworkers, so any rep can calculate their own number.

## Why this shape
We want four things at once: reward day-to-day **contribution**, push **growth**, pull for **new accounts**, and keep it **understandable**. The big distributors (Sysco, US Foods) solve this with "earn a base on your whole book, a higher rate on growth, and an elevated rate on new accounts during a ramp." We've adapted that, with each piece expressed as a direct formula the rep can see.

## The three pieces

### 1. Contribution — paid per item placed
A small set amount for **every line-item the rep writes this period** (a dial the manager sets). This rewards the actual work — and because more items come from both *more orders* and *richer orders*, it quietly encourages **cross-selling** (getting an account to add items). Salary already covers the bulk of "servicing the book," so this piece is intentionally modest.
*Example: 1,015 items × $0.20 = **$203**.*

### 2. Growth — grow your book to a target sized to each account
Each account gets a growth target based on its size, because big accounts can't climb like small ones:

| Account size (trailing-year sales) | Growth ask |
|---|---|
| Large (≥ $100k) — hard to grow | +2% |
| Medium ($20k–$100k) | +5% |
| Small (< $20k) — lots of room | +10% |

A rep's target = each account's sales in **this same stretch last year**, grown by its tier. The rep sees **one number** ("grow your book to $441k"), and earns **a cut of every dollar above it**. Holding the base book is already paid by salary + contribution, so we're not paying twice.

**Inflation is built into the target (forward, not retroactive).** Last year's basket is restated at
today's prices using per-item **cost** inflation, so the target is a concrete number the rep knows up
front — and beating it means *real* growth, not just price rises. (A flat-volume rep facing 5% cost
inflation needs +5% sales just to reach target.)

**Lunar New Year** is a moving holiday with a big demand spike, so for CNY periods the comparison is
auto-aligned to *last year's* CNY (not a fixed 364 days back) so both sides contain the spike.
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
An Cao — this period
   Sales so far     $488,000      Target  $483,000  (inflation-adjusted; ~on pace)
   Your bonus:
     Sell line items   1,015 lines × $0.10              = $102
     Grow your book    $488k vs $483k target × 8.5%     = $467
     New accounts      1% revenue share on new accounts = $7
     ─────────────────────────────────────────────────────
     Total so far                                       ≈ $576
```
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
- Growth: size thresholds & tier %s (≈ +2 / +5 / +10), **payout per $ above target** (≈ 8.5%), **part-time factor** (≈ 0.5)
- Acquisition: **revenue share** (≈ 1%), "new" window (**1 quarter**)
*(Calibrated so total bonus lands ~$3,000/period across the team; tune to your budget.)*

*(Numbers are illustrative starting points — set them to land at your intended bonus budget, with acquisition weighted as you like.)*

## One honest trade-off
To keep it understandable, growth is measured against **your own last year**, not adjusted for market-wide swings. If the whole market booms or slumps, the manager nudges the growth-% dials that period rather than the system doing it automatically. We can layer a market adjustment back in later if it's worth the added complexity.

## Lineage
This mirrors how Sysco / US Foods pay — a base on the book, an accelerator on growth, an elevated rate on new accounts during a ramp, and a salary guarantee for new reps — adapted to W&T (margins are set centrally, so we work in sales dollars, and "items placed" stands in for the distributors' drop-size / penetration metrics).
