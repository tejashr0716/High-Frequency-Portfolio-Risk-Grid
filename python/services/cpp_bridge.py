import ctypes
import os
from typing import List, Dict, Any, Optional

class CPositionData(ctypes.Structure):
    _layout_ = "ms"
    _pack_ = 8
    _fields_ = [
        ("current_price", ctypes.c_double),
        ("quantity", ctypes.c_double),
        ("implied_volatility", ctypes.c_double),
        ("delta", ctypes.c_double),
        ("gamma", ctypes.c_double)
    ]

class CPortfolioRiskMetrics(ctypes.Structure):
    _layout_ = "ms"
    _pack_ = 8
    _fields_ = [
        ("portfolio_value", ctypes.c_double),
        ("var_95_pct", ctypes.c_double),
        ("var_99_pct", ctypes.c_double),
        ("cvar_99_pct", ctypes.c_double),
        ("portfolio_delta", ctypes.c_double),
        ("portfolio_gamma", ctypes.c_double),
        ("max_simulated_loss", ctypes.c_double),
        ("status_code", ctypes.c_int32),
        ("status_label", ctypes.c_char * 32)
    ]

class RiskGridEngine:
    def __init__(self, dylib_path: Optional[str] = None):
        if dylib_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            dylib_path = os.path.join(base_dir, "libriskgrid.dylib")

        if not os.path.exists(dylib_path):
            raise FileNotFoundError(f"Shared library not found at: {dylib_path}")

        self.lib = ctypes.CDLL(dylib_path)

        # void simulate_monte_carlo_risk(const PositionData* positions, int32_t num_positions, int32_t num_simulations, double time_horizon_days, PortfolioRiskMetrics* out_metrics);
        self.lib.simulate_monte_carlo_risk.argtypes = [
            ctypes.POINTER(CPositionData),
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_double,
            ctypes.POINTER(CPortfolioRiskMetrics)
        ]
        self.lib.simulate_monte_carlo_risk.restype = None

        # void calculate_black_scholes_greeks(double S, double K, double T, double r, double sigma, int32_t is_call, double* out_price, double* out_delta, double* out_gamma, double* out_vega, double* out_theta);
        self.lib.calculate_black_scholes_greeks.argtypes = [
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double)
        ]
        self.lib.calculate_black_scholes_greeks.restype = None

    def simulate_risk(self, positions: List[Dict[str, float]], num_sims: int = 50000, horizon_days: float = 1.0) -> Dict[str, Any]:
        n = len(positions)
        if n == 0:
            return {
                "portfolio_value": 0.0,
                "var_95_pct": 0.0,
                "var_99_pct": 0.0,
                "cvar_99_pct": 0.0,
                "portfolio_delta": 0.0,
                "portfolio_gamma": 0.0,
                "max_simulated_loss": 0.0,
                "status_code": 0,
                "status_label": "COMPLIANT"
            }

        pos_arr = (CPositionData * n)()
        for i, p in enumerate(positions):
            pos_arr[i] = CPositionData(
                current_price=float(p.get("current_price", 100.0)),
                quantity=float(p.get("quantity", 0.0)),
                implied_volatility=float(p.get("implied_volatility", 0.25)),
                delta=float(p.get("delta", 1.0)),
                gamma=float(p.get("gamma", 0.0))
            )

        out_metrics = CPortfolioRiskMetrics()
        self.lib.simulate_monte_carlo_risk(
            pos_arr,
            ctypes.c_int32(n),
            ctypes.c_int32(num_sims),
            ctypes.c_double(horizon_days),
            ctypes.byref(out_metrics)
        )

        return {
            "portfolio_value": round(out_metrics.portfolio_value, 2),
            "var_95_pct": round(out_metrics.var_95_pct, 2),
            "var_99_pct": round(out_metrics.var_99_pct, 2),
            "cvar_99_pct": round(out_metrics.cvar_99_pct, 2),
            "portfolio_delta": round(out_metrics.portfolio_delta, 2),
            "portfolio_gamma": round(out_metrics.portfolio_gamma, 4),
            "max_simulated_loss": round(out_metrics.max_simulated_loss, 2),
            "status_code": out_metrics.status_code,
            "status_label": out_metrics.status_label.decode("utf-8")
        }

    def compute_greeks(self, S: float, K: float, T: float, r: float, sigma: float, is_call: bool = True) -> Dict[str, float]:
        price = ctypes.c_double()
        delta = ctypes.c_double()
        gamma = ctypes.c_double()
        vega = ctypes.c_double()
        theta = ctypes.c_double()

        self.lib.calculate_black_scholes_greeks(
            ctypes.c_double(S),
            ctypes.c_double(K),
            ctypes.c_double(T),
            ctypes.c_double(r),
            ctypes.c_double(sigma),
            ctypes.c_int32(1 if is_call else 0),
            ctypes.byref(price),
            ctypes.byref(delta),
            ctypes.byref(gamma),
            ctypes.byref(vega),
            ctypes.byref(theta)
        )

        return {
            "theoretical_price": round(price.value, 4),
            "delta": round(delta.value, 4),
            "gamma": round(gamma.value, 6),
            "vega": round(vega.value, 4),
            "theta": round(theta.value, 4)
        }
