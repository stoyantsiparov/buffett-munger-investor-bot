"""Official US SEC EDGAR XBRL API client (data.sec.gov) — 100% free, no API key required, immune to Yahoo cloud-IP limits."""

from __future__ import annotations

import time
from typing import Any
import httpx

# SEC EDGAR requires a "SampleCompanyName AdminContact@<domain>.com" User-Agent (without "Bot" which Akamai WAF blocks)
SEC_USER_AGENT = "ValueInvestingResearch research@valueinvestor.org"

_CIK_MAP_CACHE: dict[str, str] = {}
_CIK_CACHE_TS: float = 0.0
_FACTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# Pre-seeded CIK map for top US stocks so lookups are instant even before downloading company_tickers.json
STATIC_CIK_SEED: dict[str, str] = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "GOOG": "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
    "NVDA": "0001045810",
    "AVGO": "0001730168",
    "BRK.B": "0001067983",
    "BRK-B": "0001067983",
    "BRK.A": "0001067983",
    "JPM": "0000019617",
    "BAC": "0000070858",
    "WFC": "0000072971",
    "KO": "0000021344",
    "PEP": "0000077476",
    "PG": "0000080424",
    "COST": "0000909832",
    "WMT": "0000104169",
    "INTU": "0000896878",
    "CRM": "0001108524",
    "ADBE": "0000796343",
    "NFLX": "0001065280",
    "UNH": "0000731766",
    "JNJ": "0000200406",
    "LLY": "0000059478",
    "V": "0001403161",
    "MA": "0001141391",
    "AXP": "0000004962",
    "MCO": "0001059556",
    "SPGI": "0000064040",
    "FDS": "0001013237",
    "ICE": "0001571949",
    "UNP": "0000100885",
    "WM": "0000823768",
    "CAT": "0000018230",
    "DE": "0000315189",
    "OXY": "0000797468",
    "CVX": "0000093410",
    "XOM": "0000034088",
    "CRWD": "0001535527",
    "ANET": "0001596532",
    "ASML": "0000937966",
    "DAL": "0000027904",
    "CB": "0000896159",
    "PGR": "0000080661",
}


def _http_get_json(url: str, timeout: float = 6.5) -> Any:
    r = httpx.get(
        url,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def get_cik_for_ticker(ticker: str) -> str | None:
    """Resolve a US stock ticker to its 10-digit SEC CIK number."""
    global _CIK_MAP_CACHE, _CIK_CACHE_TS
    clean = ticker.strip().upper()
    if clean in STATIC_CIK_SEED:
        return STATIC_CIK_SEED[clean]
    alt = clean.replace(".", "-")
    if alt in STATIC_CIK_SEED:
        return STATIC_CIK_SEED[alt]

    now = time.time()
    if not _CIK_MAP_CACHE or (now - _CIK_CACHE_TS) > 86400:
        try:
            raw = _http_get_json("https://www.sec.gov/files/company_tickers.json", timeout=5.0)
            new_map: dict[str, str] = {}
            for item in (raw or {}).values():
                tk = str(item.get("ticker") or "").upper()
                cik_int = item.get("cik_str")
                if tk and cik_int is not None:
                    new_map[tk] = f"{int(cik_int):010d}"
            if new_map:
                _CIK_MAP_CACHE = new_map
                _CIK_CACHE_TS = now
        except Exception:
            pass

    return _CIK_MAP_CACHE.get(clean) or _CIK_MAP_CACHE.get(alt)


def _extract_annual_usd_series(us_gaap: dict, tags: tuple[str, ...], unit_keys: tuple[str, ...] = ("USD", "shares")) -> list[tuple[str, float]]:
    """Extract newest-first annual (10-K / FY) values for the first matching US-GAAP XBRL concept tag."""
    best_by_year: dict[str, float] = {}
    for tag in tags:
        node = us_gaap.get(tag)
        if not node:
            continue
        units = node.get("units") or {}
        entries = []
        for uk in unit_keys:
            if uk in units:
                entries = units[uk]
                break
        if not entries:
            continue

        year_map: dict[str, float] = {}
        for row in entries:
            form = str(row.get("form") or "")
            fp = str(row.get("fp") or "")
            frame = str(row.get("frame") or "")
            end_dt = str(row.get("end") or "")
            val = row.get("val")
            if val is None or not end_dt:
                continue
            # Only full-year 10-K / 20-F annual figures (exclude single quarters like CY2024Q1)
            is_annual = form in ("10-K", "10-K/A", "20-F", "40-F") and (fp == "FY" or ("Q" not in frame))
            if not is_annual:
                continue
            if frame and "Q" in frame and not frame.endswith("Q4"):
                continue
            # Filter out sub-year duration entries (< 300 days) when start date is present
            start_dt = str(row.get("start") or "")
            if start_dt and len(start_dt) >= 10 and len(end_dt) >= 10:
                try:
                    sy, sm = int(start_dt[:4]), int(start_dt[5:7])
                    ey, em = int(end_dt[:4]), int(end_dt[5:7])
                    months = (ey - sy) * 12 + (em - sm)
                    if months < 10:
                        continue
                except Exception:
                    pass
            yr = end_dt[:4]
            try:
                year_map[yr] = float(val)
            except Exception:
                continue

        for y, v in year_map.items():
            if y not in best_by_year:
                best_by_year[y] = v
        if len(best_by_year) >= 3:
            break

    return sorted(best_by_year.items(), key=lambda x: x[0], reverse=True)[:5]


def fetch_sec_edgar_annual_series(ticker: str) -> dict[str, Any]:
    """Fetch official audited 10-K GAAP time series from SEC EDGAR (`data.sec.gov`) for a US stock."""
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return {}

    now = time.time()
    cached = _FACTS_CACHE.get(cik)
    if cached and (now - cached[0]) < 3600:
        data = cached[1]
    else:
        try:
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            data = _http_get_json(url, timeout=6.5)
            _FACTS_CACHE[cik] = (now, data)
        except Exception:
            return {}

    facts = (data or {}).get("facts") or {}
    us_gaap = facts.get("us-gaap") or {}
    dei = facts.get("dei") or {}
    if not us_gaap:
        return {}

    entity_name = str((data or {}).get("entityName") or "").strip()

    revs = _extract_annual_usd_series(
        us_gaap,
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "RevenuesNetOfInterestExpense",
        ),
    )
    gross_profits = _extract_annual_usd_series(us_gaap, ("GrossProfit",))
    op_incomes = _extract_annual_usd_series(
        us_gaap,
        ("OperatingIncomeLoss", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"),
    )
    net_incomes = _extract_annual_usd_series(
        us_gaap,
        ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"),
    )
    ocfs = _extract_annual_usd_series(
        us_gaap,
        ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    )
    capexs = _extract_annual_usd_series(
        us_gaap,
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    )
    das = _extract_annual_usd_series(
        us_gaap,
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "Depreciation",
        ),
    )
    sbcs = _extract_annual_usd_series(
        us_gaap,
        ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    )
    equities = _extract_annual_usd_series(
        us_gaap,
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    )
    debts = _extract_annual_usd_series(
        us_gaap,
        ("LongTermDebtAndCapitalLeaseObligations", "LongTermDebt", "DebtInstrumentCarryingAmount"),
    )
    cashes = _extract_annual_usd_series(
        us_gaap,
        (
            "CashCashEquivalentsAndShortTermInvestments",
            "CashAndCashEquivalentsAtCarryingValue",
        ),
    )
    int_exps = _extract_annual_usd_series(
        us_gaap,
        ("InterestExpense", "InterestExpenseNonoperating"),
    )
    rds = _extract_annual_usd_series(
        us_gaap,
        ("ResearchAndDevelopmentExpense",),
    )
    shares = _extract_annual_usd_series(
        us_gaap,
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "CommonStockSharesOutstanding"),
        unit_keys=("shares",),
    )
    if not shares:
        shares = _extract_annual_usd_series(dei, ("EntityCommonStockSharesOutstanding",), unit_keys=("shares",))

    return {
        "source": "SEC_EDGAR_10K",
        "cik": cik,
        "entity_name": entity_name,
        "revs": revs,
        "gross_profits": gross_profits,
        "op_incomes": op_incomes,
        "net_incomes": net_incomes,
        "ocfs": ocfs,
        "capexs": capexs,
        "das": das,
        "sbcs": sbcs,
        "equities": equities,
        "debts": debts,
        "cashes": cashes,
        "int_exps": int_exps,
        "rds": rds,
        "shares": shares,
    }
