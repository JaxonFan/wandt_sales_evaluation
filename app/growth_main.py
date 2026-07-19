"""Standalone GROWTH BACKTEST service (own URL, separate ECS service via APP_MODULE=app.growth_main:app).

The next-generation scorecard, kept apart from the main app: the rep-netted cumulative profit-growth model
plus the two retained pieces (Contribution, Acquisition), pay-on-collection, a manager guide, the new-account
review tab, and the FIXED importer (keeps every invoice so account baselines are complete).

The growth rule (the manager's): for each rep, sum the profit gap of ALL accounts they work — account 1 +
account 2 + ..., each vs the SAME account a year ago, split by work-share. Net positive -> rate x net (trued
up on the book's running peak, never clawed back); net negative -> $0. Backtest window: Jan-Jun 2026 vs 2025.
"""
import io
import os
import datetime as dt
import pandas as pd
from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from .db import get_db, engine, Base
from . import models as M
from .auth import verify_password
from .config import SECRET_KEY
from . import service

Base.metadata.create_all(engine)
app = FastAPI(title="W&T Growth Backtest")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=8 * 3600)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def current_user(request: Request, db: Session):
    uid = request.session.get("uid")
    return db.get(M.User, uid) if uid else None


def _guard(request: Request, db: Session):
    user = current_user(request, db)
    return user if (user and user.role != "rep") else None


# ---------- contribution + acquisition (monthly, alongside the cumulative growth) ----------
def contribution_by_rep_month(db, months, team, item_rate):
    """Line items placed per (rep, month) x item_rate — same direct formula as the scorecard."""
    df = service.active_lines(db)
    d = df[df["associate"].isin(team)].copy()
    d["ym"] = d["document_date"].dt.to_period("M")
    per_set = {pd.Period(m, "M") for m in months}
    d = d[d["ym"].isin(per_set)]
    counts = d.groupby(["associate", "ym"]).size()
    return {(rep, str(per)): dict(n_items=int(n), bonus=float(n) * item_rate)
            for (rep, per), n in counts.items()}


def acquisition_by_rep_month(db, months, team, s):
    """Flat landing bonus by new-account size, paid ONCE at the ~quarter mark (first-sale month + 2),
    only for accounts the manager confirmed rep-won (AcquisitionReview). Size = first-8-weeks revenue,
    annualized, into the same small/medium/large tiers as the scorecard. Returns (pay_map, review_rows)."""
    df = service.active_lines(db)
    first = df.groupby("account")["document_date"].min()
    lo = pd.Period(months[0], "M") - 3                      # landed up to a quarter before the window still pays in it
    hi = pd.Period(months[-1], "M")
    cand = first[(first.dt.to_period("M") >= lo) & (first.dt.to_period("M") <= hi)]
    self_acq = service.self_acquired_set(db)
    names = service.customer_names(db)
    flags = {r.account: r.rep_won for r in db.query(M.AcquisitionReview)}
    small_max, med_max = float(s["acq_tier_small_max"]), float(s["acq_tier_medium_max"])
    flats = dict(small=float(s["acq_flat_small"]), medium=float(s["acq_flat_medium"]), large=float(s["acq_flat_large"]))
    pay, review = {}, []
    for acct, fs in cand.items():
        early = df[(df["account"] == acct) & (df["document_date"] <= fs + pd.Timedelta(days=56))]
        rep_rev = early[early["associate"].isin(team)].groupby("associate")["extended_price"].sum()
        rep = rep_rev.idxmax() if len(rep_rev) else None
        rev8 = float(early["extended_price"].sum())
        annual = rev8 * 365.0 / 56.0
        tier = "small" if annual < small_max else ("medium" if annual < med_max else "large")
        flat = flats[tier]
        pay_month = str(fs.to_period("M") + 2)
        confirmed = acct in self_acq
        if rep and confirmed and pay_month in months:
            pay[(rep, pay_month)] = pay.get((rep, pay_month), 0.0) + flat
        review.append(dict(account=acct, customer=names.get(acct, acct), rep=rep or "—",
                           first_order=str(fs.date()), rev8=rev8, annualized=annual, tier=tier, flat=flat,
                           pay_month=pay_month,
                           status=("rep-won" if flags.get(acct) is True
                                   else ("house" if flags.get(acct) is False else "unreviewed"))))
    review.sort(key=lambda r: -r["annualized"])
    return pay, review


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("backtest_login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(M.User).filter(M.User.username == username.strip().lower()).first()
    if user and verify_password(password, user.password_hash):
        request.session["uid"] = user.user_id
        return RedirectResponse("/me" if user.role == "rep" else "/", status_code=303)
    return templates.TemplateResponse("backtest_login.html",
                                      {"request": request, "error": "Wrong username or password / 用户名或密码错误"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def backtest(request: Request, m: str = None, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user and user.role == "rep":
        return RedirectResponse("/me", status_code=303)
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    r = service.run_cumulative_growth(db)      # memoized; window = fiscal_start_month (Jan) -> data end
    months = r["months"]
    if not months:
        return templates.TemplateResponse("backtest.html", {"request": request, "user": user, "months": [],
                                                            "rows": [], "m": None, "nav": {}, "rate": 0,
                                                            "team": {}, "page": "dash"})
    if m not in months:
        m = months[-1]
    mi = months.index(m)
    s = service.get_settings(db)
    _, _, team = service.attribution_maps(db)
    contrib = contribution_by_rep_month(db, months, team, float(s["item_rate"]))
    acq_pay, _review = acquisition_by_rep_month(db, months, team, s)

    # pay-on-collection: earned (all three pieces) is a TARGET; payable now scales by the rep's collected
    # fraction of their cycle billing (same rule as the scorecard's Collections page).
    df = service.active_lines(db)
    win = df[(df["document_date"] >= r["fiscal_start"]) & (df["document_date"] <= r["as_of"])]
    win = win[win["associate"].isin(team)]
    coll = {str(x) for x in service.collected_set(db)}
    billed = win.groupby("associate")["extended_price"].sum()
    collected = win[win["sop_number"].astype(str).isin(coll)].groupby("associate")["extended_price"].sum()

    rows = []
    for _, x in r["reps"].iterrows():
        rep = x["associate"]
        t = r["trajectory"][rep][mi]
        c = contrib.get((rep, m), dict(n_items=0, bonus=0.0))
        a_mo = acq_pay.get((rep, m), 0.0)
        growth_cycle = float(r["trajectory"][rep][-1]["cum_pay"])
        contrib_cycle = sum(contrib.get((rep, mm), {}).get("bonus", 0.0) for mm in months)
        acq_cycle = sum(v for (rp, _mm), v in acq_pay.items() if rp == rep)
        earned_cycle = growth_cycle + contrib_cycle + acq_cycle
        b = float(billed.get(rep, 0.0))
        frac = min(1.0, float(collected.get(rep, 0.0)) / b) if b > 0 else 0.0
        rows.append(dict(
            associate=rep,
            profit_mo=float(t["ty_book"]), profit_mo_ly=float(t["ly_book"]),
            gap_mo=float(t["ty_book"]) - float(t["ly_book"]),
            cum_gap=float(t["cum_growth"]),
            pay_mo=float(t["pay"]), cum_pay=float(t["cum_pay"]),
            n_items=c["n_items"], contrib_mo=c["bonus"], acq_mo=a_mo,
            total_mo=float(t["pay"]) + c["bonus"] + a_mo,
            earned_cycle=earned_cycle, collected_pct=frac * 100.0, payable_now=earned_cycle * frac,
        ))
    rows.sort(key=lambda z: -z["earned_cycle"])
    team_row = {k: sum(z[k] for z in rows) for k in
                ("profit_mo", "profit_mo_ly", "gap_mo", "cum_gap", "pay_mo", "cum_pay",
                 "contrib_mo", "acq_mo", "total_mo", "earned_cycle", "payable_now")}
    team_row["n_items"] = sum(z["n_items"] for z in rows)
    nav = dict(prev=(months[mi - 1] if mi > 0 else None), next=(months[mi + 1] if mi + 1 < len(months) else None),
               n=mi + 1, total=len(months))
    return templates.TemplateResponse("backtest.html", {
        "request": request, "user": user, "months": months, "m": m, "mi": mi, "nav": nav,
        "rows": rows, "team": team_row, "rate": r["cumulative_rate"], "page": "dash",
        "fiscal_start": r["fiscal_start"], "as_of": r["as_of"]})


# ---------- new-account review (assigned vs self-earned -> acquisition eligibility) ----------
@app.get("/acquisitions", response_class=HTMLResponse)
def acquisitions_page(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    r = service.run_cumulative_growth(db)
    s = service.get_settings(db)
    _, _, team = service.attribution_maps(db)
    _pay, review = acquisition_by_rep_month(db, r["months"], team, s) if r["months"] else ({}, [])
    return templates.TemplateResponse("backtest_acquisitions.html", {
        "request": request, "user": user, "rows": review, "page": "acq"})


@app.post("/acquisitions/flag")
def acquisitions_flag(request: Request, account: str = Form(...), rep_won: str = Form(...),
                      db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    rev = db.get(M.AcquisitionReview, account) or M.AcquisitionReview(account=account)
    rev.rep_won = (rep_won == "yes"); rev.user_id = user.user_id; rev.created_at = dt.datetime.utcnow()
    db.merge(rev); db.commit()
    service._ENGINE_CACHE.clear()
    return RedirectResponse("/acquisitions", status_code=303)


# ---------- import (the FIXED importer — keeps every invoice) + AR + voided ----------
@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    last = db.query(M.SalesLine).order_by(M.SalesLine.imported_at.desc()).first()
    return templates.TemplateResponse("backtest_upload.html", {
        "request": request, "user": user, "page": "upload",
        "imported_at": last.imported_at if last else None,
        "n_lines": db.query(M.SalesLine).count(), "n_collected": db.query(M.CollectedInvoice).count()})


@app.post("/upload")
async def upload_sales(request: Request, sales_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = pd.read_excel(io.BytesIO(await sales_file.read()))
    res = service.import_sales_frame(db, raw)          # shared FIXED importer
    extra = f"; excluded {res['voided']:,} voided invoices" if res["has_void_col"] else ""
    return templates.TemplateResponse("backtest_upload.html", {"request": request, "user": user, "page": "upload",
        "msg": f"Imported {res['lines']:,} sales lines across {res['orders']:,} orders "
               f"({res['tracked']:,} credited to tracked reps; the rest are history-only){extra}.",
        "n_lines": db.query(M.SalesLine).count(), "n_collected": db.query(M.CollectedInvoice).count()})


@app.post("/upload-receivables")
async def upload_receivables(request: Request, ar_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = pd.read_excel(io.BytesIO(await ar_file.read()))
    collected = service.parse_collected(raw)
    if collected is None:
        msg = "No invoice-number column (Document / Invoice / SOP Number) found in the paid file."
    else:
        db.query(M.CollectedInvoice).delete(synchronize_session=False)
        now = dt.datetime.utcnow()
        for sop in collected:
            db.add(M.CollectedInvoice(sop_number=sop, reported_at=now))
        db.commit()
        service._ENGINE_CACHE.clear()
        msg = f"Recorded {len(collected):,} paid invoices (snapshot). Payable-now updates on the dashboard."
    return templates.TemplateResponse("backtest_upload.html", {"request": request, "user": user, "page": "upload",
        "ar_msg": msg, "n_lines": db.query(M.SalesLine).count(), "n_collected": db.query(M.CollectedInvoice).count()})


@app.post("/upload-voided")
async def upload_voided(request: Request, voided_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = pd.read_excel(io.BytesIO(await voided_file.read()))
    if service._invoice_col(raw) is None:
        msg = "No invoice-number column (Document / Invoice / SOP Number) found in the file."
    elif not any("void" in str(c).strip().lower() for c in raw.columns):
        msg = ("Rejected: this file has no 'Void Status' column, so I can't tell which invoices are voided — "
               "refusing to void the whole list.")
    else:
        voided = service.parse_voided(raw)
        db.query(M.VoidedInvoice).delete(synchronize_session=False)
        now = dt.datetime.utcnow()
        for sop in voided:
            db.add(M.VoidedInvoice(sop_number=sop, reported_at=now))
        db.commit()
        service._ENGINE_CACHE.clear()
        msg = f"Recorded {len(voided):,} voided invoices (snapshot). They count toward nothing."
    return templates.TemplateResponse("backtest_upload.html", {"request": request, "user": user, "page": "upload",
        "void_msg": msg, "n_lines": db.query(M.SalesLine).count(), "n_collected": db.query(M.CollectedInvoice).count()})


# ---------- limited stock (constrained items — shared table with the main app) ----------
def _any_period(db):
    """Any Period row for the ConstrainedItem legacy FK (the flag itself is global)."""
    p = db.query(M.Period).first()
    if p is None:
        _, hi = service.data_bounds(db)
        p = M.Period(end_date=hi.date()); db.add(p); db.commit()
    return p


@app.get("/constrained", response_class=HTMLResponse)
def constrained_page(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    seen, current = set(), []
    for c in db.query(M.ConstrainedItem).order_by(M.ConstrainedItem.created_at.desc()):
        if c.item_number not in seen:
            seen.add(c.item_number); current.append(c)
    num_to_desc = service.item_descriptions(db)
    all_items = sorted(({"item": k, "desc": v or ""} for k, v in num_to_desc.items()),
                       key=lambda r: r["desc"] or r["item"])
    return templates.TemplateResponse("backtest_constrained.html", {
        "request": request, "user": user, "page": "stock", "current": current,
        "desc_by_item": num_to_desc, "all_items": all_items, "constrained_set": sorted(seen)})


@app.post("/constrained/add")
def constrained_add(request: Request, item_number: str = Form(...), note: str = Form(""),
                    ajax: str = Form(""), db: Session = Depends(get_db)):
    from fastapi.responses import JSONResponse
    user = _guard(request, db)
    if not user:
        return JSONResponse({"ok": False}, status_code=403) if ajax else RedirectResponse("/login", status_code=303)
    item = item_number.strip()
    if item and not db.query(M.ConstrainedItem).filter(M.ConstrainedItem.item_number == item).first():
        db.add(M.ConstrainedItem(period_id=_any_period(db).period_id, item_number=item,
                                 note=note, user_id=user.user_id))
        db.commit()
        service._ENGINE_CACHE.clear()
    if ajax:
        return JSONResponse({"ok": True, "item": item})
    return RedirectResponse("/constrained", status_code=303)


@app.post("/constrained/remove")
def constrained_remove(request: Request, item_number: str = Form(...), db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    db.query(M.ConstrainedItem).filter(M.ConstrainedItem.item_number == item_number.strip()).delete()
    db.commit()
    service._ENGINE_CACHE.clear()
    return RedirectResponse("/constrained", status_code=303)


# ---------- rep dashboard (their own book; pays on collection) ----------
@app.get("/me", response_class=HTMLResponse)
def me(request: Request, lang: str = "zh", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role != "rep":
        return RedirectResponse("/", status_code=303)
    name = user.associate_name
    r = service.run_cumulative_growth(db)
    months = r["months"]
    if not months or name not in r["trajectory"]:
        return templates.TemplateResponse("backtest_me.html", {
            "request": request, "user": user, "page": "me", "lang": lang, "name": name,
            "months": [], "traj": [], "accounts": [], "k": {}})
    s = service.get_settings(db)
    _, _, team = service.attribution_maps(db)
    contrib = contribution_by_rep_month(db, months, team, float(s["item_rate"]))
    acq_pay, _rev = acquisition_by_rep_month(db, months, team, s)
    traj = []
    for i, mm in enumerate(months):
        t = r["trajectory"][name][i]
        c = contrib.get((name, mm), dict(n_items=0, bonus=0.0))
        a_mo = acq_pay.get((name, mm), 0.0)
        traj.append(dict(month=mm, ty=float(t["ty_book"]), ly=float(t["ly_book"]),
                         gap=float(t["ty_book"]) - float(t["ly_book"]), cum=float(t["cum_growth"]),
                         pay=float(t["pay"]), n_items=c["n_items"], contrib=c["bonus"], acq=a_mo,
                         total=float(t["pay"]) + c["bonus"] + a_mo))
    growth_cycle = float(r["trajectory"][name][-1]["cum_pay"])
    contrib_cycle = sum(x["contrib"] for x in traj)
    acq_cycle = sum(x["acq"] for x in traj)
    earned = growth_cycle + contrib_cycle + acq_cycle
    df = service.active_lines(db)
    win = df[(df["document_date"] >= r["fiscal_start"]) & (df["document_date"] <= r["as_of"])]
    mine = win[win["associate"] == name]
    coll = {str(x) for x in service.collected_set(db)}
    billed = float(mine["extended_price"].sum())
    collected = float(mine[mine["sop_number"].astype(str).isin(coll)]["extended_price"].sum())
    frac = min(1.0, collected / billed) if billed > 0 else 0.0
    names = service.customer_names(db)
    acc = r["accounts"]
    accounts = []
    if len(acc):
        for a in acc[acc["holder"] == name].sort_values("growth", ascending=False).itertuples(index=False):
            accounts.append(dict(name=names.get(a.account, a.account), ty=float(a.ty_profit),
                                 ly=float(a.ly_profit), gap=float(a.growth), is_young=bool(a.is_young)))
    k = dict(cum_gap=float(r["trajectory"][name][-1]["cum_growth"]), earned=earned,
             growth_cycle=growth_cycle, contrib_cycle=contrib_cycle, acq_cycle=acq_cycle,
             collected_pct=frac * 100.0, payable=earned * frac)
    return templates.TemplateResponse("backtest_me.html", {
        "request": request, "user": user, "page": "me", "lang": lang, "name": name,
        "months": months, "traj": traj, "accounts": accounts, "k": k,
        "rate": r["cumulative_rate"], "fiscal_start": r["fiscal_start"], "as_of": r["as_of"]})


# ---------- guides (manager + rep, EN / 中文) ----------
@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request, lang: str = "en", db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    s = service.get_settings(db)
    return templates.TemplateResponse("backtest_guide.html", {
        "request": request, "user": user, "page": "guide", "lang": lang,
        "rate": float(s.get("cumulative_rate", 0.05)), "item_rate": float(s["item_rate"]),
        "flat_small": int(float(s["acq_flat_small"])), "flat_medium": int(float(s["acq_flat_medium"])),
        "flat_large": int(float(s["acq_flat_large"])),
        "tier_small": int(float(s["acq_tier_small_max"])), "tier_medium": int(float(s["acq_tier_medium_max"]))})


@app.get("/me/guide", response_class=HTMLResponse)
def rep_guide(request: Request, lang: str = "zh", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    s = service.get_settings(db)
    return templates.TemplateResponse("backtest_rep_guide.html", {
        "request": request, "user": user, "page": "repguide", "lang": lang,
        "rate": float(s.get("cumulative_rate", 0.05)), "item_rate": float(s["item_rate"]),
        "flat_small": int(float(s["acq_flat_small"])), "flat_medium": int(float(s["acq_flat_medium"])),
        "flat_large": int(float(s["acq_flat_large"]))})
