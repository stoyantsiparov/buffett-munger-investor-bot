"""Market fundamentals and Watchlist price alert service using yfinance."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
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


def _df_series(df, candidate_rows: tuple[str, ...]) -> list[tuple[str, float]]:
    """Extract chronological (newest-first) non-NaN (year, float) pairs from a yfinance DataFrame."""
    if df is None or getattr(df, "empty", True):
        return []
    for row_name in candidate_rows:
        if row_name in df.index:
            series = df.loc[row_name]
            out: list[tuple[str, float]] = []
            for col, val in series.items():
                try:
                    fv = float(val)
                    if not math.isnan(fv) and not math.isinf(fv):
                        out.append((str(col)[:4], fv))
                except Exception:
                    continue
            if out:
                return out
    return []


def fetch_deep_historical_metrics(ticker: str, info_metrics: dict | None = None) -> dict:
    """Fetch 4-5 year annual GAAP statements from yfinance for true Buffett multi-year ROIC,
    Owner Earnings after SBC, share count trend, interest coverage, -30% stress test, and red flags."""
    clean_symbol = ticker.strip().upper()
    info_m = info_metrics or {}
    sector = str(info_m.get("sector") or "")
    industry = str(info_m.get("industry") or "")
    market_cap = float(info_m.get("market_cap") or 0.0)
    is_bank_or_insurer = sector == "Financial Services" and any(k in industry for k in ("Bank", "Insurance"))

    try:
        t = yf.Ticker(clean_symbol)
        inc = t.income_stmt
        bs = t.balance_sheet
        cf = t.cashflow
    except Exception:
        return {}

    revs = _df_series(inc, ("Total Revenue", "Operating Revenue"))
    gross_profits = _df_series(inc, ("Gross Profit",))
    op_incomes = _df_series(inc, ("Operating Income", "EBIT", "Total Operating Income As Reported"))
    op_expenses = _df_series(inc, ("Operating Expense",))
    ebits = _df_series(inc, ("EBIT", "Operating Income"))
    int_exps = _df_series(inc, ("Interest Expense", "Interest Expense Non Operating"))
    tax_rates = _df_series(inc, ("Tax Rate For Calcs",))
    net_incomes = _df_series(inc, ("Net Income", "Net Income Common Stockholders"))
    dil_shares = _df_series(inc, ("Diluted Average Shares",))

    debts = _df_series(bs, ("Total Debt", "Long Term Debt And Capital Lease Obligation"))
    equities = _df_series(bs, ("Stockholders Equity", "Common Stock Equity"))
    cashes = _df_series(bs, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"))
    ord_shares = _df_series(bs, ("Ordinary Shares Number", "Share Issued"))

    ocfs = _df_series(cf, ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"))
    capexs = _df_series(cf, ("Capital Expenditure", "Capital Expenditure Reported"))
    das = _df_series(cf, ("Depreciation And Amortization", "Depreciation Amortization Depletion"))
    sbcs = _df_series(cf, ("Stock Based Compensation",))

    # Build year-indexed maps
    rev_by_yr = dict(revs)
    op_by_yr = dict(op_incomes)
    tax_by_yr = dict(tax_rates)
    ni_by_yr = dict(net_incomes)
    debt_by_yr = dict(debts)
    eq_by_yr = dict(equities)
    cash_by_yr = dict(cashes)

    # 1. Multi-year ROIC (or ROE for Banks/Insurers)
    roic_vals: list[float] = []
    roe_vals: list[float] = []
    op_margin_vals: list[float] = []
    years_scanned: list[str] = []

    for yr in sorted(set(ni_by_yr.keys()) | set(op_by_yr.keys()), reverse=True)[:5]:
        years_scanned.append(yr)
        eq = eq_by_yr.get(yr, 0.0)
        ni = ni_by_yr.get(yr)
        if eq and eq > 0 and ni is not None:
            roe_vals.append(round((ni / eq) * 100.0, 1))

        rev = rev_by_yr.get(yr, 0.0)
        op_inc = op_by_yr.get(yr)
        if rev and rev > 0 and op_inc is not None:
            op_margin_vals.append(round((op_inc / rev) * 100.0, 1))

        if not is_bank_or_insurer and op_inc is not None and eq > 0:
            tr = tax_by_yr.get(yr, 0.21)
            tr = max(0.15, min(0.28, tr if tr > 0 else 0.21))
            nopat = op_inc * (1.0 - tr)
            inv_cap = max(0.0, debt_by_yr.get(yr, 0.0) + eq - cash_by_yr.get(yr, 0.0) * 0.8)
            if inv_cap > 0:
                roic_vals.append(round((nopat / inv_cap) * 100.0, 1))

    avg_roic_pct = round(sum(roic_vals) / len(roic_vals), 1) if roic_vals else None
    min_roic_pct = min(roic_vals) if roic_vals else None
    avg_roe_pct = round(sum(roe_vals) / len(roe_vals), 1) if roe_vals else None
    hist_years = len(roic_vals) if (not is_bank_or_insurer and roic_vals) else len(roe_vals)

    # 2. True Owner Earnings after Maintenance CapEx AND Stock-Based Compensation (SBC)
    latest_ocf = ocfs[0][1] if ocfs else None
    latest_capex = abs(capexs[0][1]) if capexs else 0.0
    latest_da = abs(das[0][1]) if das else 0.0
    latest_sbc = abs(sbcs[0][1]) if sbcs else 0.0
    latest_ni = net_incomes[0][1] if net_incomes else None

    true_oe = None
    true_oe_yield_pct = None
    sbc_pct_of_ocf = None
    maint_capex_real = None
    if not is_bank_or_insurer and latest_ocf is not None:
        if latest_da > 0 and latest_capex > latest_da * 1.35:
            maint_capex_real = latest_da * 1.15
        elif latest_da > 0:
            maint_capex_real = min(latest_capex, latest_da * 1.15)
        else:
            maint_capex_real = latest_capex * 0.85
        true_oe = latest_ocf - maint_capex_real - latest_sbc
        if market_cap > 0:
            true_oe_yield_pct = round((true_oe / market_cap) * 100.0, 2)
        if latest_ocf > 0:
            sbc_pct_of_ocf = round((latest_sbc / latest_ocf) * 100.0, 1)

    # 3. Share Count Trend (Buybacks vs. Dilution)
    share_series = dil_shares if len(dil_shares) >= 2 else ord_shares
    share_change_pct = None
    share_years = 0
    if len(share_series) >= 2 and share_series[-1][1] > 0:
        newest_sh = share_series[0][1]
        oldest_sh = share_series[-1][1]
        share_change_pct = round(((newest_sh - oldest_sh) / oldest_sh) * 100.0, 1)
        share_years = len(share_series)

    # 4. Interest Coverage (EBIT / Interest Expense)
    interest_coverage_x = None
    latest_ebit = ebits[0][1] if ebits else None
    latest_int = abs(int_exps[0][1]) if int_exps else 0.0
    if not is_bank_or_insurer and latest_ebit is not None:
        if latest_int <= 0:
            interest_coverage_x = 999.0  # Effectively debt/interest-free
        else:
            interest_coverage_x = round(latest_ebit / latest_int, 1)

    # 5. Buffett -30% Revenue Recession Stress Test
    stress_test_pass = None
    stress_ebit_margin_pct = None
    latest_rev = revs[0][1] if revs else 0.0
    latest_gp = gross_profits[0][1] if gross_profits else None
    latest_opex = abs(op_expenses[0][1]) if op_expenses else None
    if not is_bank_or_insurer and latest_rev > 0 and latest_gp is not None and latest_opex is not None:
        stressed_rev = latest_rev * 0.70
        stressed_gp = latest_gp * 0.70
        stressed_opex = latest_opex * 0.85  # Sticky fixed costs (~85% remain in a sharp downturn)
        stressed_ebit = stressed_gp - stressed_opex
        stress_ebit_margin_pct = round((stressed_ebit / stressed_rev) * 100.0, 1)
        stress_test_pass = (stressed_ebit > 0) and (latest_int <= 0 or (stressed_ebit / latest_int) >= 1.5)

    # 6. Revenue Growth Trend & Margin Erosion Check
    rev_yoy_pct = None
    rev_cagr_pct = None
    if len(revs) >= 2 and revs[1][1] > 0:
        rev_yoy_pct = round(((revs[0][1] - revs[1][1]) / revs[1][1]) * 100.0, 1)
    if len(revs) >= 3 and revs[-1][1] > 0 and revs[0][1] > 0:
        span = len(revs) - 1
        rev_cagr_pct = round((((revs[0][1] / revs[-1][1]) ** (1.0 / span)) - 1.0) * 100.0, 1)

    margin_delta_pct = None
    if len(op_margin_vals) >= 3:
        prior_avg_margin = sum(op_margin_vals[1:]) / len(op_margin_vals[1:])
        margin_delta_pct = round(op_margin_vals[0] - prior_avg_margin, 1)

    # 7. Automated Buffett & Munger Red-Flag Detector (structured codes for EN/BG rendering)
    red_flags: list[dict] = []
    if not is_bank_or_insurer:
        if sbc_pct_of_ocf is not None and sbc_pct_of_ocf >= 20.0:
            red_flags.append({"code": "sbc_heavy", "val": sbc_pct_of_ocf})
        if latest_ni is not None and latest_ocf is not None and latest_ocf > 0 and latest_ni > latest_ocf * 1.35:
            ratio = round((latest_ni / latest_ocf) * 100.0, 0)
            red_flags.append({"code": "accounting_gain", "val": ratio})
        if stress_test_pass is False:
            red_flags.append({"code": "stress_fail", "val": stress_ebit_margin_pct})
        if interest_coverage_x is not None and interest_coverage_x < 5.0:
            red_flags.append({"code": "low_interest_cov", "val": interest_coverage_x})

    if share_change_pct is not None and share_change_pct >= 5.0:
        red_flags.append({"code": "dilution", "val": share_change_pct})
    if margin_delta_pct is not None and margin_delta_pct <= -4.0:
        red_flags.append({"code": "margin_erosion", "val": margin_delta_pct})
    if rev_yoy_pct is not None and rev_yoy_pct <= -5.0:
        red_flags.append({"code": "rev_decline", "val": rev_yoy_pct})

    return {
        "is_bank_or_insurer": is_bank_or_insurer,
        "hist_years": hist_years,
        "avg_roic_pct": avg_roic_pct,
        "min_roic_pct": min_roic_pct,
        "avg_roe_pct": avg_roe_pct,
        "latest_ocf": latest_ocf,
        "latest_capex": latest_capex,
        "latest_da": latest_da,
        "latest_sbc": latest_sbc,
        "maint_capex_real": maint_capex_real,
        "true_owner_earnings": true_oe,
        "true_oe_yield_pct": true_oe_yield_pct,
        "sbc_pct_of_ocf": sbc_pct_of_ocf,
        "share_change_pct": share_change_pct,
        "share_years": share_years,
        "interest_coverage_x": interest_coverage_x,
        "stress_test_pass": stress_test_pass,
        "stress_ebit_margin_pct": stress_ebit_margin_pct,
        "rev_yoy_pct": rev_yoy_pct,
        "rev_cagr_pct": rev_cagr_pct,
        "margin_delta_pct": margin_delta_pct,
        "red_flags": red_flags,
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
