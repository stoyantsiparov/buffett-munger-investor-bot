"""Multi-user Trading 212 Read-Only API service and portfolio diagnostics."""

import base64
import json
import time
import urllib.error
import urllib.request
from sqlalchemy.orm import Session
from bot.crypto import decrypt_secret
from bot.models import PortfolioSnapshot, User

SEMICONDUCTOR_SHORT_TICKERS = {
    "NVDA",
    "AVGO",
    "SEC0",
    "ASML",
    "AMD",
    "TSM",
    "INTC",
    "QCOM",
    "TXN",
    "AMAT",
    "MU",
    "MRVL",
    "LRCX",
    "KLAC",
    "ONTO",
    "RMBS",
    "ARM",
}


def clean_t212_ticker(raw_ticker: str) -> str:
    """Convert Trading 212 internal ticker (e.g. 'NVDA_US_EQ', 'SEC0d_EQ', 'FB_US_EQ') to clean symbol."""
    if not raw_ticker:
        return "?"
    if raw_ticker == "FB_US_EQ":
        return "META"
    base = raw_ticker.split("_")[0]
    if base.endswith("d") and len(base) > 3 and base[:-1].isupper():
        return base[:-1]
    return base


def _make_get(base_url: str, auth_header: str):
    def get(path: str):
        req = urllib.request.Request(
            base_url + path,
            headers={"Authorization": auth_header, "User-Agent": "BuffettMungerTelegramBot/1.0"},
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    wait = 6 * (attempt + 1)
                    time.sleep(wait)
                    continue
                body = e.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {e.code} on {path}: {body}") from None

    return get


def fetch_user_t212_data(api_key: str, api_secret: str | None = None, env: str = "live") -> dict:
    """Fetch read-only account, cash, positions, pies, and pending orders from Trading 212 API."""
    host = "demo" if (env or "live").lower() == "demo" else "live"
    base_url = f"https://{host}.trading212.com/api/v0"
    auth = (
        "Basic " + base64.b64encode(f"{api_key}:{api_secret}".encode("utf-8")).decode("utf-8")
        if api_secret
        else api_key
    )
    get = _make_get(base_url, auth)

    out: dict = {}
    out["account_info"] = get("/equity/account/info")
    time.sleep(1.2)
    out["cash"] = get("/equity/account/cash")
    time.sleep(1.2)

    try:
        out["positions"] = get("/equity/portfolio")
    except RuntimeError as e:
        if "404" not in str(e):
            raise
        out["positions"] = get("/equity/positions")

    time.sleep(1.2)
    try:
        pies = get("/equity/pies")
        pie_details = []
        for p in pies or []:
            pid = p.get("id")
            if pid is not None:
                time.sleep(1.5)
                try:
                    pie_details.append({"summary": p, "details": get(f"/equity/pies/{pid}")})
                except RuntimeError as e:
                    pie_details.append({"summary": p, "error": str(e)})
        out["pies"] = pie_details
    except RuntimeError:
        out["pies"] = []

    time.sleep(1.2)
    try:
        out["orders"] = get("/equity/orders")
    except RuntimeError:
        out["orders"] = []

    return out


def parse_t212_snapshot(raw_data: dict) -> dict:
    """Compute portfolio metrics, pie summaries, and semiconductor sector concentration."""
    cash = raw_data.get("cash") or {}
    positions = raw_data.get("positions") or []
    pies = raw_data.get("pies") or []
    orders = raw_data.get("orders") or []

    total_value = float(cash.get("total") or 0.0)
    free_cash = float(cash.get("free") or 0.0)
    pie_cash = float(cash.get("pieCash") or 0.0)
    unrealized_ppl = float(cash.get("ppl") or 0.0)
    realized_result = float(cash.get("result") or 0.0)
    # Market value of open positions = total - free_cash - pie_cash
    invested_market_value = round(max(0.0, total_value - free_cash - pie_cash), 2)

    # Map instrument values in account currency from pies if available
    instrument_eur_value: dict[str, float] = {}
    instrument_eur_ppl: dict[str, float] = {}
    pies_parsed = []

    for p_entry in pies:
        summary = p_entry.get("summary") or {}
        details = p_entry.get("details") or {}
        settings = details.get("settings") or {}
        res = summary.get("result") or {}

        pie_name = settings.get("name") or f"Pie #{summary.get('id', '?')}"
        pie_val = float(res.get("priceAvgValue") or 0.0)
        pie_inv = float(res.get("priceAvgInvestedValue") or 0.0)
        pie_ppl = float(res.get("priceAvgResult") or 0.0)
        pie_c = float(summary.get("cash") or 0.0)
        pie_goal = float(settings.get("goal") or 0.0)

        pie_instruments = []
        for inst in details.get("instruments") or []:
            raw_t = inst.get("ticker", "")
            short_t = clean_t212_ticker(raw_t)
            ires = inst.get("result") or {}
            ival = float(ires.get("priceAvgValue") or 0.0)
            ippl = float(ires.get("priceAvgResult") or 0.0)
            instrument_eur_value[short_t] = instrument_eur_value.get(short_t, 0.0) + ival
            instrument_eur_ppl[short_t] = instrument_eur_ppl.get(short_t, 0.0) + ippl

            pie_instruments.append(
                {
                    "ticker": short_t,
                    "value": ival,
                    "ppl": ippl,
                    "expected_pct": round(float(inst.get("expectedShare") or 0.0) * 100, 2),
                    "current_pct": round(float(inst.get("currentShare") or 0.0) * 100, 2),
                }
            )

        pies_parsed.append(
            {
                "id": summary.get("id"),
                "name": pie_name,
                "value": pie_val,
                "invested": pie_inv,
                "ppl": pie_ppl,
                "cash": pie_c,
                "goal": pie_goal,
                "instruments": pie_instruments,
            }
        )

    # Calculate semiconductor exposure
    semi_eur = 0.0
    semi_breakdown: dict[str, float] = {}

    if instrument_eur_value:
        for short_t, val in instrument_eur_value.items():
            if short_t in SEMICONDUCTOR_SHORT_TICKERS:
                semi_eur += val
                semi_breakdown[short_t] = round(val, 2)
    else:
        for pos in positions:
            short_t = clean_t212_ticker(pos.get("ticker", ""))
            if short_t in SEMICONDUCTOR_SHORT_TICKERS:
                qty = float(pos.get("quantity") or 0.0)
                cur_p = float(pos.get("currentPrice") or 0.0)
                est_val = round(qty * cur_p, 2)
                semi_eur += est_val
                semi_breakdown[short_t] = est_val

    semi_pct = round((semi_eur / total_value * 100.0), 2) if total_value > 0 else 0.0

    pending_orders = []
    for o in orders:
        pending_orders.append(
            {
                "ticker": clean_t212_ticker(o.get("ticker", "")),
                "side": o.get("side", "BUY"),
                "value": float(o.get("value") or 0.0),
                "currency": o.get("currency", "EUR"),
                "status": o.get("status", "NEW"),
            }
        )

    return {
        "total_value": round(total_value, 2),
        "invested_value": round(invested_market_value, 2),
        "free_cash": round(free_cash, 2),
        "pie_cash": round(pie_cash, 2),
        "unrealized_ppl": round(unrealized_ppl, 2),
        "realized_result": round(realized_result, 2),
        "semiconductor_eur": round(semi_eur, 2),
        "semiconductor_pct": semi_pct,
        "semiconductor_breakdown": semi_breakdown,
        "positions_count": len(positions),
        "pending_orders_count": len(pending_orders),
        "pending_orders": pending_orders,
        "pies": pies_parsed,
    }


def save_snapshot_from_raw(session: Session, user: User, raw_data: dict) -> PortfolioSnapshot:
    """Parse raw Trading 212 JSON data and store a PortfolioSnapshot row for the user."""
    parsed = parse_t212_snapshot(raw_data)
    acc = raw_data.get("account_info") or {}
    if acc.get("id"):
        user.t212_account_id = str(acc["id"])
    if acc.get("currencyCode"):
        user.t212_currency = str(acc["currencyCode"])

    snap = PortfolioSnapshot(
        user_id=user.id,
        total_value=parsed["total_value"],
        invested_value=parsed["invested_value"],
        free_cash=parsed["free_cash"],
        pie_cash=parsed["pie_cash"],
        unrealized_ppl=parsed["unrealized_ppl"],
        realized_result=parsed["realized_result"],
        semiconductor_pct=parsed["semiconductor_pct"],
        positions_count=parsed["positions_count"],
        pending_orders_count=parsed["pending_orders_count"],
        raw_json=json.dumps(raw_data, ensure_ascii=False),
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap


def sync_user_portfolio(session: Session, user: User) -> PortfolioSnapshot:
    """Decrypt user's T212 keys, fetch live portfolio from T212 API, and store snapshot."""
    api_key = decrypt_secret(user.t212_api_key_enc)
    api_secret = decrypt_secret(user.t212_api_secret_enc)
    if not api_key:
        raise ValueError("Няма свързан Trading 212 API ключ. Използвай /connect_t212.")

    raw_data = fetch_user_t212_data(api_key, api_secret, user.t212_env)
    return save_snapshot_from_raw(session, user, raw_data)


def format_portfolio_telegram(snap: PortfolioSnapshot, currency: str = "€") -> str:
    """Format a PortfolioSnapshot into a rich Bulgarian Telegram report."""
    raw = json.loads(snap.raw_json) if snap.raw_json else {}
    parsed = parse_t212_snapshot(raw) if raw else None

    ppl_sign = "+" if snap.unrealized_ppl >= 0 else ""
    real_sign = "+" if snap.realized_result >= 0 else ""
    semi_status = (
        "⚠️ Над лимита (20-25%)"
        if snap.semiconductor_pct > 25.0
        else ("⚡ На горната граница (20-25%)" if snap.semiconductor_pct >= 20.0 else "✅ В норма (<20%)")
    )

    lines = [
        "📊 *ТВОЯТ TRADING 212 ПОРТФЕЙЛ*",
        f"🕒 _Обновен: {snap.created_at.strftime('%d.%m.%Y %H:%M UTC')}_",
        "",
        f"💰 *Обща стойност:* `{currency}{snap.total_value:,.2f}`",
        f"📈 *Инвестирани позиции:* `{currency}{snap.invested_value:,.2f}` ({snap.positions_count} актива)",
        f"🥧 *Кеш в пайове (Pending):* `{currency}{snap.pie_cash:,.2f}`",
        f"💵 *Свободен кеш:* `{currency}{snap.free_cash:,.2f}`",
        f"📉 *Текущ нереализиран P&L:* `{ppl_sign}{currency}{snap.unrealized_ppl:,.2f}`",
        f"🏆 *Реализирана печалба + дивиденти:* `{real_sign}{currency}{snap.realized_result:,.2f}`",
        "",
        f"🔬 *Сектор Полупроводници:* `{snap.semiconductor_pct:.2f}%` — {semi_status}",
    ]

    if parsed:
        semi_bd = parsed.get("semiconductor_breakdown") or {}
        if semi_bd:
            bd_str = ", ".join(f"{k}: {currency}{v:,.2f}" for k, v in semi_bd.items())
            lines.append(f"   └ _({bd_str})_")

        for pie in parsed.get("pies") or []:
            lines.append("")
            p_sign = "+" if pie["ppl"] >= 0 else ""
            lines.append(
                f"🥧 *{pie['name']}* — `{currency}{pie['value']:,.2f}` "
                f"(`{p_sign}{currency}{pie['ppl']:,.2f}`) | Кеш: `{currency}{pie['cash']:,.2f}`"
            )
            sorted_inst = sorted(pie["instruments"], key=lambda x: x["value"], reverse=True)
            for inst in sorted_inst:
                i_sign = "+" if inst["ppl"] >= 0 else ""
                dev = round(inst["current_pct"] - inst["expected_pct"], 1)
                dev_str = f"+{dev}%" if dev > 0 else f"{dev}%"
                lines.append(
                    f"  • `{inst['ticker']:<5}` {currency}{inst['value']:>6.2f} "
                    f"({i_sign}{currency}{inst['ppl']:.2f}) | Дял: {inst['current_pct']:.1f}%/цел {inst['expected_pct']:.0f}% ({dev_str})"
                )

        orders = parsed.get("pending_orders") or []
        if orders:
            lines.append("")
            lines.append(f"⏳ *Чакащи поръчки ({len(orders)} бр.):*")
            for o in orders:
                lines.append(f"  • `{o['side']}` *{o['ticker']}* за `{currency}{o['value']:.2f}` ({o['status']})")

    return "\n".join(lines)
