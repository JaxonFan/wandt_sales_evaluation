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

    # --- Growth (beat what accounts your size are doing, measured over the trailing 4 weeks) ---
    "growth_window_weeks": 4,      # measure growth on the trailing 4 weeks (= the pay period; jumps surface to review)
    "size_band_count": 5,          # group accounts into this many size bands for the "typical move" de-trend
    "growth_stretch_pct": 0.03,    # the extra above your size band's typical move that you must beat
    "growth_payout_rate": 0.045,   # $ earned per sales-dollar above target (calibrated to ~$3k/period total)
    "glide_alpha": 0.20,           # how fast a level-shifted account's bar catches up to its new run-rate (0..1); ~0.2 = a quarter of memory
    "min_baseline_ratio": 0.80,    # year-ago window must be >= this x recent to use it (else glide, not YoY) — high = lean on the smoother glide bar
    "jump_multiple": 2.0,          # flag a DOUBLING: recent >= this x its bar (100%+ over) -> whole over-bar amount withheld for manager review
    "growth_cap_multiple": 2.0,    # (legacy alias; superseded by jump_multiple)
    "growth_review_min": 10000,    # (deprecated — jumps now flag on the doubling alone, no dollar floor)

    # --- Acquisition (new accounts: an elevated revenue share for ~1 quarter) ---
    "acq_revenue_pct": 0.01,       # bonus = this % of a NEW account's revenue, each period it is "new"
    "acq_ramp_periods": 3,         # an account counts as "new" for ~1 quarter (3 periods), then graduates

    # --- closure decision-support ---
    "fine_amount": 200,            # manager-confirmed behavior-churn fine ($)
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
