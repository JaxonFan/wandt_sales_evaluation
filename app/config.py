"""App configuration & default metric dials (overridable via the settings table / env)."""
import os

# Database — SQLite for local dev; set DATABASE_URL to the Postgres URL (RDS) in prod.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./app/data/scorecard.db")

# Session signing key (override in prod via the wandt/SECRET_KEY secret)
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Roster roles that are SCORED as sales reps (managers / blank are excluded everywhere).
SALES_ROLES = {"full time sales", "part time sales"}

# Metric dials (defaults from the 2-year analysis; the engine reads overrides from settings).
DEFAULTS = {
    "window_weeks": 13,            # trailing measurement window ("last 3 months")
    "period_weeks": 4,            # review/pay cadence (4-week period)
    "provisional_min_weeks": 13,   # history before an account leaves 'new' for a prior-quarter baseline
    "defend_pct": 0.35,           # share of the fixed bonus pool to Defend; rest is Grow (Grow-heavy)
    "acquisition_pct": 0.02,       # acquisition ramp bonus = this % of a new account's profit
    "acquisition_ramp_periods": 3, # a new account earns the ramp bonus for ~1 quarter, then graduates
    "familiar_min_weeks": 4,       # 'experienced' on an account if handled it >= this many distinct weeks last year
    "familiar_max_gap_weeks": 26,  # ...AND last touch within this many weeks (else ramping)
    "holiday_weight": 0.0,         # selling capacity assigned to a federal holiday (0 = a dead day)
    "fine_amount": 200,            # manager-confirmed behavior-churn fine ($)
    "bonus_pool": 1000,            # fixed Defend+Grow pool to split each period ($)
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
