import asyncio
import inspect
import json
import logging
import time
import websockets
import os
import agent_func
import struct
import hashlib

from typing import Optional, Callable
from dotenv import load_dotenv

from streaming import get_latest_frame

load_dotenv()

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

def _make_envelope(header: dict, payload_bytes: bytes) -> bytes:
    """
    Envelope format:
      [4 bytes big-endian header_len][header_json_bytes][payload_bytes]

    header is a small JSON dict (e.g. {"id": "<run_id>", "type": "screenshot", "seq": 1})
    payload_bytes is the raw PNG bytes.
    """
    header_json = json.dumps(header, separators=(",", ":" )).encode("utf-8")
    header_len = len(header_json)
    return struct.pack(">I", header_len) + header_json + payload_bytes

async def stream_latest_frames(
    ws_uri: str,
    run_id: str,
    get_latest_frame: Callable[[str], Optional[bytes]],
    interval: float = 0.5,
    send_start_end_control: bool = True,
    retry_connect_delay: float = 5.0,
    max_idle_seconds: Optional[float] = None,
    use_hash_dedup: bool = True,
):
    """
    Connects to ws_uri and streams frames produced by get_latest_frame(run_id).

    Parameters:
    - ws_uri: websocket URI to connect to
    - run_id: identifier the server uses to route frames
    - get_latest_frame: callable(run_id) -> Optional[bytes]
        Should return raw PNG bytes (or None if no new frame).
    - interval: poll interval (seconds)
    - send_start_end_control: whether to send text control messages before/after stream
    - retry_connect_delay: delay before reconnecting after failure
    - max_idle_seconds: if set, stop streaming after this many seconds without any frames
    - use_hash_dedup: if True, uses SHA256 hash to avoid sending duplicate frames

    Behavior:
    - The function runs until:
      - the entire png iterator ends (if get_latest_frame eventually returns None forever and you set max_idle_seconds),
      - or an exception occurs that cannot be recovered,
      - or you cancel the task externally.
    """
    last_hash = None
    last_sent_ts = None

    while True:
        try:
            async with websockets.connect(ws_uri) as ws:
                logging.info(f"Connected for frame streaming: {ws_uri}")

                # Main streaming loop - DO NOT EXIT unless error/timeout
                while True:
                    try:
                        frame_bytes = await asyncio.get_running_loop().run_in_executor(
                            None, lambda: get_latest_frame(run_id)
                        )
                    except Exception:
                        logging.exception("get_latest_frame error")
                        frame_bytes = None

                    now = asyncio.get_event_loop().time()

                    if frame_bytes:
                        send_frame = True
                        if use_hash_dedup:
                            h = hashlib.sha256(frame_bytes).hexdigest()
                            if h == last_hash:
                                send_frame = False
                            else:
                                last_hash = h
                                last_sent_ts = now
                        else:
                            last_sent_ts = now

                        if send_frame:
                            seq = int(last_sent_ts or now)
                            header = {"id": run_id, "type": "screenshot", "seq": seq}
                            envelope = _make_envelope(header, frame_bytes)
                            try:
                                await ws.send(envelope)
                                logging.info(f"Sent frame seq={seq}")
                                last_sent_ts = now
                            except websockets.exceptions.ConnectionClosed:
                                logging.warning("WebSocket closed while sending frame; breaking to reconnect")
                                break
                            except Exception:
                                logging.exception("Failed to send frame; will break and reconnect")
                                break
                        # else: duplicate frame, skip sending

                    else:
                        # No frame available this iteration
                        if max_idle_seconds is not None:
                            idle = now - (last_sent_ts or 0)
                            if idle >= max_idle_seconds:
                                logging.info(f"Idle timeout {idle}s, ending stream")
                                return  # Intentional exit

                    await asyncio.sleep(interval)
                    # ← NO RETURN HERE

        except (websockets.exceptions.WebSocketException, OSError) as e:
            logging.error(f"WebSocket error / connection failed: {e}. Retrying in {retry_connect_delay}s...")
            await asyncio.sleep(retry_connect_delay)
        except asyncio.CancelledError:
            logging.info("stream_latest_frames cancelled")
            return
        except Exception:
            logging.exception("Unexpected error in stream_latest_frames")
            await asyncio.sleep(retry_connect_delay)

# -------------------------------------------------------------------
# Utilities: safe call for sync/coroutine functions
# -------------------------------------------------------------------
async def call_maybe_blocking(func, *args, **kwargs):
    """
    Execute function - await if coroutine, otherwise run in thread.
    """
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    return await asyncio.to_thread(func, *args, **kwargs)

# In websocket_handler.py (both agents need this fix)

async def handle_message(message):
    logging.debug(f"Processing received message: {message}")
    response_dict = {}
    message_id = None
    try:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Received an empty or invalid message.")

        data = json.loads(message)
        
        message_id = data.get("id") or data.get("kwargs", {}).get("_run_test_id")
        
        function_name = data.get("function")
        args = data.get("args", []) or []
        kwargs = data.get("kwargs", {}) or {}

        logging.info(
            f"Parsed - id:{message_id}, function:{function_name}, "
            f"args:{args}, kwargs:{kwargs}"
        )

        # Always include ID in response if present
        response_dict = {"id": message_id} if message_id else {}

        if function_name == "list_available_methods":
            method_details = []
            for name, func in FUNCTION_MAP.items():
                sig = inspect.signature(func)
                arg_names = [
                    p.name for p in sig.parameters.values()
                    if p.name != "_run_test_id"
                ]
                method_details.append({
                    "name": name,
                    "args": arg_names,
                    "doc": func.__doc__ or ""
                })
            response_dict.update({
                "status": "success",
                "methods": method_details
            })
            return json.dumps(response_dict)

        if function_name in FUNCTION_MAP:
            func = FUNCTION_MAP[function_name]
            try:
                result = await call_maybe_blocking(func, *args, **kwargs)
                response_dict.update({
                    "status": "success",
                    "result": result
                })
            except Exception as e:
                logging.exception("Error while executing function")
                response_dict.update({
                    "status": "error",
                    "error": str(e)
                })
        else:
            response_dict.update({
                "status": "error",
                "error": f"Unknown function: {function_name}"
            })
            logging.warning(f"Function not found: {function_name}")

    except json.JSONDecodeError:
        logging.error("Failed to decode JSON message")
        response_dict = {
            "status": "error",
            "error": "Invalid JSON",
            "id": message_id
        }
    except Exception as e:
        logging.exception("Message handler failure")
        response_dict = {
            "status": "error",
            "error": str(e),
            "id": message_id
        }

    response_json = json.dumps(response_dict)
    logging.debug(f"Response: {response_json}")
    return response_json

async def handle_and_send(message, ws):
    """
    Process message with concurrency control and send response.
    """
    try:
        async with SEM:
            response_json = await handle_message(message)
            await ws.send(response_json)
            logging.debug(f"Sent: {response_json}")
    except websockets.exceptions.ConnectionClosed:
        logging.warning("WebSocket closed before response sent")
    except Exception:
        logging.exception("handle_and_send error")

async def connect_to_backend(uri):
    """Maintain single connection for commands AND streaming."""
    logging.info(f"Connecting to: {uri}")
    
    while True:
        try:
            async with websockets.connect(uri) as ws:
                logging.info("Connected - starting command handler and streaming")
                
                # Start both tasks on SAME websocket
                enable_streaming = os.getenv("ENABLE_STREAMING", "true").lower() in ("1", "true", "yes")
                
                tasks = [asyncio.create_task(command_loop(ws))]
                
                if enable_streaming:
                    tasks.append(asyncio.create_task(streaming_loop(ws)))
                
                # Wait for any task to fail
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_EXCEPTION
                )
                
                # Cancel remaining
                for task in pending:
                    task.cancel()
                    
        except Exception as e:
            logging.error(f"Connection failed: {e}")
        
        await asyncio.sleep(10)

async def command_loop(ws):
    """Handle command/response messages."""
    while True:
        message = await ws.recv()
        asyncio.create_task(handle_and_send(message, ws))

async def streaming_loop(ws):
    """Send screenshot frames on same connection."""
    run_id = os.getenv("STREAMING_RUN_ID", "1")
    interval = float(os.getenv("STREAMING_INTERVAL", "1.0"))
    
    last_hash = None
    
    while True:
        try:
            frame = await asyncio.get_running_loop().run_in_executor(
                None, lambda: get_latest_frame(run_id)
            )
            
            if frame:
                h = hashlib.sha256(frame).hexdigest()
                if h != last_hash:
                    header = {"id": run_id, "type": "screenshot", "seq": int(time.time())}
                    envelope = _make_envelope(header, frame)
                    await ws.send(envelope)  # Binary send on same WS
                    last_hash = h
            
            await asyncio.sleep(interval)
            
        except Exception as e:
            logging.exception("Streaming error")
            break

async def main_connect_ws():
    """Entry point - single call to connect_to_backend."""
    backend_ws_uri = os.getenv("BACKEND_WS_URI", "default_client_id")
    default_base = os.getenv("DEFAULT_WS_BASE", "wss://beta.barkoagent.com/ws/")
    
    full_uri = _build_uri(backend_ws_uri, default_base)
    
    if not full_uri.startswith("ws://") and not full_uri.startswith("wss://"):
        logging.error(f"Invalid WebSocket URI: {full_uri}")
        return

    logging.info(f"Backend URI: {full_uri}")
    logging.info(f"Concurrency limit: {CONCURRENCY_LIMIT}")
    
    # Single call - connect_to_backend handles retries internally
    await connect_to_backend(full_uri)

def _build_uri(base_or_id: str, default_base: str) -> str:
    """
    Build full WebSocket URI with backward compatibility.
    
    If base_or_id is a full URI (starts with ws:// or wss://), use as-is.
    Otherwise, treat as client ID and append to default_base.
    """
    if base_or_id.startswith("ws://") or base_or_id.startswith("wss://"):
        return base_or_id
    
    # Backward compatibility: treat as client ID
    base = default_base.rstrip("/")
    client_id = base_or_id.lstrip("/")
    return f"{base}/{client_id}"

