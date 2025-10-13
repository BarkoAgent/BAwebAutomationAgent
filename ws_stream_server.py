# ws_stream_server.py
import asyncio
import logging
import os
from aiohttp import web, WSMsgType
import streaming  # import the module above

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

SEND_FPS = float(os.getenv("WS_SEND_FPS", "5.0"))  # frames/second to push to clients
SEND_INTERVAL = 1.0 / max(0.1, SEND_FPS)


async def ws_stream_handler(request):
    """
    WebSocket endpoint: /ws-stream/{run_id}
    Sends raw JPEG bytes as binary WebSocket messages.
    """
    run_id = request.match_info.get("run_id", "1")
    logging.info(f"WS connection request for run_id={run_id} from {request.remote}")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async def send_loop():
        while not ws.closed:
            frame = streaming.get_latest_frame(run_id)
            if frame:
                try:
                    await ws.send_bytes(frame)
                except Exception:
                    logging.exception(f"Failed to send frame to client for run {run_id}")
                    break
            await asyncio.sleep(SEND_INTERVAL)

    send_task = asyncio.create_task(send_loop())

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                # Optional: accept simple commands
                if msg.data == "close":
                    await ws.close()
                elif msg.data.startswith("fps:"):
                    # Optional control: change server-side send fps (per connection only)
                    try:
                        new_fps = float(msg.data.split(":", 1)[1])
                        # Not applied globally; just send a response as example
                        await ws.send_str(f"ack fps {new_fps}")
                    except Exception:
                        await ws.send_str("invalid fps value")
            elif msg.type == WSMsgType.ERROR:
                logging.error(f"WS connection closed with error: {ws.exception()}")
    except asyncio.CancelledError:
        logging.info("ws handler cancelled")
    except Exception:
        logging.exception("Unexpected error in ws_stream_handler")
    finally:
        if not send_task.done():
            send_task.cancel()
            try:
                await send_task
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass
        logging.info(f"WS connection closed for run_id={run_id} from {request.remote}")

    return ws


async def index(request):
    return web.Response(text="WS stream server. Connect to /ws-stream/{run_id}", content_type="text/plain")


def run_app(host="0.0.0.0", port=8081):
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws-stream/{run_id}", ws_stream_handler)
    logging.info(f"Starting WS stream server on http://{host}:{port}")
    web.run_app(app, host=host, port=port, handle_signals=False)


if __name__ == "__main__":
    run_app(port=int(os.getenv("WS_PORT", "8081")))
