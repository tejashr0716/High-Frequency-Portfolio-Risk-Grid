"""
RiskGrid Apache Spark Portfolio Stress-Testing Grid
Runs multi-factor macroeconomic stress scenarios across institutional portfolios.
"""

import os
import sys
import json
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, DoubleType, LongType
)

def build_spark_session(app_name: str = "RiskGrid-Portfolio-Stress-Tester") -> SparkSession:
    return (
        SparkSession.builder
        .appName(app_name)
        .master("local[*]")
        .config("spark.driver.memory", "2g")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )

SCENARIOS = {
    "2008_FINANCIAL_CRISIS": {"EQUITY": -0.40, "CRYPTO": -0.65, "OPTION": -0.50},
    "2020_COVID_LIQUIDITY_SHOCK": {"EQUITY": -0.30, "CRYPTO": -0.55, "OPTION": -0.35},
    "FED_RATE_SHOCK_250BPS": {"EQUITY": -0.15, "CRYPTO": -0.28, "OPTION": -0.20}
}

def run_portfolio_stress_test(positions_json_path: str, output_report_path: str):
    spark = build_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    schema = StructType([
        StructField("portfolio_id", StringType(), True),
        StructField("ticker", StringType(), True),
        StructField("asset_class", StringType(), True),
        StructField("quantity", DoubleType(), True),
        StructField("current_price", DoubleType(), True),
        StructField("implied_volatility", DoubleType(), True)
    ])

    df = spark.read.schema(schema).json(positions_json_path)

    df = df.withColumn("position_value", F.round(F.col("quantity") * F.col("current_price"), 2))

    # Base Portfolio Value
    base_portfolios = df.groupBy("portfolio_id").agg(
        F.round(F.sum("position_value"), 2).alias("base_portfolio_value"),
        F.count("ticker").alias("num_assets")
    )

    scenario_results = {}
    for sc_name, shocks in SCENARIOS.items():
        # Map asset class shocks
        shock_expr = F.when(F.col("asset_class") == "EQUITY", shocks["EQUITY"]) \
                      .when(F.col("asset_class") == "CRYPTO", shocks["CRYPTO"]) \
                      .when(F.col("asset_class") == "OPTION", shocks["OPTION"]) \
                      .otherwise(-0.10)

        df_shocked = df.withColumn("shock_pct", shock_expr) \
                       .withColumn("stressed_price", F.col("current_price") * (1.0 + shock_expr)) \
                       .withColumn("stressed_val", F.col("quantity") * F.col("current_price") * (1.0 + shock_expr))

        stressed_summary = df_shocked.groupBy("portfolio_id").agg(
            F.round(F.sum("stressed_val"), 2).alias("stressed_portfolio_value")
        )

        joined = base_portfolios.join(stressed_summary, on="portfolio_id") \
                                .withColumn("dollar_loss", F.round(F.col("base_portfolio_value") - F.col("stressed_portfolio_value"), 2)) \
                                .withColumn("drawdown_pct", F.round((F.col("dollar_loss") / F.col("base_portfolio_value")) * 100.0, 2)) \
                                .withColumn("capital_status", F.when(F.col("drawdown_pct") > 30.0, "INSOLVENCY_RISK")
                                                              .when(F.col("drawdown_pct") > 15.0, "CAPITAL_IMPAIRMENT")
                                                              .otherwise("ADEQUATE"))

        scenario_results[sc_name] = [row.asDict() for row in joined.collect()]

    report = {
        "engine": "Apache Spark 4.2 Portfolio Stress Tester",
        "scenarios_evaluated": list(SCENARIOS.keys()),
        "scenario_results": scenario_results
    }

    os.makedirs(os.path.dirname(output_report_path), exist_ok=True)
    with open(output_report_path, "w") as f:
        json.dump(report, f, indent=2)

    spark.stop()
    return report

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: spark_portfolio_stress_tester.py <input_positions_json> <output_report_json>")
        sys.exit(1)
    run_portfolio_stress_test(sys.argv[1], sys.argv[2])
