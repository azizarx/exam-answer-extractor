"""Run with python -m backend.queue.dispatcher; restart-safe outbox/reaper."""
import logging
import signal
import threading
from backend.queue.runtime import dispatch_once

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    stopping=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopping.set())
    signal.signal(signal.SIGINT,lambda *_:stopping.set())
    while not stopping.is_set():
        try:
            dispatch_once()
        except Exception:
            logging.exception('Queue dispatch unavailable; database jobs retained')
        stopping.wait(2)
