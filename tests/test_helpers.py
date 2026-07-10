"""Unit tests for the engine's pure helper functions (exact math)."""
import pandas as pd
import pytest

from app import engine
from builders import df_from, mk


def test_exclude_constrained_items():
    df = df_from(mk("A", "Rep A", "ITEM1", "2025-01-01", "2025-01-10", 1, 100, 80),
                 mk("A", "Rep A", "ITEM2", "2025-01-01", "2025-01-10", 1, 100, 80))
    out = engine.exclude_constrained_items(df, {"ITEM1"})
    assert set(out["item_number"]) == {"ITEM2"}
    # no-op on empty set (returns the frame unchanged)
    assert len(engine.exclude_constrained_items(df, set())) == len(df)


def test_cny_aligned_offset_days():
    # A period straddling CNY 2026-02-17 aligns the year-ago window to the PRIOR CNY (2025-01-29),
    # not a flat 364 days.
    off = engine.cny_aligned_offset_days(pd.Timestamp("2026-02-05"), pd.Timestamp("2026-03-04"))
    assert off == (pd.Timestamp("2026-02-17") - pd.Timestamp("2025-01-29")).days
    assert off != 364
    # A period nowhere near CNY -> the default 364.
    assert engine.cny_aligned_offset_days(pd.Timestamp("2025-06-01"), pd.Timestamp("2025-06-29")) == 364


def test_glide_levels_is_ewma_over_prior_windows():
    # window_end excludes the current 4-week bucket; prior buckets (oldest->newest) = [100, 200].
    # EWMA(alpha=0.3, adjust=False): y0=100, y1 = 0.7*100 + 0.3*200 = 130.
    we = pd.Timestamp("2025-06-29")
    df = df_from(
        mk("A", "Rep A", "X", "2025-04-20", "2025-04-20", 1, 100, 0),   # older bucket (2)
        mk("A", "Rep A", "X", "2025-05-20", "2025-05-20", 1, 200, 0),   # recent prior bucket (1)
    )
    levels = engine._glide_levels(df, we, alpha=0.3, step_weeks=4, value_col="extended_price")
    assert levels["A"] == pytest.approx(130.0, abs=1e-6)


def test_cost_inflation_factor_matched_basket():
    # 12 items sold in both windows; recent unit cost = 1.10 x base unit cost -> factor ~1.10 (within clamp).
    base, recent = [], []
    for i in range(12):
        base += mk("A", "Rep A", f"I{i}", "2024-01-01", "2024-01-01", 1, 100, 100)     # base cost 100
        recent += mk("A", "Rep A", f"I{i}", "2025-01-01", "2025-01-01", 1, 100, 110)   # recent cost 110
    df = df_from(base, recent)
    f = engine._cost_inflation_factor(df, pd.Timestamp("2024-12-01"), pd.Timestamp("2025-02-01"),
                                      pd.Timestamp("2023-12-01"), pd.Timestamp("2024-02-01"))
    assert f == pytest.approx(1.10, abs=0.02)
    # Too few matched items -> neutral 1.0.
    small = df_from(mk("A", "Rep A", "ONLY", "2024-01-01", "2024-01-01", 1, 100, 100),
                    mk("A", "Rep A", "ONLY", "2025-01-01", "2025-01-01", 1, 100, 110))
    assert engine._cost_inflation_factor(small, pd.Timestamp("2024-12-01"), pd.Timestamp("2025-02-01"),
                                         pd.Timestamp("2023-12-01"), pd.Timestamp("2024-02-01")) == 1.0


def test_cost_adjusted_baseline():
    # baseline = cost*cost_factor + profit (per account) over the window.
    df = df_from(mk("A", "Rep A", "X", "2024-01-01", "2024-01-28", 7, 100, 80))  # 4 lines: cost 80, profit 20
    out = engine._cost_adjusted_baseline(df, pd.Timestamp("2023-12-31"), pd.Timestamp("2024-01-29"),
                                         cost_factor=1.5, scale=1.0)
    # cost 4*80=320 * 1.5 = 480 ; profit 4*20=80 ; total 560
    assert out["A"] == pytest.approx(560.0, abs=1e-6)


def test_size_band_factors_uses_market_not_own_ratio():
    # 12 flat accounts (recent==baseline) + 1 grower at 1.5x. The market factor should be ~1.0, and the
    # grower must NOT get credited its own 1.5 (that's the de-trend's whole point).
    base = pd.Series({f"F{i}": 4000.0 + 100 * i for i in range(12)})
    recent = base.copy()
    base["GROW"] = 4000.0
    recent["GROW"] = 6000.0
    factors, overall = engine._size_band_factors(base, recent, n_bands=5)
    assert overall == pytest.approx(1.0, abs=0.1)
    assert factors["GROW"] < 1.5      # de-trended toward the market, not its own ratio
