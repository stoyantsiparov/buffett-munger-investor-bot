# 🚀 24/7 Безплатен хостинг на Бъфет Telegram бота (Render + Neon PostgreSQL + UptimeRobot)

> **Цена:** `€0.00` завинаги • **Банкова карта:** НЕ се изисква • **AI токени на празен ход:** `0`  
> **Време за настройка:** ~10 минути (изцяло през браузъра)

---

## Архитектура на облачния бот
- **Render (Free Web Service, регион Frankfurt):** Държи Python бота пуснат 24/7 чрез `Dockerfile`.
- **Neon (Free PostgreSQL):** Облачна база данни — пази сигурно общия Watchlist, портфейлите, кредитите, бюджетите и AES-128 криптираните API ключове (нищо не се губи при рестарт или обновяване).
- **UptimeRobot (Free):** Изпраща лек HTTP `GET/HEAD` ping към `/health` на всеки 5 минути, за да не заспива Render (`0` Gemini AI токени!).

---

## Стъпка 1: Безплатна PostgreSQL база в Neon (~2 мин)
1. Отвори [neon.tech](https://neon.tech) и влез с Google или GitHub (без банкова карта).
2. Натисни **Create a project**:
   - **Project name:** `buffett-bot`
   - **Region:** `Europe (Frankfurt)` (най-близо до България и Render).
3. В таблото натисни **Connect** → избери **Connection string** и копирай адреса (изглежда така):
   `postgresql://neondb_owner:...@ep-xxxx.eu-central-1.aws.neon.tech/neondb?sslmode=require`
4. Запази го временно — това ще е твоята променлива **`DATABASE_URL`**.

---

## Стъпка 2: Качване на кода в частно (Private) GitHub репозитори (~3 мин)
> 🔒 **Сигурност:** Файлът `.gitignore` вече автоматично блокира `.env`, `data/`, локалната `.db` база и `portfolio-and-budget.md` — **никакви ключове или лични данни няма да се качат в GitHub!**

1. Отвори [github.com/new](https://github.com/new), създай репозитори напр. `buffett-telegram-bot` и избери **Private**.
2. Отвори терминал в папката на проекта и изпълни:
   ```powershell
   git init
   git add .gitignore .dockerignore Dockerfile render.yaml requirements.txt bot/
   git status
   ```
   *(Увери се, че в `git status` **НЯМА** `.env` и **НЯМА** папка `data/`!)*
3. След това качи кода:
   ```powershell
   git commit -m "Deploy Buffett & Munger Telegram Bot to Render"
   git branch -M main
   git remote add origin https://github.com/ТВОЕТО_ИМЕ/buffett-telegram-bot.git
   git push -u origin main
   ```

---

## Стъпка 3: Стартиране в Render (~3 мин)
1. Отвори [render.com](https://render.com) и влез с GitHub (без банкова карта).
2. Натисни **New +** → **Blueprint** *(или **Web Service**)* и избери репозиторито `buffett-telegram-bot`:
   - **Runtime:** `Docker`
   - **Region:** `Frankfurt (EU Central)`
   - **Instance Type:** `Free ($0/month)`
3. В секция **Environment Variables** добави стойностите от локалния си `.env` файл + Neon адреса:

| Променлива | Задължителна? | Откъде я взимаш |
| :--- | :---: | :--- |
| `TELEGRAM_BOT_TOKEN` | ✅ Да | От локалния `.env` файл |
| `OWNER_TELEGRAM_ID` | ✅ Да | Твоят Telegram User ID (може да се вземе от `@userinfobot`) |
| `BOT_ENCRYPTION_KEY` | ✅ Да | От локалния `.env` файл *(важно, за да не се променя при рестарт!)* |
| `DATABASE_URL` | ✅ Да | Connection string-ът от **Neon** (Стъпка 1) |
| `T212_API_KEY` | ✅ Да | От локалния `.env` файл *(за автоматично зареждане на твоя T212)* |
| `T212_API_SECRET` | ✅ Да | От локалния `.env` файл |
| `T212_ENV` | ✅ Да | `live` |
| `GEMINI_API_KEY` | 💡 По избор | Можеш да го сложиш тук ИЛИ направо в Telegram с `/set_gemini_key` |

4. ⚠️ **ВАЖНО преди натискане на Deploy:** Спри локалния бот на лаптопа си! Telegram позволява **само 1 работещ бот** на един токен (ако работят и лаптопът, и Render едновременно, ще дава `409 Conflict`).
5. Натисни **Create Web Service / Deploy**. След ~2 минути в логовете на Render ще видиш:
   - `HTTP health server listening on 0.0.0.0:10000 (/health)`
   - `Registered 15 native Telegram slash commands.`
   - `Application started`
6. Копирай публичния адрес на услугата горе вляво (напр. `https://buffett-munger-telegram-bot.onrender.com`).

---

## Стъпка 4: Настройка на UptimeRobot, за да не заспива Render (~2 мин)
1. Отвори [uptimerobot.com](https://uptimerobot.com) и направи безплатен профил (без карта).
2. Натисни **+ New Monitor**:
   - **Monitor Type:** `HTTP(s)`
   - **Friendly Name:** `Buffett Telegram Bot`
   - **URL:** `https://ТВОЯТ-АДРЕС.onrender.com/health`
   - **Monitoring Interval:** `5 minutes`
3. Натисни **Create Monitor**.

Готово! 🎉 Сега ботът работи **24/7 в облака** за теб и всичките ти приятели, дори когато лаптопът ти е напълно изключен!

---

## 💡 Как се обновява ботът в бъдеще?
Когато добавим нова функция тук на лаптопа, просто изпълняваш:
```powershell
git add bot/ requirements.txt
git commit -m "Update bot"
git push
```
Render автоматично изгражда новия контейнер и обновява бота за ~90 секунди, без да губи никакви данни от Neon!
