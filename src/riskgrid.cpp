#include "riskgrid.h"
#include <cmath>
#include <vector>
#include <random>
#include <algorithm>
#include <cstring>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static double norm_cdf(double x) {
    return 0.5 * std::erfc(-x * M_SQRT1_2);
}

static double norm_pdf(double x) {
    return (1.0 / std::sqrt(2.0 * M_PI)) * std::exp(-0.5 * x * x);
}

extern "C" {

void calculate_black_scholes_greeks(double S, double K, double T, double r, double sigma,
                                    int32_t is_call, double* out_price, double* out_delta,
                                    double* out_gamma, double* out_vega, double* out_theta) {
    if (S <= 0.0 || K <= 0.0 || T <= 0.0 || sigma <= 0.0) {
        if (out_price) *out_price = 0.0;
        if (out_delta) *out_delta = is_call ? 1.0 : -1.0;
        if (out_gamma) *out_gamma = 0.0;
        if (out_vega) *out_vega = 0.0;
        if (out_theta) *out_theta = 0.0;
        return;
    }

    double sqrt_T = std::sqrt(T);
    double d1 = (std::log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T);
    double d2 = d1 - sigma * sqrt_T;

    double exp_rt = std::exp(-r * T);
    double price, delta, gamma, vega, theta;

    double pdf_d1 = norm_pdf(d1);

    if (is_call) {
        price = S * norm_cdf(d1) - K * exp_rt * norm_cdf(d2);
        delta = norm_cdf(d1);
        theta = (- (S * pdf_d1 * sigma) / (2.0 * sqrt_T) - r * K * exp_rt * norm_cdf(d2)) / 365.0;
    } else {
        price = K * exp_rt * norm_cdf(-d2) - S * norm_cdf(-d1);
        delta = norm_cdf(d1) - 1.0;
        theta = (- (S * pdf_d1 * sigma) / (2.0 * sqrt_T) + r * K * exp_rt * norm_cdf(-d2)) / 365.0;
    }

    gamma = pdf_d1 / (S * sigma * sqrt_T);
    vega = (S * sqrt_T * pdf_d1) / 100.0; // 1% vol change

    if (out_price) *out_price = price;
    if (out_delta) *out_delta = delta;
    if (out_gamma) *out_gamma = gamma;
    if (out_vega) *out_vega = vega;
    if (out_theta) *out_theta = theta;
}

void simulate_monte_carlo_risk(const PositionData* positions, int32_t num_positions,
                               int32_t num_simulations, double time_horizon_days,
                               PortfolioRiskMetrics* out_metrics) {
    if (!positions || num_positions <= 0 || num_simulations <= 0 || !out_metrics) return;

    std::memset(out_metrics, 0, sizeof(PortfolioRiskMetrics));

    double portfolio_value = 0.0;
    double net_delta = 0.0;
    double net_gamma = 0.0;

    for (int32_t i = 0; i < num_positions; ++i) {
        double pos_val = positions[i].current_price * positions[i].quantity;
        portfolio_value += pos_val;
        net_delta += pos_val * positions[i].delta;
        net_gamma += pos_val * positions[i].gamma;
    }

    if (portfolio_value <= 0.0) {
        out_metrics->status_code = 0;
        std::strncpy(out_metrics->status_label, "COMPLIANT", sizeof(out_metrics->status_label) - 1);
        return;
    }

    double dt = (time_horizon_days > 0.0 ? time_horizon_days : 1.0) / 252.0;
    double sqrt_dt = std::sqrt(dt);

    std::vector<double> sim_returns(num_simulations, 0.0);

    // Fast deterministic pseudo-random generator
    std::mt19937_64 rng(1337);
    std::normal_distribution<double> norm_dist(0.0, 1.0);

    for (int32_t s = 0; s < num_simulations; ++s) {
        double sim_val = 0.0;
        // Common market factor shock
        double market_shock = norm_dist(rng);

        for (int32_t i = 0; i < num_positions; ++i) {
            double idio_shock = norm_dist(rng);
            // Blend 60% systematic market risk + 40% idiosyncratic risk
            double combined_shock = 0.6 * market_shock + 0.8 * idio_shock;

            double vol = positions[i].implied_volatility > 0.0 ? positions[i].implied_volatility : 0.25;
            double drift = -0.5 * vol * vol * dt;
            double diffusion = vol * sqrt_dt * combined_shock;

            double next_price = positions[i].current_price * std::exp(drift + diffusion);
            sim_val += next_price * positions[i].quantity;
        }

        sim_returns[s] = (sim_val - portfolio_value) / portfolio_value;
    }

    std::sort(sim_returns.begin(), sim_returns.end());

    int32_t idx_95 = (int32_t)(num_simulations * 0.05);
    int32_t idx_99 = (int32_t)(num_simulations * 0.01);
    if (idx_95 < 0) idx_95 = 0;
    if (idx_99 < 0) idx_99 = 0;

    double var_95 = -sim_returns[idx_95] * 100.0;
    double var_99 = -sim_returns[idx_99] * 100.0;

    // Expected Shortfall (CVaR 99%): mean of tail losses
    double tail_sum = 0.0;
    int32_t tail_count = std::max(1, idx_99);
    for (int32_t i = 0; i < tail_count; ++i) {
        tail_sum += (-sim_returns[i] * 100.0);
    }
    double cvar_99 = tail_sum / (double)tail_count;
    double max_loss = -sim_returns[0] * portfolio_value;

    out_metrics->portfolio_value = portfolio_value;
    out_metrics->var_95_pct = std::max(0.0, var_95);
    out_metrics->var_99_pct = std::max(0.0, var_99);
    out_metrics->cvar_99_pct = std::max(0.0, cvar_99);
    out_metrics->portfolio_delta = net_delta;
    out_metrics->portfolio_gamma = net_gamma;
    out_metrics->max_simulated_loss = std::max(0.0, max_loss);

    if (var_99 >= 15.0) {
        out_metrics->status_code = 2;
        std::strncpy(out_metrics->status_label, "MARGIN_CALL", sizeof(out_metrics->status_label) - 1);
    } else if (var_99 >= 10.0) {
        out_metrics->status_code = 1;
        std::strncpy(out_metrics->status_label, "WARNING", sizeof(out_metrics->status_label) - 1);
    } else {
        out_metrics->status_code = 0;
        std::strncpy(out_metrics->status_label, "COMPLIANT", sizeof(out_metrics->status_label) - 1);
    }
}

} // extern "C"
