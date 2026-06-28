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
    "growth_payout_rate": 0.03,    # $ earned per sales-dollar above target (bar = cost-adjusted last-year x real-market move; no stretch)
    "cost_inflation_weeks": 13,    # window for the company cost-inflation factor (same basket repriced at today's cost); the bar = last-year cost x this factor + last-year profit, so passing cost through isn't "growth"
    "glide_alpha": 0.20,           # how fast a level-shifted account's bar catches up to its new run-rate (0..1); ~0.2 = a quarter of memory
    "min_baseline_ratio": 0.80,    # year-ago window must be >= this x recent to use it (else glide, not YoY) — high = lean on the smoother glide bar
    "jump_multiple": 2.0,          # flag a DOUBLING: recent >= this x its bar (100%+ over) -> whole over-bar amount withheld for manager review
    "mature_smooth_weeks": 0,      # 0 = OFF (strict: compare to the EXACT same 4 weeks last year). Smoothing >0 was found to inflate growth via the size-band de-trend, so it's off; timing shifts are handled by the glide/annual paths.
    "sporadic_gap_weeks": 4,       # accounts whose median order gap exceeds this (order less often than the window) are scored ANNUALLY
    "new_product_weeks": 26,       # a SKU is "new" for this many weeks after its company-wide first sale
    "new_product_attribution": 0.20,  # a featured-new product's revenue counts at this fraction toward GROWTH (rep credited but discounted)
    "growth_cap_multiple": 2.0,    # (legacy alias; superseded by jump_multiple)
    "growth_review_min": 10000,    # (deprecated — jumps now flag on the doubling alone, no dollar floor)

    # --- Acquisition (new accounts: a flat bonus by size, paid once when the account lands) ---
    "acq_tier_small_max": 15000,   # annualized revenue < this -> "small" new account
    "acq_tier_medium_max": 65000,  # annualized revenue < this -> "medium"; >= this -> "large"
    "acq_flat_small": 100,         # flat $ for landing a small new account (rewards the effort, not raw size)
    "acq_flat_medium": 200,        # flat $ for a medium new account
    "acq_flat_large": 300,         # flat $ for a large new account
    "acq_revenue_pct": 0.01,       # (deprecated — acquisition is now a size-tiered flat amount, not a % of revenue)
    "acq_ramp_periods": 3,         # an account counts as "new" for ~1 quarter (3 periods), then graduates

    # --- closure decision-support ---
    "fine_amount": 200,            # manager-confirmed behavior-churn fine ($)
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
