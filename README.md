<div align="center">

<img src="bot/bot_avatar.png" alt="Buffett & Munger Investor Bot" width="140" />

# 🏰 Buffett & Munger Investor Bot

**Open-source, multi-user Telegram investment bot built on Warren Buffett & Charlie Munger's value investing framework.**  
Live Owner Earnings valuation • Trading 212 portfolio sync • Unified Top-3 ranker • Automated buy-zone alerts • Built-in Gemini AI agent.

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)](https://www.sqlalchemy.org/)
[![Deploy to Render](https://img.shields.io/badge/Deploy%20Free-Render%20%2B%20Neon-46E3B7?logo=render&logoColor=black)](https://render.com/deploy?repo=https://github.com/stoyantsiparov/buffett-munger-investor-bot)

</div>

---

## 🎯 Why This Bot Exists

Most retail investing tools drown you in noisy charts, hype, and short-term price swings (*Mr. Market*).  
**Buffett & Munger Investor Bot** brings disciplined, long-term **value investing** straight to your phone in Telegram — for **you and your friends** — with zero spreadsheets and zero hardcoded stock picks:

- **Know what to buy on payday:** Compares every undervalued stock you **already own** in Trading 212 head-to-head against a **shared Buffett Watchlist** and ranks today's **Unified Top 3** opportunities.
- **Protect your downside first (Charlie Munger's Inversion):** Tracks high-interest debt (>10–12% APR), enforces a cash liquidity buffer so you are never forced to sell great businesses, and automatically blocks over-concentration (>20–25% in a single risky sector like semiconductors).
- **100% Private per User + Shared Group Watchlist:** Every user connects their own **Read-Only** Trading 212 API key (encrypted with `AES-128 Fernet`, chat message auto-deleted), sets their own budget, emergency buffer, payday, and optional loans — while everyone collaborates on one synchronized group Watchlist.

---

## ✨ Core Features

| Feature | What It Does |
| :--- | :--- |
| **🏆 Unified Top-3 Ranker (`/check_alerts`)** | Evaluates live prices & Owner Earnings Intrinsic Value for **both** the shared Watchlist **and** every single stock in your Trading 212 portfolio, then ranks today's Top 3 head-to-head (`💼 Reinforce Holding` vs. `🎯 Add New from Watchlist`) with 1-tap buy tickets. |
| **🧠 Authentic Owner Earnings Engine (`/buffett`)** | Automatically separates **Maintenance CapEx** (`≈ 1.15× D&A`) from **Growth CapEx** (preventing distorted valuations on AI/cloud hyperscalers), applies a dedicated **Banking & Insurance playbook** (ROE/ROA/EPS instead of deposit-distorted OCF), and checks ROIC, cash conversion, net debt, and Margin of Safety (20–50%). |
| **📊 Live Trading 212 Sync (`/portfolio`)** | Read-Only Trading 212 API integration with Pie breakdown, unrealized/realized P&L, underweight gaps, and automatic sector-concentration alerts. |
| **📅 Automated Monthly Check (`/monthly_check`)** | Dynamic monthly investment & cash-flow plan delivered automatically on each user's custom payday (`/set_payday` — defaults to the 1st of the month). |
| **🤝 Synchronized Group Watchlist (`/watchlist`)** | Any user can run `/add_watch TICKER` — the bot computes its Buffett Intrinsic Value & Target Buy price automatically, adds it to the shared list, and broadcasts it to the group. Weekly Monday scans alert everyone when any stock enters its Buy Zone. |
| **🤖 Conversational Gemini AI Agent (`/ask`)** | Ask natural-language questions (*"Should I reinforce META or open a new position in INTU today?"*) with full live portfolio context + Google Search news. **Zero `.env` access needed for friends:** any user can plug in their own free Google AI Studio key via `/set_gemini_key` (encrypted & auto-deleted from chat). |
| **☁️ Free 24/7 Cloud Ready** | Works with **PostgreSQL (Neon / Supabase)**, **MSSQL**, and **SQLite** via SQLAlchemy 2.0, ships with a `Dockerfile`, `render.yaml`, and a built-in zero-token `/health` endpoint (`GET`/`HEAD`) for Render Free + UptimeRobot. |

---

## 📱 Example Output (`🔔 Топ 3 & Аларми`)

```text
🔔 BUY ALERT IN SHARED BUFFETT WATCHLIST!
🟢 #1 INTU (Intuit)
   • Current Price: $279.18 <= Target Buy: $285.00
   • Intrinsic Value: $350 – $420 (Discount: +33.5%)
   • Moat: Switching Costs + Network Effect (QuickBooks, TurboTax) • Stable

💼 UNDERVALUED STOCKS IN YOUR PORTFOLIO (TRADING 212):
🟢 META (Meta Platforms) (Weight: 7.4% / target 8%)
   • Current Price: $595.10 <= Target Buy: $652.73
   • Intrinsic Value: $746 – $895 (Discount: +33.5%)
   • Moat: Global Social Network Effect (3.2B+ users) • Stable
   • ⚠️ Undervalued but blocked by sector cap (>20%): NVDA (+34%)

🏆 UNIFIED TOP 3 TODAY (WATCHLIST vs. YOUR PORTFOLIO):
  1️⃣ META — 💼 Your Portfolio (reinforce 7.4%/8%) | 🟢 IN BUY ZONE (+28% MoS)
  2️⃣ INTU — 🎯 Watchlist (new)                    | 🟢 IN BUY ZONE (+28% MoS)
  3️⃣ NFLX — 💼 Your Portfolio (reinforce 7.5%/8%) | 🟡 Undervalued (+19% to IV)
```

---

## 📖 How Any User Uses the Bot (Quick Start)

Whether you host it for yourself, your friend group, or a public investing community, onboarding takes **under 60 seconds**:

1. **Start the bot:** Send `/start` — your completely private profile is created automatically (blank budget, no loans, default automated monthly check on the 1st of the month).
2. **Connect Trading 212 *(Read-Only & Encrypted)*:**
   - In Trading 212 go to **Settings → API (Beta) → Generate API Key** and enable **Read-Only** permissions only.
   - Send `/connect_t212 YOUR_API_KEY YOUR_API_SECRET` — the bot **immediately deletes your message from chat** and encrypts your keys with `AES-128 Fernet`.
3. **Set your personal budget, cash buffer & payday:**
   - `/set_budget INCOME INVEST_BUDGET BUFFER` *(e.g. `/set_budget 1500 250 500`)*
   - `/set_buffer AMOUNT` *(quick cash/Revolut buffer update)*
   - `/set_payday DAY` *(pick day `1–31` for your automated monthly report)*
   - *(Optional)* If you have a high-interest loan (>10% APR), track it for Munger's Inversion: `/set_loan BANK PRINCIPAL APR REGULAR_PMT EXTRA_PMT`
4. **Analyze stocks, check Top 3 & collaborate:**
   - Tap **`🔔 Топ 3 & Аларми`** (`/check_alerts`) for today's live Unified Top 3 & buy alerts.
   - Type **any ticker** directly in chat (e.g. `INTU`, `KO`, `MSFT`, `RR.L`) for an instant 10-point Buffett scorecard.
   - Share a discovery with everyone: `/add_watch AAPL`
5. *(Optional)* **Activate Free Gemini AI Agent (`/set_gemini_key`):**
   - Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → **Create API key** → choose **`Default Gemini Project`**.
   - Send `/set_gemini_key YOUR_KEY` in Telegram *(message auto-deleted, key encrypted)*. Ask anything in natural language right in chat!

---

## 💬 Full Command Reference

| Command | Description |
| :--- | :--- |
| `/start` | Welcome screen, profile status, and 60-second onboarding buttons |
| `/check_alerts` | Live Watchlist + Portfolio undervalued scan & **Unified Top 3** picker |
| `/portfolio` | Live Trading 212 portfolio snapshot, pies, P&L & sector exposure |
| `/monthly_check` | Dynamic monthly cash-flow, debt, buffer & investment plan |
| `/watchlist` | View the shared Buffett & Munger Watchlist with live prices & targets |
| `/add_watch TICKER [TARGET]` | Auto-value a stock by Buffett rules & share it to the Watchlist |
| `/del_watch TICKER` | Remove a ticker from the shared Watchlist |
| `/buffett TICKER` | Full 10-point Buffett & Munger analysis (or just type `TICKER` in chat) |
| `/ask QUESTION` | Ask the Buffett & Munger AI Agent (or just type any question in chat) |
| `/set_budget INC INV [BUF] [DAY]` | Configure private monthly income, investment budget, buffer & report day |
| `/set_buffer AMOUNT [TARGET]` | Quickly update your current emergency cash buffer |
| `/set_loan BANK BAL APR PMT [EXTRA]` | Track a high-interest loan (set principal to `0` to clear) |
| `/set_payday DAY` | Customize the day of month (`1–31`) for automated monthly reports |
| `/connect_t212 KEY SECRET` | Securely link a Read-Only Trading 212 API key (auto-deletes chat message) |
| `/set_gemini_key KEY` | Add your own free encrypted Gemini API key without touching `.env` |
| `/help` | Full interactive user guide inside Telegram |

---

## 🚀 Deploy Your Own Instance 24/7 for Free (€0, No Credit Card)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/stoyantsiparov/buffett-munger-investor-bot)

1. **Create a Telegram Bot:** Message [`@BotFather`](https://t.me/BotFather) → `/newbot` → copy `TELEGRAM_BOT_TOKEN`.
2. **Create a Free PostgreSQL Database:** Sign up at [neon.tech](https://neon.tech) (no card required) → create a project → copy `DATABASE_URL`.
3. **Deploy on Render (Free Web Service):** Click **Deploy to Render** above (uses [`render.yaml`](render.yaml) & [`Dockerfile`](Dockerfile)), paste `TELEGRAM_BOT_TOKEN`, `BOT_ENCRYPTION_KEY`, and `DATABASE_URL`, and deploy.
4. **Keep Awake 24/7 (0 AI Tokens):** Add a free 5-minute HTTP monitor on [uptimerobot.com](https://uptimerobot.com) pointing to `https://<your-service>.onrender.com/health`.

> Full step-by-step guide (in Bulgarian): [`DEPLOY-RENDER-NEON.md`](DEPLOY-RENDER-NEON.md)

### 🛠️ Local Setup

```bash
git clone https://github.com/stoyantsiparov/buffett-munger-investor-bot.git
cd buffett-munger-investor-bot
cp .env.example .env   # Fill in TELEGRAM_BOT_TOKEN and BOT_ENCRYPTION_KEY
pip install -r requirements.txt
python -m bot.main
```

---

## 🔐 Security & Privacy Design

- **Read-Only Broker Access:** Never asks for trading/execution permissions — only Read-Only Trading 212 API keys.
- **Encryption at Rest:** Trading 212 and Gemini API keys are encrypted with `AES-128 Fernet` (`BOT_ENCRYPTION_KEY`) before hitting the database.
- **Zero Chat Leakage:** Messages containing `/connect_t212` or `/set_gemini_key` are deleted immediately from Telegram chat history.
- **Strict Tenant Isolation:** Each Telegram `user_id` sees only their own portfolio, budget, buffer, loans, and API keys.

---

## 🧱 Tech Stack

- **Bot Framework:** `python-telegram-bot[job-queue]` (v22+)
- **Database ORM:** `SQLAlchemy 2.0` (`PostgreSQL` / `MSSQL` / `SQLite`)
- **Market Data:** `yfinance` (parallelized with `ThreadPoolExecutor`) + `Trading 212 Public API`
- **AI Agent:** `google-genai` (`gemini-4-hydrogen` + Google Search grounding, token-optimized)
- **Security:** `cryptography` (`Fernet` AES-128-CBC + HMAC)
- **Containerization:** `Docker` (`python:3.11-slim`)

---

## 🤝 Contributing & Support

Contributions, translations (English/other languages alongside Bulgarian), new broker integrations, and valuation improvements are very welcome!

- ⭐ **Star this repository** if you find it useful — it helps other value investors discover the project!
- 🐛 Found a bug or have an idea? Open an [Issue](https://github.com/stoyantsiparov/buffett-munger-investor-bot/issues) or submit a Pull Request.

> *Disclaimer: This project is for educational and personal portfolio-tracking purposes only and does not constitute regulated financial advice.*

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
