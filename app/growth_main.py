"""Standalone GROWTH BACKTEST service (own URL, separate ECS service via APP_MODULE=app.growth_main:app).

One job: show the rep-netted cumulative profit-growth model, period by period, team-style — completely apart
from the main scorecard app so the two never get mixed up. Read-only: pays nothing, writes nothing (login only).

The model (the manager's rule): for each rep, sum the profit gap of ALL accounts they work — account 1 + account
2 + ... , each vs the SAME account a year ago, split by work-share. If the NET is positive, bonus = rate x net
(trued up on the book's running peak, never clawed back); if negative, $0. Backtest window: Jan-Jun 2026 vs 2025.
"""
import os
from fastapi import FastAPI, Request, Depends, Form
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


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("backtest_login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(M.User).filter(M.User.username == username.strip().lower()).first()
    if user and user.role in ("manager", "admin") and verify_password(password, user.password_hash):
        request.session["uid"] = user.user_id
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("backtest_login.html",
                                      {"request": request, "error": "Wrong username or password (managers only)"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def backtest(request: Request, m: str = None, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or user.role == "rep":
        return RedirectResponse("/login", status_code=303)
    r = service.run_cumulative_growth(db)      # memoized; window = fiscal_start_month (Jan) -> data end
    months = r["months"]                       # e.g. ['2026-01', ... '2026-06']
    if not months:
        return templates.TemplateResponse("backtest.html", {"request": request, "user": user, "months": [],
                                                            "rows": [], "m": None, "nav": {}, "rate": 0})
    if m not in months:
        m = months[-1]
    mi = months.index(m)

    rows = []
    for _, x in r["reps"].iterrows():
        rep = x["associate"]
        t = r["trajectory"][rep][mi]
        # ty_book/ly_book = the rep's BOOK this month, share-weighted over the SAME accounts both years,
        # so the row subtracts cleanly: net gap == book this yr - same accounts last yr.
        rows.append(dict(
            associate=rep,
            profit_mo=float(t["ty_book"]), profit_mo_ly=float(t["ly_book"]),
            gap_mo=float(t["ty_book"]) - float(t["ly_book"]),
            cum_gap=float(t["cum_growth"]),
            pay_mo=float(t["pay"]), cum_pay=float(t["cum_pay"]),
        ))
    rows.sort(key=lambda z: -z["cum_pay"])
    team_row = {k: sum(z[k] for z in rows) for k in
                ("profit_mo", "profit_mo_ly", "gap_mo", "cum_gap", "pay_mo", "cum_pay")}
    nav = dict(prev=(months[mi - 1] if mi > 0 else None), next=(months[mi + 1] if mi + 1 < len(months) else None),
               n=mi + 1, total=len(months))
    return templates.TemplateResponse("backtest.html", {
        "request": request, "user": user, "months": months, "m": m, "mi": mi, "nav": nav,
        "rows": rows, "team": team_row, "rate": r["cumulative_rate"],
        "fiscal_start": r["fiscal_start"], "as_of": r["as_of"]})
