"""Database schema for the W&T scorecard.

`sales_lines` is the item-level fact table (one row per invoice line). The engine
rolls it into per-rep scorecards; manager_actions / constrained_items / awards layer
on top; audit_log records every write. Account = Customer Number. Metric = profit
(extended_price - extended_cost), so cost columns are kept.
"""
import datetime as dt
from sqlalchemy import (Column, Integer, String, Float, Boolean, Date, DateTime,
                        ForeignKey, JSON, UniqueConstraint)
from .db import Base


# ---------- master data ----------
class Associate(Base):
    __tablename__ = "associates"
    associate_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, index=True)                  # full name (null for inactive MT/PJ)
    batch_initial = Column(String, index=True)          # 2-letter batch prefix
    other_names = Column(String)                        # free-text batch variant (MORGANW, ...)
    role = Column(String)                               # full time sales | part time sales | manager
    status = Column(String, default="Active")          # Active | Inactive
    hours_per_day = Column(Float)                       # parsed from the roster Hours column (e.g. 6.5)
    salary_raw = Column(String)                         # raw roster Salary string (e.g. "$23/hour"); for a future ROI view


class Team(Base):
    """An EARNING GROUP for growth pay, managed on Settings → Teams.

    kind 'team'   : members split the growth of the accounts the team owns, equally.
                    auto=True   -> takes part in the 80%-of-orders ownership rule (each rep in exactly one such team)
                    fallback=True -> catches accounts no auto team owns outright when its members TOGETHER wrote
                                     >= 80% of the orders (e.g. "Everyone" = Team 1 + Team 2)
    kind 'house'  : earns no growth (managers; the house accounts). Exactly one.
    Individuals are not rows here: an account pinned to a rep by name pays that rep alone."""
    __tablename__ = "teams"
    name = Column(String, primary_key=True)
    kind = Column(String, default="team")              # team | house
    members = Column(JSON, default=list)               # rep names
    auto = Column(Boolean, default=True)               # in the 80% rule
    fallback = Column(Boolean, default=False)          # catches shared accounts when its members together qualify
    color = Column(String)                             # hex, for the UI
    sort = Column(Integer, default=0)
    updated_at = Column(DateTime, default=dt.datetime.utcnow)


class OwnershipPin(Base):
    """A manual ownership decision: from `effective_from` (YYYY-MM) on, `account` belongs to `team` (a team
    name, the house group, or a rep's name for an individual earner). team=None means "back to the 80% rule".
    Rows are never edited — a new decision is a new row — so earlier months keep the owner they had.
    The 80% rule governs every account-month that has no pin in force."""
    __tablename__ = "ownership_pins"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account = Column(String, index=True)
    team = Column(String)
    effective_from = Column(String, index=True)        # YYYY-MM
    user_id = Column(Integer)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class TeamMembership(Base):
    """A team's roster from `effective_from` (YYYY-MM) on. Append-only, like ownership pins: moving a person
    writes a new roster row for each team involved, and months before it keep the roster they had. The 80% rule
    credits an order to the team the rep was on in the month it was written; the equal split for a month uses
    that month's roster."""
    __tablename__ = "team_memberships"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team = Column(String, index=True)
    members = Column(JSON, default=list)
    effective_from = Column(String, index=True)        # YYYY-MM
    user_id = Column(Integer)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class AccountAssignment(Base):
    """Manager's manual account -> team assignment. ALWAYS beats the computed 80%-of-orders rule, which is
    what makes the genuinely shared accounts (no team over 80%) payable at all."""
    __tablename__ = "account_assignments"
    account = Column(String, primary_key=True)
    team = Column(String)                              # "Team 1" | "Team 2" | "House"
    note = Column(String)
    user_id = Column(Integer)
    updated_at = Column(DateTime, default=dt.datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)     # pbkdf2
    role = Column(String, default="manager")           # manager | admin | rep
    associate_name = Column(String)                    # for role='rep': which sales rep this login is
    is_active = Column(Boolean, default=True)


# ---------- the fact table (item-level; upsert by sop_number on import) ----------
class SalesLine(Base):
    __tablename__ = "sales_lines"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sop_type = Column(String)                           # Invoice | Return
    sop_number = Column(String, index=True)            # order id (dedup key on upload)
    item_number = Column(String, index=True)
    item_description = Column(String)
    qty = Column(Float)
    unit_price = Column(Float)
    extended_price = Column(Float)                      # revenue
    unit_cost = Column(Float)
    extended_cost = Column(Float)
    line_profit = Column(Float)                         # extended_price - extended_cost
    customer_number = Column(String, index=True)        # = account
    customer_name = Column(String)
    document_date = Column(Date, index=True)
    batch_number = Column(String)
    associate = Column(String, index=True)             # resolved sales rep (rep lines only)
    imported_at = Column(DateTime, default=dt.datetime.utcnow)


# ---------- computed / period state ----------
class Period(Base):
    __tablename__ = "periods"
    period_id = Column(Integer, primary_key=True, autoincrement=True)
    start_date = Column(Date); end_date = Column(Date)              # the 4-week period scored
    window_start = Column(Date); window_end = Column(Date)          # trailing 13-week window
    baseline_window_start = Column(Date); baseline_window_end = Column(Date)
    status = Column(String, default="open")                        # open | closed (locked)


# ---------- manager inputs ----------
class ManagerAction(Base):
    __tablename__ = "manager_actions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("periods.period_id"), index=True)
    account = Column(String)                            # customer_number
    associate = Column(String)
    status = Column(String, default="normal")          # normal | exempt | rebaseline | jump_rep (release big-jump windfall)
    rebaseline_value = Column(Float)
    called = Column(Boolean, default=False)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    __table_args__ = (UniqueConstraint("period_id", "account"),)


class ConstrainedItem(Base):
    """Manager-supplied supply-constrained (limited-stock) items. GLOBAL: an item flagged here is excluded
    from the GROWTH comparison in EVERY period until removed (period_id is retained only as a legacy FK)."""
    __tablename__ = "constrained_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("periods.period_id"), index=True)
    item_number = Column(String)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    __table_args__ = (UniqueConstraint("period_id", "item_number"),)


class AcquisitionReview(Base):
    """Manager review of new accounts. rep_won=False = 'not rep-won (inbound)' -> no acquisition credit.
    Keyed by account (persists across periods — 'didn't really win it' is permanent)."""
    __tablename__ = "acquisition_reviews"
    account = Column(String, primary_key=True)         # customer_number
    rep_won = Column(Boolean, default=True)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class NewProductReview(Base):
    """Manager review of newly-introduced SKUs. featured=True = a genuine new product to incentivize
    (its revenue counts at new_product_attribution toward growth). Default-absent = not featured (catalog
    churn / size-brand variants pay nothing). Keyed by item_number (a launch is a permanent fact)."""
    __tablename__ = "new_product_reviews"
    item_number = Column(String, primary_key=True)
    featured = Column(Boolean, default=False)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class SubstituteProduct(Base):
    """A 'cheaper substitute' SKU the manager flagged — the rep found a cheaper alternative to an existing product.
    Its revenue counts at `substitute_attribution` (higher than a brand-new product: some credit, not all), ramping
    to 100% over the same new_product_weeks window. Kept in its own table (not a column on new_product_reviews) so
    it creates cleanly on prod via create_all without an ALTER. Keyed by item_number."""
    __tablename__ = "substitute_products"
    item_number = Column(String, primary_key=True)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Award(Base):
    __tablename__ = "awards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("periods.period_id"), index=True)
    associate = Column(String)
    award_amount = Column(Float, default=0)
    fine_amount = Column(Float, default=0)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    __table_args__ = (UniqueConstraint("period_id", "associate"),)


class AnnualAward(Base):
    """The once-a-year manager award for a rep's infrequent ("annual") accounts (the Annual Review track).
    Keyed by associate only (one current annual award per rep); a new table so it adds via create_all with
    no ALTER on the live RDS."""
    __tablename__ = "annual_awards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    associate = Column(String, unique=True)
    award_amount = Column(Float, default=0)
    note = Column(String)
    as_of = Column(Date)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    action = Column(String)
    entity = Column(String)
    details = Column(JSON)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class CollectedInvoice(Base):
    """An invoice (sop_number) whose money has been COLLECTED, per the manager's accounts-receivable upload.
    Presence == fully collected. Each AR upload REPLACES the whole table (the report is a cumulative snapshot),
    so an invoice that reverses (bounced check) drops out and its bonus claws back on the next pay-run."""
    __tablename__ = "collected_invoices"
    sop_number = Column(String, primary_key=True)
    reported_at = Column(DateTime, default=dt.datetime.utcnow)   # when this collected set was uploaded


class VoidedInvoice(Base):
    """An invoice that was VOIDED (deleted) per the manager's transaction upload. Voided invoices are removed
    from the sales data entirely — they count toward NOTHING (contribution, growth, acquisition, or collected).
    Snapshot-replaced on each upload that carries void info."""
    __tablename__ = "voided_invoices"
    sop_number = Column(String, primary_key=True)
    reported_at = Column(DateTime, default=dt.datetime.utcnow)


class WrittenOffInvoice(Base):
    """An uncollected invoice the manager has written off (bad debt) — drops from the unpaid/pending panel so
    a dead receivable doesn't inflate the pending-bonus pipeline forever. Keyed by sop_number."""
    __tablename__ = "written_off_invoices"
    sop_number = Column(String, primary_key=True)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class GrowthPayment(Base):
    """Cumulative bonus PAID per rep per fiscal cycle on the growth-model service (progressive true-up:
    payable now = earned x collected% - paid_cum, floored at 0; 'Record pay' bumps paid_cum to the current
    collectable amount). One row per (associate, cycle start)."""
    __tablename__ = "growth_payments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    associate = Column(String, index=True)
    fiscal_start = Column(Date, index=True)
    paid_cum = Column(Float, default=0.0)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    updated_at = Column(DateTime, default=dt.datetime.utcnow)
    __table_args__ = (UniqueConstraint("associate", "fiscal_start"),)
