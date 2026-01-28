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
_RECORDING_FLAGS = set()   # set of run_ids currently recording
_RECORDED_FRAMES = {}      # run_id -> list of {seq, timestamp, data (bytes)}
_SEQ_COUNTERS = {}         # run_id -> next seq number
_ACKED_UP_TO = {}          # run_id -> last acked seq (-1 means none acked)
_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logging.getLogger("urllib3").setLevel(logging.ERROR)


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


def _stream_worker(run_id: str, driver, fps: float, jpeg_quality: int, stop_event: threading.Event, stop_timeout: Optional[float] = None):
    """
    Worker thread: captures screenshots, converts to jpeg, stores latest bytes.
    """
    interval = 1.0 / max(0.1, fps)
    logging.info(f"Stream worker started for run_id={run_id} fps={fps}")
    time_started = time.time()
    try:
        while not stop_event.is_set() and (stop_timeout is None or (time.time() - time_started) < stop_timeout):
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
                _LATEST_FRAMES[run_id] = (jpeg_bytes, time.time())
                
                # Store frames under ALL active recording IDs with seq numbers
                for rec_id in list(_RECORDING_FLAGS):
                    if rec_id not in _RECORDED_FRAMES:
                        _RECORDED_FRAMES[rec_id] = []
                        _SEQ_COUNTERS[rec_id] = 0
                    
                    seq = _SEQ_COUNTERS[rec_id]
                    _RECORDED_FRAMES[rec_id].append({
                        "seq": seq,
                        "timestamp": time.time(),
                        "data": jpeg_bytes  # stored as bytes, encoded to base64 in agent_func
                    })
                    _SEQ_COUNTERS[rec_id] = seq + 1

            stop_event.wait(interval)
    except Exception:
        logging.exception("Unexpected exception in stream worker")
    finally:
        logging.info(f"Stream worker exiting for run_id={run_id}")


def start_stream(driver, run_id: str = "1", fps: float = 1.0, jpeg_quality: int = 70, stop_timeout: Optional[float] = 180.0) -> None:
    """
    Start background thread capturing screenshots from `driver` for `run_id`.
    No-op if already running. If an existing thread is found, signal it to stop and wait
    up to `stop_timeout` seconds for it to exit before starting a new one.
    """
    with _LOCK:
        thread = _STREAM_THREADS.get(run_id)
        if thread and thread.is_alive():
            logging.info(f"Stream already running for run_id={run_id}. Stopping (timeout={stop_timeout}).")
            # call stop_stream which will join up to timeout
            # drop the lock before blocking inside stop_stream (stop_stream handles locking)

    with _LOCK:
        # Double-check: maybe another caller started a thread meanwhile
        thread = _STREAM_THREADS.get(run_id)
        if thread and thread.is_alive():
            logging.warning(f"Unable to start new stream for run_id={run_id} because an existing thread is still alive")
            return

        stop_event = threading.Event()
        thread = threading.Thread(
            target=_stream_worker,
            args=(run_id, driver, fps, jpeg_quality, stop_event, stop_timeout),
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
        # We generally do NOT clear recorded frames here,
        # so they can be retrieved after driver stops.
        # user can call clear_recorded_frames explicitly.

    logging.info(f"Stopped stream for run_id={run_id}")


def get_latest_frame(run_id: str) -> Optional[bytes]:
    """
    Return latest frame bytes for run_id (JPEG bytes preferably), or None.
    """
    with _LOCK:
        item = _LATEST_FRAMES.get(run_id)
        # _LATEST_FRAMES.pop(run_id, None) # Don't pop if you want to reuse it?
        # Original code popped it. Sticking to original behavior for streaming.
        if item is None:
            return None
        return item[0]


# -------------------------------------------------------------------------
# Recording / Persistence Logic
# -------------------------------------------------------------------------

def start_recording(run_id: str) -> None:
    """
    Enable recording (persistence) of frames for this run_id.
    """
    with _LOCK:
        _RECORDING_FLAGS.add(run_id)
        if run_id not in _RECORDED_FRAMES:
            _RECORDED_FRAMES[run_id] = []
        if run_id not in _SEQ_COUNTERS:
            _SEQ_COUNTERS[run_id] = 0
        if run_id not in _ACKED_UP_TO:
            _ACKED_UP_TO[run_id] = -1
    logging.info(f"Started recording frames for run_id={run_id}")

def stop_recording(run_id: str) -> None:
    """
    Disable recording of frames for this run_id.
    """
    with _LOCK:
        _RECORDING_FLAGS.discard(run_id)
    logging.info(f"Stopped recording frames for run_id={run_id}")

def get_recorded_frames(run_id: str, since_seq: int = 0, limit: int = 50):
    """
    Return frames where seq >= since_seq and seq > last_acked.
    Returns at most `limit` frames, ordered by seq ascending.
    """
    with _LOCK:
        frames = _RECORDED_FRAMES.get(run_id, [])
        acked = _ACKED_UP_TO.get(run_id, -1)
        
        # Filter: seq > acked AND seq >= since_seq
        filtered = [
            f for f in frames 
            if f["seq"] > acked and f["seq"] >= since_seq
        ]
        
        # Sort by seq and limit
        filtered.sort(key=lambda f: f["seq"])
        return filtered[:limit]


def ack_recorded_frames(run_id: str, up_to_seq: int) -> None:
    """
    Acknowledge frames up to and including up_to_seq.
    These frames will be excluded from future get_recorded_frames calls
    and can be garbage collected.
    """
    with _LOCK:
        _ACKED_UP_TO[run_id] = max(
            _ACKED_UP_TO.get(run_id, -1),
            up_to_seq
        )
        
        # Actually remove acked frames to free memory
        if run_id in _RECORDED_FRAMES:
            _RECORDED_FRAMES[run_id] = [
                f for f in _RECORDED_FRAMES[run_id]
                if f["seq"] > up_to_seq
            ]
    
    logging.info(f"ACK'd frames up to seq={up_to_seq} for run_id={run_id}")


def clear_recorded_frames(run_id: str) -> None:
    """
    Clear all recorded frames and reset seq counter for the given run_id.
    """
    with _LOCK:
        _RECORDED_FRAMES.pop(run_id, None)
        _SEQ_COUNTERS.pop(run_id, None)
        _ACKED_UP_TO.pop(run_id, None)
    logging.info(f"Cleared recorded frames for run_id={run_id}")
