"""Market fundamentals and Watchlist price alert service using yfinance."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import yfinance as yf
from sqlalchemy.orm import Session
from bot.models import WatchlistItem


def fetch_stock_fundamentals(ticker: str) -> dict:
    """Fetch key Buffett & Munger fundamental metrics for a stock ticker via yfinance."""
    clean_symbol = ticker.strip().upper()
    t = yf.Ticker(clean_symbol)
    info = t.info or {}

    price = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )
    if price is None:
        fast = getattr(t, "fast_info", None)
        if fast:
            price = getattr(fast, "last_price", None) or getattr(fast, "previous_close", None)

    currency = info.get("currency", "USD")
    name = info.get("longName") or info.get("shortName") or clean_symbol
    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")

    market_cap = info.get("marketCap") or 0
    pe_trailing = info.get("trailingPE")
    pe_forward = info.get("forwardPE")
    price_to_book = info.get("priceToBook")

    gross_margins = info.get("grossMargins")
    operating_margins = info.get("operatingMargins")
    profit_margins = info.get("profitMargins")
    roe = info.get("returnOnEquity")
    roa = info.get("returnOnAssets")
    revenue_growth = info.get("revenueGrowth")

    total_revenue = info.get("totalRevenue") or 0
    ebitda = info.get("ebitda") or 0
    net_income = info.get("netIncomeToCommon") or 0
    operating_cashflow = info.get("operatingCashflow") or 0
    free_cashflow = info.get("freeCashflow") or 0
    total_debt = info.get("totalDebt") or 0
    total_cash = info.get("totalCash") or 0

    net_debt = total_debt - total_cash
    net_debt_to_ebitda = (net_debt / ebitda) if ebitda and ebitda > 0 else None
    cash_conversion = (operating_cashflow / net_income) if net_income and net_income > 0 else None

    # Estimate invested capital = Total Debt + Total Equity - Excess Cash
    book_value = info.get("bookValue")
    shares_out = info.get("sharesOutstanding")
    total_equity = (book_value * shares_out) if (book_value and shares_out) else 0
    invested_capital = max(0.0, total_debt + total_equity - (total_cash * 0.8))
    nopat_est = (total_revenue * operating_margins * 0.79) if (total_revenue and operating_margins) else 0
    roic_est = (nopat_est / invested_capital) if invested_capital > 0 else None

    # Estimate Owner Earnings = OCF - Maintenance CapEx (separating Growth CapEx via D&A ~ EBITDA - EBIT)
    ebit_est = (total_revenue * operating_margins) if (total_revenue and operating_margins) else 0.0
    da_est = max(0.0, ebitda - ebit_est) if (ebitda and ebit_est > 0 and ebitda > ebit_est) else 0.0
    capex_est = max(0.0, operating_cashflow - free_cashflow) if (operating_cashflow > 0 and free_cashflow != 0) else 0.0
    if da_est > 0 and capex_est > da_est * 1.35:
        # Heavy Growth CapEx cycle (e.g., AI/Cloud buildout): Maintenance CapEx ≈ 1.15x D&A
        maintenance_capex_est = da_est * 1.15
    else:
        maintenance_capex_est = capex_est * 0.80
    owner_earnings_est = (
        operating_cashflow - maintenance_capex_est
        if operating_cashflow > 0
        else free_cashflow
    )
    owner_earnings_yield = (owner_earnings_est / market_cap) if (owner_earnings_est and market_cap > 0) else None

    return {
        "ticker": clean_symbol,
        "name": name,
        "currency": currency,
        "sector": sector,
        "industry": industry,
        "price": float(price) if price is not None else None,
        "market_cap": float(market_cap),
        "pe_trailing": float(pe_trailing) if pe_trailing is not None else None,
        "pe_forward": float(pe_forward) if pe_forward is not None else None,
        "price_to_book": float(price_to_book) if price_to_book is not None else None,
        "gross_margin_pct": round(gross_margins * 100, 2) if gross_margins is not None else None,
        "operating_margin_pct": round(operating_margins * 100, 2) if operating_margins is not None else None,
        "profit_margin_pct": round(profit_margins * 100, 2) if profit_margins is not None else None,
        "roe_pct": round(roe * 100, 2) if roe is not None else None,
        "roa_pct": round(roa * 100, 2) if roa is not None else None,
        "roic_est_pct": round(roic_est * 100, 2) if roic_est is not None else None,
        "revenue_growth_pct": round(revenue_growth * 100, 2) if revenue_growth is not None else None,
        "total_revenue": float(total_revenue),
        "ebitda": float(ebitda),
        "net_income": float(net_income),
        "operating_cashflow": float(operating_cashflow),
        "free_cashflow": float(free_cashflow),
        "owner_earnings_est": float(owner_earnings_est),
        "owner_earnings_yield_pct": round(owner_earnings_yield * 100, 2) if owner_earnings_yield is not None else None,
        "cash_conversion_pct": round(cash_conversion * 100, 2) if cash_conversion is not None else None,
        "total_debt": float(total_debt),
        "total_cash": float(total_cash),
        "net_debt": float(net_debt),
        "net_debt_to_ebitda": round(net_debt_to_ebitda, 2) if net_debt_to_ebitda is not None else None,
    }


def fetch_latest_prices_batch(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices for a list of tickers in parallel using yfinance."""
    result: dict[str, float] = {}
    unique_tickers = sorted({t.strip().upper() for t in tickers if t.strip()})
    if not unique_tickers:
        return result

    def _fetch_one(sym: str) -> tuple[str, float | None]:
        try:
            t = yf.Ticker(sym)
            fast = getattr(t, "fast_info", None)
            price = None
            if fast:
                price = getattr(fast, "last_price", None) or getattr(fast, "previous_close", None)
            if price is None:
                info = t.info or {}
                price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            if price is not None:
                return sym, round(float(price), 2)
        except Exception:
            pass
        return sym, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for sym, price in pool.map(_fetch_one, unique_tickers):
            if price is not None:
                result[sym] = price

    return result


def check_watchlist_alerts(session: Session, user_id: int | None = None, fetch_live: bool = True) -> list[WatchlistItem]:
    """Update shared watchlist prices and return items that are in the Buffett Buy Zone (price <= target_buy_price)."""
    query = session.query(WatchlistItem).filter(WatchlistItem.alert_enabled.is_(True))
    items = query.order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc()).all()
    if not items:
        return []

    if fetch_live:
        tickers = [item.ticker for item in items]
        live_prices = fetch_latest_prices_batch(tickers)
        now = datetime.now(timezone.utc)
        for item in items:
            sym = item.ticker.upper()
            if sym in live_prices:
                item.last_price = live_prices[sym]
                item.last_checked_at = now
        session.commit()

    # Deduplicate by ticker so the shared watchlist returns each triggered stock once
    seen: set[str] = set()
    triggered: list[WatchlistItem] = []
    for item in items:
        t = item.ticker.upper()
        if t in seen:
            continue
        seen.add(t)
        if item.last_price is not None and item.last_price <= item.target_buy_price:
            triggered.append(item)

    return triggered
