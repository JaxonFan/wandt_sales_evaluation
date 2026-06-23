"""Bridge between the DB and the pure engine: load sales lines, settings, overrides; run scorecards."""
import pandas as pd
from sqlalchemy import func
from .engine import compute_wandt
from . import models as M
from .config import DEFAULTS, SALES_ROLES

_LINES_CACHE = {}     # data_version -> lines DataFrame
_ENGINE_CACHE = {}    # key -> result dict
_ENGINE_CACHE_MAX = 64
PERIOD_DAYS = 28


# ---------- attribution (batch number -> sales rep) ----------
def attribution_maps(db):
    """Build (prefix_map, variant_map, sales_team) from the associates table."""
    associates = db.query(M.Associate).all()
    prefix_map, variant_map, sales_team = {}, {}, []
    for a in associates:
        if a.batch_initial:
            prefix_map[a.batch_initial.strip().upper()] = a.name
        if a.other_names:
            variant_map[a.other_names.strip().upper()] = a.name
        if a.name and (a.role or "").strip().lower() in SALES_ROLES and (a.status or "").strip().lower() == "active":
            sales_team.append(a.name)
    return prefix_map, variant_map, sorted(set(sales_team))


def resolve_associate(batch_number, prefix_map, variant_map):
    text = str(batch_number).strip().upper()
    if text in variant_map:
        return variant_map[text]
    return prefix_map.get(text[:2])


# ---------- data loading ----------
def load_lines_df(db):
    rows = db.query(M.SalesLine.customer_number, M.SalesLine.associate, M.SalesLine.document_date,
                    M.SalesLine.line_profit, M.SalesLine.extended_price, M.SalesLine.qty,
                    M.SalesLine.item_number, M.SalesLine.customer_name).all()
    df = pd.DataFrame(rows, columns=["account", "associate", "document_date", "line_profit",
                                     "extended_price", "qty", "item_number", "customer_name"])
    if len(df):
        df["document_date"] = pd.to_datetime(df["document_date"])
    return df


def get_settings(db):
    s = dict(DEFAULTS)
    for row in db.query(M.Setting).all():
        v = row.value
        try:
            if str(v).lower() in ("true", "false"):
                v = str(v).lower() == "true"
            elif "." in str(v):
                v = float(v)
            else:
                v = int(v)
        except Exception:
            pass
        s[row.key] = v
    return s


def customer_names(db):
    return {r.customer_number: r.customer_name
            for r in db.query(M.SalesLine.customer_number, M.SalesLine.customer_name).distinct()}


# ---------- period grid (fixed 4-week buckets anchored to the latest data) ----------
def data_bounds(db):
    lo = db.query(func.min(M.SalesLine.document_date)).scalar()
    hi = db.query(func.max(M.SalesLine.document_date)).scalar()
    return pd.to_datetime(lo), pd.to_datetime(hi)


def get_anchor(db, hi):
    row = db.get(M.Setting, "period_anchor")
    if row is None:
        row = M.Setting(key="period_anchor", value=hi.date().isoformat())
        db.add(row); db.commit()
    return pd.to_datetime(row.value)


def period_end(anchor, idx):
    return anchor + pd.Timedelta(days=PERIOD_DAYS * idx)


def period_grid(db, window_weeks):
    lo, hi = data_bounds(db)
    anchor = get_anchor(db, hi)
    idx_cur = int((hi - anchor).days // PERIOD_DAYS)
    need = pd.Timedelta(weeks=52 + window_weeks)
    idx_min = idx_cur
    while period_end(anchor, idx_min - 1) - need >= lo:
        idx_min -= 1
    return idx_min, idx_cur, anchor


def resolve_period(db, idx, window_weeks):
    _, hi = data_bounds(db)
    idx_min, idx_cur, anchor = period_grid(db, window_weeks)
    idx = max(idx_min, min(idx_cur, int(idx)))
    end = period_end(anchor, idx)
    as_of = min(end, hi)
    is_current = (idx == idx_cur)
    p = db.query(M.Period).filter(M.Period.end_date == end.date()).first()
    if p is None:
        p = M.Period(end_date=end.date())
        db.add(p)
    p.start_date = (end - pd.Timedelta(days=PERIOD_DAYS - 1)).date()
    p.window_start = (end - pd.Timedelta(weeks=window_weeks)).date()
    p.window_end = end.date()
    p.baseline_window_start = (end - pd.Timedelta(weeks=52 + window_weeks)).date()
    p.baseline_window_end = (end - pd.Timedelta(weeks=52)).date()
    p.status = "open" if is_current else "closed"
    db.commit()
    return p, as_of, idx, idx_min, idx_cur, is_current


def _rel(idx, idx_cur):
    return "current" if idx == idx_cur else ("last period" if idx == idx_cur - 1 else f"{idx_cur - idx} periods ago")


def period_options(anchor, idx_min, idx_cur):
    opts = []
    for i in range(idx_cur, idx_min - 1, -1):
        end = period_end(anchor, i)
        opts.append({"idx": i, "n": i - idx_min + 1,
                     "start": (end - pd.Timedelta(days=PERIOD_DAYS - 1)).date(), "end": end.date(),
                     "rel": _rel(i, idx_cur), "is_current": i == idx_cur})
    return opts


def period_nav(idx, idx_min, idx_cur, period, is_current, anchor=None):
    return {"idx": idx, "idx_min": idx_min, "idx_cur": idx_cur, "is_current": is_current,
            "n": idx - idx_min + 1, "total": idx_cur - idx_min + 1, "rel": _rel(idx, idx_cur),
            "start": period.start_date, "end": period.end_date,
            "prev_idx": idx - 1 if idx > idx_min else None,
            "next_idx": idx + 1 if idx < idx_cur else None,
            "options": period_options(anchor, idx_min, idx_cur) if anchor is not None else []}


# ---------- overrides & constrained items ----------
def get_overrides(db, period_id):
    return {a.account: {"status": a.status, "rebaseline_value": a.rebaseline_value}
            for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period_id).all()}


def get_constrained_items(db, period_id):
    return [c.item_number for c in db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period_id).all()]


# ---------- engine runner (with caching) ----------
def _data_version(db):
    n, mx = db.query(func.count(M.SalesLine.id), func.max(M.SalesLine.imported_at)).one()
    return (int(n or 0), str(mx))


def _lines_cached(db, ver):
    df = _LINES_CACHE.get(ver)
    if df is None:
        _LINES_CACHE.clear()
        df = load_lines_df(db)
        _LINES_CACHE[ver] = df
    return df


def run_engine(db, idx=None):
    s = get_settings(db)
    ww = s["window_weeks"]
    _, idx_cur0, anchor = period_grid(db, ww)
    if idx is None:
        idx = idx_cur0
    period, as_of, idx, idx_min, idx_cur, is_current = resolve_period(db, idx, ww)
    ver = _data_version(db)
    _, _, sales_team = attribution_maps(db)
    overrides = get_overrides(db, period.period_id)
    exempt = [acc for acc, ov in overrides.items() if ov["status"] == "exempt"]
    constrained = get_constrained_items(db, period.period_id)
    ssig = (ww, s["provisional_min_weeks"], s["defend_pct"], s["acquisition_pct"],
            s["acquisition_ramp_periods"], s["bonus_pool"], s["familiar_min_weeks"],
            s["familiar_max_gap_weeks"], s["holiday_weight"], tuple(sorted(exempt)), tuple(sorted(constrained)))
    key = (idx, ver, ssig, tuple(sales_team))
    res = _ENGINE_CACHE.get(key)
    if res is None:
        df = _lines_cached(db, ver)
        res = compute_wandt(df, as_of, sales_team, window_weeks=ww,
                            provisional_min_weeks=s["provisional_min_weeks"], defend_pct=s["defend_pct"],
                            acquisition_pct=s["acquisition_pct"], acquisition_ramp_periods=s["acquisition_ramp_periods"],
                            bonus_pool=s["bonus_pool"], constrained_item_numbers=constrained,
                            familiar_min_weeks=s["familiar_min_weeks"], familiar_max_gap_weeks=s["familiar_max_gap_weeks"],
                            holiday_weight=s["holiday_weight"], exempt_accounts=exempt)
        if len(_ENGINE_CACHE) >= _ENGINE_CACHE_MAX:
            _ENGINE_CACHE.clear()
        _ENGINE_CACHE[key] = res
    if period.market_drift != res["market_drift"]:
        period.market_drift = res["market_drift"]; db.commit()
    nav = period_nav(idx, idx_min, idx_cur, period, is_current, anchor)
    return res, period, s, nav


# ---------- bonus split & decision support ----------
def allocate_bonus(cards, pool, defend_pct):
    """3-way split: Defend (pool*defend_pct by profit contribution) + Grow (rest by $ above target)
    + Acquire (commission-style on new-account profit, separate from the pool)."""
    defend_pct = max(0.0, min(1.0, float(defend_pct)))
    pool = float(pool or 0)
    contribs = {c["associate"]: max(0.0, float(c.get("defend_dollars", 0) or 0)) for c in cards}
    grows = {c["associate"]: max(0.0, float(c.get("grow_dollars", 0) or 0)) for c in cards}
    tot_c, tot_g = sum(contribs.values()), sum(grows.values())
    defend_pool, grow_pool = pool * defend_pct, pool * (1 - defend_pct)
    out = {}
    for c in cards:
        a = c["associate"]
        defend = defend_pool * contribs[a] / tot_c if tot_c else 0.0
        grow = grow_pool * grows[a] / tot_g if tot_g else 0.0
        acquire = float(c.get("acquisition_bonus", 0) or 0)
        out[a] = {"defend": defend, "grow": grow, "acquire": acquire, "total": defend + grow + acquire,
                  "contribution": float(c.get("defend_dollars", 0) or 0),
                  "grow_dollars": float(c.get("grow_dollars", 0) or 0),
                  "contribution_share": (contribs[a] / tot_c * 100) if tot_c else 0.0}
    return out


def proposed_rebaseline(res, account_id):
    acct = res["account"]
    if len(acct) and account_id in acct.index:
        return round(float(acct.loc[account_id, "recent_profit"] or 0))
    return 0


def flag_silent_accounts(db, gap_multiple=3.0, min_orders=5):
    """Accounts silent > gap_multiple x their own median inter-order gap — closure candidates."""
    df = load_lines_df(db)
    if not len(df):
        return []
    as_of = df["document_date"].max().normalize()
    names = customer_names(db)
    records = []
    for account_id, group in df.groupby("account"):
        order_dates = pd.Series(sorted(group["document_date"].dt.normalize().unique()))
        if len(order_dates) < min_orders:
            continue
        median_gap_days = order_dates.diff().dt.days.median()
        days_silent = (as_of - order_dates.iloc[-1]).days
        if median_gap_days and days_silent > gap_multiple * median_gap_days:
            records.append({"account": account_id, "customer": names.get(account_id, account_id),
                            "median_gap_days": round(float(median_gap_days), 1), "days_silent": int(days_silent)})
    return sorted(records, key=lambda r: -r["days_silent"])
