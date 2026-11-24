"""Market fundamentals and Watchlist price alert service using yfinance."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import yfinance as yf
from sqlalchemy.orm import Session
from bot.models import WatchlistItem
from bot.sec_edgar import fetch_sec_edgar_annual_series


def fetch_stock_fundamentals(ticker: str) -> dict:
    """Fetch key Buffett & Munger fundamental metrics for a stock ticker via yfinance."""
    clean_symbol = ticker.strip().upper()
    t = yf.Ticker(clean_symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    fast = getattr(t, "fast_info", None)

    price = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )
    if price is None and fast:
        try:
            price = getattr(fast, "last_price", None) or getattr(fast, "previous_close", None)
        except Exception:
            pass

    currency = info.get("currency")
    if not currency and fast:
        try:
            currency = getattr(fast, "currency", None)
        except Exception:
            pass
    currency = currency or "USD"

    name = info.get("longName") or info.get("shortName") or clean_symbol
    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")

    market_cap = info.get("marketCap") or 0
    if not market_cap and fast:
        try:
            market_cap = getattr(fast, "market_cap", None) or 0
        except Exception:
            market_cap = 0
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
        "pe_trailing": round(float(pe_trailing), 2) if pe_trailing is not None else None,
        "pe_forward": round(float(pe_forward), 2) if pe_forward is not None else None,
        "price_to_book": round(float(price_to_book), 2) if price_to_book is not None else None,
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
        inc = bs = cf = None

    revs = _df_series(inc, ("Total Revenue", "Operating Revenue"))
    gross_profits = _df_series(inc, ("Gross Profit",))
    op_incomes = _df_series(inc, ("Operating Income", "EBIT", "Total Operating Income As Reported"))
    op_expenses = _df_series(inc, ("Operating Expense",))
    ebits = _df_series(inc, ("EBIT", "Operating Income"))
    int_exps = _df_series(inc, ("Interest Expense", "Interest Expense Non Operating"))
    tax_rates = _df_series(inc, ("Tax Rate For Calcs",))
    net_incomes = _df_series(inc, ("Net Income", "Net Income Common Stockholders"))
    dil_shares = _df_series(inc, ("Diluted Average Shares",))
    rds = _df_series(inc, ("Research And Development",))

    debts = _df_series(bs, ("Total Debt", "Long Term Debt And Capital Lease Obligation"))
    equities = _df_series(bs, ("Stockholders Equity", "Common Stock Equity"))
    cashes = _df_series(bs, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"))
    ord_shares = _df_series(bs, ("Ordinary Shares Number", "Share Issued"))
    receivables = _df_series(bs, ("Accounts Receivable", "Receivables"))
    inventories = _df_series(bs, ("Inventory",))
    payables = _df_series(bs, ("Accounts Payable", "Payables And Accrued Expenses"))
    goodwills = _df_series(bs, ("Goodwill", "Goodwill And Other Intangible Assets"))

    ocfs = _df_series(cf, ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"))
    capexs = _df_series(cf, ("Capital Expenditure", "Capital Expenditure Reported"))
    das = _df_series(cf, ("Depreciation And Amortization", "Depreciation Amortization Depletion"))
    sbcs = _df_series(cf, ("Stock Based Compensation",))
    divs_paid = _df_series(cf, ("Cash Dividends Paid", "Common Stock Dividend Paid"))

    # v1.3.0: Official US SEC EDGAR 10-K XBRL cross-verification & cloud-IP fallback
    data_source = "yfinance"
    sec_data = {}
    if "." not in clean_symbol or clean_symbol.endswith((".A", ".B", "-A", "-B")):
        sec_data = fetch_sec_edgar_annual_series(clean_symbol)
    if sec_data:
        data_source = "SEC EDGAR 10-K + yfinance" if revs else "SEC EDGAR 10-K"
        if info_metrics is not None and sec_data.get("entity_name") and info_metrics.get("name") in (None, "", clean_symbol):
            info_metrics["name"] = sec_data["entity_name"]
        if not revs and sec_data.get("revs"):
            revs = sec_data["revs"]
        if not gross_profits and sec_data.get("gross_profits"):
            gross_profits = sec_data["gross_profits"]
        if not op_incomes and sec_data.get("op_incomes"):
            op_incomes = sec_data["op_incomes"]
            if not ebits:
                ebits = op_incomes
        if not net_incomes and sec_data.get("net_incomes"):
            net_incomes = sec_data["net_incomes"]
        if not ocfs and sec_data.get("ocfs"):
            ocfs = sec_data["ocfs"]
        if not capexs and sec_data.get("capexs"):
            capexs = sec_data["capexs"]
        if not das and sec_data.get("das"):
            das = sec_data["das"]
        if not sbcs and sec_data.get("sbcs"):
            sbcs = sec_data["sbcs"]
        if not equities and sec_data.get("equities"):
            equities = sec_data["equities"]
        if not debts and sec_data.get("debts"):
            debts = sec_data["debts"]
        if not cashes and sec_data.get("cashes"):
            cashes = sec_data["cashes"]
        if not int_exps and sec_data.get("int_exps"):
            int_exps = sec_data["int_exps"]
        if not rds and sec_data.get("rds"):
            rds = sec_data["rds"]
        if not dil_shares and sec_data.get("shares"):
            dil_shares = sec_data["shares"]

    if not revs and not net_incomes and not ocfs:
        return {}

    # Build year-indexed maps
    rev_by_yr = dict(revs)
    gp_by_yr = dict(gross_profits)
    op_by_yr = dict(op_incomes)
    tax_by_yr = dict(tax_rates)
    ni_by_yr = dict(net_incomes)
    debt_by_yr = dict(debts)
    eq_by_yr = dict(equities)
    cash_by_yr = dict(cashes)

    # 1. Multi-year ROIC (or ROE for Banks/Insurers) & Gross Margin Stability ("Prayer Session" Test)
    roic_vals: list[float] = []
    roe_vals: list[float] = []
    op_margin_vals: list[float] = []
    gross_margin_vals: list[float] = []
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
        gp_y = gp_by_yr.get(yr)
        if rev and rev > 0 and gp_y is not None:
            gross_margin_vals.append(round((gp_y / rev) * 100.0, 1))

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

    # 6b. (v1.2.0 / v1.3.0) CapEx Intensity, Accounting Goodwill %, A/R & Inventory Bloat, $1 Test,
    # Munger "Cannibal" Buybacks, Negative Working Capital Float, R&D Growth OpEx %, and Whole-Business Payback
    capex_to_da_ratio = round(latest_capex / latest_da, 2) if (latest_da > 0 and latest_capex > 0) else None
    capex_pct_of_ocf = round((latest_capex / latest_ocf) * 100.0, 1) if (latest_ocf and latest_ocf > 0) else None
    capex_pct_of_rev = round((latest_capex / latest_rev) * 100.0, 1) if (latest_rev and latest_rev > 0) else None

    latest_rd = abs(rds[0][1]) if rds else 0.0
    rd_pct_of_rev = round((latest_rd / latest_rev) * 100.0, 1) if (latest_rd > 0 and latest_rev > 0) else None

    latest_ap = abs(payables[0][1]) if payables else 0.0
    latest_ar = abs(receivables[0][1]) if receivables else 0.0
    latest_inv = abs(inventories[0][1]) if inventories else 0.0
    negative_working_capital = bool(
        not is_bank_or_insurer
        and latest_ap > 0
        and latest_ap > (latest_ar + latest_inv) * 1.05
    )

    eff_roic_or_roe = avg_roe_pct if is_bank_or_insurer else avg_roic_pct
    is_cannibal = bool(
        share_change_pct is not None
        and share_change_pct <= -3.5
        and (eff_roic_or_roe is None or eff_roic_or_roe >= 13.0)
    )

    # v1.4.0: Munger's "Bullshit Earnings" (EBITDA Illusion %) & Buffett's "Prayer Session" Gross-Margin Test
    gross_margin_avg_pct = round(sum(gross_margin_vals) / len(gross_margin_vals), 1) if gross_margin_vals else None
    gross_margin_delta_pct = None
    if len(gross_margin_vals) >= 3:
        prior_gm_avg = sum(gross_margin_vals[1:]) / len(gross_margin_vals[1:])
        gross_margin_delta_pct = round(gross_margin_vals[0] - prior_gm_avg, 1)

    prayer_session_pricing_power = bool(
        not is_bank_or_insurer
        and gross_margin_avg_pct is not None
        and gross_margin_avg_pct >= 48.0
        and (gross_margin_delta_pct is None or gross_margin_delta_pct >= -1.2)
        and (avg_roic_pct or 0) >= 15.0
    )

    ebitda_ref = float(info_m.get("ebitda") or 0.0)
    if ebitda_ref <= 0 and latest_ebit is not None:
        ebitda_ref = float(latest_ebit + latest_da)
    ebitda_illusion_pct = None
    if not is_bank_or_insurer and ebitda_ref > 0 and true_oe is not None:
        ebitda_illusion_pct = round(max(0.0, min(100.0, ((ebitda_ref - true_oe) / ebitda_ref) * 100.0)), 1)

    latest_goodwill = abs(goodwills[0][1]) if goodwills else 0.0
    latest_equity = equities[0][1] if equities else 0.0
    goodwill_pct_of_equity = (
        round((latest_goodwill / latest_equity) * 100.0, 1)
        if (latest_goodwill > 0 and latest_equity > 0)
        else None
    )

    ar_yoy_pct = None
    if len(receivables) >= 2 and receivables[1][1] > 0:
        ar_yoy_pct = round(((receivables[0][1] - receivables[1][1]) / receivables[1][1]) * 100.0, 1)

    inv_yoy_pct = None
    if len(inventories) >= 2 and inventories[1][1] > 0:
        inv_yoy_pct = round(((inventories[0][1] - inventories[1][1]) / inventories[1][1]) * 100.0, 1)

    # Buffett $1 Retained Earnings / Incremental Return on Retained Capital (ROIIC) Test
    div_by_yr = {yr: abs(val) for yr, val in divs_paid}
    roiic_pct = None
    passes_one_dollar_test = None
    if len(years_scanned) >= 3:
        newest_yr = years_scanned[0]
        oldest_yr = years_scanned[-1]
        cum_retained = 0.0
        for yr in years_scanned[1:]:
            ni_y = ni_by_yr.get(yr, 0.0)
            div_y = div_by_yr.get(yr, 0.0)
            if ni_y > div_y:
                cum_retained += (ni_y - div_y)
        if is_bank_or_insurer:
            e_new = ni_by_yr.get(newest_yr)
            e_old = ni_by_yr.get(oldest_yr)
        else:
            op_new = op_by_yr.get(newest_yr)
            op_old = op_by_yr.get(oldest_yr)
            e_new = op_new * 0.79 if op_new is not None else None
            e_old = op_old * 0.79 if op_old is not None else None
        if cum_retained > 0 and e_new is not None and e_old is not None:
            raw_roiic = ((e_new - e_old) / cum_retained) * 100.0
            roiic_pct = round(max(-100.0, min(200.0, raw_roiic)), 1)
            eff_avg = avg_roe_pct if is_bank_or_insurer else avg_roic_pct
            if roiic_pct >= 12.0 or ((eff_avg or 0) >= 18.0 and roiic_pct >= 5.0):
                passes_one_dollar_test = True
            elif roiic_pct < 5.0 and (eff_avg or 0) < 13.0:
                passes_one_dollar_test = False

    # 7. Automated Buffett & Munger Red-Flag Detector (structured codes for EN/BG rendering)
    red_flags: list[dict] = []
    if not is_bank_or_insurer:
        if sbc_pct_of_ocf is not None and sbc_pct_of_ocf >= 20.0:
            red_flags.append({"code": "sbc_heavy", "val": sbc_pct_of_ocf})
        ttm_cc = info_m.get("cash_conversion_pct")
        if (
            latest_ni is not None
            and latest_ocf is not None
            and latest_ocf > 0
            and latest_ni > latest_ocf * 1.35
            and (ttm_cc is None or ttm_cc < 85.0)
        ):
            ratio = round((latest_ni / latest_ocf) * 100.0, 0)
            red_flags.append({"code": "accounting_gain", "val": ratio})
        if stress_test_pass is False:
            red_flags.append({"code": "stress_fail", "val": stress_ebit_margin_pct})
        if interest_coverage_x is not None and interest_coverage_x < 5.0:
            red_flags.append({"code": "low_interest_cov", "val": interest_coverage_x})
        if (
            ar_yoy_pct is not None
            and rev_yoy_pct is not None
            and latest_rev > 0
            and receivables
            and (receivables[0][1] / latest_rev) >= 0.08
            and ar_yoy_pct >= 12.0
            and (ar_yoy_pct - rev_yoy_pct) >= 15.0
        ):
            red_flags.append({"code": "ar_bloat", "val": round(ar_yoy_pct - rev_yoy_pct, 1)})
        if (
            inv_yoy_pct is not None
            and rev_yoy_pct is not None
            and latest_rev > 0
            and inventories
            and (inventories[0][1] / latest_rev) >= 0.06
            and inv_yoy_pct >= 12.0
            and (inv_yoy_pct - rev_yoy_pct) >= 15.0
        ):
            red_flags.append({"code": "inv_bloat", "val": round(inv_yoy_pct - rev_yoy_pct, 1)})
        if goodwill_pct_of_equity is not None and goodwill_pct_of_equity >= 45.0 and (avg_roic_pct is not None and avg_roic_pct < 11.0):
            red_flags.append({"code": "fake_goodwill", "val": goodwill_pct_of_equity})
        if ebitda_illusion_pct is not None and ebitda_illusion_pct >= 55.0:
            red_flags.append({"code": "ebitda_illusion", "val": ebitda_illusion_pct})
        if gross_margin_delta_pct is not None and gross_margin_delta_pct <= -3.5:
            red_flags.append({"code": "gross_margin_squeeze", "val": gross_margin_delta_pct})

    if share_change_pct is not None and share_change_pct >= 5.0:
        red_flags.append({"code": "dilution", "val": share_change_pct})
    if margin_delta_pct is not None and margin_delta_pct <= -4.0:
        red_flags.append({"code": "margin_erosion", "val": margin_delta_pct})
    if rev_yoy_pct is not None and rev_yoy_pct <= -5.0:
        red_flags.append({"code": "rev_decline", "val": rev_yoy_pct})
    if passes_one_dollar_test is False and roiic_pct is not None and roiic_pct < 0.0:
        red_flags.append({"code": "failed_dollar_test", "val": roiic_pct})

    # 8. Cloud-IP Resilience Backfill: If Yahoo Finance rate-limited `t.info` on a shared cloud IP,
    # populate missing TTM fields in `info_metrics` directly from the latest annual GAAP statements!
    if info_metrics is not None:
        price_val = float(info_metrics.get("price") or 0.0)
        newest_shares_val = share_series[0][1] if share_series else 0.0
        if (not info_metrics.get("market_cap")) and price_val > 0 and newest_shares_val > 0:
            market_cap = round(price_val * newest_shares_val, 2)
            info_metrics["market_cap"] = market_cap
            if true_oe is not None and market_cap > 0:
                true_oe_yield_pct = round((true_oe / market_cap) * 100.0, 2)
        if info_metrics.get("pe_trailing") is None and latest_ni and latest_ni > 0 and market_cap > 0:
            info_metrics["pe_trailing"] = round(market_cap / latest_ni, 2)
        if info_metrics.get("gross_margin_pct") is None and latest_rev > 0 and latest_gp is not None:
            info_metrics["gross_margin_pct"] = round((latest_gp / latest_rev) * 100.0, 2)
        if info_metrics.get("operating_margin_pct") is None and op_margin_vals:
            info_metrics["operating_margin_pct"] = op_margin_vals[0]
        if info_metrics.get("roic_est_pct") is None and roic_vals:
            info_metrics["roic_est_pct"] = roic_vals[0]
        if info_metrics.get("roe_pct") is None and roe_vals:
            info_metrics["roe_pct"] = roe_vals[0]
        if (not info_metrics.get("owner_earnings_est")) and latest_ocf is not None and maint_capex_real is not None:
            oe_fallback = float(latest_ocf - maint_capex_real)
            info_metrics["owner_earnings_est"] = oe_fallback
            if market_cap > 0:
                info_metrics["owner_earnings_yield_pct"] = round((oe_fallback / market_cap) * 100.0, 2)
        if info_metrics.get("cash_conversion_pct") is None and latest_ocf is not None and latest_ni and latest_ni > 0:
            info_metrics["cash_conversion_pct"] = round((latest_ocf / latest_ni) * 100.0, 2)
        if (not info_metrics.get("net_debt")) and (debts or cashes):
            nd_fallback = float((debts[0][1] if debts else 0.0) - (cashes[0][1] if cashes else 0.0))
            info_metrics["net_debt"] = nd_fallback
            ebitda_fallback = (latest_ebit or 0.0) + latest_da
            if info_metrics.get("net_debt_to_ebitda") is None and ebitda_fallback > 0:
                info_metrics["net_debt_to_ebitda"] = round(nd_fallback / ebitda_fallback, 2)
        if not info_metrics.get("operating_cashflow") and latest_ocf is not None:
            info_metrics["operating_cashflow"] = float(latest_ocf)
        if not info_metrics.get("net_income") and latest_ni is not None:
            info_metrics["net_income"] = float(latest_ni)

    whole_business_payback_yrs = (
        round(market_cap / true_oe, 1)
        if (not is_bank_or_insurer and true_oe and true_oe > 0 and market_cap > 0)
        else None
    )

    return {
        "data_source": data_source,
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
        "is_cannibal": is_cannibal,
        "negative_working_capital": negative_working_capital,
        "rd_pct_of_rev": rd_pct_of_rev,
        "whole_business_payback_yrs": whole_business_payback_yrs,
        "gross_margin_avg_pct": gross_margin_avg_pct,
        "gross_margin_delta_pct": gross_margin_delta_pct,
        "prayer_session_pricing_power": prayer_session_pricing_power,
        "ebitda_illusion_pct": ebitda_illusion_pct,
        "interest_coverage_x": interest_coverage_x,
        "stress_test_pass": stress_test_pass,
        "stress_ebit_margin_pct": stress_ebit_margin_pct,
        "rev_yoy_pct": rev_yoy_pct,
        "rev_cagr_pct": rev_cagr_pct,
        "margin_delta_pct": margin_delta_pct,
        "capex_to_da_ratio": capex_to_da_ratio,
        "capex_pct_of_ocf": capex_pct_of_ocf,
        "capex_pct_of_rev": capex_pct_of_rev,
        "goodwill_pct_of_equity": goodwill_pct_of_equity,
        "ar_yoy_pct": ar_yoy_pct,
        "inv_yoy_pct": inv_yoy_pct,
        "roiic_pct": roiic_pct,
        "passes_one_dollar_test": passes_one_dollar_test,
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
