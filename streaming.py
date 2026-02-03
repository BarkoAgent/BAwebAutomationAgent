# streaming.py
import base64
import threading
import time
import logging
import os
from typing import Optional
import numpy as np
import cv2

# Configuration (env-overrideable)
RECORDING_TTL_SECONDS = int(os.getenv("RECORDING_TTL_SECONDS", "300"))
MAX_ACTIVE_RECORDINGS = int(os.getenv("MAX_ACTIVE_RECORDINGS", "3"))
MAX_FRAMES_PER_RECORDING = int(os.getenv("MAX_FRAMES_PER_RECORDING", "1000"))
RECORDING_GC_INTERVAL = int(os.getenv("RECORDING_GC_INTERVAL", "30"))

# Globals
_STREAM_THREADS = {}       # run_id -> Thread
_STREAM_FLAGS = {}         # run_id -> threading.Event (stop flag)
_LATEST_FRAMES = {}        # run_id -> (jpeg_bytes, timestamp)
_RECORDING_FLAGS = set()   # set of run_ids currently recording
_RECORDED_FRAMES = {}      # run_id -> list of {seq, timestamp, data (bytes)}
_SEQ_COUNTERS = {}         # run_id -> next seq number
_ACKED_UP_TO = {}          # run_id -> last acked seq (-1 means none acked)
_LAST_FRAME_AT = {}        # run_id -> timestamp of last frame capture
_LOCK = threading.Lock()
_GC_STARTED = False

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
                    # Hard limit: max frames per recording
                    if len(_RECORDED_FRAMES.get(rec_id, [])) >= MAX_FRAMES_PER_RECORDING:
                        logging.warning(f"[LIMIT] Recording {rec_id} exceeded MAX_FRAMES_PER_RECORDING ({MAX_FRAMES_PER_RECORDING}). Auto-stopping.")
                        _RECORDING_FLAGS.discard(rec_id)
                        continue
                    
                    if rec_id not in _RECORDED_FRAMES:
                        _RECORDED_FRAMES[rec_id] = []
                        _SEQ_COUNTERS[rec_id] = 0
                    
                    seq = _SEQ_COUNTERS[rec_id]
                    _RECORDED_FRAMES[rec_id].append({
                        "seq": seq,
                        "timestamp": time.time(),
                        "data": jpeg_bytes
                    })
                    _SEQ_COUNTERS[rec_id] = seq + 1
                    _LAST_FRAME_AT[rec_id] = time.time()

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
        # Hard limit: max active recordings
        if len(_RECORDING_FLAGS) >= MAX_ACTIVE_RECORDINGS:
            logging.warning(f"[LIMIT] Cannot start recording {run_id}: MAX_ACTIVE_RECORDINGS ({MAX_ACTIVE_RECORDINGS}) reached")
            return
        
        _RECORDING_FLAGS.add(run_id)
        if run_id not in _RECORDED_FRAMES:
            _RECORDED_FRAMES[run_id] = []
        if run_id not in _SEQ_COUNTERS:
            _SEQ_COUNTERS[run_id] = 0
        if run_id not in _ACKED_UP_TO:
            _ACKED_UP_TO[run_id] = -1
        _LAST_FRAME_AT[run_id] = time.time()
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
        _RECORDING_FLAGS.discard(run_id)
        _RECORDED_FRAMES.pop(run_id, None)
        _SEQ_COUNTERS.pop(run_id, None)
        _ACKED_UP_TO.pop(run_id, None)
        _LAST_FRAME_AT.pop(run_id, None)
    logging.info(f"Cleared all state for run_id={run_id}")


# -------------------------------------------------------------------------
# TTL-Based Garbage Collection
# -------------------------------------------------------------------------

def _recording_gc():
    """Background thread that cleans up stale recordings."""
    while True:
        now = time.time()
        with _LOCK:
            for run_id in list(_RECORDING_FLAGS):
                last_activity = _LAST_FRAME_AT.get(run_id, 0)
                if now - last_activity > RECORDING_TTL_SECONDS:
                    _RECORDING_FLAGS.discard(run_id)
                    _RECORDED_FRAMES.pop(run_id, None)
                    _SEQ_COUNTERS.pop(run_id, None)
                    _ACKED_UP_TO.pop(run_id, None)
                    _LAST_FRAME_AT.pop(run_id, None)
                    logging.warning(f"[GC] Cleaned up inactive recording run_id={run_id} (TTL expired)")
        time.sleep(RECORDING_GC_INTERVAL)


def _start_gc_once():
    global _GC_STARTED
    if not _GC_STARTED:
        _GC_STARTED = True
        threading.Thread(target=_recording_gc, daemon=True, name="recording-gc").start()
        logging.info("Recording GC thread started")


_start_gc_once()

def _start_frame_recording(_run_test_id='1') -> str:
    """
    [SYSTEM] Starts persisting (recording) frames for the given run_id.
    Not exposed to users - called by backend only.
    """
    start_recording(_run_test_id)
    return "recording started"


def _stop_frame_recording(_run_test_id='1') -> str:
    """
    [SYSTEM] Stops persisting (recording) frames for the given run_id.
    Not exposed to users - called by backend only.
    """
    stop_recording(_run_test_id)
    return "recording stopped"


def _get_recorded_frames(_run_test_id='1', since_seq: int = 0, limit: int = 50):
    """
    [SYSTEM] Retrieves persisted frames with cursor-based pagination.
    Not exposed to users - called by backend only.
    
    Args:
        _run_test_id: Execution identifier
        since_seq: Return only frames with seq >= since_seq (default: 0)
        limit: Max frames to return (default: 50)
    
    Returns:
        List of dicts: [{'seq': int, 'timestamp': float, 'data': base64_str}, ...]
    """
    try:
        if since_seq == "":
            since_seq = 0
        else:
            since_seq = int(since_seq)
            
        if limit == "":
            limit = 50
        else:
            limit = int(limit)
    except (ValueError, TypeError):
        since_seq = 0
        limit = 50

    frames = get_recorded_frames(_run_test_id, since_seq=since_seq, limit=limit)
    
    result = []
    for frame in frames:
        b64_str = base64.b64encode(frame["data"]).decode('utf-8')
        result.append({
            'seq': frame["seq"],
            'timestamp': frame["timestamp"],
            'data': b64_str
        })
    
    return result


def _ack_recorded_frames(_run_test_id='1', up_to_seq: int = 0) -> str:
    """
    [SYSTEM] Acknowledge frames up to and including up_to_seq.
    ACK'd frames are freed from memory and will not be returned in future calls.
    Not exposed to users - called by backend only.
    
    Args:
        _run_test_id: Execution identifier
        up_to_seq: ACK all frames with seq <= up_to_seq
    """
    try:
        if up_to_seq == "":
            up_to_seq = 0
        else:
            up_to_seq = int(up_to_seq)
    except (ValueError, TypeError):
        up_to_seq = 0

    ack_recorded_frames(_run_test_id, up_to_seq=up_to_seq)
    return "frames acknowledged"


def _clear_frame_recording(_run_test_id='1') -> str:
    """
    [SYSTEM] Clears all persisted frames and resets seq counter for the given run_id.
    Not exposed to users - called by backend only.
    """
    clear_recorded_frames(_run_test_id)
    return "recording cleared"
