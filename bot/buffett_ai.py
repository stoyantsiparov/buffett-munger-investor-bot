"""Bilingual (EN default / BG switchable) Warren Buffett & Charlie Munger Dynamic Investment Agent."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
import re
import time
from pathlib import Path

logger = logging.getLogger("buffett_bot")
from sqlalchemy.orm import Session
from bot.crypto import decrypt_secret
from bot.i18n import format_ordinal, get_lang, localize_moat
from bot.market_service import fetch_deep_historical_metrics, fetch_stock_fundamentals
from bot.models import PortfolioSnapshot, User, UserLoan, WatchlistItem
from bot.news_service import fetch_live_ticker_news, format_news_context_for_ai
from bot.playbooks import (
    THEMATIC_PLAYBOOKS,
    build_stock_playbook_prompt_context,
    classify_industry_playbook,
    evaluate_industry_playbook_checks,
    select_playbooks_for_question,
)
from bot.t212_service import SEMICONDUCTOR_SHORT_TICKERS, parse_t212_snapshot

# Per-user multi-turn conversational memory: {telegram_id: [(timestamp, question, answer_snippet), ...]}
_USER_CHAT_HISTORY: dict[int, list[tuple[float, str, str]]] = {}
MAX_CHAT_HISTORY_TURNS = 5
CHAT_HISTORY_TTL_SEC = 12 * 3600


def clear_user_chat_history(telegram_id: int) -> int:
    """Clear stored conversational history for a user and return number of cleared turns."""
    turns = _USER_CHAT_HISTORY.pop(int(telegram_id or 0), [])
    return len(turns)


def _get_recent_chat_turns(telegram_id: int) -> list[tuple[str, str]]:
    now = time.time()
    raw = _USER_CHAT_HISTORY.get(int(telegram_id or 0), [])
    valid = [(ts, q, a) for ts, q, a in raw if (now - ts) <= CHAT_HISTORY_TTL_SEC]
    if len(valid) != len(raw):
        _USER_CHAT_HISTORY[int(telegram_id or 0)] = valid
    return [(q, a) for _, q, a in valid[-MAX_CHAT_HISTORY_TURNS:]]


def _append_chat_turn(telegram_id: int, question: str, answer: str) -> None:
    if not telegram_id:
        return
    now = time.time()
    valid = [
        (ts, q, a)
        for ts, q, a in _USER_CHAT_HISTORY.get(int(telegram_id), [])
        if (now - ts) <= CHAT_HISTORY_TTL_SEC
    ]
    clean_a = " ".join((answer or "").split())[:650]
    valid.append((now, (question or "").strip()[:300], clean_a))
    _USER_CHAT_HISTORY[int(telegram_id)] = valid[-MAX_CHAT_HISTORY_TURNS:]

BROAD_EU_ETFS = {"VWCE", "IWDA", "SXR8", "CSPX", "EUNL", "VUAA", "SPYI", "WEBN"}
SKIP_SINGLE_STOCK_VALUATION_ETFS = BROAD_EU_ETFS | {"SEC0", "XDWU", "ESIF", "BGX", "EXX5", "QDVE", "EQQQ"}

CORE_BUFFETT_SYSTEM_PROMPT_EN = """You are a personal financial and investment analyst following the complete Warren Buffett & Charlie Munger value investing system.
Always reply in clear, concise English — specific, grounded in exact numbers, with zero fluff.

1. Investment Framework (Buffett & Munger):
- 8-Point Quick Filter: Circle of competence, 10+ yr durability, economic moat (type + direction: widening / stable / narrowing), pricing power (+5-10%), cash conversion (OCF/Net Income > 90%), debt safety (Net Debt/EBITDA < 2.0x; -30% revenue stress test), management integrity ($1 retained earnings test; auto-veto on fraud), and Margin of Safety (MoS 20-50%).
- Core Metrics: Owner Earnings (OCF - Maintenance CapEx, separating Growth CapEx), 10-yr ROIC > 15%. For banks/insurers: ROE > 15%, ROA > 1.2%, CET1 > 10%, Combined Ratio < 100%, Float.
- 4 Sell Criteria: (1) Price massively exceeds Intrinsic Value; (2) Moat permanently eroded; (3) Management dishonesty / fraud (immediate sell); (4) Clearly superior opportunity with a much larger MoS. Never sell solely due to short-term Mr. Market volatility.

2. Personal Capital Management Rules:
- Munger's Inversion: Eliminate guaranteed high-interest debt (>10-12% APR) before deploying all surplus into risk assets.
- Liquidity Buffer: Maintain an emergency cash buffer (target €500) so you are never forced to sell great businesses. If below target, prioritize building it.
- Sector Concentration Cap: Maximum 20-25% in any single risky sector (track total semiconductor exposure). If chips > 20%, do NOT recommend buying more semiconductors.
- Tax awareness: Broad EU-regulated UCITS ETFs (like VWCE) enjoy favorable long-term tax treatment in many EU jurisdictions (0% CGT in Bulgaria) vs. US single stocks.
- Work ONLY from the real portfolio, Watchlist, and live market/Google Search data provided. Never fabricate prices or holdings.
"""

CORE_BUFFETT_SYSTEM_PROMPT_BG = """Ти си личен финансов и инвестиционен агент по пълната система на Уорън Бъфет и Чарли Мънгър.
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
    return format_ordinal(day, "bg")


def _is_owner_or_whitelisted(user: User | None) -> bool:
    """Check if user is the bot owner or explicitly listed in GEMINI_ALLOWED_IDS."""
    if not user:
        return False
    owner_tg_id = int(os.environ.get("OWNER_TELEGRAM_ID") or 0)
    if (owner_tg_id and user.telegram_id == owner_tg_id) or user.id == 1:
        return True
    allowed_raw = os.environ.get("GEMINI_ALLOWED_IDS", "").strip()
    if allowed_raw:
        allowed = {x.strip() for x in allowed_raw.split(",") if x.strip()}
        if str(user.telegram_id) in allowed:
            return True
    return False


def _get_gemini_api_key(user: User | None = None, session: Session | None = None) -> str:
    """Resolve Gemini API key with STRICT tenant isolation (owner's key is NEVER shared with public users).

    1) User's own encrypted key saved via /set_gemini_key.
    2) Server GEMINI_API_KEY env var ONLY for the bot owner (or GEMINI_ALLOWED_IDS whitelist).
    """
    if user and getattr(user, "gemini_api_key_enc", None):
        try:
            k = decrypt_secret(user.gemini_api_key_enc).strip()
            if k and k != "YOUR_GEMINI_API_KEY_HERE":
                return k
        except Exception:
            pass

    if not _is_owner_or_whitelisted(user):
        return ""

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
    return ""


def load_buffett_system_instruction(include_references: bool = False, lang: str = "en") -> str:
    """Return the token-optimized, cloud-safe Buffett & Munger system prompt in 'en' or 'bg'."""
    return CORE_BUFFETT_SYSTEM_PROMPT_BG if lang == "bg" else CORE_BUFFETT_SYSTEM_PROMPT_EN


def _format_friendly_ai_error(err: Exception, is_bg: bool) -> str:
    """Turn verbose Gemini SDK/HTTP errors into a crisp 1-line user-friendly notice."""
    raw = str(err)
    low = raw.lower()
    if "503" in low or "unavailable" in low or "high demand" in low or "overloaded" in low:
        return (
            "Gemini AI сървърите са временно претоварени (503) — опитай след минута."
            if is_bg
            else "Gemini AI is experiencing a temporary spike in demand (503) — try again in a minute."
        )
    if "429" in low or "resource_exhausted" in low or "quota" in low:
        return (
            "Временният безплатен лимит на Gemini е достигнат (429) — опитай след минута."
            if is_bg
            else "Gemini free-tier rate limit reached (429) — please try again in a minute."
        )
    if "timeout" in low or "timed out" in low:
        return (
            "Gemini AI не отговори навреме (timeout)."
            if is_bg
            else "Gemini AI request timed out."
        )
    clean = " ".join(raw.split())
    return clean[:140] + ("…" if len(clean) > 140 else "")


def _call_gemini_with_fallback(api_key: str, system_instruction: str, prompt: str) -> str:
    """Call Gemini Interactions API with fast-fail (no 160s 503 backoff) & Free-Tier model cascade."""
    from google import genai
    from google.genai import types as genai_types

    # Disable internal multi-minute 503 retry loops (attempts=1 -> max_retries=0)
    # so overloaded models (503/429) fail over in <1s, while healthy models get
    # enough time (25-30s) to finish generating without premature client timeouts.
    http_opts_kwargs: dict = {"timeout": 30000}
    retry_cls = getattr(genai_types, "HttpRetryOptions", None)
    if retry_cls is not None:
        http_opts_kwargs["retry_options"] = retry_cls(attempts=1)
    client = genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(**http_opts_kwargs),
    )
    use_search = os.environ.get("GEMINI_ENABLE_SEARCH", "").strip() == "1"
    # Tuple: (model_name, enable_search, thinking_level_or_none, per_attempt_timeout_sec)
    cascade: list[tuple[str, bool, str | None, float]] = []
    if use_search:
        cascade.append(("gemini-4-hydrogen", True, "low", 30.0))
    cascade.extend([
        ("gemini-3.8-flash", False, "minimal", 30.0),
        ("gemma-4-26b-a4b-it", False, None, 25.0),
        ("gemma-4-31b-it", False, None, 25.0),
        ("gemini-3.5-flash-lite", False, "minimal", 20.0),
    ])

    deadline = time.monotonic() + 55.0
    last_err: Exception | None = None
    for model_name, enable_search, thinking_lvl, attempt_timeout in cascade:
        remaining = deadline - time.monotonic()
        if remaining < 5.0:
            break
        try:
            gen_cfg: dict = {"max_output_tokens": 1200}
            if thinking_lvl:
                gen_cfg["thinking_level"] = thinking_lvl
            kwargs = {
                "model": model_name,
                "system_instruction": system_instruction,
                "input": prompt,
                "generation_config": gen_cfg,
                "timeout": min(attempt_timeout, remaining),
            }
            if enable_search:
                kwargs["tools"] = [{"type": "google_search"}]
            interaction = client.interactions.create(**kwargs)
            txt = (getattr(interaction, "output_text", None) or "").strip()
            if not txt and getattr(interaction, "outputs", None):
                txt = "\n".join(
                    (getattr(o, "text", None) or "").strip()
                    for o in interaction.outputs
                    if getattr(o, "text", None)
                ).strip()
            if txt:
                logger.info("Gemini AI succeeded with model=%s", model_name)
                return txt
        except Exception as e:
            logger.warning("Gemini AI model=%s failed: %s", model_name, str(e)[:180])
            last_err = e
            continue

    if last_err:
        raise last_err
    return ""


def _w_get(watch_item: WatchlistItem | dict | None, key: str, default=None):
    if watch_item is None:
        return default
    if isinstance(watch_item, dict):
        return watch_item.get(key, default)
    return getattr(watch_item, key, default)


def compute_quantitative_buffett_scorecard(
    metrics: dict,
    watch_item: WatchlistItem | dict | None = None,
    lang: str = "en",
) -> dict:
    """Compute a deterministic Buffett & Munger quantitative scorecard and intrinsic value range."""
    is_bg = lang == "bg"
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
    is_bank_or_insurer = sector == "Financial Services" and any(k in industry for k in ("Bank", "Insurance"))

    w_iv_low = _w_get(watch_item, "intrinsic_value_low")
    w_iv_high = _w_get(watch_item, "intrinsic_value_high")
    if watch_item and w_iv_low and w_iv_high:
        iv_low = float(w_iv_low)
        iv_high = float(w_iv_high)
        target_buy = float(_w_get(watch_item, "target_buy_price") or iv_low * 0.85)
        mos_req = float(_w_get(watch_item, "margin_of_safety_pct") or 25.0)
    elif is_bank_or_insurer and price > 0:
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
    label = "ROE" if is_bank_or_insurer else "ROIC/ROE"
    if eff_roic is not None and eff_roic >= 15.0:
        checks.append(
            f"✅ Висока възвръщаемост на капитала ({label}: `{eff_roic:.1f}%` >= 15%)"
            if is_bg
            else f"✅ High Return on Capital ({label}: `{eff_roic:.1f}%` >= 15%)"
        )
        passed += 1
    elif eff_roic is not None:
        checks.append(
            f"⚠️ Възвръщаемост на капитала под целта на Бъфет (`{eff_roic:.1f}%` < 15%)"
            if is_bg
            else f"⚠️ Return on Capital below Buffett threshold (`{eff_roic:.1f}%` < 15%)"
        )
    else:
        checks.append("ℹ️ ROIC/ROE: Няма достатъчно TTM данни" if is_bg else "ℹ️ ROIC/ROE: Insufficient TTM data")

    if is_bank_or_insurer and roa is not None:
        if roa >= 1.2:
            checks.append(
                f"✅ Силна банкова доходност на активите (ROA: `{roa:.2f}%` >= 1.2%)"
                if is_bg
                else f"✅ Strong Bank Return on Assets (ROA: `{roa:.2f}%` >= 1.2%)"
            )
            passed += 1
        else:
            checks.append(
                f"⚠️ По-ниска банкова доходност на активите (ROA: `{roa:.2f}%` < 1.2%)"
                if is_bg
                else f"⚠️ Lower Bank Return on Assets (ROA: `{roa:.2f}%` < 1.2%)"
            )
    elif op_margin is not None and op_margin >= 18.0:
        checks.append(
            f"✅ Силна ценова сила и оперативен марж (`{op_margin:.1f}%` >= 18%)"
            if is_bg
            else f"✅ Strong Pricing Power & Operating Margin (`{op_margin:.1f}%` >= 18%)"
        )
        passed += 1
    elif op_margin is not None:
        checks.append(
            f"⚠️ По-нисък оперативен марж (`{op_margin:.1f}%`)"
            if is_bg
            else f"⚠️ Modest Operating Margin (`{op_margin:.1f}%`)"
        )

    if not is_bank_or_insurer:
        if cash_conv is not None and cash_conv >= 90.0:
            checks.append(
                f"✅ Отлично качество на печалбата (OCF/Net Income: `{cash_conv:.0f}%` > 90%)"
                if is_bg
                else f"✅ Strong Earnings Quality (OCF/Net Income: `{cash_conv:.0f}%` > 90%)"
            )
            passed += 1
        elif cash_conv is not None:
            checks.append(
                f"⚠️ По-слаба кешова конверсия (OCF/Net Income: `{cash_conv:.0f}%` < 90%)"
                if is_bg
                else f"⚠️ Lower Cash Conversion (OCF/Net Income: `{cash_conv:.0f}%` < 90%)"
            )

        if metrics.get("net_debt", 0) <= 0:
            checks.append(
                "✅ Крепостен баланс: Повече кеш, отколкото дълг (Net Cash)"
                if is_bg
                else "✅ Fortress Balance Sheet: More Cash than Debt (Net Cash)"
            )
            passed += 1
        elif nd_ebitda is not None and nd_ebitda < 2.0:
            checks.append(
                f"✅ Консервативен дълг (Net Debt/EBITDA: `{nd_ebitda:.2f}x` < 2.0x)"
                if is_bg
                else f"✅ Conservative Debt (Net Debt/EBITDA: `{nd_ebitda:.2f}x` < 2.0x)"
            )
            passed += 1
        elif nd_ebitda is not None:
            checks.append(
                f"⚠️ По-висок дълг (Net Debt/EBITDA: `{nd_ebitda:.2f}x` >= 2.0x)"
                if is_bg
                else f"⚠️ Elevated Leverage (Net Debt/EBITDA: `{nd_ebitda:.2f}x` >= 2.0x)"
            )

    if price > 0 and price <= target_buy:
        checks.append(
            f"🟢 В ЗОНАТА ЗА ПОКУПКА! Текуща цена `{price:.2f}` <= Target Buy `{target_buy:.2f}`"
            if is_bg
            else f"🟢 IN BUY ZONE! Current price `{price:.2f}` <= Target Buy `{target_buy:.2f}`"
        )
        passed += 1
        verdict = (
            "🟢 КУПИ (В зоната за покупка с Марж на безопасност)"
            if is_bg
            else "🟢 BUY (In Buy Zone with Margin of Safety)"
        )
    elif price > 0 and price <= iv_high:
        checks.append(
            f"🟡 Справедлива цена (`{price:.2f}`), но изчакай Target Buy `<= {target_buy:.2f}` за пълен марж на безопасност"
            if is_bg
            else f"🟡 Fairly Valued (`{price:.2f}`), wait for Target Buy `<= {target_buy:.2f}` for full Margin of Safety"
        )
        verdict = (
            "🟡 ЗАДРЪЖ / ИЗЧАКАЙ КОРЕКЦИЯ ДО TARGET BUY"
            if is_bg
            else "🟡 HOLD / WAIT FOR PULLBACK TO TARGET BUY"
        )
    else:
        checks.append(
            f"🔴 Надценена спрямо консервативната Бъфет оценка (`{price:.2f}` > `{iv_high:.2f}`)"
            if is_bg
            else f"🔴 Overvalued vs. conservative Buffett valuation (`{price:.2f}` > `{iv_high:.2f}`)"
        )
        verdict = (
            "⏳ ИЗЧАКАЙ КОРЕКЦИЯ (Твърде скъпа в момента)"
            if is_bg
            else "⏳ WAIT FOR CORRECTION (Too expensive right now)"
        )

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


def rank_portfolio_buy_candidates(parsed_snap: dict | None, lang: str = "en") -> dict:
    """Dynamically rank the best positions to buy RIGHT NOW inside the user's actual Trading 212 pies."""
    is_bg = lang == "bg"
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
                        "reason": (
                            f"полупроводници са {semi_pct:.1f}% (>20% таван)"
                            if is_bg
                            else f"semiconductors at {semi_pct:.1f}% (>20% cap)"
                        ),
                        "cur_pct": cur_pct,
                        "exp_pct": exp_pct,
                    }
                )
                continue

            is_broad_etf = ticker in BROAD_EU_ETFS
            score = (underweight * 3.5) + max(-15.0, min(25.0, -ppl_pct * 0.9))
            if is_broad_etf:
                score += 18.0
            if cur_pct > exp_pct + 2.0 and not is_broad_etf:
                score -= 20.0

            reason_parts = []
            if is_broad_etf:
                reason_parts.append(
                    "0% данък в ЕС + глобална диверсификация" if is_bg else "EU UCITS ETF + global diversification"
                )
            if underweight > 0.3:
                reason_parts.append(f"под целта с -{underweight:.1f}%" if is_bg else f"underweight by -{underweight:.1f}%")
            if ppl_pct < 0:
                reason_parts.append(
                    f"на отстъпка ({ppl_pct:.1f}% под ср. цена)" if is_bg else f"pullback ({ppl_pct:.1f}% below avg cost)"
                )
            elif ppl_pct < 5.0 and not is_broad_etf:
                reason_parts.append(
                    f"близо до ср. цена (+{ppl_pct:.1f}%)" if is_bg else f"near avg cost (+{ppl_pct:.1f}%)"
                )
            if not reason_parts:
                reason_parts.append(
                    f"стабилна позиция (дял {cur_pct:.1f}%/{exp_pct:.0f}%)"
                    if is_bg
                    else f"steady holding (wt {cur_pct:.1f}%/{exp_pct:.0f}%)"
                )

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
    lang: str | None = None,
) -> list[dict]:
    """Scan all individual stocks in the user's OWN Trading 212 portfolio for Buffett undervaluation."""
    eff_lang = lang or get_lang(user)
    is_bg = eff_lang == "bg"

    if not latest_snap or not latest_snap.raw_json:
        return []
    try:
        raw_data = json.loads(latest_snap.raw_json)
        parsed = parse_t212_snapshot(raw_data)
    except Exception:
        return []

    semi_pct = float(parsed.get("semiconductor_pct") or 0.0)
    semi_blocked = semi_pct >= 20.0

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
            score = compute_quantitative_buffett_scorecard(metrics, w_item, lang=eff_lang)
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
            stored_moat = w_item.get("moat_summary") if w_item else None
            moat = localize_moat(ticker, stored_moat, eff_lang)
            if moat in ("Wide Economic Moat", "Широк конкурентен ров"):
                roic_s = metrics.get("roic_est_pct") or "N/A"
                om_s = metrics.get("operating_margin_pct") or "N/A"
                moat = f"ROIC: {roic_s}% | Опер. марж: {om_s}%" if is_bg else f"ROIC: {roic_s}% | Op. Margin: {om_s}%"

            info = held_map[ticker]
            invested = max(1.0, info["value"] - info["ppl"])
            ppl_pct = round((info["ppl"] / invested) * 100.0, 1)
            underweight = round(info["exp_pct"] - info["cur_pct"], 1)

            rank_score = (discount_high_pct * 1.6) + (underweight * 3.0)
            if in_buy_zone:
                rank_score += 30.0
            if semi_blocked and is_chip:
                rank_score -= 60.0

            sector_warn = ""
            if semi_blocked and is_chip:
                sector_warn = (
                    f"⚠️ Полупроводници са {semi_pct:.1f}% (>20%)"
                    if is_bg
                    else f"⚠️ Semiconductors at {semi_pct:.1f}% (>20% cap)"
                )

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
                "sector_warn": sector_warn,
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
    lang: str | None = None,
) -> list[dict]:
    """Dynamically rank the Top 3 best investment opportunities from the shared Buffett Watchlist right now."""
    eff_lang = lang or get_lang(user)
    is_bg = eff_lang == "bg"

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

        gap_to_buy_pct = round(((w.target_buy_price - w.last_price) / w.target_buy_price) * 100.0, 1)
        mos_mid_pct = round(((iv_mid - w.last_price) / iv_mid) * 100.0, 1) if iv_mid > 0 else 0.0
        in_buy_zone = w.last_price <= w.target_buy_price
        is_chip = w.ticker.upper() in SEMICONDUCTOR_SHORT_TICKERS

        score = (gap_to_buy_pct * 2.2) + (mos_mid_pct * 1.3)
        if in_buy_zone:
            score += 35.0
        if w.roic_pct and w.roic_pct >= 15.0:
            score += min(12.0, (w.roic_pct - 10.0) * 0.4)
        if semi_blocked and is_chip:
            score -= 60.0

        sym = "$" if w.currency == "USD" else ("p " if w.currency == "GBX" else "€")
        if in_buy_zone:
            status_tag = (
                f"🟢 В ЗОНА ЗА ПОКУПКА (+{mos_mid_pct:.0f}% MoS)"
                if is_bg
                else f"🟢 IN BUY ZONE (+{mos_mid_pct:.0f}% MoS)"
            )
        elif gap_to_buy_pct >= -12.0:
            status_tag = (
                f"🟡 На {abs(gap_to_buy_pct):.1f}% от Target Buy (MoS: {mos_mid_pct:+.0f}%)"
                if is_bg
                else f"🟡 {abs(gap_to_buy_pct):.1f}% above Target Buy (MoS: {mos_mid_pct:+.0f}%)"
            )
        else:
            status_tag = (
                f"⏳ Чакаме корекция ({abs(gap_to_buy_pct):.0f}% над Target Buy)"
                if is_bg
                else f"⏳ Wait for pullback ({abs(gap_to_buy_pct):.0f}% above Target Buy)"
            )

        sector_warn = (
            ("⚠️ Чипове >20% в портфейла" if is_bg else "⚠️ Chips >20% in portfolio")
            if (semi_blocked and is_chip)
            else ""
        )
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
                "moat_summary": localize_moat(w.ticker, w.moat_summary, eff_lang),
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
    lang: str | None = None,
) -> dict:
    """Head-to-head Top 3 Buffett ranking comparing BOTH the shared Watchlist AND the user's Trading 212 portfolio."""
    eff_lang = lang or get_lang(user)
    is_bg = eff_lang == "bg"

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
    candidates: dict[str, dict] = {}

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
            status_tag = (
                f"🟢 В ЗОНА ЗА ПОКУПКА (+{mos_mid_pct:.0f}% MoS)"
                if is_bg
                else f"🟢 IN BUY ZONE (+{mos_mid_pct:.0f}% MoS)"
            )
        elif gap_to_buy_pct >= -12.0:
            status_tag = (
                f"🟡 На {abs(gap_to_buy_pct):.1f}% от Target Buy (MoS: {mos_mid_pct:+.0f}%)"
                if is_bg
                else f"🟡 {abs(gap_to_buy_pct):.1f}% above Target Buy (MoS: {mos_mid_pct:+.0f}%)"
            )
        else:
            status_tag = (
                f"⏳ {abs(gap_to_buy_pct):.0f}% над Target Buy"
                if is_bg
                else f"⏳ {abs(gap_to_buy_pct):.0f}% above Target Buy"
            )

        candidates[t] = {
            "ticker": t,
            "company_name": w.company_name or t,
            "source": "watchlist",
            "source_badge": "🎯 Watchlist (нова)" if is_bg else "🎯 Watchlist (new)",
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
            "sector_warn": (
                (f"Чипове {semi_pct:.1f}% >20%" if is_bg else f"Chips {semi_pct:.1f}% >20%")
                if (semi_blocked and is_chip)
                else ""
            ),
            "status_tag": status_tag,
            "roic_pct": w.roic_pct,
            "moat_summary": localize_moat(t, w.moat_summary, eff_lang),
            "cur_pct": None,
            "exp_pct": None,
            "pie_name": None,
            "score": round(score, 2),
        }

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
        if underweight > 0:
            score += min(10.0, underweight * 2.5)
        elif cur_pct > exp_pct + 1.5 and exp_pct > 0:
            score -= min(15.0, (cur_pct - exp_pct) * 4.0)
        if semi_blocked and is_chip:
            score -= 80.0

        if in_buy_zone:
            status_tag = (
                f"🟢 В ЗОНА ЗА ПОКУПКА (+{mos_mid_pct:.0f}% MoS)"
                if is_bg
                else f"🟢 IN BUY ZONE (+{mos_mid_pct:.0f}% MoS)"
            )
        elif price <= iv_high:
            status_tag = (
                f"🟡 Подценена (+{p['discount_pct']:.0f}% до IV, дял {cur_pct:.1f}%/{exp_pct:.0f}%)"
                if is_bg
                else f"🟡 Undervalued (+{p['discount_pct']:.0f}% to IV, wt {cur_pct:.1f}%/{exp_pct:.0f}%)"
            )
        else:
            status_tag = (
                f"⏳ {abs(gap_to_buy_pct):.0f}% над Target Buy"
                if is_bg
                else f"⏳ {abs(gap_to_buy_pct):.0f}% above Target Buy"
            )

        already_in_wl = t in candidates
        candidates[t] = {
            "ticker": t,
            "company_name": p["company_name"],
            "source": "both" if already_in_wl else "portfolio",
            "source_badge": (
                f"💼 Твой портфейл (подсили {cur_pct:.1f}%/{exp_pct:.0f}%)"
                if is_bg
                else f"💼 Your Portfolio (reinforce {cur_pct:.1f}%/{exp_pct:.0f}%)"
            ),
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
            "sector_warn": (
                (f"Чипове {semi_pct:.1f}% >20%" if is_bg else f"Chips {semi_pct:.1f}% >20%")
                if (semi_blocked and is_chip)
                else ""
            ),
            "status_tag": status_tag,
            "roic_pct": roic_val,
            "moat_summary": localize_moat(t, p.get("moat_summary"), eff_lang),
            "cur_pct": cur_pct,
            "exp_pct": exp_pct,
            "pie_name": p["pie_name"],
            "score": round(score, 2),
        }

    ranked_all = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    eligible_for_top3 = [x for x in ranked_all if not (semi_blocked and x["is_chip"])]
    top3 = (eligible_for_top3 or ranked_all)[:3]

    has_port = [x for x in top3 if x["source"] in ("portfolio", "both")]
    has_wl = [x for x in top3 if x["source"] == "watchlist"]
    insight = ""
    if has_port and has_wl:
        p1 = has_port[0]
        w1 = has_wl[0]
        if top3[0]["source"] in ("portfolio", "both"):
            insight = (
                f"Днес {p1['ticker']} (вече в портфейла ти) води класирането — можеш да я подсилиш на отстъпка "
                f"без да раздробяваш портфейла, или да добавиш нова позиция в {w1['ticker']} от Watchlist."
                if is_bg
                else f"Today {p1['ticker']} (already in your portfolio) leads the ranking — reinforce it at a discount "
                f"without diluting focus, or open a new position in {w1['ticker']} from the Watchlist."
            )
        else:
            insight = (
                f"Днес {w1['ticker']} (нова от Watchlist) предлага най-висок марж на безопасност, "
                f"а {p1['ticker']} е най-силната подценена позиция за подсилване вътре в портфейла ти!"
                if is_bg
                else f"Today {w1['ticker']} (new from Watchlist) offers the highest Margin of Safety, "
                f"while {p1['ticker']} is the strongest undervalued holding to reinforce inside your portfolio!"
            )
    elif has_port:
        p1 = has_port[0]
        insight = (
            f"Днес най-добрите сделки са вътре в твоя портфейл (води {p1['ticker']}) — "
            "по Бъфет е по-умно да подсилиш чудесен бизнес, който вече притежаваш на отстъпка, вместо да купуваш по-скъпа нова компания!"
            if is_bg
            else f"Today's best bargains are inside your own portfolio (led by {p1['ticker']}) — "
            "per Buffett, reinforcing a great business you already own at a discount beats adding a pricier new stock!"
        )
    elif has_wl:
        w1 = has_wl[0]
        insight = (
            f"Днес {w1['ticker']} от общия Watchlist предлага по-добра цена и марж на безопасност от акциите в портфейла."
            if is_bg
            else f"Today {w1['ticker']} from the shared Watchlist offers a stronger Margin of Safety than existing holdings."
        )

    return {"top3": top3, "all_ranked": ranked_all, "comparison_insight": insight}


def build_dynamic_investment_plan_lines(
    session: Session,
    user: User,
    latest_snap: PortfolioSnapshot | None = None,
) -> list[str]:
    """Build a 100% dynamic, non-hardcoded monthly investment plan in 'en' or 'bg'."""
    lang = get_lang(user)
    is_bg = lang == "bg"
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

    buf_missing = max(0.0, round(user.emergency_buffer_target - user.emergency_buffer_current, 2))
    inv_budget = float(user.monthly_invest_budget or 0.0)
    investable_now = inv_budget

    if buf_missing > 0 and inv_budget > 0:
        suggested_to_buffer = round(min(buf_missing, max(50.0, inv_budget * 0.45)), 2)
        investable_now = round(max(0.0, inv_budget - suggested_to_buffer), 2)
        if is_bg:
            lines.append(
                f"🛡️ *ПРИОРИТЕТ — Ликвиден буфер:* Текущо `{cur}{user.emergency_buffer_current:,.2f}` / цел `{cur}{user.emergency_buffer_target:,.2f}` "
                f"_(липсват {cur}{buf_missing:,.2f})_. По правилото на Бъфет задели първо `{cur}{suggested_to_buffer:,.2f}` към буфера, "
                f"а останалите `{cur}{investable_now:,.2f}` инвестирай в Trading 212!"
            )
        else:
            lines.append(
                f"🛡️ *PRIORITY — Liquidity Buffer:* Current `{cur}{user.emergency_buffer_current:,.2f}` / target `{cur}{user.emergency_buffer_target:,.2f}` "
                f"_(missing {cur}{buf_missing:,.2f})_. Per Buffett's liquidity rule, allocate `{cur}{suggested_to_buffer:,.2f}` to your cash buffer first, "
                f"and invest the remaining `{cur}{investable_now:,.2f}` in Trading 212!"
            )
    elif buf_missing > 0:
        lines.append(
            f"🛡️ *Ликвиден буфер:* Текущо `{cur}{user.emergency_buffer_current:,.2f}` / цел `{cur}{user.emergency_buffer_target:,.2f}` — "
            "изгради си неприкосновен буфер преди рискови покупки, за да не си принуден никога да продаваш добри акции."
            if is_bg
            else f"🛡️ *Liquidity Buffer:* Current `{cur}{user.emergency_buffer_current:,.2f}` / target `{cur}{user.emergency_buffer_target:,.2f}` — "
            "build your emergency cash reserve first so you are never forced to sell great businesses."
        )

    if high_apr_loans:
        for l in high_apr_loans:
            fee = round(l.early_principal_payment * (l.early_fee_percent / 100.0), 2) if l.early_principal_payment > 0 else 0.0
            pmt = round(l.regular_payment + l.early_principal_payment + fee, 2)
            principal_reduction_mo = max(1.0, l.early_principal_payment + l.regular_payment * 0.84)
            months_left = max(1, math.ceil(l.principal_balance / principal_reduction_mo))
            if is_bg:
                lines.append(
                    f"💳 *Инверсия на Мънгър ({l.lender_name} — {l.apr_percent:.2f}% ГПР):* "
                    f"`{cur}{pmt:,.2f}/мес.` → остават ~`{months_left}` мес. до `€0` дълг, след което тези `{cur}{pmt:,.2f}/мес.` се освобождават за инвестиции!"
                )
            else:
                lines.append(
                    f"💳 *Munger's Inversion ({l.lender_name} — {l.apr_percent:.2f}% APR):* "
                    f"`{cur}{pmt:,.2f}/mo` → ~`{months_left}` mos left to debt-free, unlocking `{cur}{pmt:,.2f}/mo` for investing!"
                )

    if inv_budget <= 0:
        lines.append(
            "💡 _Настрой месечния си бюджет с_ `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР`_, за да изчислява ботът точните суми._"
            if is_bg
            else "💡 _Configure your monthly budget via_ `/set_budget INCOME INVEST_BUDGET BUFFER` _to unlock exact allocations._"
        )
        return lines

    ranked_port = rank_portfolio_buy_candidates(parsed_snap, lang=lang)
    if ranked_port["pies"]:
        if ranked_port["semi_blocked"]:
            lines.append(
                f"⚠️ *Сектор Полупроводници е {ranked_port['semi_pct']:.2f}% (>20% таван):* "
                "Ботът автоматично изключи чиповете от днешните покупки."
                if is_bg
                else f"⚠️ *Semiconductors at {ranked_port['semi_pct']:.2f}% (>20% cap):* "
                "Chips are automatically excluded from today's buy plan."
            )
        total_pie_val = sum(p["pie_value"] for p in ranked_port["pies"]) or 1.0
        for p in ranked_port["pies"]:
            weight = p["pie_value"] / total_pie_val if len(ranked_port["pies"]) > 1 else 1.0
            pie_alloc = round(investable_now * weight, 2)
            top_tickers = ", ".join(f"`{c['ticker']}` ({c['reason']})" for c in p["top_picks"][:3])
            blocked_tickers = ", ".join(f"`{b['ticker']}`" for b in p["blocked"])
            if is_bg:
                blocked_note = f" | *€0 за:* {blocked_tickers}" if blocked_tickers else ""
                lines.append(
                    f"🥧 *{p['pie_name']}:* `~{cur}{pie_alloc:,.2f}` → Топ избор днес: {top_tickers or 'Няма подходящи'}{blocked_note}"
                )
            else:
                blocked_note = f" | *€0 for:* {blocked_tickers}" if blocked_tickers else ""
                lines.append(
                    f"🥧 *{p['pie_name']}:* `~{cur}{pie_alloc:,.2f}` → Top picks today: {top_tickers or 'None eligible'}{blocked_note}"
                )
    else:
        lines.append(
            f"📈 *Месечна инвестиция:* `{cur}{investable_now:,.2f}` — насочи към широк UCITS ETF (`VWCE`, 0% данък в ЕС) "
            "и най-подценените компании от Топ 3 на общия Watchlist."
            if is_bg
            else f"📈 *Monthly Investment:* `{cur}{investable_now:,.2f}` — allocate to a broad UCITS ETF (`VWCE`) "
            "and the most undervalued Top-3 picks from the shared Watchlist."
        )

    top3_wl = rank_top3_watchlist_opportunities(session, user, latest_snap, lang=lang)
    if top3_wl:
        wl_str = " | ".join(
            f"*#{i+1} {x['ticker']}* (`{x['currency_sym']}{x['last_price']:.2f}`, {x['status_tag']})"
            for i, x in enumerate(top3_wl)
        )
        lines.append(
            f"🌟 *Топ 3 идеи от Watchlist днес:* {wl_str}"
            if is_bg
            else f"🌟 *Top 3 Watchlist Ideas Today:* {wl_str}"
        )

    return lines


def format_deep_audit_lines(deep: dict | None, sym: str, lang: str = "en") -> list[str]:
    """Render the v1.1.0/v1.3.0 Multi-Year Historical Audit & Red-Flag Detector block in EN or BG."""
    if not deep or not deep.get("hist_years"):
        return []
    is_bg = lang == "bg"
    yrs = deep["hist_years"]
    src_badge = f" [{deep['data_source']}]" if deep.get("data_source") else ""
    lines = [
        "",
        f"🔬 *2. Дълбок {yrs}-годишен исторически одит & Детектор за капани{src_badge}:*"
        if is_bg
        else f"🔬 *2. Deep {yrs}-Year Historical Audit & Red-Flag Detector{src_badge}:*",
    ]

    if deep.get("is_bank_or_insurer"):
        if deep.get("avg_roe_pct") is not None:
            icon = "✅" if deep["avg_roe_pct"] >= 15.0 else "⚠️"
            lines.append(
                f"  {icon} *{yrs}-г. среден ROE (банков тест):* `{deep['avg_roe_pct']:.1f}%`"
                if is_bg
                else f"  {icon} *{yrs}-Yr Avg ROE (Bank Playbook):* `{deep['avg_roe_pct']:.1f}%`"
            )
    else:
        if deep.get("avg_roic_pct") is not None:
            icon = "✅" if deep["avg_roic_pct"] >= 15.0 else "⚠️"
            min_s = f" (мин: `{deep['min_roic_pct']:.1f}%`)" if (deep.get("min_roic_pct") is not None and is_bg) else (
                f" (min: `{deep['min_roic_pct']:.1f}%`)" if deep.get("min_roic_pct") is not None else ""
            )
            lines.append(
                f"  {icon} *{yrs}-г. среден ROIC:* `{deep['avg_roic_pct']:.1f}%`{min_s}"
                if is_bg
                else f"  {icon} *{yrs}-Yr Avg ROIC:* `{deep['avg_roic_pct']:.1f}%`{min_s}"
            )
        if deep.get("true_owner_earnings") is not None:
            toe_b = deep["true_owner_earnings"] / 1e9
            sbc_b = (deep.get("latest_sbc") or 0.0) / 1e9
            sbc_pct = deep.get("sbc_pct_of_ocf")
            sbc_tag = f" | SBC: `{sym}{sbc_b:.2f}B` (`{sbc_pct:.0f}%` OCF)" if sbc_pct is not None else ""
            y_tag = f" (Yield: `{deep['true_oe_yield_pct']:.2f}%`)" if deep.get("true_oe_yield_pct") is not None else ""
            lines.append(
                f"  💵 *Истински Owner Earnings (след SBC):* `{sym}{toe_b:,.2f}B`{y_tag}{sbc_tag}"
                if is_bg
                else f"  💵 *True Owner Earnings (after SBC):* `{sym}{toe_b:,.2f}B`{y_tag}{sbc_tag}"
            )

    if deep.get("rev_cagr_pct") is not None:
        cagr_icon = "✅" if deep["rev_cagr_pct"] >= 5.0 else ("🟡" if deep["rev_cagr_pct"] >= 0 else "⚠️")
        lines.append(
            f"  {cagr_icon} *Ръст на приходите (CAGR):* `{deep['rev_cagr_pct']:+.1f}%/год.`"
            if is_bg
            else f"  {cagr_icon} *Revenue CAGR:* `{deep['rev_cagr_pct']:+.1f}%/yr`"
        )

    if deep.get("share_change_pct") is not None:
        sc = deep["share_change_pct"]
        sy = deep.get("share_years") or yrs
        if sc <= -2.0:
            lines.append(
                f"  ✅ *Обратно изкупуване (Buybacks):* `{sc:+.1f}%` акции за {sy} г. (в полза на акционерите)"
                if is_bg
                else f"  ✅ *Share Buybacks:* `{sc:+.1f}%` share count over {sy} yrs (shareholder-friendly)"
            )
        elif sc >= 3.0:
            lines.append(
                f"  ⚠️ *Разводняване (Dilution):* `{sc:+.1f}%` повече акции за {sy} г."
                if is_bg
                else f"  ⚠️ *Share Dilution:* `{sc:+.1f}%` more shares over {sy} yrs"
            )
        else:
            lines.append(
                f"  ℹ️ *Брой акции ({sy} г.):* Стабилен (`{sc:+.1f}%`)"
                if is_bg
                else f"  ℹ️ *Share Count ({sy} yrs):* Stable (`{sc:+.1f}%`)"
            )

    if not deep.get("is_bank_or_insurer"):
        ic = deep.get("interest_coverage_x")
        if ic is not None:
            if ic >= 100.0:
                lines.append(
                    "  ✅ *Покритие на лихвите (EBIT/Interest):* `100x+` (Крепостен баланс)"
                    if is_bg
                    else "  ✅ *Interest Coverage (EBIT/Interest):* `100x+` (Fortress)"
                )
            elif ic >= 5.0:
                lines.append(
                    f"  ✅ *Покритие на лихвите:* `{ic:.1f}x` (> 5.0x)"
                    if is_bg
                    else f"  ✅ *Interest Coverage:* `{ic:.1f}x` (> 5.0x)"
                )
            else:
                lines.append(
                    f"  ⚠️ *Покритие на лихвите:* `{ic:.1f}x` (< 5.0x — повишен риск)"
                    if is_bg
                    else f"  ⚠️ *Interest Coverage:* `{ic:.1f}x` (< 5.0x — tight)"
                )

        stp = deep.get("stress_test_pass")
        sm = deep.get("stress_ebit_margin_pct")
        if stp is True:
            lines.append(
                f"  ✅ *Стрес тест (−30% приходи):* Издържа комфортно (стрес EBIT марж `~{sm:.1f}%`)"
                if is_bg
                else f"  ✅ *Recession Stress Test (−30% Revenue):* Passes comfortably (stressed EBIT margin `~{sm:.1f}%`)"
            )
        elif stp is False:
            lines.append(
                f"  🚩 *Стрес тест (−30% приходи):* Уязвима (стрес EBIT марж `{sm:.1f}%`)"
                if is_bg
                else f"  🚩 *Recession Stress Test (−30% Revenue):* Vulnerable (stressed EBIT margin `{sm:.1f}%`)"
            )

    flags = deep.get("red_flags") or []
    if flags:
        flag_texts = []
        for f in flags:
            code, val = f["code"], f["val"]
            if code == "sbc_heavy":
                flag_texts.append(
                    f"🚩 Тежък SBC (`{val:.0f}%` от OCF изяжда печалбата на акционерите)"
                    if is_bg
                    else f"🚩 Heavy SBC Dilution (`{val:.0f}%` of OCF erodes Owner Earnings)"
                )
            elif code == "accounting_gain":
                flag_texts.append(
                    f"🚩 Счетоводна аномалия (Net Income е `{val:.0f}%` от OCF — възможна еднократна печалба)"
                    if is_bg
                    else f"🚩 Earnings Quality Alert (Net Income is `{val:.0f}%` of OCF — possible one-time gain)"
                )
            elif code == "dilution":
                flag_texts.append(
                    f"🚩 Силно разводняване на акционерите (`+{val:.1f}%` нови акции)"
                    if is_bg
                    else f"🚩 Heavy Share Dilution (`+{val:.1f}%` share count growth)"
                )
            elif code == "margin_erosion":
                flag_texts.append(
                    f"🚩 Свиване на оперативния марж (`{val:+.1f}%` спрямо средния — провери рова!)"
                    if is_bg
                    else f"🚩 Margin Erosion (`{val:+.1f} pts` below multi-yr avg — check moat!)"
                )
            elif code == "rev_decline":
                flag_texts.append(
                    f"🚩 Спад на приходите YoY (`{val:+.1f}%`)"
                    if is_bg
                    else f"🚩 Revenue Decline YoY (`{val:+.1f}%`)"
                )
            elif code == "stress_fail":
                flag_texts.append(
                    "🚩 Не издържа рецесионния стрес тест при −30% приходи"
                    if is_bg
                    else "🚩 Fails Buffett's −30% revenue recession stress test"
                )
            elif code == "low_interest_cov":
                flag_texts.append(
                    f"🚩 Ниско покритие на лихвите (`{val:.1f}x` < 5x)"
                    if is_bg
                    else f"🚩 Low Interest Coverage (`{val:.1f}x` < 5x)"
                )
            elif code == "ar_bloat":
                flag_texts.append(
                    f"🚩 Вземанията (A/R) растат с `+{val:.1f} п.п.` над приходите (провери качеството на приходите)"
                    if is_bg
                    else f"🚩 Receivables growing `+{val:.1f} pts` faster than revenue (check revenue recognition)"
                )
            elif code == "inv_bloat":
                flag_texts.append(
                    f"🚩 Запасите (Inventory) растат с `+{val:.1f} п.п.` над приходите (риск от залежаване)"
                    if is_bg
                    else f"🚩 Inventory growing `+{val:.1f} pts` faster than revenue (inventory bloat risk)"
                )
            elif code == "fake_goodwill":
                flag_texts.append(
                    f"🚩 Висок счетоводен Goodwill (`{val:.0f}%` от капитала) при нисък ROIC („капитализиран адреналин“)"
                    if is_bg
                    else f"🚩 Heavy M&A Goodwill (`{val:.0f}%` of equity) with low ROIC ('capitalized adrenaline')"
                )
            elif code == "failed_dollar_test":
                flag_texts.append(
                    f"🚩 Не издържа Теста за $1 задържана печалба (ROIIC `{val:+.1f}%`)"
                    if is_bg
                    else f"🚩 Fails Buffett's $1 Retained Earnings Test (ROIIC `{val:+.1f}%`)"
                )
            elif code == "ebitda_illusion":
                flag_texts.append(
                    f"🚩 Илюзия на EBITDA („Bullshit Earnings“ по Мънгър — `{val:.0f}%` от EBITDA отиват за CapEx и SBC)"
                    if is_bg
                    else f"🚩 Munger 'Bullshit Earnings' Alert (`{val:.0f}%` of EBITDA consumed by Maintenance CapEx & SBC)"
                )
            elif code == "gross_margin_squeeze":
                flag_texts.append(
                    f"🚩 Свиване на брутния марж (`{val:+.1f} п.п.` — риск тип Kraft Heinz срещу Costco Kirkland)"
                    if is_bg
                    else f"🚩 Gross-Margin Squeeze (`{val:+.1f} pts` — Kraft Heinz vs. Kirkland private-label pressure)"
                )
        lines.extend([f"  {ft}" for ft in flag_texts])
    else:
        lines.append(
            "  🛡️ *Детектор за капани:* ✅ Няма открити счетоводни или SBC червени флагове!"
            if is_bg
            else "  🛡️ *Red-Flag Detector:* ✅ Zero accounting, SBC, or dilution red flags detected!"
        )
    return lines


def analyze_stock_with_buffett(
    metrics: dict,
    watch_item: WatchlistItem | None = None,
    user: User | None = None,
    latest_snap: PortfolioSnapshot | None = None,
    session: Session | None = None,
    deep_metrics: dict | None = None,
) -> str:
    """Produce a full English (default) or Bulgarian Buffett & Munger stock report with multi-year deep audit."""
    lang = get_lang(user)
    is_bg = lang == "bg"
    cur = metrics.get("currency", "USD")
    sym = "$" if cur == "USD" else ("p " if cur == "GBX" else ("€" if cur == "EUR" else f"{cur} "))
    price = metrics.get("price") or (watch_item.last_price if watch_item else None) or 0.0
    score = compute_quantitative_buffett_scorecard(metrics, watch_item, lang=lang)

    mcap_b = (metrics.get("market_cap") or 0.0) / 1e9
    oe_b = (metrics.get("owner_earnings_est") or 0.0) / 1e9
    nd_b = (metrics.get("net_debt") or 0.0) / 1e9
    nd_ebitda_s = f"{metrics.get('net_debt_to_ebitda')}x" if metrics.get("net_debt_to_ebitda") is not None else "Net Cash"

    if is_bg:
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
            f"  • *Нетен дълг (Net Debt):* `{sym}{nd_b:,.2f}B` (Net Debt/EBITDA: `{nd_ebitda_s}`)",
        ]
    else:
        header_lines = [
            f"🧠 *BUFFETT & MUNGER ANALYSIS: {metrics['ticker']} ({metrics['name']})*",
            f"🏷️ *Sector:* {metrics.get('sector', 'N/A')} | {metrics.get('industry', 'N/A')}",
            "",
            "📊 *1. Key Fundamental Metrics (TTM):*",
            f"  • *Current Price:* `{sym}{price:,.2f}` (Market Cap: `{sym}{mcap_b:,.1f}B`)",
            f"  • *P/E (Trailing / Forward):* `{metrics.get('pe_trailing') or 'N/A'}` / `{metrics.get('pe_forward') or 'N/A'}`",
            f"  • *Gross / Operating Margin:* `{metrics.get('gross_margin_pct') or 'N/A'}%` / `{metrics.get('operating_margin_pct') or 'N/A'}%`",
            f"  • *ROIC (est.) / ROE:* `{metrics.get('roic_est_pct') or (watch_item.roic_pct if watch_item else 'N/A')}%` / `{metrics.get('roe_pct') or 'N/A'}%`",
            f"  • *Owner Earnings (OCF - Maint. CapEx):* `{sym}{oe_b:,.2f}B` (Yield: `{metrics.get('owner_earnings_yield_pct') or 'N/A'}%`)",
            f"  • *Cash Conversion (OCF / Net Income):* `{metrics.get('cash_conversion_pct') or 'N/A'}%`",
            f"  • *Net Debt:* `{sym}{nd_b:,.2f}B` (Net Debt/EBITDA: `{nd_ebitda_s}`)",
        ]

    # Section 2 (v1.1.0): Deep Multi-Year Historical Audit & Red-Flag Detector
    deep_lines = format_deep_audit_lines(deep_metrics, sym, lang)
    header_lines.extend(deep_lines)
    sec_val_num = 3 if deep_lines else 2
    sec_ai_num = 4 if deep_lines else 3

    # v1.2.0: Industry Playbook & Buffett Economic Model Classification (03 & 08)
    pb_info = evaluate_industry_playbook_checks(metrics, deep_metrics, lang=lang)
    header_lines.extend([
        "",
        f"📘 *Бъфет Плейбук:* *{pb_info['title']}*"
        if is_bg
        else f"📘 *Buffett Playbook:* *{pb_info['title']}*",
        f"  • *Бизнес модел:* {pb_info['model_type']}"
        if is_bg
        else f"  • *Economic Model:* {pb_info['model_type']}",
        f"  • {pb_info['badge']}",
    ])
    for enote in pb_info["extra_notes"]:
        header_lines.append(f"  • {enote}")
    header_lines.append(f"  💡 _{pb_info['lesson']}_")

    moat_text = localize_moat(metrics["ticker"], watch_item.moat_summary if watch_item else None, lang)
    if moat_text and moat_text not in ("Wide Economic Moat", "Широк конкурентен ров"):
        header_lines.extend([
            f"🏰 *Конкурентен ров:* {moat_text}" if is_bg else f"🏰 *Economic Moat:* {moat_text}",
        ])

    if is_bg:
        header_lines.extend([
            "",
            f"⚖️ *{sec_val_num}. Бърз филтър на Бъфет & Оценка на Вътрешната стойност:*",
            *[f"  {c}" for c in score["checks"]],
            "",
            f"🎯 *Вътрешна стойност (Intrinsic Value):* `{sym}{score['iv_low']:,.2f} – {sym}{score['iv_high']:,.2f}`",
            f"🛡️ *Целева цена за покупка (Target Buy с {score['mos_req']:.0f}% MoS):* `<= {sym}{score['target_buy']:,.2f}`",
            f"📌 *Присъда:* *{score['verdict']}*",
        ])
        if watch_item and watch_item.notes:
            header_lines.append(f"📝 *Бележка:* _{watch_item.notes}_")
    else:
        header_lines.extend([
            "",
            f"⚖️ *{sec_val_num}. Buffett Quick Filter & Intrinsic Valuation:*",
            *[f"  {c}" for c in score["checks"]],
            "",
            f"🎯 *Intrinsic Value Range:* `{sym}{score['iv_low']:,.2f} – {sym}{score['iv_high']:,.2f}`",
            f"🛡️ *Target Buy Price ({score['mos_req']:.0f}% MoS):* `<= {sym}{score['target_buy']:,.2f}`",
            f"📌 *Verdict:* *{score['verdict']}*",
        ])

    # v1.3.0: Fetch live news headlines (Yahoo Finance RSS + Google News RSS, 0 API keys)
    live_news = fetch_live_ticker_news(metrics["ticker"], metrics.get("name"), limit=4)
    if live_news:
        header_lines.extend([
            "",
            "📰 *Пресни новини & Събития (На живо):*"
            if is_bg
            else "📰 *Live News & Regulatory Events:*",
        ])
        for item in live_news[:3]:
            dt_s = f" _({item['date']})_" if item.get("date") else ""
            clean_t = item["title"].replace("*", "").replace("_", "").replace("`", "")
            header_lines.append(f"  • {clean_t}{dt_s}")

    gemini_key = _get_gemini_api_key(user=user, session=session)
    if gemini_key:
        try:
            portfolio_ctx = ""
            if latest_snap:
                portfolio_ctx = (
                    f"User portfolio: €{latest_snap.total_value:.0f}, semiconductors {latest_snap.semiconductor_pct:.1f}% (cap 20-25%)."
                )
            deep_ctx = (
                f"\nMulti-year historical audit: {json.dumps(deep_metrics, ensure_ascii=False, separators=(',', ':'))}"
                if deep_metrics
                else ""
            )
            news_ctx_str = format_news_context_for_ai({metrics["ticker"]: live_news}) if live_news else ""
            news_block = f"\n{news_ctx_str}" if news_ctx_str else ""
            playbook_ctx = build_stock_playbook_prompt_context(metrics, deep_metrics, lang=lang)
            if is_bg:
                prompt = (
                    f"Анализирай {metrics['ticker']} ({metrics['name']}) по Бъфет и Мънгър.\n"
                    f"TTM данни: {json.dumps(metrics, ensure_ascii=False, separators=(',', ':'))}{deep_ctx}{news_block}\n"
                    f"IV: {sym}{score['iv_low']}-{sym}{score['iv_high']}, Target Buy <= {sym}{score['target_buy']}. {portfolio_ctx}\n"
                    f"Активни Бъфет плейбуци:\n{playbook_ctx}\n"
                    "Дай стегнато обобщение в 4 точки (до 1150 знака), коментирайки и живите новини (ако има важни събития/регулации), многогодишния ROIC, Теста за $1, канибалските buybacks/SBC и червените флагове:\n"
                    "1) Франчайз/Ров (вид, посока и индустриална специфика); 2) Мениджмънт ($1 тест, SBC, buybacks и институционален императив); 3) Инверсия на Мънгър & Новини (Топ 2 риска); 4) Кога да продадем (4-те критерия)."
                )
            else:
                prompt = (
                    f"Analyze {metrics['ticker']} ({metrics['name']}) using Buffett & Munger's framework.\n"
                    f"TTM metrics: {json.dumps(metrics, ensure_ascii=False, separators=(',', ':'))}{deep_ctx}{news_block}\n"
                    f"IV: {sym}{score['iv_low']}-{sym}{score['iv_high']}, Target Buy <= {sym}{score['target_buy']}. {portfolio_ctx}\n"
                    f"Active Buffett Playbooks:\n{playbook_ctx}\n"
                    "Write a crisp 4-part summary (under 1150 chars), incorporating live news/regulatory events, industry playbook, multi-year ROIC, $1 Retained Earnings Test, cannibal buybacks/SBC & any red flags:\n"
                    "1) Franchise/Moat (type, trend & industry specifics); 2) Management ($1 test, SBC, buybacks & institutional imperative); 3) Munger's Inversion & Live News (Top 2 risks); 4) When to sell (Buffett's 4 criteria)."
                )
            ai_text = _call_gemini_with_fallback(
                gemini_key,
                load_buffett_system_instruction(lang=lang),
                prompt,
            )
            if ai_text:
                header_lines.extend([
                    "",
                    f"🤖 *{sec_ai_num}. Задълбочен AI Анализ (Бъфет & Мънгър Агент):*"
                    if is_bg
                    else f"🤖 *{sec_ai_num}. Deep AI Analysis (Buffett & Munger Agent):*",
                    ai_text,
                ])
        except Exception as e:
            friendly = _format_friendly_ai_error(e, is_bg)
            header_lines.append(
                f"\n_(ℹ️ {friendly} Пълният 4-годишен одит по Бъфет е показан горе.)_"
                if is_bg
                else f"\n_(ℹ️ {friendly} Full multi-year Buffett audit shown above.)_"
            )

    return "\n".join(header_lines)


def analyze_stock_with_munger_checklist(
    metrics: dict,
    watch_item: WatchlistItem | None = None,
    user: User | None = None,
    latest_snap: PortfolioSnapshot | None = None,
    session: Session | None = None,
    deep_metrics: dict | None = None,
) -> str:
    """Produce Charlie Munger's Two-Track Analysis & 12-Point Aviation Pre-Buy Checklist (/munger TICKER)."""
    lang = get_lang(user)
    is_bg = lang == "bg"
    deep = deep_metrics or {}
    cur = metrics.get("currency", "USD")
    sym = "$" if cur == "USD" else ("p " if cur == "GBX" else ("€" if cur == "EUR" else f"{cur} "))
    price = metrics.get("price") or (watch_item.last_price if watch_item else None) or 0.0
    score = compute_quantitative_buffett_scorecard(metrics, watch_item, lang=lang)
    pb_info = evaluate_industry_playbook_checks(metrics, deep, lang=lang)

    is_fin = bool(deep.get("is_bank_or_insurer"))
    avg_roic = deep.get("avg_roic_pct") or metrics.get("roic_est_pct")
    avg_roe = deep.get("avg_roe_pct") or metrics.get("roe_pct")
    eff_ret = avg_roe if is_fin else (avg_roic if avg_roic is not None else avg_roe)
    gm_avg = deep.get("gross_margin_avg_pct") or metrics.get("gross_margin_pct")
    gm_delta = deep.get("gross_margin_delta_pct")
    ebitda_ill = deep.get("ebitda_illusion_pct")
    sbc_pct = deep.get("sbc_pct_of_ocf")
    share_chg = deep.get("share_change_pct")
    payback = deep.get("whole_business_payback_yrs")
    roiic = deep.get("roiic_pct")
    dollar_pass = deep.get("passes_one_dollar_test")
    stress_pass = deep.get("stress_test_pass")
    int_cov = deep.get("interest_coverage_x")
    nd_ebitda = metrics.get("net_debt_to_ebitda")
    flags = deep.get("red_flags") or []

    # Score the 12-point Munger Aviation Gatekeeper Checklist deterministically
    c1_pass = pb_info["playbook_key"] != "cautionary_cyclicals"
    c2_pass = len(flags) == 0
    c3_pass = (eff_ret or 0) >= 15.0
    c4_pass = is_fin or bool(deep.get("prayer_session_pricing_power")) or ((gm_avg or 0) >= 40.0 and (gm_delta is None or gm_delta >= -2.0))
    c5_pass = is_fin or ((ebitda_ill is None or ebitda_ill < 50.0) and (sbc_pct is None or sbc_pct < 20.0))
    c6_pass = (eff_ret or 0) >= 15.0 and (dollar_pass is not False)
    c7_pass = (share_chg is None or share_chg <= 2.0)
    c8_pass = is_fin or ((stress_pass is not False) and (int_cov is None or int_cov >= 5.0) and (nd_ebitda is None or nd_ebitda < 2.0))
    c9_pass = not any(f["code"] in ("ar_bloat", "inv_bloat", "fake_goodwill", "accounting_gain") for f in flags)
    c10_pass = (payback is None or payback <= 25.0) and (price <= score["iv_high"])
    c11_pass = price <= score["target_buy"]

    has_high_apr_debt = False
    buf_missing = 0.0
    if user:
        buf_missing = max(0.0, float(user.emergency_buffer_target or 0) - float(user.emergency_buffer_current or 0))
    is_chip = metrics["ticker"].upper() in SEMICONDUCTOR_SHORT_TICKERS
    semi_pct = float(latest_snap.semiconductor_pct) if latest_snap else 0.0
    c12_pass = (buf_missing <= 0) and not (is_chip and semi_pct >= 20.0)

    passed_count = sum([c1_pass, c2_pass, c3_pass, c4_pass, c5_pass, c6_pass, c7_pass, c8_pass, c9_pass, c10_pass, c11_pass, c12_pass])

    if is_bg:
        lines = [
            f"🦅 *ЧАРЛИ МЪНГЪР: ДВУПИСТОВ АНАЛИЗ & 12-ТОЧКОВ ЧЕКЛИСТ ({metrics['ticker']})*",
            f"📘 *Плейбук:* {pb_info['title']} | *Резултат:* `{passed_count}/12` контролни точки",
            "",
            "⚙️ *ПИСТА 1 — Микроикономика, Мащаб & Истински Owner Earnings:*",
            f"  {'✅' if c1_pass else '⚠️'} *1. Кръг на компетентност & Скорост на промяна:* {pb_info['model_type']}",
            f"  {'✅' if c2_pass else '🚩'} *2. Инверсия на Мънгър (Детектор за провал):* {f'{len(flags)} червени флага' if flags else '0 счетоводни/SBC червени флага'}",
            f"  {'✅' if c3_pass else '⚠️'} *3. 5-те предимства на мащаба & 40-г. ROIC конвергенция:* `{eff_ret if eff_ret is not None else 'N/A'}%` (цел >= 15%)",
            f"  {'✅' if c4_pass else '⚠️'} *4. Ценова сила без „молитвена сесия“ (Kraft vs. Kirkland):* Брутен марж `{gm_avg if gm_avg is not None else 'N/A'}%` ({f'{gm_delta:+.1f} п.п. тренд' if gm_delta is not None else 'стабилен'})",
            f"  {'✅' if c5_pass else '🚩'} *5. Истински Owner Earnings срещу „Bullshit Earnings“ (EBITDA):* "
            + (
                "Банков/Застрахователен модел"
                if is_fin
                else f"EBITDA илюзия `{ebitda_ill if ebitda_ill is not None else 0:.0f}%` | SBC `{sbc_pct if sbc_pct is not None else 0:.1f}%` от OCF"
            ),
            f"  {'✅' if c6_pass else '⚠️'} *6. Тест за $1 задържана печалба (ROIIC):* `{f'{roiic:+.1f}%' if roiic is not None else 'N/A'}`",
            f"  {'✅' if c7_pass else '🚩'} *7. „Канибалски“ Buybacks & Оборотен капитал:* Акции `{f'{share_chg:+.1f}%' if share_chg is not None else 'N/A'}`"
            + (" (🦈 Канибал)" if deep.get("is_cannibal") else "")
            + (" | 🔄 Отрицателен обор. капитал" if deep.get("negative_working_capital") else ""),
            f"  {'✅' if c8_pass else '🚩'} *8. Крепостен баланс & Стрес тест (−30% приходи):* "
            + (
                f"Лихвено покритие `{int_cov if int_cov is not None else 'N/A'}x` | Стрес EBIT `{deep.get('stress_ebit_margin_pct')}%`"
                if not is_fin
                else "Финансова институция (следи CET1 > 10% / Combined Ratio < 100%)"
            ),
            f"  {'✅' if c9_pass else '🚩'} *9. Хигиена на счетоводството (Mark-to-Myth тест):* "
            + ("Чисти отчети (A/R, Запаси и Goodwill в норма)" if c9_pass else "Открито отклонение в A/R, Запаси или Goodwill!"),
            f"  {'✅' if c10_pass else '⚠️'} *10. Формула на Езоп (Изплащане на 100% от бизнеса):* `~{payback if payback is not None else 'N/A'} г.` чрез Истински Owner Earnings",
            "",
            "🧠 *ПИСТА 2 — Психология на човешките грешки (25-те капана от Харвард 1995):*",
            f"  {'✅' if c11_pass else '⏳'} *11. Контрастен капан & Марж на безопасност:* Цена `{sym}{price:,.2f}` срещу Target Buy `<= {sym}{score['target_buy']:,.2f}` (IV `{sym}{score['iv_low']:,.0f}–{sym}{score['iv_high']:,.0f}`)",
            f"  {'✅' if c12_pass else '🛡️'} *12. Лична капитална дисциплина (Буфер, Кредит & Секторен таван):* "
            + (
                f"Буфер липсват `€{buf_missing:.0f}` | Чипове `{semi_pct:.1f}%` (таван 20–25%)"
                if not c12_pass
                else "Буфер и секторни лимити са в норма"
            ),
        ]
    else:
        lines = [
            f"🦅 *CHARLIE MUNGER: TWO-TRACK ANALYSIS & 12-POINT CHECKLIST ({metrics['ticker']})*",
            f"📘 *Playbook:* {pb_info['title']} | *Score:* `{passed_count}/12` gatekeeper checks",
            "",
            "⚙️ *TRACK 1 — Microeconomics, Scale & True Owner Earnings:*",
            f"  {'✅' if c1_pass else '⚠️'} *1. Circle of Competence & Rate of Change:* {pb_info['model_type']}",
            f"  {'✅' if c2_pass else '🚩'} *2. Munger's Inversion (Failure Detector):* {f'{len(flags)} red flags' if flags else '0 accounting/SBC red flags'}",
            f"  {'✅' if c3_pass else '⚠️'} *3. 5 Scale Advantages & 40-Yr ROIC Convergence:* `{eff_ret if eff_ret is not None else 'N/A'}%` (target >= 15%)",
            f"  {'✅' if c4_pass else '⚠️'} *4. 'Prayer Session' Pricing Power (Kraft vs. Kirkland):* Gross margin `{gm_avg if gm_avg is not None else 'N/A'}%` ({f'{gm_delta:+.1f} pts trend' if gm_delta is not None else 'stable'})",
            f"  {'✅' if c5_pass else '🚩'} *5. True Owner Earnings vs. 'Bullshit Earnings' (EBITDA):* "
            + (
                "Bank/Insurance Balance-Sheet Model"
                if is_fin
                else f"EBITDA illusion `{ebitda_ill if ebitda_ill is not None else 0:.0f}%` | SBC `{sbc_pct if sbc_pct is not None else 0:.1f}%` of OCF"
            ),
            f"  {'✅' if c6_pass else '⚠️'} *6. Buffett $1 Retained Earnings Test (ROIIC):* `{f'{roiic:+.1f}%' if roiic is not None else 'N/A'}`",
            f"  {'✅' if c7_pass else '🚩'} *7. 'Cannibal' Buybacks & Working Capital:* Shares `{f'{share_chg:+.1f}%' if share_chg is not None else 'N/A'}`"
            + (" (🦈 Cannibal)" if deep.get("is_cannibal") else "")
            + (" | 🔄 Negative Working Capital" if deep.get("negative_working_capital") else ""),
            f"  {'✅' if c8_pass else '🚩'} *8. Fortress Balance Sheet & −30% Stress Test:* "
            + (
                f"Interest cov `{int_cov if int_cov is not None else 'N/A'}x` | Stressed EBIT `{deep.get('stress_ebit_margin_pct')}%`"
                if not is_fin
                else "Financial institution (track CET1 > 10% / Combined Ratio < 100%)"
            ),
            f"  {'✅' if c9_pass else '🚩'} *9. Accounting Hygiene ('Mark-to-Myth' Check):* "
            + ("Clean GAAP conversion (A/R, Inventory & Goodwill healthy)" if c9_pass else "Flagged divergence in A/R, Inventory, or Goodwill!"),
            f"  {'✅' if c10_pass else '⚠️'} *10. Aesop's Whole-Business Payback Test:* `~{payback if payback is not None else 'N/A'} yrs` of True Owner Earnings",
            "",
            "🧠 *TRACK 2 — Psychology of Human Misjudgment (25 Harvard Biases):*",
            f"  {'✅' if c11_pass else '⏳'} *11. Contrast-Misreaction & Margin of Safety:* Price `{sym}{price:,.2f}` vs Target Buy `<= {sym}{score['target_buy']:,.2f}` (IV `{sym}{score['iv_low']:,.0f}–{sym}{score['iv_high']:,.0f}`)",
            f"  {'✅' if c12_pass else '🛡️'} *12. Personal Capital Discipline (Buffer, Debt & Sector Cap):* "
            + (
                f"Buffer missing `€{buf_missing:.0f}` | Chips `{semi_pct:.1f}%` (cap 20–25%)"
                if not c12_pass
                else "Buffer & sector limits healthy"
            ),
        ]

    gemini_key = _get_gemini_api_key(user=user, session=session)
    if gemini_key:
        try:
            munger_pb = (
                THEMATIC_PLAYBOOKS["press_conference_masterclass_09"]["bg" if is_bg else "en"]
                + "\n"
                + THEMATIC_PLAYBOOKS["munger_psychology_checklist_10"]["bg" if is_bg else "en"]
            )
            prompt = (
                f"Ти си Чарли Мънгър. Анализирай {metrics['ticker']} ({metrics['name']}) по Двупистовия анализ и Правилото на Дарвин.\n"
                f"Данни: {json.dumps(metrics, ensure_ascii=False, separators=(',', ':'))}\n"
                f"4-г. одит: {json.dumps(deep, ensure_ascii=False, separators=(',', ':'))}\n"
                f"{munger_pb}\n"
                "Дай остър, прям коментар в стила на Чарли Мънгър в 3 кратки точки (до 950 знака):\n"
                "1) Кои от 5-те предимства на мащаба или Ефект Лолапалуза работят за компанията (и има ли „Bullshit Earnings“ / SBC илюзия);\n"
                "2) Правилото на Дарвин (30-минутният контра-аргумент: най-силната меча теза и психологическите капани от 25-те тенденции);\n"
                "3) Финална присъда по Перфокартата с 20 дупки (струва ли си 1 дупка на текущата цена)."
                if is_bg
                else f"You are Charlie Munger. Analyze {metrics['ticker']} ({metrics['name']}) using Two-Track Analysis and Darwin's 30-Minute Rule.\n"
                f"Metrics: {json.dumps(metrics, ensure_ascii=False, separators=(',', ':'))}\n"
                f"4-yr audit: {json.dumps(deep, ensure_ascii=False, separators=(',', ':'))}\n"
                f"{munger_pb}\n"
                "Write a razor-sharp Charlie Munger verdict in 3 concise points (under 950 chars):\n"
                "1) Which of the 5 Scale Advantages or Lollapalooza forces apply (and any 'Bullshit Earnings' EBITDA/SBC illusion);\n"
                "2) Darwin's 30-Minute Counter-Thesis (strongest bear case & which of the 25 psychological biases could trap buyers here);\n"
                "3) 20-Slot Lifetime Punch Card Verdict (is it worth 1 punch at today's price?)."
            )
            ai_text = _call_gemini_with_fallback(
                gemini_key,
                load_buffett_system_instruction(lang=lang),
                prompt,
            )
            if ai_text:
                lines.extend([
                    "",
                    "🦅 *Коментар на Чарли Мънгър (Правилото на Дарвин & 25-те капана):*"
                    if is_bg
                    else "🦅 *Charlie Munger's Verdict (Darwin's Rule & 25 Biases):*",
                    ai_text,
                ])
        except Exception as e:
            friendly = _format_friendly_ai_error(e, is_bg)
            lines.append(f"\n_(ℹ️ {friendly})_")

    return "\n".join(lines)


def ask_buffett_agent(
    session: Session,
    user: User,
    question: str,
    latest_snap: PortfolioSnapshot | None = None,
) -> str:
    """Conversational Buffett & Munger Agent inside Telegram in 'en' (default) or 'bg'."""
    lang = get_lang(user)
    is_bg = lang == "bg"

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
    port_evals = evaluate_user_portfolio_undervalued(session, user, latest_snap, lang=lang) if latest_snap else []
    unified = rank_unified_top3_opportunities(session, user, latest_snap, port_evals, lang=lang)

    gemini_key = _get_gemini_api_key(user=user, session=session)
    if gemini_key:
        try:
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
            port_ctx = {}
            if parsed_snap:
                port_ctx = {
                    "total_eur": parsed_snap["total_value"],
                    "unrealized_ppl": parsed_snap["unrealized_ppl"],
                    "semiconductor_pct": parsed_snap["semiconductor_pct"],
                    "pies": [
                        {
                            "name": p["name"],
                            "value": p["value"],
                            "instruments": [
                                {
                                    "t": i["ticker"],
                                    "v": i["value"],
                                    "cur": i["current_pct"],
                                    "exp": i["expected_pct"],
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

            # Auto-enrich with multi-year deep historical metrics, live RSS news & industry playbooks
            # Also check recent chat history so follow-up questions keep context on previously discussed tickers!
            recent_turns = _get_recent_chat_turns(user.telegram_id)
            known_tickers = {x["ticker"] for x in unified.get("all_ranked") or []}
            stop_words = {"ROE", "ROA", "ROIC", "OCF", "FCF", "CAPEX", "EBITDA", "GAAP", "SBC", "ETF", "UCITS", "USD", "EUR", "CGT", "APR", "TBI", "TOP", "BUY", "SELL", "MOS", "SEC", "DOJ"}
            raw_tokens = re.findall(r"\b[A-Z]{2,5}(?:\.[A-Z])?\b", question.upper())
            mentioned = [tok for tok in raw_tokens if tok in known_tickers]
            if not mentioned:
                mentioned = [tok for tok in raw_tokens if tok not in stop_words][:2]
            if not mentioned and recent_turns:
                prev_q = recent_turns[-1][0].upper()
                prev_tokens = re.findall(r"\b[A-Z]{2,5}(?:\.[A-Z])?\b", prev_q)
                mentioned = [tok for tok in prev_tokens if tok in known_tickers or tok not in stop_words][:2]

            deep_mentioned: dict[str, dict] = {}
            mentioned_playbooks: list[str] = []
            ticker_news_map: dict[str, list[dict[str, str]]] = {}
            for tk in list(dict.fromkeys(mentioned))[:2]:
                try:
                    m_tk = fetch_stock_fundamentals(tk)
                    d_tk = fetch_deep_historical_metrics(tk, m_tk)
                    pb_key = classify_industry_playbook(m_tk.get("sector"), m_tk.get("industry"), tk)
                    mentioned_playbooks.append(pb_key)
                    t_news = fetch_live_ticker_news(tk, m_tk.get("name"), limit=4)
                    if t_news:
                        ticker_news_map[tk] = t_news
                    if d_tk:
                        deep_mentioned[tk] = {
                            "src": d_tk.get("data_source"),
                            "playbook": pb_key,
                            "avg_roic_pct": d_tk.get("avg_roic_pct"),
                            "avg_roe_pct": d_tk.get("avg_roe_pct"),
                            "true_oe_b": round(d_tk["true_owner_earnings"] / 1e9, 2) if d_tk.get("true_owner_earnings") is not None else None,
                            "sbc_pct_ocf": d_tk.get("sbc_pct_of_ocf"),
                            "share_chg_pct": d_tk.get("share_change_pct"),
                            "is_cannibal": d_tk.get("is_cannibal"),
                            "neg_wc": d_tk.get("negative_working_capital"),
                            "rd_pct_rev": d_tk.get("rd_pct_of_rev"),
                            "payback_yrs": d_tk.get("whole_business_payback_yrs"),
                            "int_cov_x": d_tk.get("interest_coverage_x"),
                            "capex_da_x": d_tk.get("capex_to_da_ratio"),
                            "roiic_pct": d_tk.get("roiic_pct"),
                            "dollar_test": d_tk.get("passes_one_dollar_test"),
                            "stress_pass": d_tk.get("stress_test_pass"),
                            "red_flags": d_tk.get("red_flags"),
                        }
                except Exception:
                    pass

            deep_block = (
                f"Multi-year deep audit (SEC EDGAR 10-K + yfinance) for mentioned tickers: {json.dumps(deep_mentioned, ensure_ascii=False, separators=(',', ':'))}\n"
                if deep_mentioned
                else ""
            )
            news_str = format_news_context_for_ai(ticker_news_map)
            news_block = f"{news_str}\n" if news_str else ""
            routed_playbooks = select_playbooks_for_question(
                question,
                mentioned_playbooks=mentioned_playbooks,
                lang=lang,
            )
            playbook_block = f"Routed Buffett Reference Playbooks:\n{routed_playbooks}\n" if routed_playbooks else ""

            history_block = ""
            if recent_turns:
                hist_lines = [f"  - User: {hq}\n    Agent: {ha}" for hq, ha in recent_turns[-3:]]
                history_block = "Recent Conversation Memory (for follow-up continuity):\n" + "\n".join(hist_lines) + "\n"

            full_prompt = (
                f"{history_block}"
                f"User ({user.full_name}) current question: {question}\n"
                f"Profile: income €{user.monthly_income:.0f}, budget €{user.monthly_invest_budget:.0f}, "
                f"buffer €{user.emergency_buffer_current:.0f}/€{user.emergency_buffer_target:.0f}, payday {user.payday_day}.\n"
                f"Loans: {json.dumps(loans_ctx, ensure_ascii=False, separators=(',', ':'))}\n"
                f"T212 Portfolio: {json.dumps(port_ctx, ensure_ascii=False, separators=(',', ':'))}\n"
                f"Unified Top 3 (Watchlist vs Portfolio): {json.dumps(unified_ctx, ensure_ascii=False, separators=(',', ':'))}\n"
                f"{deep_block}"
                f"{news_block}"
                f"{playbook_block}"
                + (
                    "Отговори стегнато за Telegram на български (до 2400 знака), с конкретни числа, живите новини (ако има) и маршрутизираните Бъфет плейбуци, с ясен избор дали да подсили позиция в портфейла или да добави нова от Watchlist."
                    if is_bg
                    else "Answer concisely for Telegram in English (under 2400 chars), with exact numbers, live news (if any), and the routed Buffett playbooks, giving a clear recommendation on whether to reinforce an existing holding or open a new Watchlist position."
                )
            )
            ai_text = _call_gemini_with_fallback(
                gemini_key,
                load_buffett_system_instruction(lang=lang),
                full_prompt,
            )
            if ai_text:
                _append_chat_turn(user.telegram_id, question, ai_text)
                title = "🤖 *БЪФЕТ & МЪНГЪР АГЕНТ:*" if is_bg else "🤖 *BUFFETT & MUNGER AGENT:*"
                return f"{title}\n\n{ai_text}"
        except Exception as e:
            friendly = _format_friendly_ai_error(e, is_bg)
            fallback_note = (
                f"_(ℹ️ {friendly} Показвам живия количествен разчет по Бъфет.)_\n\n"
                if is_bg
                else f"_(ℹ️ {friendly} Showing live quantitative Buffett breakdown.)_\n\n"
            )
    else:
        fallback_note = (
            "💡 _За свободен AI разговор с новини на живо добави безплатен Gemini API ключ направо тук с_ "
            "`/set_gemini_key КЛЮЧ` _(виж ℹ️ Помощ за 30-секундна инструкция). Ето живия ти динамичен разчет в момента:_\n\n"
            if is_bg
            else "💡 _For free-form AI chat with live Google Search news, add a free Gemini API key right here via_ "
            "`/set_gemini_key YOUR_KEY` _(see ℹ️ Help for a 30-second guide). Here is your live quantitative Buffett breakdown:_\n\n"
        )

    plan_lines = build_dynamic_investment_plan_lines(session, user, latest_snap)
    lines = [
        "🤖 *БЪФЕТ & МЪНГЪР АГЕНТ (ДИНАМИЧЕН АНАЛИЗ ДНЕС)*"
        if is_bg
        else "🤖 *BUFFETT & MUNGER AGENT (LIVE QUANTITATIVE ANALYSIS)*",
        fallback_note.strip(),
        "",
    ]
    if unified["top3"]:
        lines.append(
            "🏆 *ОБЩ ТОП 3 ДНЕС (WATCHLIST vs. ТВОЯТ ПОРТФЕЙЛ):*"
            if is_bg
            else "🏆 *UNIFIED TOP 3 TODAY (WATCHLIST vs. YOUR PORTFOLIO):*"
        )
        for idx, x in enumerate(unified["top3"], 1):
            sym = x["currency_sym"]
            lines.append(
                f"  {idx}️⃣ *{x['ticker']}* — {x['source_badge']}\n"
                f"     └ `{sym}{x['last_price']:,.2f}` | IV `{sym}{x['iv_low']:,.0f}–{sym}{x['iv_high']:,.0f}` | {x['status_tag']}"
            )
        if unified["comparison_insight"]:
            lines.append(f"💡 _{unified['comparison_insight']}_")
        lines.append("")

    lines.extend([
        "📋 *Динамична препоръка според твоя портфейл, буфер и пазарните цени:*"
        if is_bg
        else "📋 *Dynamic Action Plan based on your portfolio, cash buffer & live prices:*",
        *[f"• {l}" for l in plan_lines],
    ])
    return "\n".join(lines)


def generate_monthly_check_report(session: Session, user: User) -> str:
    """Generate the complete 5-section Monthly Check (/monthly_check) report dynamically in 'en' or 'bg'."""
    lang = get_lang(user)
    is_bg = lang == "bg"

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
    day_ord = format_ordinal(user.payday_day, lang)
    lines = [
        (
            f"📅 *МЕСЕЧЕН ФИНАНСОВ ОТЧЕТ ({day_ord} число на месеца)*"
            if is_bg
            else f"📅 *MONTHLY FINANCIAL CHECK ({day_ord} of the month)*"
        ),
        (
            f"👤 *Потребител:* {user.full_name or user.username or user.telegram_id}"
            if is_bg
            else f"👤 *Investor:* {user.full_name or user.username or user.telegram_id}"
        ),
        "",
    ]

    # Section 1: Portfolio status
    lines.append("📊 *1. СЪСТОЯНИЕ НА ТВОЯ ПОРТФЕЙЛ*" if is_bg else "📊 *1. YOUR PORTFOLIO SNAPSHOT*")
    if latest_snap:
        ppl_sign = "+" if latest_snap.unrealized_ppl >= 0 else ""
        if is_bg:
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
            semi_warn = (
                "⚠️ Above 25% cap — DO NOT add more semiconductors!"
                if latest_snap.semiconductor_pct >= 25.0
                else ("⚡ Above 20% — avoid adding more chips" if latest_snap.semiconductor_pct >= 20.0 else "✅ Healthy (<20%)")
            )
            lines.extend([
                f"  • *Total Trading 212 Value:* `{cur}{latest_snap.total_value:,.2f}`",
                f"  • *Invested Positions:* `{cur}{latest_snap.invested_value:,.2f}` | *Pie Cash:* `{cur}{latest_snap.pie_cash:,.2f}`",
                f"  • *Unrealized P&L:* `{ppl_sign}{cur}{latest_snap.unrealized_ppl:,.2f}` | *Realized:* `+{cur}{latest_snap.realized_result:,.2f}`",
                f"  • *Semiconductor Sector:* `{latest_snap.semiconductor_pct:.2f}%` ({semi_warn})",
            ])
    else:
        lines.append(
            "  • _Все още нямаш свързан Trading 212 портфейл. Използвай_ `/connect_t212`_, за да го синхронизираш._"
            if is_bg
            else "  • _No Trading 212 portfolio connected yet. Use_ `/connect_t212` _to sync your account._"
        )

    # Section 2: Top 3 Watchlist
    lines.extend(["", "🧠 *2. ТОП 3 ВЪЗМОЖНОСТИ ОТ WATCHLIST ДНЕС*" if is_bg else "🧠 *2. TOP 3 WATCHLIST OPPORTUNITIES TODAY*"])
    top3_wl = rank_top3_watchlist_opportunities(session, user, latest_snap, lang=lang)
    if top3_wl:
        for i, item in enumerate(top3_wl, 1):
            warn = f" ({item['sector_warn']})" if item["sector_warn"] else ""
            lines.append(
                f"  {i}️⃣ *`{item['ticker']}`* ({item['company_name']}): `{item['currency_sym']}{item['last_price']:.2f}` "
                f"| Target Buy `<= {item['currency_sym']}{item['target_buy_price']:.2f}` | {item['status_tag']}{warn}"
            )
    else:
        lines.append("  • ⏳ Няма заредени компании в Watchlist." if is_bg else "  • ⏳ No stocks in Watchlist yet.")

    # Section 3: Debt Action (Munger's Inversion)
    lines.extend(["", "💳 *3. ДЕЙСТВИЕ ПО КРЕДИТИ (Инверсия на Мънгър)*" if is_bg else "💳 *3. DEBT ACTION (Munger's Inversion)*"])
    total_loan_outflow = 0.0
    if active_loans:
        for loan in active_loans:
            fee_eur = round(loan.early_principal_payment * (loan.early_fee_percent / 100.0), 2) if loan.early_principal_payment > 0 else 0.0
            monthly_loan_total = round(loan.regular_payment + loan.early_principal_payment + fee_eur, 2)
            total_loan_outflow += monthly_loan_total
            new_principal_est = max(0.0, round(loan.principal_balance - loan.early_principal_payment - (loan.regular_payment * 0.84), 2))
            if is_bg:
                lines.extend([
                    f"  • *Кредитор:* *{loan.lender_name}* {f'(Договор № {loan.contract_number})' if loan.contract_number else ''}",
                    f"  • *Текуща главница:* `{cur}{loan.principal_balance:,.2f}` при ГПР `{loan.apr_percent:.2f}%`",
                    f"  • *1️⃣ Редовна вноска:* `{cur}{loan.regular_payment:,.2f}`",
                    f"  • *2️⃣ Предсрочно погасяване на главница:* `{cur}{loan.early_principal_payment:,.2f}` (+ такса `{cur}{fee_eur:.2f}`)",
                    f"  • *Общо плащане по кредита за месеца:* `{cur}{monthly_loan_total:,.2f}` _(гарантирана доходност от {loan.apr_percent:.2f}%!)_",
                    f"  • *Очаквана остатъчна главница:* `~{cur}{new_principal_est:,.2f}`",
                ])
                if loan.notes:
                    lines.append(f"  • *План:* _{loan.notes}_")
            else:
                lines.extend([
                    f"  • *Lender:* *{loan.lender_name}* {f'(Contract #{loan.contract_number})' if loan.contract_number else ''}",
                    f"  • *Current Principal:* `{cur}{loan.principal_balance:,.2f}` at `{loan.apr_percent:.2f}% APR`",
                    f"  • *1️⃣ Regular Payment:* `{cur}{loan.regular_payment:,.2f}`",
                    f"  • *2️⃣ Extra Principal Prepayment:* `{cur}{loan.early_principal_payment:,.2f}` (+ fee `{cur}{fee_eur:.2f}`)",
                    f"  • *Total Monthly Debt Outflow:* `{cur}{monthly_loan_total:,.2f}` _(guaranteed {loan.apr_percent:.2f}% return!)_",
                    f"  • *Est. Remaining Principal:* `~{cur}{new_principal_est:,.2f}`",
                ])
                if loan.notes:
                    lines.append(f"  • *Plan:* _{loan.notes}_")
    else:
        lines.append(
            "  • ✅ Нямаш въведени активни кредити! _(Добави кредит >10% ГПР с_ `/set_loan`_)._"
            if is_bg
            else "  • ✅ No active loans recorded! _(Track any high-interest loan >10% APR with_ `/set_loan`_)._"
        )

    # Section 4: Dynamic Investment Plan
    lines.extend(["", "📈 *4. ДИНАМИЧЕН ПЛАН ЗА ИНВЕСТИЦИЯ ДНЕС*" if is_bg else "📈 *4. DYNAMIC INVESTMENT PLAN TODAY*"])
    for dl in build_dynamic_investment_plan_lines(session, user, latest_snap):
        lines.append(f"  • {dl}")

    # Section 5: Liquidity Buffer & Budget
    lines.extend(["", "🛡️ *5. ПРОВЕРКА НА ЛИКВИДНИЯ БУФЕР И БЮДЖЕТА*" if is_bg else "🛡️ *5. LIQUIDITY BUFFER & BUDGET CHECK*"])
    if user.monthly_income <= 0 and user.monthly_invest_budget <= 0:
        lines.append(
            "  • _Бюджетът ти е празен. Въведи данните си с_ `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР`."
            if is_bg
            else "  • _Your budget is blank. Configure it via_ `/set_budget INCOME INVEST_BUDGET BUFFER`."
        )
    else:
        buf_ok = user.emergency_buffer_current >= user.emergency_buffer_target
        remaining_for_living = round(user.monthly_income - total_loan_outflow - user.monthly_invest_budget, 2)
        if is_bg:
            buf_icon = "✅" if buf_ok else f"⚠️ Липсват `{cur}{user.emergency_buffer_target - user.emergency_buffer_current:,.2f}` — изгради го приоритетно!"
            lines.extend([
                f"  • *Неприкосновен ликвиден буфер:* `{cur}{user.emergency_buffer_current:,.2f}` / цел `{cur}{user.emergency_buffer_target:,.2f}` {buf_icon}",
                f"  • *Месечен нетен доход:* `{cur}{user.monthly_income:,.2f}`",
                f"  • *След кредит (`{cur}{total_loan_outflow:,.2f}`) и бюджет за буфер/инвестиции (`{cur}{user.monthly_invest_budget:,.2f}`):* Остават `{cur}{remaining_for_living:,.2f}` за текущи разходи.",
            ])
        else:
            buf_icon = "✅" if buf_ok else f"⚠️ Missing `{cur}{user.emergency_buffer_target - user.emergency_buffer_current:,.2f}` — prioritize building it!"
            lines.extend([
                f"  • *Emergency Liquidity Buffer:* `{cur}{user.emergency_buffer_current:,.2f}` / target `{cur}{user.emergency_buffer_target:,.2f}` {buf_icon}",
                f"  • *Monthly Net Income:* `{cur}{user.monthly_income:,.2f}`",
                f"  • *After debt (`{cur}{total_loan_outflow:,.2f}`) & invest/buffer budget (`{cur}{user.monthly_invest_budget:,.2f}`):* `{cur}{remaining_for_living:,.2f}` left for living expenses.",
            ])

    return "\n".join(lines)
