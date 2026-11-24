"""Smart Buffett & Munger Playbook Router (v1.2.0).

Distills all 8 Buffett reference guides (01-thinking-frameworks through 08-industry-playbooks)
into bilingual (EN/BG), token-efficient modules that are routed on demand by industry and topic.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Industry Classification (maps yfinance sector/industry/ticker -> playbook)
# ---------------------------------------------------------------------------

BANK_TICKERS = {"JPM", "BAC", "WFC", "C", "USB", "PNC", "TFC", "GS", "MS", "KB", "HSBC", "ING", "DB"}
INSURANCE_TICKERS = {"BRK.B", "BRK-B", "BRK.A", "BRK-A", "CB", "PGR", "ALL", "TRV", "MET", "AFL", "AIG", "MKL", "ACGL"}
CONSUMER_BRAND_TICKERS = {"KO", "PEP", "PG", "COST", "WMT", "MCD", "SBUX", "NKE", "CL", "MDLZ", "KHC", "CELH", "PM", "MO", "UL"}
MEDIA_TICKERS = {"NFLX", "DIS", "SPOT", "TTWO", "EA", "RBLX", "WBD", "PARA", "NYT", "CMCSA", "LYV"}
ENERGY_UTILITY_TICKERS = {"OXY", "CVX", "XOM", "COP", "SHEL", "TTE", "BP", "NEE", "DUK", "SO", "CEG", "VST", "CCJ", "ENB", "KMI"}
RAIL_INDUSTRIAL_TICKERS = {"UNP", "CSX", "NSC", "CP", "CNI", "WM", "RSG", "CAT", "DE", "GE", "HON", "UPS", "FDX", "FIX", "NVT", "URI"}
CAUTIONARY_TICKERS = {"DAL", "UAL", "AAL", "LUV", "RYAAY", "JBLU", "SAVE", "CCL", "NCLH"}


def classify_industry_playbook(sector: str | None, industry: str | None, ticker: str | None = None) -> str:
    """Classify a company into one of Buffett's 8 industry playbooks from 08-industry-playbooks.md."""
    tk = (ticker or "").strip().upper()
    sec = (sector or "").strip().lower()
    ind = (industry or "").strip().lower()

    if tk in INSURANCE_TICKERS or "insurance" in ind or "reinsurance" in ind:
        return "insurance"
    if tk in BANK_TICKERS or "bank" in ind:
        return "banking"
    if tk in CAUTIONARY_TICKERS or any(k in ind for k in ("airline", "textile", "apparel manufacturing", "paper", "steel", "aluminum")):
        return "cautionary_cyclicals"
    if tk in RAIL_INDUSTRIAL_TICKERS or any(k in ind for k in ("rail", "freight", "trucking", "waste", "aerospace", "machinery", "industrial", "engineering", "construction")):
        return "railroads_industrials"
    if tk in ENERGY_UTILITY_TICKERS or sec in ("energy", "utilities") or any(k in ind for k in ("oil", "gas", "utility", "utilities", "electric", "renewable", "solar", "wind", "uranium", "nuclear", "pipeline")):
        return "energy_utilities"
    if tk in MEDIA_TICKERS or any(k in ind for k in ("entertainment", "media", "publishing", "broadcasting", "gaming", "electronic gaming", "movies", "music", "streaming")):
        return "media_entertainment"
    if tk in CONSUMER_BRAND_TICKERS or sec in ("consumer defensive", "consumer staples") or any(k in ind for k in ("beverage", "confection", "packaged foods", "household", "personal products", "tobacco", "restaurant", "retail", "discount stores", "apparel retail", "footwear", "grocery")):
        return "consumer_brands"
    if sec in ("technology", "communication services") or any(k in ind for k in ("software", "semiconductor", "internet", "information technology", "computer", "cloud", "cyber", "electronics", "telecom")):
        return "technology"
    if sec == "financial services":
        return "financial_markets"
    if sec == "healthcare":
        return "healthcare"
    return "general_franchise"


# ---------------------------------------------------------------------------
# 2. Industry Playbook Summaries & AI Rules (from 08-industry-playbooks.md)
# ---------------------------------------------------------------------------

INDUSTRY_PLAYBOOKS: dict[str, dict[str, str]] = {
    "insurance": {
        "title_en": "Insurance Playbook (Float & Underwriting Discipline)",
        "title_bg": "Плейбук Застраховане (Float & Андеррайтинг дисциплина)",
        "badge_en": "🛡️ Insurance Engine • Check Combined Ratio (<100%) & Float Cost",
        "badge_bg": "🛡️ Застрахователен модел • Следи Combined Ratio (<100%) и цена на Float-а",
        "lesson_en": (
            "Buffett Rule (National Indemnity / GEICO): 'Collect now, pay later.' Float is a hidden asset ONLY if Combined Ratio < 100% "
            "(negative-cost capital). Never chase market share at underwriting losses; low-cost direct distribution (GEICO) wins."
        ),
        "lesson_bg": (
            "Правило на Бъфет (National Indemnity / GEICO): „Събираш премиите днес, плащаш щетите по-късно.“ Float-ът е скрит актив САМО ако Combined Ratio < 100% "
            "(безплатен капитал с отрицателна цена). Никога не се гони пазарен дял със загуба от застраховане."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Insurance]: Evaluate (1) Underwriting discipline — would they shrink volume rather than write underpriced risks? "
            "Combined Ratio < 100% means negative-cost float; >100% is expensive borrowing. (2) Float durability (P&C cannot be run on like bank deposits). "
            "(3) Cost advantage (GEICO direct model) & conservative float investment. Warn against making up underwriting losses with investment income."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Застраховане]: Оцени (1) Андеррайтинг дисциплината — Combined Ratio < 100% означава Float с отрицателна цена (плащат ти да инвестираш); "
            ">100% е скъп заем. (2) Размер и стабилност на Float-а. (3) Разходно предимство (GEICO директен модел) и консервативен портфейл."
        ),
    },
    "banking": {
        "title_en": "Banking Playbook (15–20x Leverage & Credit Culture)",
        "title_bg": "Плейбук Банки (15–20x ливъридж & Кредитна дисциплина)",
        "badge_en": "🏦 Banking Model • Target ROE > 15%, ROA > 1.2%, CET1 > 10%, Efficiency < 55%",
        "badge_bg": "🏦 Банков модел • Цел ROE > 15%, ROA > 1.2%, CET1 > 10%, Efficiency < 55%",
        "lesson_en": (
            "Buffett Rule (Wells Fargo 1990 vs. Irish Banks 2008): Banks operate at 15–20x leverage — small credit losses can wipe out equity. "
            "Look for low-cost sticky deposits, Efficiency Ratio < 55%, ROA > 1.2%, ROE > 15%, and conservative lending. Beware 'cheap P/E' value traps in banking crises."
        ),
        "lesson_bg": (
            "Правило на Бъфет (Wells Fargo 1990 срещу Ирландските банки 2008): Банките работят с 15–20x ливъридж — малки кредитни загуби могат да заличат капитала. "
            "Търси евтина депозитна база, ROA > 1.2%, ROE > 15%, CET1 > 10% и консервативен кредитен портфейл. „Евтиното“ при банкова криза често е капан."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Banking]: Standard FCF/Net-Debt metrics do not apply to banks. Evaluate: ROE (>15%), ROA (>1.2%), CET1 (>10%), Efficiency Ratio (<55%), "
            "low-cost deposit moat, and credit culture. Cite Buffett's Irish Banks lesson (lost 89% buying 'cheap' opaque banks) and post-2020 caution on black-box derivatives/long-duration bond losses."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Банки]: Стандартните FCF/Net Debt метрики не важат за банки. Оцени: ROE (>15%), ROA (>1.2%), CET1 (>10%), Efficiency Ratio (<55%), "
            "евтина депозитна база и кредитен риск. Помни урока на Бъфет от ирландските банки (-89% при покупка на „евтина“ непрозрачна банка)."
        ),
    },
    "consumer_brands": {
        "title_en": "Consumer Brands & Retail Playbook (See's Candies / Coca-Cola / Costco)",
        "title_bg": "Плейбук Потребителски марки & Ритейл (See's Candies / Coca-Cola / Costco)",
        "badge_en": "🍬 Consumer Franchise • Pricing Power (+5–10%) & Economic Goodwill Shield",
        "badge_bg": "🍬 Потребителски франчайз • Ценова сила (+5–10%) & Икономически Goodwill срещу инфлация",
        "lesson_en": (
            "Buffett Rule (See's Candies 1972 / Coca-Cola 1988 / NFM): Asset-light brands with emotional share-of-mind and +5–10% pricing power "
            "compound through inflation with minimal CapEx (Economic Goodwill). In retail, only relentless low-cost scale flywheels (Costco / Mrs. B) survive; watch for Dexter Shoe import/channel disruption."
        ),
        "lesson_bg": (
            "Правило на Бъфет (See's Candies 1972 / Coca-Cola 1988 / NFM): Леки на активи марки с емоционална лоялност и +5–10% ценова сила "
            "преодоляват инфлацията без тежък CapEx (Икономически Goodwill). В ритейла оцеляват само най-нискоразходните лидери (Costco / Mrs. B); пази се от урока Dexter Shoe."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Consumer & 03-Goodwill]: Apply Buffett's 1991 Franchise 3-part test (needed/desired, no close substitute in customers' minds, unregulated pricing). "
            "Check +5-10% pricing power, repeat purchase habit, gross margin trend, and Economic Goodwill (high ROIC on tangible assets = best inflation shield). For retail, test the Mrs. B / Costco low-cost flywheel vs. e-commerce/private-label erosion."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Потребителски марки & 03-Goodwill]: Приложи 3-точковия тест на Бъфет за Франчайз (желана стока, без близък заместител в съзнанието на клиента, без ценова регулация). "
            "Провери ценовата сила (+5–10%), навика за повторна покупка, брутния марж и Икономическия Goodwill (най-добрата защита от инфлация)."
        ),
    },
    "technology": {
        "title_en": "Technology & Software Playbook (Apple Ecosystem vs. IBM Disruption)",
        "title_bg": "Плейбук Технологии & Софтуер (Apple екосистема срещу IBM ерозия)",
        "badge_en": "💻 Tech & SaaS Lens • Structural Switching Costs/Network vs. SBC & Disruption Risk",
        "badge_bg": "💻 Технологичен/SaaS модел • Разходи за смяна/Мрежов ефект срещу SBC & Технологичен риск",
        "lesson_en": (
            "Buffett Rule (Apple 2016 vs. IBM 2011): A temporary tech lead (early Nokia) is NOT a moat. Invest only when backed by structural switching costs "
            "(ERP/OS/workflows, NRR > 120%), network effects, or an irreversible data flywheel — AND deduct Stock-Based Compensation (SBC) from Owner Earnings. If 10-yr durability isn't predictable, put in the Too Hard Pile."
        ),
        "lesson_bg": (
            "Правило на Бъфет (Apple 2016 срещу IBM 2011): Временната технологична преднина НЕ е ров. Търси структурни разходи за смяна "
            "(екосистема/ERP/данни, NRR > 120%) или мрежови ефекти — и ВИНАГИ изваждай SBC (акции за персонала) от Owner Earnings. Ако 10-годишната трайност е неясна → „Твърде трудно“ (Too Hard Pile)."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Technology]: Distinguish temporary tech leadership (fragile) from structural moats: switching costs (Apple iOS, mission-critical SaaS/ERP), "
            "network effects, or data flywheel. Check if AI/cloud could erode the moat like IBM in 2011. Strictly penalize excessive SBC (>20% of OCF) and share dilution."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Технологии]: Разграничи временна технологична преднина (крехка) от структурен ров: високи разходи за смяна (Apple екосистема, критичен софтуер), "
            "мрежов ефект или data flywheel. Провери дали AI не руши рова (като облака при IBM през 2011 г.) и накажи висок SBC (>20% от OCF) или разводняване."
        ),
    },
    "media_entertainment": {
        "title_en": "Media & Platforms Playbook (Subscription Stickiness vs. Toll-Bridge Erosion)",
        "title_bg": "Плейбук Медии & Платформи (Абонаментна лоялност срещу ерозия на монопола)",
        "badge_en": "🎬 Media/Platform Lens • Subscription/IP Stickiness vs. Ad Cyclicality & Content CapEx",
        "badge_bg": "🎬 Медиен/Платформен модел • Абонаментна лоялност & IP срещу рекламна цикличност",
        "lesson_en": (
            "Buffett Rule (Washington Post / Buffalo News Exit): 'Industry fate outweighs management capability.' Moat durability matters more than current width. "
            "Modern winners need subscription stickiness, irreplaceable IP, or data network effects — and real cash flow after content amortization."
        ),
        "lesson_bg": (
            "Правило на Бъфет (Washington Post / Buffalo News): „Съдбата на индустрията е по-силна от таланта на мениджмънта.“ Трайността на рова е по-важна от текущата му ширина. "
            "Търси абонаментна незаменимост, уникално IP и реален свободен кеш след разходите за съдържание."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Media]: Apply the Buffalo News lesson (tech disruption can destroy decades-old toll bridges). "
            "Check whether revenue comes from sticky user subscriptions/IP (stronger) or cyclical attention/advertising (more fragile in recessions), and whether content spend leaves true Owner Earnings."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Медии]: Приложи урока от Buffalo News (технологичната промяна може да разруши стари монополи). "
            "Оцени дали приходите идват от трайни абонаменти/уникално IP или от проциклична реклама, и колко реален кеш остава след разходите за съдържание."
        ),
    },
    "energy_utilities": {
        "title_en": "Energy & Utilities Playbook (BHE Regulated Returns & PetroChina/OXY Reserve Value)",
        "title_bg": "Плейбук Енергетика & Ютилити (BHE регулирана доходност & OXY/PetroChina резерви)",
        "badge_en": "⚡ Energy/Utility Model • Check CapEx Intensity, Debt/Rates & Commodity Cycle Position",
        "badge_bg": "⚡ Енергиен/Ютилити модел • Следи CapEx тежестта, лихвения риск и фазата на суровинния цикъл",
        "lesson_en": (
            "Buffett Rule (BHE / PetroChina 2002 vs. ConocoPhillips 2008): Regulated utilities offer stable inelastic cash flows and absorb large capital, "
            "but are sensitive to interest rates and heavy CapEx. For oil/gas/uranium producers, buy ONLY below reserve value with low breakeven costs — NEVER at peak commodity prices (ConocoPhillips lesson)."
        ),
        "lesson_bg": (
            "Правило на Бъфет (BHE / PetroChina 2002 срещу ConocoPhillips 2008): Регулираните мрежи/ютилити дават предвидим кешов поток, но гълтат огромен CapEx и страдат при високи лихви. "
            "При добивни компании (петрол/газ/уран) се купува САМО с голям дисконт спрямо резервите и ниска себестойност — НИКОГА на върха на суровинния цикъл."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Energy/Utilities]: For utilities/power generators, assess regulatory friendliness, massive ongoing CapEx vs D&A, and interest-rate/debt burden. "
            "For oil/gas/commodity producers (PetroChina/OXY vs ConocoPhillips 2008 mistake), assess reserve life, low-cost production curve, capital discipline (buybacks/dividends vs empire drilling), and cycle price risk."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Енергетика/Ютилити]: При ютилити/енергийни мрежи оцени регулаторната възвръщаемост, тежкия CapEx спрямо D&A и дълговата чувствителност към лихвите. "
            "При добивни компании (PetroChina/OXY срещу грешката с ConocoPhillips през 2008 г.) оцени себестойността на добива, резервите, капиталовата дисциплина и къде сме в суровинния цикъл."
        ),
    },
    "railroads_industrials": {
        "title_en": "Railroads & Heavy Industrials Playbook (BNSF Efficient Scale vs. CapEx Consumption)",
        "title_bg": "Плейбук Железници & Индустрия (BNSF незаменима мрежа срещу тежък поддържащ CapEx)",
        "badge_en": "🚂 Physical Moat / Industrial • Irreplicable Scale, but Maintenance CapEx > D&A",
        "badge_bg": "🚂 Индустриален/Мрежов ров • Незаменима инфраструктура, но Поддържащ CapEx > Амортизациите",
        "lesson_en": (
            "Buffett Rule (BNSF Railway 2009): Physical networks and efficient-scale industrials can be irreplicable for 100 years, "
            "but they are capital consumers — true Maintenance CapEx often exceeds GAAP Depreciation, meaning real distributable Owner Earnings are lower than reported Net Income."
        ),
        "lesson_bg": (
            "Правило на Бъфет (BNSF Railway 2009): Физическите мрежи и индустриалните лидери с ефективен мащаб са незаменими за десетилетия, "
            "но са „консуматори на капитал“ — реалният поддържащ CapEx често надхвърля счетоводните амортизации (D&A), затова истинските Owner Earnings са по-ниски от GAAP печалбата."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Railroads/Industrials]: Evaluate physical network irreplicability / efficient scale and cost advantage (Operating Ratio = OpEx/Revenue < 65% for rail). "
            "Crucially check CapEx vs. D&A: per Buffett's BNSF rule, capital-intensive industrials must spend more than depreciation just to stand still, reducing true Owner Earnings."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Железници/Индустрия]: Оцени незаменимостта на физическата инфраструктура / ефективния мащаб и разходното предимство. "
            "Задължително сравни CapEx спрямо D&A: по правилото на Бъфет за BNSF, капиталоемките индустрии харчат над амортизациите само за да поддържат бизнеса, което намалява истинските Owner Earnings."
        ),
    },
    "cautionary_cyclicals": {
        "title_en": "Cautionary Cyclical / Commodity Playbook (Airlines & Berkshire Textiles Warning)",
        "title_bg": "Предупредителен Плейбук: Суровинни/Циклични капани (Авиокомпании & Текстил)",
        "badge_en": "⚠️ Commodity / Cyclical Trap Risk • 'Standees at a Parade' — High CapEx, Low Pricing Power",
        "badge_bg": "⚠️ Риск от суровинен/цикличен капан • „Изправени на парад“ — Тежък CapEx без ценова сила",
        "lesson_en": (
            "Buffett Warning (Berkshire Textiles 1965–1985 & Airlines): 'When a management with a reputation for brilliance tackles a business with a reputation for bad economics, "
            "it is the reputation of the business that remains intact.' In undifferentiated industries, every CapEx upgrade is like standing on tiptoes at a parade — nobody sees better, everyone works harder."
        ),
        "lesson_bg": (
            "Предупреждение на Бъфет (Текстилният бизнес на Berkshire 1965–1985 & Авиокомпаниите): „Когато блестящ мениджмънт се сблъска с бизнес с лоша икономика, "
            "репутацията на бизнеса остава непокътната.“ В отрасли без диференциация всяка нова инвестиция е като изправяне на пръсти на парад — всички харчат капитал, а маржовете остават ниски."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Cautionary Airlines/Textiles & 07-Value Traps]: Apply Buffett's 'Standees at a Parade' test. Check if products/seats are undifferentiated commodities with high fixed costs, "
            "heavy ongoing CapEx, and zero pricing power. Unless the company has a proven niche monopoly (like NetJets), demand a massive 40–50% Margin of Safety or avoid as a Value Trap."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Авиокомпании/Текстил & 07-Стойностни капани]: Приложи теста на Бъфет „Изправени на парад“. Провери дали продуктът е недиференцирана стока с високи фиксирани разходи, "
            "постоянен тежък CapEx и нулева ценова сила. Ако няма уникален нишов ров (като NetJets), изисквай 40–50% MoS или избегни като Value Trap."
        ),
    },
    "financial_markets": {
        "title_en": "Financial Toll-Bridges & Asset Managers Playbook (Moody's / AmEx / Exchanges)",
        "title_bg": "Плейбук Финансови магистрали & Борси (Moody's / AmEx / ICE / FDS)",
        "badge_en": "🏛️ Financial Toll-Bridge • High ROIC Network/Data Moat without Bank Balance-Sheet Risk",
        "badge_bg": "🏛️ Финансова „тол-магистрала“ • Висок ROIC от мрежа/данни без банков кредитен риск",
        "lesson_en": (
            "Buffett Rule (Moody's / Visa / Exchanges): Unlike traditional banks, financial data providers, exchanges, and payment networks act as asset-light toll bridges "
            "with enormous pricing power and minimal balance-sheet credit risk. Watch regulatory/antitrust risk and debt used for roll-up acquisitions."
        ),
        "lesson_bg": (
            "Правило на Бъфет (Moody's / Visa / Борси): За разлика от класическите банки, финансовите борси, платежни мрежи и доставчици на данни са леки на активи „тол-магистрали“ "
            "с огромна ценова сила и без банков кредитен риск. Следи дали дългът от придобивания (Net Debt/EBITDA) е под контрол."
        ),
        "ai_prompt_en": (
            "[Playbook 08-Financial Toll-Bridges]: Distinguish asset-light financial networks/exchanges/data providers (Moody's/Visa/ICE/FDS — toll bridges with high ROIC and switching costs) "
            "from balance-sheet lenders (banks/BDCs). If it lends from its own balance sheet, check credit quality & leverage; if it's a fee/data toll bridge, check NRR, pricing power, and M&A goodwill."
        ),
        "ai_prompt_bg": (
            "[Плейбук 08-Финансови магистрали]: Разграничи леките на активи борси, платежни мрежи и финансови данни (Moody's/Visa/ICE/FDS — тол-магистрали с висок ROIC и високи разходи за смяна) "
            "от кредитиращите институции. Провери ценовата сила, задържането на клиентите и дали дългът/Goodwill от придобивания е разумен."
        ),
    },
    "healthcare": {
        "title_en": "Healthcare & Pharma Playbook (Patent Cliffs vs. Healthcare Infrastructure)",
        "title_bg": "Плейбук Здравеопазване & Фармация (Патентни скали срещу здравна инфраструктура)",
        "badge_en": "🧬 Healthcare Lens • Check Patent Portfolio Breadth vs. Single-Drug / Regulatory Risk",
        "badge_bg": "🧬 Здравен/Фарма модел • Широчина на патентите/мрежата срещу риск от изтичане на патент",
        "lesson_en": (
            "Buffett Rule (03-Business Moat & UNH/JNJ): A patent moat is only durable if backed by a broad portfolio, platform scale, or distribution/payer network — "
            "never bet on a single drug or pre-profit clinical binary outcome outside your Circle of Competence."
        ),
        "lesson_bg": (
            "Правило на Бъфет (03-Ров & UNH/JNJ): Патентният ров е траен само при широко портфолио, мащабна дистрибуция или застрахователна мрежа — "
            "никога не се залага на едно-единствено лекарство или губеща биотехнологична компания с бинарен клиничен риск извън Кръга на компетентност."
        ),
        "ai_prompt_en": (
            "[Playbook 03/08-Healthcare]: Check Circle of Competence first: profitable healthcare infrastructure/insurers/diversified medtech with recurring revenue are inside the circle; "
            "unprofitable single-asset clinical biotech is speculative and belongs in the Too Hard Pile. Check patent expiration cliffs, R&D ROI, and regulatory pricing pressure."
        ),
        "ai_prompt_bg": (
            "[Плейбук 03/08-Здравеопазване]: Първо провери Кръга на компетентност: печеливша здравна инфраструктура/мрежи с предвидим кеш са в кръга; "
            "губещи биотехнологични компании без печалба са спекулация („Твърде трудно“). Оцени патентния риск, R&D възвръщаемостта и регулаторния натиск."
        ),
    },
    "general_franchise": {
        "title_en": "Core Buffett Franchise vs. Commodity Playbook (1991 Letter Standard)",
        "title_bg": "Основен Плейбук на Бъфет: Франчайз срещу Суровинен бизнес (Писмо 1991 г.)",
        "badge_en": "🏰 Franchise vs. Commodity Test • Check 10-Yr ROIC (>15%), Pricing Power & Moat Trend",
        "badge_bg": "🏰 Тест Франчайз срещу Стока • Следи 10-г. ROIC (>15%), Ценова сила и Посока на рова",
        "lesson_en": (
            "Buffett 1991 Franchise Standard: A true economic franchise supplies a product/service that is (1) needed or desired, "
            "(2) has no close substitute in customers' minds, and (3) is not subject to price regulation — earning ROIC > 15% with high cash conversion."
        ),
        "lesson_bg": (
            "Стандарт за Франчайз на Бъфет (1991 г.): Истинският франчайз предлага продукт/услуга, който (1) е необходим или желан, "
            "(2) няма близък заместител в съзнанието на клиентите и (3) не подлежи на ценова регулация — генерирайки ROIC > 15% с висока кешова конверсия."
        ),
        "ai_prompt_en": (
            "[Playbook 03-Franchise vs Commodity]: Test if the company can raise prices +5-10% without losing volume (Franchise) or if competitors dictate price (Commodity). "
            "Check the 5 moat sources (intangibles, low cost, switching costs, network effects, efficient scale) and whether the moat is widening, stable, or narrowing."
        ),
        "ai_prompt_bg": (
            "[Плейбук 03-Франчайз срещу Суровинен бизнес]: Провери дали компанията може да вдигне цените с +5–10% без загуба на клиенти (Франчайз) или пазарът диктува цената (Стока). "
            "Оцени 5-те източника на ров и дали ровът се разширява, е стабилен или се стеснява."
        ),
    },
}


# ---------------------------------------------------------------------------
# 3. Thematic Playbooks (01 through 07) for Smart Question Routing
# ---------------------------------------------------------------------------

THEMATIC_PLAYBOOKS: dict[str, dict[str, str]] = {
    "sell_and_risk_07": {
        "en": (
            "[Ref 07 - When to Sell, Value Traps, Herb Stein's Law & Real Bond Math]: "
            "Check Buffett's 4 Sell Criteria item-by-item: "
            "(1) Price severely exceeds Intrinsic Value (PetroChina exit at $275-300B vs $35-40B entry; beware 1964-1981 valuation compression where even great businesses deliver 0% for a decade if bought at extreme multiples); "
            "(2) Moat is fundamentally & permanently destroyed (Paramount 2024 exit — admit mistake 100% and sell, NOT temporary cyclical trouble); "
            "(3) Management integrity / accounting fraud issue (AUTOMATIC IMMEDIATE SELL); "
            "(4) A clearly superior opportunity with a much larger Margin of Safety exists. "
            "NEVER sell just because price dropped or Mr. Market panicked (160-Acre Farm Analogy). "
            "Counter Sunk-Cost Fallacy ('If I owned 0 shares today, would I buy at this price?'). "
            "Remember Real Bond Math: 3-4% long bonds after tax and 2-3% inflation yield ~0% real purchasing power; cash is strictly an emergency buffer and crisis option."
        ),
        "bg": (
            "[Реф. 07 - Кога се продава, Стойностни капани, Закон на Хърб Стайн & Реална доходност]: "
            "Провери точка по точка 4-те критерия на Бъфет за продажба: "
            "(1) Цената екстремно надхвърля Вътрешната стойност (като излизането от PetroChina на $275–300B след покупка на $35–40B; дори велик бизнес дава 0% доходност с години, ако е купен на екстремен мултипликатор като през 1964–1981 г.); "
            "(2) Икономическият ров е фундаментално и трайно разрушен (като Paramount през 2024 г. — признаваш грешката и излизаш, за разлика от временна цикличност); "
            "(3) Проблем с почтеността на мениджмънта или счетоводна измама (НЕЗАБАВНА ПРОДАЖБА); "
            "(4) Налична е драстично по-добра възможност с много по-голям Марж на безопасност. "
            "НИКОГА не се продава заради спад в цената (Аналогията със 160-акровата ферма и Г-н Пазар). Приложи Sunk Cost теста: „Ако днес имах 0 акции, щях ли да купя на тази цена?“ "
            "Помни реалната математика на облигациите: 3–4% лихва след данъци и инфлация дава ~0% реална покупателна способност; кешът е само за буфер и кризи."
        ),
    },
    "moat_and_goodwill_03": {
        "en": (
            "[Ref 03 - Moat, Rate-of-Change Filter, Consumer Utility Gap & Economic Goodwill]: "
            "Evaluate the 5 moats (Intangibles/Brand, Low-Cost Producer, Switching Costs [NRR>120%], Network Effects, Efficient Scale) and trend (Widening/Stable/Narrowing). "
            "Apply: (1) Rate-of-Change Filter (avoid industries with rapid unpredictable technological shifts; favor habits stable over 10-20 yrs); "
            "(2) Consumer Utility vs. Price Gap (Buffett's Apple test: families would give up a $35,000 second car before a $1,000 iPhone); "
            "(3) Hypothetical Competitor Test ('Would I wrestle a grizzly bear rather than compete with Mrs. B?'); "
            "(4) Economic Goodwill (high ROIC on tangible assets = best inflation shield, like See's Candies) vs. Fake Accounting Goodwill ('capitalized adrenaline' from overpriced M&A)."
        ),
        "bg": (
            "[Реф. 03 - Икономически ров, Филтър за скорост на промяна, Потребителска полезност & Goodwill]: "
            "Оцени 5-те вида ров и посоката му (Разширяващ се/Стабилен/Стесняващ се). "
            "Приложи: (1) Филтър за скорост на промяна в индустрията (избягвай сектори с бърза и непредвидима промяна; търси навици, стабилни за 10–20 г.); "
            "(2) Разлика между полезност за клиента и цена (Тестът на Бъфет за Apple: семейство би се отказало от втора кола за $35,000 преди да се откаже от iPhone); "
            "(3) Икономически Goodwill (висок ROIC върху малко материални активи — най-добрата защита от инфлация като See's Candies) срещу Фалшив счетоводен Goodwill („капитализиран адреналин“ от скъпи придобивания)."
        ),
    },
    "management_and_capital_04_06": {
        "en": (
            "[Ref 04 & 06 - Management, 'Cannibal' Buybacks, Aesop's Formula & Whole-Business Math]: "
            "3D Management Check: (1) Integrity & Tail-Risk Aversion ('Anything times zero is zero'; 100% personal accountability on mistakes like Paramount; zero insider-conflict appearance like skipping Microsoft); "
            "(2) Capital Allocation ($1 Retained Earnings Test & 5 paths); (3) Owner Mentality & Incentive-Caused Bias (FedEx night-shift rule). "
            "Munger's 'Cannibal' Compounders: high-ROIC businesses retiring 2-5%+ of shares/yr below Intrinsic Value (Apple, GEICO, Japanese 5 Trading Houses at 14% earnings yield funded with 0.5% Yen debt) multiply per-share value tax-free. "
            "Aesop's 600 BC Formula & Whole-Business Sanity Check: Look at 100% of Market Cap and ask how many years of True Owner Earnings are required to pay it back."
        ),
        "bg": (
            "[Реф. 04 & 06 - Мениджмънт, „Канибалски“ обратни изкупувания, Формулата на Езоп & Цял бизнес]: "
            "3-измерна оценка: (1) Почтеност и избягване на нулев риск („Всяко число по нула е нула“; 100% лична отговорност при грешки като Paramount); "
            "(2) Капиталова алокация (Тестът за $1 задържана печалба); (3) Манталитет на собственик и Изкривяване от стимули (Incentive-Caused Bias). "
            "„Канибалите“ на Мънгър: компании с висок ROIC, които изкупуват и унищожават 2–5%+ от акциите си годишно на разумна цена (Apple, GEICO, 5-те японски търговски къщи на ~14% доходност срещу 0.5% дълг в йени), увеличават дела ти без данък. "
            "Формула на Езоп (600 г. пр.Хр.) & Тест за покупка на целия бизнес: Виж цялата пазарна капитализация и сметни за колко години Истински Owner Earnings се изплаща."
        ),
    },
    "financials_and_traps_05": {
        "en": (
            "[Ref 05 - True Owner Earnings, Negative Working Capital & Intangible Growth OpEx]: "
            "See through GAAP to economic reality: True Owner Earnings = OCF - Maintenance CapEx - SBC (never trust raw EBITDA). "
            "Check: (1) Negative Working Capital / Customer-Funded Float (Payables > Receivables + Inventory, like Apple/Costco/See's/Blue Chip Stamps — growth generates cash instead of consuming working capital); "
            "(2) Intangible Growth CapEx Expensed as OpEx (high R&D % of revenue depresses GAAP Net Income and inflates optical P/E — look at OCF & True Owner Earnings); "
            "(3) ROIC > 15%, Cash Conversion > 90%, Net Debt/EBITDA < 2x, EBIT/Interest > 5x, -30% Revenue Stress Test, and A/R or Inventory outpacing Revenue."
        ),
        "bg": (
            "[Реф. 05 - Истински Owner Earnings, Отрицателен оборотен капитал & R&D през отчета]: "
            "Гледай икономическата реалност зад GAAP: Истински Owner Earnings = OCF − Поддържащ CapEx − SBC. "
            "Провери: (1) Отрицателен оборотен капитал / Финансиране от клиенти и доставчици (Задължения към доставчици > Вземания + Запаси, като при Apple/Costco/See's — растежът генерира кеш вместо да гълта оборотен капитал); "
            "(2) Растящ CapEx, отчетен като разход (висок R&D % от приходите намалява GAAP печалбата и прави P/E оптически висок — гледай OCF и Истински Owner Earnings); "
            "(3) ROIC > 15%, Кешова конверсия > 90%, Net Debt/EBITDA < 2x, EBIT/Лихви > 5x, стрес тест при −30% приходи и A/R или Запаси над ръста на приходите."
        ),
    },
    "philosophy_and_mental_01_02": {
        "en": (
            "[Ref 01 & 02 - 20-Slot Punch Card, 12 Decisions in 58 Yrs, Lollapalooza & 3-Stock Rule]: "
            "Act as if you have a lifetime 20-Slot Punch Card; across 58 years and ~400 decisions at Berkshire, only ~12 decisions (1 every 5 yrs) drove the wealth — the edge is patience ('watching paint dry') and not selling compounders. "
            "Munger's 3-Stock vs. Know-Nothing Rule: An uninformed investor should hold a low-cost broad index ETF (VWCE/S&P 500); a focused business analyst needs only 3-6 wonderful businesses (putting money in idea #30 instead of #1 is madness). "
            "Check for Lollapalooza Effects (3-4 forces compounding in one direction) and Rip Van Winkle tax deferral (0% CGT on EU UCITS ETFs in Bulgaria)."
        ),
        "bg": (
            "[Реф. 01 & 02 - Перфокарта с 20 дупки, 12 решения за 58 г., Ефект Лолапалуза & Правилото за 3 акции]: "
            "Действай сякаш имаш Перфокарта с 20 инвестиции за цял живот; за 58 години и ~400 решения в Berkshire само ~12 решения (по 1 на 5 г.) са създали богатството — тайната е търпението и задържането на победителите. "
            "Двупистовото правило на Мънгър и Бъфет: Непрофесионалистът печели най-много с широк индексен ETF с ниска такса (VWCE / S&P 500); фокусираният анализатор се нуждае само от 3–6 велики бизнеса (пари в идея №30 вместо в №1 е безумие). "
            "Търси Ефекта Лолапалуза (3–4 сили в една посока) и данъчния ефект „Рип Ван Уинкъл“ (0% данък за UCITS ETF в България)."
        ),
    },
}


# ---------------------------------------------------------------------------
# 4. Deterministic Industry Scorecard & Economic Model Classification
# ---------------------------------------------------------------------------

def evaluate_industry_playbook_checks(
    metrics: dict,
    deep_metrics: dict | None = None,
    lang: str = "en",
) -> dict:
    """Return industry-specific Buffett playbook metadata and extra quantitative checks."""
    is_bg = lang == "bg"
    deep = deep_metrics or {}
    pb_key = classify_industry_playbook(
        metrics.get("sector"),
        metrics.get("industry"),
        metrics.get("ticker"),
    )
    pb = INDUSTRY_PLAYBOOKS.get(pb_key, INDUSTRY_PLAYBOOKS["general_franchise"])

    avg_roic = deep.get("avg_roic_pct") or metrics.get("roic_est_pct")
    avg_roe = deep.get("avg_roe_pct") or metrics.get("roe_pct")
    gross_m = metrics.get("gross_margin_pct")
    op_m = metrics.get("operating_margin_pct")
    capex_to_da = deep.get("capex_to_da_ratio")
    capex_pct_ocf = deep.get("capex_pct_of_ocf")
    capex_pct_rev = deep.get("capex_pct_of_rev")
    sbc_pct = deep.get("sbc_pct_of_ocf")
    goodwill_pct_eq = deep.get("goodwill_pct_of_equity")

    is_heavy_capex = (
        (capex_to_da is not None and capex_to_da >= 1.35)
        and (capex_pct_rev is None or capex_pct_rev >= 8.0)
        and ((capex_pct_ocf is not None and capex_pct_ocf >= 30.0) or (capex_pct_rev is not None and capex_pct_rev >= 12.0))
    )
    is_asset_light = (
        (capex_pct_rev is not None and capex_pct_rev <= 8.0)
        or (capex_pct_ocf is not None and capex_pct_ocf <= 22.0)
        or (capex_to_da is not None and capex_to_da <= 1.15)
    )

    # Classify Buffett Economic Model (03-business-moat.md & 08-industry-playbooks.md)
    if pb_key in ("banking", "insurance"):
        if (avg_roe or 0) >= 14.0:
            model_type = (
                "Финансова крепост (Висока възвръщаемост при дисциплиниран риск)"
                if is_bg
                else "Disciplined Financial Compounder (ROE >= 14%)"
            )
        else:
            model_type = (
                "Финансова институция с ливъридж (изисква строг кредитен/резервен одит)"
                if is_bg
                else "Leveraged Financial Institution (requires strict credit/reserve audit)"
            )
    elif pb_key == "cautionary_cyclicals" or ((avg_roic is not None and avg_roic < 8.0) and (op_m is not None and op_m < 10.0)):
        model_type = (
            "Суровинен / Цикличен бизнес („Изправени на парад“ — слаба ценова сила)"
            if is_bg
            else "Commodity / Cyclical Business ('Standees at a Parade' — weak pricing power)"
        )
    elif (avg_roic or 0) >= 16.0 and (gross_m or 0) >= 45.0 and not is_heavy_capex:
        model_type = (
            "Икономически Франчайз / „Тол-магистрала“ (Лек на активи + Висок Икономически Goodwill)"
            if is_bg
            else "Asset-Light Economic Franchise / 'Toll Bridge' (High Economic Goodwill)"
        )
    elif (avg_roic or 0) >= 12.0 and is_heavy_capex:
        model_type = (
            "Капиталоемък ров (Силна мрежа/мащаб, но изисква тежък реинвестиционен CapEx)"
            if is_bg
            else "Capital-Intensive Moat (Irreplicable scale, but heavy ongoing CapEx consumer)"
        )
    else:
        model_type = (
            "Хибриден бизнес модел (Умерен ров — изисква 30–40% Марж на безопасност)"
            if is_bg
            else "Hybrid Business Model (Moderate moat — requires 30–40% Margin of Safety)"
        )

    extra_notes: list[str] = []
    if pb_key not in ("banking", "insurance"):
        if is_asset_light and not is_heavy_capex and (avg_roic or 0) >= 16.0:
            cap_tag = f"CapEx/Приходи: `{capex_pct_rev:.1f}%`" if (capex_pct_rev is not None and is_bg) else (
                f"CapEx/Rev: `{capex_pct_rev:.1f}%`" if capex_pct_rev is not None else f"CapEx/D&A: `{capex_to_da:.2f}x`"
            )
            extra_notes.append(
                f"💎 *Икономически Goodwill (See's Candies ефект):* Лек на активи модел ({cap_tag}) с висок ROIC — естествен щит срещу инфлация"
                if is_bg
                else f"💎 *Economic Goodwill (See's Candies effect):* Asset-light ({cap_tag}) + high ROIC — natural inflation shield"
            )
        elif is_heavy_capex and capex_to_da is not None:
            ocf_tag = f", `{capex_pct_ocf:.0f}%` от OCF" if (capex_pct_ocf is not None and is_bg) else (
                f", `{capex_pct_ocf:.0f}%` of OCF" if capex_pct_ocf is not None else ""
            )
            extra_notes.append(
                f"🏗️ *Консуматор на капитал (BNSF правило):* CapEx е `{capex_to_da:.2f}x` над D&A ({ocf_tag.lstrip(', ')}) — изисква тежки капиталови реинвестиции"
                if is_bg
                else f"🏗️ *Capital Consumer (BNSF rule):* CapEx is `{capex_to_da:.2f}x` of D&A ({ocf_tag.lstrip(', ')}) — requires heavy capital reinvestment"
            )

    if goodwill_pct_eq is not None and goodwill_pct_eq >= 40.0 and (avg_roic is not None and avg_roic < 12.0):
        extra_notes.append(
            f"⚠️ *Счетоводен Goodwill риск:* Goodwill е `{goodwill_pct_eq:.0f}%` от собствения капитал при нисък ROIC (`{avg_roic:.1f}%`) — риск от надплатени придобивания"
            if is_bg
            else f"⚠️ *Accounting Goodwill Risk:* Goodwill is `{goodwill_pct_eq:.0f}%` of equity with subpar ROIC (`{avg_roic:.1f}%`) — historical M&A overpayment risk"
        )

    roiic_pct = deep.get("roiic_pct")
    passes_dollar_test = deep.get("passes_one_dollar_test")
    if passes_dollar_test is True and roiic_pct is not None:
        extra_notes.append(
            f"✅ *Тест за $1 задържана печалба:* Издържан (възвръщаемост върху реинвестирания капитал `{roiic_pct:+.1f}%` >= 12%)"
            if is_bg
            else f"✅ *Buffett $1 Retained Earnings Test:* Passed (incremental return on retained capital `{roiic_pct:+.1f}%` >= 12%)"
        )
    elif passes_dollar_test is False and roiic_pct is not None:
        extra_notes.append(
            f"⚠️ *Тест за $1 задържана печалба:* Под въпрос (incremental return `{roiic_pct:+.1f}%` < 8% върху задържаните печалби)"
            if is_bg
            else f"⚠️ *Buffett $1 Retained Earnings Test:* Weak incremental return (`{roiic_pct:+.1f}%` < 8% on retained earnings)"
        )

    if pb_key == "technology" and sbc_pct is not None and sbc_pct < 12.0:
        extra_notes.append(
            f"✅ *Дисциплиниран софтуерен/тех модел:* Нисък SBC (`{sbc_pct:.1f}%` от OCF)"
            if is_bg
            else f"✅ *Disciplined Tech Compensation:* Low SBC (`{sbc_pct:.1f}%` of OCF)"
        )

    if deep.get("is_cannibal"):
        sc_val = deep.get("share_change_pct") or 0.0
        extra_notes.append(
            f"🦈 *„Канибал“ по Мънгър (Cannibal Stock):* Изкупени и унищожени `{sc_val:+.1f}%` от акциите при висок ROIC — делът ти расте автоматично без данък"
            if is_bg
            else f"🦈 *Munger 'Cannibal' Compounder:* Retired `{sc_val:+.1f}%` of shares at high ROIC — tax-free per-share ownership growth"
        )

    if deep.get("negative_working_capital"):
        extra_notes.append(
            "🔄 *Отрицателен оборотен капитал (Blue Chip / Apple ефект):* Клиентите и доставчиците финансират растежа без нужда от нов капитал"
            if is_bg
            else "🔄 *Negative Working Capital Float (Blue Chip / Apple effect):* Customers & suppliers fund growth with zero working-capital drag"
        )

    rd_pct = deep.get("rd_pct_of_rev")
    if rd_pct is not None and rd_pct >= 10.0:
        extra_notes.append(
            f"🧪 *Скрит растеж през отчета (R&D: `{rd_pct:.1f}%` от приходите):* Инвестициите за бъдещ растеж минават като текущ разход и правят GAAP P/E оптически по-висок"
            if is_bg
            else f"🧪 *Intangible Growth Expensed as OpEx (R&D: `{rd_pct:.1f}%` of revenue):* Growth investments run through the income statement, making GAAP P/E look optically higher"
        )

    payback_yrs = deep.get("whole_business_payback_yrs")
    if payback_yrs is not None:
        pb_icon = "✅" if payback_yrs <= 22.0 else ("🟡" if payback_yrs <= 32.0 else "⚠️")
        extra_notes.append(
            f"{pb_icon} *Тест за покупка на 100% от бизнеса (Езоп / Бъфет):* Пазарната кап. се изплаща за `~{payback_yrs:.1f} г.` чрез Истински Owner Earnings"
            if is_bg
            else f"{pb_icon} *Whole-Business Payback Test (Aesop / Buffett):* 100% of Market Cap pays back in `~{payback_yrs:.1f} yrs` of True Owner Earnings"
        )

    return {
        "playbook_key": pb_key,
        "title": pb["title_bg"] if is_bg else pb["title_en"],
        "badge": pb["badge_bg"] if is_bg else pb["badge_en"],
        "lesson": pb["lesson_bg"] if is_bg else pb["lesson_en"],
        "ai_prompt": pb["ai_prompt_bg"] if is_bg else pb["ai_prompt_en"],
        "model_type": model_type,
        "extra_notes": extra_notes,
    }


# ---------------------------------------------------------------------------
# 5. Smart Playbook Router for Single-Stock Analysis & Free-Form Questions
# ---------------------------------------------------------------------------

def build_stock_playbook_prompt_context(
    metrics: dict,
    deep_metrics: dict | None = None,
    lang: str = "en",
) -> str:
    """Return a compact (~250-400 token) industry + accounting playbook injection for /buffett TICKER."""
    is_bg = lang == "bg"
    lang_key = "bg" if is_bg else "en"
    pb_info = evaluate_industry_playbook_checks(metrics, deep_metrics, lang=lang)
    blocks = [
        f"Buffett Economic Model Classification: {pb_info['model_type']}",
        pb_info["ai_prompt"],
        THEMATIC_PLAYBOOKS["management_and_capital_04_06"][lang_key],
    ]
    deep = deep_metrics or {}
    if deep.get("red_flags") or (metrics.get("net_debt_to_ebitda") or 0) >= 2.0:
        blocks.append(THEMATIC_PLAYBOOKS["financials_and_traps_05"][lang_key])
    return "\n".join(blocks)


def select_playbooks_for_question(
    question: str,
    mentioned_playbooks: list[str] | None = None,
    lang: str = "en",
) -> str:
    """Inspect a user's question + mentioned ticker industries and select the 1-3 relevant Buffett playbooks."""
    lang_key = "bg" if lang == "bg" else "en"
    q = (question or "").lower()
    selected: list[str] = []

    # 1) Route by question intent keywords (EN + BG)
    if any(k in q for k in (
        "sell", "trim", "hold", "drop", "crash", "overvalued", "trap", "exit",
        "прод", "задържа", "падна", "срив", "надценен", "капан", "изляза", "загуба",
    )):
        selected.append(THEMATIC_PLAYBOOKS["sell_and_risk_07"][lang_key])

    if any(k in q for k in (
        "moat", "brand", "goodwill", "franchise", "commodity", "pricing power", "competit",
        "ров", "бранд", "марка", "франчайз", "ценова сила", "конкурент", "предимство",
    )):
        selected.append(THEMATIC_PLAYBOOKS["moat_and_goodwill_03"][lang_key])

    if any(k in q for k in (
        "manag", "ceo", "buyback", "repurchase", "dividend", "acqui", "sbc", "allocat", "imperative", "retained",
        "менидж", "обратно изкупуване", "дивидент", "придобиван", "алокация", "императив", "задържан",
    )):
        selected.append(THEMATIC_PLAYBOOKS["management_and_capital_04_06"][lang_key])

    if any(k in q for k in (
        "owner earnings", "roic", "roe", "ebitda", "capex", "accounting", "cash flow", "debt", "leverage", "look-through", "receivable", "inventory",
        "отчет", "печалб", "счетовод", "кеш", "дълг", "ливъридж", "вземания", "запаси", "амортизац",
    )):
        selected.append(THEMATIC_PLAYBOOKS["financials_and_traps_05"][lang_key])

    if any(k in q for k in (
        "mr. market", "market", "diversif", "concentrat", "circle", "inversion", "inflation", "tax", "compound", "patience",
        "пазар", "диверсиф", "концентрац", "кръг", "инверсия", "инфлация", "данък", "сложен процент", "търпение", "буфер", "кредит",
    )):
        selected.append(THEMATIC_PLAYBOOKS["philosophy_and_mental_01_02"][lang_key])

    # 2) Route by explicit industry keywords in question + mentioned tickers' classified industries
    ind_keys: list[str] = list(mentioned_playbooks or [])
    if any(k in q for k in ("bank", "jpm", "bac", "wfc", "банк")):
        ind_keys.append("banking")
    if any(k in q for k in ("insur", "float", "geico", "brk", "застрахов", "флоут")):
        ind_keys.append("insurance")
    if any(k in q for k in ("airline", "textile", "авио", "текстил")):
        ind_keys.append("cautionary_cyclicals")
    if any(k in q for k in ("rail", "bnsf", "unp", "industrial", "железниц", "индустр")):
        ind_keys.append("railroads_industrials")
    if any(k in q for k in ("energy", "oil", "utility", "utilities", "oxy", "енерг", "петрол", "ютилити", "ток")):
        ind_keys.append("energy_utilities")
    if any(k in q for k in ("media", "newspaper", "streaming", "nflx", "dis", "медии", "вестник", "стрийминг")):
        ind_keys.append("media_entertainment")
    if any(k in q for k in ("consumer", "retail", "coca-cola", "see's", "costco", "ko", "ритейл", "кока-кола", "потребител")):
        ind_keys.append("consumer_brands")
    if any(k in q for k in ("tech", "software", "saas", "chip", "semiconductor", "ai", "apple", "meta", "intu", "софтуер", "технолог", "чип")):
        ind_keys.append("technology")

    seen_ind: set[str] = set()
    for ik in ind_keys:
        if ik in INDUSTRY_PLAYBOOKS and ik not in seen_ind and len(seen_ind) < 2:
            seen_ind.add(ik)
            pb = INDUSTRY_PLAYBOOKS[ik]
            selected.append(pb["ai_prompt_bg"] if lang == "bg" else pb["ai_prompt_en"])

    # Default fallback if question is general portfolio/top-3 comparison: include Moat/Capital + Philosophy
    if not selected:
        selected.append(THEMATIC_PLAYBOOKS["moat_and_goodwill_03"][lang_key])
        selected.append(THEMATIC_PLAYBOOKS["management_and_capital_04_06"][lang_key])

    # Cap at 3 concise playbook blocks (~400-650 tokens total)
    return "\n".join(selected[:3])
