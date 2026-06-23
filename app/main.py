"""FastAPI manager dashboard — login, overview, associate drilldown/overrides/award,
upload (item-level), per-period constrained items, closure candidates, settings, export."""
import io, math, datetime as dt
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
from .config import SECRET_KEY
from . import service

Base.metadata.create_all(engine)
app = FastAPI(title="W&T Sales Scorecard")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=8 * 3600)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def current_user(request: Request, db: Session):
    uid = request.session.get("uid")
    return db.get(M.User, uid) if uid else None


def audit(db, user, action, entity, details):
    db.add(M.AuditLog(user_id=user.user_id if user else None, action=action, entity=str(entity), details=details))
    db.commit()


def _clean(card):
    """pandas turns None into float NaN in numeric columns; convert back so Jinja's `is none` works."""
    if not card:
        return card
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in card.items()}


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
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Wrong username or password"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- team overview ----------
@app.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_db), p: int = None,
             pool: float = None, defend: float = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    res, period, settings, nav = service.run_engine(db, p)
    cards = [_clean(c) for c in res["scorecards"].to_dict("records")] if len(res["scorecards"]) else []
    pool = settings.get("bonus_pool", 1000) if pool is None else pool
    defend_pct = (settings.get("defend_pct", 0.35) if defend is None else defend / 100.0)
    alloc = service.allocate_bonus(cards, pool, defend_pct)
    awards = {a.associate: a for a in db.query(M.Award).filter(M.Award.period_id == period.period_id)}
    last_import = db.query(M.SalesLine).order_by(M.SalesLine.imported_at.desc()).first()
    return templates.TemplateResponse("overview.html", {
        "request": request, "user": user, "cards": cards, "period": period, "nav": nav,
        "market_drift": round((res["market_drift"] - 1) * 100, 1),
        "awards": awards, "data_through": period.window_end, "alloc": alloc,
        "pool": round(float(pool)), "defend_pct": round(defend_pct * 100),
        "imported_at": last_import.imported_at if last_import else None})


# ---------- associate drilldown + overrides + award ----------
@app.get("/associate/{name}", response_class=HTMLResponse)
def associate(name: str, request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    res, period, settings, nav = service.run_engine(db, p)
    names = service.customer_names(db)
    lines = res["lines"]
    sub = lines[lines["associate"] == name].copy() if len(lines) else lines
    rows = []
    if len(sub):
        for _, r in sub.iterrows():
            account_id = r["account"]
            target = r["profit_target"]
            perf = ((r["actual_profit"] / target - 1) * 100) if (pd.notna(target) and target) else None
            real = ((r["volume_dollars"] / r["baseline_revenue_share"]) * 100) if r["baseline_revenue_share"] else None
            rows.append(dict(
                account=account_id, name=names.get(account_id, account_id),
                actual_profit=round(r["actual_profit"] or 0),
                profit_target=(round(target) if pd.notna(target) else None),
                perf=(round(perf, 1) if perf is not None else None),
                real_growth=(round(real, 1) if real is not None else None),
                status=r["status"], tier=r["tier"], proposed=service.proposed_rebaseline(res, account_id)))
        rows.sort(key=lambda x: (not (x["status"] != "scored" or (x["perf"] or 0) < -20), -(x["actual_profit"] or 0)))
    cards = [_clean(c) for c in res["scorecards"].to_dict("records")] if len(res["scorecards"]) else []
    card = next((c for c in cards if c["associate"] == name), None)
    alloc = service.allocate_bonus(cards, settings.get("bonus_pool", 1000), settings.get("defend_pct", 0.35))
    proposed_bonus = alloc.get(name)
    actions = {a.account: a for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id)}
    award = db.query(M.Award).filter(M.Award.period_id == period.period_id, M.Award.associate == name).first()
    return templates.TemplateResponse("associate.html", {
        "request": request, "user": user, "name": name, "card": card, "rows": rows,
        "actions": actions, "award": award, "period": period, "nav": nav, "proposed_bonus": proposed_bonus})


@app.post("/override")
def override(request: Request, account: str = Form(...), associate: str = Form(...),
             status: str = Form(...), rebaseline_value: float = Form(None),
             called: str = Form(None), note: str = Form(""), p: int = Form(None),
             db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    period, idx, is_current = resolve_p(db, p)
    if not is_current:
        return RedirectResponse(f"/associate/{associate}?p={idx}", status_code=303)
    a = db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                         M.ManagerAction.account == account).first()
    if not a:
        a = M.ManagerAction(period_id=period.period_id, account=account)
        db.add(a)
    a.associate = associate
    a.status = status
    a.rebaseline_value = rebaseline_value if status == "rebaseline" else None
    a.called = (called == "on")
    a.note = note
    a.user_id = user.user_id
    a.created_at = dt.datetime.utcnow()
    db.commit()
    audit(db, user, "override", f"account:{account}",
          {"period": period.period_id, "status": status, "value": rebaseline_value, "called": a.called, "note": note})
    return RedirectResponse(f"/associate/{associate}?p={idx}", status_code=303)


@app.post("/award")
def set_award(request: Request, associate: str = Form(...), award_amount: float = Form(0),
              fine_amount: float = Form(0), note: str = Form(""), p: int = Form(None),
              db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    period, idx, is_current = resolve_p(db, p)
    if not is_current:
        return RedirectResponse(f"/associate/{associate}?p={idx}", status_code=303)
    aw = db.query(M.Award).filter(M.Award.period_id == period.period_id, M.Award.associate == associate).first()
    if not aw:
        aw = M.Award(period_id=period.period_id, associate=associate)
        db.add(aw)
    aw.award_amount, aw.fine_amount, aw.note = award_amount, fine_amount, note
    aw.user_id, aw.created_at = user.user_id, dt.datetime.utcnow()
    db.commit()
    audit(db, user, "set_award", f"associate:{associate}",
          {"period": period.period_id, "award": award_amount, "fine": fine_amount})
    return RedirectResponse(f"/associate/{associate}?p={idx}", status_code=303)


# ---------- per-period constrained items ----------
@app.get("/constrained", response_class=HTMLResponse)
def constrained_page(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    _res, period, _s, nav = service.run_engine(db, p)
    current = db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period.period_id).all()
    # auto-detect candidates from the latest window (high revenue share + volatile qty)
    df = service.load_lines_df(db)
    candidates = []
    if len(df):
        df = df[df["document_date"] > pd.to_datetime(period.window_start)]
        if len(df):
            desc = (db.query(M.SalesLine.item_number, M.SalesLine.item_description).distinct())
            num_to_desc = {i: d for i, d in desc}
            summary = df.groupby("item_number").agg(revenue=("extended_price", "sum")).sort_values("revenue", ascending=False).head(10)
            total = summary.revenue.sum() or 1
            candidates = [{"item_number": i, "description": num_to_desc.get(i, ""),
                           "revenue": round(row.revenue), "share": round(row.revenue / df["extended_price"].sum() * 100, 1)}
                          for i, row in summary.iterrows()]
    return templates.TemplateResponse("constrained.html", {
        "request": request, "user": user, "period": period, "nav": nav,
        "current": current, "candidates": candidates})


@app.post("/constrained/add")
def constrained_add(request: Request, item_number: str = Form(...), note: str = Form(""),
                    p: int = Form(None), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    period, idx, is_current = resolve_p(db, p)
    existing = db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period.period_id,
                                                  M.ConstrainedItem.item_number == item_number.strip()).first()
    if not existing:
        db.add(M.ConstrainedItem(period_id=period.period_id, item_number=item_number.strip(),
                                 note=note, user_id=user.user_id))
        db.commit()
        audit(db, user, "constrained_add", f"item:{item_number}", {"period": period.period_id})
    return RedirectResponse(f"/constrained?p={idx}", status_code=303)


@app.post("/constrained/remove")
def constrained_remove(request: Request, item_number: str = Form(...), p: int = Form(None),
                       db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    period, idx, _ = resolve_p(db, p)
    db.query(M.ConstrainedItem).filter(M.ConstrainedItem.period_id == period.period_id,
                                       M.ConstrainedItem.item_number == item_number.strip()).delete()
    db.commit()
    return RedirectResponse(f"/constrained?p={idx}", status_code=303)


# ---------- closure candidates (silence detector) ----------
@app.get("/closures", response_class=HTMLResponse)
def closures_page(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    _res, period, _s, nav = service.run_engine(db, p)
    candidates = service.flag_silent_accounts(db)
    exempt = {a.account for a in db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                                                  M.ManagerAction.status == "exempt")}
    return templates.TemplateResponse("closures.html", {
        "request": request, "user": user, "period": period, "nav": nav,
        "candidates": candidates, "exempt": exempt})


@app.post("/closures/exempt")
def closures_exempt(request: Request, account: str = Form(...), note: str = Form(""),
                    p: int = Form(None), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    period, idx, _ = resolve_p(db, p)
    a = db.query(M.ManagerAction).filter(M.ManagerAction.period_id == period.period_id,
                                         M.ManagerAction.account == account).first()
    if not a:
        a = M.ManagerAction(period_id=period.period_id, account=account)
        db.add(a)
    a.status = "exempt"; a.note = note or "confirmed closed"; a.user_id = user.user_id
    a.created_at = dt.datetime.utcnow()
    db.commit()
    audit(db, user, "closure_exempt", f"account:{account}", {"period": period.period_id})
    return RedirectResponse(f"/closures?p={idx}", status_code=303)


# ---------- data upload (item-level; idempotent by sop_number) ----------
@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("upload.html", {"request": request, "user": user, "msg": None})


@app.post("/upload")
async def upload(request: Request, sales_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    prefix_map, variant_map, sales_team = service.attribution_maps(db)
    raw = pd.read_excel(io.BytesIO(await sales_file.read()))
    sales = raw[raw["SOP Type"] == "Invoice"].copy()
    for col in ["Extended Price", "Extended Cost", "QTY", "Unit Price", "Unit Cost"]:
        sales[col] = pd.to_numeric(sales[col], errors="coerce")
    sales["Document Date"] = pd.to_datetime(sales["Document Date"], errors="coerce")
    sales["associate"] = sales["Batch Number"].apply(lambda b: service.resolve_associate(b, prefix_map, variant_map))
    sales = sales[sales["associate"].isin(sales_team)].dropna(subset=["Document Date"])

    # idempotent: replace all existing lines for each uploaded sop_number
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


# ---------- settings & export ----------
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("settings.html", {"request": request, "user": user,
                                                        "settings": service.get_settings(db)})


@app.get("/export.csv")
def export_csv(request: Request, db: Session = Depends(get_db), p: int = None):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    res, period, _, _ = service.run_engine(db, p)
    df = res["scorecards"].copy()
    awards = {a.associate: a for a in db.query(M.Award).filter(M.Award.period_id == period.period_id)}
    if len(df):
        df["award_usd"] = df["associate"].map(lambda a: awards[a].award_amount if a in awards else 0)
        df["fine_usd"] = df["associate"].map(lambda a: awards[a].fine_amount if a in awards else 0)
    buf = io.StringIO(); df.to_csv(buf, index=False); buf.seek(0)
    fn = f"wandt_scorecard_{period.window_end}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fn}"})
