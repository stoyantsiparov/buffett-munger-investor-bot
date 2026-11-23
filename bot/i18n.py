"""Bilingual localization helpers (English default + Bulgarian switchable via /lang)."""

from __future__ import annotations

SUPPORTED_LANGS = ("en", "bg")


def get_lang(user=None) -> str:
    """Return 'bg' if user selected Bulgarian, otherwise default to 'en' (English)."""
    lang = (getattr(user, "language", None) or "en").strip().lower()
    return "bg" if lang == "bg" else "en"


def format_ordinal(day: int, lang: str = "en") -> str:
    """Format a day of month (1-31) in natural English (1st, 2nd, 13th) or Bulgarian (1-во, 2-ро, 13-то)."""
    if lang == "bg":
        if day in (1, 21, 31):
            return f"{day}-во"
        if day in (2, 22):
            return f"{day}-ро"
        if day in (7, 8, 27, 28):
            return f"{day}-мо"
        if day in (5, 6, 25, 26, 29, 30):
            return f"{day}-о"
        return f"{day}-то"
    # English ordinal
    if 11 <= (day % 100) <= 13:
        return f"{day}th"
    rem = day % 10
    if rem == 1:
        return f"{day}st"
    if rem == 2:
        return f"{day}nd"
    if rem == 3:
        return f"{day}rd"
    return f"{day}th"


KNOWN_MOAT_EN: dict[str, str] = {
    # Portfolio core holdings
    "V": "Two-Sided Network Effect + Global Payment Toll Bridge • Stable",
    "MA": "Two-Sided Network Effect + Global Payment Toll Bridge • Stable",
    "SPGI": "Institutional Credit Rating Oligopoly + S&P 500 Benchmarks • Stable",
    "MSFT": "High Switching Costs (Enterprise Cloud / Office / Windows) • Widening",
    "GOOGL": "Network Effect + Data + Search / YouTube / Cloud Ecosystem • Stable",
    "META": "Global Social Network Effect (3.2B+ Users) • Stable",
    "AMZN": "Logistics Scale + AWS Cloud Leader + Prime Ecosystem • Widening",
    "ZTS": "Patents & #1 Global Animal Health Leadership • Stable",
    "UNH": "#1 Scale in Health Insurance + Optum Data Platform • Stable",
    "JPM": "Scale + #1 Fortress Balance Sheet in US Banking • Stable",
    "NFLX": "#1 Global Streaming Scale + Subscription Flywheel • Widening",
    "AVGO": "Custom AI Silicon + VMware Switching Costs • Stable",
    "NVDA": "CUDA Software Ecosystem + AI Accelerator Leader • Watch Regulatory Risk",
    # Curated Buffett Watchlist
    "INTU": "Switching Costs + Network Effect (QuickBooks, TurboTax) • Stable",
    "FDS": "Switching Costs + Institutional Financial Data (95%+ Retention) • Stable",
    "CRM": "Switching Costs + Enterprise System of Record Ecosystem • Stable",
    "ROL": "Route Density + Orkin Brand (80% Recurring Subscriptions) • Widening",
    "PH": "Switching Costs + Certified Aerospace & Motion Parts • Widening",
    "ADP": "Payroll Switching Costs + $40B+ Interest-Earning Float • Stable",
    "COST": "Scale Cost Advantage + Kirkland Brand (92%+ Renewal) • Widening",
    "BX": "#1 Scale ($1.4T AUM) + Brand + Perpetual Capital • Stable",
    "ICE": "Exchange & Clearing Monopoly/Duopoly (NYSE, Futures) • Widening",
    "UNP": "Efficient Scale (32,000-Mile Rail Network) • Stable",
    "LLY": "GLP-1 Patents (through 2036) + Manufacturing Scale • Stable",
    "KO": "#1 Global Beverage Brand + 225+ Bottling Partners • Stable",
    "ASML": "100% Monopoly in EUV / High-NA Lithography • Widening",
    "AMAT": "Wafer Fab Equipment Oligopoly + Recurring Services • Widening",
    "TXN": "Low-Cost 300mm Fabs + 80,000 Analog Chips • Widening",
    "ANET": "Cloud Networking Switching Costs (EOS Software) • Widening",
    "SHOP": "E-Commerce Switching Costs + Merchant Ecosystem • Widening",
    "RMBS": "DDR5 Server Memory Patents + Interface Chips • Stable",
    "ASND": "TransCon Patented Platform + 10-Yr Orphan Drug Exclusivity • Widening",
    "APP": "Axon AI Ad Algorithm + Data Flywheel • Widening",
    "UBER": "Two-Sided Mobility & Delivery Network Effect • Stable",
    "RR.L": "Widebody Aero Engine Duopoly (TotalCare) + Defense • Widening",
    "CRWD": "High Switching Costs (Falcon Agent) + Threat Graph • Stable",
}

KNOWN_MOAT_BG: dict[str, str] = {
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
    "NVDA": "CUDA софтуерна екосистема + Лидер в AI ускорителите • Под наблюдение (SEC)",
}


def localize_moat(ticker: str, stored_moat: str | None, lang: str = "en") -> str:
    """Return a clean moat summary in the user's preferred language ('en' or 'bg')."""
    t = ticker.strip().upper()
    if lang == "en":
        if t in KNOWN_MOAT_EN:
            return KNOWN_MOAT_EN[t]
        if stored_moat and not any("\u0400" <= ch <= "\u04FF" for ch in stored_moat):
            return stored_moat
        return "Wide Economic Moat"
    # Bulgarian
    if stored_moat and any("\u0400" <= ch <= "\u04FF" for ch in stored_moat):
        return stored_moat
    if t in KNOWN_MOAT_BG:
        return KNOWN_MOAT_BG[t]
    return stored_moat or "Широк конкурентен ров"
