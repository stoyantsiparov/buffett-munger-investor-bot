"""SQLAlchemy 2.0 ORM models compatible with MSSQL (SQL Server), SQLite, and PostgreSQL."""

from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Telegram user profile, encrypted Trading 212 credentials, and monthly budget parameters."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(128), nullable=True)
    full_name = Column(String(256), nullable=True)

    # Encrypted Read-Only Trading 212 API credentials
    t212_api_key_enc = Column(String(512), nullable=True)
    t212_api_secret_enc = Column(String(512), nullable=True)
    t212_env = Column(String(16), nullable=False, default="live")
    t212_account_id = Column(String(64), nullable=True)
    t212_currency = Column(String(16), nullable=False, default="EUR")

    # Optional encrypted personal Gemini API Key (friends can set via /set_gemini_key without touching .env)
    gemini_api_key_enc = Column(String(512), nullable=True)

    # Personal Finance & Buffett/Munger Parameters (default blank for new friends)
    monthly_income = Column(Float, nullable=False, default=0.0)
    monthly_invest_budget = Column(Float, nullable=False, default=0.0)
    emergency_buffer_target = Column(Float, nullable=False, default=500.0)
    emergency_buffer_current = Column(Float, nullable=False, default=0.0)
    payday_day = Column(Integer, nullable=False, default=1)
    investment_plan = Column(Text, nullable=True)  # Optional note; monthly plan is generated dynamically

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    loans = relationship("UserLoan", back_populates="user", cascade="all, delete-orphan")
    snapshots = relationship("PortfolioSnapshot", back_populates="user", cascade="all, delete-orphan")
    watchlist_items = relationship("WatchlistItem", back_populates="user", cascade="all, delete-orphan")


class UserLoan(Base):
    """High-interest loan tracked for Charlie Munger's Inversion rule (eliminate >10-12% APR first)."""

    __tablename__ = "user_loans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    lender_name = Column(String(128), nullable=False, default="Bank Loan")
    contract_number = Column(String(64), nullable=True)
    principal_balance = Column(Float, nullable=False, default=0.0)
    apr_percent = Column(Float, nullable=False, default=0.0)
    regular_payment = Column(Float, nullable=False, default=0.0)
    early_principal_payment = Column(Float, nullable=False, default=0.0)
    early_fee_percent = Column(Float, nullable=False, default=0.5)
    due_day = Column(Integer, nullable=False, default=15)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(String(512), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="loans")


class PortfolioSnapshot(Base):
    """Point-in-time snapshot of a user's Trading 212 portfolio."""

    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    total_value = Column(Float, nullable=False, default=0.0)
    invested_value = Column(Float, nullable=False, default=0.0)
    free_cash = Column(Float, nullable=False, default=0.0)
    pie_cash = Column(Float, nullable=False, default=0.0)
    unrealized_ppl = Column(Float, nullable=False, default=0.0)
    realized_result = Column(Float, nullable=False, default=0.0)
    semiconductor_pct = Column(Float, nullable=False, default=0.0)
    positions_count = Column(Integer, nullable=False, default=0)
    pending_orders_count = Column(Integer, nullable=False, default=0)

    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    user = relationship("User", back_populates="snapshots")


class WatchlistItem(Base):
    """Buffett & Munger Watchlist stock with Intrinsic Value range and Target Buy Price alert."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "ticker", name="uq_user_watchlist_ticker"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    rank_order = Column(Integer, nullable=False, default=99)
    ticker = Column(String(32), nullable=False, index=True)
    company_name = Column(String(128), nullable=True)
    currency = Column(String(8), nullable=False, default="USD")

    moat_summary = Column(String(256), nullable=True)
    roic_pct = Column(Float, nullable=True)
    intrinsic_value_low = Column(Float, nullable=True)
    intrinsic_value_high = Column(Float, nullable=True)
    target_buy_price = Column(Float, nullable=False)
    margin_of_safety_pct = Column(Float, nullable=False, default=25.0)

    last_price = Column(Float, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    alert_enabled = Column(Boolean, nullable=False, default=True)
    last_alert_sent_at = Column(DateTime, nullable=True)
    notes = Column(String(512), nullable=True)

    user = relationship("User", back_populates="watchlist_items")
