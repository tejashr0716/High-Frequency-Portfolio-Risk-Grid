-- RiskGrid High-Frequency Algorithmic Risk Schema
DROP TABLE IF EXISTS margin_calls CASCADE;
DROP TABLE IF EXISTS risk_assessments CASCADE;
DROP TABLE IF EXISTS positions CASCADE;
DROP TABLE IF EXISTS portfolios CASCADE;

CREATE TABLE portfolios (
    portfolio_id VARCHAR(32) PRIMARY KEY,
    client_id VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    total_capital DOUBLE PRECISION NOT NULL,
    base_currency VARCHAR(8) DEFAULT 'USD',
    max_drawdown_limit_pct DOUBLE PRECISION DEFAULT 15.0,
    status VARCHAR(32) DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE positions (
    position_id BIGSERIAL PRIMARY KEY,
    portfolio_id VARCHAR(32) NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    ticker VARCHAR(32) NOT NULL,
    asset_class VARCHAR(32) NOT NULL, -- EQUITY, CRYPTO, OPTION, COMMODITY
    quantity DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    current_price DOUBLE PRECISION NOT NULL,
    implied_volatility DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE risk_assessments (
    assessment_id BIGSERIAL PRIMARY KEY,
    portfolio_id VARCHAR(32) NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    total_value DOUBLE PRECISION NOT NULL,
    var_95_pct DOUBLE PRECISION NOT NULL,
    var_99_pct DOUBLE PRECISION NOT NULL,
    cvar_99_pct DOUBLE PRECISION NOT NULL,
    portfolio_delta DOUBLE PRECISION NOT NULL,
    portfolio_gamma DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL, -- COMPLIANT, WARNING, MARGIN_CALL
    computed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE margin_calls (
    call_id BIGSERIAL PRIMARY KEY,
    portfolio_id VARCHAR(32) NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    shortfall_usd DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) DEFAULT 'PENDING', -- PENDING, LIQUIDATED, RESOLVED
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_positions_portfolio ON positions(portfolio_id);
CREATE INDEX idx_risk_portfolio_time ON risk_assessments(portfolio_id, computed_at DESC);
CREATE INDEX idx_margins_portfolio ON margin_calls(portfolio_id, status);

-- Seed Initial Portfolios and Positions
INSERT INTO portfolios (portfolio_id, client_id, name, total_capital, max_drawdown_limit_pct) VALUES
('PORT_ALPHA_01', 'FUND_CITADEL', 'Global Long/Short Alpha Fund', 50000000.0, 12.0),
('PORT_VOL_02', 'FUND_MILLENNIUM', 'Cross-Asset Volatility Arbitrage', 25000000.0, 15.0),
('PORT_MOMENTUM_03', 'FUND_POINT72', 'High-Beta Momentum Longs', 15000000.0, 18.0);

INSERT INTO positions (portfolio_id, ticker, asset_class, quantity, entry_price, current_price, implied_volatility) VALUES
('PORT_ALPHA_01', 'NVDA', 'EQUITY', 15000.0, 115.0, 138.5, 0.42),
('PORT_ALPHA_01', 'AAPL', 'EQUITY', 25000.0, 210.0, 228.0, 0.24),
('PORT_ALPHA_01', 'MSFT', 'EQUITY', 10000.0, 410.0, 425.0, 0.22),
('PORT_VOL_02', 'BTC-USD', 'CRYPTO', 150.0, 58000.0, 67500.0, 0.65),
('PORT_VOL_02', 'ETH-USD', 'CRYPTO', 1200.0, 2400.0, 2650.0, 0.72),
('PORT_VOL_02', 'SPY_2600_PUT', 'OPTION', 500.0, 12.5, 9.8, 0.18),
('PORT_MOMENTUM_03', 'TSLA', 'EQUITY', 20000.0, 215.0, 245.0, 0.58),
('PORT_MOMENTUM_03', 'AMZN', 'EQUITY', 18000.0, 175.0, 192.0, 0.28);
