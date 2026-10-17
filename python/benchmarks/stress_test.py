import asyncio
import random
import time
import statistics
from httpx import AsyncClient, ASGITransport
from python.api.main import app
from python.database.connection import close_db_pool
from python.services.redis_service import close_redis

PORTFOLIOS = ["PORT_ALPHA_01", "PORT_VOL_02", "PORT_MOMENTUM_03"]
TOTAL_REQUESTS = 600
CONCURRENCY = 15

async def worker(client: AsyncClient, queue: asyncio.Queue, latencies: list):
    while not queue.empty():
        port_id, num_sims = await queue.get()
        t0 = time.perf_counter()
        resp = await client.post(
            f"/api/v1/portfolios/{port_id}/revalue",
            json={"num_simulations": num_sims, "horizon_days": 1.0}
        )
        t1 = time.perf_counter()
        if resp.status_code == 200:
            latencies.append((t1 - t0) * 1000.0)
        queue.task_done()

async def run_stress_test():
    print("=================================================================")
    print("  RiskGrid Quantitative Risk Revaluation Stress Test            ")
    print(f"  Target: {TOTAL_REQUESTS} revaluations | Concurrency: {CONCURRENCY} workers")
    print("=================================================================\n")

    queue = asyncio.Queue()
    for _ in range(TOTAL_REQUESTS):
        port_id = random.choice(PORTFOLIOS)
        num_sims = 10000 # 10,000 full Monte Carlo paths per evaluation
        await queue.put((port_id, num_sims))

    latencies = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_time = time.perf_counter()
        tasks = [asyncio.create_task(worker(client, queue, latencies)) for _ in range(CONCURRENCY)]
        await queue.join()
        for t in tasks:
            t.cancel()
        total_time = time.perf_counter() - start_time

    await close_db_pool()
    await close_redis()

    latencies.sort()
    throughput = len(latencies) / total_time
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]

    print(f">> Completed:            {len(latencies)} / {TOTAL_REQUESTS} revaluations")
    print(f">> Total Wall Time:      {total_time:.2f} seconds")
    print(f">> Sustained Throughput: {throughput:,.1f} risk-evals/sec")
    print(f">> Latency p50:          {p50:.2f} ms")
    print(f">> Latency p95:          {p95:.2f} ms")
    print(f">> Latency p99:          {p99:.2f} ms")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(run_stress_test())
