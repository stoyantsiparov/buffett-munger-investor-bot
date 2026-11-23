"""Bilingual (EN default / BG switchable) Warren Buffett & Charlie Munger Telegram Investment Bot."""

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
    generate_monthly_check_report,
    rank_portfolio_buy_candidates,
    rank_unified_top3_opportunities,
)
from bot.crypto import encrypt_secret
from bot.db import get_session, init_db
from bot.i18n import format_ordinal, get_lang, localize_moat
from bot.market_service import check_watchlist_alerts, fetch_stock_fundamentals
from bot.models import PortfolioSnapshot, User, UserLoan, WatchlistItem
from bot.seed_owner import seed_default_watchlist_for_user, seed_owner_profile
from bot.t212_service import (
    SEMICONDUCTOR_SHORT_TICKERS,
    format_portfolio_telegram,
    parse_t212_snapshot,
    sync_user_portfolio,
)

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("buffett_bot")


def get_reply_keyboard(user: User | None = None) -> ReplyKeyboardMarkup:
    lang = get_lang(user)
    day = user.payday_day if user else 1
    ord_str = format_ordinal(day, lang)
    if lang == "bg":
        return ReplyKeyboardMarkup(
            [
                ["📊 Портфейл (T212)", f"📅 Месечен отчет ({ord_str})"],
                ["🎯 Watchlist", "🔔 Топ 3 & Аларми"],
                ["🤖 Бъфет Агент", "💳 Кредит & Бюджет"],
                ["🔑 Свържи Trading 212", "🌐 Език (EN/BG)"],
                ["ℹ️ Помощ"],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        [
            ["📊 Portfolio (T212)", f"📅 Monthly Check ({ord_str})"],
            ["🎯 Watchlist", "🔔 Top 3 & Alerts"],
            ["🤖 Buffett Agent", "💳 Loans & Budget"],
            ["🔑 Connect Trading 212", "🌐 Language (EN/BG)"],
            ["ℹ️ Help"],
        ],
        resize_keyboard=True,
    )


async def safe_reply(message, text: str, reply_markup=None) -> None:
    """Send a Telegram message with Markdown, falling back to plain text if Markdown entities fail."""
    chunks = [text[i : i + 3950] for i in range(0, len(text), 3950)] or [text]
    for idx, chunk in enumerate(chunks):
        rm = reply_markup if idx == len(chunks) - 1 else None
        try:
            await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN, reply_markup=rm)
        except BadRequest:
            await message.reply_text(chunk.replace("*", "").replace("`", "").replace("_", ""), reply_markup=rm)


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
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN, reply_markup=rm)
        except BadRequest:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk.replace("*", "").replace("`", "").replace("_", ""),
                reply_markup=rm,
            )


def get_master_watchlist_owner_id(session) -> int:
    owner = session.query(User).order_by(User.id.asc()).first()
    return owner.id if owner else 1


def get_or_register_user(tg_user) -> User:
    """Find user by telegram_id, bind unclaimed owner row, or create a blank English-default user profile."""
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
            if not getattr(user, "language", None):
                user.language = "en"
                updated = True
            if updated:
                session.commit()
            return user

        unclaimed_owner = session.query(User).filter(User.telegram_id == 0).first()
        if unclaimed_owner:
            unclaimed_owner.telegram_id = tg_user.id
            unclaimed_owner.username = tg_user.username
            unclaimed_owner.full_name = (
                f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip() or unclaimed_owner.full_name
            )
            session.commit()
            session.refresh(unclaimed_owner)
            logger.info("Bound seeded owner profile to Telegram ID %s", tg_user.id)
            return unclaimed_owner

        new_user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip() or "Investor",
            language="en",
            monthly_income=0.0,
            monthly_invest_budget=0.0,
            emergency_buffer_target=500.0,
            emergency_buffer_current=0.0,
            payday_day=1,
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        logger.info("Created new user profile (lang=en, payday=1, blank budget/loans) for Telegram ID %s", tg_user.id)
        return new_user


def format_watchlist_telegram(session, lang: str = "en") -> tuple[str, InlineKeyboardMarkup]:
    """Render the shared, synchronized Watchlist in 'en' (default) or 'bg'."""
    is_bg = lang == "bg"
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

    lines = (
        [
            "🎯 *ОБЩ WATCHLIST (БЪФЕТ & МЪНГЪР — СИНХРОНИЗИРАН ЗА ВСИЧКИ)*",
            "_Когато някой от нас добави компания тук, тя се вижда от всички и следим цената ѝ заедно!_",
            "",
        ]
        if is_bg
        else [
            "🎯 *SHARED BUFFETT & MUNGER WATCHLIST (SYNCED FOR ALL USERS)*",
            "_When anyone adds an undervalued stock here, everyone sees it and gets buy-zone alerts!_",
            "",
        ]
    )

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
            badge = "🟢 *КУПИ СЕГА*" if is_bg else "🟢 *BUY NOW*"
        elif w.last_price is not None and w.last_price <= w.target_buy_price * 1.15:
            badge = "🟡 *Близо*" if is_bg else "🟡 *Close*"
        else:
            badge = "⏳"

        added_by = ""
        if w.notes and w.notes.startswith("👤"):
            added_by = f" | _{w.notes.split('|')[0].strip()}_"

        p_lbl = "Цена" if is_bg else "Price"
        lines.append(
            f"{badge} *#{w.rank_order} `{w.ticker}`* ({w.company_name}){added_by}\n"
            f"   └ {p_lbl}: `{price_str}` | Target Buy: `<= {target_str}` | IV: `{iv_str}`"
        )

    if is_bg:
        lines.extend([
            "",
            "🤝 *Как да споделиш нова компания с всички в Watchlist-а:*",
            "• `/add_watch ТИКЪР` — Ботът сам изчислява Вътрешната стойност и Target Buy по Бъфет и известява всички!",
            "• `/add_watch ТИКЪР TARGET_PRICE [IV_LOW] [IV_HIGH]` — Добавяне с твоя целева цена",
            "• `/del_watch ТИКЪР` — Премахване на акция от общия списък",
            "• `/buffett ТИКЪР` — Пълен Бъфет анализ на акцията",
        ])
    else:
        lines.extend([
            "",
            "🤝 *Share a new stock with everyone in the Watchlist:*",
            "• `/add_watch TICKER` — Auto-computes Buffett Intrinsic Value & Target Buy and notifies everyone!",
            "• `/add_watch TICKER TARGET_PRICE [IV_LOW] [IV_HIGH]` — Add with a custom target buy price",
            "• `/del_watch TICKER` — Remove a ticker from the shared Watchlist",
            "• `/buffett TICKER` — Full 10-point Buffett analysis",
        ])

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔔 Топ 3 днес & Цени на живо" if is_bg else "🔔 Top 3 Today & Live Prices",
                    callback_data="act_check_alerts",
                ),
                InlineKeyboardButton(
                    "🤖 Попитай Агента" if is_bg else "🤖 Ask Buffett Agent",
                    callback_data="agent_top3",
                ),
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
    """Build detailed Buffett Buy Alerts (Watchlist + User's Own Portfolio) + Unified Top 3 Picker in EN or BG."""
    lang = get_lang(user)
    is_bg = lang == "bg"

    parsed_snap = None
    if latest_snap and latest_snap.raw_json:
        try:
            parsed_snap = parse_t212_snapshot(json.loads(latest_snap.raw_json))
        except Exception:
            parsed_snap = None

    unified = rank_unified_top3_opportunities(session, user, latest_snap, portfolio_evals, lang=lang)
    unified_top3 = unified["top3"]
    ranked_port = rank_portfolio_buy_candidates(parsed_snap, lang=lang)

    lines: list[str] = []

    # 1. Detailed Buy Alerts in Shared Watchlist
    if triggered:
        if is_bg:
            lines.extend([
                "🔔 *АЛАРМА ЗА ПОКУПКА В ОБЩИЯ БЪФЕТ WATCHLIST!*",
                "Следните компании са в или под Целевата цена за покупка (Target Buy) с Марж на безопасност:",
                "",
            ])
        else:
            lines.extend([
                "🔔 *BUY ALERT IN SHARED BUFFETT WATCHLIST!*",
                "The following businesses are trading at or below Target Buy Price with a Margin of Safety:",
                "",
            ])
        for w in triggered[:6]:
            sym = "$" if w.currency == "USD" else ("p " if w.currency == "GBX" else "€")
            discount = (
                round(((w.intrinsic_value_high - w.last_price) / w.intrinsic_value_high) * 100.0, 1)
                if (w.intrinsic_value_high and w.last_price)
                else w.margin_of_safety_pct
            )
            moat_txt = localize_moat(w.ticker, w.moat_summary, lang)
            if is_bg:
                lines.extend([
                    f"🟢 *#{w.rank_order} `{w.ticker}` ({w.company_name})*",
                    f"   • Текуща цена: `{sym}{w.last_price:,.2f}` <= Target Buy: `{sym}{w.target_buy_price:,.2f}`",
                    f"   • Вътрешна стойност: `{sym}{w.intrinsic_value_low or 0:,.0f} – {sym}{w.intrinsic_value_high or 0:,.0f}` (Отстъпка: `+{discount:.1f}%`)",
                    f"   • Ров: _{moat_txt}_",
                    "",
                ])
            else:
                lines.extend([
                    f"🟢 *#{w.rank_order} `{w.ticker}` ({w.company_name})*",
                    f"   • Current Price: `{sym}{w.last_price:,.2f}` <= Target Buy: `{sym}{w.target_buy_price:,.2f}`",
                    f"   • Intrinsic Value: `{sym}{w.intrinsic_value_low or 0:,.0f} – {sym}{w.intrinsic_value_high or 0:,.0f}` (Discount: `+{discount:.1f}%`)",
                    f"   • Moat: _{moat_txt}_",
                    "",
                ])
        if len(triggered) > 6:
            lines.append(
                f"_...и още {len(triggered) - 6} компании в зоната за покупка (виж 🎯 Watchlist)._"
                if is_bg
                else f"_...and {len(triggered) - 6} more stocks in Buy Zone (see 🎯 Watchlist)._"
            )
            lines.append("")
    else:
        if is_bg:
            lines.extend([
                "🔔 *ПРОВЕРКА НА ЦЕНОВИ АЛАРМИ (WATCHLIST + ТВОЯТ ПОРТФЕЙЛ)*",
                "_В момента няма нова акция от общия Watchlist под Target Buy цената._",
                "",
            ])
        else:
            lines.extend([
                "🔔 *PRICE ALERT SCAN (WATCHLIST + YOUR PORTFOLIO)*",
                "_No shared Watchlist stock is below its Target Buy Price right now._",
                "",
            ])

    # 2. Detailed Undervalued Alerts in User's OWN Trading 212 Portfolio
    if portfolio_evals:
        buy_zone_port = [p for p in portfolio_evals if p["in_buy_zone"] and not p["sector_warn"]]
        fair_disc_port = [p for p in portfolio_evals if (not p["in_buy_zone"]) and p["is_undervalued"] and not p["sector_warn"]]
        blocked_under_port = [p for p in portfolio_evals if p["is_undervalued"] and p["sector_warn"]]

        if latest_snap and latest_snap.created_at:
            snap_ts = (
                f" _(данни: {latest_snap.created_at.strftime('%d.%m.%Y %H:%M')})_"
                if is_bg
                else f" _(synced: {latest_snap.created_at.strftime('%Y-%m-%d %H:%M UTC')})_"
            )
        else:
            snap_ts = ""

        lines.append(
            f"💼 *ПОДЦЕНЕНИ КОМПАНИИ В ТВОЯ ПОРТФЕЙЛ (TRADING 212):*{snap_ts}"
            if is_bg
            else f"💼 *UNDERVALUED STOCKS IN YOUR PORTFOLIO (TRADING 212):*{snap_ts}"
        )
        shown_port = (buy_zone_port + fair_disc_port)[:4]
        if shown_port:
            for p in shown_port:
                sym = p["currency_sym"]
                badge = "🟢" if p["in_buy_zone"] else "🟡"
                cmp_op = "<=" if p["in_buy_zone"] else "vs"
                moat_txt = localize_moat(p["ticker"], p["moat_summary"], lang)
                if is_bg:
                    lines.extend([
                        f"{badge} *`{p['ticker']}` ({p['company_name']})* _(Дял: {p['cur_pct']:.1f}% / цел {p['exp_pct']:.0f}%)_",
                        f"   • Текуща цена: `{sym}{p['last_price']:,.2f}` {cmp_op} Target Buy: `{sym}{p['target_buy_price']:,.2f}`",
                        f"   • Вътрешна стойност: `{sym}{p['iv_low']:,.0f} – {sym}{p['iv_high']:,.0f}` (Отстъпка: `+{p['discount_pct']:.1f}%`)",
                        f"   • Ров: _{moat_txt}_",
                    ])
                else:
                    lines.extend([
                        f"{badge} *`{p['ticker']}` ({p['company_name']})* _(Wt: {p['cur_pct']:.1f}% / tgt {p['exp_pct']:.0f}%)_",
                        f"   • Current Price: `{sym}{p['last_price']:,.2f}` {cmp_op} Target Buy: `{sym}{p['target_buy_price']:,.2f}`",
                        f"   • Intrinsic Value: `{sym}{p['iv_low']:,.0f} – {sym}{p['iv_high']:,.0f}` (Discount: `+{p['discount_pct']:.1f}%`)",
                        f"   • Moat: _{moat_txt}_",
                    ])
        else:
            lines.append(
                "  • _Всички не-чипови акции в портфейла ти са около или над консервативната си вътрешна стойност днес._"
                if is_bg
                else "  • _All non-chip holdings in your portfolio are near or above conservative intrinsic value today._"
            )

        if blocked_under_port:
            b_str = ", ".join(f"`{p['ticker']}` (+{p['discount_pct']:.0f}%)" for p in blocked_under_port)
            lines.append(
                f"  • ⚠️ _Подценени, но блокирани от лимита за полупроводници (>20%):_ {b_str}"
                if is_bg
                else f"  • ⚠️ _Undervalued, but blocked by semiconductor sector cap (>20%):_ {b_str}"
            )
        lines.append("")
    elif not user.t212_api_key_enc:
        lines.extend([
            (
                "💼 *ТВОЯТ ПОРТФЕЙЛ:* _Още не си свързал Trading 212 (`/connect_t212`), затова класирането долу е само от общия Watchlist._"
                if is_bg
                else "💼 *YOUR PORTFOLIO:* _Connect Trading 212 (`/connect_t212`) to compare your own holdings head-to-head against the Watchlist._"
            ),
            "",
        ])

    # 3. Unified Head-to-Head Top 3
    if unified_top3:
        lines.append(
            "🏆 *ОБЩ ТОП 3 ЗА ИНВЕСТИЦИЯ ДНЕС (WATCHLIST vs. ТВОЯТ ПОРТФЕЙЛ):*"
            if is_bg
            else "🏆 *UNIFIED TOP 3 TODAY (WATCHLIST vs. YOUR PORTFOLIO):*"
        )
        for idx, item in enumerate(unified_top3, 1):
            sym = item["currency_sym"]
            roic_str = f" | ROIC `{item['roic_pct']:.0f}%`" if item["roic_pct"] else ""
            warn_str = f" ⚠️ _{item['sector_warn']}_" if item["sector_warn"] else ""
            lines.append(
                f"  {idx}️⃣ *`{item['ticker']}`* — *{item['source_badge']}*\n"
                f"     └ `{sym}{item['last_price']:,.2f}` | IV `{sym}{item['iv_low']:,.0f}–{sym}{item['iv_high']:,.0f}`{roic_str} — {item['status_tag']}{warn_str}"
            )
        if unified["comparison_insight"]:
            lbl = "💡 *Извод:*" if is_bg else "💡 *Takeaway:*"
            lines.append(f"{lbl} {unified['comparison_insight']}")
        lines.append("")

    if user.emergency_buffer_current < user.emergency_buffer_target:
        missing = user.emergency_buffer_target - user.emergency_buffer_current
        if is_bg:
            lines.append(
                f"🛡️ *Ликвиден буфер:* `€{user.emergency_buffer_current:,.2f}` / `€{user.emergency_buffer_target:,.2f}` "
                f"_(липсват `€{missing:,.2f}`)_ — не забравяй да заделиш част за Revolut буфера!"
            )
        else:
            lines.append(
                f"🛡️ *Liquidity Buffer:* `€{user.emergency_buffer_current:,.2f}` / `€{user.emergency_buffer_target:,.2f}` "
                f"_(missing `€{missing:,.2f}`)_ — remember to allocate part of your surplus to your cash buffer!"
            )
        lines.append("")

    lines.append(
        "👇 *Избери компания от Обединения Топ 3 за готов персонален план за покупка:*"
        if is_bg
        else "👇 *Pick a company from the Unified Top 3 for a personalized buy ticket:*"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    if unified_top3:
        buttons.append(
            [
                InlineKeyboardButton(f"{i}️⃣ {x['btn_icon']} {x['ticker']}", callback_data=f"pick_{x['ticker']}")
                for i, x in enumerate(unified_top3, 1)
            ]
        )

    top3_tickers = {x["ticker"] for x in unified_top3}
    broad_etf_pick = next(
        (c for c in ranked_port["top_overall"] if c.get("is_broad_etf") and c["ticker"] not in top3_tickers),
        None,
    )
    if broad_etf_pick:
        etf_lbl = (
            f"🇪🇺 0% Данък ETF: {broad_etf_pick['ticker']}"
            if is_bg
            else f"🇪🇺 Broad EU ETF: {broad_etf_pick['ticker']}"
        )
        buttons.append([InlineKeyboardButton(etf_lbl, callback_data=f"pick_{broad_etf_pick['ticker']}")])

    buttons.append(
        [
            InlineKeyboardButton(
                "🤖 Сравни: Нова vs Портфейл" if is_bg else "🤖 Compare: New vs Portfolio",
                callback_data="agent_top3",
            ),
            InlineKeyboardButton(
                "🔄 Синхронизирай T212" if is_bg else "🔄 Sync T212 Live",
                callback_data="act_sync_t212",
            ),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch interface and AI language between English (default) and Bulgarian."""
    user = get_or_register_user(update.effective_user)
    args = context.args or []

    if args:
        choice = args[0].strip().lower()
        if choice in ("en", "eng", "english"):
            await _apply_language_change(update.effective_message, user, "en")
            return
        if choice in ("bg", "bul", "bulgarian", "бг"):
            await _apply_language_change(update.effective_message, user, "bg")
            return

    lang = get_lang(user)
    cur_label = "🇬🇧 English" if lang == "en" else "🇧🇬 Български"
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇬🇧 English (Default)", callback_data="lang_en"),
                InlineKeyboardButton("🇧🇬 Български", callback_data="lang_bg"),
            ]
        ]
    )
    await safe_reply(
        update.effective_message,
        f"🌐 *LANGUAGE / ЕЗИК*\n\n"
        f"Current language / Текущ език: *{cur_label}*\n\n"
        "Choose your preferred language below (applies to menus, alerts, reports & AI Agent):",
        reply_markup=kb,
    )


async def _apply_language_change(message, user: User, new_lang: str) -> None:
    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.language = new_lang
        session.commit()
        session.refresh(db_user)

    if new_lang == "bg":
        text = (
            "✅ *Езикът е сменен на 🇧🇬 Български!*\n\n"
            "Всички менюта, анализи, ценови аларми, месечни отчети и Бъфет AI Агентът вече работят на български. "
            "По всяко време можеш да смениш обратно с `/lang` или бутона `🌐 Език (EN/BG)`."
        )
    else:
        text = (
            "✅ *Language switched to 🇬🇧 English!*\n\n"
            "All menus, valuations, price alerts, monthly reports, and the Buffett AI Agent now operate in English. "
            "You can switch back anytime via `/lang` or the `🌐 Language (EN/BG)` button."
        )
    await safe_reply(message, text, reply_markup=get_reply_keyboard(db_user))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        has_gemini = bool(_get_gemini_api_key(user=db_user, session=session))
        user = db_user

    lang = get_lang(user)
    is_bg = lang == "bg"
    ord_str = format_ordinal(user.payday_day, lang)

    if is_bg:
        has_t212 = "✅ Свързан (Read-Only)" if user.t212_api_key_enc else "⚠️ Не е свързан (`/connect_t212`)"
        gemini_status = "✅ Активен (Gemini AI + живи новини)" if has_gemini else "💡 Количествен режим _(активирай AI с `/set_gemini_key`)_"
        if user.monthly_income > 0 or user.monthly_invest_budget > 0:
            buf_warn = (
                f"⚠️ _(липсват `€{user.emergency_buffer_target - user.emergency_buffer_current:,.2f}`)_"
                if user.emergency_buffer_current < user.emergency_buffer_target
                else "✅"
            )
            budget_info = (
                f"💵 *Нетен месечен доход:* `€{user.monthly_income:,.2f}`\n"
                f"📈 *Месечен бюджет за инвестиции/буфер:* `€{user.monthly_invest_budget:,.2f}` (Отчет: {ord_str} число)\n"
                f"🛡️ *Ликвиден буфер:* `€{user.emergency_buffer_current:,.2f}` / цел `€{user.emergency_buffer_target:,.2f}` {buf_warn}"
            )
        else:
            budget_info = (
                f"📅 *Месечен отчет:* Автоматично на *{ord_str} число на месеца* _(сменя се с `/set_payday`)_\n"
                "💡 *Твоят бюджет и кредити са празни (100% лични за теб).* Натисни `💳 Кредит & Бюджет` или `ℹ️ Помощ`!"
            )
        welcome = (
            f"Здравей, *{user.full_name}*! 👋\n\n"
            "Добре дошъл в интелигентния инвестиционен агент по системата на *Уорън Бъфет и Чарли Мънгър*!\n\n"
            "🏆 *Обединен Топ 3 (Watchlist vs. Твоят портфейл):* Ботът сравнява на живо *подценените компании в твоя портфейл* срещу *общия Watchlist*, за да знаеш дали днес да подсилиш своя позиция или да добавиш нова!\n"
            "🔒 *100% Лични финанси:* Твоят Trading 212 портфейл, бюджет, буфер, кредити и API ключове са криптирани и видими САМО за теб.\n"
            "🤝 *Общ Watchlist:* Списъкът в `🎯 Watchlist` е общ за цялата група!\n\n"
            f"🔐 *Твоят Trading 212:* {has_t212}\n"
            f"🤖 *Бъфет AI Агент:* {gemini_status}\n"
            f"🌐 *Език:* 🇧🇬 Български _(смени на English с `/lang`)_\n"
            f"{budget_info}\n\n"
            "Избери бутон от менюто долу, напиши тикър (напр. `INTU`) или ми задай свободен въпрос!"
        )
    else:
        has_t212 = "✅ Connected (Read-Only)" if user.t212_api_key_enc else "⚠️ Not connected (`/connect_t212`)"
        gemini_status = "✅ Active (Gemini AI + Live News)" if has_gemini else "💡 Quantitative mode _(unlock AI via `/set_gemini_key`)_"
        if user.monthly_income > 0 or user.monthly_invest_budget > 0:
            buf_warn = (
                f"⚠️ _(missing `€{user.emergency_buffer_target - user.emergency_buffer_current:,.2f}`)_"
                if user.emergency_buffer_current < user.emergency_buffer_target
                else "✅"
            )
            budget_info = (
                f"💵 *Monthly Net Income:* `€{user.monthly_income:,.2f}`\n"
                f"📈 *Monthly Invest/Buffer Budget:* `€{user.monthly_invest_budget:,.2f}` (Report: {ord_str})\n"
                f"🛡️ *Liquidity Buffer:* `€{user.emergency_buffer_current:,.2f}` / target `€{user.emergency_buffer_target:,.2f}` {buf_warn}"
            )
        else:
            budget_info = (
                f"📅 *Monthly Report:* Automated on the *{ord_str} of each month* _(change with `/set_payday`)_\n"
                "💡 *Your budget and loans start completely blank (100% private to you).* Tap `💳 Loans & Budget` or `ℹ️ Help`!"
            )
        welcome = (
            f"Welcome, *{user.full_name}*! 👋\n\n"
            "This is your intelligent value-investing assistant built on *Warren Buffett & Charlie Munger's system*.\n\n"
            "🏆 *Unified Top 3 (Watchlist vs. Your Portfolio):* Compares *undervalued stocks in your Trading 212 portfolio* head-to-head against the *shared Watchlist* so you know whether to reinforce a current holding or open a new position!\n"
            "🔒 *100% Private Finances:* Your Trading 212 API keys, budget, cash buffer, and loans are AES-128 encrypted and visible ONLY to you.\n"
            "🤝 *Shared Watchlist:* Stocks in `🎯 Watchlist` are synced across the group!\n\n"
            f"🔐 *Trading 212:* {has_t212}\n"
            f"🤖 *Buffett AI Agent:* {gemini_status}\n"
            f"🌐 *Language:* 🇬🇧 English _(switch to Български via `/lang`)_\n"
            f"{budget_info}\n\n"
            "Pick a menu button below, type any stock ticker (e.g. `INTU`), or ask a question directly in chat!"
        )

    await safe_reply(update.effective_message, welcome, reply_markup=get_reply_keyboard(user))

    quick_rows = [
        [
            InlineKeyboardButton("🇧🇬 Превключи на Български", callback_data="lang_bg")
            if not is_bg
            else InlineKeyboardButton("🇬🇧 Switch to English", callback_data="lang_en"),
            InlineKeyboardButton(
                "🔔 Виж Топ 3 днес" if is_bg else "🔔 View Top 3 Today",
                callback_data="act_check_alerts",
            ),
        ]
    ]
    if not user.t212_api_key_enc or (user.monthly_income == 0 and user.monthly_invest_budget == 0):
        quick_rows.append(
            [
                InlineKeyboardButton(
                    "🔑 Свържи Trading 212" if is_bg else "🔑 Connect Trading 212",
                    callback_data="act_connect_t212",
                ),
                InlineKeyboardButton(
                    "💳 Настрой Бюджет" if is_bg else "💳 Set Budget",
                    callback_data="act_budget_help",
                ),
            ]
        )
        quick_rows.append(
            [
                InlineKeyboardButton(
                    "🤖 Сложи Gemini AI ключ" if is_bg else "🤖 Add Free Gemini AI Key",
                    callback_data="act_gemini_help",
                )
            ]
        )
    await safe_reply(
        update.effective_message,
        "🚀 *Бързи действия / Quick Actions:*" if is_bg else "🚀 *Quick Actions & Language:*",
        reply_markup=InlineKeyboardMarkup(quick_rows),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    ord_str = format_ordinal(user.payday_day, lang)

    if lang == "bg":
        text = (
            "📘 *ПОМОЩ: КАК РАБОТИ БОТЪТ И КАК ДА ГО ИЗПОЛЗВАШ*\n\n"
            "Този бот е твоят личен инвестиционен агент по системата на *Уорън Бъфет и Чарли Мънгър*. Всички препоръки се изчисляват *динамично на живо*.\n\n"
            "🔒 *1. ТВОИТЕ ЛИЧНИ ДАННИ (Видими САМО за теб)*\n"
            "  • *Свържи Trading 212 (Read-Only):* Settings -> API -> Generate API Key (само права за четене!) и напиши:\n"
            "    `/connect_t212 API_KEY API_SECRET` _(съобщението се трие веднага и ключът се криптира с AES-128)_\n"
            "  • *Бюджет и буфер:* `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ БУФЕР` _(напр._ `/set_budget 1200 150 500`_)_\n"
            "  • *Само буфер:* `/set_buffer СУМА` | *Ден за месечен отчет:* `/set_payday ДЕН` _(текущо:_ "
            f"*{ord_str} число*_)_\n"
            "  • *Кредит (ако имаш):* `/set_loan БАНКА ГЛАВНИЦА ГПР% РЕДОВНА ПРЕДСРОЧНА` _(главница `0` го изтрива)_\n\n"
            "🏆 *2. ОБЕДИНЕН ТОП 3 (WATCHLIST vs. ПОРТФЕЙЛ) & АЛАРМИ*\n"
            "  • Натисни *🔔 Топ 3 & Аларми* (`/check_alerts`) — сравнява на живо *общия Watchlist* и *твоите позиции в Trading 212*, за да знаеш дали да подсилиш своя акция (`💼`) или да добавиш нова (`🎯`).\n"
            "  • *Веднъж на 1 седмица* (всеки понеделник) ботът праща автоматичен преглед.\n"
            "  • Сподели акция в *🎯 Watchlist*: `/add_watch ТИКЪР` | Бърз анализ: напиши тикър (напр. `INTU`, `KO`) или `/buffett ТИКЪР`.\n\n"
            "🤖 *3. GEMINI AI АГЕНТ (БЕЗПЛАТЕН КЛЮЧ НАПРАВО В TELEGRAM)*\n"
            "  1) Отвори `aistudio.google.com/apikey` -> *Create API key* -> избери *Default Gemini Project*.\n"
            "  2) Напиши тук: `/set_gemini_key ТВОЯТ_КЛЮЧ` _(трие се веднага и се криптира!)_\n\n"
            "🌐 *4. СМЯНА НА ЕЗИКА:* Напиши `/lang` или натисни `🌐 Език (EN/BG)` за 🇬🇧 English / 🇧🇬 Български."
        )
    else:
        text = (
            "📘 *HELP: HOW THE BUFFETT & MUNGER BOT WORKS*\n\n"
            "This bot is your personal value-investing assistant built on *Warren Buffett & Charlie Munger's framework*. All valuations and picks are calculated *live*.\n\n"
            "🔒 *1. YOUR PRIVATE FINANCES (Visible ONLY to you)*\n"
            "  • *Connect Trading 212 (Read-Only):* Go to T212 Settings -> API -> Generate API Key (Read-Only permissions) and send:\n"
            "    `/connect_t212 API_KEY API_SECRET` _(your message is deleted immediately and keys are AES-128 encrypted)_\n"
            "  • *Budget & Cash Buffer:* `/set_budget INCOME INVEST_BUDGET BUFFER` _(e.g._ `/set_budget 1500 250 500`_)_\n"
            "  • *Buffer only:* `/set_buffer AMOUNT` | *Monthly report day:* `/set_payday DAY` _(current:_ "
            f"*{ord_str}*_)_\n"
            "  • *High-interest Loan (optional):* `/set_loan BANK PRINCIPAL APR REGULAR EXTRA` _(set principal to `0` to clear)_\n\n"
            "🏆 *2. UNIFIED TOP 3 (WATCHLIST vs. YOUR PORTFOLIO) & ALERTS*\n"
            "  • Tap *🔔 Top 3 & Alerts* (`/check_alerts`) — scans BOTH the *shared Watchlist* and *your own Trading 212 holdings* live, ranking them head-to-head so you know whether to reinforce a current holding (`💼`) or add a new stock (`🎯`).\n"
            "  • *Weekly automated scan* runs every Monday for all users.\n"
            "  • Share a stock to *🎯 Watchlist*: `/add_watch TICKER` | Instant 10-point scorecard: type any ticker (e.g. `INTU`, `KO`) or `/buffett TICKER`.\n\n"
            "🤖 *3. FREE GEMINI AI AGENT (NO `.env` NEEDED)*\n"
            "  1) Open `aistudio.google.com/apikey` -> *Create API key* -> select *Default Gemini Project*.\n"
            "  2) Send `/set_gemini_key YOUR_KEY` right here _(auto-deleted & encrypted!)_\n\n"
            "🌐 *4. SWITCH LANGUAGE:* Use `/lang` or tap `🌐 Language (EN/BG)` to toggle between 🇬🇧 English and 🇧🇬 Български."
        )
    await safe_reply(update.effective_message, text, reply_markup=get_reply_keyboard(user))


async def cmd_set_gemini_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow ANY user to set their own encrypted free Gemini API key directly in Telegram."""
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    args = context.args or []

    if len(args) < 1:
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            has_own = bool(db_user.gemini_api_key_enc)
            has_any = bool(_get_gemini_api_key(user=db_user, session=session))

        if is_bg:
            status = (
                "✅ Имаш записан собствен криптиран Gemini ключ."
                if has_own
                else ("✅ AI Агентът вече е активен (ползва споделения ключ)." if has_any else "⚠️ Все още нямаш добавен Gemini API ключ.")
            )
            msg = (
                "🤖 *НАСТРОЙКА НА GEMINI AI API КЛЮЧ (БЕЗПЛАТНО, БЕЗ `.env`)*\n\n"
                f"*Текущ статус:* {status}\n\n"
                "*Как да вземеш безплатен ключ за 30 секунди:*\n"
                "1️⃣ Отвори `aistudio.google.com/apikey` и влез с Google.\n"
                "2️⃣ Натисни *Create API key* -> избери **`Default Gemini Project`** и дай име (напр. `Buffett Bot`).\n"
                "3️⃣ Копирай ключа (`AIza...`) и изпрати тук:\n"
                "`/set_gemini_key ТВОЯТ_API_KEY`\n\n"
                "🛡️ _Съобщението се трие веднага от чата и ключът се пази криптиран с AES-128. За премахване:_ `/set_gemini_key off`."
            )
        else:
            status = (
                "✅ You have a personal encrypted Gemini key saved."
                if has_own
                else ("✅ AI Agent is active (using shared bot key). You may still add your own for a separate quota." if has_any else "⚠️ No Gemini API key configured yet.")
            )
            msg = (
                "🤖 *SET FREE GEMINI AI API KEY (DIRECTLY IN TELEGRAM)*\n\n"
                f"*Current status:* {status}\n\n"
                "*Get a free key in 30 seconds:*\n"
                "1️⃣ Open `aistudio.google.com/apikey` and sign in with Google.\n"
                "2️⃣ Click *Create API key* -> select **`Default Gemini Project`** and name it (e.g. `Buffett Bot`).\n"
                "3️⃣ Copy the key (`AIza...`) and send:\n"
                "`/set_gemini_key YOUR_API_KEY`\n\n"
                "🛡️ _Your message is immediately deleted from chat and encrypted with AES-128 Fernet. To remove:_ `/set_gemini_key off`."
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
        await update.effective_chat.send_message(
            "🗑️ Личният ти Gemini API ключ беше премахнат." if is_bg else "🗑️ Your personal Gemini API key has been removed."
        )
        return

    if len(raw_key) < 20:
        await update.effective_chat.send_message(
            "❌ Ключът изглежда твърде кратък. Опитай пак с /set_gemini_key КЛЮЧ."
            if is_bg
            else "❌ That key looks too short. Copy the full key (starts with AIza...) and try /set_gemini_key YOUR_KEY."
        )
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.gemini_api_key_enc = encrypt_secret(raw_key)
        session.commit()
        owner = session.query(User).order_by(User.id.asc()).first()
        is_owner = owner is not None and owner.id == db_user.id

    if is_bg:
        shared_note = (
            "\n🤝 _Като собственик на бота, този ключ активира AI Агента и за приятелите ти!_"
            if is_owner
            else "\n🔒 _Ключът е криптиран и се ползва единствено за твоя профил._"
        )
        msg = f"✅ *Gemini AI API ключът е запазен и криптиран!*\n_(Съобщението с ключа беше изтрито от чата.)_{shared_note}"
    else:
        shared_note = (
            "\n🤝 _As the bot owner, this key also powers the AI Agent for your group!_"
            if is_owner
            else "\n🔒 _Encrypted and used exclusively for your profile._"
        )
        msg = f"✅ *Gemini AI API key saved & encrypted!*\n_(Your message containing the key was deleted from chat.)_{shared_note}"
    await safe_send(context.bot, update.effective_chat.id, msg)


async def cmd_connect_t212(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    is_bg = lang == "bg"
    args = context.args or []

    if len(args) < 1:
        msg = (
            "🔑 *СВЪРЗВАНЕ НА ТВОЯ TRADING 212 ПОРТФЕЙЛ (READ-ONLY)*\n\n"
            "Изпрати командата във формат:\n"
            "`/connect_t212 ТВОЯТ_API_KEY ТВОЯТ_API_SECRET`\n\n"
            "🛡️ *Сигурност:*\n"
            "1. Създай ключ само с права за четене (*Read-Only*) от Trading 212 -> Settings -> API.\n"
            "2. Веднага след изпращане ботът *изтрива съобщението ти* и го криптира с `AES-128 Fernet`."
            if is_bg
            else "🔑 *CONNECT YOUR TRADING 212 PORTFOLIO (READ-ONLY)*\n\n"
            "Send your Read-Only API credentials:\n"
            "`/connect_t212 YOUR_API_KEY YOUR_API_SECRET`\n\n"
            "🛡️ *Security:*\n"
            "1. Generate a *Read-Only* key in Trading 212 -> Settings -> API.\n"
            "2. The bot *immediately deletes your message from chat* and encrypts the keys with `AES-128 Fernet`."
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
        "🔐 Ключовете са криптирани! Тествам връзката с Trading 212 API (~8 сек.)..."
        if is_bg
        else "🔐 Keys encrypted! Testing live Trading 212 API connection (~8s)..."
    )

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.t212_api_key_enc = encrypt_secret(api_key)
        db_user.t212_api_secret_enc = encrypt_secret(api_secret) if api_secret else None
        db_user.t212_env = env_mode
        session.commit()

        try:
            snap = await asyncio.to_thread(sync_user_portfolio, session, db_user)
            report = format_portfolio_telegram(snap, lang=lang)
            ok_hdr = (
                "✅ *Твоят Trading 212 портфейл е свързан и синхронизиран успешно!*"
                if is_bg
                else "✅ *Your Trading 212 portfolio is connected and synced!*"
            )
            await safe_edit(status_msg, f"{ok_hdr}\n\n{report}")
        except Exception as e:
            err_msg = (
                f"⚠️ Ключовете са записани, но възникна грешка при Trading 212 API:\n`{e}`"
                if is_bg
                else f"⚠️ Keys were saved, but Trading 212 API returned an error:\n`{e}`"
            )
            await safe_edit(status_msg, err_msg)


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    is_bg = lang == "bg"
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
                    update.effective_message,
                    "⚠️ Все още нямаш свързан личен Trading 212 портфейл.\nИзползвай `/connect_t212 API_KEY API_SECRET`!"
                    if is_bg
                    else "⚠️ No Trading 212 portfolio connected yet.\nUse `/connect_t212 API_KEY API_SECRET` to connect!",
                )
                return
            wait_msg = await update.effective_message.reply_text(
                "🔄 Извличам твоя портфейл на живо от Trading 212 API..."
                if is_bg
                else "🔄 Fetching your live portfolio from Trading 212 API..."
            )
            try:
                db_user = session.query(User).filter(User.id == user.id).first()
                snap = await asyncio.to_thread(sync_user_portfolio, session, db_user)
            except Exception as e:
                await safe_edit(wait_msg, f"❌ Trading 212 error: `{e}`")
                return
            await wait_msg.delete()

        report = format_portfolio_telegram(snap, lang=lang)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 Обнови от Trading 212" if is_bg else "🔄 Sync T212 Live",
                        callback_data="act_sync_t212",
                    ),
                    InlineKeyboardButton(
                        "🏆 Топ 3 за покупка днес" if is_bg else "🏆 Top 3 Buys Today",
                        callback_data="act_check_alerts",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📅 Месечен отчет" if is_bg else "📅 Monthly Check",
                        callback_data="act_monthly_check",
                    ),
                    InlineKeyboardButton(
                        "🤖 Попитай Агента" if is_bg else "🤖 Ask Buffett Agent",
                        callback_data="agent_portfolio",
                    ),
                ],
            ]
        )
        await safe_reply(update.effective_message, report, reply_markup=kb)


async def cmd_monthly_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        report = generate_monthly_check_report(session, db_user)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🏆 Избери от Топ 3 днес" if is_bg else "🏆 Pick from Top 3 Today",
                    callback_data="act_check_alerts",
                ),
                InlineKeyboardButton(
                    "🔄 Синхронизирай T212" if is_bg else "🔄 Sync T212",
                    callback_data="act_sync_t212",
                ),
            ],
            [
                InlineKeyboardButton("🎯 Watchlist", callback_data="act_watchlist"),
                InlineKeyboardButton(
                    "🤖 Попитай Агента" if is_bg else "🤖 Ask Buffett Agent",
                    callback_data="agent_top3",
                ),
            ],
        ]
    )
    await safe_reply(update.effective_message, report, reply_markup=kb)


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    with get_session() as session:
        text, kb = format_watchlist_telegram(session, lang=get_lang(user))
    await safe_reply(update.effective_message, text, reply_markup=kb)


def _run_watchlist_check_isolated() -> list[int]:
    with get_session() as s:
        items = check_watchlist_alerts(s, None, True)
        return [w.id for w in items]


def _run_portfolio_eval_isolated(user_id: int) -> list[dict]:
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
        return evaluate_user_portfolio_undervalued(s, u, snap, lang=get_lang(u))


async def cmd_check_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    chat = update.effective_chat
    wait_msg = await chat.send_message(
        "🔎 Проверявам живите цени и Вътрешната стойност на общия Watchlist И на компаниите в твоя портфейл (~5 сек.)..."
        if is_bg
        else "🔎 Scanning live prices & Buffett Intrinsic Value for BOTH the shared Watchlist AND your portfolio (~5s)..."
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
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    question = " ".join(context.args or []).strip()

    if not question:
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            has_gemini = bool(_get_gemini_api_key(user=db_user, session=session))

        if is_bg:
            ai_badge = "✅ *Gemini AI активен* (живи новини + задълбочен анализ)" if has_gemini else "💡 *Количествен режим* _(за свободен AI чат сложи безплатен ключ с `/set_gemini_key`)_"
            rows = [
                [InlineKeyboardButton("🏆 Сравни Топ 3 днес (Нова vs Портфейл)", callback_data="agent_top3")],
                [InlineKeyboardButton("📊 Кои позиции от портфейла ми да подсиля?", callback_data="agent_portfolio")],
                [InlineKeyboardButton("🛡️ Как да разпределя парите (буфер, кредит, акции)?", callback_data="agent_budget")],
                [InlineKeyboardButton("🧠 Анализ на конкретна акция (INTU / META / KO)", callback_data="act_buffett_menu")],
            ]
            if not has_gemini:
                rows.append([InlineKeyboardButton("🔑 Как да активирам безплатен Gemini AI ключ?", callback_data="act_gemini_help")])
            msg = (
                "🤖 *БЪФЕТ & МЪНГЪР ИНВЕСТИЦИОНЕН АГЕНТ*\n\n"
                f"{ai_badge}\n\n"
                "Избери готов въпрос долу *или ми напиши свободен въпрос направо в чата* "
                "(напр. _„Да подсиля ли META или да купя INTU?“_):"
            )
        else:
            ai_badge = "✅ *Gemini AI Active* (live Google Search news + deep analysis)" if has_gemini else "💡 *Quantitative Mode* _(unlock free-form AI chat via `/set_gemini_key`)_"
            rows = [
                [InlineKeyboardButton("🏆 Compare Top 3 Today (New vs Portfolio)", callback_data="agent_top3")],
                [InlineKeyboardButton("📊 Which portfolio holdings should I reinforce?", callback_data="agent_portfolio")],
                [InlineKeyboardButton("🛡️ How to allocate cash (buffer, debt, stocks)?", callback_data="agent_budget")],
                [InlineKeyboardButton("🧠 Deep Stock Scorecard (INTU / META / KO)", callback_data="act_buffett_menu")],
            ]
            if not has_gemini:
                rows.append([InlineKeyboardButton("🔑 How to add a free Gemini AI key?", callback_data="act_gemini_help")])
            msg = (
                "🤖 *BUFFETT & MUNGER INVESTMENT AGENT*\n\n"
                f"{ai_badge}\n\n"
                "Pick a prompt below *or type any investing question directly in chat* "
                "(e.g. _\"Should I reinforce META or open a new position in INTU?\"_):"
            )
        await safe_reply(update.effective_message, msg, reply_markup=InlineKeyboardMarkup(rows))
        return

    await _run_agent_question(update.effective_chat, user, question)


async def _run_agent_question(chat, user: User, question: str) -> None:
    is_bg = get_lang(user) == "bg"
    wait_msg = await chat.send_message(
        "🤖 Анализирам портфейла ти, буфера, кредитите и живите пазарни данни по Бъфет и Мънгър..."
        if is_bg
        else "🤖 Analyzing your portfolio, cash buffer, loans, and live valuations using Buffett & Munger rules..."
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
                    InlineKeyboardButton(
                        "🏆 Избери от Топ 3 днес" if is_bg else "🏆 Pick from Top 3 Today",
                        callback_data="act_check_alerts",
                    ),
                    InlineKeyboardButton(
                        "📅 Месечен отчет" if is_bg else "📅 Monthly Check",
                        callback_data="act_monthly_check",
                    ),
                ]
            ]
        )
        await safe_edit(wait_msg, answer, reply_markup=kb)
    except Exception as e:
        await safe_edit(wait_msg, f"❌ Error: `{e}`")


async def _handle_pick_stock(chat, user: User, ticker: str) -> None:
    lang = get_lang(user)
    is_bg = lang == "bg"
    wait_msg = await chat.send_message(
        f"🎯 Подготвям персонален план за покупка на *{ticker}*..."
        if is_bg
        else f"🎯 Preparing a personalized Buffett buy ticket for *{ticker}*...",
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

            score = compute_quantitative_buffett_scorecard(metrics, watch_item, lang=lang)
            cur_sym = "$" if metrics.get("currency") == "USD" else ("p " if metrics.get("currency") == "GBX" else "€")
            price = metrics.get("price") or (watch_item.last_price if watch_item else 0.0) or 0.0
            inv_budget = float(db_user.monthly_invest_budget or 0.0)
            buf_missing = max(0.0, db_user.emergency_buffer_target - db_user.emergency_buffer_current)
            investable = (
                round(max(0.0, inv_budget - min(buf_missing, max(50.0, inv_budget * 0.45))), 2)
                if (buf_missing > 0 and inv_budget > 0)
                else inv_budget
            )
            suggested_eur = round(max(20.0, investable * 0.35), 2) if investable > 0 else 50.0

            is_chip = ticker.upper() in SEMICONDUCTOR_SHORT_TICKERS
            semi_pct = float(latest_snap.semiconductor_pct) if latest_snap else 0.0

            warnings = []
            if is_chip and semi_pct >= 20.0:
                warnings.append(
                    f"⚠️ *Секторно предупреждение:* Полупроводниците ти са `{semi_pct:.1f}%` (>20% таван)."
                    if is_bg
                    else f"⚠️ *Sector Cap Warning:* Your semiconductor exposure is `{semi_pct:.1f}%` (>20% cap)."
                )
            if buf_missing > 0:
                warnings.append(
                    f"🛡️ *Буфер:* Ликвидният ти буфер е `€{db_user.emergency_buffer_current:,.2f}` / `€{db_user.emergency_buffer_target:,.2f}` (липсват `€{buf_missing:,.2f}`)."
                    if is_bg
                    else f"🛡️ *Liquidity Buffer:* `€{db_user.emergency_buffer_current:,.2f}` / `€{db_user.emergency_buffer_target:,.2f}` (missing `€{buf_missing:,.2f}`)."
                )

            is_eu_etf = ticker.upper() in {"VWCE", "SEC0", "XDWU", "ESIF", "BGX", "IWDA", "SXR8", "CSPX"}
            if is_bg:
                tax_note = "0% данък капиталова печалба в България (регулиран пазар в ЕС/ЕИП)" if is_eu_etf else "10% данък капиталова печалба в БГ (САЩ/извън ЕС) и 10% дивидент в САЩ"
                held_status_line = "🎯 *Статус в портфейла ти:* Нова компания (подходяща за добавяне към US пай или като нова позиция)."
            else:
                tax_note = "EU-regulated UCITS ETF (tax-efficient in most EU countries; 0% CGT in Bulgaria)" if is_eu_etf else "US/Non-EU single stock (standard local CGT & US dividend withholding apply)"
                held_status_line = "🎯 *Portfolio Status:* New Watchlist company (not currently held — ready to open a new position)."

            if latest_snap and latest_snap.raw_json:
                try:
                    parsed = parse_t212_snapshot(json.loads(latest_snap.raw_json))
                    for pie in parsed.get("pies") or []:
                        for inst in pie.get("instruments") or []:
                            if inst["ticker"].upper() == ticker.upper():
                                held_status_line = (
                                    f"💼 *Статус в портфейла ти:* Вече я притежаваш в *{pie['name']}* — `€{inst['value']:,.2f}` (дял `{inst['current_pct']:.1f}%` / цел `{inst['expected_pct']:.0f}%`)."
                                    if is_bg
                                    else f"💼 *Portfolio Status:* Already held in *{pie['name']}* — `€{inst['value']:,.2f}` (weight `{inst['current_pct']:.1f}%` / target `{inst['expected_pct']:.0f}%`)."
                                )
                                break
                except Exception:
                    pass

            if is_bg:
                lines = [
                    f"🎯 *ИЗБРАНА ПОЗИЦИЯ ЗА ИНВЕСТИЦИЯ: `{ticker}` ({metrics.get('name', ticker)})*",
                    "",
                    held_status_line,
                    f"• *Жива цена:* `{cur_sym}{price:,.2f}`",
                    f"• *Вътрешна стойност (IV):* `{cur_sym}{score['iv_low']:,.2f} – {cur_sym}{score['iv_high']:,.2f}`",
                    f"• *Целева цена за покупка (Target Buy):* `<= {cur_sym}{score['target_buy']:,.2f}`",
                    f"• *Присъда:* *{score['verdict']}*",
                    f"• *Данък:* _{tax_note}_",
                    "",
                    f"💶 *Предложена сума за вход:* *`€{suggested_eur:,.2f}`* _(от наличните `€{investable:,.2f}` след резервиране за буфер)_",
                ]
            else:
                lines = [
                    f"🎯 *SELECTED BUY TICKET: `{ticker}` ({metrics.get('name', ticker)})*",
                    "",
                    held_status_line,
                    f"• *Live Price:* `{cur_sym}{price:,.2f}`",
                    f"• *Intrinsic Value Range:* `{cur_sym}{score['iv_low']:,.2f} – {cur_sym}{score['iv_high']:,.2f}`",
                    f"• *Target Buy Price:* `<= {cur_sym}{score['target_buy']:,.2f}`",
                    f"• *Verdict:* *{score['verdict']}*",
                    f"• *Tax Note:* _{tax_note}_",
                    "",
                    f"💶 *Suggested Entry Allocation:* *`€{suggested_eur:,.2f}`* _(from `€{investable:,.2f}` investable after buffer reserve)_",
                ]
            if warnings:
                lines.append("")
                lines.extend(warnings)

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"✅ Потвърждавам: {ticker}" if is_bg else f"✅ Confirm Pick: {ticker}",
                            callback_data=f"confirm_{ticker}_{int(suggested_eur)}",
                        ),
                        InlineKeyboardButton(
                            "🧠 Пълен Бъфет анализ" if is_bg else "🧠 Full Buffett Scorecard",
                            callback_data=f"buffett_{ticker}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Обратно към Топ 3" if is_bg else "⬅️ Back to Top 3",
                            callback_data="act_check_alerts",
                        )
                    ],
                ]
            )
        await safe_edit(wait_msg, "\n".join(lines), reply_markup=kb)
    except Exception as e:
        await safe_edit(wait_msg, f"❌ Error loading *{ticker}*: `{e}`")


async def cmd_add_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    is_bg = lang == "bg"
    args = context.args or []
    if len(args) < 1:
        await safe_reply(
            update.effective_message,
            "🤝 *ДОБАВЯНЕ НА АКЦИЯ В ОБЩИЯ WATCHLIST (ЗА ВСИЧКИ)*\n\n"
            "1️⃣ `/add_watch ТИКЪР` _(ботът сам изчислява IV и Target Buy!)_\n"
            "2️⃣ `/add_watch ТИКЪР TARGET_BUY [IV_LOW] [IV_HIGH]`"
            if is_bg
            else "🤝 *ADD A STOCK TO THE SHARED WATCHLIST (FOR ALL USERS)*\n\n"
            "1️⃣ `/add_watch TICKER` _(auto-computes Buffett IV & Target Buy!)_\n"
            "2️⃣ `/add_watch TICKER TARGET_BUY [IV_LOW] [IV_HIGH]`",
        )
        return

    ticker = args[0].strip().upper()
    wait_msg = await update.effective_message.reply_text(
        f"🔎 Извличам финансови данни и Бъфет оценка за *{ticker}*..."
        if is_bg
        else f"🔎 Fetching fundamentals & Buffett valuation for *{ticker}*...",
        parse_mode=ParseMode.MARKDOWN,
    )
    metrics = await asyncio.to_thread(fetch_stock_fundamentals, ticker)
    score = compute_quantitative_buffett_scorecard(metrics, None, lang=lang)

    if len(args) >= 2:
        try:
            target_buy = float(args[1].replace(",", "."))
            iv_low = float(args[2].replace(",", ".")) if len(args) >= 3 else round(target_buy * 1.25, 2)
            iv_high = float(args[3].replace(",", ".")) if len(args) >= 4 else round(target_buy * 1.45, 2)
        except ValueError:
            await safe_edit(
                wait_msg,
                "❌ Цените трябва да са валидни числа (напр. `/add_watch MSFT 380 450 520`)."
                if is_bg
                else "❌ Prices must be valid numbers (e.g. `/add_watch MSFT 380 450 520`).",
            )
            return
    else:
        target_buy = score["target_buy"]
        iv_low = score["iv_low"]
        iv_high = score["iv_high"]

    author_name = user.full_name or (f"@{user.username}" if user.username else "Investor")
    company_name = metrics.get("name") or ticker
    currency = metrics.get("currency") or "USD"
    cur_price = metrics.get("price")
    roic = metrics.get("roic_est_pct")
    sym = "$" if currency == "USD" else ("p " if currency == "GBX" else "€")

    with get_session() as session:
        master_id = get_master_watchlist_owner_id(session)
        existing = (
            session.query(WatchlistItem)
            .filter(WatchlistItem.user_id == master_id, WatchlistItem.ticker == ticker)
            .first()
        )
        is_new = existing is None
        if existing:
            existing.target_buy_price = target_buy
            existing.intrinsic_value_low = iv_low
            existing.intrinsic_value_high = iv_high
            existing.last_price = cur_price or existing.last_price
            existing.company_name = company_name
            existing.currency = currency
            if roic is not None:
                existing.roic_pct = roic
            existing.notes = f"👤 {author_name} | {score['verdict']}"
        else:
            max_rank = (
                session.query(WatchlistItem.rank_order)
                .filter(WatchlistItem.user_id == master_id)
                .order_by(WatchlistItem.rank_order.desc())
                .first()
            )
            next_rank = (max_rank[0] + 1) if max_rank else 1
            item = WatchlistItem(
                user_id=master_id,
                rank_order=next_rank,
                ticker=ticker,
                company_name=company_name,
                currency=currency,
                moat_summary=localize_moat(ticker, None, "en"),
                roic_pct=roic,
                intrinsic_value_low=iv_low,
                intrinsic_value_high=iv_high,
                target_buy_price=target_buy,
                margin_of_safety_pct=score["mos_req"],
                last_price=cur_price,
                notes=f"👤 {author_name} | {score['verdict']}",
            )
            session.add(item)
        session.commit()

        other_users = (
            session.query(User)
            .filter(User.telegram_id != 0, User.telegram_id != user.telegram_id)
            .all()
        )

    if is_bg:
        action_verb = "добавена в" if is_new else "обновена в"
        in_zone = "🟢 *В ЗОНАТА ЗА ПОКУПКА СЕГА!*" if (cur_price and cur_price <= target_buy) else "⏳ Чакаме Target Buy"
        price_line = f"• *Текуща цена:* `{sym}{cur_price:,.2f}`\n" if cur_price else ""
        confirm_text = (
            f"✅ *{ticker} ({company_name})* беше {action_verb} *ОБЩИЯ WATCHLIST*!\n\n"
            f"{price_line}"
            f"• *Target Buy:* `<= {sym}{target_buy:,.2f}`\n"
            f"• *Вътрешна стойност:* `{sym}{iv_low:,.0f} – {sym}{iv_high:,.0f}`\n"
            f"• *Статус:* {in_zone}"
        )
    else:
        action_verb = "added to" if is_new else "updated in"
        in_zone = "🟢 *IN BUY ZONE RIGHT NOW!*" if (cur_price and cur_price <= target_buy) else "⏳ Waiting for Target Buy"
        price_line = f"• *Current Price:* `{sym}{cur_price:,.2f}`\n" if cur_price else ""
        confirm_text = (
            f"✅ *{ticker} ({company_name})* was {action_verb} the *SHARED WATCHLIST*!\n\n"
            f"{price_line}"
            f"• *Target Buy:* `<= {sym}{target_buy:,.2f}`\n"
            f"• *Intrinsic Value:* `{sym}{iv_low:,.0f} – {sym}{iv_high:,.0f}`\n"
            f"• *Status:* {in_zone}"
        )
    await safe_edit(wait_msg, confirm_text)

    for ou in other_users:
        o_bg = get_lang(ou) == "bg"
        bcast = (
            f"🤝 *ОБЩ WATCHLIST:* *{author_name}* добави `{ticker}` ({company_name}) | Target Buy: `<= {sym}{target_buy:,.2f}`"
            if o_bg
            else f"🤝 *SHARED WATCHLIST:* *{author_name}* shared `{ticker}` ({company_name}) | Target Buy: `<= {sym}{target_buy:,.2f}`"
        )
        try:
            await safe_send(context.bot, ou.telegram_id, bcast)
        except Exception:
            pass


async def cmd_del_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    if not context.args:
        await safe_reply(
            update.effective_message,
            "Използвай: `/del_watch ТИКЪР`" if is_bg else "Usage: `/del_watch TICKER`",
        )
        return
    ticker = context.args[0].strip().upper()
    with get_session() as session:
        master_id = get_master_watchlist_owner_id(session)
        deleted = (
            session.query(WatchlistItem)
            .filter(WatchlistItem.user_id == master_id, WatchlistItem.ticker == ticker)
            .delete()
        )
        session.commit()
    if deleted:
        await safe_reply(
            update.effective_message,
            f"🗑️ *{ticker}* беше премахната от общия Watchlist."
            if is_bg
            else f"🗑️ *{ticker}* was removed from the shared Watchlist.",
        )
    else:
        await safe_reply(
            update.effective_message,
            f"ℹ️ *{ticker}* не е намерена в общия Watchlist."
            if is_bg
            else f"ℹ️ *{ticker}* was not found in the shared Watchlist.",
        )


async def cmd_buffett(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    if not context.args:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("INTU (#1 Buy)", callback_data="buffett_INTU"),
                    InlineKeyboardButton("META", callback_data="buffett_META"),
                    InlineKeyboardButton("KO (Coca-Cola)", callback_data="buffett_KO"),
                ],
                [
                    InlineKeyboardButton("ASML", callback_data="buffett_ASML"),
                    InlineKeyboardButton("GOOGL", callback_data="buffett_GOOGL"),
                    InlineKeyboardButton("JPM", callback_data="buffett_JPM"),
                ],
            ]
        )
        await safe_reply(
            update.effective_message,
            "🧠 *БЪФЕТ & МЪНГЪР АНАЛИЗАТОР НА АКЦИИ*\n\nНапиши `/buffett ТИКЪР` (или просто тикъра, напр. `INTU`) или избери компания:"
            if is_bg
            else "🧠 *BUFFETT & MUNGER STOCK ANALYZER*\n\nSend `/buffett TICKER` (or just type a ticker like `INTU`, `META`, `KO`) or pick below:",
            reply_markup=kb,
        )
        return

    ticker = context.args[0].strip().upper()
    await _run_buffett_analysis_reply(update.effective_chat, user, ticker)


async def _run_buffett_analysis_reply(chat, user: User, ticker: str) -> None:
    is_bg = get_lang(user) == "bg"
    wait_msg = await chat.send_message(
        f"🧠 Извличам финансови отчети и изчислявам Owner Earnings и Вътрешна стойност по Бъфет за *{ticker}*..."
        if is_bg
        else f"🧠 Fetching financials and calculating Buffett Owner Earnings & Intrinsic Value for *{ticker}*...",
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
                        f"🎯 Избери {ticker} за покупка" if is_bg else f"🎯 Buy Ticket: {ticker}",
                        callback_data=f"pick_{ticker}",
                    ),
                    InlineKeyboardButton(
                        "➕ Добави в Watchlist" if is_bg else "➕ Add to Watchlist",
                        callback_data=f"quickwatch_{ticker}",
                    ),
                ]
            ]
        )
        await safe_edit(wait_msg, report, reply_markup=kb)
    except Exception as e:
        await safe_edit(wait_msg, f"❌ Analysis error for *{ticker}*: `{e}`")


async def cmd_set_loan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    args = context.args or []
    if len(args) < 4:
        with get_session() as session:
            loans = session.query(UserLoan).filter(UserLoan.user_id == user.id, UserLoan.is_active.is_(True)).all()
            loan_lines = []
            for l in loans:
                note_line = f"\n  └ 📋 _{l.notes}_" if l.notes else ""
                if is_bg:
                    loan_lines.append(
                        f"• *{l.lender_name}*: Главница `€{l.principal_balance:.2f}`, ГПР `{l.apr_percent:.2f}%`, "
                        f"Редовна `€{l.regular_payment:.2f}` + Предсрочна `€{l.early_principal_payment:.2f}`"
                        f"{note_line}"
                    )
                else:
                    loan_lines.append(
                        f"• *{l.lender_name}*: Principal `€{l.principal_balance:.2f}`, APR `{l.apr_percent:.2f}%`, "
                        f"Regular `€{l.regular_payment:.2f}` + Extra `€{l.early_principal_payment:.2f}`"
                        f"{note_line}"
                    )
        if loan_lines:
            current_str = "\n".join(loan_lines)
        else:
            current_str = (
                "✅ _В твоя профил няма въведени кредити._"
                if is_bg
                else "✅ _No active loans recorded in your private profile._"
            )
        msg = (
            f"💳 *ТВОИТЕ КРЕДИТИ (Инверсия на Мънгър):*\n{current_str}\n\n"
            "За добавяне/обновяване: `/set_loan БАНКА ГЛАВНИЦА ГПР% РЕДОВНА [ПРЕДСРОЧНА]`\n"
            "Пример: `/set_loan МоятаБанка 1000 15.5 100 50` _(при главница `0` се изтрива)_"
            if is_bg
            else f"💳 *YOUR LOANS (Munger's Inversion):*\n{current_str}\n\n"
            "To add or update a loan: `/set_loan BANK PRINCIPAL APR% REGULAR [EXTRA_PREPAYMENT]`\n"
            "Example: `/set_loan MyBank 1000 15.5 100 50` _(set principal to `0` to mark paid off)_"
        )
        await safe_reply(update.effective_message, msg)
        return

    lender = args[0].strip()
    try:
        principal = float(args[1].replace(",", "."))
        apr = float(args[2].replace(",", "."))
        reg_pmt = float(args[3].replace(",", "."))
        early_pmt = float(args[4].replace(",", ".")) if len(args) >= 5 else 0.0
    except ValueError:
        await safe_reply(
            update.effective_message,
            "❌ Моля, въведи валидни числа." if is_bg else "❌ Please enter valid numbers for principal, APR, and payments.",
        )
        return

    with get_session() as session:
        loan = session.query(UserLoan).filter(UserLoan.user_id == user.id, UserLoan.lender_name == lender).first()
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
            update.effective_message,
            f"✅ Кредитът към *{lender}* е записан (`€{principal:.2f}` @ `{apr:.2f}%` ГПР)!"
            if is_bg
            else f"✅ Loan with *{lender}* saved (`€{principal:.2f}` @ `{apr:.2f}%` APR)!",
        )
    else:
        await safe_reply(
            update.effective_message,
            f"🎉 Кредитът към *{lender}* е маркиран като изплатен (`€0.00`)!"
            if is_bg
            else f"🎉 Congrats! Loan with *{lender}* marked as paid off (`€0.00`)!",
        )


async def cmd_set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    is_bg = lang == "bg"
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

        ord_str = format_ordinal(db_user.payday_day, lang)
        if db_user.monthly_income > 0 or db_user.monthly_invest_budget > 0:
            buf_icon = "✅" if db_user.emergency_buffer_current >= db_user.emergency_buffer_target else "⚠️"
            dynamic_block = "\n".join(f"• {l}" for l in dynamic_lines)
            if is_bg:
                status_lines = (
                    f"• Нетен месечен доход: `€{db_user.monthly_income:.2f}` (Отчет: {ord_str} число)\n"
                    f"• Месечна сума за буфер/инвестиции: `€{db_user.monthly_invest_budget:.2f}`\n"
                    f"• Ликвиден буфер: `€{db_user.emergency_buffer_current:.2f}` / цел `€{db_user.emergency_buffer_target:.2f}` {buf_icon}\n\n"
                    f"📈 *ДИНАМИЧЕН ПЛАН ЗА ИНВЕСТИРАНЕ ДНЕС:*\n{dynamic_block}"
                )
            else:
                status_lines = (
                    f"• Monthly Net Income: `€{db_user.monthly_income:.2f}` (Report: {ord_str})\n"
                    f"• Monthly Invest/Buffer Budget: `€{db_user.monthly_invest_budget:.2f}`\n"
                    f"• Liquidity Buffer: `€{db_user.emergency_buffer_current:.2f}` / target `€{db_user.emergency_buffer_target:.2f}` {buf_icon}\n\n"
                    f"📈 *LIVE DYNAMIC INVESTMENT PLAN TODAY:*\n{dynamic_block}"
                )
        else:
            status_lines = (
                f"_Все още не си въвел своя месечен бюджет. Автоматичен отчет: *{ord_str} число*._"
                if is_bg
                else f"_Your monthly budget is blank. Automated monthly report: *{ord_str} of the month*._"
            )

        msg = (
            f"💰 *ТВОЯТ ЛИЧЕН МЕСЕЧЕН БЮДЖЕТ:*\n{status_lines}\n\n"
            "Промяна: `/set_budget ДОХОД ИНВЕСТ_БЮДЖЕТ [БУФЕР] [ДЕН]`\n"
            "🛡️ Само буфер: `/set_buffer СУМА` | 📅 Ден на отчета: `/set_payday ДЕН`"
            if is_bg
            else f"💰 *YOUR PRIVATE MONTHLY BUDGET:*\n{status_lines}\n\n"
            "Update: `/set_budget INCOME INVEST_BUDGET [BUFFER] [REPORT_DAY]`\n"
            "🛡️ Buffer only: `/set_buffer AMOUNT` | 📅 Report day: `/set_payday DAY`"
        )
        await safe_reply(update.effective_message, msg)
        return

    try:
        income = float(args[0].replace(",", "."))
        inv_budget = float(args[1].replace(",", "."))
        buf = float(args[2].replace(",", ".")) if len(args) >= 3 else user.emergency_buffer_current
        new_payday = int(args[3]) if len(args) >= 4 else None
        if new_payday is not None and not (1 <= new_payday <= 31):
            raise ValueError
    except ValueError:
        await safe_reply(
            update.effective_message,
            "❌ Моля, въведи валидни числа (напр. `/set_budget 1200 150 500`)."
            if is_bg
            else "❌ Please provide valid numbers (e.g. `/set_budget 1500 250 500`).",
        )
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

    ord_str = format_ordinal(db_user.payday_day, lang)
    await safe_reply(
        update.effective_message,
        (
            f"✅ Бюджетът е обновен! Доход: `€{income:.2f}` | Инвестиции/буфер: `€{inv_budget:.2f}` | Буфер: `€{buf:.2f}` | Отчет: `{ord_str} число`"
            if is_bg
            else f"✅ Budget updated! Income: `€{income:.2f}` | Invest/Buffer: `€{inv_budget:.2f}` | Buffer: `€{buf:.2f}` | Report: `{ord_str}`"
        ),
        reply_markup=get_reply_keyboard(db_user),
    )


async def cmd_set_buffer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    is_bg = get_lang(user) == "bg"
    args = context.args or []
    if len(args) < 1:
        await safe_reply(
            update.effective_message,
            f"🛡️ *Ликвиден буфер:* `€{user.emergency_buffer_current:,.2f}` / цел `€{user.emergency_buffer_target:,.2f}`\n"
            "Промяна: `/set_buffer ТЕКУЩА_СУМА [ЦЕЛ]`"
            if is_bg
            else f"🛡️ *Liquidity Buffer:* `€{user.emergency_buffer_current:,.2f}` / target `€{user.emergency_buffer_target:,.2f}`\n"
            "Update: `/set_buffer CURRENT_AMOUNT [TARGET]`",
        )
        return
    try:
        cur_buf = float(args[0].replace(",", "."))
        target_buf = float(args[1].replace(",", ".")) if len(args) >= 2 else user.emergency_buffer_target
    except ValueError:
        await safe_reply(update.effective_message, "❌ Invalid number. Example: `/set_buffer 150`")
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.emergency_buffer_current = max(0.0, cur_buf)
        db_user.emergency_buffer_target = max(0.0, target_buf)
        session.commit()

    await safe_reply(
        update.effective_message,
        f"✅ Ликвидният буфер е обновен: `€{max(0.0, cur_buf):,.2f}` / цел `€{max(0.0, target_buf):,.2f}`!"
        if is_bg
        else f"✅ Liquidity buffer updated: `€{max(0.0, cur_buf):,.2f}` / target `€{max(0.0, target_buf):,.2f}`!",
    )


async def cmd_set_payday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    is_bg = lang == "bg"
    args = context.args or []
    if len(args) != 1:
        ord_str = format_ordinal(user.payday_day, lang)
        await safe_reply(
            update.effective_message,
            f"📅 Текущ ден за автоматичен месечен отчет: *{ord_str} число*.\nПромяна: `/set_payday ДЕН` (1–31)"
            if is_bg
            else f"📅 Current automated monthly report day: *{ord_str} of the month*.\nUpdate: `/set_payday DAY` (1–31)",
        )
        return
    try:
        day = int(args[0].strip())
        if not (1 <= day <= 31):
            raise ValueError
    except ValueError:
        await safe_reply(
            update.effective_message,
            "❌ Въведи число от 1 до 31." if is_bg else "❌ Please enter a day between 1 and 31.",
        )
        return

    with get_session() as session:
        db_user = session.query(User).filter(User.id == user.id).first()
        db_user.payday_day = day
        session.commit()
        session.refresh(db_user)

    ord_str = format_ordinal(day, lang)
    await safe_reply(
        update.effective_message,
        f"✅ Денят за автоматичен месечен отчет е сменен на *{ord_str} число*!"
        if is_bg
        else f"✅ Automated monthly report day updated to the *{ord_str} of each month*!",
        reply_markup=get_reply_keyboard(db_user),
    )


async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user = get_or_register_user(update.effective_user)
    lang = get_lang(user)
    is_bg = lang == "bg"
    data = query.data or ""

    if data == "lang_en":
        await _apply_language_change(query.message, user, "en")
        return
    if data == "lang_bg":
        await _apply_language_change(query.message, user, "bg")
        return

    if data == "act_sync_t212":
        if not user.t212_api_key_enc:
            await safe_reply(
                query.message,
                "⚠️ Първо свържи своя Trading 212 API ключ с `/connect_t212`."
                if is_bg
                else "⚠️ Connect your Read-Only Trading 212 API key first via `/connect_t212`.",
            )
            return
        wait_msg = await query.message.reply_text(
            "🔄 Синхронизирам портфейла на живо от Trading 212..."
            if is_bg
            else "🔄 Syncing your live portfolio from Trading 212..."
        )
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            try:
                snap = await asyncio.to_thread(sync_user_portfolio, session, db_user)
                report = format_portfolio_telegram(snap, lang=get_lang(db_user))
                await safe_edit(wait_msg, report)
            except Exception as e:
                await safe_edit(wait_msg, f"❌ Sync error: `{e}`")

    elif data == "act_monthly_check":
        with get_session() as session:
            db_user = session.query(User).filter(User.id == user.id).first()
            report = generate_monthly_check_report(session, db_user)
        await safe_reply(query.message, report)

    elif data == "act_watchlist":
        with get_session() as session:
            text, kb = format_watchlist_telegram(session, lang=lang)
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
        await cmd_set_budget(update, context)

    elif data == "agent_top3":
        q = (
            "Сравни Обединения Топ 3 днес (подценените акции от моя портфейл срещу общия Watchlist): по-добре ли е да подсиля своя позиция или да добавя нова, и колко да заделя за буфера?"
            if is_bg
            else "Compare today's Unified Top 3 (undervalued holdings in my portfolio vs. shared Watchlist): should I reinforce an existing holding or add a new stock, and how much goes to my cash buffer?"
        )
        await _run_agent_question(query.message.chat, user, q)

    elif data == "agent_portfolio":
        q = (
            "Анализирай позициите в моя Trading 212 портфейл днес — кои да подсиля и от кои сектори да се пазя?"
            if is_bg
            else "Analyze my Trading 212 portfolio today — which undervalued holdings should I reinforce and which sectors should I cap?"
        )
        await _run_agent_question(query.message.chat, user, q)

    elif data == "agent_budget":
        q = (
            "Как да разпределя най-разумно месечния си доход между ликвидния буфер, кредита и инвестициите по Бъфет и Мънгър?"
            if is_bg
            else "How should I allocate my monthly income between my liquidity buffer, high-interest debt, and investments using Buffett & Munger rules?"
        )
        await _run_agent_question(query.message.chat, user, q)

    elif data.startswith("pick_"):
        ticker = data.split("_", 1)[1].strip().upper()
        await _handle_pick_stock(query.message.chat, user, ticker)

    elif data.startswith("confirm_"):
        parts = data.split("_")
        ticker = parts[1].upper() if len(parts) > 1 else "?"
        amount = parts[2] if len(parts) > 2 else "50"
        msg = (
            f"✅ *Изборът ти е записан:* `{ticker}` (`€{amount}` в Trading 212).\n"
            f"💡 _Не забравяй буфера (`€{user.emergency_buffer_current:.0f}/€{user.emergency_buffer_target:.0f}`) и натисни `🔄 Синхронизирай T212` след покупката!_"
            if is_bg
            else f"✅ *Buy pick noted:* `{ticker}` (`€{amount}` in Trading 212).\n"
            f"💡 _Remember your cash buffer (`€{user.emergency_buffer_current:.0f}/€{user.emergency_buffer_target:.0f}`), and tap `🔄 Sync T212 Live` after buying!_"
        )
        await safe_reply(query.message, msg)

    elif data.startswith("buffett_"):
        ticker = data.split("_", 1)[1].strip().upper()
        await _run_buffett_analysis_reply(query.message.chat, user, ticker)

    elif data.startswith("quickwatch_"):
        ticker = data.split("_", 1)[1].strip().upper()
        context.args = [ticker]
        await cmd_add_watch(update, context)


async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if "Портфейл" in text or "Portfolio" in text:
        await cmd_portfolio(update, context)
    elif "Месечен отчет" in text or "Monthly Check" in text:
        await cmd_monthly_check(update, context)
    elif "Watchlist" in text:
        await cmd_watchlist(update, context)
    elif any(k in text for k in ("ценови аларми", "Топ 3", "Аларми", "Top 3", "Alerts")):
        await cmd_check_alerts(update, context)
    elif "Бъфет Агент" in text or "Buffett Agent" in text:
        context.args = []
        await cmd_ask(update, context)
    elif "Бъфет Анализ" in text or "Buffett Analysis" in text:
        context.args = []
        await cmd_buffett(update, context)
    elif any(k in text for k in ("Кредит", "Бюджет", "Loans", "Budget")):
        await cmd_set_loan(update, context)
        await cmd_set_budget(update, context)
    elif "Trading 212" in text or "Connect T212" in text:
        context.args = []
        await cmd_connect_t212(update, context)
    elif "Език" in text or "Language" in text:
        context.args = []
        await cmd_lang(update, context)
    elif "Помощ" in text or text in ("ℹ️ Help", "Help"):
        await cmd_help(update, context)
    elif len(text) <= 6 and text.replace(".", "").isalpha():
        user = get_or_register_user(update.effective_user)
        await _run_buffett_analysis_reply(update.effective_chat, user, text.upper())
    elif len(text) >= 4:
        user = get_or_register_user(update.effective_user)
        await _run_agent_question(update.effective_chat, user, text)


async def scheduled_watchlist_alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly Monday scan: pushes localized Unified Top 3 & Buy Alerts to every user."""
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
            hdr = (
                "📅 *СЕДМИЧЕН ПРЕГЛЕД НА ПАЗАРА (ПОНЕДЕЛНИК)*"
                if get_lang(u) == "bg"
                else "📅 *WEEKLY MARKET SCAN (MONDAY)*"
            )
        try:
            await safe_send(context.bot, chat_id, f"{hdr}\n\n{text}", reply_markup=kb)
        except Exception as e:
            logger.warning("Failed to send weekly alert to user %s: %s", chat_id, e)


async def scheduled_monthly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily check: sends each user's localized dynamic monthly report on their configured payday."""
    now = datetime.now()
    today_day = now.day
    last_day_of_month = calendar.monthrange(now.year, now.month)[1]
    with get_session() as session:
        all_users = session.query(User).filter(User.telegram_id != 0).all()
        users = [u for u in all_users if min(max(1, u.payday_day), last_day_of_month) == today_day]
        for u in users:
            try:
                if u.t212_api_key_enc:
                    await asyncio.to_thread(sync_user_portfolio, session, u)
                lang = get_lang(u)
                report = generate_monthly_check_report(session, u)
                btn_lbl = "🏆 Избери от Топ 3 днес" if lang == "bg" else "🏆 Pick from Top 3 Today"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(btn_lbl, callback_data="act_check_alerts")]])
                ord_str = format_ordinal(u.payday_day, lang)
                hdr = (
                    f"🔔 *АВТОМАТИЧЕН МЕСЕЧЕН ОТЧЕТ ({ord_str} число)*"
                    if lang == "bg"
                    else f"🔔 *AUTOMATED MONTHLY REPORT ({ord_str} of the month)*"
                )
                await safe_send(context.bot, u.telegram_id, f"{hdr}\n\n{report}", reply_markup=kb)
            except Exception as e:
                logger.warning("Failed to send monthly report to user %s: %s", u.telegram_id, e)


async def post_init_setup(application: Application) -> None:
    """Register clean international '/' slash commands menu in Telegram."""
    commands = [
        BotCommand("start", "Start & main menu"),
        BotCommand("check_alerts", "Unified Top 3 (Watchlist vs Portfolio) & buy alerts"),
        BotCommand("portfolio", "Your live Trading 212 portfolio"),
        BotCommand("monthly_check", "Dynamic monthly financial check & plan"),
        BotCommand("watchlist", "Shared Buffett & Munger Watchlist"),
        BotCommand("ask", "Ask the Buffett & Munger AI Agent"),
        BotCommand("buffett", "10-point Buffett stock scorecard (/buffett INTU)"),
        BotCommand("add_watch", "Add a stock to the shared Watchlist"),
        BotCommand("set_budget", "Set income, invest budget & cash buffer"),
        BotCommand("set_buffer", "Update emergency cash buffer"),
        BotCommand("set_loan", "Track a high-interest loan (Munger's Inversion)"),
        BotCommand("set_payday", "Set day of month for automated monthly report"),
        BotCommand("connect_t212", "Connect Read-Only Trading 212 API"),
        BotCommand("set_gemini_key", "Add free Gemini AI key (encrypted, no .env)"),
        BotCommand("lang", "Switch language: English (default) / Bulgarian"),
        BotCommand("help", "Full user guide & instructions"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Registered %d native Telegram slash commands.", len(commands))
    except Exception as e:
        logger.warning("Could not register Telegram slash commands: %s", e)


class _HealthHandler(BaseHTTPRequestHandler):
    """Lightweight HTTP health endpoint for Render Free Web Service & UptimeRobot (0 AI tokens)."""

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
        return


def start_health_server_if_needed() -> ThreadingHTTPServer | None:
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
    start_health_server_if_needed()

    init_db()
    seed_owner_profile()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("⚠️ Missing TELEGRAM_BOT_TOKEN in .env / Environment Variables!")
        return

    app = Application.builder().token(token).post_init(post_init_setup).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("lang", cmd_lang))
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
        app.job_queue.run_daily(scheduled_watchlist_alert_job, time=dtime(hour=15, minute=0), days=(1,))
        app.job_queue.run_daily(scheduled_monthly_report_job, time=dtime(hour=9, minute=0))

    logger.info("Buffett & Munger Telegram Bot started polling...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
