# 🏰 Buffett & Munger Investor Telegram Bot

Multi-user value investing Telegram bot powered by **Warren Buffett & Charlie Munger's investment system** — featuring live Owner Earnings intrinsic valuation, a **Unified Top-3 ranker** (comparing your own Trading 212 holdings head-to-head against a shared group Watchlist), automated price alerts, monthly check reports, and an optional Gemini AI agent.

## ✨ Key Features

- **🏆 Unified Top-3 Picker (`Watchlist vs. Your Portfolio`):** Evaluates both the shared Buffett Watchlist and every individual stock in each user's Trading 212 portfolio on live Owner Earnings (separating *Maintenance CapEx* from *Growth CapEx* + specialized banking/insurance valuation) and ranks the top 3 opportunities head-to-head.
- **🔒 Multi-User & 100% Isolated Personal Finances:** Every user gets a completely private profile (encrypted Read-Only Trading 212 API keys via `AES-128 Fernet`, private monthly budget, Revolut/cash liquidity buffer, high-interest loans for Munger's Inversion, and customizable monthly report day).
- **🤝 Shared Synchronized Watchlist:** Anyone in the group can share a newly discovered undervalued stock via `/add_watch TICKER` — the bot computes its Buffett Intrinsic Value & Target Buy price automatically and notifies the group.
- **🤖 Built-in Buffett & Munger AI Agent (`/ask` + `/set_gemini_key`):** Any user can optionally plug in a free Google AI Studio Gemini API key directly in Telegram (encrypted in DB, chat message auto-deleted) — zero `.env` access required!
- **☁️ Cloud-Ready 24/7 Architecture:** SQLAlchemy 2.0 supporting **PostgreSQL (Neon / Supabase)**, **MSSQL**, and **SQLite**, plus a built-in zero-token HTTP `/health` endpoint (`GET`/`HEAD`) and `Dockerfile` + `render.yaml` for free 24/7 hosting on Render.

## 🚀 Quick Cloud Deploy (Free: Render + Neon PostgreSQL)

See [`DEPLOY-RENDER-NEON.md`](DEPLOY-RENDER-NEON.md) for the complete 10-minute guide (no credit card required).

## ⚙️ Environment Variables

Copy `.env.example` to `.env` (for local runs) or configure them in Render's Environment tab:

```env
TELEGRAM_BOT_TOKEN=your_botfather_token
OWNER_TELEGRAM_ID=your_telegram_numeric_id
BOT_ENCRYPTION_KEY=your_fernet_key
DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
T212_API_KEY=optional_readonly_t212_key
T212_API_SECRET=optional_readonly_t212_secret
T212_ENV=live
GEMINI_API_KEY=optional_google_ai_studio_key
```

## 🛠️ Local Development

```bash
pip install -r requirements.txt
python -m bot.main
```

## 📄 License

This project is licensed under the [MIT License](LICENSE).
