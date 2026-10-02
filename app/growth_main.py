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
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from .db import get_db, engine, Base
from . import models as M
from .auth import verify_password, hash_password
from .config import SECRET_KEY
from . import service
from . import config as cfg

Base.metadata.create_all(engine)


def _seed_targets():
    """One-time: give each TEAM a growth target key at the default, so the manager sees editable rows on
    /settings. Idempotent — only inserts a key if it's absent, so manager edits are never overwritten.
    (The old per-rep target keys are left in place, unused, rather than deleted behind the manager's back.)"""
    from .db import SessionLocal
    from .config import TEAMS, DEFAULTS
    db = SessionLocal()
    try:
        for name in TEAMS:
            key = f"growth_target::{name}"
            if not db.get(M.Setting, key):
                db.add(M.Setting(key=key, value=str(DEFAULTS["growth_target_default"])))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


_seed_targets()

app = FastAPI(title="W&T Sales Scorecard (growth model)")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=8 * 3600)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.filters["month_name"] = lambda n: ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                                 "Oct", "Nov", "Dec"][int(n)]


def current_user(request: Request, db: Session):
    uid = request.session.get("uid")
    return db.get(M.User, uid) if uid else None


def _guard(request: Request, db: Session):
    user = current_user(request, db)
    return user if (user and user.role != "rep") else None


# contribution + acquisition live in service.py (the pay ledger needs them too); re-exported here
# so the route handlers below keep reading the same names.
contribution_by_rep_month = service.contribution_by_rep_month
acquisition_by_rep_month = service.acquisition_by_rep_month


def _build_version():
    """The commit this image was built from (written into the image by scripts/deploy.sh). Reported by
    /healthz so a deploy can CHECK that the new code is actually serving instead of assuming it."""
    for path in ("VERSION", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")):
        try:
            with open(path) as fh:
                return fh.read().strip()
        except OSError:
            continue
    return "dev"


BUILD_VERSION = _build_version()


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return f"ok {BUILD_VERSION}"


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
    r = service.run_cumulative_growth(db, with_comparison=False)   # window = fiscal_start_month -> data end
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
    growth_active = r.get("growth_active", True)
    contrib = contribution_by_rep_month(db, months, team, float(s["item_rate"]))
    # Before growth_start the program is CONTRIBUTION ONLY — no growth, no landing bonuses (an account that
    # lands now still gets reviewed on the New accounts tab and pays once the growth cycle opens).
    acq_pay = acquisition_by_rep_month(db, months, team, s)[0] if growth_active else {}

    thru = months[:mi + 1]
    # PAY is a live, whole-program number, not a per-month one: the ledger keeps every chapter open until it
    # is fully collected AND fully paid, so money earned in a closed chapter still releases here.
    ledger = service.pay_ledger(db)

    # GROWTH is earned by the TEAM (the accounts it owns) and split equally; CONTRIBUTION and ACQUISITION
    # stay individual. So there are two views: the team's growth, then each rep's own pay line.
    team_rows = []
    for _, x in r.get("teams", pd.DataFrame()).iterrows():
        tname = x["team"]
        t = r["trajectory"][tname][mi]
        team_rows.append(dict(
            team=tname, members=r["team_members"].get(tname, []), n_accounts=int(x["n_accounts"]),
            profit_mo=float(t["ty_book"]), profit_mo_ly=float(t["ly_book"]),
            gap_mo=float(t["ty_book"]) - float(t["ly_book"]), cum_gap=float(t["cum_growth"]),
            pay_mo=float(t["pay"]), cum_pay=float(t["cum_pay"]),
            target=(float(t["target"]) if t["target"] is not None else None)))

    # every rep on the roster gets a pay line (contribution is individual, so a house-team rep still earns it);
    # the growth share comes from their team, or is absent if they are not on a paid team.
    rep_team = {row["associate"]: row["team"] for _, row in r["reps"].iterrows()} if len(r["reps"]) else {}
    members_of = {t: len(ms) for t, ms in r.get("team_members", {}).items()}
    zero = dict(pay=0.0, cum_pay=0.0)
    rows = []
    for rep in team:
        t = r.get("rep_trajectory", {}).get(rep, [zero] * len(months))[mi]
        x = dict(team=rep_team.get(rep), members=members_of.get(rep_team.get(rep), 1))
        c = contrib.get((rep, m), dict(n_items=0, bonus=0.0))
        a_mo = acq_pay.get((rep, m), 0.0)
        growth_cycle = float(t["cum_pay"])                                              # through selected month
        contrib_cycle = sum(contrib.get((rep, mm), {}).get("bonus", 0.0) for mm in thru)
        acq_cycle = sum(v for (rp, mmn), v in acq_pay.items() if rp == rep and mmn in thru)
        tot = ledger["totals"].get(rep, {})
        rows.append(dict(
            associate=rep, team=x["team"], members=int(x["members"] or 1),
            pay_mo=float(t["pay"]), cum_pay=growth_cycle,
            n_items=c["n_items"], contrib_mo=c["bonus"], acq_mo=a_mo,
            total_mo=float(t["pay"]) + c["bonus"] + a_mo,
            earned_cycle=growth_cycle + contrib_cycle + acq_cycle,
            earned_all=tot.get("earned", 0.0),
            collected_pct=(100.0 * tot.get("collectable", 0.0) / tot["earned"]) if tot.get("earned") else 0.0,
            paid=tot.get("paid", 0.0), payable_now=tot.get("owed", 0.0),
            carried=tot.get("owed_prior", 0.0), unreleased=tot.get("unreleased", 0.0),
        ))
    rows.sort(key=lambda z: (z["team"] is None, z["team"] or "", -z["earned_cycle"]))

    # --- each row is MONTH-SCOPED: the selected month's earnings, its own collection, what was paid against
    # it and what it still owes. A past month is read-only (whatever it still owes rolls forward and is paid
    # from the latest month, which is the only view with a pay button and the all-months total).
    is_latest = (mi == len(months) - 1)
    for x in rows:
        rep = x["associate"]
        detail = service.rep_pay_detail(db, rep, show="all") or dict(months=[])
        mrows = detail["months"]                                   # newest first
        cur = next((mm for mm in mrows if mm["month"] == m), None)
        x["panel"] = cur
        for key in ("earned", "collected_pct", "collectable", "paid", "owed", "unreleased"):
            x["m_" + key] = cur[key] if cur else 0.0
        x["carried"] = sum(mm["owed"] for mm in mrows if mm["month"] < m)     # unpaid from EARLIER months only
        x["pay_total"] = x["m_owed"] + x["carried"]                            # what a payment today settles
        # exactly what a payment today is for: each month with money owed, oldest first (the order it settles)
        x["pay_breakdown"] = [dict(label=mm["label"], owed=mm["owed"], released=mm["collectable"], paid=mm["paid"])
                              for mm in sorted(mrows, key=lambda r: r["month"])
                              if mm["month"] <= m and mm["owed"] > 0.005]
        if cur:
            # the newest 25 of EACH status, so the Missing / Collected filter always has something to show
            newest = cur["invoices"]
            keep = [i for i in newest if not i["paid"]][:25] + [i for i in newest if i["paid"]][:25]
            keep.sort(key=lambda i: (i["date"], i["sop_number"]), reverse=True)
            x["statement"] = [dict(cur, invoices=keep)]
        else:
            x["statement"] = []
        x["earlier"] = [mm for mm in mrows if mm["month"] < m]

    # group by team for display: a colored header row per team, members beneath it
    palette = {"Team 1": "t1", "Team 2": "t2"}
    groups = []
    for x in rows:
        key = x["team"] or "House"
        g = next((g for g in groups if g["team"] == key), None)
        if g is None:
            g = dict(team=key, css=palette.get(key, "house"), rows=[],
                     members=r.get("team_members", {}).get(key, []))
            groups.append(g)
        g["rows"].append(x)
    for g in groups:
        g["sub"] = {k: sum(z[k] for z in g["rows"]) for k in
                    ("pay_mo", "contrib_mo", "acq_mo", "total_mo", "m_earned", "m_collectable", "m_paid",
                     "m_owed", "carried", "pay_total")}
        g["sub"]["n_items"] = sum(z["n_items"] for z in g["rows"])
    team_row = {k: sum(z[k] for z in rows) for k in
                ("pay_mo", "cum_pay", "contrib_mo", "acq_mo", "total_mo", "earned_cycle", "earned_all",
                 "paid", "payable_now", "carried", "unreleased", "m_earned", "m_collectable", "m_paid",
                 "m_owed", "m_unreleased", "pay_total")}
    team_row["n_items"] = sum(z["n_items"] for z in rows)
    for k in ("profit_mo", "profit_mo_ly", "gap_mo", "cum_gap"):
        team_row[k] = sum(z[k] for z in team_rows)
    nav = dict(prev=(months[mi - 1] if mi > 0 else None), next=(months[mi + 1] if mi + 1 < len(months) else None),
               n=mi + 1, total=len(months))
    return templates.TemplateResponse("backtest.html", {
        "request": request, "user": user, "months": months, "m": m, "mi": mi, "nav": nav,
        "rows": rows, "groups": groups, "team": team_row, "team_rows": team_rows, "rate": r["cumulative_rate"], "page": "dash",
        "unassigned": service.unassigned_summary(db),
        "is_latest": (mi == len(months) - 1), "growth_active": growth_active,
        "ledger": ledger, "payments": [
            dict(associate=p.associate, chapter=str(p.fiscal_start), paid=float(p.paid_cum or 0.0),
                 by={u.user_id: u.username for u in db.query(M.User).all()}.get(p.user_id, "—"),
                 when=p.updated_at)
            for p in db.query(M.GrowthPayment).order_by(M.GrowthPayment.updated_at.desc()).limit(50).all()],
        "growth_start": r["growth_start"],
        "fiscal_start": r["fiscal_start"], "as_of": r["as_of"]})


# ---------- record pay (progressive true-up: bump paid to the current collectable amount) ----------
@app.post("/pay")
def record_pay(request: Request, associate: str = Form(...), amount: float = Form(...),
               db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    # settle the OLDEST chapter first, and never beyond what that chapter has collected
    service.allocate_payment(db, associate, amount, user.user_id)
    return RedirectResponse("/", status_code=303)


# ---------- per-rep drill-down: which accounts drove the number ----------
@app.get("/rep/{name}", response_class=HTMLResponse)
def rep_detail(request: Request, name: str, m: str = None, db: Session = Depends(get_db)):
    """Growth is a TEAM number, so a rep's drill-down is their team's."""
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    r = service.run_cumulative_growth(db, with_comparison=False)
    for team_name, members in r.get("team_members", {}).items():
        if name in members:
            return RedirectResponse(f"/team/{team_name}" + (f"?m={m}" if m else ""), status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/team/{team_name}", response_class=HTMLResponse)
def team_detail(request: Request, team_name: str, m: str = None, db: Session = Depends(get_db)):
    """Which accounts drove a team's growth number — the team owns each one whole, so no share column."""
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    r = service.run_cumulative_growth(db, with_comparison=False)
    months = r["months"]
    # the drill-down is purely a growth view — nothing to show in the contribution-only chapter
    if not months or team_name not in r["trajectory"] or not r.get("growth_active", True):
        return RedirectResponse("/" if user.role != "rep" else "/me", status_code=303)
    if user.role == "rep" and user.associate_name not in r["team_members"].get(team_name, []):
        return RedirectResponse("/me", status_code=303)          # a rep only sees their own team
    if m not in months:
        m = months[-1]
    mi = months.index(m)
    names = service.customer_names(db)
    rows = []
    for acct, v in r["account_monthly"].items():
        if team_name not in v["shares"]:
            continue
        ty_mo, ly_mo = v["mo_ty"][mi], v["mo_ly"][mi]
        rows.append(dict(account=acct, customer=names.get(acct, acct),
                         ty_mo=ty_mo, ly_mo=ly_mo, gap_mo=ty_mo - ly_mo,
                         cum_gap=v["cum"][mi], is_young=bool(v["is_young"])))
    rows.sort(key=lambda z: -z["gap_mo"])
    tot = {k: sum(z[k] for z in rows) for k in ("ty_mo", "ly_mo", "gap_mo", "cum_gap")}
    t = r["trajectory"][team_name][mi]
    members = r["team_members"].get(team_name, [])
    nav = dict(prev=(months[mi - 1] if mi > 0 else None), next=(months[mi + 1] if mi + 1 < len(months) else None))
    return templates.TemplateResponse("backtest_rep_detail.html", {
        "request": request, "user": user, "page": "dash", "name": team_name, "members": members,
        "m": m, "months": months, "nav": nav,
        "rows": rows, "tot": tot, "pay_mo": float(t["pay"]), "cum_pay": float(t["cum_pay"]),
        "share_each": (float(t["pay"]) / len(members)) if members else 0.0,
        "rate": r["cumulative_rate"], "growth_active": r.get("growth_active", True),
        "growth_start": r["growth_start"]})


# ---------- the pay ledger: every chapter, open until collected and paid ----------
@app.get("/ledger")
def ledger_page(request: Request):
    return RedirectResponse("/", status_code=303)          # the ledger now lives on the dashboard


# ---------- one rep's pay, invoice by invoice: where every number came from ----------
MONTHS_PER_PAGE = 12


@app.get("/pay/{name}", response_class=HTMLResponse)
def pay_detail(request: Request, name: str, page_no: int = 1, db: Session = Depends(get_db)):
    """One rep's audit: one row per month, each expanding to that month's invoice ledger. Paged by
    MONTHS_PER_PAGE (newest first) so a rep with years of history stays readable."""
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    detail = service.rep_pay_detail(db, name, show="all")
    if detail is None:
        return RedirectResponse("/", status_code=303)
    months = detail["months"]                                        # newest first
    n_pages = max(1, (len(months) + MONTHS_PER_PAGE - 1) // MONTHS_PER_PAGE)
    page_no = min(max(1, page_no), n_pages)
    lo = (page_no - 1) * MONTHS_PER_PAGE
    return templates.TemplateResponse("backtest_pay_detail.html", dict(
        detail, months=months[lo:lo + MONTHS_PER_PAGE], all_months=months, request=request, user=user,
        page="pay", name=name, page_no=page_no, n_pages=n_pages))


@app.get("/invoice/{sop_number}.json")
def invoice_json(request: Request, sop_number: str, db: Session = Depends(get_db)):
    """The invoice's lines for the inline expansion (same access rule as the page)."""
    user = current_user(request, db)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    inv = service.invoice_detail(db, sop_number)
    if inv is None or (user.role == "rep" and inv["header"]["associate"] != user.associate_name):
        return JSONResponse({"ok": False}, status_code=404)
    h = inv["header"]
    return JSONResponse({"ok": True, "header": dict(h, date=str(h["date"])), "lines": inv["lines"]})


@app.get("/invoice/{sop_number}", response_class=HTMLResponse)
def invoice_page(request: Request, sop_number: str, db: Session = Depends(get_db)):
    """Inspect one invoice, line by line. Managers see any; a rep sees only invoices they wrote."""
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    inv = service.invoice_detail(db, sop_number)
    if inv is None or (user.role == "rep" and inv["header"]["associate"] != user.associate_name):
        return RedirectResponse("/" if user.role != "rep" else "/me", status_code=303)
    return templates.TemplateResponse("backtest_invoice.html", dict(inv, request=request, user=user, page="pay"))


# ---------- account assignment: which TEAM owns each account ----------
@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, view: str = "shared", q: str = "", db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from .config import TEAM_OWNERSHIP_PCT, TEAM_WINDOW_MONTHS, HOUSE_TEAM
    rows = service.account_assignments(db)
    teams = list(service.team_members(db)) + [HOUSE_TEAM]
    counts = dict(all=len(rows), shared=sum(1 for r in rows if r["shared"]))
    for t in teams:
        counts[t] = sum(1 for r in rows if r["team"] == t)
    shown = [r for r in rows if (view == "all" or (view == "shared" and r["shared"]) or r["team"] == view)]
    if q:
        needle = q.strip().lower()
        shown = [r for r in shown if needle in r["customer"].lower() or needle in r["account"].lower()]
    return templates.TemplateResponse("backtest_accounts.html", {
        "request": request, "user": user, "page": "accounts", "rows": shown[:400], "n_shown": len(shown),
        "teams": teams, "team_members": service.team_members(db), "view": view, "q": q, "counts": counts,
        "pct": TEAM_OWNERSHIP_PCT * 100, "months": TEAM_WINDOW_MONTHS})


@app.post("/accounts/assign.json")
async def accounts_assign_json(request: Request, db: Session = Depends(get_db)):
    """Drag-and-drop assignment: {account, team} -> {ok, team}. Empty team clears the override."""
    user = _guard(request, db)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    account, team = str(body.get("account", "")).strip(), str(body.get("team", "")).strip()
    from .config import HOUSE_TEAM
    if not account or (team and team not in list(service.team_members(db)) + [HOUSE_TEAM]):
        return JSONResponse({"ok": False}, status_code=400)
    row = db.get(M.AccountAssignment, account) or M.AccountAssignment(account=account)
    row.team = team or None; row.user_id = user.user_id; row.updated_at = dt.datetime.utcnow()
    db.merge(row); db.commit()
    return JSONResponse({"ok": True, "team": team or None})


@app.post("/accounts/assign")
def accounts_assign(request: Request, account: str = Form(...), team: str = Form(...), view: str = Form("shared"),
                    db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    row = db.get(M.AccountAssignment, account) or M.AccountAssignment(account=account)
    row.team = team or None                       # empty -> clear the override, fall back to the 80% rule
    row.user_id = user.user_id
    row.updated_at = dt.datetime.utcnow()
    db.merge(row); db.commit()
    return RedirectResponse(f"/accounts?view={view}", status_code=303)


# ---------- my account: change my own password ----------
MIN_PASSWORD = 8


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, lang: str = "zh", saved: int = 0, err: str = "",
                 db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("backtest_account.html", {
        "request": request, "user": user, "page": "account", "saved": bool(saved), "err": err,
        "lang": (lang if user.role == "rep" else "en"), "min_len": MIN_PASSWORD})


@app.post("/account")
def account_save(request: Request, current: str = Form(...), new: str = Form(...),
                 confirm: str = Form(...), lang: str = Form("zh"), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not verify_password(current, user.password_hash):
        err = "wrong"
    elif len(new) < MIN_PASSWORD:
        err = "short"
    elif new != confirm:
        err = "mismatch"
    elif verify_password(new, user.password_hash):
        err = "same"
    else:
        user.password_hash = hash_password(new)
        db.commit()
        return RedirectResponse(f"/account?saved=1&lang={lang}", status_code=303)
    return RedirectResponse(f"/account?err={err}&lang={lang}", status_code=303)


# ---------- logins (manager): one account per rep, reset a forgotten password ----------
DEFAULT_PASSWORD = "demo123"


@app.get("/logins", response_class=HTMLResponse)
def logins_page(request: Request, saved: str = "", db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    _, _, roster = service.attribution_maps(db)
    rep_team = service.team_of_rep(db)
    users = db.query(M.User).order_by(M.User.role, M.User.username).all()
    rows = [dict(username=u.username, role=u.role, associate=u.associate_name,
                 team=rep_team.get(u.associate_name or ""),
                 default_pw=verify_password(DEFAULT_PASSWORD, u.password_hash)) for u in users]
    have = {u.associate_name for u in users if u.associate_name}
    missing = [n for n in roster if n not in have]
    return templates.TemplateResponse("backtest_logins.html", {
        "request": request, "user": user, "page": "logins", "rows": rows, "missing": missing,
        "saved": saved, "min_len": MIN_PASSWORD})


@app.post("/logins/reset")
def logins_reset(request: Request, username: str = Form(...), password: str = Form(...),
                 db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    target = db.query(M.User).filter(M.User.username == username).first()
    if target is None or len(password) < MIN_PASSWORD:
        return RedirectResponse("/logins?saved=error", status_code=303)
    target.password_hash = hash_password(password)
    db.commit()
    return RedirectResponse(f"/logins?saved={username}", status_code=303)


@app.post("/logins/create")
def logins_create(request: Request, associate: str = Form(...), username: str = Form(...),
                  password: str = Form(...), db: Session = Depends(get_db)):
    """Give a roster rep their own read-only login."""
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    username = username.strip().lower()
    if not username or len(password) < MIN_PASSWORD or \
            db.query(M.User).filter(M.User.username == username).first():
        return RedirectResponse("/logins?saved=error", status_code=303)
    db.add(M.User(username=username, password_hash=hash_password(password), role="rep",
                  associate_name=associate))
    db.commit()
    return RedirectResponse(f"/logins?saved={username}", status_code=303)


# ---------- underperforming accounts (rolling 3-month watch list) ----------
def _underperf_context(db, view, team=None):
    s = service.get_settings(db)
    rows = service.underperforming_accounts(db)
    if team:
        rows = [r for r in rows if r["team"] == team]
    counts = dict(both=sum(1 for r in rows if r["negative"] and r["below_band"]),
                  negative=sum(1 for r in rows if r["negative"]),
                  below=sum(1 for r in rows if r["below_band"]),
                  new=sum(1 for r in rows if r["is_new"]), all=len(rows))
    pick = {"both": lambda r: r["negative"] and r["below_band"],
            "negative": lambda r: r["negative"],
            "below": lambda r: r["below_band"],
            "new": lambda r: r["is_new"],
            "all": lambda r: True}
    shown = [r for r in rows if pick.get(view, pick["both"])(r)]
    return dict(rows=shown[:300], n_shown=len(shown), counts=counts, view=view,
                months=int(s.get("underperf_window_months", 3)),
                min_profit=float(s.get("underperf_min_profit", 500)))


@app.get("/underperformers", response_class=HTMLResponse)
def underperformers_page(request: Request, view: str = "both", db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ctx = _underperf_context(db, view)
    return templates.TemplateResponse("backtest_underperformers.html", dict(
        ctx, **service.review_counts(db), request=request, user=user, page="under", lang="en",
        mine=False, team=None))


@app.get("/me/watch", response_class=HTMLResponse)
def rep_underperformers(request: Request, lang: str = "zh", view: str = "both",
                        db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role != "rep":
        return RedirectResponse("/underperformers", status_code=303)
    r = service.run_cumulative_growth(db, with_comparison=False)
    my_team = next((t for t, ms in r.get("team_members", {}).items() if user.associate_name in ms), None)
    ctx = _underperf_context(db, view, team=my_team)
    return templates.TemplateResponse("backtest_underperformers.html", dict(
        ctx, request=request, user=user, page="mewatch", lang=lang, mine=True, team=my_team))


# ---------- new-account review (assigned vs self-earned -> acquisition eligibility) ----------
@app.get("/acquisitions", response_class=HTMLResponse)
def acquisitions_page(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    r = service.run_cumulative_growth(db, with_comparison=False)
    s = service.get_settings(db)
    _, _, team = service.attribution_maps(db)
    _pay, review = acquisition_by_rep_month(db, r["months"], team, s) if r["months"] else ({}, [])
    return templates.TemplateResponse("backtest_acquisitions.html", dict(
        service.review_counts(db), request=request, user=user, rows=review, page="acq", lang="en"))


@app.post("/acquisitions/flag")
def acquisitions_flag(request: Request, account: str = Form(...), rep_won: str = Form(...),
                      db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    rev = db.get(M.AcquisitionReview, account) or M.AcquisitionReview(account=account)
    rev.rep_won = (rep_won == "yes"); rev.user_id = user.user_id; rev.created_at = dt.datetime.utcnow()
    db.merge(rev); db.commit()
    # NOTE: no cache clear — acquisition status doesn't affect the (expensive) growth engine, and the
    # acquisition memo is keyed by the AcquisitionReview signature, so this mark refreshes it on its own.
    return RedirectResponse("/acquisitions", status_code=303)


# ---------- settings: growth pay dials (base/accel rate, per-rep target %) ----------
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    s = service.get_settings(db)
    _, _, team = service.attribution_maps(db)
    default_t = float(s.get("growth_target_default", 0.06))
    # targets are per TEAM now (growth is earned by the team and split equally among its members)
    reps = [dict(name=t, target=float(s.get(f"growth_target::{t}", default_t)), members=", ".join(ms))
            for t, ms in service.team_members(db).items()]
    return templates.TemplateResponse("backtest_settings.html", {
        "request": request, "user": user, "page": "settings", "saved": bool(saved), "reps": reps,
        "base_rate": float(s.get("cumulative_rate", 0.05)),
        "accel_rate": float(s.get("growth_accel_rate", 0.075)), "default_target": default_t,
        "acq_small": int(float(s["acq_flat_small"])), "acq_medium": int(float(s["acq_flat_medium"])),
        "acq_large": int(float(s["acq_flat_large"])),
        "tier_small": int(float(s["acq_tier_small_max"])), "tier_medium": int(float(s["acq_tier_medium_max"]))})


@app.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()

    def put(key, val):
        row = db.get(M.Setting, key) or M.Setting(key=key)
        row.value = str(val); db.merge(row)

    for key in ("cumulative_rate", "growth_accel_rate", "growth_target_default"):
        if form.get(key, "").strip():
            put(key, float(form[key]))
    for key in ("acq_flat_small", "acq_flat_medium", "acq_flat_large"):
        if form.get(key, "").strip():
            put(key, int(float(form[key])))
    for name in service.team_members(db):
        v = form.get(f"target::{name}", "").strip()
        if v:
            put(f"growth_target::{name}", float(v))
    db.commit()
    service._ENGINE_CACHE.clear()
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------- import (the FIXED importer — keeps every invoice) + AR + voided ----------
def _upload_state(db):
    """Facts for the import boxes: what's on file and when it last changed, per file type."""
    from sqlalchemy import func as F
    lines, last_import = db.query(F.count(M.SalesLine.id), F.max(M.SalesLine.imported_at)).one()
    data_to = db.query(F.max(M.SalesLine.document_date)).scalar()
    n_coll, coll_at = db.query(F.count(M.CollectedInvoice.sop_number), F.max(M.CollectedInvoice.reported_at)).one()
    n_void, void_at = db.query(F.count(M.VoidedInvoice.sop_number), F.max(M.VoidedInvoice.reported_at)).one()
    return dict(n_lines=int(lines or 0), imported_at=last_import, data_to=data_to,
                n_collected=int(n_coll or 0), collected_at=coll_at, n_voided=int(n_void or 0), voided_at=void_at)


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("backtest_upload.html", dict(
        _upload_state(db), request=request, user=user, page="upload"))


@app.post("/upload")
async def upload_sales(request: Request, sales_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = pd.read_excel(io.BytesIO(await sales_file.read()))
    res = service.import_sales_frame(db, raw)          # shared FIXED importer
    extra = f"; excluded {res['voided']:,} voided invoices" if res["has_void_col"] else ""
    return templates.TemplateResponse("backtest_upload.html", dict(
        _upload_state(db), request=request, user=user, page="upload",
        msg=f"Imported {res['lines']:,} sales lines across {res['orders']:,} orders "
            f"({res['tracked']:,} credited to tracked reps; the rest are history-only){extra}."))


@app.post("/upload-receivables")
async def upload_receivables(request: Request, ar_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = pd.read_excel(io.BytesIO(await ar_file.read()))
    res = service.apply_collected_upload(db, raw)     # one month or cumulative, any order — never wipes
    if res is None:
        msg = "No invoice-number column (Document / Invoice / SOP Number) found in the paid file."
    else:
        msg = service.collected_upload_message(res) + " Payable-now updates on the dashboard."
    return templates.TemplateResponse("backtest_upload.html", dict(
        _upload_state(db), request=request, user=user, page="upload", ar_msg=msg))


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
    return templates.TemplateResponse("backtest_upload.html", dict(
        _upload_state(db), request=request, user=user, page="upload", void_msg=msg))


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
    r = service.run_cumulative_growth(db, with_comparison=False)
    months = r["months"]
    my_team = next((t for t, ms in r.get("team_members", {}).items() if name in ms), None)
    if not months:
        return templates.TemplateResponse("backtest_me.html", {
            "request": request, "user": user, "page": "me", "lang": lang, "name": name,
            "months": [], "traj": [], "accounts": [], "k": {}, "my_team": my_team, "members": [],
            "growth_active": r.get("growth_active", True), "growth_start": r["growth_start"]})
    s = service.get_settings(db)
    _, _, team = service.attribution_maps(db)
    growth_active = r.get("growth_active", True)
    contrib = contribution_by_rep_month(db, months, team, float(s["item_rate"]))
    acq_pay = acquisition_by_rep_month(db, months, team, s)[0] if growth_active else {}
    # growth numbers are the TEAM's; the rep's share of the pay is an equal split. ty/ly/gap columns show the
    # team's book so the rep can see what the share was earned on.
    members = r.get("team_members", {}).get(my_team, [])
    team_traj = r["trajectory"].get(my_team) if my_team else None
    my_traj = r.get("rep_trajectory", {}).get(name)
    traj = []
    for i, mm in enumerate(months):
        t = team_traj[i] if team_traj else dict(ty_book=0.0, ly_book=0.0, cum_growth=0.0)
        my_pay = float(my_traj[i]["pay"]) if my_traj else 0.0
        c = contrib.get((name, mm), dict(n_items=0, bonus=0.0))
        a_mo = acq_pay.get((name, mm), 0.0)
        traj.append(dict(month=mm, ty=float(t["ty_book"]), ly=float(t["ly_book"]),
                         gap=float(t["ty_book"]) - float(t["ly_book"]), cum=float(t["cum_growth"]),
                         pay=my_pay, n_items=c["n_items"], contrib=c["bonus"], acq=a_mo,
                         total=my_pay + c["bonus"] + a_mo))
    growth_cycle = float(my_traj[-1]["cum_pay"]) if my_traj else 0.0
    contrib_cycle = sum(x["contrib"] for x in traj)
    acq_cycle = sum(x["acq"] for x in traj)
    earned_cycle_only = growth_cycle + contrib_cycle + acq_cycle
    # pay comes from the LEDGER (every chapter, open until collected and paid) — not just this cycle, so
    # nothing a rep earned before the cycle rolled over goes missing.
    led = service.pay_ledger(db)["totals"].get(name, {})
    earned = led.get("earned", earned_cycle_only)
    frac = (led.get("collectable", 0.0) / earned) if earned else 0.0
    names = service.customer_names(db)
    acc = r["accounts"]
    accounts = []
    if len(acc) and my_team:
        for a in acc[acc["holder"] == my_team].sort_values("growth", ascending=False).itertuples(index=False):
            accounts.append(dict(name=names.get(a.account, a.account), ty=float(a.ty_profit),
                                 ly=float(a.ly_profit), gap=float(a.growth), is_young=bool(a.is_young)))
    already = led.get("paid", 0.0)
    rr = r["reps"]; me_row = rr[rr["associate"] == name] if len(rr) else rr
    target = float(me_row.iloc[0]["target"]) if len(me_row) and me_row.iloc[0]["target"] is not None else None
    net = float(team_traj[-1]["cum_growth"]) if team_traj else 0.0
    k = dict(cum_gap=net, earned=earned, earned_cycle=earned_cycle_only,
             growth_cycle=growth_cycle, contrib_cycle=contrib_cycle, acq_cycle=acq_cycle,
             collected_pct=frac * 100.0, paid=already, payable=led.get("owed", 0.0),
             unreleased=led.get("unreleased", 0.0), carried=led.get("owed_prior", 0.0),
             target=target, target_pct=(min(100.0, max(0.0, net) / target * 100.0) if target else None))
    return templates.TemplateResponse("backtest_me.html", {
        "request": request, "user": user, "page": "me", "lang": lang, "name": name,
        "months": months, "traj": traj, "accounts": accounts, "k": k,
        "my_team": my_team, "members": members,
        "rate": r["cumulative_rate"], "fiscal_start": r["fiscal_start"], "as_of": r["as_of"],
        "growth_active": growth_active, "growth_start": r["growth_start"]})


# ---------- quiet / silent accounts (haven't ordered in a while) ----------
@app.get("/quiet", response_class=HTMLResponse)
def quiet_accounts(request: Request, db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("backtest_quiet.html", dict(
        service.review_counts(db), request=request, user=user, page="quiet", lang="en",
        rows=service.flag_silent_accounts(db), mine=False))


@app.get("/me/quiet", response_class=HTMLResponse)
def rep_quiet(request: Request, lang: str = "zh", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role != "rep":
        return RedirectResponse("/quiet", status_code=303)
    return templates.TemplateResponse("backtest_quiet.html", {
        "request": request, "user": user, "page": "mequiet", "lang": lang,
        "rows": service.flag_silent_accounts(db, associate=user.associate_name), "mine": True})


# ---------- guides (manager + rep, EN / 中文) ----------
@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request, lang: str = "en", db: Session = Depends(get_db)):
    user = _guard(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    s = service.get_settings(db)
    return templates.TemplateResponse("backtest_guide.html", {
        "request": request, "user": user, "page": "guide", "lang": lang,
        "teams": service.team_members(db), "pct": float(cfg.TEAM_OWNERSHIP_PCT) * 100,
        "window_months": cfg.TEAM_WINDOW_MONTHS,
        "growth_start": pd.Timestamp(s.get("growth_start", "2026-10-01")),
        "growth_live": pd.Timestamp(s.get("program_start", "2026-08-01")) >= pd.Timestamp(s.get("growth_start", "2026-10-01")),
        "rate": float(s.get("cumulative_rate", 0.05)), "accel": float(s.get("growth_accel_rate", 0.075)),
        "default_target": float(s.get("growth_target_default", 0.06)), "item_rate": float(s["item_rate"]),
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
        "teams": service.team_members(db),
        "my_team": next((t for t, ms in service.team_members(db).items()
                         if user.associate_name in ms), None),
        "growth_start": pd.Timestamp(s.get("growth_start", "2026-10-01")),
        "growth_live": pd.Timestamp(s.get("program_start", "2026-08-01")) >= pd.Timestamp(s.get("growth_start", "2026-10-01")),
        "rate": float(s.get("cumulative_rate", 0.05)), "accel": float(s.get("growth_accel_rate", 0.075)),
        "item_rate": float(s["item_rate"]),
        "flat_small": int(float(s["acq_flat_small"])), "flat_medium": int(float(s["acq_flat_medium"])),
        "flat_large": int(float(s["acq_flat_large"]))})
