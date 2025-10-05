# streaming.py
import threading
import time
import logging
from typing import Optional
import numpy as np
import cv2

# Globals
_STREAM_THREADS = {}       # run_id -> Thread
_STREAM_FLAGS = {}         # run_id -> threading.Event (stop flag)
_LATEST_FRAMES = {}        # run_id -> (jpeg_bytes, timestamp)
_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')


def _png_to_jpeg_bytes(png_bytes: bytes, quality: int = 80) -> bytes:
    """
    Convert PNG bytes (Selenium's get_screenshot_as_png) to JPEG bytes.
    """
    nparr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode image bytes")
    ok, enc = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("Failed to encode JPEG")
    return enc.tobytes()


def _stream_worker(run_id: str, driver, fps: float, jpeg_quality: int, stop_event: threading.Event):
    """
    Worker thread: captures screenshots, converts to jpeg, stores latest bytes.
    """
    interval = 1.0 / max(0.1, fps)
    logging.info(f"Stream worker started for run_id={run_id} fps={fps}")

    try:
        while not stop_event.is_set():
            try:
                png_bytes = driver.get_driver().get_screenshot_as_png()
            except Exception:
                logging.exception(f"Failed to capture screenshot for run {run_id}")
                stop_event.wait(interval)
                stop_stream(run_id)
                continue

            try:
                jpeg_bytes = _png_to_jpeg_bytes(png_bytes, quality=jpeg_quality)
            except Exception:
                logging.exception(f"PNG->JPEG conversion failed for run {run_id}; storing PNG as fallback")
                jpeg_bytes = png_bytes

            with _LOCK:
                logging.info(f"Captured frame for run_id={run_id}, size={len(jpeg_bytes)} bytes")
                _LATEST_FRAMES[run_id] = (jpeg_bytes, time.time())

            stop_event.wait(interval)
    except Exception:
        logging.exception("Unexpected exception in stream worker")
    finally:
        logging.info(f"Stream worker exiting for run_id={run_id}")


def start_stream(driver, run_id: str = "1", fps: float = 1.0, jpeg_quality: int = 70) -> None:
    """
    Start background thread capturing screenshots from `driver` for `run_id`.
    No-op if already running.
    """
    with _LOCK:
        thread = _STREAM_THREADS.get(run_id)
        if thread and thread.is_alive():
            logging.info(f"Stream already running for run_id={run_id}")
            return

        stop_event = threading.Event()
        thread = threading.Thread(
            target=_stream_worker,
            args=(run_id, driver, fps, jpeg_quality, stop_event),
            daemon=True,
            name=f"stream-{run_id}"
        )
        _STREAM_THREADS[run_id] = thread
        _STREAM_FLAGS[run_id] = stop_event
        thread.start()
        logging.info(f"Started streaming thread for run_id={run_id}")


def stop_stream(run_id: str) -> None:
    """
    Stop stream thread for run_id and cleanup.
    """
    with _LOCK:
        stop_event = _STREAM_FLAGS.get(run_id)
        thread = _STREAM_THREADS.get(run_id)

    if stop_event:
        stop_event.set()
    if thread:
        thread.join(timeout=2.0)

    with _LOCK:
        _STREAM_FLAGS.pop(run_id, None)
        _STREAM_THREADS.pop(run_id, None)
        _LATEST_FRAMES.pop(run_id, None)

    logging.info(f"Stopped stream for run_id={run_id}")


def get_latest_frame(run_id: str) -> Optional[bytes]:
    """
    Return latest frame bytes for run_id (JPEG bytes preferably), or None.
    """
    with _LOCK:
        item = _LATEST_FRAMES.get(run_id)
        if item is None:
            return None
        return item[0]
