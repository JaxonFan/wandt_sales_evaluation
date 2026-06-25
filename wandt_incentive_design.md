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

A rep's target = each account's sales in **this same stretch last year**, grown by its tier. The rep sees **one number** ("grow your book to $441k"), and earns **a cut of every dollar above it** (e.g. 10¢ per $1). Holding the base book is already paid by salary + contribution, so we're not paying twice.
**Part-time reps:** their growth *stretch* is scaled to the hours they work (≈ half), so a part-timer with a small book gets a lighter, fair target — but the **same payout rate** per dollar grown.

### 3. Acquisition — landing bonus + a one-quarter ramp, bigger for bigger accounts
Every new account pays the rep two ways, both a **% of the account's sales** (so a bigger account pays more):
- A **landing bonus** the period it first orders (≈ 10% of its sales that period).
- A **ramp bonus** (≈ 5% of its sales each period) for the rest of **one quarter**, then it graduates into the normal book.
No quota — a quiet period costs nothing; landing pays well.

## What the associate sees
```
An Cao — this period
   Sales so far     $488,000        Target  $441,000   (111% — on pace)
   Your bonus:
     Sell items       1,015 items × $0.20            = $203
     Grow your book   $488k vs $441k target × 10%    = $4,721
     New accounts     0 this period                  = $37 (ramp)
     ─────────────────────────────────────────────────────
     Total so far                                    ≈ $4,961
```
*(Real figures from the latest 4-week period in the data.)*

Each rep also gets a **"where am I vs my target"** dashboard: one target number, how far along they are, whether they're ahead or behind a **calendar-aware pace line** (it allows for slow weekends, so a quiet Sunday doesn't read as "behind"), a "to finish, ~$X/selling-day" guide, the three bonus lines as a running tally, the new accounts still earning for them, and a short **accounts-to-watch** list (customers that have gone quieter than usual — worth a call).

## How it handles the tricky cases
- **A star already running a big book full-out** — earns the item credit on all their work and the growth cut on anything above target; the target itself is large because their book is large, so they're never zeroed for "no room to grow."
- **A rep who was slacking and picks up the pace** — only earns above their *own* target, so getting back to their normal level is no windfall.
- **Big vs. many-small books** — items reward the grind of many accounts; growth tiers keep big-account targets realistic.
- **New reps / new territory** — nothing here punishes a thin early book; acquisition is reward-only.
- **Part-timers** — growth target scaled to hours; items and acquisition need no adjustment.
- **Finding customers is slow** — acquisition has no quota; a dry period costs nothing.

## Dials the manager sets (all editable in the app)
- Contribution: **$ per item** (≈ $0.20)
- Growth: size thresholds & tier %s (≈ +2 / +5 / +10), **payout per $ above target** (≈ 10%), **part-time factor** (≈ 0.5)
- Acquisition: landing % (≈ 10%), ramp % (≈ 5%), ramp length (**1 quarter**)

*(Numbers are illustrative starting points — set them to land at your intended bonus budget, with acquisition weighted as you like.)*

## One honest trade-off
To keep it understandable, growth is measured against **your own last year**, not adjusted for market-wide swings. If the whole market booms or slumps, the manager nudges the growth-% dials that period rather than the system doing it automatically. We can layer a market adjustment back in later if it's worth the added complexity.

## Lineage
This mirrors how Sysco / US Foods pay — a base on the book, an accelerator on growth, an elevated rate on new accounts during a ramp, and a salary guarantee for new reps — adapted to W&T (margins are set centrally, so we work in sales dollars, and "items placed" stands in for the distributors' drop-size / penetration metrics).
