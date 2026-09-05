"""App configuration & default metric dials (overridable via the settings table / env)."""
import os

# Database — SQLite for local dev; set DATABASE_URL to the Postgres URL (RDS) in prod.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./app/data/scorecard.db")

# Session signing key (override in prod via the wandt/SECRET_KEY secret)
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Roster roles that are SCORED as sales reps (managers / blank are excluded everywhere).
SALES_ROLES = {"full time sales", "part time sales"}

# Metric dials (defaults from the 2-year analysis; the engine reads overrides from settings).
# The bonus is THREE direct, self-computable pieces (per 4-week period), no pool:
#   Contribution = line_items x item_rate
#   Growth       = max(0, recent revenue - target) x growth_payout_rate
#                  target = (last-year cost x cost-inflation factor + last-year profit) x typical move for accounts your size
#   Acquisition  = a size-tiered flat bonus, paid once when a self-acquired account lands
DEFAULTS = {
    "period_weeks": 4,             # review/pay cadence (4-week period); the bonus is assessed per period
    "window_weeks": 13,            # trailing window for closure cadence / context
    "holiday_weight": 0.0,         # selling capacity assigned to a federal holiday (0 = a dead day)

    # --- Contribution (line items placed) ---
    "item_rate": 0.10,             # $ earned per invoice LINE ITEM written this period (manager-set dial)

    # --- Growth (beat what accounts your size are doing, measured over the trailing 4 weeks) ---
    "growth_window_weeks": 4,      # measure growth on the trailing 4 weeks (= the pay period; jumps surface to review)
    "size_band_count": 5,          # group accounts into this many size bands for the "typical move" de-trend
    "size_band_window_weeks": 13,  # measure the size-band "typical move" over this many trailing weeks vs the same window a year ago (13 = a quarter: de-noises the 4-week window without chasing the full annual trend — over a quarter established accounts are ~flat, so the factor lands near 1.0)
    "size_band_floor": 0.9,        # de-trend factor clamped >= this; 0.9 = a soft segment may discount the bar at most 10% below cost-adjusted last year (the noisy 4-week window was cutting the smallest band -39%)
    "growth_payout_rate": 0.01,    # $ earned per sales-dollar above target (bar = cost-adjusted last-year x real-market move; no stretch)
    "cost_inflation_weeks": 13,    # window for the company cost-inflation factor (same basket repriced at today's cost); the bar = last-year cost x this factor + last-year profit, so passing cost through isn't "growth"
    "glide_alpha": 0.30,           # how fast a level-shifted account's bar catches up to its new run-rate (0..1); ~0.3 = a few periods of memory
    "min_baseline_ratio": 0.80,    # year-ago window must be >= this x recent to use it (else glide, not YoY) — high = lean on the smoother glide bar
    "jump_multiple": 2.0,          # flag a DOUBLING: recent >= this x its bar (100%+ over) -> whole over-bar amount withheld for manager review
    "mature_smooth_weeks": 0,      # 0 = OFF (strict: compare to the EXACT same 4 weeks last year). Smoothing >0 was found to inflate growth via the size-band de-trend, so it's off; timing shifts are handled by the glide/annual paths.
    "sporadic_gap_weeks": 4,       # accounts whose median order gap exceeds this (order less often than the window) are scored ANNUALLY
    "growth_quarter_floor": 0.95,  # an account earns growth ONLY if its trailing-13-week PROFIT is >= this x the same 13 weeks last year (else a 4-week pop on a profit-shrinking account doesn't count). Shown to reps as a revenue redline = today's cost + this x last-year quarter profit.
    "growth_quarter_min_prior": 3000,  # (legacy, revenue) retained for reference; the gate now uses growth_quarter_min_profit
    "growth_quarter_min_profit": 600,  # only apply the quarter gate to accounts with at least this much PROFIT in the prior-year quarter (new/small accounts are never gated)
    "growth_annual_floor": 0.95,   # annual reality check: an account earns growth only if its trailing-52-week revenue is >= this x the cost-adjusted prior 52 weeks (0.95 = allow 5% YoY slack, matching the quarter gate; blocks growth-harvesting on flat/declining accounts)
    "growth_annual_min_prior": 50000,  # only apply the annual gate to accounts with at least this much prior-year revenue (small accounts' YoY is too noisy to gate)
    "new_product_weeks": 13,       # a SKU is "new" for this many weeks after its company-wide first sale; its credit ramps over this window
    "new_product_attribution": 0.40,  # a featured-new product's growth credit STARTS here and ramps smoothly to 100% by new_product_weeks (no cliff)
    "substitute_attribution": 0.60,   # a 'cheaper substitute' SKU starts higher than a new product (more credit — but not all — for finding a cheaper alternative), same ramp to 100%
    "growth_cap_multiple": 2.0,    # (legacy alias; superseded by jump_multiple)
    "growth_review_min": 10000,    # (deprecated — jumps now flag on the doubling alone, no dollar floor)

    # --- Acquisition (new accounts: a flat bonus by size, paid once when the account lands) ---
    "acq_tier_small_max": 15000,   # annualized revenue < this -> "small" new account
    "acq_tier_medium_max": 65000,  # annualized revenue < this -> "medium"; >= this -> "large"
    "acq_flat_small": 50,          # flat $ for landing a small new account (rewards the effort, not raw size)
    "acq_flat_medium": 100,        # flat $ for a medium new account
    "acq_flat_large": 200,         # flat $ for a large new account
    "acq_revenue_pct": 0.01,       # (deprecated — acquisition is now a size-tiered flat amount, not a % of revenue)
    "acq_ramp_periods": 3,         # an account counts as "new" for ~1 quarter (3 periods), then graduates

    # --- Cumulative profit-growth (the growth-model service) ---
    "cumulative_rate": 0.05,       # BASE rate: $ earned per $ of NET cumulative YoY PROFIT growth up to the rep's target (rep-level netting; progressive true-up on the rep's peak, no clawback)
    "growth_accel_rate": 0.075,    # ACCELERATOR rate on the portion of growth ABOVE the rep's target (marginal)
    "growth_target_default": 0.06, # default per-rep target = this x the rep's last-year book profit (overrides in Setting keys 'growth_target::<name>')
    "young_account_pct": 0.01,     # (display/back-compat) a brand-new account's gap already equals its own profit, so it earns cumulative_rate x profit automatically; this dial is no longer used in the math
    "young_account_months": 12,    # an account younger than this is flagged "new" on the page (no full year-ago to compare)
    "fiscal_start_month": 10,      # cumulative cycle anchor month. 10 = October: the cycle runs Oct->Sep and resets each October (Oct vs Oct last year, then Oct+Nov vs Oct+Nov, ...).
    "program_start": "2026-08-01",  # the program's first day. The displayed cycle never starts before this, so nothing earned before launch shows up as pay (Aug-Sep 2026 is its own contribution-only chapter).
    "growth_start": "2026-10-01",  # growth + acquisition begin here. Before this date the scorecard pays CONTRIBUTION ONLY; history is still kept (it is the year-ago side of the Oct comparison, and what makes new/quiet accounts detectable).

    # --- underperforming accounts (rolling 3-month watch list) ---
    "underperf_window_months": 3,   # rolling window: the last 3 months of PROFIT vs the same 3 months a year ago
    "underperf_min_profit": 500,    # ignore accounts below this much profit in the year-ago window (too small/noisy to judge)
    "underperf_bands": 5,           # accounts are compared against the MEDIAN growth of accounts their own size (quintiles by year-ago profit)

    # --- closure decision-support ---
    "fine_amount": 200,            # manager-confirmed behavior-churn fine ($)
}

# ---------------------------------------------------------------------------------------------------
# TEAMS. Accounts are hard to pin on one person, so GROWTH is measured and paid at the TEAM level: an
# account belongs to the team whose members wrote >= TEAM_OWNERSHIP_PCT of its ORDERS over the trailing
# TEAM_WINDOW_MONTHS, and the team's growth pay is split EQUALLY among its members. An account no team
# owns outright is UNASSIGNED — it earns nothing until the manager assigns it on the Accounts tab (a
# manual assignment always beats the computed one). Contribution and acquisition stay INDIVIDUAL.
TEAMS = {
    "Team 1": ("An Cao", "Ting Ting"),
    "Team 2": ("Garmi Mei", "Vanessa Wu", "Wendy Ye"),
}
HOUSE_TEAM = "House"                     # not paid growth: managers' own book + the house accounts below
HOUSE_MEMBERS = ("Morgan Wu", "Cindy Chan", "Tina Ni")
TEAM_OWNERSHIP_PCT = 0.80                # a team owns an account at >= this share of its orders
TEAM_WINDOW_MONTHS = 12                  # ownership is measured over the trailing 12 months of orders

# Accounts that are HOUSE by default whoever writes the order (still overridable on the Accounts tab).
HOUSE_ACCOUNTS = frozenset({
    "FIRSTIN01", "FIRSTTX01",                                        # FIRST CHOICE SEAFOOD
    "ENSONFL01", "ENSONFL02", "ENSONGA01", "ENSONOH01", "ENSONVA01",  # ENSON MARKET / SEAFOOD
    "CASH & CARRY",              # the bare walk-in catch-all; the NAMED variants (CANEWBK01 / CASH & CARRY01) are real accounts
})

# Accounts removed from the GROWTH calc only (house accounts run by non-reps; still fine for the raw data).
GROWTH_EXEMPT_ACCOUNTS = HOUSE_ACCOUNTS

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
