-- ============================================================================
-- T-SQL Схема за Microsoft SQL Server (SSMS / Azure SQL)
-- Приложение: Buffett & Munger Multi-User Telegram Investment Bot
-- За да използвате MSSQL вместо SQLite, изпълнете този скрипт в SSMS и сложете
-- в .env файла:
-- DATABASE_URL=mssql+pyodbc://USER:PASSWORD@SERVER_NAME/BuffettBotDB?driver=ODBC+Driver+17+for+SQL+Server
-- (или за Windows Authentication на локален SQL Server):
-- DATABASE_URL=mssql+pyodbc://@localhost\SQLEXPRESS/BuffettBotDB?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes
-- ============================================================================

IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = N'BuffettBotDB')
BEGIN
    CREATE DATABASE [BuffettBotDB];
END
GO

USE [BuffettBotDB];
GO

-- 1. Таблица: Потребители (users)
IF OBJECT_ID(N'dbo.users', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.users (
        id INT IDENTITY(1,1) PRIMARY KEY,
        telegram_id BIGINT NOT NULL UNIQUE,
        username NVARCHAR(128) NULL,
        full_name NVARCHAR(256) NULL,

        -- Криптирани (Fernet AES-128-CBC + HMAC) Read-Only ключове за Trading 212 и опционален личен Gemini AI ключ
        t212_api_key_enc NVARCHAR(512) NULL,
        t212_api_secret_enc NVARCHAR(512) NULL,
        t212_env NVARCHAR(16) NOT NULL DEFAULT N'live',
        t212_account_id NVARCHAR(64) NULL,
        t212_currency NVARCHAR(16) NOT NULL DEFAULT N'EUR',
        gemini_api_key_enc NVARCHAR(512) NULL,

        -- Личен бюджет и правила на Бъфет/Мънгър (празни по подразбиране за нови приятели)
        monthly_income FLOAT NOT NULL DEFAULT 0.0,
        monthly_invest_budget FLOAT NOT NULL DEFAULT 0.0,
        emergency_buffer_target FLOAT NOT NULL DEFAULT 500.0,
        emergency_buffer_current FLOAT NOT NULL DEFAULT 0.0,
        payday_day INT NOT NULL DEFAULT 1,
        investment_plan NVARCHAR(MAX) NULL,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
    );

    CREATE INDEX IX_users_telegram_id ON dbo.users(telegram_id);
END
GO

-- 2. Таблица: Кредити с висока лихва за Инверсия на Мънгър (user_loans)
IF OBJECT_ID(N'dbo.user_loans', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.user_loans (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        lender_name NVARCHAR(128) NOT NULL DEFAULT N'Bank Loan',
        contract_number NVARCHAR(64) NULL,
        principal_balance FLOAT NOT NULL DEFAULT 0.0,
        apr_percent FLOAT NOT NULL DEFAULT 0.0,
        regular_payment FLOAT NOT NULL DEFAULT 0.0,
        early_principal_payment FLOAT NOT NULL DEFAULT 0.0,
        early_fee_percent FLOAT NOT NULL DEFAULT 0.5,
        due_day INT NOT NULL DEFAULT 15,
        is_active BIT NOT NULL DEFAULT 1,
        notes NVARCHAR(512) NULL,
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_user_loans_users FOREIGN KEY (user_id)
            REFERENCES dbo.users(id) ON DELETE CASCADE
    );

    CREATE INDEX IX_user_loans_user_id ON dbo.user_loans(user_id);
END
GO

-- 3. Таблица: Исторически снимки на портфейла от Trading 212 (portfolio_snapshots)
IF OBJECT_ID(N'dbo.portfolio_snapshots', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portfolio_snapshots (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        total_value FLOAT NOT NULL DEFAULT 0.0,
        invested_value FLOAT NOT NULL DEFAULT 0.0,
        free_cash FLOAT NOT NULL DEFAULT 0.0,
        pie_cash FLOAT NOT NULL DEFAULT 0.0,
        unrealized_ppl FLOAT NOT NULL DEFAULT 0.0,
        realized_result FLOAT NOT NULL DEFAULT 0.0,
        semiconductor_pct FLOAT NOT NULL DEFAULT 0.0,
        positions_count INT NOT NULL DEFAULT 0,
        pending_orders_count INT NOT NULL DEFAULT 0,
        raw_json NVARCHAR(MAX) NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_portfolio_snapshots_users FOREIGN KEY (user_id)
            REFERENCES dbo.users(id) ON DELETE CASCADE
    );

    CREATE INDEX IX_portfolio_snapshots_user_id ON dbo.portfolio_snapshots(user_id);
END
GO

-- 4. Таблица: Бъфет Watchlist и ценови аларми (watchlist_items)
IF OBJECT_ID(N'dbo.watchlist_items', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.watchlist_items (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        rank_order INT NOT NULL DEFAULT 99,
        ticker NVARCHAR(32) NOT NULL,
        company_name NVARCHAR(128) NULL,
        currency NVARCHAR(8) NOT NULL DEFAULT N'USD',
        moat_summary NVARCHAR(256) NULL,
        roic_pct FLOAT NULL,
        intrinsic_value_low FLOAT NULL,
        intrinsic_value_high FLOAT NULL,
        target_buy_price FLOAT NOT NULL,
        margin_of_safety_pct FLOAT NOT NULL DEFAULT 25.0,
        last_price FLOAT NULL,
        last_checked_at DATETIME2 NULL,
        alert_enabled BIT NOT NULL DEFAULT 1,
        last_alert_sent_at DATETIME2 NULL,
        notes NVARCHAR(512) NULL,

        CONSTRAINT FK_watchlist_items_users FOREIGN KEY (user_id)
            REFERENCES dbo.users(id) ON DELETE CASCADE,
        CONSTRAINT uq_user_watchlist_ticker UNIQUE (user_id, ticker)
    );

    CREATE INDEX IX_watchlist_items_user_id ON dbo.watchlist_items(user_id);
    CREATE INDEX IX_watchlist_items_ticker ON dbo.watchlist_items(ticker);
END
GO
