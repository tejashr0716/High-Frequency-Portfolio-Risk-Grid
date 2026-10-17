import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from python.database.connection import init_db_pool, close_db_pool, get_db_pool
from python.services.cpp_bridge import RiskGridEngine
from python.services.redis_service import (
    init_redis, close_redis, cache_portfolio_risk, get_cached_portfolio_risk, publish_risk_update
)
from python.services.rabbitmq_service import init_rabbitmq, close_rabbitmq, publish_margin_call

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("riskgrid.api")

engine: Optional[RiskGridEngine] = None

def get_engine() -> RiskGridEngine:
    global engine
    if engine is None:
        engine = RiskGridEngine()
    return engine

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing RiskGrid Services...")
    await init_db_pool()
    await init_redis()
    await init_rabbitmq()
    get_engine()
    logger.info("RiskGrid C++ Engine and distributed services ready.")
    yield
    logger.info("Shutting down RiskGrid Services...")
    await close_rabbitmq()
    await close_redis()
    await close_db_pool()

app = FastAPI(
    title="RiskGrid High-Frequency Risk Management Engine",
    version="1.0.0",
    description="Sub-Millisecond Quantitative Portfolio Risk & Monte Carlo VaR Grid",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PositionCreatePayload(BaseModel):
    ticker: str
    asset_class: str = "EQUITY"
    quantity: float
    entry_price: float
    current_price: float
    implied_volatility: float = 0.25

class RevalueRequest(BaseModel):
    num_simulations: int = Field(50000, ge=1000, le=500000)
    horizon_days: float = Field(1.0, ge=0.1, le=30.0)

@app.get("/health")
async def health_check():
    return {
        "status": "HEALTHY",
        "service": "RiskGrid Risk Gateway",
        "engine": "C++ Monte Carlo & Black-Scholes Greeks Loaded",
        "active_websockets": len(ws_manager.active_connections)
    }

@app.get("/api/v1/portfolios")
async def get_portfolios():
    db = await get_db_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM portfolios ORDER BY portfolio_id ASC;")
        output = []
        for r in rows:
            latest = await conn.fetchrow(
                "SELECT * FROM risk_assessments WHERE portfolio_id = $1 ORDER BY computed_at DESC LIMIT 1;",
                r["portfolio_id"]
            )
            positions_count = await conn.fetchval(
                "SELECT COUNT(*) FROM positions WHERE portfolio_id = $1;", r["portfolio_id"]
            )
            output.append({
                "portfolio_id": r["portfolio_id"],
                "client_id": r["client_id"],
                "name": r["name"],
                "total_capital": r["total_capital"],
                "base_currency": r["base_currency"],
                "max_drawdown_limit_pct": r["max_drawdown_limit_pct"],
                "status": r["status"],
                "positions_count": positions_count,
                "latest_assessment": {
                    "total_value": latest["total_value"],
                    "var_95_pct": latest["var_95_pct"],
                    "var_99_pct": latest["var_99_pct"],
                    "cvar_99_pct": latest["cvar_99_pct"],
                    "portfolio_delta": latest["portfolio_delta"],
                    "portfolio_gamma": latest["portfolio_gamma"],
                    "status": latest["status"],
                    "computed_at": latest["computed_at"].isoformat()
                } if latest else None
            })
        return output

@app.get("/api/v1/portfolios/{portfolio_id}")
async def get_portfolio_detail(portfolio_id: str):
    db = await get_db_pool()
    async with db.acquire() as conn:
        port = await conn.fetchrow("SELECT * FROM portfolios WHERE portfolio_id = $1;", portfolio_id)
        if not port:
            raise HTTPException(status_code=404, detail=f"Portfolio '{portfolio_id}' not found.")

        positions = await conn.fetch("SELECT * FROM positions WHERE portfolio_id = $1 ORDER BY position_id ASC;", portfolio_id)
        cached_risk = await get_cached_portfolio_risk(portfolio_id)

        return {
            "portfolio": dict(port),
            "positions": [dict(p) for p in positions],
            "cached_risk": cached_risk
        }

@app.post("/api/v1/portfolios/{portfolio_id}/positions", status_code=status.HTTP_201_CREATED)
async def add_position(portfolio_id: str, payload: PositionCreatePayload):
    db = await get_db_pool()
    async with db.acquire() as conn:
        port = await conn.fetchrow("SELECT * FROM portfolios WHERE portfolio_id = $1;", portfolio_id)
        if not port:
            raise HTTPException(status_code=404, detail=f"Portfolio '{portfolio_id}' not found.")

        row = await conn.fetchrow(
            """
            INSERT INTO positions (portfolio_id, ticker, asset_class, quantity, entry_price, current_price, implied_volatility)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING position_id, updated_at;
            """,
            portfolio_id, payload.ticker, payload.asset_class, payload.quantity,
            payload.entry_price, payload.current_price, payload.implied_volatility
        )
        return {"position_id": row["position_id"], "portfolio_id": portfolio_id, "ticker": payload.ticker}

@app.post("/api/v1/portfolios/{portfolio_id}/revalue")
async def revalue_portfolio(portfolio_id: str, req: RevalueRequest = RevalueRequest()):
    db = await get_db_pool()
    eng = get_engine()
    async with db.acquire() as conn:
        port = await conn.fetchrow("SELECT * FROM portfolios WHERE portfolio_id = $1;", portfolio_id)
        if not port:
            raise HTTPException(status_code=404, detail=f"Portfolio '{portfolio_id}' not found.")

        positions = await conn.fetch("SELECT * FROM positions WHERE portfolio_id = $1;", portfolio_id)
        if not positions:
            raise HTTPException(status_code=400, detail="Portfolio has no positions to revalue.")

        pos_items = []
        for p in positions:
            delta = 1.0
            gamma = 0.0
            if p["asset_class"] == "OPTION":
                g = eng.compute_greeks(p["current_price"], p["entry_price"], 0.25, 0.05, p["implied_volatility"])
                delta = g["delta"]
                gamma = g["gamma"]

            pos_items.append({
                "current_price": p["current_price"],
                "quantity": p["quantity"],
                "implied_volatility": p["implied_volatility"],
                "delta": delta,
                "gamma": gamma
            })

        # Run C++ High-Speed Monte Carlo Simulation
        risk_result = eng.simulate_risk(
            pos_items,
            num_sims=req.num_simulations,
            horizon_days=req.horizon_days
        )

        max_allowed_drawdown = port["max_drawdown_limit_pct"]
        is_breached = risk_result["var_99_pct"] > max_allowed_drawdown
        assessment_status = "MARGIN_CALL" if is_breached else ("WARNING" if risk_result["var_99_pct"] > (max_allowed_drawdown * 0.75) else "COMPLIANT")

        row = await conn.fetchrow(
            """
            INSERT INTO risk_assessments (portfolio_id, total_value, var_95_pct, var_99_pct, cvar_99_pct, portfolio_delta, portfolio_gamma, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING assessment_id, computed_at;
            """,
            portfolio_id, risk_result["portfolio_value"], risk_result["var_95_pct"],
            risk_result["var_99_pct"], risk_result["cvar_99_pct"], risk_result["portfolio_delta"],
            risk_result["portfolio_gamma"], assessment_status
        )

        margin_call_id = None
        if is_breached:
            shortfall = risk_result["portfolio_value"] * (risk_result["var_99_pct"] - max_allowed_drawdown) / 100.0
            mc_row = await conn.fetchrow(
                """
                INSERT INTO margin_calls (portfolio_id, shortfall_usd, status)
                VALUES ($1, $2, 'PENDING')
                RETURNING call_id;
                """,
                portfolio_id, shortfall
            )
            margin_call_id = mc_row["call_id"]
            # Publish event to RabbitMQ
            await publish_margin_call("riskgrid.margin.call", {
                "call_id": margin_call_id,
                "portfolio_id": portfolio_id,
                "shortfall_usd": shortfall,
                "var_99_pct": risk_result["var_99_pct"],
                "limit_pct": max_allowed_drawdown
            })

        # Cache in Redis
        event_data = {
            "type": "PORTFOLIO_REVALUE",
            "assessment_id": row["assessment_id"],
            "portfolio_id": portfolio_id,
            "risk_metrics": risk_result,
            "compliance_status": assessment_status,
            "margin_call_id": margin_call_id,
            "computed_at": row["computed_at"].isoformat()
        }
        await cache_portfolio_risk(portfolio_id, event_data)
        await publish_risk_update(event_data)
        asyncio.create_task(ws_manager.broadcast(event_data))

        return event_data

@app.get("/api/v1/greeks")
async def calculate_greeks(
    S: float = Query(..., description="Spot Price", ge=0.01),
    K: float = Query(..., description="Strike Price", ge=0.01),
    T: float = Query(..., description="Time to Expiry in Years", ge=0.001),
    r: float = Query(0.05, description="Risk-free Interest Rate"),
    sigma: float = Query(0.25, description="Implied Volatility", ge=0.001),
    is_call: bool = Query(True, description="True for Call, False for Put")
):
    eng = get_engine()
    return eng.compute_greeks(S=S, K=K, T=T, r=r, sigma=sigma, is_call=is_call)

@app.get("/api/v1/margin-calls")
async def get_margin_calls(status_filter: str = "PENDING"):
    db = await get_db_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM margin_calls WHERE status = $1 ORDER BY created_at DESC;", status_filter)
        return [
            {
                "call_id": r["call_id"],
                "portfolio_id": r["portfolio_id"],
                "shortfall_usd": r["shortfall_usd"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
                "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None
            }
            for r in rows
        ]

@app.post("/api/v1/margin-calls/{call_id}/resolve")
async def resolve_margin_call(call_id: int):
    db = await get_db_pool()
    async with db.acquire() as conn:
        res = await conn.execute(
            "UPDATE margin_calls SET status = 'RESOLVED', resolved_at = NOW() WHERE call_id = $1;",
            call_id
        )
        if res == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Margin call not found.")
        return {"call_id": call_id, "status": "RESOLVED"}

@app.websocket("/ws/risk")
async def websocket_risk(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "CONNECTED",
            "message": "RiskGrid High-Frequency Risk Stream Active"
        })
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
