"""In-memory background job registry, polled by the frontend."""
import threading
import traceback
import uuid
from collections import deque

from . import languages

JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
_TRANSCRIPTIONS = deque()
_worker_running = False


class JobError(Exception):
    """Expected job failure with a browser-translatable message."""

    def __init__(self, key: str, msg: str, args: tuple | list = ()):
        super().__init__(msg)
        self.key = key
        self.msg_args = list(args)


def running_with_label(label: str) -> str | None:
    with _LOCK:
        for jid, j in JOBS.items():
            if j["status"] in ("queued", "running") and j["label"] == label:
                return jid
    return None


def _create(label, status, **metadata):
    # Never evict active jobs, including queued transcriptions.
    if len(JOBS) > 100:
        done = [k for k, j in JOBS.items() if j["status"] in ("done", "error")]
        for k in done[:len(JOBS) - 100]:
            del JOBS[k]
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"status": status, "progress": 0.0, "label": label,
                 "message": "", "key": "", "args": [], "result": None,
                 **metadata}
    return jid


def _run(jid, target, args=()):
    try:
        result = target(jid, *args)
        with _LOCK:
            JOBS[jid].update(result=result, status="done", progress=1.0)
    except Exception as e:  # surfaced to UI; does not stop the queue
        traceback.print_exc()
        with _LOCK:
            JOBS[jid].update(status="error", message=str(e),
                             key=getattr(e, "key", ""),
                             args=getattr(e, "msg_args", []))


def start(target, *args, label="") -> str:
    with _LOCK:
        jid = _create(label, "running")
    threading.Thread(target=_run, args=(jid, target, args), daemon=True).start()
    return jid


def start_transcription(target, *, session_id, title, model, language):
    """Atomically deduplicate and enqueue; other job types stay independent."""
    global _worker_running
    with _LOCK:
        existing = running_with_label(f"transcribe:{session_id}")
        if existing:
            return existing, True
        jid = _create(f"transcribe:{session_id}", "queued", kind="transcription",
                      session_id=session_id, title=title, model=model,
                      language=language)
        _TRANSCRIPTIONS.append((jid, target))
        if not _worker_running:
            _worker_running = True
            threading.Thread(target=_drain_transcriptions, daemon=True).start()
        return jid, False


def _drain_transcriptions():
    global _worker_running
    while True:
        with _LOCK:
            if not _TRANSCRIPTIONS:
                _worker_running = False
                return
            jid, target = _TRANSCRIPTIONS.popleft()
            JOBS[jid]["status"] = "running"
        with languages.using_language(JOBS[jid]["language"]):
            _run(jid, target)


def set_progress(jid: str, p: float, message: str = "", key: str = "",
                 args: tuple | list = ()):
    with _LOCK:
        if jid in JOBS:
            JOBS[jid]["progress"] = round(p, 3)
            if message or key:
                JOBS[jid]["message"] = message
                JOBS[jid]["key"] = key
                JOBS[jid]["args"] = list(args)


def set_message(jid: str, message: str, key: str = "",
                args: tuple | list = ()):
    with _LOCK:
        if jid in JOBS and (message or key):
            JOBS[jid]["message"] = message
            JOBS[jid]["key"] = key
            JOBS[jid]["args"] = list(args)


def transcriptions(language: str) -> list[dict]:
    """Newest submissions first; queue positions refer to the global FIFO."""
    with _LOCK:
        positions = {jid: index for index, (jid, _) in
                     enumerate(_TRANSCRIPTIONS, 1)}
        selected = []
        completed = 0
        for jid, j in reversed(JOBS.items()):
            if j.get("kind") != "transcription" or j["language"] != language:
                continue
            if j["status"] in ("done", "error"):
                completed += 1
                if completed > 50:
                    continue
            selected.append(
                {"id": jid, "status": j["status"], "progress": j["progress"],
                 "message": j["message"], "message_key": j["key"],
                 "message_args": list(j["args"]), "result": j["result"],
                 "error": j["message"] if j["status"] == "error" else None,
                 "session_id": j["session_id"], "title": j["title"],
                 "model": j["model"], "queue_position": positions.get(jid)})
        return selected


def get(jid: str) -> dict | None:
    with _LOCK:
        job = JOBS.get(jid)
        return dict(job) if job is not None else None
