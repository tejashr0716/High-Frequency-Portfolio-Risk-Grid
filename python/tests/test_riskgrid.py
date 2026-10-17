import json
import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from python.api.main import app
from python.services.cpp_bridge import RiskGridEngine
from python.database.connection import close_db_pool
from python.services.redis_service import init_redis, cache_portfolio_risk, get_cached_portfolio_risk, close_redis
from python.spark.spark_portfolio_stress_tester import run_portfolio_stress_test

@pytest.fixture(scope="module")
def cpp_engine():
    return RiskGridEngine()

@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await close_db_pool()
    await close_redis()

def test_cpp_greeks_analytical(cpp_engine):
    # S=100, K=100, T=1.0, r=0.05, sigma=0.20 (ATM Call)
    call_g = cpp_engine.compute_greeks(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, is_call=True)
    assert 9.0 < call_g["theoretical_price"] < 12.0
    assert 0.55 < call_g["delta"] < 0.70
    assert call_g["gamma"] > 0.0
    assert call_g["vega"] > 0.0

    put_g = cpp_engine.compute_greeks(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, is_call=False)
    assert -0.45 < put_g["delta"] < -0.30

def test_cpp_monte_carlo_risk(cpp_engine):
    positions = [
        {"current_price": 100.0, "quantity": 1000.0, "implied_volatility": 0.20, "delta": 1.0, "gamma": 0.0},
        {"current_price": 50.0, "quantity": 2000.0, "implied_volatility": 0.25, "delta": 1.0, "gamma": 0.0}
    ]
    res = cpp_engine.simulate_risk(positions, num_sims=20000, horizon_days=1.0)
    assert res["portfolio_value"] == 200000.0
    assert 0.5 < res["var_95_pct"] < 6.0
    assert res["var_99_pct"] >= res["var_95_pct"]
    assert res["cvar_99_pct"] >= res["var_99_pct"]
    assert res["status_label"] == "COMPLIANT"

def test_cpp_monte_carlo_margin_breach(cpp_engine):
    # 200% implied volatility extreme speculative crypto / meme options
    positions = [
        {"current_price": 1000.0, "quantity": 500.0, "implied_volatility": 2.50, "delta": 1.0, "gamma": 0.0}
    ]
    res = cpp_engine.simulate_risk(positions, num_sims=20000, horizon_days=1.0)
    assert res["var_99_pct"] > 15.0
    assert res["status_label"] == "MARGIN_CALL"

@pytest.mark.asyncio
async def test_redis_portfolio_risk_cache():
    await init_redis()
    mock_data = {"portfolio_id": "TEST_PORT_01", "var_99_pct": 4.5}
    await cache_portfolio_risk("TEST_PORT_01", mock_data)
    cached = await get_cached_portfolio_risk("TEST_PORT_01")
    assert cached is not None
    assert cached["var_99_pct"] == 4.5
    await close_redis()

@pytest.mark.asyncio
async def test_api_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "HEALTHY"

@pytest.mark.asyncio
async def test_api_portfolios_list(async_client):
    resp = await async_client.get("/api/v1/portfolios")
    assert resp.status_code == 200
    portfolios = resp.json()
    assert len(portfolios) >= 3

@pytest.mark.asyncio
async def test_api_portfolio_detail(async_client):
    resp = await async_client.get("/api/v1/portfolios/PORT_ALPHA_01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio"]["portfolio_id"] == "PORT_ALPHA_01"
    assert len(data["positions"]) >= 3

@pytest.mark.asyncio
async def test_api_add_position(async_client):
    payload = {
        "ticker": "GOOGL",
        "asset_class": "EQUITY",
        "quantity": 5000.0,
        "entry_price": 170.0,
        "current_price": 175.0,
        "implied_volatility": 0.22
    }
    resp = await async_client.post("/api/v1/portfolios/PORT_ALPHA_01/positions", json=payload)
    assert resp.status_code == 201
    assert resp.json()["ticker"] == "GOOGL"

@pytest.mark.asyncio
async def test_api_revalue_and_margin_call_lifecycle(async_client):
    # Revalue PORT_ALPHA_01 (Conservative Large-Caps)
    resp = await async_client.post("/api/v1/portfolios/PORT_ALPHA_01/revalue", json={"num_simulations": 10000, "horizon_days": 1.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compliance_status"] in ["COMPLIANT", "WARNING"]
    assert data["risk_metrics"]["portfolio_value"] > 0.0

    # Add ultra high-volatility position to PORT_VOL_02 to trigger margin call
    pos_payload = {
        "ticker": "LEVERAGED_MEME_3X",
        "asset_class": "CRYPTO",
        "quantity": 100000.0,
        "entry_price": 50.0,
        "current_price": 50.0,
        "implied_volatility": 3.80
    }
    await async_client.post("/api/v1/portfolios/PORT_VOL_02/positions", json=pos_payload)

    # Revalue PORT_VOL_02 -> Expected MARGIN_CALL
    rev_resp = await async_client.post("/api/v1/portfolios/PORT_VOL_02/revalue", json={"num_simulations": 10000, "horizon_days": 1.0})
    assert rev_resp.status_code == 200
    rev_data = rev_resp.json()
    assert rev_data["compliance_status"] == "MARGIN_CALL"
    assert rev_data["margin_call_id"] is not None

    # Check pending margin calls
    mc_resp = await async_client.get("/api/v1/margin-calls")
    assert mc_resp.status_code == 200
    calls = mc_resp.json()
    assert len(calls) >= 1

    # Resolve margin call
    call_id = rev_data["margin_call_id"]
    resolve_resp = await async_client.post(f"/api/v1/margin-calls/{call_id}/resolve")
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "RESOLVED"

@pytest.mark.asyncio
async def test_api_greeks_endpoint(async_client):
    resp = await async_client.get("/api/v1/greeks?S=150.0&K=155.0&T=0.5&r=0.05&sigma=0.30&is_call=true")
    assert resp.status_code == 200
    greeks = resp.json()
    assert "delta" in greeks
    assert "gamma" in greeks
    assert "vega" in greeks

def test_spark_stress_tester_pipeline(tmp_path):
    input_file = str(tmp_path / "positions_input.json")
    output_report = str(tmp_path / "stress_report.json")

    mock_positions = [
        {"portfolio_id": "PORT_ALPHA_01", "ticker": "NVDA", "asset_class": "EQUITY", "quantity": 1000.0, "current_price": 130.0, "implied_volatility": 0.40},
        {"portfolio_id": "PORT_ALPHA_01", "ticker": "AAPL", "asset_class": "EQUITY", "quantity": 2000.0, "current_price": 220.0, "implied_volatility": 0.22},
        {"portfolio_id": "PORT_VOL_02", "ticker": "BTC-USD", "asset_class": "CRYPTO", "quantity": 50.0, "current_price": 65000.0, "implied_volatility": 0.70}
    ]

    with open(input_file, "w") as f:
        for pos in mock_positions:
            f.write(json.dumps(pos) + "\n")

    report = run_portfolio_stress_test(input_file, output_report)
    assert len(report["scenarios_evaluated"]) == 3
    assert "2008_FINANCIAL_CRISIS" in report["scenario_results"]
    assert os.path.exists(output_report)
