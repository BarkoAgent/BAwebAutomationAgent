#!/usr/bin/env python3
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

from websocket_handler import main_connect_ws

load_dotenv()

if __name__ == "__main__":
    if not os.getenv("BACKEND_WS_URI"):
        backend_uri = input("BACKEND_WS_URI not set. Please enter BACKEND_WS_URI: ")
        if not backend_uri:
            logging.error("No BACKEND_WS_URI provided, exiting.")
            sys.exit(1)
        os.environ["BACKEND_WS_URI"] = backend_uri

    try:
        asyncio.run(main_connect_ws())
    except KeyboardInterrupt:
        logging.info("Client stopped manually.")
