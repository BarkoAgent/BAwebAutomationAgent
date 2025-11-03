#!/usr/bin/env python3
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

from websocket_handler import main_connect_ws, stream_latest_frames
from streaming import get_latest_frame

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)


async def start_streaming_loop(backend_ws_uri: str):
    """
    Screenshot streaming loop.
    The streaming endpoint is the same backend URI + '/1'.
    """
    if not backend_ws_uri:
        logging.error("Backend URI missing, cannot start streaming loop.")
        return

    if not (backend_ws_uri.startswith("ws://") or backend_ws_uri.startswith("wss://")):
        logging.error(f"Invalid backend WebSocket URI: {backend_ws_uri}")
        return

    # Append /1 for streaming endpoint
    streaming_uri = backend_ws_uri.rstrip("/") + "/1"
    run_id = os.getenv("STREAMING_RUN_ID", "1")

    logging.info(f"Starting screenshot stream to: {streaming_uri}")

    while True:
        await stream_latest_frames(
            ws_uri=streaming_uri,
            run_id=run_id,
            get_latest_frame=get_latest_frame,
            interval=float(os.getenv("STREAMING_INTERVAL", "1.0")),
            send_start_end_control=True,
            retry_connect_delay=5.0,
            max_idle_seconds=None,
            use_hash_dedup=True
        )


async def main():
    """
    Main entry point - runs command/control connection and optional streaming.
    """
    backend_ws_uri = os.getenv("BACKEND_WS_URI")

    if not backend_ws_uri:
        logging.error("BACKEND_WS_URI not set, cannot start backend connection.")
        sys.exit(1)

    enable_streaming = os.getenv("ENABLE_STREAMING", "true").lower() in ("1", "true", "yes")

    if enable_streaming:
        logging.info("Screenshot streaming enabled.")
        await asyncio.gather(
            main_connect_ws(),
            # start_streaming_loop(backend_ws_uri)
        )
    else:
        logging.info("Starting in command/control mode only.")
        await main_connect_ws()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Client stopped manually.")