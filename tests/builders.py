"""Synthetic-data builders for engine tests.

The engine is a pure function over a 9-column DataFrame of item-level sales lines, so tests build a small
synthetic book and assert behavior. Key trick: a test account's growth only shows against a market bar of
~1.0, so we surround it with a `flat_book` of steady accounts (see `_size_band_factors`).
"""
import pandas as pd

from app.config import DEFAULTS
from app.engine import compute_period_bonus

# A clean 4-week (28-day) period well away from Lunar New Year, with ~2 years of prior history so the
# year-ago / annual windows exist.
PERIOD_START = pd.Timestamp("2025-06-01")
PERIOD_END = pd.Timestamp("2025-06-29")
AS_OF = PERIOD_END
HISTORY_START = pd.Timestamp("2023-05-01")
TEAM = ["Rep A"]

COLS = ["account", "associate", "document_date", "line_profit", "extended_price",
        "extended_cost", "qty", "item_number", "customer_name"]


def mk(account, associate, item, start, end, step_days, price, cost, qty=1.0):
    """One sales line every `step_days` from start..end (inclusive) for one account/item."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    rows, d = [], start
    while d <= end:
        rows.append(dict(account=account, associate=associate, document_date=d,
                         line_profit=price - cost, extended_price=price, extended_cost=cost,
                         qty=qty, item_number=item, customer_name=account))
        d += pd.Timedelta(days=step_days)
    return rows


def df_from(*specs):
    """Assemble one or more row-lists (from mk) into the engine's expected DataFrame."""
    rows = [r for spec in specs for r in spec]
    df = pd.DataFrame(rows, columns=COLS)
    df["document_date"] = pd.to_datetime(df["document_date"])
    return df


def flat_book(associate="Rep A", start=HISTORY_START, end=AS_OF, n=12, weekly=1000.0, margin=0.15,
              prefix="FLAT"):
    """n accounts each ordering the SAME amount weekly for the whole history -> market bar ~1.0 (flat YoY,
    at target). Sizes are lightly staggered so the size bands can form."""
    specs = []
    for i in range(n):
        w = weekly * (1.0 + 0.05 * i)           # gentle size spread, still flat YoY per account
        specs += mk(f"{prefix}{i:02d}", associate, "BG", start, end, 7, w, w * (1 - margin))
    return specs


_DIAL_KEYS = [
    "item_rate", "growth_window_weeks", "size_band_count", "growth_payout_rate", "glide_alpha",
    "jump_multiple", "min_baseline_ratio", "mature_smooth_weeks", "sporadic_gap_weeks",
    "cost_inflation_weeks", "growth_quarter_floor", "growth_quarter_min_prior", "growth_quarter_min_profit",
    "growth_annual_floor", "growth_annual_min_prior", "new_product_weeks", "new_product_attribution",
    "acq_tier_small_max", "acq_tier_medium_max", "acq_flat_small", "acq_flat_medium", "acq_flat_large",
    "acq_ramp_periods", "holiday_weight",
]


def dials(**overrides):
    """The live config dials as compute_period_bonus kwargs, with per-test overrides."""
    d = {k: DEFAULTS[k] for k in _DIAL_KEYS if k in DEFAULTS}
    d["period_days"] = 28
    d.update(overrides)
    return d


def run_period(df, self_acquired=(), exempt_accounts=(), jump_released=(),
               constrained_item_numbers=(), featured_new_products=(), team=None, **dial_overrides):
    """Run the live period engine on `df` for the standard test period."""
    return compute_period_bonus(
        df, PERIOD_START, PERIOD_END, team or TEAM, as_of=AS_OF,
        self_acquired=frozenset(self_acquired), exempt_accounts=frozenset(exempt_accounts),
        jump_released=frozenset(jump_released),
        constrained_item_numbers=frozenset(constrained_item_numbers),
        featured_new_products=frozenset(featured_new_products),
        **dials(**dial_overrides),
    )


def card(res, rep="Rep A"):
    """The scorecard row dict for one rep."""
    sc = res["scorecards"]
    return sc[sc["associate"] == rep].iloc[0].to_dict()


def acct_row(res, account, rep="Rep A"):
    """The per-(rep, account) detail row, or None if the account wasn't scored this period."""
    a = res["accounts"]
    m = a[(a["account"] == account) & (a["associate"] == rep)]
    return m.iloc[0].to_dict() if len(m) else None
