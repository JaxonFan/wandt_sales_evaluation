"""Bridge between the DB and the pure engine: load sales lines, settings, overrides; run scorecards."""
import pandas as pd
from sqlalchemy import func
from .engine import compute_period_bonus, compute_annual_review
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
                    M.SalesLine.line_profit, M.SalesLine.extended_price, M.SalesLine.extended_cost,
                    M.SalesLine.qty, M.SalesLine.item_number, M.SalesLine.customer_name,
                    M.SalesLine.sop_number).all()
    df = pd.DataFrame(rows, columns=["account", "associate", "document_date", "line_profit",
                                     "extended_price", "extended_cost", "qty", "item_number", "customer_name",
                                     "sop_number"])
    if len(df):
        df["document_date"] = pd.to_datetime(df["document_date"])
    return df


def collected_set(db):
    """Invoice numbers (sop_number) whose money has been collected, per the latest AR upload."""
    return {s for (s,) in db.query(M.CollectedInvoice.sop_number).all()}


def parse_ar_collected(raw):
    """From a receivables DataFrame, the DEDUPED set of collected invoice numbers — stripped of whitespace,
    blanks dropped, so duplicate rows / padded variants collapse to one. Returns None if the file has no
    recognizable invoice-number column. (Storage also dedups: sop_number is the PK and each upload replaces
    the whole set.)"""
    col = next((c for c in raw.columns
                if str(c).strip().lower() in ("document number", "invoice number", "sop number")), None)
    if col is None:
        return None
    return {str(v).strip() for v in raw[col].dropna() if str(v).strip()}


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
    """customer_number -> its most-common name, with Big5/GBK mojibake decoded (same fix as item names)."""
    rows = db.query(M.SalesLine.customer_number, M.SalesLine.customer_name,
                    func.count(M.SalesLine.id)).group_by(M.SalesLine.customer_number,
                                                         M.SalesLine.customer_name).all()
    best = {}   # customer_number -> (count, name)
    for num, name, cnt in rows:
        if name is None:
            continue
        if num not in best or cnt > best[num][0]:
            best[num] = (cnt, name)
    return {num: decode_desc(nm) for num, (c, nm) in best.items()}


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


# ---------- constrained items (GLOBAL: a limited-stock flag applies to every period) ----------
def get_constrained_items(db):
    return [i for (i,) in db.query(M.ConstrainedItem.item_number).distinct()]


# The most common Chinese characters (Simplified + Traditional), used only to pick between two candidate
# decodings — a byte string can be valid as BOTH Big5 and GBK but mean different things (e.g. Ocean Bay's
# "³ÉÐË³¬ÊÐ" is 成兴超市 in GBK but garbage 傖倓閉庈 in Big5). The correct reading has far more common chars.
_COMMON_HANZI = set(
    "的一是不了人我在有他这中大来上国个到说们为子和你地出道也时年得就那要下以生会自着去之过家学对可她里后"
    "小么心多天而能好都然没日于起还发成事只作当想看文无开手十用主行方又如前所本见经头面公同三已老从动两长"
    "知民样现分将外但身些与高意进把法此实回二理美点月命色向题目何等果么怎给取城市场超记兴隆丰源海鲜肉菜龙"
    "华亚东西南北记食品司龍華亞東發記興隆豐運來好金銀鑫達發達順旺祥福鴻源興隆盛豐盈昌隆記行號店超市場農贸"
    "貿易水果蔬菜魚肉禽蛋副食百货百貨批发批發鮮活鲜活")


def _score_common(s):
    return sum(1 for c in s if c in _COMMON_HANZI)


def decode_desc(s):
    """Recover Chinese item/customer names the ERP export stored as Big5/GBK bytes misread as Latin-1
    (mojibake, e.g. '¤¤ªá¸Ã' -> '中花蜆', '³ÉÐË³¬ÊÐ' -> '成兴超市'). The data is a mix of Big5 (Traditional)
    and GBK (Simplified), and a byte string can be valid as BOTH with different meanings — so we try both and,
    when both succeed, keep the reading with more COMMON Chinese characters (the wrong decode yields rare
    glyphs). Self-correcting: already-correct unicode / plain ASCII / undecodable text passes through as-is,
    so it works on old and newly-uploaded data at display time."""
    if not s:
        return s
    try:
        b = s.encode("latin-1")            # real (already-decoded) Chinese has code points > 255 -> raises
    except (UnicodeEncodeError, AttributeError):
        return s
    best = None
    for enc in ("big5", "gbk"):
        try:
            cand = b.decode(enc)
        except UnicodeDecodeError:
            continue
        sc = _score_common(cand)
        if best is None or sc > best[0]:
            best = (sc, cand)
    return best[1] if best is not None else s


def item_descriptions(db):
    """item_number -> its MOST-COMMON description. The source data sometimes carries a few stray/typo
    descriptions for an item (e.g. 18,372 'MANILA CLAM/REGULAR' lines + 2 'MANILA CLAM/SMALL'); pick the
    one that appears on the most lines so the label is correct and deterministic."""
    rows = db.query(M.SalesLine.item_number, M.SalesLine.item_description,
                    func.count(M.SalesLine.id)).group_by(M.SalesLine.item_number,
                                                          M.SalesLine.item_description).all()
    best = {}   # item -> (count, description)
    for item, desc, cnt in rows:
        if desc is None:
            continue
        if item not in best or cnt > best[item][0]:
            best[item] = (cnt, desc)
    return {item: decode_desc(d) for item, (c, d) in best.items()}


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


def account_quarter_chart(db, account, end, weeks=13, w=300, h=54):
    """SVG-ready data for a this-year-vs-last-year weekly sales chart for ONE account (Big Jumps review).
    Weekly (7-day) bins, oldest->newest, over the last `weeks` weeks ending `end`, and the same span a year
    earlier (364 days). Returns polyline point strings + geometry for the template. Display-only."""
    end = pd.Timestamp(end).normalize()
    df = _lines_cached(db, _data_version(db))
    d = df[df["account"] == account]

    def weekly(anchor):
        lo = anchor - pd.Timedelta(weeks=weeks)
        wdf = d[(d["document_date"] > lo) & (d["document_date"] <= anchor)]
        if not len(wdf):
            return [0.0] * weeks
        wk = ((anchor - wdf["document_date"]).dt.days // 7)            # 0 = most recent 7 days
        sums = wdf.assign(_wk=wk).groupby("_wk")["extended_price"].sum()
        return [float(sums.get(weeks - 1 - i, 0.0)) for i in range(weeks)]   # oldest -> newest

    cur = weekly(end)
    prev = weekly(end - pd.Timedelta(days=364))
    vmax = max(cur + prev + [1.0])
    pad = 3.0

    def pts(vals):
        n = len(vals)
        return " ".join("%.1f,%.1f" % (i / (n - 1) * w, h - pad - (v / vmax) * (h - 2 * pad))
                        for i, v in enumerate(vals))

    return dict(cur_pts=pts(cur), prev_pts=pts(prev), w=w, h=h, vmax=round(vmax),
                period_x=round((weeks - 4) / (weeks - 1) * w, 1),
                cur4=round(sum(cur[-4:])), prev4=round(sum(prev[-4:])),
                cur_total=round(sum(cur)), prev_total=round(sum(prev)))


# ---------- the direct-formula period bonus (Contribution / Growth / Acquisition) ----------
def self_acquired_set(db):
    """Accounts the manager confirmed the rep self-acquired -> eligible for the 1% share.
    Default (no review record) = assigned, so a new account earns the 1% only once confirmed."""
    return {r.account for r in db.query(M.AcquisitionReview).filter(M.AcquisitionReview.rep_won == True)}


def exempt_set(db, period_id):
    """Accounts the manager exempted this period -> removed from GROWTH only (closed/collapsed)."""
    return {a.account for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period_id,
                                                                M.ManagerAction.status == "exempt")}


def featured_new_product_set(db):
    """SKUs the manager confirmed are genuinely new -> their revenue counts at new_product_attribution
    toward growth. Default-absent = not featured (catalog churn pays nothing)."""
    return {r.item_number for r in db.query(M.NewProductReview).filter(M.NewProductReview.featured == True)}


def new_product_candidates(db, new_product_weeks=26):
    """Auto-detected new-SKU candidates (company-wide first-seen within new_product_weeks) for the manager to
    confirm/reject: item_number, description, first-seen, recent revenue, median $/unit, featured flag."""
    rows = db.query(M.SalesLine.item_number, M.SalesLine.item_description, M.SalesLine.document_date,
                    M.SalesLine.qty, M.SalesLine.extended_price).all()
    df = pd.DataFrame(rows, columns=["item", "desc", "date", "qty", "rev"])
    if not len(df):
        return []
    df["date"] = pd.to_datetime(df["date"]); end = df["date"].max().normalize()
    first = df.groupby("item")["date"].min()
    new_items = first[first > end - pd.Timedelta(weeks=new_product_weeks)]
    featured = {r.item_number: r.featured for r in db.query(M.NewProductReview)}
    rec = df[df["date"] > end - pd.Timedelta(weeks=13)]
    out = []
    for it in new_items.index:
        s = df[df["item"] == it]
        out.append(dict(item=it, desc=(decode_desc(s["desc"].dropna().value_counts().index[0]) if s["desc"].notna().any() else ""),
                        first_seen=new_items[it].date(),
                        unit_price=round(float((s["rev"] / s["qty"].replace(0, 1)).median()), 1),
                        rev13=round(float(rec[rec["item"] == it]["rev"].sum())),
                        featured=bool(featured.get(it, False))))
    return sorted(out, key=lambda r: -r["rev13"])


def featured_extra_products(db, existing_items, new_product_weeks=26):
    """Manager-featured products that the auto-detector didn't surface (e.g. added manually by item#, or
    first sold longer ago than new_product_weeks). Returned in the same row shape as new_product_candidates
    so they render in the same table, tagged manual=True (and unknown=True if the item# isn't in sales yet)."""
    existing = set(existing_items)
    featured_items = [r.item_number for r in db.query(M.NewProductReview).filter(M.NewProductReview.featured == True)
                      if r.item_number not in existing]
    if not featured_items:
        return []
    rows = db.query(M.SalesLine.item_number, M.SalesLine.item_description, M.SalesLine.document_date,
                    M.SalesLine.qty, M.SalesLine.extended_price).filter(
                        M.SalesLine.item_number.in_(featured_items)).all()
    df = pd.DataFrame(rows, columns=["item", "desc", "date", "qty", "rev"])
    end = pd.to_datetime(df["date"]).max().normalize() if len(df) else None
    out = []
    for it in featured_items:
        s = df[df["item"] == it] if len(df) else df
        if len(s):
            first_seen = pd.to_datetime(s["date"]).min()
            within = end is not None and first_seen > end - pd.Timedelta(weeks=new_product_weeks)
            out.append(dict(item=it, desc=(decode_desc(s["desc"].dropna().value_counts().index[0]) if s["desc"].notna().any() else ""),
                            first_seen=first_seen.date(),
                            unit_price=round(float((s["rev"] / s["qty"].replace(0, 1)).median()), 1),
                            rev13=round(float(s[pd.to_datetime(s["date"]) > end - pd.Timedelta(weeks=13)]["rev"].sum())),
                            featured=True, manual=True, stale=not within))
        else:
            out.append(dict(item=it, desc="", first_seen=None, unit_price=0, rev13=0,
                            featured=True, manual=True, unknown=True))
    return out


def jump_released_set(db, period_id):
    """Accounts the manager confirmed the rep genuinely won this period -> release the withheld big-jump
    windfall (default for a flagged jump is customer-driven = withheld)."""
    return {a.account for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period_id,
                                                                M.ManagerAction.status == "jump_rep")}


def _dials(s):
    """Pull the bonus dials out of settings into compute_period_bonus kwargs."""
    return dict(
        item_rate=float(s["item_rate"]),
        growth_window_weeks=int(s["growth_window_weeks"]), size_band_count=int(s["size_band_count"]),
        size_band_window_weeks=int(s["size_band_window_weeks"]), size_band_floor=float(s["size_band_floor"]),
        growth_payout_rate=float(s["growth_payout_rate"]),
        glide_alpha=float(s["glide_alpha"]), jump_multiple=float(s["jump_multiple"]),
        min_baseline_ratio=float(s["min_baseline_ratio"]), growth_review_min=float(s["growth_review_min"]),
        mature_smooth_weeks=int(s["mature_smooth_weeks"]), sporadic_gap_weeks=int(s["sporadic_gap_weeks"]),
        cost_inflation_weeks=int(s["cost_inflation_weeks"]),
        growth_quarter_floor=float(s["growth_quarter_floor"]), growth_quarter_min_prior=float(s["growth_quarter_min_prior"]),
        growth_quarter_min_profit=float(s["growth_quarter_min_profit"]),
        growth_annual_floor=float(s["growth_annual_floor"]), growth_annual_min_prior=float(s["growth_annual_min_prior"]),
        new_product_weeks=int(s["new_product_weeks"]), new_product_attribution=float(s["new_product_attribution"]),
        acq_tier_small_max=float(s["acq_tier_small_max"]), acq_tier_medium_max=float(s["acq_tier_medium_max"]),
        acq_flat_small=float(s["acq_flat_small"]), acq_flat_medium=float(s["acq_flat_medium"]),
        acq_flat_large=float(s["acq_flat_large"]), acq_ramp_periods=int(s["acq_ramp_periods"]),
        period_days=PERIOD_DAYS, holiday_weight=float(s["holiday_weight"]))


def run_period_bonus(db, idx=None):
    """Compute the three-piece bonus for grid period `idx` (default current). Returns
    (res, period, settings, nav, as_of). Mid-period, actuals run to the latest data date."""
    s = get_settings(db)
    ww = s["window_weeks"]
    _, idx_cur0, anchor = period_grid(db, ww)
    if idx is None:
        idx = idx_cur0
    period, as_of, idx, idx_min, idx_cur, is_current = resolve_period(db, idx, ww)
    df = _lines_cached(db, _data_version(db))
    _, _, team = attribution_maps(db)
    res = compute_period_bonus(df, period.start_date, period.end_date, team, as_of=as_of,
                               self_acquired=self_acquired_set(db),
                               exempt_accounts=exempt_set(db, period.period_id),
                               jump_released=jump_released_set(db, period.period_id),
                               constrained_item_numbers=get_constrained_items(db),
                               featured_new_products=featured_new_product_set(db), **_dials(s))
    nav = period_nav(idx, idx_min, idx_cur, period, is_current, anchor)
    return res, period, s, nav, as_of


def written_off_set(db):
    """Invoice numbers the manager wrote off as bad debt -> dropped from the unpaid/pending panel."""
    return {s for (s,) in db.query(M.WrittenOffInvoice.sop_number).all()}


def collected_scorecard(db, idx=None):
    """The 'Collected (actual)' bonus for a period: the TARGET bonus (fixed bars from the full run) with the
    realized side gated by collection. Contribution scales by the collected LINE-ITEM fraction (exact);
    Growth + Acquisition scale by the collected REVENUE fraction of the period. This keeps Collected <= Target
    and converges to Target as invoices collect (the AR file only covers 2025-06+, so bars must NOT be
    recomputed on collected-only data or the pre-coverage baseline collapses). Returns (rows_by_rep, period, nav)."""
    res, period, s, nav, as_of = run_period_bonus(db, idx)
    sc = res["scorecards"].set_index("associate")
    df = _lines_cached(db, _data_version(db))
    coll = collected_set(db)
    lo, hi = pd.Timestamp(period.start_date), pd.Timestamp(period.end_date)
    w = df[(df["document_date"] >= lo) & (df["document_date"] <= hi)]
    w_coll = w[w["sop_number"].astype(str).isin(coll)]
    out = {}
    for rep in sc.index:
        c = sc.loc[rep]
        g = w[w["associate"] == rep]; gc = w_coll[w_coll["associate"] == rep]
        tot_rev = float(g["extended_price"].sum()); col_rev = float(gc["extended_price"].sum())
        tot_items = len(g); col_items = len(gc)
        fr = (col_rev / tot_rev) if tot_rev else 1.0        # revenue collection fraction (growth/acq)
        fi = (col_items / tot_items) if tot_items else 1.0  # line-item collection fraction (contribution)
        contribution = float(c["contribution_bonus"]); growth = float(c["growth_bonus"]); acq = float(c["acquisition_bonus"])
        col_total = contribution * fi + growth * fr + acq * fr
        out[rep] = dict(associate=rep,
                        contribution=contribution, contribution_collected=contribution * fi,
                        growth=growth, growth_collected=growth * fr,
                        acquisition=acq, acquisition_collected=acq * fr,
                        total=float(c["total_bonus"]), total_collected=col_total,
                        collected_rev=round(col_rev), billed_rev=round(tot_rev),
                        collected_pct=(round(col_total / float(c["total_bonus"]) * 100) if c["total_bonus"] else 100))
    return out, period, nav


def collections_payrun(db):
    """Per-rep, per-period Target vs Collected bonus + cumulative Paid (Award) + Payable-now (the progressive
    true-up). Sum of `payable` across open periods = this cycle's payroll; the per-period rows are the
    'previous period + this period' breakdown. Returns (rows_by_rep, meta)."""
    s = get_settings(db)
    ww = s["window_weeks"]
    idx_min, idx_cur, anchor = period_grid(db, ww)
    _, _, team = attribution_maps(db)
    awards = {(a.period_id, a.associate): float(a.award_amount or 0.0) for a in db.query(M.Award).all()}
    by_rep = {rep: [] for rep in team}
    for idx in range(idx_cur, idx_min - 1, -1):
        cards, period, _ = collected_scorecard(db, idx)
        for rep in team:
            c = cards.get(rep)
            if c is None:
                continue
            tb = c["total"]; cb = c["total_collected"]
            paid = awards.get((period.period_id, rep), 0.0)
            by_rep[rep].append(dict(idx=idx, n=idx - idx_min + 1, start=period.start_date, end=period.end_date,
                                    period_id=period.period_id, target=round(tb), collected=round(cb),
                                    paid=round(paid), payable=round(cb - paid),
                                    collected_pct=(round(cb / tb * 100) if tb else 100)))
    return by_rep, dict(idx_min=idx_min, idx_cur=idx_cur)


def unpaid_accounts(db, associate):
    """A rep's OUTSTANDING (billed-but-uncollected, not written-off) invoices grouped by account, with aging
    buckets. Shows which accounts owe money -> hence the rep's pending (unpaid) bonus."""
    df = _lines_cached(db, _data_version(db))
    coll = collected_set(db)
    woff = written_off_set(db)
    names = customer_names(db)
    _, hi = data_bounds(db)
    d = df[(df["associate"] == associate) & (~df["sop_number"].astype(str).isin(coll))
           & (~df["sop_number"].astype(str).isin(woff))].copy()
    # only invoices within AR coverage (before 2025-06 there's no receivables data -> not counted as "unpaid")
    d = d[d["document_date"] >= pd.Timestamp("2025-06-01")]
    def _bucket(days):
        return "0-30" if days <= 30 else ("31-60" if days <= 60 else ("61-90" if days <= 90 else "90+"))
    rows = []
    for acct, g in d.groupby("account"):
        oldest = g["document_date"].min()
        days = (pd.Timestamp(hi) - oldest).days
        # per-invoice detail (group the account's lines on the invoice #), oldest-first
        inv = (g.groupby("sop_number").agg(date=("document_date", "min"), amount=("extended_price", "sum"),
                                           lines=("item_number", "size")).reset_index().sort_values("date"))
        invoice_list = [dict(sop_number=r.sop_number, date=r.date.date().isoformat(),
                             amount=round(float(r.amount)), lines=int(r.lines),
                             days=(pd.Timestamp(hi) - r.date).days, bucket=_bucket((pd.Timestamp(hi) - r.date).days))
                        for r in inv.itertuples()]
        rows.append(dict(account=acct, name=names.get(acct, acct), outstanding=round(float(g["extended_price"].sum())),
                         invoices=int(g["sop_number"].nunique()), oldest_days=days, bucket=_bucket(days),
                         invoice_list=invoice_list))
    rows.sort(key=lambda r: -r["outstanding"])
    return rows, round(sum(r["outstanding"] for r in rows))


def annual_exempt_set(db):
    """Accounts the manager exempted in ANY period -> removed from the annual growth track (closed/collapsed).
    The annual track is rolling (not per-period), so an exemption applies regardless of when it was set."""
    return {a.account for a in db.query(M.ManagerAction).filter(M.ManagerAction.status == "exempt")}


def run_annual_review(db):
    """Compute the Annual Review track (sporadic accounts, rolling trailing 12 months vs prior 12 months).
    Rolling / as-of the latest data date — independent of the 4-week period grid. Returns (res, as_of, settings)."""
    s = get_settings(db)
    _, idx_cur0, _ = period_grid(db, s["window_weeks"])
    period, as_of, *_ = resolve_period(db, idx_cur0, s["window_weeks"])
    df = _lines_cached(db, _data_version(db))
    _, _, team = attribution_maps(db)
    res = compute_annual_review(df, as_of, team,
                                exempt_accounts=annual_exempt_set(db),
                                featured_new_products=featured_new_product_set(db), **_dials(s))
    return res, as_of, s


def compute_annual_goal(db, associate):
    """Rep-facing Annual Review payload: this rep's infrequent accounts on a rolling trailing-12-month basis,
    with the annual growth bonus they're earning. Mirrors compute_rep_goal but on the annual cadence."""
    res, as_of, s = run_annual_review(db)
    card = next((c for c in res["scorecards"].to_dict("records") if c["associate"] == associate), None)
    names = customer_names(db)
    accounts = res["accounts"]
    rows = []
    if len(accounts):
        sub = accounts[accounts["associate"] == associate]
        for _, r in sub.iterrows():
            rows.append({"customer": names.get(r["account"], r["account"]), "status": r["status"],
                         "sales": int(r["sales"]), "target": (int(r["target"]) if pd.notna(r["target"]) else None),
                         "perf": (int(r["perf"]) if pd.notna(r["perf"]) else None),
                         "acct12": int(r["acct12"]), "prior12": int(r["prior12"]),
                         "yoy": (int(r["yoy"]) if pd.notna(r["yoy"]) else None)})
        rows.sort(key=lambda x: -(x["acct12"] or 0))
    actual = float(card["annual_actual"]) if card else 0.0
    target = float(card["annual_target"]) if card else 0.0
    bonus = float(card["annual_growth_bonus"]) if card else 0.0
    return {"associate": associate, "as_of": as_of, "card": card, "rows": rows,
            "actual": actual, "target": target, "bonus": bonus,
            "pct": (actual / target * 100) if target else 0.0}


def compute_rep_goal(db, associate, idx=None):
    """The rep-facing dashboard payload: one target, where they are vs a calendar-aware pace,
    run-rate to finish, the three bonus pieces, new accounts, and accounts-to-watch."""
    res, period, s, nav, as_of = run_period_bonus(db, idx)
    card = next((c for c in res["scorecards"].to_dict("records") if c["associate"] == associate), None)
    df = _lines_cached(db, _data_version(db))
    names = customer_names(db)
    period_end = pd.Timestamp(period.end_date)

    # growth = recent revenue vs the bar: cost-adjusted last-year (today's cost + last-year profit) x your size tier's real move.
    actual = float(card["growth_actual"]) if card else 0.0          # your recent revenue (period-equivalent)
    target = float(card["growth_target"]) if card else 0.0          # the bar to beat
    last_year = float(card["growth_base_raw"]) if card else 0.0     # cost-adjusted last-year (today's cost + last-year profit)
    lifted = float(card["growth_base"]) if card else 0.0            # after the size-tier real-market move
    lift_pct = (lifted / last_year - 1) * 100 if last_year else 0.0

    rep_accounts = res["accounts"]
    rep_accounts = rep_accounts[rep_accounts["associate"] == associate] if len(rep_accounts) else rep_accounts
    new_accounts = []
    gated_accounts = []
    if len(rep_accounts):
        for _, r in rep_accounts[rep_accounts["status"].isin(["landing", "ramp"])].iterrows():
            new_accounts.append({"customer": names.get(r["account"], r["account"]),
                                 "status": r["status"], "sales": round(float(r["rep_quarter_sales"]))})
        # accounts whose growth didn't count this period — either quarter PROFIT shrinking, or flat/down YoY
        for _, r in rep_accounts[rep_accounts.get("gated", False) == True].iterrows():
            annual = (r.get("gate_reason") == "annual_flat")
            if annual:
                base = float(r["ann_prior_rev"]) or 1.0
                gated_accounts.append({"customer": names.get(r["account"], r["account"]), "annual": True,
                                       "q_recent_rev": int(r["ann_recent_rev"]), "q_redline": int(r["ann_prior_rev"]),
                                       "qoy_pct": round((float(r["ann_recent_rev"]) / base - 1) * 100)})
            else:
                pp = float(r["q_prior_profit"]) or 1.0
                gated_accounts.append({"customer": names.get(r["account"], r["account"]), "annual": False,
                                       "q_recent_rev": int(r["q_recent_rev"]), "q_redline": int(r["q_redline"]),
                                       "q_recent_profit": int(r["q_recent_profit"]), "q_prior_profit": int(r["q_prior_profit"]),
                                       "qoy_pct": round((float(r["q_recent_profit"]) / pp - 1) * 100)})

    # accounts to watch = silent accounts in this rep's book (touched in the trailing window)
    book_cut = period_end - pd.Timedelta(weeks=s["window_weeks"])
    rep_book = set(df[(df["associate"] == associate) & (df["document_date"] > book_cut)]["account"]) if len(df) else set()
    watch = [w for w in flag_silent_accounts(db) if w["account"] in rep_book][:6]

    collected = collected_scorecard(db, idx)[0].get(associate) if db.query(M.CollectedInvoice).count() else None
    return {"associate": associate, "card": card, "period": period, "nav": nav,
            "actual": actual, "target": target, "pct": (actual / target * 100) if target else 0.0,
            "last_year": last_year, "lift_pct": lift_pct, "collected": collected,
            "new_accounts": new_accounts, "gated_accounts": gated_accounts, "watch": watch}


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
            rep_rev = group.dropna(subset=["associate"]).groupby("associate")["extended_price"].sum()
            rep = rep_rev.idxmax() if len(rep_rev) else ""
            records.append({"account": account_id, "customer": names.get(account_id, account_id),
                            "rep": rep, "median_gap_days": round(float(median_gap_days), 1),
                            "days_silent": int(days_silent)})
    return sorted(records, key=lambda r: -r["days_silent"])
