import asyncio
import inspect
import json
import logging
import websockets
import os
import agent_func
import inspect

# -------------------------
# Configuration & Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

# Concurrency limit (max number of concurrent handlers). Default: 4
CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "4"))

# Global primitive for concurrency control
SEM = asyncio.Semaphore(CONCURRENCY_LIMIT)

# Build a mapping between function names and the actual implementations.
FUNCTION_MAP = {
    name: obj
    for name, obj in inspect.getmembers(agent_func, inspect.isfunction)
    if not name.startswith("_")
}

# -------------------------------------------------------------------
# Utilities: safe call for sync/coroutine functions
# -------------------------------------------------------------------
async def call_maybe_blocking(func, *args, **kwargs):
    """
    If func is an async coroutine function, await it.
    Otherwise, run the blocking sync function in a thread using asyncio.to_thread.
    """
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    return await asyncio.to_thread(func, *args, **kwargs)

# -------------------------------------------------------------------
# MESSAGE HANDLER
# -------------------------------------------------------------------
async def handle_message(message):
    """
    Processes a single incoming message and returns the response as a JSON string.
    Expected message format (JSON):
      {
        "function": "function_name",
        "args": [...],
        "kwargs": { ... }
      }
    """
    logging.debug(f"Processing received message: {message}")
    response_dict = {}
    try:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Received an empty or invalid message.")

        data = json.loads(message)
        message_id = data.get("kwargs", {}).get("_run_test_id")
        function_name = data.get("function")
        args = data.get("args", []) or []
        kwargs = data.get("kwargs", {}) or {}
        logging.info(f"Parsed data - id: {message_id}, function: {function_name}, args: {args}, kwargs: {kwargs}")

        # Prepare base response with optional id for correlation.
        response_dict = {"id": message_id} if message_id is not None else {}

        # Special handling for listing available methods.
        if function_name == "list_available_methods":
            method_details = []
            for name, func in FUNCTION_MAP.items():
                sig = inspect.signature(func)
                arg_names = [param.name for param in sig.parameters.values() if param.name != "_run_test_id"]
                method_details.append({
                    "name": name,
                    "args": arg_names,
                    "doc": func.__doc__ or ""
                })
            response_dict.update({"status": "success", "methods": method_details, "id": message_id})
            return json.dumps(response_dict)

        # If the requested function exists, call it.
        if function_name in FUNCTION_MAP:
            func = FUNCTION_MAP[function_name]
            logging.debug(f"Calling function '{function_name}' with args: {args} and kwargs: {kwargs}")

            try:
                result = await call_maybe_blocking(func, *args, **kwargs)
                response_dict.update({"status": "success", "result": result, "id": message_id})
            except Exception as e:
                logging.exception("Error while executing function")
                response_dict.update({"status": "error", "error": str(e), "id": message_id})
        else:
            response_dict.update({"status": "error", "error": f"Unknown function: {function_name}", "id": message_id})
            logging.warning(f"Function not found: {function_name}")

    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from message: {message}")
        response_dict = {"status": "error", "error": "Invalid JSON received", "id": message_id}
    except Exception as e:
        logging.exception("Error processing message")
        response_dict = {"status": "error", "error": str(e), "id": message_id}

    response_json = json.dumps(response_dict)
    logging.debug(f"Returning JSON response: {response_json}")
    return response_json

async def handle_and_send(message, ws):
    """
    Wrapper that:
      - Acquires semaphore (limits concurrency)
      - Calls handle_message
      - Sends the result back over the websocket
    """
    try:
        async with SEM:
            response_json = await handle_message(message)
            # Send the response. websockets.send is async and can be awaited concurrently.
            await ws.send(response_json)
            logging.debug(f"Sent response: {response_json}")
    except websockets.exceptions.ConnectionClosed:
        logging.warning("WebSocket closed before we could send the response.")
    except Exception:
        logging.exception("Failed in handle_and_send")

# -------------------------------------------------------------------
# WebSocket connection & receive loop (spawns background tasks)
# -------------------------------------------------------------------
async def connect_to_backend(uri):
    logging.info(f"Connecting to WebSocket backend at {uri}")
    while True:
        try:
            async with websockets.connect(uri) as ws:
                logging.info("Connection established with backend.")
                try:
                    while True:
                        message = await ws.recv()
                        logging.debug(f"Message received from backend: {message}")
                        asyncio.create_task(handle_and_send(message, ws))
                except websockets.exceptions.ConnectionClosed as e:
                    logging.error(f"Connection closed: {getattr(e, 'code', '')} {getattr(e, 'reason', '')}")
                    break
                except Exception as e:
                    logging.exception("Unexpected error during active connection")
                    try:
                        error_response = json.dumps({"status": "error", "error": f"Client-side error: {str(e)}"})
                        await ws.send(error_response)
                    except Exception:
                        logging.error("Failed to send error message before closing.")
                    break

        except (websockets.exceptions.WebSocketException, OSError) as e:
            logging.error(f"Failed to connect or connection lost: {e}")
        except Exception:
            logging.exception("Unexpected error in connection logic")
        logging.info("Attempting to reconnect in 10 seconds...")
        await asyncio.sleep(10)

async def main_connect_ws():
    backend_uri = 'wss://beta.barkoagent.com/ws/' + os.getenv("BACKEND_WS_URI", "default_client_id")
    if not backend_uri.startswith("ws://") and not backend_uri.startswith("wss://"):
        logging.error(f"Invalid BACKEND_WS_URI: {backend_uri}. It must start with ws:// or wss://")
        return

    logging.info(f"Using backend WebSocket URI: {backend_uri}")
    logging.info(f"CONCURRENCY_LIMIT={CONCURRENCY_LIMIT}")
    while True:
        await connect_to_backend(backend_uri)