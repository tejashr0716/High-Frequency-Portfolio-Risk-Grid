#include "riskgrid.h"
#include <iostream>
#include <chrono>
#include <vector>
#include <iomanip>

int main() {
    std::cout << "========================================================\n";
    std::cout << "   RiskGrid C++ High-Frequency Monte Carlo Benchmark   \n";
    std::cout << "========================================================\n\n";

    // 1. Black-Scholes Greeks Benchmark
    const int GREEKS_RUNS = 2000000;
    double price, delta, gamma, vega, theta;
    auto start = std::chrono::high_resolution_clock::now();
    for (int i = 0; i < GREEKS_RUNS; ++i) {
        calculate_black_scholes_greeks(150.0 + (i % 50), 155.0, 0.25, 0.05, 0.35, 1,
                                       &price, &delta, &gamma, &vega, &theta);
    }
    auto end = std::chrono::high_resolution_clock::now();
    double greeks_sec = std::chrono::duration<double>(end - start).count();
    double greeks_ops = (double)GREEKS_RUNS / greeks_sec;
    double greeks_ns = (greeks_sec / (double)GREEKS_RUNS) * 1e9;

    std::cout << ">> Black-Scholes Greeks Throughput: " << std::fixed << std::setprecision(2)
              << (greeks_ops / 1e6) << " Million evals/sec ("
              << greeks_ns << " ns/op)\n";

    // 2. Monte Carlo Portfolio Simulation Benchmark
    std::vector<PositionData> positions = {
        { 138.5, 15000.0, 0.42, 1.0, 0.0 }, // NVDA
        { 228.0, 25000.0, 0.24, 1.0, 0.0 }, // AAPL
        { 425.0, 10000.0, 0.22, 1.0, 0.0 }, // MSFT
        { 67500.0, 150.0, 0.65, 1.0, 0.0 }, // BTC
        { 2650.0, 1200.0, 0.72, 1.0, 0.0 }  // ETH
    };

    const int NUM_SIMS = 100000;
    PortfolioRiskMetrics metrics;

    start = std::chrono::high_resolution_clock::now();
    simulate_monte_carlo_risk(positions.data(), positions.size(), NUM_SIMS, 1.0, &metrics);
    end = std::chrono::high_resolution_clock::now();

    double mc_sec = std::chrono::duration<double>(end - start).count();
    double paths_per_sec = (double)(NUM_SIMS * positions.size()) / mc_sec;

    std::cout << ">> Monte Carlo Path Throughput:     " << std::fixed << std::setprecision(2)
              << (paths_per_sec / 1e6) << " Million asset-paths/sec ("
              << (mc_sec * 1000.0) << " ms for " << NUM_SIMS << " full portfolio paths)\n";

    std::cout << "\nComputed Portfolio Risk Assessment:\n";
    std::cout << "  Portfolio Value:   $" << metrics.portfolio_value << "\n";
    std::cout << "  VaR 95% (1-Day):   " << metrics.var_95_pct << "%\n";
    std::cout << "  VaR 99% (1-Day):   " << metrics.var_99_pct << "%\n";
    std::cout << "  CVaR 99% (ES):     " << metrics.cvar_99_pct << "%\n";
    std::cout << "  Dollar Delta:      $" << metrics.portfolio_delta << "\n";
    std::cout << "  Status:            " << metrics.status_label << "\n";
    std::cout << "========================================================\n";

    return 0;
}
