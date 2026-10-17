#ifndef RISKGRID_H
#define RISKGRID_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#pragma pack(push, 8)
typedef struct {
    double current_price;
    double quantity;
    double implied_volatility; // annualized volatility e.g. 0.35
    double delta;              // 1.0 for equity, Black-Scholes delta for options
    double gamma;              // 0.0 for equity, Black-Scholes gamma for options
} PositionData;

typedef struct {
    double portfolio_value;
    double var_95_pct;         // VaR 95% (e.g. 3.25%)
    double var_99_pct;         // VaR 99% (e.g. 5.80%)
    double cvar_99_pct;        // CVaR / Expected Shortfall 99% (e.g. 7.95%)
    double portfolio_delta;    // Aggregate dollar delta ($)
    double portfolio_gamma;    // Aggregate portfolio gamma
    double max_simulated_loss; // Worst case loss in simulated paths ($)
    int32_t status_code;       // 0: COMPLIANT, 1: WARNING, 2: MARGIN_CALL
    char status_label[32];     // "COMPLIANT", "WARNING", "MARGIN_CALL"
} PortfolioRiskMetrics;
#pragma pack(pop)

// Monte Carlo Portfolio Value-at-Risk & Expected Shortfall simulation
void simulate_monte_carlo_risk(const PositionData* positions, int32_t num_positions,
                               int32_t num_simulations, double time_horizon_days,
                               PortfolioRiskMetrics* out_metrics);

// Closed-form analytical Black-Scholes Greeks calculator
void calculate_black_scholes_greeks(double S, double K, double T, double r, double sigma,
                                    int32_t is_call, double* out_price, double* out_delta,
                                    double* out_gamma, double* out_vega, double* out_theta);

#ifdef __cplusplus
}
#endif

#endif // RISKGRID_H
