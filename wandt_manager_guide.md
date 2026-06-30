# Manager guide (plain English)

This is the manager's companion to the rep guide. The app **suggests** a bonus for each rep every 4 weeks
using three simple pieces — **Contribution** (line items), **Growth** (beating a fair bar), and
**Acquisition** (landing new accounts). You stay in control: the suggestion is a fair starting point, and
**you set the final award**. Everything you change is logged.

If you're brand new, read this top to bottom once — each section is a page in the app and the action you take
there.

---

## How a bonus is built (the 30-second version)

Per 4-week period, for each rep:

- **Contribution** = line items written × **$0.10**.
- **Growth** = **1%** of every sales dollar above the rep's **target bar**. The bar = *today's cost of last
  year's order + last year's profit*, then moved by *how much accounts that size really grew*. (So passing
  higher costs through, or just riding a market boom, doesn't count as growth. There is **no stretch hurdle** —
  every dollar above the bar earns.)
- **Acquisition** = a flat **$50 / $100 / $150** when a rep lands a new account they actually won.

Infrequent accounts are handled separately on the **Annual review** page (once a year), not per period.

---

## Team page — read the scoreboard

The home page (**Team**) lists every rep for the selected period: line items, contribution, growth target vs
growth actual, growth $, new accounts, acquisition, a **Review** flag count, the **suggested bonus**, and the
**award** you've set. Use the period selector at the top to move between periods. Click any rep to open their
detail page.

**Suggested vs. award:** the suggested number is what the formulas produce. The **award** is what you decide to
pay — start from the suggestion and adjust for anything the formula can't see.

---

## Rep detail page — set the final award

Click a rep to see their three pieces and a **per-account table** (each account's last-4-week sales, its
target, how it's doing, and its status). Here you can:

- **Set the award and a behavior-churn fine.** Enter the final award $ (defaults to the suggested total) and,
  if needed, a **fine** (default $200) for behavior that churned an account — with a note. This is your manual
  override of the payout.
- **Exempt an account.** Use the per-account **Normal / Exempt** dropdown to drop an account from that rep's
  **growth** (e.g. it closed or collapsed, and its drop is unfairly dragging the rep down). Line items and any
  acquisition credit are untouched — exempt only removes it from growth.

---

## New accounts — confirm who actually won them

**New accounts** page. A new account defaults to **Assigned** (handed to the rep) — which earns **no landing
bonus**. When the rep genuinely **won** the account, flip it to **Self-acquired**, which releases the flat
acquisition bonus ($50/$100/$150 by the account's annualized size). This is the gate for acquisition pay:
nothing pays until you confirm it here.

---

## Big jumps — release or withhold a windfall

**Big jumps** page. When an account suddenly does **2×+ its normal level** in 4 weeks, the engine **withholds**
the over-bar "windfall" and lists it here — because a sudden 2–10× is usually the *customer* growing their own
business, not the rep's selling. For each one you decide:

- **Customer-driven — withhold** (default): the windfall stays out of the rep's growth.
- **Rep won it — pay windfall**: releases it into the rep's growth.

The page shows the whole-account 4-week sales, the **normal level** it's compared to (the higher of its recent
pace and its seasonally-adjusted year-ago), the **jump ×**, the **last 3 months vs the prior 3**, the rep's
share, and the dollars held. Ordinary growth (under 2×) is never listed here — it pays automatically.

A **"likely timing" badge** means the account's **last 3 months are flat** versus the prior 3 — so a 4-week
spike is probably just a recurring order that landed on a different week this year, **not** real growth. When
you see it, you'll usually leave the windfall **customer-driven / withheld**. The same badge appears on a rep's
detail page on accounts whose *drop* this period is merely a shifted order — so you don't misread a normal
account as one the rep is losing.

Each flagged row also has a **mini chart** — the account's weekly sales for the **last 13 weeks this year vs the
same weeks last year** (current 4 weeks shaded). You can confirm a timing shift at a glance without leaving the
app: if last year's and this year's spikes sit at *different* weeks, it's a shifted order; if this year's line
rides clearly above last year's, it's real growth.

**Quarter-health gate.** Growth only counts if the account is actually growing: if its **last 13 weeks are more
than 5% below the same 13 weeks last year**, a 4-week pop **doesn't** earn growth — it's dropped automatically
(not a review item). You'll see those accounts with a **"qtr down — no growth"** badge on the rep detail page,
and the rep sees them on their own page under "growth not counted." This is what now handles GW-Corona-style
cases (jumped in the window, but down over the quarter). The 5% floor is a Settings dial.

**A note on small timing bumps.** Only **big** spikes (2×+) are auto-withheld for your review. A *small* bump
from a shifted order just pays through — it's usually a few dollars and not worth chasing, and leaving it flow
avoids holding back a rep who's genuinely starting to grow. If you ever see an account's **"timing?" badge** (on
a rep's detail page) sitting on a number that's clearly inflating that rep's growth, you can trim it with the
**award**. The system handles the big cases automatically and leaves the small judgment calls to you.

---

## Closures — set a dead account aside

**Closures** page. The app flags accounts that have gone **silent** far longer than they normally go between
orders (a likely closure). Confirm one as **closed/exempt** to drop it from scoring so it doesn't drag the
rep's growth down. (You can also exempt any account directly from the rep detail page.)

---

## New products — credit genuine launches

**New products** page. Lists SKUs first sold company-wide in the last ~26 weeks. Most are catalog churn (a
re-coded size or brand of something already sold) and should stay **Not featured** (they pay nothing extra).
Mark a **genuinely new** product **Featured**, and its sales count toward the seller's growth at a reduced
**20%** (credited, but not fully, since the company created the product).

---

## Limited stock — keep comparisons fair

**Limited stock** page. If a product was supply-constrained (you couldn't sell what you didn't have), add it as
a **constrained item** for the period. It's then removed from **both** the current window and the year-ago
baseline, so a shortage doesn't unfairly lower a rep's growth. The page also auto-suggests candidates; you
confirm them.

---

## Annual review — the once-a-year accounts

**Annual review** page. Accounts that order too rarely for a 4-week measure (infrequent) are scored here
instead, on a rolling **last-12-months vs the prior 12 months** basis, and **paid once a year**. The page lists
each rep's infrequent accounts, their annual sales vs target, and a suggested annual growth bonus; open a rep
to **set their annual award**. These accounts still earn line items in the regular per-period flow.

---

## Roster — reps, hours, salary

**Reps** page. The managed list of sales associates: role (full-time / part-time / manager), active status,
hours per day, and salary. Only **full-time** and **part-time sales** roles are scored; managers and inactive
people are excluded everywhere. Every edit is saved with a **change history** (who changed what, and when).
This roster is reference data — it does **not** feed the bonus math directly.

---

## Import — load the sales data

**Import** page. Upload the item-level invoice export (Excel). Import is **idempotent by order number** —
re-uploading a file replaces those orders' lines rather than double-counting, so it's always safe to re-run.
Only the sales reps' lines are stored.

---

## Settings — the dials (and what they do)

**Settings** page. These tune the formulas; the current values are sensible defaults. Grouped:

**Contribution**

- **$ per line item** — pay per order line (now **$0.10**).

**Growth**

- **Measurement window (weeks)** — how long a window growth is measured over (now **4** = one period).
- **Payout per $ above the bar** — the growth rate (now **1%**).
- **Size bands** — how many size groups accounts are split into for the "typical move" comparison (now **5**).
- **Cost-inflation window (weeks)** — the window used to re-price last year's basket at today's cost (now 13),
  so passing higher costs through isn't counted as growth.
- **Glide catch-up speed (0–1)** — how fast a newer/level-shifted account's bar catches up to its own recent
  pace (now **0.20** ≈ a quarter of memory; lower = rewards a climb longer).
- **Use last-year only if ≥ this × recent** — when the year-ago window is reliable enough to use vs. falling
  back to the glide bar (now 0.80).
- **Flag a "big jump" at (× the bar)** — the doubling threshold for the jumps review (now **2×**).
- **Smooth last-year bar over ± weeks** — optional smoothing of the year-ago window (now **0** = off/strict).
- **Annual cadence if order gap > weeks** — accounts whose median gap between orders exceeds this go to the
  Annual review (now **4 weeks**).
- **New-product window / attribution** — how long a SKU counts as "new" (now 26 weeks) and at what fraction it
  credits to growth (now **20%**).

**Acquisition**

- **Small / Medium thresholds and the three flat amounts** — the size cutoffs by annualized revenue
  (**<$15k / $15–65k / >$65k**) and the flat landing bonuses (**$50 / $100 / $150**).
- **"New" window (periods)** — how long an account counts as new for acquisition (now **3** ≈ one quarter).

**Other**

- **Behavior-churn fine ($)** — the default fine you can apply on the rep page (now **$200**).

Changing a dial recalculates every rep's bonus immediately.

---

## Export

**Export** downloads a CSV of the full scoreboard for the period, including the **award** and **fine** you've
set — ready for payroll or your own records.

---

## The one rule to remember

The app gives you a **fair, explainable starting number** for every rep; **you** make the final call (award,
fine, exemptions, jump releases, acquisition confirmations). Every change is logged, so the whole thing stays
auditable.
