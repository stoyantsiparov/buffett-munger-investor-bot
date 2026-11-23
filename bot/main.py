"""Multi-User Warren Buffett & Charlie Munger Telegram Investment Bot & AI Agent.

Features:
  - 100% dynamic, non-hardcoded monthly investment plan calculated live from each user's Trading 212 pies, loans, and Revolut/cash emergency buffer.
  - Interactive Top-3 Investment Picker inside /check_alerts (🔔 Топ 3 & Аларми) so users can choose from today's Top 3 Watchlist stocks OR Top 3 underweight positions in their own Trading 212 portfolio.
  - Built-in conversational Buffett & Munger AI Agent (/ask or free-text questions in chat) with Gemini 4 Hydrogen + Google Search + full live portfolio & Watchlist context.
  - Shared, synchronized Watchlist across all friends with instant notifications on additions and weekly Monday price checks.
"""

import asyncio
import calendar
from datetime import datetime, time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.buffett_ai import (
    _get_gemini_api_key,
    analyze_stock_with_buffett,
    ask_buffett_agent,
    build_dynamic_investment_plan_lines,
    compute_quantitative_buffett_scorecard,
    evaluate_user_portfolio_undervalued,
    format_bg_ordinal,
    generate_monthly_check_report,
    rank_portfolio_buy_candidates,
    rank_top3_watchlist_opportunities,
    rank_unified_top3_opportunities,
)
from bot.crypto import encrypt_secret
from bot.db import get_session, init_db
from bot.market_service import check_watchlist_alerts, fetch_stock_fundamentals
from bot.models import PortfolioSnapshot, User, UserLoan, WatchlistItem
from bot.seed_owner import seed_default_watchlist_for_user, seed_owner_profile
from bot.t212_service import SEMICONDUCTOR_SHORT_TICKERS, format_portfolio_telegram, parse_t212_snapshot, sync_user_portfolio

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("buffett_bot")


def get_reply_keyboard(user: User | None = None) -> ReplyKeyboardMarkup:
    day = user.payday_day if user else 1
    suffix = format_bg_ordinal(day)
    return ReplyKeyboardMarkup(
        [
            ["📊 Портфейл (T212)", f"📅 Месечен отчет ({suffix})"],
            ["🎯 Watchlist", "🔔 Топ 3 & Аларми"],
            ["🤖 Бъфет Агент", "💳 Кредит & Бюджет"],
            ["🔑 Свържи Trading 212", "ℹ️ Помощ"],
        ],
        resize_keyboard=True,
    )


async def safe_reply(message, text: str, reply_markup=None) -> None:
    """Send a Telegram message with Markdown, falling back to plain text if Markdown entities fail."""
    chunks = [text[i : i + 3950] for i in range(0, len(text), 3950)] or [text]
    for idx, chunk in enumerate(chunks):
        rm = reply_markup if idx == len(chunks) - 1 else None
        try:
            await message.reply_text(
                chunk,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=rm,
            )
        except BadRequest:
            await message.reply_text(
                chunk.replace("*", "").replace("`", "").replace("_", ""),
                reply_markup=rm,
            )


async def safe_edit(message, text: str, reply_markup=None) -> None:
    """Edit a Telegram message safely with Markdown fallback and automatic overflow chunking."""
    chunks = [text[i : i + 3950] for i in range(0, len(text), 3950)] or [text]
    first_rm = reply_markup if len(chunks) == 1 else None
    try:
        await message.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN, reply_markup=first_rm)
    except BadRequest:
        await message.edit_text(
            chunks[0].replace("*", "").replace("`", "").replace("_", ""),
            reply_markup=first_rm,
        )
    for idx, extra in enumerate(chunks[1:], 1):
        rm = reply_markup if idx == len(chunks) - 1 else None
        try:
            await message.chat.send_message(extra, parse_mode=ParseMode.MARKDOWN, reply_markup=rm)
        except BadRequest:
            await message.chat.send_message(
                extra.replace("*", "").replace("`", "").replace("_", ""),
                reply_markup=rm,
            )


async def safe_send(bot, chat_id: int, text: str, reply_markup=None) -> None:
    """Send a Telegram message via bot.send_message safely with chunking and Markdown fallback."""
    chunks = [text[i : i + 3950] for i in range(0, len(text), 3950)] or [text]
    for idx, chunk in enumerate(chunks):
        rm = reply_markup if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=rm,
            )
        except BadRequest:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk.replace("*", "").replace("`", "").replace("_", ""),
                reply_markup=rm,
            )


def get_master_watchlist_owner_id(session) -> int:
    """Return the owner user_id (1) that holds the shared synchronized Watchlist."""
    owner = session.query(User).order_by(User.id.asc()).first()
    return owner.id if owner else 1


def get_or_register_user(tg_user) -> User:
    """Find user by telegram_id, or bind the seeded owner row on first /start, or create a blank friend profile."""
    with get_session() as session:
        user = session.query(User).filter(User.telegram_id == tg_user.id).first()
        if user:
            updated = False
            if tg_user.username and user.username != tg_user.username:
                user.username = tg_user.username
                updated = True
            full_name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
            if full_name and user.full_name != full_name:
                user.full_name = full_name
                updated = True
            if updated:
                session.commit()
            return user

        unclaimed_owner = session.query(User).filter(User.telegram_id == 0).first()
        if unclaimed_owner:
            unclaimed_owner.telegram_id = tg_user.id
            unclaimed_owner.username = tg_user.username
            unclaimed_owner.full_name = (
                f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
                or unclaimed_owner.full_name
            )
            unclaimed_owner.payday_day = 13
            session.commit()
            session.refresh(unclaimed_owner)
            logger.info("Bound seeded owner profile (payday=13) to Telegram ID %s", tg_user.id)
            return unclaimed_owner

        new_user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip() or "Инвеститор",
            monthly_income=0.0,
            monthly_invest_budget=0.0,
            emergency_buffer_target=500.0,
            emergency_buffer_current=0.0,
            payday_day=1,
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        logger.info("Created new friend profile (payday=1, blank budget/loans) for Telegram ID %s", tg_user.id)
        return new_user


def format_watchlist_telegram(session) -> tuple[str, InlineKeyboardMarkup]:
    """Render the shared, synchronized Watchlist visible to all friends."""
    master_id = get_master_watchlist_owner_id(session)
    items = (
        session.query(WatchlistItem)
        .filter(WatchlistItem.user_id == master_id)
        .order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc())
        .all()
    )
    if not items:
        owner = session.query(User).filter(User.id == master_id).first()
        if owner:
            seed_default_watchlist_for_user(session, owner)
            items = (
                session.query(WatchlistItem)
                .filter(WatchlistItem.user_id == master_id)
                .order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc())
                .all()
            )

    lines = [
        "🎯 *ОБЩ WATCHLIST (БЪФЕТ & МЪНГЪР — СИНХРОНИЗИРАН ЗА ВСИЧКИ)*",
        "_Когато някой от нас добави компания тук, тя се вижда от всички и следим цената ѝ заедно!_",
        "",
    ]

    for w in items:
        sym = "$" if w.currency == "USD" else ("p " if w.currency == "GBX" else "€")
        price_str = f"{sym}{w.last_price:,.2f}" if w.last_price is not None else "N/A"
        target_str = f"{sym}{w.target_buy_price:,.2f}"
        iv_str = (
            f"{sym}{w.intrinsic_value_low:,.0f}–{sym}{w.intrinsic_value_high:,.0f}"
            if (w.intrinsic_value_low and w.intrinsic_value_high)
            else "N/A"
        )

        if w.last_price is not None and w.last_price <= w.target_buy_price:
            badge = "🟢 *КУПИ СЕГА*"
        elif w.last_price is not None and w.last_price <= w.target_buy_price * 1.15:
            badge = "🟡 *Близо*"
        else:
            badge = "⏳"

        added_by = ""
        if w.notes and w.notes.startswith("👤"):
            added_by = f" | _{w.notes.split('|')[0].strip()}_"

        lines.append(
            f"{badge} *#{w.rank_order} `{w.ticker}`* ({w.company_name}){added_by}\n"
            f"   └ Цена: `{price_str}` | Target Buy: `<= {target_str}` | IV: `{iv_str}`"
        )

    lines.extend([
        "",
        "🤝 *Как да споделиш нова компания с всички в Watchlist-а:*",
        "• `/add_watch ТИКЪР` — Ботът сам изчислява Вътрешната стойност и Target Buy по Бъфет и известява всички!",
        "• `/add_watch ТИКЪР TARGET_PRICE [IV_LOW] [IV_HIGH]` — Добавяне с твоя целева цена",
        "• `/del_watch ТИКЪР` — Премахване на акция от общия списък",
        "• `/buffett ТИКЪР` — Пълен Бъфет анализ на акцията",
    ])

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔔 Топ 3 днес & Цени на живо", callback_data="act_check_alerts"),
                InlineKeyboardButton("🤖 Попитай Агента", callback_data="agent_top3"),
            ],
            [
                InlineKeyboardButton("🧠 INTU", callback_data="buffett_INTU"),
                InlineKeyboardButton("🧠 FDS", callback_data="buffett_FDS"),
                InlineKeyboardButton("🧠 CRM", callback_data="buffett_CRM"),
                InlineKeyboardButton("🧠 KO", callback_data="buffett_KO"),
            ],
        ]
    )
    return "\n".join(lines), kb


def build_top3_and_alerts_report(
    session,
    user: User,
    triggered: list[WatchlistItem],
    latest_snap: PortfolioSnapshot | None,
    portfolio_evals: list[dict] | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build detailed Buffett Buy Alerts (Watchlist + User's Own Portfolio) + Unified Head-to-Head Top 3 Picker."""
    parsed_snap = None
    if latest_snap and latest_snap.raw_json:
        try:
            parsed_snap = parse_t212_snapshot(json.loads(latest_snap.raw_json))
        except Exception:
            parsed_snap = None

    unified = rank_unified_top3_opportunities(session, user, latest_snap, portfolio_evals)
    unified_top3 = unified["top3"]
    ranked_port = rank_portfolio_buy_candidates(parsed_snap)

    lines: list[str] = []

    # 1. Detailed Buy Alerts in Shared Watchlist
    if triggered:
        lines.extend([
            "🔔 *АЛАРМА ЗА ПОКУПКА В ОБЩИЯ БЪФЕТ WATCHLIST!*",
            "Следните компании са в или под Целевата цена за покупка (Target Buy) с Марж на безопасност:",
            "",
        ])
        for w in triggered[:6]:
            sym = "$" if w.currency == "USD" else ("p " if w.currency == "GBX" else "€")
            discount = (
                round(((w.intrinsic_value_high - w.last_price) / w.intrinsic_value_high) * 100.0, 1)
                if (w.intrinsic_value_high and w.last_price)
                else w.margin_of_safety_pct
            )
            lines.extend([
                f"🟢 *#{w.rank_order} `{w.ticker}` ({w.company_name})*",
                f"   • Текуща цена: `{sym}{w.last_price:,.2f}` <= Target Buy: `{sym}{w.target_buy_price:,.2f}`",
                f"   • Вътрешна стойност: `{sym}{w.intrinsic_value_low or 0:,.0f} – {sym}{w.intrinsic_value_high or 0:,.0f}` (Отстъпка: `+{discount:.1f}%`)",
                f"   • Ров: _{w.moat_summary or 'Широк ров'}_",
                "",
            ])
        if len(triggered) > 6:
            lines.append(f"_...и още {len(triggered) - 6} компании в зоната за покупка (виж 🎯 Watchlist)._")
            lines.append("")
    else:
        lines.extend([
            "🔔 *ПРОВЕРКА НА ЦЕНОВИ АЛАРМИ (WATCHLIST + ТВОЯТ ПОРТФЕЙЛ)*",
            "_В момента няма нова акция от общия Watchlist под Target Buy цената._",
            "",
        ])

    # 2. Detailed Undervalued Alerts in the User's OWN Trading 212 Portfolio!
    if portfolio_evals:
        buy_zone_port = [p for p in portfolio_evals if p["in_buy_zone"] and not p["sector_warn"]]
        fair_disc_port = [p for p in portfolio_evals if (not p["in_buy_zone"]) and p["is_undervalued"] and not p["sector_warn"]]
        blocked_under_port = [p for p in portfolio_evals if p["is_undervalued"] and p["sector_warn"]]

        snap_ts = (
            f" _(данни: {latest_snap.created_at.strftime('%d.%m.%Y %H:%M')})_"
            if (latest_snap and latest_snap.created_at)
            else ""
        )
        lines.append(f"💼 *ПОДЦЕНЕНИ КОМПАНИИ В ТВОЯ ПОРТФЕЙЛ (TRADING 212):*{snap_ts}")
        shown_port = (buy_zone_port + fair_disc_port)[:4]
        if shown_port:
            for p in shown_port:
                sym = p["currency_sym"]
                badge = "🟢" if p["in_buy_zone"] else "🟡"
                cmp_op = "<=" if p["in_buy_zone"] else "vs"
                lines.extend([
                    f"{badge} *`{p['ticker']}` ({p['company_name']})* _(Дял: {p['cur_pct']:.1f}% / цел {p['exp_pct']:.0f}%)_",
                    f"   • Текуща цена: `{sym}{p['last_price']:,.2f}` {cmp_op} Target Buy: `{sym}{p['target_buy_price']:,.2f}`",
                    f"   • Вътрешна стойност: `{sym}{p['iv_low']:,.0f} – {sym}{p['iv_high']:,.0f}` (Отстъпка: `+{p['discount_pct']:.1f}%`)",
                    f"   • Ров: _{p['moat_summary']}_",
                ])
        else:
            lines.append("  • _Всички не-чипови акции в портфейла ти са около или над консервативната си вътрешна стойност днес._")

        if blocked_under_port:
            b_str = ", ".join(f"`{p['ticker']}` (+{p['discount_pct']:.0f}%)" for p in blocked_under_port)
            lines.append(f"  • ⚠️ _Подценени, но блокирани от лимита за полупроводници (>20%):_ {b_str}")
        lines.append("")
    elif not user.t212_api_key_enc:
        lines.extend([
            "💼 *ТВОЯТ ПОРТФЕЙЛ:* _Още не си свързал Trading 212 (`/connect_t212`), затова класирането долу е само от общия Watchlist._",
            "",
        ])

    # 3. UNIFIED HEAD-TO-HEAD TOP 3 (Watchlist vs. User's Own Portfolio!)
    if unified_top3:
        lines.append("🏆 *ОБЩ ТОП 3 ЗА ИНВЕСТИЦИЯ ДНЕС (WATCHLIST vs. ТВОЯТ ПОРТФЕЙЛ):*")
        for idx, item in enumerate(unified_top3, 1):
            sym = item["currency_sym"]
            roic_str = f" | ROIC `{item['roic_pct']:.0f}%`" if item["roic_pct"] else ""
            warn_str = f" ⚠️ _{item['sector_warn']}_" if item["sector_warn"] else ""
            lines.append(
                f"  {idx}️⃣ *`{item['ticker']}`* — *{item['source_badge']}*\n"
                f"     └ `{sym}{item['last_price']:,.2f}` | IV `{sym}{item['iv_low']:,.0f}–{sym}{item['iv_high']:,.0f}`{roic_str} — {item['status_tag']}{warn_str}"
            )
        if unified["comparison_insight"]:
            lines.append(f"💡 *Извод:* {unified['comparison_insight']}")
        lines.append("")

    if user.emergency_buffer_current < user.emergency_buffer_target:
        missing = user.emergency_buffer_target - user.emergency_buffer_current
        lines.append(
            f"🛡️ *Ликвиден буфер:* `€{user.emergency_buffer_current:,.2f}` / `€{user.emergency_buffer_target:,.2f}` "
            f"_(липсват `€{missing:,.2f}`)_ — не забравяй да заделиш част за Revolut буфера!"
        )
        lines.append("")

    lines.append("👇 *Избери компания от Обединения Топ 3 за готов персонален план за покупка:*")

    buttons: list[list[InlineKeyboardButton]] = []
    if unified_top3:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{i}️⃣ {x['btn_icon']} {x['ticker']}",
                    callback_data=f"pick_{x['ticker']}",
                )
                for i, x in enumerate(unified_top3, 1)
            ]
        )

    # Also offer a 0%-tax broad EU ETF quick-pick if user holds one (e.g. VWCE) and it's not already in Top 3
    top3_tickers = {x["ticker"] for x in unified_top3}
    broad_etf_pick = next(
        (c for c in ranked_port["top_overall"] if c.get("is_broad_etf") and c["ticker"] not in top3_tickers),
        None,
    )
    row2 = [
        InlineKeyboardButton("🤖 Сравни: Нова vs Портфейл", callback_data="agent_top3"),
        InlineKeyboardButton("🔄 Синхронизирай T212", callback_data="act_sync_t212"),
    ]
    if broad_etf_pick:
        buttons.append(
            [InlineKeyboardButton(f"🇪🇺 0% Данък ETF: {broad_etf_pick['ticker']}", callback_data=f"pick_{broad_etf_pick['ticker']}")]
        )
    buttons.append(row2)
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        has_gemini = bool(_get_gemini_api_key(user=db_user, session=session))
        user = db_user

    has_t212 = "✅ Свързан (Read-Only)" if user.t212_api_key_enc else "⚠️ Не е свързан (`/connect_t212`)"
    gemini_status = "✅ Активен (Gemini AI + живи новини)" if has_gemini else "💡 Количествен режим _(активирай AI с `/set_gemini_key`)_"
    day_suffix = format_bg_ordinal(user.payday_day)

    if user.monthly_income > 0 or user.monthly_invest_budget > 0:
        buf_warn = (
            f"⚠️ _(липсват `€{user.emergency_buffer_target - user.emergency_buffer_current:,.2f}`)_"
            if user.emergency_buffer_current < user.emergency_buffer_target
            else "✅"
        )
        budget_info = (
            f"💵 *Нетен месечен доход:* `€{user.monthly_income:,.2f}`\n"
            f"📈 *Месечен бюджет за инвестиции/буфер:* `€{user.monthly_invest_budget:,.2f}` (Отчет: {day_suffix} число)\n"
            f"🛡️ *Ликвиден буфер:* `€{user.emergency_buffer_current:,.2f}` / цел `€{user.emergency_buffer_target:,.2f}` {buf_warn}"
        )
    else:
        budget_info = (
            f"📅 *Месечен отчет:* Автоматично на *{day_suffix} число на месеца* _(сменя се с `/set_payday`)_\n"
            "💡 *Твоят бюджет и кредити са празни (100% лични за теб).* Натисни `💳 Кредит & Бюджет` или `ℹ️ Помощ`, за да настроиш своите суми!"
        )

    welcome = (
        f"Здравей, *{user.full_name}*! 👋\n\n"
        "Добре дошъл в интелигентния инвестиционен агент по системата на *Уорън Бъфет и Чарли Мънгър*!\n\n"
        "🏆 *Обединен Топ 3 (Watchlist vs. Твоят портфейл):* Ботът сравнява на живо *подценените компании в твоя портфейл* срещу *общия Watchlist*, за да знаеш дали днес да подсилиш своя позиция или да добавиш нова!\n"
        "🔒 *100% Лични финанси:* Твоят Trading 212 портфейл, бюджет, буфер, кредити и API ключове са криптирани и видими САМО за теб.\n"
        "🤝 *Общ Watchlist:* Списъкът в `🎯 Watchlist` е общ за цялата ни група!\n\n"
        f"🔐 *Твоят Trading 212:* {has_t212}\n"
        f"🤖 *Бъфет AI Агент:* {gemini_status}\n"
        f"{budget_info}\n\n"
        "Избери бутон от менюто долу, напиши тикър (напр. `INTU`) или ми задай свободен въпрос направо в чата!"
    )
    await safe_reply(update.message, welcome, reply_markup=get_reply_keyboard(user))

    # For new friends who haven't finished setup yet, offer a clean 1-tap onboarding card
    if not user.t212_api_key_enc or (user.monthly_income == 0 and user.monthly_invest_budget == 0):
        quick_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔑 Свържи Trading 212", callback_data="act_connect_t212"),
                    InlineKeyboardButton("💳 Настрой Бюджет", callback_data="act_budget_help"),
                ],
                [
                    InlineKeyboardButton("🤖 Сложи Gemini AI ключ", callback_data="act_gemini_help"),
                    InlineKeyboardButton("🔔 Виж Топ 3 днес", callback_data="act_check_alerts"),
                ],
            ]
        )
        await safe_reply(
            update.message,
            "🚀 *Бърз старт за 60 секунди — избери стъпка:*",
            reply_markup=quick_kb,
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    day_suffix = format_bg_ordinal(user.payday_day)
    text = (
        "📘 *ПОМОЩ: КАК РАБОТИ БОТЪТ И КАК ДА ГО ИЗПОЛЗВАШ*\n\n"
        "Този бот е твоят личен инвестиционен агент по системата на *Уорън Бъфет и Чарли Мънгър*. Всички препоръки се изчисляват *динамично на живо* според текущите цени и твоя портфейл.\n\n"
        "🔒 *1. ТВОИТЕ ЛИЧНИ ДАННИ (Видими САМО за теб)*\n"
        "  • *Свържи Trading 212 портфейл (100% безопасно, Read-Only):*\n"
        "    От Trading 212 -> *Settings -> API (Beta) -> Generate API Key* (само отметки за четене!) и напиши:\n"
        "    `/connect_t212 ТВОЯТ_KEY ТВОЯТ_SECRET`\n"
        "    _(Ключът се криптира с AES-128 и съобщението ти се трие веднага от чата!)_\n"
        "  • *Настрой своя бюджет и буфер:*\n"
        "    `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР` _(напр._ `/set_budget 1200 150 500`_)_\n"
        "    За бърза промяна само на буфера: `/set_buffer СУМА` _(напр._ `/set_buffer 200`_)_\n"
        "  • *Промени деня за автоматичния месечен отчет:*\n"
        "    `/set_payday ДЕН` _(за нови потребители по подразбиране е 1-во число; твоят текущ ден е_ "
        f"*{day_suffix} число*_)_\n"
        "  • *Ако имаш кредит (Инверсия на Мънгър):*\n"
        "    По подразбиране нямаш въведени кредити. Ако имаш, въведи го с:\n"
        "    `/set_loan БАНКА ГЛАВНИЦА ГПР% РЕДОВНА_ВНОСКА ПРЕДСРОЧНА_ВНОСКА`\n"
        "    _(При главница `0` кредитът се изтрива)._\n\n"
        "🏆 *2. ОБЕДИНЕН ТОП 3 (WATCHLIST vs. ТВОЯТ ПОРТФЕЙЛ) & АЛАРМИ*\n"
        "  • Натисни *🔔 Топ 3 & Аларми* (`/check_alerts`) — ботът проверява на живо *и общия Watchlist, и акциите в твоя Trading 212 портфейл*, след което ги сравнява директно в *Общ Топ 3*, за да знаеш дали да подсилиш своя подценена акция (`💼`) или да добавиш нова (`🎯`).\n"
        "  • *Веднъж на 1 седмица* (всеки понеделник) ботът праща автоматичен преглед за всички.\n"
        "  • Сподели нова компания с групата в *🎯 Watchlist*: `/add_watch ТИКЪР` _(ботът сам изчислява IV и Target Buy и известява всички!)_\n"
        "  • За 10-точков анализ напиши директно тикър (напр. `INTU`, `KO`, `META`) или `/buffett ТИКЪР`.\n\n"
        "🤖 *3. GEMINI AI АГЕНТ (БЕЗПЛAТЕН КЛЮЧ НАПРАВО В TELEGRAM)*\n"
        "  • Всеки потребител може да активира собствен безплатен AI агент с живи новини (не ти трябва достъп до сървъра!):\n"
        "    1) Отвори `aistudio.google.com/apikey` и влез с Google.\n"
        "    2) Натисни *Create API key* → в _Choose an imported project_ избери *Default Gemini Project* (или създай нов) и му дай име (напр. `Buffett Bot`).\n"
        "    3) Копирай ключа и напиши тук: `/set_gemini_key ТВОЯТ_КЛЮЧ`\n"
        "  • _(Съобщението с ключа се трие веднага и се пази криптирано само за теб! Ако собственикът на бота вече е добавил ключ, AI работи автоматично за всички.)_"
    )
    await safe_reply(update.message, text, reply_markup=get_reply_keyboard(user))


async def cmd_set_gemini_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow ANY user (owner or friend) to set their own encrypted free Gemini API key directly in Telegram."""
    user = get_or_register_user(update.effective_user)
    args = context.args or []

    if len(args) < 1:
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            has_own = bool(db_user.gemini_api_key_enc)
            has_any = bool(_get_gemini_api_key(user=db_user, session=session))
        status = (
            "✅ Имаш записан собствен криптиран Gemini ключ."
            if has_own
            else (
                "✅ AI Агентът вече е активен (ползва споделения ключ на бота). Можеш да сложиш и свой личен ключ за отделна квота."
                if has_any
                else "⚠️ Все още нямаш добавен Gemini API ключ."
            )
        )
        msg = (
            "🤖 *НАСТРОЙКА НА GEMINI AI API КЛЮЧ (БЕЗПЛАТНО, БЕЗ `.env`)*\n\n"
            f"*Текущ статус:* {status}\n\n"
            "*Как да вземеш безплатен ключ за 30 секунди:*\n"
            "1️⃣ Отвори `aistudio.google.com/apikey` и влез с Google акаунт.\n"
            "2️⃣ Натисни *Create API key*:\n"
            "   • *Name your key:* напр. `Buffett Bot`\n"
            "   • *Choose an imported project:* избери **`Default Gemini Project`** _(или натисни `+ Create project`)_\n"
            "3️⃣ Копирай ключа (започва с `AIza...`) и изпрати тук:\n"
            "`/set_gemini_key ТВОЯТ_API_KEY`\n\n"
            "🛡️ *Сигурност:* Веднага след изпращане ботът *изтрива съобщението ти от чата* и записва ключа криптиран с `AES-128 Fernet`.\n"
            "_(За премахване на личния ключ напиши:_ `/set_gemini_key off`_)._"
        )
        await safe_reply(update.effective_message, msg)
        return

    raw_key = args[0].strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    if raw_key.lower() in ("off", "delete", "clear", "remove", "0"):
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            db_user.gemini_api_key_enc = None
            session.commit()
        await update.effective_chat.send_message("🗑️ Личният ти Gemini API ключ беше премахнат.")
        return

    if len(raw_key) < 20:
        await update.effective_chat.send_message(
            "❌ Ключът изглежда твърде кратък или невалиден. Копирай пълния ключ от Google AI Studio (започва с AIza...) и опитай пак с /set_gemini_key КЛЮЧ."
        )
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.gemini_api_key_enc = encrypt_secret(raw_key)
        session.commit()
        owner = session.query(User).order_by(User.id.asc()).first()
        is_owner = owner is not None and owner.id == db_user.id

    shared_note = (
        "\n🤝 _Тъй като си собственикът на бота, този ключ автоматично активира AI Агента и за твоите приятели (докато не си сложат свой собствен)!_"
        if is_owner
        else "\n🔒 _Ключът е криптиран и се ползва единствено за твоя профил._"
    )
    await safe_send(
        context.bot,
        update.effective_chat.id,
        "✅ *Gemini AI API ключът е запазен и криптиран успешно!*\n"
        "_(Съобщението с ключа беше изтрито от чата за твоя сигурност.)_"
        f"{shared_note}\n\n"
        "Вече можеш да натиснеш *🤖 Бъфет Агент* или просто да ми напишеш свободен въпрос в чата!",
    )


async def cmd_connect_t212(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    args = context.args or []

    if len(args) < 1:
        msg = (
            "🔑 *СВЪРЗВАНЕ НА ТВОЯ TRADING 212 ПОРТФЕЙЛ (READ-ONLY)*\n\n"
            "За да свържеш личния си портфейл, изпрати командата във формат:\n"
            "`/connect_t212 ТВОЯТ_API_KEY ТВОЯТ_API_SECRET`\n\n"
            "🛡️ *100% Сигурност и поверителност:*\n"
            "1. Създай ключ само с права за четене (*Read-Only*) от Trading 212 -> Settings -> API.\n"
            "2. Веднага след изпращане ботът *автоматично изтрива съобщението с ключа от чата* и го записва криптиран с `AES-128 Fernet` единствено в твоя личен профил."
        )
        await safe_reply(update.effective_message, msg)
        return

    api_key = args[0].strip()
    api_secret = args[1].strip() if len(args) >= 2 else ""
    env_mode = args[2].strip().lower() if len(args) >= 3 else "live"
    if env_mode not in ("live", "demo"):
        env_mode = "live"

    try:
        await update.message.delete()
    except Exception:
        pass

    status_msg = await update.effective_chat.send_message(
        "🔐 Ключовете са криптирани успешно! Тествам връзката с Trading 212 API (отнема ~8 секунди)..."
    )

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.t212_api_key_enc = encrypt_secret(api_key)
        db_user.t212_api_secret_enc = encrypt_secret(api_secret) if api_secret else None
        db_user.t212_env = env_mode
        session.commit()

        try:
            snap = await asyncio.to_thread(sync_user_portfolio, session, db_user)
            report = format_portfolio_telegram(snap)
            await safe_edit(
                status_msg,
                f"✅ *Твоят Trading 212 портфейл е свързан и синхронизиран успешно!*\n\n{report}",
            )
        except Exception as e:
            await safe_edit(
                status_msg,
                f"⚠️ Ключовете са записани, но възникна грешка при извличане от Trading 212 API:\n`{e}`\n\n"
                "Провери дали ключът е активен и има Read-Only права за Account, Portfolio и Pies.",
            )


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    with get_session() as session:
        snap = (
            session.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.user_id == user.id)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        if not snap:
            if not user.t212_api_key_enc:
                await safe_reply(
                    update.message,
                    "⚠️ Все още нямаш свързан личен Trading 212 портфейл.\n"
                    "Използвай `/connect_t212 API_KEY API_SECRET`, за да го свържеш!",
                )
                return
            wait_msg = await update.message.reply_text("🔄 Извличам твоя портфейл на живо от Trading 212 API...")
            try:
                db_user = session.query(User).filter(User.id == user.id).first()
                snap = await asyncio.to_thread(sync_user_portfolio, session, db_user)
            except Exception as e:
                await safe_edit(wait_msg, f"❌ Грешка при връзка с Trading 212: `{e}`")
                return
            await wait_msg.delete()

        report = format_portfolio_telegram(snap)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Обнови от Trading 212 на живо", callback_data="act_sync_t212"),
                    InlineKeyboardButton("🏆 Топ 3 за покупка днес", callback_data="act_check_alerts"),
                ],
                [
                    InlineKeyboardButton("📅 Месечен отчет", callback_data="act_monthly_check"),
                    InlineKeyboardButton("🤖 Попитай Агента за портфейла", callback_data="agent_portfolio"),
                ],
            ]
        )
        await safe_reply(update.message, report, reply_markup=kb)


async def cmd_monthly_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        report = generate_monthly_check_report(session, db_user)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🏆 Избери от Топ 3 днес", callback_data="act_check_alerts"),
                InlineKeyboardButton("🔄 Синхронизирай T212", callback_data="act_sync_t212"),
            ],
            [
                InlineKeyboardButton("🎯 Watchlist", callback_data="act_watchlist"),
                InlineKeyboardButton("🤖 Попитай Бъфет Агента", callback_data="agent_top3"),
            ],
        ]
    )
    await safe_reply(update.message, report, reply_markup=kb)


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    get_or_register_user(update.effective_user)
    with get_session() as session:
        text, kb = format_watchlist_telegram(session)
    await safe_reply(update.message, text, reply_markup=kb)


def _run_watchlist_check_isolated() -> list[int]:
    """Thread-safe wrapper: runs live Watchlist price refresh in its own SQLAlchemy session and returns triggered IDs."""
    with get_session() as s:
        items = check_watchlist_alerts(s, None, True)
        return [w.id for w in items]


def _run_portfolio_eval_isolated(user_id: int) -> list[dict]:
    """Thread-safe wrapper: evaluates a user's held T212 single stocks in its own SQLAlchemy session."""
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if not u:
            return []
        snap = (
            s.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.user_id == u.id)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        return evaluate_user_portfolio_undervalued(s, u, snap)


async def cmd_check_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    chat = update.effective_chat
    wait_msg = await chat.send_message(
        "🔎 Проверявам живите цени и Вътрешната стойност на общия Watchlist И на компаниите в твоя портфейл (отнема ~5 сек.)..."
    )
    triggered_ids, portfolio_evals = await asyncio.gather(
        asyncio.to_thread(_run_watchlist_check_isolated),
        asyncio.to_thread(_run_portfolio_eval_isolated, user.id),
    )
    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        latest_snap = (
            session.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.user_id == db_user.id)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        triggered = (
            session.query(WatchlistItem)
            .filter(WatchlistItem.id.in_(triggered_ids))
            .order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc())
            .all()
            if triggered_ids
            else []
        )
        text, kb = build_top3_and_alerts_report(session, db_user, triggered, latest_snap, portfolio_evals)
    await safe_edit(wait_msg, text, reply_markup=kb)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Interactive Buffett & Munger Agent menu or direct question answering."""
    user = get_or_register_user(update.effective_user)
    question = " ".join(context.args or []).strip()

    if not question:
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            has_gemini = bool(_get_gemini_api_key(user=db_user, session=session))
        ai_badge = "✅ *Gemini AI активен* (живи новини + задълбочен анализ)" if has_gemini else "💡 *Количествен режим* _(за свободен AI чат с новини сложи безплатен ключ с `/set_gemini_key`)_"
        rows = [
            [InlineKeyboardButton("🏆 Сравни Топ 3 днес (Нова vs Портфейл)", callback_data="agent_top3")],
            [InlineKeyboardButton("📊 Кои позиции от портфейла ми да подсиля?", callback_data="agent_portfolio")],
            [InlineKeyboardButton("🛡️ Как да разпределя парите между буфер, кредит и акции?", callback_data="agent_budget")],
            [InlineKeyboardButton("🧠 Анализ на конкретна акция (INTU / META / KO)", callback_data="act_buffett_menu")],
        ]
        if not has_gemini:
            rows.append([InlineKeyboardButton("🔑 Как да активирам безплатен Gemini AI ключ?", callback_data="act_gemini_help")])
        kb = InlineKeyboardMarkup(rows)
        await safe_reply(
            update.message,
            "🤖 *БЪФЕТ & МЪНГЪР ИНВЕСТИЦИОНЕН АГЕНТ*\n\n"
            f"{ai_badge}\n\n"
            "Аз виждам актуалния ти Trading 212 портфейл, ликвидния ти буфер, кредитите и общия Watchlist на живо.\n\n"
            "Избери готов въпрос долу *или просто ми напиши свободен въпрос направо в чата* "
            "(напр. _„Да подсиля ли META или да купя нова позиция в INTU?“_):",
            reply_markup=kb,
        )
        return

    await _run_agent_question(update.effective_chat, user, question)


async def _run_agent_question(chat, user: User, question: str) -> None:
    wait_msg = await chat.send_message(
        "🤖 Анализирам портфейла ти, буфера, кредитите и живите пазарни данни по системата на Бъфет и Мънгър..."
    )
    try:
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            latest_snap = (
                session.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.user_id == db_user.id)
                .order_by(PortfolioSnapshot.created_at.desc())
                .first()
            )
            answer = await asyncio.to_thread(ask_buffett_agent, session, db_user, question, latest_snap)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🏆 Избери от Топ 3 днес", callback_data="act_check_alerts"),
                    InlineKeyboardButton("📅 Месечен отчет", callback_data="act_monthly_check"),
                ]
            ]
        )
        await safe_edit(wait_msg, answer, reply_markup=kb)
    except Exception as e:
        await safe_edit(wait_msg, f"❌ Грешка при обработка на въпроса: `{e}`")


async def _handle_pick_stock(chat, user: User, ticker: str) -> None:
    """When a user taps 'Избери <TICKER>' from Top 3, build a personalized Buy Ticket."""
    wait_msg = await chat.send_message(
        f"🎯 Подготвям персонален план за покупка на *{ticker}* според твоя бюджет и портфейл...",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        metrics = await asyncio.to_thread(fetch_stock_fundamentals, ticker)
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            master_id = get_master_watchlist_owner_id(session)
            watch_item = (
                session.query(WatchlistItem)
                .filter(WatchlistItem.user_id == master_id, WatchlistItem.ticker == ticker)
                .first()
            )
            latest_snap = (
                session.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.user_id == db_user.id)
                .order_by(PortfolioSnapshot.created_at.desc())
                .first()
            )
            if watch_item and metrics.get("price"):
                watch_item.last_price = metrics["price"]
                session.commit()

            score = compute_quantitative_buffett_scorecard(metrics, watch_item)
            cur_sym = "$" if metrics.get("currency") == "USD" else ("p " if metrics.get("currency") == "GBX" else "€")
            price = metrics.get("price") or (watch_item.last_price if watch_item else 0.0) or 0.0
            inv_budget = float(db_user.monthly_invest_budget or 0.0)
            buf_missing = max(0.0, db_user.emergency_buffer_target - db_user.emergency_buffer_current)
            investable = round(max(0.0, inv_budget - min(buf_missing, max(50.0, inv_budget * 0.45))), 2) if (buf_missing > 0 and inv_budget > 0) else inv_budget
            suggested_eur = round(max(20.0, investable * 0.35), 2) if investable > 0 else 50.0

            is_chip = ticker.upper() in SEMICONDUCTOR_SHORT_TICKERS
            semi_pct = float(latest_snap.semiconductor_pct) if latest_snap else 0.0

            warnings = []
            if is_chip and semi_pct >= 20.0:
                warnings.append(f"⚠️ *Секторно предупреждение:* Полупроводниците ти са `{semi_pct:.1f}%` (>20% таван). Помисли дали да не избереш не-чипова компания!")
            if buf_missing > 0:
                warnings.append(f"🛡️ *Буфер:* Ликвидният ти буфер е `€{db_user.emergency_buffer_current:,.2f}` / `€{db_user.emergency_buffer_target:,.2f}` (липсват `€{buf_missing:,.2f}`).")

            is_eu_etf = ticker.upper() in {"VWCE", "SEC0", "XDWU", "ESIF", "BGX", "IWDA", "SXR8", "CSPX"}
            tax_note = "0% данък капиталова печалба в България (регулиран пазар в ЕС/ЕИП)" if is_eu_etf else "10% данък капиталова печалба в БГ (САЩ/извън ЕС) и 10% удържан данък дивидент в САЩ"

            # Check whether the user ALREADY owns this stock in Trading 212
            held_status_line = "🎯 *Статус в портфейла ти:* Нова компания (все още не я притежаваш — подходяща за добавяне към US пай или като нова позиция)."
            if latest_snap and latest_snap.raw_json:
                try:
                    parsed = parse_t212_snapshot(json.loads(latest_snap.raw_json))
                    for pie in parsed.get("pies") or []:
                        for inst in pie.get("instruments") or []:
                            if inst["ticker"].upper() == ticker.upper():
                                held_status_line = (
                                    f"💼 *Статус в портфейла ти:* Вече я притежаваш в *{pie['name']}* — "
                                    f"текуща стойност `€{inst['value']:,.2f}` (дял `{inst['current_pct']:.1f}%` / цел `{inst['expected_pct']:.0f}%`). "
                                    "Можеш да я подсилиш на отстъпка!"
                                )
                                break
                except Exception:
                    pass

            lines = [
                f"🎯 *ИЗБРАНА ПОЗИЦИЯ ЗА ИНВЕСТИЦИЯ: `{ticker}` ({metrics.get('name', ticker)})*",
                "",
                held_status_line,
                f"• *Жива цена:* `{cur_sym}{price:,.2f}`",
                f"• *Вътрешна стойност (IV по Бъфет):* `{cur_sym}{score['iv_low']:,.2f} – {cur_sym}{score['iv_high']:,.2f}`",
                f"• *Целева цена за покупка (Target Buy):* `<= {cur_sym}{score['target_buy']:,.2f}`",
                f"• *Присъда:* *{score['verdict']}*",
                f"• *Данък (България):* _{tax_note}_",
                "",
                f"💶 *Предложена сума за вход от месечния ти бюджет:* *`€{suggested_eur:,.2f}`* _(от наличните `€{investable:,.2f}` за акции след резервиране за буфер)_",
            ]
            if warnings:
                lines.append("")
                lines.extend(warnings)

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(f"✅ Потвърждавам избора: {ticker}", callback_data=f"confirm_{ticker}_{int(suggested_eur)}"),
                        InlineKeyboardButton(f"🧠 Пълен Бъфет анализ", callback_data=f"buffett_{ticker}"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Обратно към Топ 3", callback_data="act_check_alerts"),
                    ],
                ]
            )
        await safe_edit(wait_msg, "\n".join(lines), reply_markup=kb)
    except Exception as e:
        await safe_edit(wait_msg, f"❌ Грешка при избор на *{ticker}*: `{e}`")


async def cmd_add_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    args = context.args or []
    if len(args) < 1:
        await safe_reply(
            update.effective_message,
            "🤝 *ДОБАВЯНЕ НА АКЦИЯ В ОБЩИЯ WATCHLIST (ЗА ВСИЧКИ)*\n\n"
            "Можеш да добавиш акция по два начина:\n"
            "1️⃣ *Автоматична Бъфет оценка (само с тикър):*\n"
            "`/add_watch ТИКЪР` _(напр._ `/add_watch AAPL` _— ботът сам изчислява Вътрешната стойност и Target Buy цената!)_\n\n"
            "2️⃣ *С твоя целева цена за покупка:*\n"
            "`/add_watch ТИКЪР TARGET_BUY [IV_LOW] [IV_HIGH]`\n"
            "_(Пример:_ `/add_watch MSFT 380 450 520`_)_\n\n"
            "🔔 _Веднага щом я добавиш, всички приятели в бота ще я видят в общия Watchlist и ще получат известие!_",
        )
        return

    ticker = args[0].strip().upper()
    wait_msg = await update.effective_message.reply_text(
        f"🔎 Извличам финансови данни и Бъфет оценка за *{ticker}*...",
        parse_mode=ParseMode.MARKDOWN,
    )
    metrics = await asyncio.to_thread(fetch_stock_fundamentals, ticker)
    score = compute_quantitative_buffett_scorecard(metrics, None)

    if len(args) >= 2:
        try:
            target_buy = float(args[1].replace(",", "."))
            iv_low = float(args[2].replace(",", ".")) if len(args) >= 3 else round(target_buy * 1.25, 2)
            iv_high = float(args[3].replace(",", ".")) if len(args) >= 4 else round(target_buy * 1.45, 2)
        except ValueError:
            await safe_edit(wait_msg, "❌ Цените трябва да са валидни числа (напр. `/add_watch MSFT 380 450 520`).")
            return
    else:
        target_buy = score["target_buy"]
        iv_low = score["iv_low"]
        iv_high = score["iv_high"]

    author_name = user.full_name or user.username or f"User #{user.id}"
    cur_price = metrics.get("price")
    comp_name = metrics.get("name", ticker)
    currency = metrics.get("currency", "USD")
    sym = "$" if currency == "USD" else ("p " if currency == "GBX" else "€")

    with get_session() as session:
        master_id = get_master_watchlist_owner_id(session)
        item = (
            session.query(WatchlistItem)
            .filter(WatchlistItem.user_id == master_id, WatchlistItem.ticker == ticker)
            .first()
        )
        is_new = item is None
        if is_new:
            max_rank = (
                session.query(WatchlistItem)
                .filter(WatchlistItem.user_id == master_id)
                .count()
            )
            item = WatchlistItem(
                user_id=master_id,
                rank_order=max_rank + 1,
                ticker=ticker,
                company_name=comp_name,
                currency=currency,
                moat_summary=f"ROIC: {metrics.get('roic_est_pct') or 'N/A'}% | Опер. марж: {metrics.get('operating_margin_pct') or 'N/A'}%",
                roic_pct=metrics.get("roic_est_pct"),
                intrinsic_value_low=iv_low,
                intrinsic_value_high=iv_high,
                target_buy_price=target_buy,
                last_price=cur_price,
                alert_enabled=True,
                notes=f"👤 Добавена от {author_name}",
            )
            session.add(item)
        else:
            item.target_buy_price = target_buy
            item.intrinsic_value_low = iv_low
            item.intrinsic_value_high = iv_high
            if cur_price:
                item.last_price = cur_price
            item.notes = f"👤 Обновена от {author_name}"
        session.commit()

        other_users = (
            session.query(User)
            .filter(User.telegram_id != 0, User.telegram_id != user.telegram_id)
            .all()
        )
        other_chat_ids = [u.telegram_id for u in other_users]

    in_buy_zone = cur_price is not None and cur_price <= target_buy
    zone_status = "🟢 *В ЗОНАТА ЗА ПОКУПКА ДНЕС!*" if in_buy_zone else "⏳ _Чакаме корекция до Target Buy_"
    action_word = "добави нова компания" if is_new else "обнови компания"

    price_line = f"• *Текуща цена:* `{sym}{cur_price:,.2f}`\n" if cur_price else ""
    await safe_edit(
        wait_msg,
        f"✅ *{ticker}* ({comp_name}) е записана в *общия Watchlist* за всички!\n\n"
        f"{price_line}"
        f"• *Целева цена (Target Buy):* `<= {sym}{target_buy:,.2f}`\n"
        f"• *Вътрешна стойност (IV):* `{sym}{iv_low:,.2f} – {sym}{iv_high:,.2f}`\n"
        f"• *Статус:* {zone_status}",
    )

    if other_chat_ids:
        price_fmt = f"{sym}{cur_price:,.2f}" if cur_price else "N/A"
        broadcast_text = (
            f"🤝 *НОВА НАХОДКА В ОБЩИЯ WATCHLIST!*\n\n"
            f"👤 *{author_name}* {action_word}: *{ticker} ({comp_name})*\n"
            f"• Текуща цена: `{price_fmt}`\n"
            f"• Целева цена за покупка (Target Buy): `<= {sym}{target_buy:,.2f}`\n"
            f"• Вътрешна стойност по Бъфет: `{sym}{iv_low:,.2f} – {sym}{iv_high:,.2f}`\n"
            f"• Статус: {zone_status}\n\n"
            f"Напиши `/buffett {ticker}` за пълен фундаментален анализ или `/watchlist` за целия списък!"
        )
        for cid in other_chat_ids:
            try:
                await context.bot.send_message(chat_id=cid, text=broadcast_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning("Could not broadcast watchlist update to %s: %s", cid, e)


async def cmd_del_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    if not context.args:
        await safe_reply(update.message, "Използвай: `/del_watch ТИКЪР` (напр. `/del_watch CRWD`)")
        return
    ticker = context.args[0].strip().upper()
    author_name = user.full_name or user.username or f"User #{user.id}"

    with get_session() as session:
        master_id = get_master_watchlist_owner_id(session)
        deleted = (
            session.query(WatchlistItem)
            .filter(WatchlistItem.user_id == master_id, WatchlistItem.ticker == ticker)
            .delete()
        )
        session.commit()
        other_chat_ids = [
            u.telegram_id
            for u in session.query(User).filter(User.telegram_id != 0, User.telegram_id != user.telegram_id).all()
        ]

    if deleted:
        await safe_reply(update.message, f"🗑️ Акцията *{ticker}* беше премахната от общия Watchlist.")
        for cid in other_chat_ids:
            try:
                await context.bot.send_message(
                    chat_id=cid,
                    text=f"ℹ️ *{author_name}* премахна *{ticker}* от общия Watchlist.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
    else:
        await safe_reply(update.message, f"ℹ️ *{ticker}* не е намерена в общия Watchlist.")


async def cmd_buffett(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    if not context.args:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("INTU (#1 Buy)", callback_data="buffett_INTU"),
                    InlineKeyboardButton("NVDA (Карантина)", callback_data="buffett_NVDA"),
                    InlineKeyboardButton("KO (Coca-Cola)", callback_data="buffett_KO"),
                ],
                [
                    InlineKeyboardButton("ASML", callback_data="buffett_ASML"),
                    InlineKeyboardButton("META", callback_data="buffett_META"),
                    InlineKeyboardButton("GOOGL", callback_data="buffett_GOOGL"),
                ],
            ]
        )
        await safe_reply(
            update.effective_message,
            "🧠 *БЪФЕТ & МЪНГЪР АНАЛИЗАТОР НА АКЦИИ*\n\n"
            "Напиши `/buffett ТИКЪР` (или просто напиши тикъра в чата, напр. `INTU`, `MSFT`, `KO`) "
            "или избери компания от бутоните:",
            reply_markup=kb,
        )
        return

    ticker = context.args[0].strip().upper()
    await _run_buffett_analysis_reply(update.effective_chat, user, ticker)


async def _run_buffett_analysis_reply(chat, user: User, ticker: str) -> None:
    wait_msg = await chat.send_message(
        f"🧠 Извличам финансови отчети и изчислявам Owner Earnings и Вътрешна стойност по Бъфет за *{ticker}*...",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        metrics = await asyncio.to_thread(fetch_stock_fundamentals, ticker)
        with get_session() as session:
            master_id = get_master_watchlist_owner_id(session)
            watch_item = (
                session.query(WatchlistItem)
                .filter(WatchlistItem.user_id == master_id, WatchlistItem.ticker == ticker)
                .first()
            )
            latest_snap = (
                session.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.user_id == user.id)
                .order_by(PortfolioSnapshot.created_at.desc())
                .first()
            )
            if watch_item and metrics.get("price"):
                watch_item.last_price = metrics["price"]
                session.commit()

            db_user = session.query(User).filter(User.id == user.id).first()
            report = await asyncio.to_thread(
                analyze_stock_with_buffett,
                metrics,
                watch_item,
                db_user,
                latest_snap,
                session,
            )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"🎯 Избери {ticker} за инвестиция",
                        callback_data=f"pick_{ticker}",
                    ),
                    InlineKeyboardButton(
                        f"➕ Добави в Watchlist",
                        callback_data=f"quickwatch_{ticker}",
                    ),
                ]
            ]
        )
        await safe_edit(wait_msg, report, reply_markup=kb)
    except Exception as e:
        await safe_edit(wait_msg, f"❌ Грешка при анализ на *{ticker}*: `{e}`")


async def cmd_set_loan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    args = context.args or []
    if len(args) < 4:
        with get_session() as session:
            loans = session.query(UserLoan).filter(UserLoan.user_id == user.id, UserLoan.is_active.is_(True)).all()
            loan_lines = []
            for l in loans:
                note_line = f"\n  └ 📋 _План: {l.notes}_" if l.notes else ""
                loan_lines.append(
                    f"• *{l.lender_name}* {f'(Договор № {l.contract_number})' if l.contract_number else ''}: "
                    f"Главница `€{l.principal_balance:.2f}`, ГПР `{l.apr_percent:.2f}%`, "
                    f"Редовна `€{l.regular_payment:.2f}` + Предсрочна `€{l.early_principal_payment:.2f}` (такса `0.5%` = `€{l.early_principal_payment*0.005:.2f}` → общо `€{l.regular_payment + l.early_principal_payment*1.005:.2f}/мес.`)"
                    f"{note_line}"
                )
        if loan_lines:
            current_str = "\n".join(loan_lines)
        else:
            current_str = (
                "✅ _В твоя профил няма въведени кредити._\n"
                "_(Ако нямаш кредит, не е нужно да въвеждаш нищо — целият ти свободен капитал отива за буфер и инвестиции!)_"
            )
        await safe_reply(
            update.message,
            f"💳 *ТВОИТЕ КРЕДИТИ (Инверсия на Мънгър):*\n{current_str}\n\n"
            "💡 Ако имаш кредит и искаш ботът да следи погасяването му в твоя личен профил, използвай:\n"
            "`/set_loan БАНКА ГЛАВНИЦА ГПР% РЕДОВНА_ВНОСКА [ПРЕДСРОЧНА_ГЛАВНИЦА]`\n"
            "Пример за въвеждане:\n"
            "`/set_loan МоятаБанка 1000 15.5 100 50`\n"
            "_(За да изтриеш/маркираш кредит като изплатен, въведи главница `0`)_",
        )
        return

    lender = args[0].strip()
    try:
        principal = float(args[1].replace(",", "."))
        apr = float(args[2].replace(",", "."))
        reg_pmt = float(args[3].replace(",", "."))
        early_pmt = float(args[4].replace(",", ".")) if len(args) >= 5 else 0.0
    except ValueError:
        await update.message.reply_text("❌ Моля, въведи валидни числа за главница, ГПР и вноски.")
        return

    with get_session() as session:
        loan = (
            session.query(UserLoan)
            .filter(UserLoan.user_id == user.id, UserLoan.lender_name == lender)
            .first()
        )
        if not loan:
            loan = UserLoan(user_id=user.id, lender_name=lender)
            session.add(loan)
        loan.principal_balance = principal
        loan.apr_percent = apr
        loan.regular_payment = reg_pmt
        loan.early_principal_payment = early_pmt
        loan.is_active = principal > 0
        session.commit()

    if principal > 0:
        await safe_reply(
            update.message,
            f"✅ Кредитът към *{lender}* е записан в твоя личен профил!\n"
            f"Главница: `€{principal:.2f}` | ГПР: `{apr:.2f}%` | Редовна: `€{reg_pmt:.2f}` | Предсрочна: `€{early_pmt:.2f}`",
        )
    else:
        await safe_reply(update.message, f"🎉 Поздравления! Кредитът към *{lender}* е маркиран като напълно изплатен (`€0.00`)!")


async def cmd_set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    args = context.args or []
    if len(args) < 2:
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            latest_snap = (
                session.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.user_id == db_user.id)
                .order_by(PortfolioSnapshot.created_at.desc())
                .first()
            )
            dynamic_lines = build_dynamic_investment_plan_lines(session, db_user, latest_snap)

        if db_user.monthly_income > 0 or db_user.monthly_invest_budget > 0:
            buf_icon = "✅" if db_user.emergency_buffer_current >= db_user.emergency_buffer_target else "⚠️"
            dynamic_block = "\n".join(f"• {l}" for l in dynamic_lines)
            status_lines = (
                f"• Нетен месечен доход: `€{db_user.monthly_income:.2f}` (Месечен отчет: {format_bg_ordinal(db_user.payday_day)} число)\n"
                f"• Месечна сума за буфер/инвестиции: `€{db_user.monthly_invest_budget:.2f}`\n"
                f"• Ликвиден буфер (Revolut/Кеш): `€{db_user.emergency_buffer_current:.2f}` / цел `€{db_user.emergency_buffer_target:.2f}` {buf_icon}\n\n"
                f"📈 *ДИНАМИЧЕН ПЛАН ЗА ИНВЕСТИРАНЕ ДНЕС (изчислен на живо):*\n{dynamic_block}"
            )
        else:
            status_lines = (
                "_Все още не си въвел своя месечен доход и бюджет за инвестиции._\n"
                f"• Автоматичен месечен отчет: *{format_bg_ordinal(db_user.payday_day)} число на месеца*\n"
                "_(Въведи данните си по-долу, за да се запазят в твоя личен профил!)_"
            )
        await safe_reply(
            update.message,
            f"💰 *ТВОЯТ ЛИЧЕН МЕСЕЧЕН БЮДЖЕТ:*\n"
            f"{status_lines}\n\n"
            "За промяна на бюджета използвай:\n"
            "`/set_budget ЧИСТ_ДОХОД ИНВЕСТ_БЮДЖЕТ [ТЕКУЩ_БУФЕР] [ДЕН_НА_ОТЧЕТА]`\n"
            "Пример: `/set_budget 1200 150 500`\n"
            "🛡️ Само за буфера: `/set_buffer СУМА` _(напр._ `/set_buffer 100`_)_\n"
            "📅 Само за деня на отчета: `/set_payday ДЕН` _(напр._ `/set_payday 5`_)_",
        )
        return

    try:
        income = float(args[0].replace(",", "."))
        inv_budget = float(args[1].replace(",", "."))
        buf = float(args[2].replace(",", ".")) if len(args) >= 3 else user.emergency_buffer_current
        new_payday = int(args[3]) if len(args) >= 4 else None
        if new_payday is not None and not (1 <= new_payday <= 31):
            await update.message.reply_text("❌ Денят за месечен отчет трябва да е число от 1 до 31.")
            return
    except ValueError:
        await update.message.reply_text("❌ Моля, въведи валидни числа (напр. `/set_budget 1200 150 500`).")
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.monthly_income = income
        db_user.monthly_invest_budget = inv_budget
        db_user.emergency_buffer_current = buf
        if new_payday is not None:
            db_user.payday_day = new_payday
        session.commit()
        session.refresh(db_user)

    await safe_reply(
        update.message,
        f"✅ Личният ти бюджет е обновен!\n"
        f"Чист доход: `€{income:.2f}` | За инвестиции/буфер: `€{inv_budget:.2f}/мес.` | Ликвиден буфер: `€{buf:.2f}` | Месечен отчет: `{format_bg_ordinal(db_user.payday_day)} число`",
        reply_markup=get_reply_keyboard(db_user),
    )


async def cmd_set_buffer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quickly update current emergency buffer amount (e.g. /set_buffer 150)."""
    user = get_or_register_user(update.effective_user)
    args = context.args or []
    if len(args) < 1:
        await safe_reply(
            update.message,
            f"🛡️ *ТВОЯТ ЛИКВИДЕН БУФЕР (Revolut / Кеш):*\n"
            f"Текущо: `€{user.emergency_buffer_current:,.2f}` / Цел: `€{user.emergency_buffer_target:,.2f}`\n\n"
            "За да обновиш сумата на буфера, напиши:\n"
            "`/set_buffer ТЕКУЩА_СУМА [ЦЕЛ]` _(напр._ `/set_buffer 150` _или_ `/set_buffer 0 500`_)_",
        )
        return

    try:
        cur_buf = float(args[0].replace(",", "."))
        target_buf = float(args[1].replace(",", ".")) if len(args) >= 2 else user.emergency_buffer_target
    except ValueError:
        await safe_reply(update.message, "❌ Моля, въведи валидна сума (напр. `/set_buffer 150`).")
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.emergency_buffer_current = max(0.0, cur_buf)
        db_user.emergency_buffer_target = max(0.0, target_buf)
        session.commit()

    await safe_reply(
        update.message,
        f"✅ Ликвидният ти буфер е обновен: `€{max(0.0, cur_buf):,.2f}` / цел `€{max(0.0, target_buf):,.2f}`!",
    )


async def cmd_set_payday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow any user to customize the day of month (1-31) for their automated monthly report."""
    user = get_or_register_user(update.effective_user)
    args = context.args or []
    if len(args) != 1:
        await safe_reply(
            update.message,
            f"📅 *НАСТРОЙКА НА ДЕНЯ ЗА МЕСЕЧЕН ОТЧЕТ*\n\n"
            f"Текущият ти настроен ден е: *{format_bg_ordinal(user.payday_day)} число на месеца*.\n\n"
            "За да го промениш, изпрати число от `1` до `31`:\n"
            "`/set_payday ДЕН` _(напр._ `/set_payday 1` _или_ `/set_payday 5`_)_",
        )
        return

    try:
        day = int(args[0].strip())
        if not (1 <= day <= 31):
            raise ValueError
    except ValueError:
        await safe_reply(update.message, "❌ Моля, въведи валиден ден от месеца между `1` и `31` (напр. `/set_payday 1`).")
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.payday_day = day
        session.commit()
        session.refresh(db_user)

    await safe_reply(
        update.message,
        f"✅ Денят ти за автоматичен месечен отчет е променен на *{format_bg_ordinal(day)} число на месеца*!",
        reply_markup=get_reply_keyboard(db_user),
    )


async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user = get_or_register_user(update.effective_user)
    data = query.data or ""

    if data == "act_sync_t212":
        if not user.t212_api_key_enc:
            await safe_reply(query.message, "⚠️ Първо свържи своя Trading 212 API ключ с `/connect_t212`.")
            return
        wait_msg = await query.message.reply_text("🔄 Синхронизирам твоя портфейл на живо от Trading 212 API...")
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            try:
                snap = await asyncio.to_thread(sync_user_portfolio, session, db_user)
                report = format_portfolio_telegram(snap)
                await safe_edit(wait_msg, report)
            except Exception as e:
                await safe_edit(wait_msg, f"❌ Грешка при синхронизация: `{e}`")

    elif data == "act_monthly_check":
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            report = generate_monthly_check_report(session, db_user)
        await safe_reply(query.message, report)

    elif data == "act_watchlist":
        with get_session() as session:
            text, kb = format_watchlist_telegram(session)
        await safe_reply(query.message, text, reply_markup=kb)

    elif data == "act_check_alerts":
        await cmd_check_alerts(update, context)

    elif data == "act_buffett_menu":
        context.args = []
        await cmd_buffett(update, context)

    elif data == "act_gemini_help":
        context.args = []
        await cmd_set_gemini_key(update, context)

    elif data == "act_connect_t212":
        context.args = []
        await cmd_connect_t212(update, context)

    elif data == "act_budget_help":
        context.args = []
        await safe_reply(
            query.message,
            "💳 *НАСТРОЙКА НА ЛИЧНИЯ ТИ БЮДЖЕТ, БУФЕР И КРЕДИТ:*\n\n"
            "• *Бюджет и буфер:* `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР`\n"
            "  _(Пример:_ `/set_budget 1200 150 500`_)_\n"
            "• *Само буфер в Revolut:* `/set_buffer СУМА` _(напр._ `/set_buffer 200`_)_\n"
            "• *Ден за месечен отчет:* `/set_payday ДЕН` _(напр._ `/set_payday 1`_)_\n"
            "• *Кредит (ако имаш):* `/set_loan БАНКА ГЛАВНИЦА ГПР% РЕДОВНА ПРЕДСРОЧНА`",
        )

    elif data == "agent_top3":
        await _run_agent_question(
            query.message.chat,
            user,
            "Сравни Обединения Топ 3 днес (подценените акции от моя портфейл срещу общия Watchlist): по-добре ли е да подсиля позиция, която вече притежавам, или да добавя нова компания, и колко да заделя за буфера?",
        )

    elif data == "agent_portfolio":
        await _run_agent_question(
            query.message.chat,
            user,
            "Анализирай позициите в моя Trading 212 портфейл днес — кои да подсиля на следващата вноска и от кои сектори да се пазя?",
        )

    elif data == "agent_budget":
        await _run_agent_question(
            query.message.chat,
            user,
            "Как да разпределя най-разумно месечния си доход между ликвидния буфер, кредита (ако имам) и инвестициите по правилата на Бъфет и Мънгър?",
        )

    elif data.startswith("pick_"):
        ticker = data.split("_", 1)[1].strip().upper()
        await _handle_pick_stock(query.message.chat, user, ticker)

    elif data.startswith("confirm_"):
        parts = data.split("_")
        ticker = parts[1].upper() if len(parts) > 1 else "?"
        amount = parts[2] if len(parts) > 2 else "50"
        await safe_reply(
            query.message,
            f"✅ *Изборът ти е записан за следващата ти инвестиция:*\n\n"
            f"• *Акция/ETF:* `{ticker}`\n"
            f"• *Планирана сума:* `€{amount}` (в Trading 212)\n"
            f"• 💡 _Съвет на Бъфет:_ Провери дали си заделил и планираната сума за ликвидния буфер в Revolut (`€{user.emergency_buffer_current:.0f}/€{user.emergency_buffer_target:.0f}`), "
            f"а след като купиш `{ticker}`, натисни `🔄 Синхронизирай T212` в `📊 Портфейл`!",
        )

    elif data.startswith("buffett_"):
        ticker = data.split("_", 1)[1].strip().upper()
        await _run_buffett_analysis_reply(query.message.chat, user, ticker)

    elif data.startswith("quickwatch_"):
        ticker = data.split("_", 1)[1].strip().upper()
        context.args = [ticker]
        await cmd_add_watch(update, context)


async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if "Портфейл" in text:
        await cmd_portfolio(update, context)
    elif "Месечен отчет" in text:
        await cmd_monthly_check(update, context)
    elif "Watchlist" in text:
        await cmd_watchlist(update, context)
    elif "ценови аларми" in text or "Топ 3" in text or "Аларми" in text:
        await cmd_check_alerts(update, context)
    elif "Бъфет Агент" in text:
        context.args = []
        await cmd_ask(update, context)
    elif "Бъфет Анализ" in text:
        context.args = []
        await cmd_buffett(update, context)
    elif "Кредит" in text or "Бюджет" in text:
        await cmd_set_loan(update, context)
        await cmd_set_budget(update, context)
    elif "Trading 212" in text:
        await cmd_connect_t212(update, context)
    elif "Помощ" in text:
        await cmd_help(update, context)
    elif len(text) <= 6 and text.replace(".", "").isalpha():
        # Single stock ticker (e.g. INTU, KO, ASML, RR.L) -> Instant Buffett stock analysis!
        user = get_or_register_user(update.effective_user)
        await _run_buffett_analysis_reply(update.effective_chat, user, text.upper())
    elif len(text) >= 4:
        # Any free-text question in Bulgarian/English -> Conversational Buffett & Munger Agent!
        user = get_or_register_user(update.effective_user)
        await _run_agent_question(update.effective_chat, user, text)


async def scheduled_watchlist_alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly background job (every Monday): refreshes live prices and pushes personalized Top 3 + Buy Alerts to all users."""
    triggered_ids = await asyncio.to_thread(_run_watchlist_check_isolated)
    with get_session() as session:
        user_ids = [u.id for u in session.query(User).filter(User.telegram_id != 0).all()]

    for uid in user_ids:
        port_evals = await asyncio.to_thread(_run_portfolio_eval_isolated, uid)
        with get_session() as session:
            u = session.query(User).filter(User.id == uid).first()
            if not u:
                continue
            latest_snap = (
                session.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.user_id == u.id)
                .order_by(PortfolioSnapshot.created_at.desc())
                .first()
            )
            triggered = (
                session.query(WatchlistItem)
                .filter(WatchlistItem.id.in_(triggered_ids))
                .order_by(WatchlistItem.rank_order.asc(), WatchlistItem.id.asc())
                .all()
                if triggered_ids
                else []
            )
            text, kb = build_top3_and_alerts_report(session, u, triggered, latest_snap, port_evals)
            chat_id = u.telegram_id
        try:
            await safe_send(
                context.bot,
                chat_id,
                f"📅 *СЕДМИЧЕН ПРЕГЛЕД НА ПАЗАРА (ПОНЕДЕЛНИК)*\n\n{text}",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to send weekly Top 3 alert to user %s: %s", chat_id, e)


async def scheduled_monthly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily check: sends the automated dynamic monthly report on each user's configured payday (default 1st for friends, 13th for owner)."""
    now = datetime.now()
    today_day = now.day
    last_day_of_month = calendar.monthrange(now.year, now.month)[1]
    with get_session() as session:
        all_users = session.query(User).filter(User.telegram_id != 0).all()
        users = [
            u for u in all_users
            if min(max(1, u.payday_day), last_day_of_month) == today_day
        ]
        for u in users:
            try:
                if u.t212_api_key_enc:
                    await asyncio.to_thread(sync_user_portfolio, session, u)
                report = generate_monthly_check_report(session, u)
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏆 Избери от Топ 3 днес", callback_data="act_check_alerts")]]
                )
                await safe_send(
                    context.bot,
                    u.telegram_id,
                    f"🔔 *АВТОМАТИЧЕН МЕСЕЧЕН ОТЧЕТ ({format_bg_ordinal(u.payday_day)} число)*\n\n{report}",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning("Failed to send monthly report to user %s: %s", u.telegram_id, e)


async def post_init_setup(application: Application) -> None:
    """Register clean native Telegram '/' slash commands menu for mobile and desktop users."""
    commands = [
        BotCommand("start", "Начало и главно меню"),
        BotCommand("check_alerts", "Обединен Топ 3 (Watchlist vs Портфейл) и аларми"),
        BotCommand("portfolio", "Моят Trading 212 портфейл"),
        BotCommand("monthly_check", "Месечен финансов отчет и план"),
        BotCommand("watchlist", "Общ Бъфет Watchlist (за цялата група)"),
        BotCommand("ask", "Попитай Бъфет & Мънгър AI Агента"),
        BotCommand("buffett", "10-точков Бъфет анализ на акция (/buffett INTU)"),
        BotCommand("add_watch", "Добави акция в общия Watchlist"),
        BotCommand("set_budget", "Настрой доход, инвест. бюджет и буфер"),
        BotCommand("set_buffer", "Обнови ликвидния буфер (Revolut/кеш)"),
        BotCommand("set_loan", "Добави/обнови кредит (Инверсия на Мънгър)"),
        BotCommand("set_payday", "Смени деня за автоматичния месечен отчет"),
        BotCommand("connect_t212", "Свържи Trading 212 API (Read-Only)"),
        BotCommand("set_gemini_key", "Добави безплатен Gemini AI ключ (без .env)"),
        BotCommand("help", "Пълни инструкции как работи ботът"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Registered %d native Telegram slash commands.", len(commands))
    except Exception as e:
        logger.warning("Could not register Telegram slash commands: %s", e)


class _HealthHandler(BaseHTTPRequestHandler):
    """Lightweight HTTP health endpoint for Render Free Web Service & UptimeRobot (uses 0 AI tokens)."""

    def do_GET(self) -> None:
        if self.path in ("/", "/health", "/healthz"):
            body = b'{"status":"ok","service":"buffett-munger-telegram-bot"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self) -> None:
        if self.path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:
        # Suppress noisy access logs on every UptimeRobot ping
        return


def start_health_server_if_needed() -> ThreadingHTTPServer | None:
    """Bind an HTTP health server on $PORT when deployed on Render/cloud (or ENABLE_HEALTH_SERVER=1)."""
    port_str = os.environ.get("PORT", "").strip()
    if not port_str and os.environ.get("ENABLE_HEALTH_SERVER", "").strip() != "1":
        return None
    port = int(port_str or "10000")
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health-http-server")
    t.start()
    logger.info("HTTP health server listening on 0.0.0.0:%d (/health)", port)
    return server


def main() -> None:
    # Bind health port first on Render so port-scan succeeds immediately even while DB/T212 initializes
    start_health_server_if_needed()

    init_db()
    seed_owner_profile()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("⚠️ Липсва TELEGRAM_BOT_TOKEN в .env / Environment Variables!")
        return

    app = Application.builder().token(token).post_init(post_init_setup).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("connect_t212", cmd_connect_t212))
    app.add_handler(CommandHandler("set_gemini_key", cmd_set_gemini_key))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("monthly_check", cmd_monthly_check))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("check_alerts", cmd_check_alerts))
    app.add_handler(CommandHandler("add_watch", cmd_add_watch))
    app.add_handler(CommandHandler("del_watch", cmd_del_watch))
    app.add_handler(CommandHandler("buffett", cmd_buffett))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("set_loan", cmd_set_loan))
    app.add_handler(CommandHandler("set_budget", cmd_set_budget))
    app.add_handler(CommandHandler("set_buffer", cmd_set_buffer))
    app.add_handler(CommandHandler("set_payday", cmd_set_payday))
    app.add_handler(CallbackQueryHandler(on_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_menu))

    if app.job_queue:
        # Weekly Top 3 + watchlist alerts: every Monday (day=1 in 0=Sun..6=Sat) at 15:00 UTC (17:00 BG time)
        app.job_queue.run_daily(scheduled_watchlist_alert_job, time=dtime(hour=15, minute=0), days=(1,))
        app.job_queue.run_daily(scheduled_monthly_report_job, time=dtime(hour=9, minute=0))

    logger.info("Buffett & Munger Telegram Bot started polling...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
