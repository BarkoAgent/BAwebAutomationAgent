#!/usr/bin/env python3
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
import certifi

os.environ.setdefault('SSL_CERT_FILE', certifi.where())
os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
import agent_func
from ba_ws_sdk import main_connect_ws

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

async def main():
    """
    Entry point: initializes WebSocket connection and handles optional streaming.
    Actual behavior depends on environment variables:

    - AGENT_CONNECTION_TYPE:
        'manager' -> multiplexed single socket (for Agent Manager)
        'direct'  -> dual sockets (direct-to-app)
    - ENABLE_STREAMING:
        'true'/'1' to enable frame streaming
    """
    backend_ws_uri = os.getenv("BACKEND_WS_URI")
    if not backend_ws_uri:
        logging.error("BACKEND_WS_URI not set. Cannot start backend connection.")
        sys.exit(1)

    connection_type = os.getenv("AGENT_CONNECTION_TYPE", "manager").lower()
    enable_streaming = os.getenv("ENABLE_STREAMING", "true").lower() in ("1", "true", "yes")

    logging.info(f"Starting agent with connection type: {connection_type.upper()}")
    if enable_streaming:
        logging.info("Streaming is enabled via environment settings.")
    else:
        logging.info("Streaming is disabled.")

    try:
        await main_connect_ws(agent_func)
    except Exception as e:
        logging.exception(f"Agent encountered an error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Client stopped manually.")
