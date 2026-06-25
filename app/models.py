"""Database schema for the W&T scorecard.

`sales_lines` is the item-level fact table (one row per invoice line). The engine
rolls it into per-rep scorecards; manager_actions / constrained_items / awards layer
on top; audit_log records every write. Account = Customer Number. Metric = profit
(extended_price - extended_cost), so cost columns are kept.
"""
import datetime as dt
from sqlalchemy import (Column, Integer, BigInteger, String, Float, Boolean, Date, DateTime,
                        ForeignKey, JSON, UniqueConstraint, Index)
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
    market_drift = Column(Float)
    status = Column(String, default="open")                        # open | closed (locked)


class Scorecard(Base):
    __tablename__ = "scorecards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("periods.period_id"), index=True)
    associate = Column(String, index=True)
    actual_profit = Column(Float); profit_target = Column(Float)
    profit_perf_pct = Column(Float); profit_vs_market_pct = Column(Float)
    real_growth_pct = Column(Float)
    accounts = Column(Integer); new_accounts = Column(Integer)
    __table_args__ = (UniqueConstraint("period_id", "associate"),)


# ---------- manager inputs ----------
class ManagerAction(Base):
    __tablename__ = "manager_actions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("periods.period_id"), index=True)
    account = Column(String)                            # customer_number
    associate = Column(String)
    status = Column(String, default="normal")          # normal | exempt | rebaseline
    rebaseline_value = Column(Float)
    called = Column(Boolean, default=False)
    note = Column(String)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    __table_args__ = (UniqueConstraint("period_id", "account"),)


class ConstrainedItem(Base):
    """Per-period, manager-supplied supply-constrained items (removed symmetrically)."""
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
