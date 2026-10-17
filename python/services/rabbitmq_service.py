import json
import logging
import os
import aio_pika
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")

connection: Optional[aio_pika.RobustConnection] = None
channel: Optional[aio_pika.RobustChannel] = None
exchange: Optional[aio_pika.RobustExchange] = None

async def init_rabbitmq():
    global connection, channel, exchange
    try:
        connection = await aio_pika.connect_robust(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            login=RABBITMQ_USER,
            password=RABBITMQ_PASSWORD
        )
        channel = await connection.channel()
        exchange = await channel.declare_exchange("riskgrid.events", aio_pika.ExchangeType.TOPIC, durable=True)

        margins_dlq = await channel.declare_queue("riskgrid.margins.dlq", durable=True)
        margins_q = await channel.declare_queue(
            "riskgrid.margins.q",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "riskgrid.events",
                "x-dead-letter-routing-key": "riskgrid.margin.dlq"
            }
        )
        await margins_q.bind(exchange, "riskgrid.margin.#")
        await margins_dlq.bind(exchange, "riskgrid.margin.dlq")
        logger.info("RabbitMQ connected and topology declared for RiskGrid.")
    except Exception as e:
        logger.warning(f"RabbitMQ initialization skipped or unavailable: {e}")
        connection, channel, exchange = None, None, None

async def publish_margin_call(routing_key: str, alert_data: Dict[str, Any]):
    global exchange
    if exchange is not None:
        try:
            message = aio_pika.Message(
                body=json.dumps(alert_data).encode("utf-8"),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json"
            )
            await exchange.publish(message, routing_key=routing_key)
        except Exception as e:
            logger.error(f"Failed to publish margin call to RabbitMQ: {e}")

async def close_rabbitmq():
    global connection
    if connection is not None and not connection.is_closed:
        await connection.close()
        connection = None
