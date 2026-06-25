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
#   Growth       = max(0, trailing-quarter sales - target) x growth_payout_rate x (4/13)
#                  target = own last-year quarter x (typical move for accounts your size) x (1 + stretch)
#   Acquisition  = acq_revenue_pct x a NEW account's revenue, for ~1 quarter
DEFAULTS = {
    "period_weeks": 4,             # review/pay cadence (4-week period); the bonus is assessed per period
    "window_weeks": 13,            # trailing window for closure cadence / context
    "holiday_weight": 0.0,         # selling capacity assigned to a federal holiday (0 = a dead day)

    # --- Contribution (line items placed) ---
    "item_rate": 0.10,             # $ earned per invoice LINE ITEM written this period (manager-set dial)

    # --- Growth (beat what accounts your size are doing, measured over a trailing quarter) ---
    "growth_window_weeks": 13,     # measure growth on the trailing 13 weeks (smooths single-period lumps)
    "size_band_count": 5,          # group accounts into this many size bands for the "typical move" de-trend
    "growth_stretch_pct": 0.03,    # the extra above your size band's typical move that you must beat
    "growth_payout_rate": 0.045,   # $ earned per sales-dollar above target (calibrated to ~$3k/period total)
    "growth_cap_multiple": 2.0,    # an account counts toward growth up to this x its target; excess is held back
    "growth_review_min": 20000,    # held-back above this $ flags the account on the manager's review list
    "full_time_hours": 8.0,        # a full work day; each rep's FTE = min(1, their hours/day ÷ this) scales their stretch
    "part_time_factor": 0.5,       # fallback STRETCH factor for a part-time rep with no hours on file

    # --- Acquisition (new accounts: an elevated revenue share for ~1 quarter) ---
    "acq_revenue_pct": 0.01,       # bonus = this % of a NEW account's revenue, each period it is "new"
    "acq_ramp_periods": 3,         # an account counts as "new" for ~1 quarter (3 periods), then graduates

    # --- closure decision-support ---
    "fine_amount": 200,            # manager-confirmed behavior-churn fine ($)
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
