"""Per-user live event bus.

Every device a user is signed in on subscribes here, so an approval raised by a command
typed in the terminal shows up on their phone, and the desktop activity rail sees what
the phone asked the PC to do.
"""

import queue
import threading
import time
import uuid

_subs = {}
_lock = threading.Lock()


def subscribe(user_id: int) -> queue.Queue:
    q = queue.Queue(maxsize=200)
    with _lock:
        _subs.setdefault(user_id, set()).add(q)
    return q


def unsubscribe(user_id: int, q: queue.Queue):
    with _lock:
        _subs.get(user_id, set()).discard(q)


def publish(user_id: int, event: dict):
    event.setdefault("eid", uuid.uuid4().hex[:10])
    event.setdefault("ts", time.time())
    with _lock:
        targets = list(_subs.get(user_id, ()))
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass
