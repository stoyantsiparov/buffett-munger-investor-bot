"""Seed script for the owner's profile (configured via environment variables) and curated Buffett Watchlist."""

import json
import os
from pathlib import Path
from sqlalchemy.orm import Session
from bot.crypto import encrypt_secret
from bot.db import get_session, init_db
from bot.models import PortfolioSnapshot, User, UserLoan, WatchlistItem
from bot.t212_service import save_snapshot_from_raw, sync_user_portfolio

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BUFFETT_WATCHLIST = [
    {
        "rank_order": 1,
        "ticker": "INTU",
        "company_name": "Intuit",
        "currency": "USD",
        "moat_summary": "Разходи за смяна + Мрежови ефект (QuickBooks, TurboTax) • Стабилен",
        "roic_pct": 18.0,
        "intrinsic_value_low": 350.0,
        "intrinsic_value_high": 420.0,
        "target_buy_price": 285.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 273.61,
        "notes": "🟢 В зона за покупка днес (~15.7x GAAP P/E; +$3.3B кеш от продажбата на Mailchimp).",
    },
    {
        "rank_order": 2,
        "ticker": "FDS",
        "company_name": "FactSet Research Systems",
        "currency": "USD",
        "moat_summary": "Разходи за смяна + Финансови бази данни (95%+ задържане) • Стабилен",
        "roic_pct": 22.5,
        "intrinsic_value_low": 320.0,
        "intrinsic_value_high": 380.0,
        "target_buy_price": 265.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 311.93,
        "notes": "Нужен е още ~15% спад за 25% MoS; следи ръста на абонаментите (ASV > 6%).",
    },
    {
        "rank_order": 3,
        "ticker": "CRM",
        "company_name": "Salesforce",
        "currency": "USD",
        "moat_summary": "Разходи за смяна + Екосистема (System of Record) • Стабилен",
        "roic_pct": 15.5,
        "intrinsic_value_low": 178.0,
        "intrinsic_value_high": 240.0,
        "target_buy_price": 150.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 182.78,
        "notes": "Чакаме цена <= $150 и спад на Net Debt/EBITDA под 2.0x.",
    },
    {
        "rank_order": 4,
        "ticker": "ROL",
        "company_name": "Rollins (Orkin)",
        "currency": "USD",
        "moat_summary": "Гъстота на маршрутите + Марка Orkin (80% абонаменти) • Разширяващ се",
        "roic_pct": 17.5,
        "intrinsic_value_low": 27.3,
        "intrinsic_value_high": 34.2,
        "target_buy_price": 28.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 37.82,
        "notes": "CapEx едва 0.7% от приходите, 123% кешова конверсия — вечен нецикличен франчайз.",
    },
    {
        "rank_order": 5,
        "ticker": "PH",
        "company_name": "Parker-Hannifin (PAR)",
        "currency": "EUR",
        "moat_summary": "Разходи за смяна + Сертифицирани аерокосмически части • Разширяващ се",
        "roic_pct": 17.5,
        "intrinsic_value_low": 520.0,
        "intrinsic_value_high": 635.0,
        "target_buy_price": 480.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 798.30,
        "notes": "107% FCF конверсия, CapEx 2.1% — #1 индустриален компаундър.",
    },
    {
        "rank_order": 6,
        "ticker": "ADP",
        "company_name": "Automatic Data Processing",
        "currency": "USD",
        "moat_summary": "Разходи за смяна (Payroll) + $40.4B безплатен Float • Стабилен",
        "roic_pct": 35.8,
        "intrinsic_value_low": 220.0,
        "intrinsic_value_high": 264.0,
        "target_buy_price": 215.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 302.40,
        "notes": "Плаща заплатите на 1 от 6 работници в САЩ и печели $1.35B лихва върху float-а.",
    },
    {
        "rank_order": 7,
        "ticker": "COST",
        "company_name": "Costco Wholesale",
        "currency": "USD",
        "moat_summary": "Предимство в разходите от мащаб + Марка Kirkland • Разширяващ се",
        "roic_pct": 21.9,
        "intrinsic_value_low": 515.0,
        "intrinsic_value_high": 615.0,
        "target_buy_price": 540.0,
        "margin_of_safety_pct": 20.0,
        "last_price": 1102.90,
        "notes": "Любимият бизнес на Чарли Мънгър (92.3% подновяване), но на 53x P/E се чака голяма корекция.",
    },
    {
        "rank_order": 8,
        "ticker": "BX",
        "company_name": "Blackstone",
        "currency": "USD",
        "moat_summary": "Мащаб #1 ($1.4T AUM) + Марка + Заключен капитал • Стабилен",
        "roic_pct": 30.0,
        "intrinsic_value_low": 95.0,
        "intrinsic_value_high": 112.0,
        "target_buy_price": 80.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 111.73,
        "notes": "Капиталово лек генератор на такси (FRE); купува се при пазарна паника.",
    },
    {
        "rank_order": 9,
        "ticker": "ICE",
        "company_name": "Intercontinental Exchange",
        "currency": "USD",
        "moat_summary": "Борсов и клирингов монопол/дуопол (NYSE, фючърси) • Разширяващ се",
        "roic_pct": 11.0,
        "intrinsic_value_low": 140.0,
        "intrinsic_value_high": 155.0,
        "target_buy_price": 115.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 172.79,
        "notes": "Незаменими тол-мостове; чакаме да изплати дълга от покупката на MarketAxess.",
    },
    {
        "rank_order": 10,
        "ticker": "UNP",
        "company_name": "Union Pacific",
        "currency": "USD",
        "moat_summary": "Ефективен мащаб (32,000 мили жп мрежа) • Стабилен",
        "roic_pct": 16.5,
        "intrinsic_value_low": 180.0,
        "intrinsic_value_high": 217.0,
        "target_buy_price": 165.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 288.16,
        "notes": "Физически некопируем жп монопол (като BNSF на Бъфет).",
    },
    {
        "rank_order": 11,
        "ticker": "LLY",
        "company_name": "Eli Lilly & Co",
        "currency": "USD",
        "moat_summary": "Патенти (GLP-1 до 2036 г.) + Производствен мащаб • Стабилен",
        "roic_pct": 38.2,
        "intrinsic_value_low": 725.0,
        "intrinsic_value_high": 950.0,
        "target_buy_price": 700.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 1348.05,
        "notes": "83% брутен марж и 32% нетен марж, но се изисква MoS заради изтичането на патентите след 2036 г.",
    },
    {
        "rank_order": 12,
        "ticker": "KO",
        "company_name": "The Coca-Cola Company",
        "currency": "USD",
        "moat_summary": "Глобална марка №1 + 225+ бутилиращи партньора • Стабилен",
        "roic_pct": 15.8,
        "intrinsic_value_low": 65.0,
        "intrinsic_value_high": 79.0,
        "target_buy_price": 58.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 98.00,
        "notes": "Класиката на Бъфет ($13.6B Owner Earnings, >100% кешова конверсия), но скъпа на $98 (~31x P/OE).",
    },
    {
        "rank_order": 12,
        "ticker": "ASML",
        "company_name": "ASML Holding",
        "currency": "EUR",
        "moat_summary": "100% монопол в EUV/High-NA литографията • Разширяващ се",
        "roic_pct": 35.0,
        "intrinsic_value_low": 880.0,
        "intrinsic_value_high": 1050.0,
        "target_buy_price": 800.0,
        "margin_of_safety_pct": 25.0,
        "last_price": 1168.00,
        "notes": "Купува се отделно само след като делът на полупроводниците в портфейла падне под 20%.",
    },
    {
        "rank_order": 13,
        "ticker": "AMAT",
        "company_name": "Applied Materials",
        "currency": "USD",
        "moat_summary": "Олигопол в машините за чипове (WFE) + Сервиз • Разширяващ се",
        "roic_pct": 32.0,
        "intrinsic_value_low": 160.0,
        "intrinsic_value_high": 190.0,
        "target_buy_price": 140.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 185.00,
        "notes": "Чист баланс с нетен кеш; купува се при спад на сектор полупроводници < 20%.",
    },
    {
        "rank_order": 14,
        "ticker": "TXN",
        "company_name": "Texas Instruments",
        "currency": "USD",
        "moat_summary": "Ниски разходи (300mm фабове) + 80,000 аналогови чипа • Разширяващ се",
        "roic_pct": 28.0,
        "intrinsic_value_low": 170.0,
        "intrinsic_value_high": 215.0,
        "target_buy_price": 155.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 268.97,
        "notes": "Еталонен мениджмънт, фокусиран върху FCF на акция.",
    },
    {
        "rank_order": 15,
        "ticker": "ANET",
        "company_name": "Arista Networks",
        "currency": "USD",
        "moat_summary": "Разходи за смяна (EOS софтуер за облачни мрежи) • Разширяващ се",
        "roic_pct": 44.0,
        "intrinsic_value_low": 84.0,
        "intrinsic_value_high": 107.0,
        "target_buy_price": 95.0,
        "margin_of_safety_pct": 35.0,
        "last_price": 172.90,
        "notes": "$0 дълг и +$14.4B кеш, но висока зависимост от CapEx на MSFT и META.",
    },
    {
        "rank_order": 16,
        "ticker": "SHOP",
        "company_name": "Shopify",
        "currency": "USD",
        "moat_summary": "Разходи за смяна + Екосистема в e-commerce • Разширяващ се",
        "roic_pct": 30.0,
        "intrinsic_value_low": 70.0,
        "intrinsic_value_high": 85.0,
        "target_buy_price": 60.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 126.96,
        "notes": "Отличен основател-собственик и $0 дълг, но се чака значително охлаждане на P/E.",
    },
    {
        "rank_order": 17,
        "ticker": "RMBS",
        "company_name": "Rambus",
        "currency": "USD",
        "moat_summary": "Патенти в DDR5 сървърна памет + Интерфейсни чипове • Стабилен",
        "roic_pct": 25.0,
        "intrinsic_value_low": 48.0,
        "intrinsic_value_high": 62.0,
        "target_buy_price": 44.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 67.12,
        "notes": "$0 дълг и 80% брутен марж.",
    },
    {
        "rank_order": 18,
        "ticker": "ASND",
        "company_name": "Ascendis Pharma",
        "currency": "USD",
        "moat_summary": "Патентована платформа TransCon + Orphan Drug (10 г.) • Разширяващ се",
        "roic_pct": 20.0,
        "intrinsic_value_low": 195.0,
        "intrinsic_value_high": 245.0,
        "target_buy_price": 175.0,
        "margin_of_safety_pct": 35.0,
        "last_price": 252.73,
        "notes": "92.3% брутен марж; чакаме 4 поредни тримесечия чиста GAAP печалба.",
    },
    {
        "rank_order": 19,
        "ticker": "APP",
        "company_name": "AppLovin",
        "currency": "USD",
        "moat_summary": "AI алгоритъм Axon + Data Flywheel в рекламата • Разширяващ се",
        "roic_pct": 45.0,
        "intrinsic_value_low": 300.0,
        "intrinsic_value_high": 380.0,
        "target_buy_price": 230.0,
        "margin_of_safety_pct": 40.0,
        "last_price": 351.00,
        "notes": "88.7% брутен марж, но изисква 40% MoS заради зависимост от Apple iOS/Google Android.",
    },
    {
        "rank_order": 20,
        "ticker": "UBER",
        "company_name": "Uber Technologies",
        "currency": "USD",
        "moat_summary": "Двустранен мрежов ефект в превози и доставки • Стабилен",
        "roic_pct": 13.1,
        "intrinsic_value_low": 55.0,
        "intrinsic_value_high": 70.0,
        "target_buy_price": 48.0,
        "margin_of_safety_pct": 35.0,
        "last_price": 64.40,
        "notes": "$8.3B Owner Earnings, но изисква 35-40% MoS заради риска от автономни таксита (Waymo/Tesla).",
    },
    {
        "rank_order": 21,
        "ticker": "RR.L",
        "company_name": "Rolls-Royce Holdings",
        "currency": "GBX",
        "moat_summary": "Дуопол в големи авиодвигатели (TotalCare) + AUKUS • Разширяващ се",
        "roic_pct": 22.0,
        "intrinsic_value_low": 740.0,
        "intrinsic_value_high": 900.0,
        "target_buy_price": 630.0,
        "margin_of_safety_pct": 30.0,
        "last_price": 1310.0,
        "notes": "Отличен обрат (£3.3B чисти Owner Earnings, +£2.1B кеш), но на 1,310p (~33x P/OE) е скъпа.",
    },
    {
        "rank_order": 22,
        "ticker": "CRWD",
        "company_name": "CrowdStrike",
        "currency": "USD",
        "moat_summary": "Разходи за смяна (Falcon агент) + Threat Graph • Стабилен",
        "roic_pct": 14.0,
        "intrinsic_value_low": 62.0,
        "intrinsic_value_high": 91.0,
        "target_buy_price": 65.0,
        "margin_of_safety_pct": 40.0,
        "last_price": 229.44,
        "notes": "Висок SBC (~23% от приходите) изяжда GAAP печалбата.",
    },
]


def load_env_dict() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def seed_default_watchlist_for_user(session: Session, user: User) -> int:
    """Insert or update the 21 curated Buffett & Munger Watchlist stocks for a user."""
    existing_by_ticker = {
        w.ticker.upper(): w
        for w in session.query(WatchlistItem).filter(WatchlistItem.user_id == user.id).all()
    }
    count = 0
    for item_data in DEFAULT_BUFFETT_WATCHLIST:
        t = item_data["ticker"].upper()
        if t in existing_by_ticker:
            w = existing_by_ticker[t]
            w.rank_order = item_data["rank_order"]
            w.company_name = item_data["company_name"]
            w.currency = item_data["currency"]
            w.moat_summary = item_data["moat_summary"]
            w.roic_pct = item_data["roic_pct"]
            w.intrinsic_value_low = item_data["intrinsic_value_low"]
            w.intrinsic_value_high = item_data["intrinsic_value_high"]
            w.target_buy_price = item_data["target_buy_price"]
            w.margin_of_safety_pct = item_data["margin_of_safety_pct"]
            if w.last_price is None:
                w.last_price = item_data["last_price"]
            w.notes = item_data["notes"]
        else:
            w = WatchlistItem(user_id=user.id, **item_data)
            session.add(w)
            count += 1
    session.commit()
    return count


def seed_owner_profile() -> dict:
    """Initialize DB and seed the owner's profile from private environment variables + default Buffett Watchlist."""
    db_url_masked = init_db()
    env = load_env_dict()

    def _get(key: str, default: str = "") -> str:
        return (os.environ.get(key) or env.get(key) or default).strip()

    owner_tg_id = int(_get("OWNER_TELEGRAM_ID", "0") or 0)
    t212_key = _get("T212_API_KEY", "")
    t212_secret = _get("T212_API_SECRET", "")
    t212_env = _get("T212_ENV", "live") or "live"
    monthly_income = float(_get("OWNER_MONTHLY_INCOME", "0") or 0.0)
    invest_budget = float(_get("OWNER_INVEST_BUDGET", "0") or 0.0)
    buf_target = float(_get("OWNER_BUFFER_TARGET", "500") or 500.0)
    buf_current = float(_get("OWNER_BUFFER_CURRENT", "0") or 0.0)
    payday_day = int(_get("OWNER_PAYDAY_DAY", "1") or 1)
    owner_lang = (_get("OWNER_LANGUAGE", "en") or "en").lower()
    if owner_lang not in ("en", "bg"):
        owner_lang = "en"
    loan_json_raw = _get("OWNER_LOAN_JSON", "")

    with get_session() as session:
        user = session.query(User).filter(User.telegram_id == owner_tg_id).first() if owner_tg_id else None
        if not user:
            user = session.query(User).order_by(User.id.asc()).first()

        if not user:
            user = User(
                telegram_id=owner_tg_id,
                username="owner",
                full_name="Owner",
                language=owner_lang,
                t212_api_key_enc=encrypt_secret(t212_key) if t212_key else None,
                t212_api_secret_enc=encrypt_secret(t212_secret) if t212_secret else None,
                t212_env=t212_env,
                monthly_income=monthly_income,
                monthly_invest_budget=invest_budget,
                emergency_buffer_target=buf_target,
                emergency_buffer_current=buf_current,
                payday_day=payday_day,
                investment_plan=None,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        else:
            if t212_key and not user.t212_api_key_enc:
                user.t212_api_key_enc = encrypt_secret(t212_key)
            if t212_secret and not user.t212_api_secret_enc:
                user.t212_api_secret_enc = encrypt_secret(t212_secret)
            user.t212_env = t212_env
            session.commit()

        # Optional initial loan seeded privately via OWNER_LOAN_JSON environment variable
        loan = session.query(UserLoan).filter(UserLoan.user_id == user.id, UserLoan.is_active.is_(True)).first()
        if not loan and loan_json_raw:
            try:
                ld = json.loads(loan_json_raw)
                loan = UserLoan(
                    user_id=user.id,
                    lender_name=str(ld.get("lender_name", "Bank Loan")),
                    contract_number=ld.get("contract_number"),
                    principal_balance=float(ld.get("principal_balance", 0.0)),
                    apr_percent=float(ld.get("apr_percent", 0.0)),
                    regular_payment=float(ld.get("regular_payment", 0.0)),
                    early_principal_payment=float(ld.get("early_principal_payment", 0.0)),
                    early_fee_percent=float(ld.get("early_fee_percent", 0.5)),
                    due_day=int(ld.get("due_day", 15)),
                    is_active=True,
                    notes=ld.get("notes"),
                )
                session.add(loan)
                session.commit()
            except Exception:
                loan = None

        # Seed latest Trading 212 snapshot from local data/ or live API on fresh cloud DB
        existing_snap = (
            session.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.user_id == user.id)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        data_dir = PROJECT_ROOT / "data"
        json_files = sorted(data_dir.glob("portfolio_*.json"), reverse=True) if data_dir.exists() else []
        if not existing_snap and json_files:
            raw_data = json.loads(json_files[0].read_text(encoding="utf-8"))
            existing_snap = save_snapshot_from_raw(session, user, raw_data)
        elif not existing_snap and user.t212_api_key_enc:
            try:
                existing_snap = sync_user_portfolio(session, user)
            except Exception:
                pass

        # Seed curated Buffett Watchlist stocks
        added_watch = seed_default_watchlist_for_user(session, user)
        total_watch = session.query(WatchlistItem).filter(WatchlistItem.user_id == user.id).count()

        return {
            "db_url": db_url_masked,
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "has_encrypted_t212_key": bool(user.t212_api_key_enc),
            "loan_lender": loan.lender_name if loan else None,
            "loan_principal": loan.principal_balance if loan else 0.0,
            "loan_apr": loan.apr_percent if loan else 0.0,
            "snapshot_total_eur": existing_snap.total_value if existing_snap else 0.0,
            "snapshot_semi_pct": existing_snap.semiconductor_pct if existing_snap else 0.0,
            "watchlist_total": total_watch,
            "watchlist_added": added_watch,
        }


if __name__ == "__main__":
    summary = seed_owner_profile()
    print("Seed completed successfully:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
