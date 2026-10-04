"""The assistant that sits on top of the scorecard — ask it, in your own words, about pay, accounts, invoices.

    "Why is An Cao's collected % so low?"   "Which Team 2 accounts dropped most this quarter?"
    "Chart Ting Ting's monthly earned vs released"   "Who owes us more than 60 days?"

It answers from the scorecard's own records through the read-only tools below, in the language the question
was asked in, and says which number came from where. It never invents a number: a record that is not there is
answered "no data for that". It changes nothing — no payments, no assignments. It can draw a chart (returned as
an image) and, when a SerpAPI key is configured, look things up on the web — clearly marked as from the web.

The loop is the plain tool-use loop: the model asks for a tool, the tool runs against the service layer, the
result goes back, until the model answers. A rep's session only sees that rep's own data.
"""
from __future__ import annotations

import base64
import io
import json
import os
from datetime import datetime

import pandas as pd

from . import service

MODEL = "claude-opus-5"                                             # when BACKBONE=claude
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")   # the default backbone (same as the procurement app)
BACKBONE = os.environ.get("ASSISTANT_BACKBONE", "gemini")           # 'gemini' | 'claude'
MAX_STEPS = 10
MAX_TOKENS = 4000


def _secret(name: str) -> str | None:
    """An API key from the environment (ECS injects it from Secrets Manager) or a local secrets/<NAME> file."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    f = os.path.join(here, "secrets", name)
    if os.path.exists(f):
        return open(f).read().strip() or None
    return None


def configured() -> dict:
    gem, cla = bool(_secret("GEMINI_API_KEY")), bool(_secret("ANTHROPIC_API_KEY"))
    backbone = "gemini" if (BACKBONE == "gemini" and gem) else ("claude" if cla else ("gemini" if gem else None))
    return {"gemini": gem, "claude": cla, "backbone": backbone, "model": (GEMINI_MODEL if backbone == "gemini" else MODEL if backbone else None),
            "web": bool(_secret("SERPAPI_KEY"))}


SYSTEM = """You are the assistant inside the W&T Sales Scorecard, answering the sales manager (or a sales rep) at the computer.

What the scorecard is: a monthly incentive. Every invoice LINE is on file (item code, description, qty, price, cost, profit), so
product questions — what sells, what an account buys, a SKU's trend — are answered with the product tools. Per rep per month: Contribution = line items x rate (individual);
Growth share = the TEAM's cumulative year-over-year profit growth pay on the accounts it owns, split equally (from Oct 2026);
New accounts = flat landing bonus (individual). Pay follows COLLECTION month by month: a month's earnings are RELEASED in
proportion to how much of THAT month's invoices the customers have paid; OWED = released - paid; unpaid money never expires.
Accounts belong to the team that wrote 80% of their orders in the last 12 months; the manager can pin exceptions.

Rules:
1. Numbers about this business (pay, invoices, accounts, growth, collections) come ONLY from the tools. If a tool has no
   record, say "no data for that" — never estimate or invent a figure. Market or general facts may come from web_search,
   and must be marked "(web: source)". Never mix the two.
2. Answer in the language the question was asked in (English or 中文). Plain words, short. Every number carries its unit
   and where it came from, e.g. "(Sep 2026 ledger)", "(invoice INV0398092)", "(assignment page)".
3. Look things up before answering: resolve a customer with find_accounts and a person with reps first.
4. You change nothing. If asked to pay, assign, or edit, explain where in the app the manager does it.
5. Arithmetic you do yourself (totals, differences, percentages) is labelled "my calculation" with the inputs shown.
6. When a chart would say it better than a table, call chart. Keep tables to 8 rows or fewer; the chart can hold more.
7. If the question is ambiguous, say what you assumed in one line and answer; don't interrogate.
8. Plain text only — no markdown headings, bold, or tables. Lists: one item per line starting with "·"."""

TOOLS = [
    {"name": "reps", "description": "The sales reps, their teams, and the house group. Call first when a question names a person or team.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "months", "description": "Which months the scorecard covers (the pay chapters) and today's data date.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "pay", "description": "A rep's pay ledger month by month: contribution, growth share, new-account bonus, earned, collected %, released, paid, owed, waiting. Omit rep for everyone (totals per rep).",
     "input_schema": {"type": "object", "properties": {"rep": {"type": "string"}, "month": {"type": "string", "description": "YYYY-MM, optional"}}}},
    {"name": "invoices", "description": "A rep's invoices in a month: number, date, customer, amount, lines, collected or missing. Filter status to 'missing' or 'collected'.",
     "input_schema": {"type": "object", "properties": {"rep": {"type": "string"}, "month": {"type": "string"}, "status": {"type": "string"}, "n": {"type": "integer"}}, "required": ["rep", "month"]}},
    {"name": "invoice", "description": "One invoice line by line (items, qty, price, cost, profit) and its collected/missing status.",
     "input_schema": {"type": "object", "properties": {"sop_number": {"type": "string"}}, "required": ["sop_number"]}},
    {"name": "find_accounts", "description": "Match a customer name (English or Chinese, partial) to account codes. Call before asking about an account.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "account", "description": "One account: owner team, order shares per team, monthly profit for the last 24 months, first order, who sells it.",
     "input_schema": {"type": "object", "properties": {"account": {"type": "string"}}, "required": ["account"]}},
    {"name": "assignments", "description": "Account ownership: which team owns which account, the shared (unassigned) ones, pins. Filter by team name, 'shared', or a rep name.",
     "input_schema": {"type": "object", "properties": {"view": {"type": "string"}, "n": {"type": "integer"}}}},
    {"name": "growth", "description": "Team growth this cycle: each team's book profit vs the same accounts last year, net gap, target, growth pay, and the accounts driving it (top gainers and losers).",
     "input_schema": {"type": "object", "properties": {"team": {"type": "string"}, "n": {"type": "integer"}}}},
    {"name": "falling_behind", "description": "Accounts behind over the last 3 months: down vs last year and/or below their size band's median. Filter by team.",
     "input_schema": {"type": "object", "properties": {"team": {"type": "string"}, "n": {"type": "integer"}}}},
    {"name": "quiet", "description": "Accounts that have gone quiet (no order for longer than their usual gap).",
     "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}}},
    {"name": "late_payments", "description": "Unpaid invoices by age: buckets, the accounts owing most, oldest invoice. Optional min_days to list only older ones.",
     "input_schema": {"type": "object", "properties": {"min_days": {"type": "integer"}, "team": {"type": "string"}, "n": {"type": "integer"}}}},
    {"name": "new_accounts", "description": "Accounts that first ordered recently: first order, size tier, rep, reviewed as rep-won / house / unreviewed, landing bonus.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "find_products", "description": "Match a product by item code or description (English/Chinese, partial) to item codes. Call before asking about a product.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "product_sales", "description": "One product's sales by month: quantity, revenue, cost, profit, margin, invoices, and who sells it / who buys it. Optional rep or account filter, months back (default 12).",
     "input_schema": {"type": "object", "properties": {"item": {"type": "string"}, "months": {"type": "integer"}, "rep": {"type": "string"}, "account": {"type": "string"}}, "required": ["item"]}},
    {"name": "top_products", "description": "Best (or worst) selling products over the last N months, ranked by revenue, profit, or qty; optional rep or account filter. Also answers 'what does account X buy' and 'what does rep Y sell most'.",
     "input_schema": {"type": "object", "properties": {"months": {"type": "integer"}, "n": {"type": "integer"}, "by": {"type": "string", "description": "revenue | profit | qty"}, "rep": {"type": "string"}, "account": {"type": "string"}, "bottom": {"type": "boolean"}}}},
    {"name": "category_sales", "description": "ALL products whose description or code matches a term (e.g. 'shrimp', 'oyster', 'clam', 'tilapia'), added up: qty, revenue, profit by month, who sells it, who buys it, and the top SKUs inside the category. Use this for any question about a kind of product rather than one SKU. Optional rep/account filter, months back (default 12).",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}, "months": {"type": "integer"}, "rep": {"type": "string"}, "account": {"type": "string"}}, "required": ["q"]}},
    {"name": "settings", "description": "The pay dials: item rate, growth rates and targets, landing bonuses, late-after days, cycle dates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "chart", "description": "Draw a chart from data you already have and show it to the user. kind: 'bar', 'line', or 'barh'. series: list of {name, values}; labels: x-axis labels (same length as values).",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "kind": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}},
                                                       "series": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "values": {"type": "array", "items": {"type": "number"}}}, "required": ["name", "values"]}},
                                                       "y_label": {"type": "string"}, "currency": {"type": "boolean"}}, "required": ["title", "kind", "labels", "series"]}},
    {"name": "web_search", "description": "Search the web (Google via SerpAPI) for market facts, a customer's public info, seasonality, news. Not for the business's own numbers. Returns titles, links, snippets.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}, "n": {"type": "integer"}}, "required": ["q"]}},
]


def _money(x):
    return round(float(x or 0.0), 2)


class Tools:
    """Read-only views over the service layer. `rep_only` scopes a rep's session to their own data."""

    def __init__(self, db, rep_only: str | None = None):
        self.db = db
        self.rep_only = rep_only
        self.images: list = []

    # ---- people / time
    def reps(self):
        _, _, roster = service.attribution_maps(self.db)
        teams = service.teams_table(self.db)
        return {"reps": roster, "teams": [{"name": t["name"], "kind": t["kind"], "members": t["members"],
                                          "auto_rule": t["auto"], "fallback": t["fallback"]} for t in teams],
                "primary_team": service.team_of_rep(self.db)}

    def months(self):
        ch = service.pay_chapters(self.db)
        _lo, hi = service.data_bounds(self.db)
        return {"data_through": str(hi.date()), "chapters": [{"label": c["label"], "from": str(c["start"].date()),
                                                             "to": str(c["end"].date()), "growth_active": c["growth_active"],
                                                             "closed": c["closed"]} for c in ch]}

    # ---- pay
    def pay(self, rep: str | None = None, month: str | None = None):
        if self.rep_only:
            rep = self.rep_only
        ledger = service.pay_ledger(self.db)
        if rep:
            if rep not in ledger["months"]:
                return {"error": f"no rep called {rep}; see reps"}
            rows = ledger["months"][rep]
            if month:
                rows = [r for r in rows if r["month"] == month]
            out = [{k: (_money(v) if isinstance(v, float) else v) for k, v in r.items()
                    if k in ("month", "n_items", "contribution", "growth", "acquisition", "earned", "billed", "collected",
                             "collected_pct", "collectable", "paid", "owed", "unreleased")} for r in rows]
            for o in out:
                o["released"] = o.pop("collectable"); o["waiting"] = o.pop("unreleased")
            t = ledger["totals"][rep]
            return {"rep": rep, "months": out, "totals": {k: _money(v) for k, v in t.items()}}
        return {"totals_by_rep": {r: {k: _money(v) for k, v in t.items()} for r, t in ledger["totals"].items()}}

    def invoices(self, rep: str, month: str, status: str | None = None, n: int | None = None):
        if self.rep_only:
            rep = self.rep_only
        d = service.rep_pay_detail(self.db, rep, show="all")
        if d is None:
            return {"error": f"no rep called {rep}"}
        m = next((x for x in d["months"] if x["month"] == month), None)
        if m is None:
            return {"error": f"{rep} has no invoices in {month}"}
        inv = m["invoices"]
        if status == "missing":
            inv = [i for i in inv if not i["paid"]]
        elif status == "collected":
            inv = [i for i in inv if i["paid"]]
        n = max(1, min(n or 25, 100))
        return {"rep": rep, "month": month, "count": len(inv), "billed": _money(m["billed"]), "collected": _money(m["collected"]),
                "collected_pct": round(m["collected_pct"], 1),
                "invoices": [{"invoice": i["sop_number"], "date": str(i["date"].date()), "customer": i["customer"],
                              "amount": _money(i["amount"]), "lines": i["lines"],
                              "status": "written off" if i["written_off"] else ("collected" if i["paid"] else "missing")} for i in inv[:n]]}

    def invoice(self, sop_number: str):
        inv = service.invoice_detail(self.db, sop_number)
        if inv is None:
            return {"error": f"no invoice {sop_number}"}
        h = inv["header"]
        if self.rep_only and h["associate"] != self.rep_only:
            return {"error": "not your invoice"}
        return {"invoice": h["sop_number"], "customer": h["customer"], "date": str(h["date"]), "written_by": h["associate"],
                "lines": h["n_lines"], "total": _money(h["total"]), "profit": _money(h["profit"]),
                "status": "voided" if h["voided"] else "written off" if h["written_off"] else "collected" if h["collected"] else "missing",
                "items": [{"item": l["item"], "description": l["description"], "qty": l["qty"], "unit_price": _money(l["unit_price"]),
                           "amount": _money(l["extended_price"]), "profit": _money(l["profit"])} for l in inv["lines"]]}

    # ---- accounts
    def find_accounts(self, q: str):
        names = service.customer_names(self.db)
        ql = q.strip().lower()
        hits = [(a, n) for a, n in names.items() if ql in n.lower() or ql in a.lower()]
        return {"matches": [{"account": a, "customer": n} for a, n in hits[:15]], "count": len(hits)}

    def account(self, account: str):
        df = service.active_lines(self.db)
        d = df[df["account"] == account]
        if not len(d):
            return {"error": f"no account {account}; use find_accounts"}
        if self.rep_only and self.rep_only not in set(d["associate"]):
            return {"error": "not one of your accounts"}
        names = service.customer_names(self.db)
        row = next((r for r in service.account_assignments(self.db) if r["account"] == account), None)
        d = d.assign(ym=d["document_date"].dt.to_period("M").astype(str))
        monthly = d.groupby("ym")["line_profit"].sum().tail(24)
        sellers = d[d["document_date"] >= d["document_date"].max() - pd.DateOffset(months=12)].groupby("associate")["extended_price"].sum()
        return {"account": account, "customer": names.get(account, account), "first_order": str(d["document_date"].min().date()),
                "last_order": str(d["document_date"].max().date()),
                "owner": (row or {}).get("team"), "pinned": bool((row or {}).get("manual")), "order_shares": {k: round(v * 100) for k, v in ((row or {}).get("shares") or {}).items()},
                "sellers_12mo_revenue": {k: _money(v) for k, v in sellers.sort_values(ascending=False).items()},
                "monthly_profit": {k: _money(v) for k, v in monthly.items()}}

    def assignments(self, view: str | None = None, n: int | None = None):
        rows = service.account_assignments(self.db)
        if self.rep_only:
            mine = service.team_of_rep(self.db).get(self.rep_only)
            rows = [r for r in rows if r["team"] in (mine, self.rep_only)]
        elif view == "shared":
            rows = [r for r in rows if r["shared"]]
        elif view:
            rows = [r for r in rows if r["team"] == view]
        n = max(1, min(n or 30, 200))
        return {"count": len(rows), "accounts": [{"account": r["account"], "customer": r["customer"], "owner": r["team"],
                                                  "pinned": bool(r["manual"]), "orders_12mo": r["orders"],
                                                  "shares": {k: round(v * 100) for k, v in r["shares"].items()},
                                                  "profit_12mo": _money(r["profit"])} for r in rows[:n]]}

    def growth(self, team: str | None = None, n: int | None = None):
        r = service.run_cumulative_growth(self.db, with_comparison=False)
        if not r.get("growth_active", True):
            return {"note": "growth is not active yet — this is the contribution-only chapter", "starts": str(r["growth_start"].date())}
        if self.rep_only:
            team = service.team_of_rep(self.db).get(self.rep_only)
        teams = r.get("teams")
        out = {"cycle": f"{r['months'][0]} to {r['months'][-1]}", "teams": []}
        names = service.customer_names(self.db)
        for _, t in (teams.iterrows() if teams is not None else []):
            if team and t["team"] != team:
                continue
            accts = [(a, v) for a, v in r["account_monthly"].items() if v["primary"] == t["team"]]
            accts.sort(key=lambda av: -av[1]["gap"])
            k = max(1, min(n or 5, 25))
            out["teams"].append({"team": t["team"], "accounts": int(t["n_accounts"]), "cumulative_net_gap": _money(t["cum_growth"]),
                                 "target": (_money(t["target"]) if t["target"] is not None else None), "growth_pay_so_far": _money(t["earned"]),
                                 "top_gainers": [{"customer": names.get(a, a), "gap": _money(v["gap"])} for a, v in accts[:k]],
                                 "top_losers": [{"customer": names.get(a, a), "gap": _money(v["gap"])} for a, v in accts[-k:][::-1] if v["gap"] < 0]})
        return out

    def falling_behind(self, team: str | None = None, n: int | None = None):
        rows = service.underperforming_accounts(self.db)
        if self.rep_only:
            team = service.team_of_rep(self.db).get(self.rep_only)
        if team:
            rows = [r for r in rows if r["team"] == team]
        rows = [r for r in rows if r["flagged"]]
        n = max(1, min(n or 15, 100))
        return {"count": len(rows), "window": "last 3 months vs the same 3 months last year",
                "accounts": [{"customer": r["customer"], "owner": r["team"], "profit_3mo": _money(r["ty_profit"]), "last_year": _money(r["ly_profit"]),
                              "change": _money(r["change"]), "growth_pct": (round(r["growth_pct"] * 100, 1) if r["growth_pct"] is not None else None),
                              "band_median_pct": (round(r["band_median"] * 100, 1) if r.get("band_median") is not None else None),
                              "flags": [f for f, on in (("down", r["negative"]), ("below band", r["below_band"])) if on]} for r in rows[:n]]}

    def quiet(self, n: int | None = None):
        rows = service.flag_silent_accounts(self.db, associate=self.rep_only) if self.rep_only else service.flag_silent_accounts(self.db)
        n = max(1, min(n or 15, 100))
        return {"count": len(rows), "accounts": rows[:n]}

    def late_payments(self, min_days: int | None = None, team: str | None = None, n: int | None = None):
        data = service.late_invoices(self.db)
        inv = data["invoices"]
        if self.rep_only:
            inv = [i for i in inv if i["rep"] == self.rep_only]
        if min_days:
            inv = [i for i in inv if i["age"] >= min_days]
        if team and not self.rep_only:
            keep = {a["account"] for a in data["accounts"] if (a["team"] or "shared") == team}
            inv = [i for i in inv if i["account"] in keep]
        by = {}
        for i in inv:
            b = by.setdefault(i["account"], {"customer": i["customer"], "invoices": 0, "amount": 0.0, "oldest_days": 0})
            b["invoices"] += 1; b["amount"] += i["amount"]; b["oldest_days"] = max(b["oldest_days"], i["age"])
        accts = sorted(by.values(), key=lambda b: -b["amount"])
        n = max(1, min(n or 15, 100))
        return {"as_of": str(data["as_of"].date()), "late_after_days": data["late_after"],
                "buckets": [{"range": b["label"], "invoices": b["n"], "amount": _money(b["amount"])} for b in data["buckets"]],
                "total_unpaid": _money(sum(i["amount"] for i in inv)), "accounts": [dict(a, amount=_money(a["amount"])) for a in accts[:n]]}

    def new_accounts(self):
        s = service.get_settings(self.db)
        _, _, roster = service.attribution_maps(self.db)
        _lo, hi = service.data_bounds(self.db)
        months = [str(m) for m in pd.period_range((hi - pd.DateOffset(months=11)).to_period("M"), hi.to_period("M"), freq="M")]
        _pay, review = service.acquisition_by_rep_month(self.db, months, roster, s)
        if self.rep_only:
            review = [r for r in review if r["rep"] == self.rep_only]
        return {"count": len(review), "accounts": [{k: (_money(v) if isinstance(v, float) else v) for k, v in r.items()} for r in review]}

    # ---- products (every invoice line carries item, qty, price, cost, profit)
    def _lines(self, months: int | None = None, rep: str | None = None, account: str | None = None):
        df = service.active_lines(self.db)
        if self.rep_only:
            rep = self.rep_only
        if months:
            df = df[df["document_date"] > df["document_date"].max() - pd.DateOffset(months=int(months))]
        if rep:
            df = df[df["associate"] == rep]
        if account:
            df = df[df["account"] == account]
        return df

    def find_products(self, q: str):
        descs = service.item_descriptions(self.db)                 # {item_number: decoded description}
        ql = q.strip().lower()
        hits = [(i, d) for i, d in descs.items() if ql in str(d).lower() or ql in str(i).lower()]
        return {"matches": [{"item": i, "description": d} for i, d in hits[:20]], "count": len(hits)}

    def product_sales(self, item: str, months: int | None = 12, rep: str | None = None, account: str | None = None):
        d = self._lines(months, rep, account)
        d = d[d["item_number"] == item]
        if not len(d):
            return {"error": f"no sales of {item} in that window; use find_products to check the code"}
        desc = service.item_descriptions(self.db).get(item, item)
        d = d.assign(ym=d["document_date"].dt.to_period("M").astype(str))
        by_m = d.groupby("ym").agg(qty=("qty", "sum"), revenue=("extended_price", "sum"), cost=("extended_cost", "sum"),
                                   profit=("line_profit", "sum"), invoices=("sop_number", "nunique"))
        names = service.customer_names(self.db)
        sellers = d.groupby("associate")["extended_price"].sum().sort_values(ascending=False)
        buyers = d.groupby("account")["extended_price"].sum().sort_values(ascending=False).head(8)
        rev = float(d["extended_price"].sum()); prof = float(d["line_profit"].sum())
        return {"item": item, "description": desc, "window_months": months, "qty": _money(d["qty"].sum()), "revenue": _money(rev),
                "profit": _money(prof), "margin_pct": round(100 * prof / rev, 1) if rev else None, "invoices": int(d["sop_number"].nunique()),
                "by_month": {m: {"qty": _money(r.qty), "revenue": _money(r.revenue), "profit": _money(r.profit), "invoices": int(r.invoices)} for m, r in by_m.iterrows()},
                "sellers_revenue": {k: _money(v) for k, v in sellers.items()},
                "top_buyers_revenue": {names.get(k, k): _money(v) for k, v in buyers.items()}}

    def category_sales(self, q: str, months: int | None = 12, rep: str | None = None, account: str | None = None):
        descs = service.item_descriptions(self.db)
        ql = q.strip().lower()
        items = {i for i, d in descs.items() if ql in str(d).lower() or ql in str(i).lower()}
        if not items:
            return {"error": f"no product matches '{q}'"}
        d = self._lines(months, rep, account)
        d = d[d["item_number"].isin(items)]
        if not len(d):
            return {"q": q, "matching_skus": len(items), "note": "no sales of these products in that window"}
        d = d.assign(ym=d["document_date"].dt.to_period("M").astype(str))
        by_m = d.groupby("ym").agg(qty=("qty", "sum"), revenue=("extended_price", "sum"), profit=("line_profit", "sum"), invoices=("sop_number", "nunique"))
        names = service.customer_names(self.db)
        sellers = d.groupby("associate")["extended_price"].sum().sort_values(ascending=False)
        buyers = d.groupby("account")["extended_price"].sum().sort_values(ascending=False).head(8)
        skus = d.groupby("item_number")["extended_price"].sum().sort_values(ascending=False).head(8)
        rev = float(d["extended_price"].sum()); prof = float(d["line_profit"].sum())
        return {"q": q, "matching_skus": len(items), "skus_sold": int(d["item_number"].nunique()), "window_months": months,
                "rep": self.rep_only or rep, "account": account,
                "qty": _money(d["qty"].sum()), "revenue": _money(rev), "profit": _money(prof),
                "margin_pct": round(100 * prof / rev, 1) if rev else None, "invoices": int(d["sop_number"].nunique()),
                "by_month": {m: {"qty": _money(r.qty), "revenue": _money(r.revenue), "profit": _money(r.profit), "invoices": int(r.invoices)} for m, r in by_m.iterrows()},
                "sellers_revenue": {k: _money(v) for k, v in sellers.items()},
                "top_buyers_revenue": {names.get(k, k): _money(v) for k, v in buyers.items()},
                "top_skus_revenue": {f"{i} {descs.get(i, '')}": _money(v) for i, v in skus.items()}}

    def top_products(self, months: int | None = 3, n: int | None = 15, by: str | None = "revenue", rep: str | None = None,
                     account: str | None = None, bottom: bool = False):
        d = self._lines(months, rep, account)
        if not len(d):
            return {"error": "no sales in that window"}
        col = {"revenue": "extended_price", "profit": "line_profit", "qty": "qty"}.get((by or "revenue").lower(), "extended_price")
        g = d.groupby("item_number").agg(qty=("qty", "sum"), revenue=("extended_price", "sum"), profit=("line_profit", "sum"),
                                         invoices=("sop_number", "nunique"), accounts=("account", "nunique"))
        g = g.sort_values(col.replace("extended_price", "revenue").replace("line_profit", "profit"), ascending=bool(bottom))
        descs = service.item_descriptions(self.db)
        n = max(1, min(n or 15, 50))
        return {"window_months": months, "ranked_by": by, "rep": self.rep_only or rep, "account": account, "distinct_products": int(len(g)),
                "products": [{"item": i, "description": descs.get(i, i), "qty": _money(r.qty), "revenue": _money(r.revenue),
                              "profit": _money(r.profit), "margin_pct": round(100 * r.profit / r.revenue, 1) if r.revenue else None,
                              "invoices": int(r.invoices), "accounts": int(r.accounts)} for i, r in g.head(n).iterrows()]}

    def settings(self):
        s = service.get_settings(self.db)
        keys = ("item_rate", "cumulative_rate", "growth_accel_rate", "growth_target_default", "acq_flat_small", "acq_flat_medium",
                "acq_flat_large", "acq_tier_small_max", "acq_tier_medium_max", "late_after_days", "program_start", "growth_start", "fiscal_start_month")
        return {k: s.get(k) for k in keys} | {k: v for k, v in s.items() if k.startswith("growth_target::")}

    # ---- chart
    def chart(self, title: str, kind: str, labels: list, series: list, y_label: str | None = None, currency: bool = True):
        import warnings
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
        # Chinese labels need a CJK-capable font: Noto Sans CJK in the image, PingFang/Hiragino on a Mac
        plt.rcParams["font.family"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "PingFang SC", "Hiragino Sans GB",
                                       "Microsoft YaHei", "DejaVu Sans", "sans-serif"]
        plt.rcParams["axes.unicode_minus"] = False
        warnings.filterwarnings("ignore", message="Glyph .* missing from font")
        import logging
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)   # "font not found" is expected while it walks the fallback list
        palette = ["#5A48B6", "#2B6CB0", "#1F9D57", "#C0682B", "#B83280", "#2C7A7B"]
        fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=140)
        x = list(range(len(labels)))
        k = max(1, len(series))
        for i, s in enumerate(series):
            vals = [float(v or 0) for v in s["values"]][:len(labels)]
            if kind == "line":
                ax.plot(x[:len(vals)], vals, marker="o", linewidth=2, color=palette[i % 6], label=s["name"])
            elif kind == "barh":
                ax.barh([xi + i / k * 0.8 for xi in x[:len(vals)]], vals, height=0.8 / k, color=palette[i % 6], label=s["name"])
            else:
                ax.bar([xi + i / k * 0.8 - 0.4 + 0.4 / k for xi in x[:len(vals)]], vals, width=0.8 / k, color=palette[i % 6], label=s["name"])
        if kind == "barh":
            ax.set_yticks([xi + 0.4 - 0.4 / k for xi in x]); ax.set_yticklabels(labels, fontsize=8)
            if currency: ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
        else:
            ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8, rotation=0 if len(labels) <= 8 else 45, ha="center" if len(labels) <= 8 else "right")
            if currency: ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.set_title(title, fontsize=11, loc="left", fontweight="bold")
        if y_label: ax.set_ylabel(y_label, fontsize=9)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
        ax.grid(axis="x" if kind == "barh" else "y", color="#eee"); ax.set_axisbelow(True)
        if len(series) > 1: ax.legend(fontsize=8, frameon=False)
        fig.tight_layout()
        buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig)
        self.images.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
        return {"ok": True, "shown": True, "note": "the chart is displayed to the user below your answer; refer to it, don't re-list its numbers"}

    # ---- web
    def web_search(self, q: str, n: int | None = None):
        key = _secret("SERPAPI_KEY")
        if not key:
            return {"error": "web search is not configured (no SERPAPI_KEY) — answer from the scorecard only"}
        import urllib.parse, urllib.request
        qs = urllib.parse.urlencode({"engine": "google", "q": q, "num": max(3, min(n or 8, 10)), "hl": "en", "gl": "us", "api_key": key})
        try:
            with urllib.request.urlopen("https://serpapi.com/search.json?" + qs, timeout=30) as r:
                data = json.loads(r.read())
        except Exception as e:
            return {"error": f"search failed: {type(e).__name__}"}
        out = {"q": q, "results": []}
        ab = data.get("answer_box") or {}
        if ab:
            out["answer_box"] = {k: ab[k] for k in ("title", "answer", "snippet", "link") if k in ab}
        for r in (data.get("organic_results") or [])[: max(3, min(n or 8, 10))]:
            out["results"].append({"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet"), "source": r.get("source")})
        return out

    def call(self, name: str, args: dict):
        fn = getattr(self, name, None)
        if not fn or name.startswith("_") or name == "call":
            return {"error": f"no tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


def _gemini_tools() -> list:
    """The same tools in Gemini's function-declaration shape (no empty parameter objects)."""
    decls = []
    for t in TOOLS:
        d = {"name": t["name"], "description": t["description"]}
        if t["input_schema"].get("properties"):
            d["parameters"] = t["input_schema"]
        decls.append(d)
    return [{"functionDeclarations": decls}]


def _gemini_post(body: dict) -> dict:
    import urllib.request
    key = _secret("GEMINI_API_KEY")
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key},
                                 data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def _run_gemini(convo: list, tools: "Tools", system: str, used: list) -> str:
    """The same loop against Gemini. The model's parts go back verbatim (that keeps its thought signatures,
    which Gemini needs to continue a function-calling turn)."""
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in convo]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "tools": _gemini_tools(), "contents": contents,
            "generationConfig": {"maxOutputTokens": 6000, "temperature": 0.2, "thinkingConfig": {"thinkingLevel": "low"}}}
    for _ in range(MAX_STEPS + 1):
        resp = _gemini_post(body)
        cand = (resp.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        if not calls:
            return text
        contents.append({"role": "model", "parts": parts})
        parts_out = []
        for c in calls:
            args = dict(c.get("args") or {})
            out = tools.call(c["name"], args)
            used.append({"tool": c["name"], "args": args})
            txt = json.dumps(out, ensure_ascii=False, default=str)
            res = json.loads(txt) if len(txt) <= 14000 else {"truncated": txt[:14000]}
            parts_out.append({"functionResponse": {"name": c["name"], "response": {"result": res}}})
        contents.append({"role": "user", "parts": parts_out})
    contents.append({"role": "user", "parts": [{"text": "Enough lookups. Answer now from what you already found; say clearly what you could not find."}]})
    body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
    parts = ((_gemini_post(body).get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))


def run(messages: list, db, rep_only: str | None = None, client=None) -> dict:
    """One turn: the conversation so far (role/content pairs from the page) -> reply text, any charts, tools used."""
    tools = Tools(db, rep_only=rep_only)
    convo = [{"role": m["role"], "content": m["content"]} for m in messages
             if m.get("role") in ("user", "assistant") and m.get("content")]
    if not convo or convo[-1]["role"] != "user":
        return {"reply": "Ask me something.", "images": [], "used": []}
    today = datetime.now().strftime("%Y-%m-%d")
    system = SYSTEM + f"\nToday is {today}." + (f"\nThis session belongs to the sales rep {rep_only}: answer only about their own pay and accounts." if rep_only else
                                                 "\nThis session belongs to the manager.")
    used, reply = [], ""
    cfg = configured()
    if client is None and cfg["backbone"] == "gemini":
        try:
            reply = _run_gemini(convo, tools, system, used)
        except Exception as e:
            return {"reply": f"The assistant hit an error talking to Gemini ({type(e).__name__}). Try again in a moment.",
                    "images": tools.images, "used": used}
        return {"reply": reply or "(no answer)", "images": tools.images, "used": used, "model": GEMINI_MODEL}
    if client is None:
        key = _secret("ANTHROPIC_API_KEY")
        if not key:
            return {"reply": "The assistant isn't connected yet — no GEMINI_API_KEY (or ANTHROPIC_API_KEY) is configured. "
                             "Once it is, ask me about pay, accounts, invoices or collections.", "images": [], "used": [],
                    "unconfigured": True}
        import anthropic
        client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=90)
    for _ in range(MAX_STEPS + 1):
        msg = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system, tools=TOOLS, messages=convo,
                                     thinking={"type": "adaptive"}, output_config={"effort": "medium"})
        blocks = list(msg.content)
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")
        calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
        if msg.stop_reason == "refusal":
            reply = text or "I can't help with that one."
            break
        if not calls or msg.stop_reason != "tool_use":
            reply = text
            break
        convo.append({"role": "assistant", "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in blocks]})
        results = []
        for c in calls:
            args = dict(c.input or {})
            out = tools.call(c.name, args)
            used.append({"tool": c.name, "args": args})
            results.append({"type": "tool_result", "tool_use_id": c.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str)[:14000]})
        convo.append({"role": "user", "content": results})
    else:
        reply = reply or "That took too many lookups — try a narrower question."
    return {"reply": reply or "(no answer)", "images": tools.images, "used": used}
