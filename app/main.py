"""FastAPI dashboard — manager views (team / rep drilldown / overrides / award / constrained /
closures / upload / settings / export) and a rep-facing goal dashboard (/me)."""
import io, datetime as dt
import pandas as pd
from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
import os

from .db import get_db, engine, Base
from . import models as M
from .auth import verify_password
from .config import SECRET_KEY, DEFAULTS
from . import service

Base.metadata.create_all(engine)
app = FastAPI(title="W&T Sales Scorecard")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=8 * 3600)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

EDITABLE_DIALS = ["item_rate", "growth_window_weeks", "size_band_count", "growth_stretch_pct",
                  "growth_payout_rate", "glide_alpha", "min_baseline_ratio", "jump_multiple",
                  "growth_review_min", "full_time_hours",
                  "part_time_factor", "acq_revenue_pct", "acq_ramp_periods", "fine_amount"]


def current_user(request: Request, db: Session):
    uid = request.session.get("uid")
    return db.get(M.User, uid) if uid else None


def audit(db, user, action, entity, details):
    db.add(M.AuditLog(user_id=user.user_id if user else None, action=action, entity=str(entity), details=details))
    db.commit()


def resolve_p(db, p):
    ww = service.get_settings(db)["window_weeks"]
    if p is None:
        _, p, _ = service.period_grid(db, ww)
    period, _as_of, idx, _imin, _icur, is_current = service.resolve_period(db, p, ww)
    return period, idx, is_current


# ---------- login ----------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(M.User).filter(M.User.username == username, M.User.is_active == True).first()
    if user and verify_password(password, user.password_hash):
        request.session["uid"] = user.user_id
        return RedirectResponse("/me" if user.role == "rep" else "/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Wrong username or password"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- bonus guide (managers + reps; EN / 中文) ----------
GUIDES_DIR = os.path.join(os.path.dirname(__file__), "guides")


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request, db: Session = Depends(get_db), lang: str = "en"):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    import markdown as _md
    lang = "zh" if lang == "zh" else "en"
    path = os.path.join(GUIDES_DIR, f"explainer_{lang}.md")
    with open(path, encoding="utf-8") as f:
        html = _md.markdown(f.read(), extensions=["extra", "sane_lists"])
    return templates.TemplateResponse("guide.html", {"request": request, "user": user, "body": html, "lang": lang})


# ---------- rep goal dashboard ----------
@app.get("/me", response_class=HTMLResponse)
def my_goal(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role == "rep" and not user.associate_name:
        return RedirectResponse("/logout", status_code=303)
    name = user.associate_name if user.role == "rep" else None
    if not name:  # a manager hitting /me with no rep -> send to team page
        return RedirectResponse("/", status_code=303)
    goal = service.compute_rep_goal(db, name, p)
    return templates.TemplateResponse("me.html", {"request": request, "user": user, "g": goal, "viewer": "self"})


@app.get("/rep/{name}", response_class=HTMLResponse)
def rep_goal_view(name: str, request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login" if not user else "/me", status_code=303)
    goal = service.compute_rep_goal(db, name, p)
    return templates.TemplateResponse("me.html", {"request": request, "user": user, "g": goal, "viewer": "manager"})


# ---------- team overview (manager) ----------
@app.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role == "rep":
        return RedirectResponse("/me", status_code=303)
    res, period, settings, nav, as_of = service.run_period_bonus(db, p)
    cards = res["scorecards"].sort_values("total_bonus", ascending=False).to_dict("records") if len(res["scorecards"]) else []
    awards = {a.associate: a for a in db.query(M.Award).filter(M.Award.period_id == period.period_id)}
    last_import = db.query(M.SalesLine).order_by(M.SalesLine.imported_at.desc()).first()
    return templates.TemplateResponse("overview.html", {
        "request": request, "user": user, "cards": cards, "period": period, "nav": nav,
        "awards": awards, "data_through": as_of,
        "imported_at": last_import.imported_at if last_import else None})


# ---------- rep drilldown + overrides + award (manager) ----------
@app.get("/associate/{name}", response_class=HTMLResponse)
def associate(name: str, request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login" if not user else "/me", status_code=303)
    res, period, settings, nav, as_of = service.run_period_bonus(db, p)
    names = service.customer_names(db)
    accounts = res["accounts"]
    sub = accounts[accounts["associate"] == name].copy() if len(accounts) else accounts
    rows = []
    if len(sub):
        for _, r in sub.iterrows():
            target = r["account_target"]
            sales = float(r["rep_quarter_sales"])               # trailing-quarter sales
            perf = ((sales / target - 1) * 100) if (target and pd.notna(target)) else None
            rows.append(dict(account=r["account"], name=names.get(r["account"], r["account"]),
                             sales=round(sales), counted=round(sales - float(r["held_back"])),
                             target=(round(float(target)) if (target and pd.notna(target)) else None),
                             perf=(round(perf, 0) if perf is not None else None),
                             status=r["status"], capped=bool(r["capped"]), held_back=int(r["held_back"])))
        rows.sort(key=lambda x: (not x["capped"], x["status"] != "mature", -(x["sales"] or 0)))
    card = next((c for c in res["scorecards"].to_dict("records") if c["associate"] == name), None)
    actions = {a.account: a for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id)}
    award = db.query(M.Award).filter(M.Award.period_id == period.period_id, M.Award.associate == name).first()
    return templates.TemplateResponse("associate.html", {
        "request": request, "user": user, "name": name, "card": card, "rows": rows,
        "actions": actions, "award": award, "period": period, "nav": nav})


@app.post("/override")
def override(request: Request, account: str = Form(...), associate: str = Form(...),
             status: str = Form(...), note: str = Form(""), p: int = Form(None),
             db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    period, idx, is_current = resolve_p(db, p)
    if is_current:
        a = db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                             M.ManagerAction.account == account).first()
        if not a:
            a = M.ManagerAction(period_id=period.period_id, account=account); db.add(a)
        a.associate = associate; a.status = status; a.note = note
        a.user_id = user.user_id; a.created_at = dt.datetime.utcnow()
        db.commit()
        audit(db, user, "override", f"account:{account}", {"period": period.period_id, "status": status})
    return RedirectResponse(f"/associate/{associate}?p={idx}", status_code=303)


@app.post("/award")
def set_award(request: Request, associate: str = Form(...), award_amount: float = Form(0),
              fine_amount: float = Form(0), note: str = Form(""), p: int = Form(None),
              db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    period, idx, is_current = resolve_p(db, p)
    if is_current:
        aw = db.query(M.Award).filter(M.Award.period_id == period.period_id, M.Award.associate == associate).first()
        if not aw:
            aw = M.Award(period_id=period.period_id, associate=associate); db.add(aw)
        aw.award_amount, aw.fine_amount, aw.note = award_amount, fine_amount, note
        aw.user_id, aw.created_at = user.user_id, dt.datetime.utcnow()
        db.commit()
        audit(db, user, "set_award", f"associate:{associate}", {"period": period.period_id, "award": award_amount})
    return RedirectResponse(f"/associate/{associate}?p={idx}", status_code=303)


# ---------- per-period constrained items ----------
@app.get("/constrained", response_class=HTMLResponse)
def constrained_page(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    _res, period, _s, nav, _as_of = service.run_period_bonus(db, p)
    current = db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period.period_id).all()
    df = service.load_lines_df(db)
    candidates = []
    if len(df):
        df = df[df["document_date"] > pd.to_datetime(period.window_start)]
        if len(df):
            num_to_desc = {i: d for i, d in db.query(M.SalesLine.item_number, M.SalesLine.item_description).distinct()}
            summary = df.groupby("item_number").agg(revenue=("extended_price", "sum")).sort_values("revenue", ascending=False).head(10)
            total = df["extended_price"].sum() or 1
            candidates = [{"item_number": i, "description": num_to_desc.get(i, ""),
                           "revenue": round(row.revenue), "share": round(row.revenue / total * 100, 1)}
                          for i, row in summary.iterrows()]
    return templates.TemplateResponse("constrained.html", {
        "request": request, "user": user, "period": period, "nav": nav, "current": current, "candidates": candidates})


@app.post("/constrained/add")
def constrained_add(request: Request, item_number: str = Form(...), note: str = Form(""),
                    p: int = Form(None), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    period, idx, _ = resolve_p(db, p)
    if not db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period.period_id,
                                              M.ConstrainedItem.item_number == item_number.strip()).first():
        db.add(M.ConstrainedItem(period_id=period.period_id, item_number=item_number.strip(),
                                 note=note, user_id=user.user_id)); db.commit()
    return RedirectResponse(f"/constrained?p={idx}", status_code=303)


@app.post("/constrained/remove")
def constrained_remove(request: Request, item_number: str = Form(...), p: int = Form(None),
                       db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    period, idx, _ = resolve_p(db, p)
    db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period.period_id,
                                       M.ConstrainedItem.item_number == item_number.strip()).delete()
    db.commit()
    return RedirectResponse(f"/constrained?p={idx}", status_code=303)


# ---------- closure candidates ----------
@app.get("/closures", response_class=HTMLResponse)
def closures_page(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    _res, period, _s, nav, _as_of = service.run_period_bonus(db, p)
    candidates = service.flag_silent_accounts(db)
    exempt = {a.account for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                                                  M.ManagerAction.status == "exempt")}
    return templates.TemplateResponse("closures.html", {
        "request": request, "user": user, "period": period, "nav": nav, "candidates": candidates, "exempt": exempt})


@app.post("/closures/exempt")
def closures_exempt(request: Request, account: str = Form(...), note: str = Form(""),
                    p: int = Form(None), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    period, idx, _ = resolve_p(db, p)
    a = db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                         M.ManagerAction.account == account).first()
    if not a:
        a = M.ManagerAction(period_id=period.period_id, account=account); db.add(a)
    a.status = "exempt"; a.note = note or "confirmed closed"; a.user_id = user.user_id
    a.created_at = dt.datetime.utcnow(); db.commit()
    return RedirectResponse(f"/closures?p={idx}", status_code=303)


# ---------- new-account review (confirm self-acquired vs assigned) ----------
@app.get("/acquisitions", response_class=HTMLResponse)
def acquisitions_page(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    res, period, _s, nav, _as_of = service.run_period_bonus(db, p)
    names = service.customer_names(db)
    accounts = res["accounts"]
    flags = {r.account: r.rep_won for r in db.query(M.AcquisitionReview)}   # rep_won True = confirmed self-acquired
    rows = []
    if len(accounts):
        new = accounts[accounts["status"].isin(["landing", "ramp", "assigned"])]
        for _, r in new.sort_values("rep_quarter_sales", ascending=False).iterrows():
            rows.append(dict(account=r["account"], customer=names.get(r["account"], r["account"]),
                             associate=r["associate"], status=r["status"], sales=round(float(r["rep_quarter_sales"])),
                             self_acquired=bool(flags.get(r["account"], False)))) # default = assigned (not yet confirmed)
    return templates.TemplateResponse("acquisitions.html", {
        "request": request, "user": user, "period": period, "nav": nav, "rows": rows})


@app.post("/acquisitions/flag")
def acquisitions_flag(request: Request, account: str = Form(...), rep_won: str = Form(...),
                      p: int = Form(None), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    _, idx, _ = resolve_p(db, p)
    rev = db.get(M.AcquisitionReview, account) or M.AcquisitionReview(account=account)
    rev.rep_won = (rep_won == "yes"); rev.user_id = user.user_id; rev.created_at = dt.datetime.utcnow()
    db.merge(rev); db.commit()
    service._ENGINE_CACHE.clear()
    audit(db, user, "acq_review", f"account:{account}", {"rep_won": rev.rep_won})
    return RedirectResponse(f"/acquisitions?p={idx}", status_code=303)


# ---------- big-jump review (investigate doublings: customer-driven vs rep-won) ----------
@app.get("/jumps", response_class=HTMLResponse)
def jumps_page(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    res, period, _s, nav, _as_of = service.run_period_bonus(db, p)
    names = service.customer_names(db)
    accounts = res["accounts"]
    rows = []
    if len(accounts):
        flagged = accounts[accounts["capped"] == True]
        for _, r in flagged.sort_values("windfall", ascending=False).iterrows():
            recent = float(r["rep_quarter_sales"])
            est = float(r["established"]) or float(r["account_target"] or 0)
            rows.append(dict(account=r["account"], customer=names.get(r["account"], r["account"]),
                             associate=r["associate"], recent=round(recent),
                             established=round(est), windfall=int(r["windfall"]),
                             ratio=(round(recent / est, 1) if est else None),
                             released=bool(r["released"])))
    return templates.TemplateResponse("jumps.html", {
        "request": request, "user": user, "period": period, "nav": nav, "rows": rows})


@app.post("/jumps/flag")
def jumps_flag(request: Request, account: str = Form(...), associate: str = Form(...),
               decision: str = Form(...), p: int = Form(None), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    period, idx, is_current = resolve_p(db, p)
    if is_current:
        a = db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                             M.ManagerAction.account == account).first()
        if not a:
            a = M.ManagerAction(period_id=period.period_id, account=account); db.add(a)
        # rep-won -> release the windfall; customer-driven (default) -> withhold (status normal)
        a.associate = associate; a.status = "jump_rep" if decision == "rep" else "normal"
        a.user_id = user.user_id; a.created_at = dt.datetime.utcnow()
        db.commit()
        audit(db, user, "jump_review", f"account:{account}", {"period": period.period_id, "decision": decision})
    return RedirectResponse(f"/jumps?p={idx}", status_code=303)


# ---------- item-level upload (idempotent by sop_number) ----------
@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("upload.html", {"request": request, "user": user, "msg": None})


@app.post("/upload")
async def upload(request: Request, sales_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    prefix_map, variant_map, sales_team = service.attribution_maps(db)
    raw = pd.read_excel(io.BytesIO(await sales_file.read()))
    sales = raw[raw["SOP Type"] == "Invoice"].copy()
    for col in ["Extended Price", "Extended Cost", "QTY", "Unit Price", "Unit Cost"]:
        sales[col] = pd.to_numeric(sales[col], errors="coerce")
    sales["Document Date"] = pd.to_datetime(sales["Document Date"], errors="coerce")
    sales["associate"] = sales["Batch Number"].apply(lambda b: service.resolve_associate(b, prefix_map, variant_map))
    sales = sales[sales["associate"].isin(sales_team)].dropna(subset=["Document Date"])
    sop_numbers = {str(s).strip() for s in sales["SOP Number"]}
    if sop_numbers:
        db.query(M.SalesLine).filter(M.SalesLine.sop_number.in_(sop_numbers)).delete(synchronize_session=False)
    n = 0
    for r in sales.to_dict("records"):
        ext_price = float(r["Extended Price"]) if pd.notna(r["Extended Price"]) else None
        ext_cost = float(r["Extended Cost"]) if pd.notna(r["Extended Cost"]) else None
        db.add(M.SalesLine(
            sop_type=str(r["SOP Type"]), sop_number=str(r["SOP Number"]).strip(),
            item_number=str(r["Item Number"]).strip(), item_description=str(r["Item Description"]),
            qty=float(r["QTY"]) if pd.notna(r["QTY"]) else None,
            unit_price=float(r["Unit Price"]) if pd.notna(r["Unit Price"]) else None,
            extended_price=ext_price, unit_cost=float(r["Unit Cost"]) if pd.notna(r["Unit Cost"]) else None,
            extended_cost=ext_cost,
            line_profit=(ext_price - ext_cost) if (ext_price is not None and ext_cost is not None) else None,
            customer_number=str(r["Customer Number"]).strip(), customer_name=str(r["Customer Name"]).strip(),
            document_date=r["Document Date"].date(), batch_number=str(r["Batch Number"]).strip().upper(),
            associate=r["associate"], imported_at=dt.datetime.utcnow()))
        n += 1
    db.commit()
    audit(db, user, "upload", "sales_lines", {"lines": n, "orders": len(sop_numbers)})
    return templates.TemplateResponse("upload.html", {"request": request, "user": user,
        "msg": f"Imported {n:,} rep sales lines across {len(sop_numbers):,} orders."})


# ---------- settings (editable dials) & export ----------
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), saved: int = 0):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("settings.html", {"request": request, "user": user,
        "settings": service.get_settings(db), "editable": EDITABLE_DIALS, "saved": bool(saved)})


@app.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    for key in EDITABLE_DIALS:
        if key in form and str(form[key]).strip() != "":
            row = db.get(M.Setting, key) or M.Setting(key=key)
            row.value = str(form[key]).strip(); db.merge(row)
    db.commit()
    service._ENGINE_CACHE.clear()
    audit(db, user, "settings", "dials", {k: form.get(k) for k in EDITABLE_DIALS if k in form})
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/export.csv")
def export_csv(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    res, period, _s, _nav, _as_of = service.run_period_bonus(db, p)
    df = res["scorecards"].copy()
    awards = {a.associate: a for a in db.query(M.Award).filter(M.Award.period_id == period.period_id)}
    if len(df):
        df["award_usd"] = df["associate"].map(lambda a: awards[a].award_amount if a in awards else 0)
        df["fine_usd"] = df["associate"].map(lambda a: awards[a].fine_amount if a in awards else 0)
    buf = io.StringIO(); df.to_csv(buf, index=False); buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=wandt_bonus_{period.end_date}.csv"})
