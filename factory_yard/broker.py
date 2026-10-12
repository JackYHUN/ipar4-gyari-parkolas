"""Loopback-only MQTT 3.1.1 broker; replaceable with external Mosquitto."""
import asyncio
import logging
from amqtt.broker import Broker


async def serve(host, port):
    logging.basicConfig(level=logging.WARNING)
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"{host}:{port}"}},
                     "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
    await broker.start()
    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()
