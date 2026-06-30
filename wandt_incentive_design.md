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
- **Jump review (no dollar floor).** A single-period **doubling** — `recent ≥ 2× its normal level`, where
  **normal level = the higher of** the account's recent run-rate **and** its seasonally-adjusted year-ago bar
  (so a weak year-ago comp can't false-flag a normal month) — is itself the anomaly (a 10× is almost always the
  *customer* expanding, not the rep), so the **entire over-bar amount is withheld** and the account is put on the
  manager's `/jumps` page. The manager releases the windfall in full if the rep genuinely won it; ordinary
  (sub-double) growth pays through. The `/jumps` page shows a this-year-vs-last-year **chart**, a **12-mo-vs-prior**
  figure, and a *"likely timing"* badge when the swing is just a recurring order that shifted weeks.
- **Lunar New Year:** any period within ~3 weeks of CNY is auto-aligned to *last year's* CNY (a moving
  holiday) so the spike lines up on both sides.
- **Accounts without a full year of history** are never compared to a fake $0 baseline (which used to
  double-pay a new account as acquisition *and* "growth"). New accounts earn **acquisition** for their
  first quarter; then **provisional** (their own prior quarter × the company's seasonal swing) until they
  have a year; then year-over-year. Accounts that order too infrequently for a 4-week measure (**median or mean
  order gap ≥ 4 weeks** — i.e. less than ~one order per period on average, which also catches burst-then-dormant
  accounts) are scored on the **Annual Review** track (rolling 12 months vs. the prior 12, paid once a year), and
  still earn line-item contribution each period. A *"new (<1 yr)"* tag marks accounts with no full year of
  history so a $0 prior-year reads as new, not an error.
There is **no stretch hurdle** — once a rep covers today's cost and last year's profit, every dollar above earns.
Each rep's roster data (hours, salary, role) is a managed reference record on the **`/reps`** page, with a change
history — it does not feed the bonus.

### Cost-protected growth bar (2026 update)
Growth stays **revenue-based**, but the year-ago bar = **(last-year cost × a company cost-inflation factor)
+ last-year profit** — "cover today's cost of last year's basket and still clear last year's profit." The
factor is a matched-item Laspeyres (same basket repriced at today's cost, ~1.06). The size-tier de-trend is
computed **on this cost-adjusted baseline**, which strips cost OUT of the de-trend (its factor falls ~0.87→0.83
= /1.06), so the de-trend reflects only the *real* market move while cost is handled precisely per account. Net:
a rep who merely passes higher costs through earns ~0 growth. `growth_payout_rate` is **1%** of every dollar above the bar.

### Quarter-health gate (2026 update)
A 4-week pop doesn't pay if the account is **shrinking over the bigger picture**. If an account's **trailing 13
weeks are below 95% of the same 13 weeks last year** (and it has a real prior-year quarter, > $3k), its growth
**doesn't count** this period — it's dropped automatically (not a manager-review item). The rep sees it on their
page under *"growth not counted,"* the manager sees a *"qtr down"* badge. So growth only pays when the account
is genuinely healthy, not when a one-off order lands on a declining account.

### Annual vs. periodic accounts (2026 update)
An account whose **median or mean order gap is ≥ 4 weeks** (it orders monthly or sparser on average — including
burst-then-dormant accounts) can't be measured fairly in a
4-week window — its order lands in different weeks each year. Those accounts are pulled onto a separate **Annual
Review** track: **rolling 12 months vs. the prior 12 months, paid once a year**, off the per-period flow. They
still earn line-item contribution every period. Everything else is scored per 4-week period as below.

### 3. Acquisition — a **size-tiered flat bonus**, paid once when a self-acquired account lands
A self-acquired new account pays a **flat bonus by size** — **$50 / $100 / $150** for small / medium / large
(by annualized revenue: <$15k / $15–65k / >$65k). Flat (not a % of revenue) so the reward is for *winning* the
account, not its size. Paid **once at the ~quarter mark** (the period its age is `[2·period, 3·period)` after
first order), sized by its **annualized first-quarter run-rate** (trailing-13wk × 4) — not a noisy first-period
guess. One payment per account, to the rep with the most of its revenue over that quarter.
**Manager review (default = Assigned):** new accounts default to **Assigned** — they earn line items now
and provisional growth once they have a quarter of history, but **no landing bonus**. On the *New accounts*
page the manager confirms the ones the rep actually **won** as **Self-acquired**, which releases the
**flat landing bonus** ($50 / $100 / $150 by size). (Assigned ≠ self-acquired: no acquisition credit.)

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
     New accounts      flat bonus by size (1 landed, medium)  = $100
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
- Growth: measurement window (4 wks), # size bands (5), **cost-inflation window** (13 wks), **payout per $ above target** (1%), glide/jump dials
- Acquisition: **size-tiered flat bonus** ($50/$100/$150 by size, paid at the quarter mark), "new" window (**1 quarter**)
*(Calibrated so total bonus lands ~$3,000/period across the team; tune to your budget.)*

*(Numbers are illustrative starting points — set them to land at your intended bonus budget, with acquisition weighted as you like.)*

## One honest trade-off
Growth is measured over a short **4-week window**, which keeps it intuitive ("you beat your bar this month") and
surfaces one-time jumps to the manager — but a 4-week window is **lumpy** for accounts that order irregularly.
We handle that without smoothing the number itself: the **size-band de-trend** already removes market-wide
swings (you earn for beating accounts your size, so a boom or slump moves your bar with it); the **jump review**
withholds one-time spikes; the **timing badge + chart** flag a shifted order; the **quarter-health gate** stops
a pop from paying on a shrinking account; and genuinely infrequent accounts move to the **annual track**. The
residual cost — small period-to-period bounce on the smallest accounts — is intentional, and the manager always
sets the final award.

## Lineage
This mirrors how Sysco / US Foods pay — a base on the book, an accelerator on growth, an elevated rate on new accounts during a ramp, and a salary guarantee for new reps — adapted to W&T (margins are set centrally, so we work in sales dollars, and "items placed" stands in for the distributors' drop-size / penetration metrics).
