#!/usr/bin/env python3
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

from websocket_handler import main_connect_ws, stream_latest_frames
from streaming import get_latest_frame

load_dotenv()

async def start_streaming_loop():
    backend_uri = 'wss://beta.barkoagent.com/ws/' + os.getenv("BACKEND_WS_URI", "default_client_id")
    run_id = "1"
    await stream_latest_frames(
        ws_uri=backend_uri,
        run_id=run_id,
        get_latest_frame=get_latest_frame,
        interval=1,  # Adjust as needed
        send_start_end_control=True,
        retry_connect_delay=5.0,
        max_idle_seconds=None,
        use_hash_dedup=True
    )

async def main():
    await asyncio.gather(
        main_connect_ws(),
        start_streaming_loop()
    )

if __name__ == "__main__":
    if not os.getenv("BACKEND_WS_URI"):
        backend_uri = input("BACKEND_WS_URI not set. Please enter BACKEND_WS_URI: ")
        if not backend_uri:
            logging.error("No BACKEND_WS_URI provided, exiting.")
            sys.exit(1)
        os.environ["BACKEND_WS_URI"] = backend_uri

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Client stopped manually.")
