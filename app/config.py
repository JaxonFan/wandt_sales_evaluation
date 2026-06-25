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
#   Contribution = items_placed x item_rate
#   Growth       = max(0, sales - target) x growth_payout_rate ; target is size-tiered + part-time-scaled
#   Acquisition  = landing (% of a new account's first-period sales) + ramp (% for the rest of ~1 quarter)
DEFAULTS = {
    "period_weeks": 4,             # review/pay cadence (4-week period); the bonus is assessed per period
    "window_weeks": 13,            # trailing window used for closure cadence / context
    "holiday_weight": 0.0,         # selling capacity assigned to a federal holiday (0 = a dead day)

    # --- Contribution (line items placed) ---
    "item_rate": 0.10,             # $ earned per invoice LINE ITEM written this period (manager-set dial)

    # --- Growth (grow your book to a size-tiered target) ---
    "growth_large_min": 100000,    # account's prior-year sales >= this -> "large" tier
    "growth_medium_min": 20000,    # ... >= this (and < large) -> "medium"; below -> "small"
    "growth_large_pct": 0.02,      # growth ask for large (mature) accounts
    "growth_medium_pct": 0.05,     # growth ask for medium accounts
    "growth_small_pct": 0.10,      # growth ask for small accounts (most headroom)
    "growth_payout_rate": 0.18,    # $ earned per sales-dollar above the (inflation-adjusted) growth target
    "part_time_factor": 0.5,       # part-time reps' growth STRETCH scaled by this (full-time = 1.0)

    # --- Acquisition (new accounts: an elevated revenue share for ~1 quarter) ---
    "acq_revenue_pct": 0.01,       # bonus = this % of a NEW account's revenue, each period it is "new"
    "acq_ramp_periods": 3,         # an account counts as "new" for ~1 quarter (3 periods), then graduates

    # --- closure decision-support ---
    "fine_amount": 200,            # manager-confirmed behavior-churn fine ($)
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
