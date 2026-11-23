"""Warren Buffett & Charlie Munger Dynamic Investment Agent, Top-3 Picker, and Monthly Check Engine."""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
from sqlalchemy.orm import Session
from bot.crypto import decrypt_secret
from bot.market_service import fetch_stock_fundamentals
from bot.models import PortfolioSnapshot, User, UserLoan, WatchlistItem
from bot.t212_service import SEMICONDUCTOR_SHORT_TICKERS, parse_t212_snapshot

BROAD_EU_ETFS = {"VWCE", "IWDA", "SXR8", "CSPX", "EUNL", "VUAA", "SPYI", "WEBN"}
SKIP_SINGLE_STOCK_VALUATION_ETFS = BROAD_EU_ETFS | {"SEC0", "XDWU", "ESIF", "BGX", "EXX5", "QDVE", "EQQQ"}

KNOWN_MOAT_SUMMARIES = {
    "V": "Двустранен мрежови ефект + Глобален разплащателен тол-мост • Стабилен",
    "MA": "Двустранен мрежови ефект + Глобален разплащателен тол-мост • Стабилен",
    "SPGI": "Институционален рейтингов олигопол + S&P 500 индекси • Стабилен",
    "MSFT": "Неразрушими разходи за смяна (Enterprise Cloud / Office / Windows) • Разширяващ се",
    "GOOGL": "Мрежови ефект + Данни + Search / YouTube / Cloud екосистема • Стабилен",
    "META": "Глобален социален мрежови ефект (3.2B+ потребители) • Стабилен",
    "AMZN": "Логистичен мащаб + AWS облачен лидер + Prime екосистема • Разширяващ се",
    "ZTS": "Патенти и лидерство №1 във ветеринарната медицина • Стабилен",
    "UNH": "Мащаб #1 в здравното застраховане + Optum данни • Стабилен",
    "JPM": "Мащаб + Крепостен баланс #1 в американското банкиране • Стабилен",
    "NFLX": "Мащаб #1 в стрийминга + Глобален абонаментен модел • Разширяващ се",
    "AVGO": "Персонализирани AI чипове + VMware разходи за смяна • Стабилен",
    "NVDA": "CUDA софтуерна екосистема + Лидер в AI ускорителите • Под карантина (SEC)",
}

CORE_BUFFETT_SYSTEM_PROMPT = """Ти си личен финансов и инвестиционен агент по пълната система на Уорън Бъфет и Чарли Мънгър.
Отговаряй на чист български език — конкретно, кратко, с точни числа и без общи приказки.

1. Инвестиционна рамка (Бъфет & Мънгър):
- Бърз филтър (8 точки): Кръг на компетентност, трайност (10+ г.), икономически ров (вид + посока: разширяващ се / стабилен / стесняващ се), ценова сила (+5–10%), кешова конверсия (OCF/Net Income > 90%), дългова безопасност (Net Debt/EBITDA < 2.0x; стрес тест −30% приходи), почтеност на мениджмънта ($1 тест, автоматично вето при измама) и Марж на безопасност (MoS 20–50%).
- Метрики: Owner Earnings (OCF − Поддържащ CapEx, отделяйки растящия CapEx), 10-г. ROIC > 15%. За банки/застрахователи: ROE > 15%, ROA > 1.2%, CET1 > 10%, Combined Ratio < 100%, Float.
- 4 критерия за продажба: (1) Цената екстремно надхвърля IV; (2) Ровът е трайно разрушен; (3) Непочтен мениджмънт/измама (незабавна продажба); (4) Значително по-добра възможност с много по-висок MoS. Не продавай заради краткосрочна волатилност (Mr. Market).

2. Лични правила за капитала:
- Инверсия на Мънгър: Първо се погасяват гарантирани кредити с висока лихва (>10–12% ГПР).
- Ликвиден буфер: Минимум €500 неприкосновен буфер (Revolut/кеш). Ако буферът е под целта, винаги предлагай част от парите да отидат първо за него.
- Секторна концентрация: Максимум 20–25% в един рисков сектор (следи сумарния дял на полупроводниците). При >20% чипове НЕ препоръчвай нови чипове.
- Данъци (България): 0% данък капиталова печалба за UCITS ETF на регулиран пазар в ЕС/ЕИП (VWCE и др.); 10% за акции извън ЕС (САЩ) и 10% удържан дивидент в САЩ.
- Работи САМО с подадените реални данни от портфейла и Watchlist-а (и актуални новини от Google Search). Никога не измисляй цени или позиции.
"""


def format_bg_ordinal(day: int) -> str:
    """Format a day of month (1-31) in natural Bulgarian ordinal neuter form (1-во, 2-ро, 13-то...)."""
    if day in (1, 21, 31):
        return f"{day}-во"
    if day in (2, 22):
        return f"{day}-ро"
    if day in (7, 8, 27, 28):
        return f"{day}-мо"
    if day in (5, 6, 25, 26, 29, 30):
        return f"{day}-о"
    return f"{day}-то"


def _get_gemini_api_key(user: User | None = None, session: Session | None = None) -> str:
    """Resolve Gemini API key: 1) User's own encrypted key (/set_gemini_key), 2) Server .env, 3) Owner's shared key in DB."""
    if user and getattr(user, "gemini_api_key_enc", None):
        try:
            k = decrypt_secret(user.gemini_api_key_enc).strip()
            if k and k != "YOUR_GEMINI_API_KEY_HERE":
                return k
        except Exception:
            pass

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("GEMINI_API_KEY=") and not s.startswith("#"):
                    gemini_key = s.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY_HERE":
        return gemini_key

    if session is not None:
        owner = session.query(User).order_by(User.id.asc()).first()
        if owner and getattr(owner, "gemini_api_key_enc", None):
            try:
                k = decrypt_secret(owner.gemini_api_key_enc).strip()
                if k and k != "YOUR_GEMINI_API_KEY_HERE":
                    return k
            except Exception:
                pass
    return ""


def load_buffett_system_instruction(include_references: bool = False) -> str:
    """Return the token-optimized, self-contained Buffett & Munger system instruction (~70% fewer tokens, cloud-safe)."""
    return CORE_BUFFETT_SYSTEM_PROMPT


def _w_get(watch_item: WatchlistItem | dict | None, key: str, default=None):
    if watch_item is None:
        return default
    if isinstance(watch_item, dict):
        return watch_item.get(key, default)
    return getattr(watch_item, key, default)


def compute_quantitative_buffett_scorecard(metrics: dict, watch_item: WatchlistItem | dict | None = None) -> dict:
    """Compute a deterministic Buffett & Munger quantitative scorecard and intrinsic value range."""
    price = metrics.get("price") or _w_get(watch_item, "last_price") or 0.0
    roic = metrics.get("roic_est_pct") or _w_get(watch_item, "roic_pct")
    roe = metrics.get("roe_pct")
    roa = metrics.get("roa_pct")
    cash_conv = metrics.get("cash_conversion_pct")
    nd_ebitda = metrics.get("net_debt_to_ebitda")
    op_margin = metrics.get("operating_margin_pct")
    oe_yield = metrics.get("owner_earnings_yield_pct")
    rev_growth = metrics.get("revenue_growth_pct") or 8.0
    sector = str(metrics.get("sector") or "")
    industry = str(metrics.get("industry") or "")
    is_bank_or_insurer = (
        sector == "Financial Services"
        and any(k in industry for k in ("Bank", "Insurance"))
    )

    w_iv_low = _w_get(watch_item, "intrinsic_value_low")
    w_iv_high = _w_get(watch_item, "intrinsic_value_high")
    if watch_item and w_iv_low and w_iv_high:
        iv_low = float(w_iv_low)
        iv_high = float(w_iv_high)
        target_buy = float(_w_get(watch_item, "target_buy_price") or iv_low * 0.85)
        mos_req = float(_w_get(watch_item, "margin_of_safety_pct") or 25.0)
    elif is_bank_or_insurer and price > 0:
        # Buffett banking/insurance playbook: value on normalized EPS + ROE (not deposit-distorted OCF)
        pe = metrics.get("pe_trailing") or metrics.get("pe_forward")
        roe_val = float(roe or 12.0)
        bank_mult_low = 11.0 if roe_val >= 15.0 else (9.5 if roe_val >= 12.0 else 8.0)
        bank_mult_high = bank_mult_low + 2.5
        if pe and pe > 0:
            eps = price / pe
            iv_low = round(eps * bank_mult_low, 2)
            iv_high = round(eps * bank_mult_high, 2)
        else:
            iv_low = round(price * 0.85, 2)
            iv_high = round(price * 1.05, 2)
        mos_req = 25.0 if roe_val >= 15.0 else 30.0
        target_buy = round(iv_low * (1.0 - mos_req / 200.0), 2)
    else:
        quality_bonus = 1.15 if (roic and roic >= 15.0) or (roe and roe >= 18.0) else 0.95
        growth_clamped = max(-5.0, min(15.0, float(rev_growth)))
        fair_oe_multiple_low = min(20.0, max(10.0, (12.5 + growth_clamped * 0.40) * quality_bonus))
        fair_oe_multiple_high = fair_oe_multiple_low * 1.20

        pe_tr = metrics.get("pe_trailing")
        pe_fw = metrics.get("pe_forward")
        eps_tr = (price / pe_tr) if (pe_tr and pe_tr > 0 and price > 0) else None
        eps_fw = (price / pe_fw) if (pe_fw and pe_fw > 0 and price > 0) else None
        if eps_tr and eps_fw:
            norm_eps = min((eps_tr + eps_fw) / 2.0, eps_fw * 1.15)
        else:
            norm_eps = eps_fw or eps_tr

        ocf = float(metrics.get("operating_cashflow") or 0.0)
        ni = float(metrics.get("net_income") or 0.0)

        if oe_yield and oe_yield > 0 and price > 0:
            oe_per_share = price * (oe_yield / 100.0)
            # Anchor Owner Earnings against normalized EPS to prevent Growth CapEx or WC distortion
            if norm_eps and norm_eps > 0:
                if ocf > 0 and (ni <= 0 or ocf >= ni * 0.70):
                    oe_per_share = max(norm_eps * 0.80, min(oe_per_share, norm_eps * 1.15))
                else:
                    oe_per_share = min(oe_per_share, norm_eps * 1.15)
            iv_low = round(oe_per_share * fair_oe_multiple_low, 2)
            iv_high = round(oe_per_share * fair_oe_multiple_high, 2)
        elif norm_eps and norm_eps > 0:
            iv_low = round(norm_eps * fair_oe_multiple_low, 2)
            iv_high = round(norm_eps * fair_oe_multiple_high, 2)
        else:
            iv_low = round(price * 0.85, 2)
            iv_high = round(price * 1.05, 2)

        mos_req = 25.0 if (roic and roic >= 15.0) or (roe and roe >= 18.0) else 35.0
        target_buy = round(iv_low * (1.0 - mos_req / 200.0), 2)

    iv_mid = (iv_low + iv_high) / 2.0
    current_mos_pct = round(((iv_mid - price) / iv_mid) * 100.0, 1) if (iv_mid > 0 and price > 0) else 0.0

    checks = []
    passed = 0

    eff_roic = roe if is_bank_or_insurer else (roic if roic is not None else roe)
    if eff_roic is not None and eff_roic >= 15.0:
        label = "ROE" if is_bank_or_insurer else "ROIC/ROE"
        checks.append(f"✅ Висока възвръщаемост на капитала ({label}: `{eff_roic:.1f}%` >= 15%)")
        passed += 1
    elif eff_roic is not None:
        checks.append(f"⚠️ Възвръщаемост на капитала под целта на Бъфет (`{eff_roic:.1f}%` < 15%)")
    else:
        checks.append("ℹ️ ROIC/ROE: Няма достатъчно TTM данни")

    if is_bank_or_insurer and roa is not None:
        if roa >= 1.2:
            checks.append(f"✅ Силна банкова доходност на активите (ROA: `{roa:.2f}%` >= 1.2%)")
            passed += 1
        else:
            checks.append(f"⚠️ По-ниска банкова доходност на активите (ROA: `{roa:.2f}%` < 1.2%)")
    elif op_margin is not None and op_margin >= 18.0:
        checks.append(f"✅ Силна ценова сила и оперативен марж (`{op_margin:.1f}%` >= 18%)")
        passed += 1
    elif op_margin is not None:
        checks.append(f"⚠️ По-нисък оперативен марж (`{op_margin:.1f}%`)")

    if not is_bank_or_insurer:
        if cash_conv is not None and cash_conv >= 90.0:
            checks.append(f"✅ Отлично качество на печалбата (OCF/Net Income: `{cash_conv:.0f}%` > 90%)")
            passed += 1
        elif cash_conv is not None:
            checks.append(f"⚠️ По-слаба кешова конверсия (OCF/Net Income: `{cash_conv:.0f}%` < 90%)")

        if metrics.get("net_debt", 0) <= 0:
            checks.append("✅ Крепостен баланс: Повече кеш, отколкото дълг (Net Cash)")
            passed += 1
        elif nd_ebitda is not None and nd_ebitda < 2.0:
            checks.append(f"✅ Консервативен дълг (Net Debt/EBITDA: `{nd_ebitda:.2f}x` < 2.0x)")
            passed += 1
        elif nd_ebitda is not None:
            checks.append(f"⚠️ По-висок дълг (Net Debt/EBITDA: `{nd_ebitda:.2f}x` >= 2.0x)")

    if price > 0 and price <= target_buy:
        checks.append(f"🟢 В ЗОНАТА ЗА ПОКУПКА! Текуща цена `{price:.2f}` <= Target Buy `{target_buy:.2f}`")
        passed += 1
        verdict = "🟢 КУПИ (В зоната за покупка с Марж на безопасност)"
    elif price > 0 and price <= iv_high:
        checks.append(f"🟡 Справедлива цена (`{price:.2f}`), но изчакай Target Buy `<= {target_buy:.2f}` за пълен марж на безопасност")
        verdict = "🟡 ЗАДРЪЖ / ИЗЧАКАЙ КОРЕКЦИЯ ДО TARGET BUY"
    else:
        checks.append(f"🔴 Надценена спрямо консервативната Бъфет оценка (`{price:.2f}` > `{iv_high:.2f}`)")
        verdict = "⏳ ИЗЧАКАЙ КОРЕКЦИЯ (Твърде скъпа в момента)"

    return {
        "iv_low": iv_low,
        "iv_high": iv_high,
        "target_buy": target_buy,
        "mos_req": mos_req,
        "current_mos_pct": current_mos_pct,
        "checks": checks,
        "passed": passed,
        "verdict": verdict,
    }


def rank_portfolio_buy_candidates(parsed_snap: dict | None) -> dict:
    """Dynamically rank the best positions to buy RIGHT NOW inside the user's actual Trading 212 pies.

    Never hardcodes stock names — adapts automatically on every payday (13th / 1st) based on live
    underweight gaps, P&L pullbacks, broad EU ETF tax efficiency, and the 20-25% semiconductor cap.
    """
    if not parsed_snap or not parsed_snap.get("pies"):
        return {"pies": [], "top_overall": [], "semi_blocked": False, "semi_pct": 0.0}

    semi_pct = float(parsed_snap.get("semiconductor_pct") or 0.0)
    semi_blocked = semi_pct >= 20.0
    pie_results = []
    all_candidates = []

    for pie in parsed_snap.get("pies") or []:
        instruments = pie.get("instruments") or []
        eligible = []
        blocked = []

        for inst in instruments:
            ticker = inst["ticker"]
            val = float(inst.get("value") or 0.0)
            ppl = float(inst.get("ppl") or 0.0)
            invested = max(1.0, val - ppl)
            ppl_pct = round((ppl / invested) * 100.0, 2)
            cur_pct = float(inst.get("current_pct") or 0.0)
            exp_pct = float(inst.get("expected_pct") or 0.0)
            underweight = round(exp_pct - cur_pct, 2)

            if semi_blocked and ticker in SEMICONDUCTOR_SHORT_TICKERS:
                blocked.append(
                    {
                        "ticker": ticker,
                        "reason": f"полупроводници са {semi_pct:.1f}% (>20% таван)",
                        "cur_pct": cur_pct,
                        "exp_pct": exp_pct,
                    }
                )
                continue

            # Dynamic Buffett/Rebalancing score:
            # 1) Underweight vs target share (+3.5 pts per 1% underweight)
            # 2) Trading at pullback / below avg price (lower ppl_pct gets higher score)
            # 3) Broad 0%-tax UCITS ETF bonus in ETF pies
            is_broad_etf = ticker in BROAD_EU_ETFS
            score = (underweight * 3.5) + max(-15.0, min(25.0, -ppl_pct * 0.9))
            if is_broad_etf:
                score += 18.0
            # Penalize significantly overweight single stocks (>2% above target)
            if cur_pct > exp_pct + 2.0 and not is_broad_etf:
                score -= 20.0

            reason_parts = []
            if is_broad_etf:
                reason_parts.append("0% данък в ЕС + глобална диверсификация")
            if underweight > 0.3:
                reason_parts.append(f"под целта с -{underweight:.1f}%")
            if ppl_pct < 0:
                reason_parts.append(f"на отстъпка ({ppl_pct:.1f}% под ср. цена)")
            elif ppl_pct < 5.0 and not is_broad_etf:
                reason_parts.append(f"близо до ср. цена (+{ppl_pct:.1f}%)")
            if not reason_parts:
                reason_parts.append(f"стабилна позиция (дял {cur_pct:.1f}%/{exp_pct:.0f}%)")

            cand = {
                "pie_name": pie["name"],
                "ticker": ticker,
                "value": val,
                "ppl": ppl,
                "ppl_pct": ppl_pct,
                "cur_pct": cur_pct,
                "exp_pct": exp_pct,
                "underweight": underweight,
                "is_broad_etf": is_broad_etf,
                "score": round(score, 2),
                "reason": ", ".join(reason_parts),
            }
            eligible.append(cand)
            all_candidates.append(cand)

        eligible.sort(key=lambda x: x["score"], reverse=True)
        pie_results.append(
            {
                "pie_name": pie["name"],
                "pie_value": float(pie.get("value") or 0.0),
                "top_picks": eligible[:4],
                "blocked": blocked,
            }
        )

    all_candidates.sort(key=lambda x: x["score"], reverse=True)
    return {
        "pies": pie_results,
        "top_overall": all_candidates[:3],
        "semi_blocked": semi_blocked,
        "semi_pct": semi_pct,
    }


def evaluate_user_portfolio_undervalued(
    session: Session,
    user: User,
    latest_snap: PortfolioSnapshot | None = None,
) -> list[dict]:
    """Scan all individual stocks in the user's OWN Trading 212 portfolio for Buffett undervaluation (live prices + Owner Earnings IV)."""
    if not latest_snap or not latest_snap.raw_json:
        return []
    try:
        raw_data = json.loads(latest_snap.raw_json)
        parsed = parse_t212_snapshot(raw_data)
    except Exception:
        return []

    semi_pct = float(parsed.get("semiconductor_pct") or 0.0)
    semi_blocked = semi_pct >= 20.0

    # Collect unique single stocks held by this user (excluding ETFs)
    held_map: dict[str, dict] = {}
    for pie in parsed.get("pies") or []:
        for inst in pie.get("instruments") or []:
            t = inst["ticker"].upper()
            if t in SKIP_SINGLE_STOCK_VALUATION_ETFS:
                continue
            held_map[t] = {
                "ticker": t,
                "pie_name": pie["name"],
                "value": float(inst.get("value") or 0.0),
                "ppl": float(inst.get("ppl") or 0.0),
                "cur_pct": float(inst.get("current_pct") or 0.0),
                "exp_pct": float(inst.get("expected_pct") or 0.0),
            }

    if not held_map:
        return []

    # Pre-extract plain dicts from shared Watchlist BEFORE spawning threads (100% SQLAlchemy thread-safe)
    owner = session.query(User).order_by(User.id.asc()).first()
    master_id = owner.id if owner else 1
    watch_by_ticker = {
        w.ticker.upper(): {
            "last_price": w.last_price,
            "roic_pct": w.roic_pct,
            "intrinsic_value_low": w.intrinsic_value_low,
            "intrinsic_value_high": w.intrinsic_value_high,
            "target_buy_price": w.target_buy_price,
            "margin_of_safety_pct": w.margin_of_safety_pct,
            "moat_summary": w.moat_summary,
        }
        for w in session.query(WatchlistItem).filter(WatchlistItem.user_id == master_id).all()
    }

    def _eval_one(ticker: str) -> dict | None:
        try:
            metrics = fetch_stock_fundamentals(ticker)
            if not metrics or not metrics.get("price"):
                return None
            w_item = watch_by_ticker.get(ticker)
            score = compute_quantitative_buffett_scorecard(metrics, w_item)
            price = float(metrics["price"])
            iv_low = float(score["iv_low"])
            iv_high = float(score["iv_high"])
            target_buy = float(score["target_buy"])
            discount_high_pct = round(((iv_high - price) / iv_high) * 100.0, 1) if iv_high > 0 else 0.0
            in_buy_zone = price <= target_buy
            is_undervalued = price <= iv_high
            is_chip = ticker in SEMICONDUCTOR_SHORT_TICKERS

            cur = metrics.get("currency", "USD")
            sym = "$" if cur == "USD" else ("p " if cur == "GBX" else "€")
            moat = (
                (w_item.get("moat_summary") if w_item and w_item.get("moat_summary") else None)
                or KNOWN_MOAT_SUMMARIES.get(ticker)
                or f"ROIC: {metrics.get('roic_est_pct') or 'N/A'}% | Опер. марж: {metrics.get('operating_margin_pct') or 'N/A'}%"
            )
            info = held_map[ticker]
            invested = max(1.0, info["value"] - info["ppl"])
            ppl_pct = round((info["ppl"] / invested) * 100.0, 1)
            underweight = round(info["exp_pct"] - info["cur_pct"], 1)

            # Ranking score: Buy Zone + Discount to IV + Underweight gap; penalize chips if >20%
            rank_score = (discount_high_pct * 1.6) + (underweight * 3.0)
            if in_buy_zone:
                rank_score += 30.0
            if semi_blocked and is_chip:
                rank_score -= 60.0

            return {
                "ticker": ticker,
                "company_name": metrics.get("name") or ticker,
                "currency_sym": sym,
                "last_price": price,
                "target_buy_price": target_buy,
                "iv_low": iv_low,
                "iv_high": iv_high,
                "discount_pct": discount_high_pct,
                "in_buy_zone": in_buy_zone,
                "is_undervalued": is_undervalued,
                "is_chip": is_chip,
                "sector_warn": f"⚠️ Полупроводници са {semi_pct:.1f}% (>20%)" if (semi_blocked and is_chip) else "",
                "moat_summary": moat,
                "pie_name": info["pie_name"],
                "cur_pct": info["cur_pct"],
                "exp_pct": info["exp_pct"],
                "underweight": underweight,
                "ppl_pct": ppl_pct,
                "roic_pct": metrics.get("roic_est_pct") or metrics.get("roe_pct"),
                "rank_score": round(rank_score, 2),
            }
        except Exception:
            return None

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for res in pool.map(_eval_one, list(held_map.keys())):
            if res:
                results.append(res)

    results.sort(key=lambda x: x["rank_score"], reverse=True)
    return results


def rank_top3_watchlist_opportunities(
    session: Session,
    user: User,
    latest_snap: PortfolioSnapshot | None = None,
) -> list[dict]:
    """Dynamically rank the Top 3 best investment opportunities from the shared Buffett Watchlist right now."""
    owner = session.query(User).order_by(User.id.asc()).first()
    master_id = owner.id if owner else 1
    items = (
        session.query(WatchlistItem)
        .filter(WatchlistItem.user_id == master_id)
        .order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc())
        .all()
    )
    if not items:
        return []

    semi_pct = float(latest_snap.semiconductor_pct) if latest_snap else 0.0
    semi_blocked = semi_pct >= 20.0

    scored = []
    for w in items:
        if w.last_price is None or w.last_price <= 0 or not w.target_buy_price:
            continue
        iv_low = float(w.intrinsic_value_low or (w.target_buy_price * 1.25))
        iv_high = float(w.intrinsic_value_high or (w.target_buy_price * 1.45))
        iv_mid = (iv_low + iv_high) / 2.0

        # Positive gap_to_buy_pct means ALREADY below Target Buy Price!
        gap_to_buy_pct = round(((w.target_buy_price - w.last_price) / w.target_buy_price) * 100.0, 1)
        mos_mid_pct = round(((iv_mid - w.last_price) / iv_mid) * 100.0, 1) if iv_mid > 0 else 0.0
        in_buy_zone = w.last_price <= w.target_buy_price
        is_chip = w.ticker.upper() in SEMICONDUCTOR_SHORT_TICKERS

        # Score: prioritize Buy Zone + high Margin of Safety + ROIC > 15%; penalize chips if user > 20%
        score = (gap_to_buy_pct * 2.2) + (mos_mid_pct * 1.3)
        if in_buy_zone:
            score += 35.0
        if w.roic_pct and w.roic_pct >= 15.0:
            score += min(12.0, (w.roic_pct - 10.0) * 0.4)
        if semi_blocked and is_chip:
            score -= 60.0

        sym = "$" if w.currency == "USD" else ("p " if w.currency == "GBX" else "€")
        if in_buy_zone:
            status_tag = f"🟢 В ЗОНА ЗА ПОКУПКА (+{mos_mid_pct:.0f}% MoS)"
        elif gap_to_buy_pct >= -12.0:
            status_tag = f"🟡 На {abs(gap_to_buy_pct):.1f}% от Target Buy (MoS: {mos_mid_pct:+.0f}%)"
        else:
            status_tag = f"⏳ Чакаме корекция ({abs(gap_to_buy_pct):.0f}% над Target Buy)"

        sector_warn = "⚠️ Чипове >20% в портфейла" if (semi_blocked and is_chip) else ""
        scored.append(
            {
                "item": w,
                "ticker": w.ticker,
                "company_name": w.company_name or w.ticker,
                "currency_sym": sym,
                "last_price": float(w.last_price),
                "target_buy_price": float(w.target_buy_price),
                "iv_low": iv_low,
                "iv_high": iv_high,
                "mos_mid_pct": mos_mid_pct,
                "gap_to_buy_pct": gap_to_buy_pct,
                "in_buy_zone": in_buy_zone,
                "is_chip": is_chip,
                "sector_warn": sector_warn,
                "status_tag": status_tag,
                "roic_pct": w.roic_pct,
                "moat_summary": w.moat_summary or "Широк конкурентен ров",
                "score": round(score, 2),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:3]


def rank_unified_top3_opportunities(
    session: Session,
    user: User,
    latest_snap: PortfolioSnapshot | None = None,
    portfolio_evals: list[dict] | None = None,
) -> dict:
    """Head-to-head Top 3 Buffett ranking comparing BOTH the shared Watchlist AND the user's own Trading 212 portfolio.

    Lets every user see immediately whether today's best euro should reinforce an undervalued stock
    they ALREADY own (💼 Подсили) or open a new discounted position from the Watchlist (🎯 Нов от Watchlist).
    """
    owner = session.query(User).order_by(User.id.asc()).first()
    master_id = owner.id if owner else 1
    watch_items = (
        session.query(WatchlistItem)
        .filter(WatchlistItem.user_id == master_id)
        .order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc())
        .all()
    )

    semi_pct = float(latest_snap.semiconductor_pct) if latest_snap else 0.0
    semi_blocked = semi_pct >= 20.0

    # Map of unified candidates keyed by ticker (deduplicated if present in both Watchlist & Portfolio)
    candidates: dict[str, dict] = {}

    # 1. Pool A: Shared Watchlist candidates
    for w in watch_items:
        if w.last_price is None or w.last_price <= 0 or not w.target_buy_price:
            continue
        t = w.ticker.upper()
        iv_low = float(w.intrinsic_value_low or (w.target_buy_price * 1.25))
        iv_high = float(w.intrinsic_value_high or (w.target_buy_price * 1.45))
        iv_mid = (iv_low + iv_high) / 2.0
        price = float(w.last_price)
        target = float(w.target_buy_price)

        gap_to_buy_pct = round(((target - price) / target) * 100.0, 1)
        mos_mid_pct = round(((iv_mid - price) / iv_mid) * 100.0, 1) if iv_mid > 0 else 0.0
        discount_high_pct = round(((iv_high - price) / iv_high) * 100.0, 1) if iv_high > 0 else 0.0
        in_buy_zone = price <= target
        is_chip = t in SEMICONDUCTOR_SHORT_TICKERS

        score = (gap_to_buy_pct * 2.0) + (mos_mid_pct * 1.4)
        if in_buy_zone:
            score += 35.0
        if w.roic_pct and w.roic_pct >= 15.0:
            score += min(12.0, (w.roic_pct - 10.0) * 0.4)
        if semi_blocked and is_chip:
            score -= 80.0

        sym = "$" if w.currency == "USD" else ("p " if w.currency == "GBX" else "€")
        if in_buy_zone:
            status_tag = f"🟢 В ЗОНА ЗА ПОКУПКА (+{mos_mid_pct:.0f}% MoS)"
        elif gap_to_buy_pct >= -12.0:
            status_tag = f"🟡 На {abs(gap_to_buy_pct):.1f}% от Target Buy (MoS: {mos_mid_pct:+.0f}%)"
        else:
            status_tag = f"⏳ {abs(gap_to_buy_pct):.0f}% над Target Buy"

        candidates[t] = {
            "ticker": t,
            "company_name": w.company_name or t,
            "source": "watchlist",
            "source_badge": "🎯 Watchlist (нова)",
            "btn_icon": "🎯",
            "currency_sym": sym,
            "last_price": price,
            "target_buy_price": target,
            "iv_low": iv_low,
            "iv_high": iv_high,
            "mos_mid_pct": mos_mid_pct,
            "discount_pct": discount_high_pct,
            "gap_to_buy_pct": gap_to_buy_pct,
            "in_buy_zone": in_buy_zone,
            "is_chip": is_chip,
            "sector_warn": f"Чипове {semi_pct:.1f}% >20%" if (semi_blocked and is_chip) else "",
            "status_tag": status_tag,
            "roic_pct": w.roic_pct,
            "moat_summary": w.moat_summary or "Широк конкурентен ров",
            "cur_pct": None,
            "exp_pct": None,
            "pie_name": None,
            "score": round(score, 2),
        }

    # 2. Pool B: User's OWN Trading 212 Portfolio stocks (evaluated live!)
    for p in (portfolio_evals or []):
        t = p["ticker"].upper()
        price = float(p["last_price"])
        target = float(p["target_buy_price"])
        iv_low = float(p["iv_low"])
        iv_high = float(p["iv_high"])
        iv_mid = (iv_low + iv_high) / 2.0
        if price <= 0 or target <= 0:
            continue

        gap_to_buy_pct = round(((target - price) / target) * 100.0, 1)
        mos_mid_pct = round(((iv_mid - price) / iv_mid) * 100.0, 1) if iv_mid > 0 else 0.0
        in_buy_zone = bool(p["in_buy_zone"])
        is_chip = bool(p["is_chip"])
        roic_val = p.get("roic_pct")
        cur_pct = float(p.get("cur_pct") or 0.0)
        exp_pct = float(p.get("exp_pct") or 0.0)
        underweight = round(exp_pct - cur_pct, 1)

        score = (gap_to_buy_pct * 2.0) + (mos_mid_pct * 1.4)
        if in_buy_zone:
            score += 35.0
        if roic_val and roic_val >= 15.0:
            score += min(12.0, (roic_val - 10.0) * 0.4)
        # Buffett concentration/rebalancing adjustment: gently favor underweight great businesses you already know,
        # and penalize already-overweight single positions so users don't over-concentrate
        if underweight > 0:
            score += min(10.0, underweight * 2.5)
        elif cur_pct > exp_pct + 1.5 and exp_pct > 0:
            score -= min(15.0, (cur_pct - exp_pct) * 4.0)
        if semi_blocked and is_chip:
            score -= 80.0

        if in_buy_zone:
            status_tag = f"🟢 В ЗОНА ЗА ПОКУПКА (+{mos_mid_pct:.0f}% MoS)"
        elif price <= iv_high:
            status_tag = f"🟡 Подценена (+{p['discount_pct']:.0f}% до IV, дял {cur_pct:.1f}%/{exp_pct:.0f}%)"
        else:
            status_tag = f"⏳ {abs(gap_to_buy_pct):.0f}% над Target Buy"

        already_in_wl = t in candidates
        candidates[t] = {
            "ticker": t,
            "company_name": p["company_name"],
            "source": "both" if already_in_wl else "portfolio",
            "source_badge": f"💼 Твой портфейл (подсили {cur_pct:.1f}%/{exp_pct:.0f}%)",
            "btn_icon": "💼",
            "currency_sym": p["currency_sym"],
            "last_price": price,
            "target_buy_price": target,
            "iv_low": iv_low,
            "iv_high": iv_high,
            "mos_mid_pct": mos_mid_pct,
            "discount_pct": float(p["discount_pct"]),
            "gap_to_buy_pct": gap_to_buy_pct,
            "in_buy_zone": in_buy_zone,
            "is_chip": is_chip,
            "sector_warn": f"Чипове {semi_pct:.1f}% >20%" if (semi_blocked and is_chip) else "",
            "status_tag": status_tag,
            "roic_pct": roic_val,
            "moat_summary": p["moat_summary"],
            "cur_pct": cur_pct,
            "exp_pct": exp_pct,
            "pie_name": p["pie_name"],
            "score": round(score, 2),
        }

    ranked_all = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    # Enforce Buffett/Munger Sector Cap Iron Rule: exclude blocked semiconductors from Top 3 buy picks when >20%
    eligible_for_top3 = [x for x in ranked_all if not (semi_blocked and x["is_chip"])]
    top3 = (eligible_for_top3 or ranked_all)[:3]

    # Build a crisp head-to-head comparison takeaway for the user
    has_port = [x for x in top3 if x["source"] in ("portfolio", "both")]
    has_wl = [x for x in top3 if x["source"] == "watchlist"]
    insight = ""
    if has_port and has_wl:
        p1 = has_port[0]
        w1 = has_wl[0]
        if top3[0]["source"] in ("portfolio", "both"):
            insight = (
                f"Днес *`{p1['ticker']}`* (вече в портфейла ти) води класирането — можеш да я подсилиш на отстъпка "
                f"без да раздробяваш портфейла, или да добавиш нова позиция в *`{w1['ticker']}`* от Watchlist."
            )
        else:
            insight = (
                f"Днес *`{w1['ticker']}`* (нова от Watchlist) предлага най-висок марж на безопасност, "
                f"а *`{p1['ticker']}`* е най-силната подценена позиция за подсилване вътре в портфейла ти!"
            )
    elif has_port:
        p1 = has_port[0]
        insight = (
            f"Днес най-добрите сделки са *вътре в твоя портфейл* (води *`{p1['ticker']}`*) — "
            "по Бъфет е по-умно да подсилиш чудесен бизнес, който вече притежаваш на отстъпка, вместо да купуваш по-скъпа нова компания!"
        )
    elif has_wl:
        w1 = has_wl[0]
        insight = (
            f"Днес *`{w1['ticker']}`* от общия Watchlist предлага по-добра цена и марж на безопасност от акциите в портфейла."
        )

    return {"top3": top3, "all_ranked": ranked_all, "comparison_insight": insight}


def build_dynamic_investment_plan_lines(
    session: Session,
    user: User,
    latest_snap: PortfolioSnapshot | None = None,
) -> list[str]:
    """Build a 100% dynamic, non-hardcoded monthly investment plan from live portfolio, loans, buffer, and Watchlist."""
    cur = "€" if user.t212_currency == "EUR" else user.t212_currency
    lines: list[str] = []

    parsed_snap = None
    if latest_snap and latest_snap.raw_json:
        try:
            parsed_snap = parse_t212_snapshot(json.loads(latest_snap.raw_json))
        except Exception:
            parsed_snap = None

    active_loans = (
        session.query(UserLoan)
        .filter(UserLoan.user_id == user.id, UserLoan.is_active.is_(True))
        .all()
    )
    high_apr_loans = [l for l in active_loans if l.apr_percent >= 10.0]

    # 1. Dynamic Emergency Buffer Rule (Buffett's Liquidity Rule)
    buf_missing = max(0.0, round(user.emergency_buffer_target - user.emergency_buffer_current, 2))
    inv_budget = float(user.monthly_invest_budget or 0.0)
    investable_now = inv_budget

    if buf_missing > 0 and inv_budget > 0:
        suggested_to_buffer = round(min(buf_missing, max(50.0, inv_budget * 0.45)), 2)
        investable_now = round(max(0.0, inv_budget - suggested_to_buffer), 2)
        lines.append(
            f"🛡️ *ПРИОРИТЕТ — Ликвиден буфер:* Текущо `{cur}{user.emergency_buffer_current:,.2f}` / цел `{cur}{user.emergency_buffer_target:,.2f}` "
            f"_(липсват `{cur}{buf_missing:,.2f}`)_. По правилото на Бъфет задели първо *`{cur}{suggested_to_buffer:,.2f}`* към Revolut буфера, "
            f"а останалите *`{cur}{investable_now:,.2f}`* инвестирай в Trading 212!"
        )
    elif buf_missing > 0:
        lines.append(
            f"🛡️ *Ликвиден буфер:* Текущо `{cur}{user.emergency_buffer_current:,.2f}` / цел `{cur}{user.emergency_buffer_target:,.2f}` — "
            "изгради си неприкосновен буфер преди рискови покупки, за да не си принуден никога да продаваш добри акции."
        )

    # 2. Dynamic High-Interest Loan Payoff Projection (Munger's Inversion)
    if high_apr_loans:
        total_loan_pmt = 0.0
        for l in high_apr_loans:
            fee = round(l.early_principal_payment * (l.early_fee_percent / 100.0), 2) if l.early_principal_payment > 0 else 0.0
            pmt = round(l.regular_payment + l.early_principal_payment + fee, 2)
            total_loan_pmt += pmt
            principal_reduction_mo = max(1.0, l.early_principal_payment + l.regular_payment * 0.84)
            months_left = max(1, math.ceil(l.principal_balance / principal_reduction_mo))
            lines.append(
                f"💳 *Инверсия на Мънгър ({l.lender_name} — {l.apr_percent:.2f}% ГПР):* "
                f"`{cur}{pmt:,.2f}/мес.` → остават ~`{months_left}` мес. до `€0` дълг, след което тези `{cur}{pmt:,.2f}/мес.` се освобождават за инвестиции!"
            )

    if inv_budget <= 0:
        lines.append("💡 _Настрой месечния си бюджет с_ `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР`_, за да изчислява ботът точните суми._")
        return lines

    # 3. Dynamic Per-Pie Live Recommendations (from actual T212 snapshot!)
    ranked_port = rank_portfolio_buy_candidates(parsed_snap)
    if ranked_port["pies"]:
        if ranked_port["semi_blocked"]:
            lines.append(
                f"⚠️ *Сектор Полупроводници е `{ranked_port['semi_pct']:.2f}%` (>20% таван):* "
                "Ботът автоматично изключи чиповете от днешните покупки."
            )
        total_pie_val = sum(p["pie_value"] for p in ranked_port["pies"]) or 1.0
        for p in ranked_port["pies"]:
            weight = p["pie_value"] / total_pie_val if len(ranked_port["pies"]) > 1 else 1.0
            pie_alloc = round(investable_now * weight, 2)
            top_tickers = ", ".join(
                f"`{c['ticker']}` ({c['reason']})" for c in p["top_picks"][:3]
            )
            blocked_tickers = ", ".join(f"`{b['ticker']}`" for b in p["blocked"])
            blocked_note = f" | *€0 за:* {blocked_tickers}" if blocked_tickers else ""
            lines.append(
                f"🥧 *{p['pie_name']}:* `~{cur}{pie_alloc:,.2f}` → Топ избор днес: {top_tickers or 'Няма подходящи'}{blocked_note}"
            )
    else:
        lines.append(
            f"📈 *Месечна инвестиция:* `{cur}{investable_now:,.2f}` — насочи към широк UCITS ETF (`VWCE`, 0% данък в ЕС) "
            "и най-подценените компании от Топ 3 на общия Watchlist."
        )

    # 4. Dynamic Top 3 Opportunities from Watchlist RIGHT NOW
    top3_wl = rank_top3_watchlist_opportunities(session, user, latest_snap)
    if top3_wl:
        wl_str = " | ".join(
            f"*#{i+1} `{x['ticker']}`* (`{x['currency_sym']}{x['last_price']:.2f}`, {x['status_tag']})"
            for i, x in enumerate(top3_wl)
        )
        lines.append(f"🌟 *Топ 3 идеи от Watchlist днес:* {wl_str}")

    return lines


def analyze_stock_with_buffett(
    metrics: dict,
    watch_item: WatchlistItem | None = None,
    user: User | None = None,
    latest_snap: PortfolioSnapshot | None = None,
    session: Session | None = None,
) -> str:
    """Produce a full Bulgarian Buffett & Munger stock report (Quantitative + Gemini 4 Hydrogen AI)."""
    cur = metrics.get("currency", "USD")
    sym = "$" if cur == "USD" else ("p " if cur == "GBX" else ("€" if cur == "EUR" else f"{cur} "))
    price = metrics.get("price") or (watch_item.last_price if watch_item else None) or 0.0
    score = compute_quantitative_buffett_scorecard(metrics, watch_item)

    mcap_b = (metrics.get("market_cap") or 0.0) / 1e9
    oe_b = (metrics.get("owner_earnings_est") or 0.0) / 1e9
    nd_b = (metrics.get("net_debt") or 0.0) / 1e9

    header_lines = [
        f"🧠 *БЪФЕТ & МЪНГЪР АНАЛИЗ: {metrics['ticker']} ({metrics['name']})*",
        f"🏷️ *Сектор:* {metrics.get('sector', 'N/A')} | {metrics.get('industry', 'N/A')}",
        "",
        "📊 *1. Ключови фундаментални метрики (TTM):*",
        f"  • *Текуща цена:* `{sym}{price:,.2f}` (Пазарна кап.: `{sym}{mcap_b:,.1f}B`)",
        f"  • *P/E (Trailing / Forward):* `{metrics.get('pe_trailing') or 'N/A'}` / `{metrics.get('pe_forward') or 'N/A'}`",
        f"  • *Брутен / Оперативен марж:* `{metrics.get('gross_margin_pct') or 'N/A'}%` / `{metrics.get('operating_margin_pct') or 'N/A'}%`",
        f"  • *ROIC (оценка) / ROE:* `{metrics.get('roic_est_pct') or (watch_item.roic_pct if watch_item else 'N/A')}%` / `{metrics.get('roe_pct') or 'N/A'}%`",
        f"  • *Owner Earnings (OCF - Maint. CapEx):* `{sym}{oe_b:,.2f}B` (Yield: `{metrics.get('owner_earnings_yield_pct') or 'N/A'}%`)",
        f"  • *Кешова конверсия (OCF / Net Income):* `{metrics.get('cash_conversion_pct') or 'N/A'}%`",
        f"  • *Нетен дълг (Net Debt):* `{sym}{nd_b:,.2f}B` (Net Debt/EBITDA: `{metrics.get('net_debt_to_ebitda') if metrics.get('net_debt_to_ebitda') is not None else 'Net Cash'}x`)",
    ]

    if watch_item and watch_item.moat_summary:
        header_lines.extend([
            "",
            f"🏰 *Конкурентен ров (от Watchlist):* {watch_item.moat_summary}",
        ])

    header_lines.extend([
        "",
        "⚖️ *2. Бърз филтър на Бъфет & Оценка на Вътрешната стойност:*",
        *[f"  {c}" for c in score["checks"]],
        "",
        f"🎯 *Вътрешна стойност (Intrinsic Value):* `{sym}{score['iv_low']:,.2f} – {sym}{score['iv_high']:,.2f}`",
        f"🛡️ *Целева цена за покупка (Target Buy с {score['mos_req']:.0f}% MoS):* `<= {sym}{score['target_buy']:,.2f}`",
        f"📌 *Присъда:* *{score['verdict']}*",
    ])

    if watch_item and watch_item.notes:
        header_lines.append(f"📝 *Бележка:* _{watch_item.notes}_")

    gemini_key = _get_gemini_api_key(user=user, session=session)
    if gemini_key:
        try:
            from google import genai

            client = genai.Client(api_key=gemini_key)
            portfolio_ctx = ""
            if latest_snap:
                portfolio_ctx = (
                    f"Портфейл на потребителя: Обща стойност €{latest_snap.total_value:.2f}, "
                    f"Дял полупроводници: {latest_snap.semiconductor_pct:.2f}% (лимит 20-25%)."
                )
            prompt = (
                f"Анализирай компанията {metrics['ticker']} ({metrics['name']}) по системата на Уорън Бъфет и Чарли Мънгър.\n"
                f"Данни: {json.dumps(metrics, ensure_ascii=False)}\n"
                f"Изчислен диапазон на вътрешна стойност: {sym}{score['iv_low']} - {sym}{score['iv_high']}, "
                f"Target Buy Price: <= {sym}{score['target_buy']}.\n"
                f"{portfolio_ctx}\n"
                "Напиши стегнато обобщение в 4 кратки секции (до 1200 знака общо):\n"
                "1) Вид и посока на рова (Moat)\n"
                "2) Мениджмънт и капиталова алокация ($1 тест)\n"
                "3) Инверсия на Мънгър (Топ 2 риска)\n"
                "4) Кога да продадем (4-те критерия на Бъфет)."
            )
            interaction = client.interactions.create(
                model="gemini-4-hydrogen",
                system_instruction=load_buffett_system_instruction(),
                input=prompt,
                tools=[{"type": "google_search"}],
            )
            if interaction and interaction.output_text:
                header_lines.extend([
                    "",
                    "🤖 *3. Задълбочен AI Анализ (Бъфет & Мънгър Агент + Живи новини):*",
                    interaction.output_text.strip(),
                ])
        except Exception as e:
            header_lines.append(f"\n_(Бележка: AI коментарът не беше зареден: {e})_")

    return "\n".join(header_lines)


def ask_buffett_agent(
    session: Session,
    user: User,
    question: str,
    latest_snap: PortfolioSnapshot | None = None,
) -> str:
    """Conversational Buffett & Munger Agent inside Telegram with full live portfolio + Watchlist context."""
    if latest_snap is None:
        latest_snap = (
            session.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.user_id == user.id)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )

    parsed_snap = None
    if latest_snap and latest_snap.raw_json:
        try:
            parsed_snap = parse_t212_snapshot(json.loads(latest_snap.raw_json))
        except Exception:
            parsed_snap = None

    active_loans = (
        session.query(UserLoan)
        .filter(UserLoan.user_id == user.id, UserLoan.is_active.is_(True))
        .all()
    )
    top3_wl = rank_top3_watchlist_opportunities(session, user, latest_snap)
    ranked_port = rank_portfolio_buy_candidates(parsed_snap)
    port_evals = evaluate_user_portfolio_undervalued(session, user, latest_snap) if latest_snap else []
    unified = rank_unified_top3_opportunities(session, user, latest_snap, port_evals)

    gemini_key = _get_gemini_api_key(user=user, session=session)
    if gemini_key:
        try:
            from google import genai

            client = genai.Client(api_key=gemini_key)
            loans_ctx = [
                {
                    "lender": l.lender_name,
                    "principal": l.principal_balance,
                    "apr_pct": l.apr_percent,
                    "regular_pmt": l.regular_payment,
                    "early_pmt": l.early_principal_payment,
                }
                for l in active_loans
            ]
            wl_ctx = [
                {
                    "ticker": x["ticker"],
                    "name": x["company_name"],
                    "price": x["last_price"],
                    "target_buy": x["target_buy_price"],
                    "iv": f"{x['iv_low']}-{x['iv_high']}",
                    "mos_pct": x["mos_mid_pct"],
                    "in_buy_zone": x["in_buy_zone"],
                    "roic": x["roic_pct"],
                    "moat": x["moat_summary"],
                }
                for x in top3_wl
            ]
            # Compact portfolio context for AI
            port_ctx = {}
            if parsed_snap:
                port_ctx = {
                    "total_eur": parsed_snap["total_value"],
                    "unrealized_ppl": parsed_snap["unrealized_ppl"],
                    "semiconductor_pct": parsed_snap["semiconductor_pct"],
                    "semiconductor_breakdown": parsed_snap["semiconductor_breakdown"],
                    "pies": [
                        {
                            "name": p["name"],
                            "value": p["value"],
                            "instruments": [
                                {
                                    "ticker": i["ticker"],
                                    "val": i["value"],
                                    "ppl": i["ppl"],
                                    "cur_pct": i["current_pct"],
                                    "exp_pct": i["expected_pct"],
                                }
                                for i in p["instruments"]
                            ],
                        }
                        for p in parsed_snap["pies"]
                    ],
                }

            unified_ctx = [
                {
                    "ticker": x["ticker"],
                    "source": x["source_badge"],
                    "price": x["last_price"],
                    "target_buy": x["target_buy_price"],
                    "iv": f"{x['iv_low']:.0f}-{x['iv_high']:.0f}",
                    "mos_pct": x["mos_mid_pct"],
                    "in_buy_zone": x["in_buy_zone"],
                }
                for x in unified["top3"]
            ]

            full_prompt = (
                f"Въпрос ({user.full_name}): {question}\n"
                f"Профил: доход €{user.monthly_income:.0f}, бюджет €{user.monthly_invest_budget:.0f}, "
                f"буфер €{user.emergency_buffer_current:.0f}/€{user.emergency_buffer_target:.0f}, отчет {user.payday_day}-о число.\n"
                f"Кредити: {json.dumps(loans_ctx, ensure_ascii=False, separators=(',', ':'))}\n"
                f"Портфейл T212: {json.dumps(port_ctx, ensure_ascii=False, separators=(',', ':'))}\n"
                f"Обединен Топ 3 (Watchlist vs Портфейл): {json.dumps(unified_ctx, ensure_ascii=False, separators=(',', ':'))}\n"
                "Отговори стегнато за Telegram (до 2400 знака), с конкретни числа и ясен избор "
                "дали да подсили позиция в портфейла или да добави нова от Watchlist."
            )
            interaction = client.interactions.create(
                model="gemini-4-hydrogen",
                system_instruction=load_buffett_system_instruction(include_references=True),
                input=full_prompt,
                tools=[{"type": "google_search"}],
            )
            if interaction and interaction.output_text:
                return f"🤖 *БЪФЕТ & МЪНГЪР АГЕНТ:*\n\n{interaction.output_text.strip()}"
        except Exception as e:
            fallback_note = f"_(AI връзката върна грешка: {e} — показвам живия количествен разчет по Бъфет)_\n\n"
    else:
        fallback_note = (
            "💡 _За свободен AI разговор с новини на живо добави безплатен Gemini API ключ направо тук с_ "
            "`/set_gemini_key КЛЮЧ` _(виж ℹ️ Помощ за 30-секундна инструкция). Ето живия ти динамичен разчет в момента:_\n\n"
        )

    # Smart deterministic Buffett Agent answer using unified Watchlist vs Portfolio Top 3 + buffer/loans!
    plan_lines = build_dynamic_investment_plan_lines(session, user, latest_snap)
    lines = [
        "🤖 *БЪФЕТ & МЪНГЪР АГЕНТ (ДИНАМИЧЕН АНАЛИЗ ДНЕС)*",
        fallback_note.strip(),
        "",
    ]
    if unified["top3"]:
        lines.append("🏆 *ОБЩ ТОП 3 ДНЕС (WATCHLIST vs. ТВОЯТ ПОРТФЕЙЛ):*")
        for idx, x in enumerate(unified["top3"], 1):
            sym = x["currency_sym"]
            lines.append(
                f"  {idx}️⃣ *`{x['ticker']}`* — {x['source_badge']}\n"
                f"     └ `{sym}{x['last_price']:,.2f}` | IV `{sym}{x['iv_low']:,.0f}–{sym}{x['iv_high']:,.0f}` | {x['status_tag']}"
            )
        if unified["comparison_insight"]:
            lines.append(f"💡 _{unified['comparison_insight']}_")
        lines.append("")

    lines.extend([
        "📋 *Динамична препоръка според твоя портфейл, буфер и пазарните цени:*",
        *[f"• {l}" for l in plan_lines],
    ])
    return "\n".join(lines)


def generate_monthly_check_report(session: Session, user: User) -> str:
    """Generate the complete 5-section Monthly Check (/monthly_check) report dynamically (ZERO hardcoding)."""
    latest_snap = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.user_id == user.id)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )
    active_loans = (
        session.query(UserLoan)
        .filter(UserLoan.user_id == user.id, UserLoan.is_active.is_(True))
        .all()
    )

    cur = "€" if user.t212_currency == "EUR" else user.t212_currency
    day_suffix = format_bg_ordinal(user.payday_day)
    lines = [
        f"📅 *МЕСЕЧЕН ФИНАНСОВ ОТЧЕТ ({day_suffix} число на месеца)*",
        f"👤 *Потребител:* {user.full_name or user.username or user.telegram_id}",
        "",
    ]

    # Section 1: 📊 Състояние на портфейла
    lines.append("📊 *1. СЪСТОЯНИЕ НА ТВОЯ ПОРТФЕЙЛ*")
    if latest_snap:
        ppl_sign = "+" if latest_snap.unrealized_ppl >= 0 else ""
        semi_warn = (
            "⚠️ Над тавана от 25% — НЕ добавяй нови полупроводници!"
            if latest_snap.semiconductor_pct >= 25.0
            else ("⚡ Над 20% — избягвай нови чипове" if latest_snap.semiconductor_pct >= 20.0 else "✅ В норма (<20%)")
        )
        lines.extend([
            f"  • *Обща стойност в Trading 212:* `{cur}{latest_snap.total_value:,.2f}`",
            f"  • *Инвестирани позиции:* `{cur}{latest_snap.invested_value:,.2f}` | *Кеш в пайове:* `{cur}{latest_snap.pie_cash:,.2f}`",
            f"  • *Нереализиран P&L:* `{ppl_sign}{cur}{latest_snap.unrealized_ppl:,.2f}` | *Реализиран:* `+{cur}{latest_snap.realized_result:,.2f}`",
            f"  • *Сектор Полупроводници:* `{latest_snap.semiconductor_pct:.2f}%` ({semi_warn})",
        ])
    else:
        lines.append("  • _Все още нямаш свързан Trading 212 портфейл. Използвай_ `/connect_t212`_, за да го синхронизираш._")

    # Section 2: 🏆 Динамичен Топ 3 от Общия Watchlist днес
    lines.extend(["", "🧠 *2. ТОП 3 ВЪЗМОЖНОСТИ ОТ WATCHLIST ДНЕС*"])
    top3_wl = rank_top3_watchlist_opportunities(session, user, latest_snap)
    if top3_wl:
        for i, item in enumerate(top3_wl, 1):
            warn = f" ({item['sector_warn']})" if item["sector_warn"] else ""
            lines.append(
                f"  {i}️⃣ *`{item['ticker']}`* ({item['company_name']}): `{item['currency_sym']}{item['last_price']:.2f}` "
                f"| Target Buy `<= {item['currency_sym']}{item['target_buy_price']:.2f}` | {item['status_tag']}{warn}"
            )
    else:
        lines.append("  • ⏳ В момента няма заредени компании в Watchlist-а.")

    # Section 3: 💳 Действие по кредити (Инверсия на Мънгър)
    lines.extend(["", "💳 *3. ДЕЙСТВИЕ ПО КРЕДИТИ (Инверсия на Мънгър)*"])
    total_loan_outflow = 0.0
    if active_loans:
        for loan in active_loans:
            fee_eur = round(loan.early_principal_payment * (loan.early_fee_percent / 100.0), 2) if loan.early_principal_payment > 0 else 0.0
            monthly_loan_total = round(loan.regular_payment + loan.early_principal_payment + fee_eur, 2)
            total_loan_outflow += monthly_loan_total
            new_principal_est = max(0.0, round(loan.principal_balance - loan.early_principal_payment - (loan.regular_payment * 0.84), 2))
            lines.extend([
                f"  • *Кредитор:* *{loan.lender_name}* {f'(Договор № {loan.contract_number})' if loan.contract_number else ''}",
                f"  • *Текуща главница:* `{cur}{loan.principal_balance:,.2f}` при ГПР `{loan.apr_percent:.2f}%`",
                f"  • *1️⃣ Редовна вноска:* `{cur}{loan.regular_payment:,.2f}`",
                f"  • *2️⃣ Предсрочно погасяване на главница:* `{cur}{loan.early_principal_payment:,.2f}` (+ такса `{cur}{fee_eur:.2f}`)",
                f"  • *Общо плащане по кредита за месеца:* `{cur}{monthly_loan_total:,.2f}` _(гарантирана доходност от {loan.apr_percent:.2f}%!)_",
                f"  • *Очаквана остатъчна главница след плащането:* `~{cur}{new_principal_est:,.2f}`",
            ])
            if loan.notes:
                lines.append(f"  • *План:* _{loan.notes}_")
    else:
        lines.append("  • ✅ Нямаш въведени активни кредити! _(Ако имаш кредит с висока лихва над 10–12% ГПР, можеш да го добавиш с_ `/set_loan`_)._")

    # Section 4: 📈 Динамичен план за инвестиция (изчислен на живо от пайовете и Watchlist-а!)
    lines.extend(["", "📈 *4. ДИНАМИЧЕН ПЛАН ЗА ИНВЕСТИЦИЯ ДНЕС*"])
    dynamic_lines = build_dynamic_investment_plan_lines(session, user, latest_snap)
    for dl in dynamic_lines:
        lines.append(f"  • {dl}")

    # Section 5: 🛡️ Проверка на буфера и бюджета
    lines.extend(["", "🛡️ *5. ПРОВЕРКА НА ЛИКВИДНИЯ БУФЕР И БЮДЖЕТА*"])
    if user.monthly_income <= 0 and user.monthly_invest_budget <= 0:
        lines.append("  • _Бюджетът ти е празен. Въведи своите данни с_ `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР` _за автоматичен разчет._")
    else:
        buf_ok = user.emergency_buffer_current >= user.emergency_buffer_target
        buf_icon = "✅" if buf_ok else f"⚠️ Липсват `{cur}{user.emergency_buffer_target - user.emergency_buffer_current:,.2f}` — изгради го приоритетно!"
        remaining_for_living = round(user.monthly_income - total_loan_outflow - user.monthly_invest_budget, 2)
        lines.extend([
            f"  • *Неприкосновен ликвиден буфер:* `{cur}{user.emergency_buffer_current:,.2f}` / цел `{cur}{user.emergency_buffer_target:,.2f}` {buf_icon}",
            f"  • *Месечен нетен доход:* `{cur}{user.monthly_income:,.2f}`",
            f"  • *След кредит (`{cur}{total_loan_outflow:,.2f}`) и бюджет за буфер/инвестиции (`{cur}{user.monthly_invest_budget:,.2f}`):* Остават `{cur}{remaining_for_living:,.2f}` за текущи разходи.",
        ])

    return "\n".join(lines)
