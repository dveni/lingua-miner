"""Deterministic queue tests: events control work, never real Whisper."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import jobs


@pytest.fixture
def api(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "CON", main.db.connect(tmp_path / "queue.db"))
    monkeypatch.setattr(jobs, "JOBS", {})
    return TestClient(main.app)


def session(title, language="ca"):
    import app.main as main
    return main.db.create_session(
        main.CON, title=title, language=language, source_type="local",
        media_path=f"/private/media/{title}.mp4", srt_source="none",
        model_size="-", duration_secs=30, transcript_json="[]")


def wait_done(jid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = jobs.get(jid)
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.005)
    raise AssertionError(f"job {jid} did not finish")


def enqueue(target, sid, language="ca"):
    return jobs.start_transcription(target, session_id=sid, title=f"Title {sid}",
                                    model="small", language=language)


def test_transcriptions_run_fifo_without_blocking_other_jobs():
    release = threading.Event()
    started = threading.Event()
    order = []
    ids = []

    def first(jid):
        order.append("first")
        started.set()
        assert release.wait(5)

    try:
        jid, duplicate = enqueue(first, "fifo-first")
        ids.append(jid)
        assert duplicate is False
        assert started.wait(2)
        for sid in ("second", "third"):
            jid, _ = enqueue(lambda jid, sid=sid: order.append(sid), f"fifo-{sid}")
            ids.append(jid)
            assert jobs.get(jid)["status"] == "queued"
        independent = jobs.start(lambda jid: "independent", label="download")
        assert wait_done(independent)["result"] == "independent"
        assert order == ["first"]
    finally:
        release.set()
        for jid in ids:
            wait_done(jid)
    assert order == ["first", "second", "third"]


def test_same_session_is_deduplicated_atomically_while_queued_and_running():
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    started = threading.Event()
    ids = []
    calls = []

    def blocker(jid):
        started.set()
        assert release.wait(5)

    try:
        blocker_id, _ = enqueue(blocker, "dedup-blocker")
        ids.append(blocker_id)
        assert started.wait(2)
        assert enqueue(blocker, "dedup-blocker") == (blocker_id, True)
        barrier = threading.Barrier(12)

        def submit(_):
            barrier.wait(timeout=3)
            return enqueue(lambda jid: calls.append(jid), "dedup-session")

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(submit, range(12)))
        ids.extend(jid for jid, _ in results)
        assert len(set(ids[1:])) == 1
        assert sum(not duplicate for _, duplicate in results) == 1
        assert jobs.get(ids[1])["status"] == "queued"
    finally:
        release.set()
        for jid in ids:
            wait_done(jid)
    assert calls == [ids[1]]
    # A terminal job does not prevent a deliberate retry.
    retry_id, duplicate = enqueue(lambda jid: None, "dedup-session")
    assert duplicate is False
    assert retry_id != ids[1]
    wait_done(retry_id)


def test_api_queue_metadata_error_recovery_and_language_scope(api, monkeypatch):
    from app import transcribe

    release = threading.Event()
    started = threading.Event()
    ids = []
    first = session("First")
    second = session("Second")
    third = session("Third", "de")

    def work(jid, path, model, duration):
        assert model == "small"
        assert duration == 30
        if "First" in path:
            jobs.set_progress(jid, .25, "Working", key="job.transcribing", args=[1])
            started.set()
            assert release.wait(5)
            raise jobs.JobError("err.test", "Expected failure", ("detail",))
        return []

    monkeypatch.setattr(transcribe, "transcribe", work)
    try:
        for sid in (first, second, third):
            response = api.post(f"/api/sessions/{sid}/transcribe", json={"model": "small"})
            assert response.status_code == 200
            ids.append(response.json()["job_id"])
            if sid == first:
                assert started.wait(2)
        duplicate = api.post(f"/api/sessions/{second}/transcribe", json={"model": "small"})
        assert duplicate.json() == {"job_id": ids[1], "already_running": True}
        ordinary = jobs.start(lambda jid: None, label="youtube")
        wait_done(ordinary)
        response = api.get("/api/transcriptions")
        assert response.status_code == 200
        listed = response.json()["jobs"]
        assert [j["id"] for j in listed] == [ids[1], ids[0]]
        assert listed[0] == {
            "id": ids[1], "status": "queued", "progress": 0.0,
            "message": "", "message_key": "", "message_args": [],
            "result": None, "error": None, "session_id": second,
            "title": "Second", "model": "small", "queue_position": 1,
        }
        assert listed[1]["status"] == "running"
        assert listed[1]["queue_position"] is None
        assert listed[1]["progress"] == .25
        assert listed[1]["message_key"] == "job.transcribing"
        assert listed[1]["message_args"] == [1]
        assert "/private/" not in response.text
        legacy = api.get(f"/api/jobs/{ids[0]}").json()
        assert legacy["key"] == "job.transcribing"
        assert legacy["args"] == [1]
        assert api.get(f"/api/jobs/{ids[1]}").json()["status"] == "queued"
        api.post("/api/settings", json={"language": "de"})
        german = api.get("/api/transcriptions").json()["jobs"]
        assert [j["id"] for j in german] == [ids[2]]
        assert german[0]["queue_position"] == 2  # global FIFO position
    finally:
        release.set()
        for jid in ids:
            wait_done(jid)
    api.post("/api/settings", json={"language": "ca"})
    listed = api.get("/api/transcriptions").json()["jobs"]
    assert [j["status"] for j in listed] == ["done", "error"]
    assert listed[0]["result"] == {"segments": 0}
    assert listed[0]["progress"] == 1.0
    assert listed[1]["error"] == "Expected failure"
    assert listed[1]["message_key"] == "err.test"
    assert listed[1]["message_args"] == ["detail"]
    assert all(j["queue_position"] is None for j in listed)


def test_discovery_limits_completed_history_but_retains_all_active(api):
    completed = []
    for index in range(55):
        jid, _ = enqueue(lambda jid: None, f"history-{index}")
        wait_done(jid)
        completed.append(jid)
    listed = api.get("/api/transcriptions").json()["jobs"]
    assert [j["id"] for j in listed] == list(reversed(completed[-50:]))

    release = threading.Event()
    started = threading.Event()
    active = []

    def block(jid):
        started.set()
        assert release.wait(5)

    try:
        jid, _ = enqueue(block, "history-running")
        active.append(jid)
        assert started.wait(2)
        for index in range(105):
            jid, _ = enqueue(lambda jid: None, f"history-queued-{index}")
            active.append(jid)
        # Ordinary job creation must not evict queued work either.
        wait_done(jobs.start(lambda jid: None, label="download"))
        listed = api.get("/api/transcriptions").json()["jobs"]
        pending = [j for j in listed if j["status"] in ("queued", "running")]
        assert [j["id"] for j in pending] == list(reversed(active))
        assert [j["queue_position"] for j in pending[:-1]] == list(range(105, 0, -1))
        assert all(jobs.get(jid) is not None for jid in active)
    finally:
        release.set()
        for jid in active:
            wait_done(jid)


def test_remote_clients_cannot_enqueue_transcriptions(api, monkeypatch):
    import app.main as main
    sid = session("Protected")
    monkeypatch.setattr(main, "_is_local_client", lambda request: False)
    response = api.post(f"/api/sessions/{sid}/transcribe", json={"model": "small"})
    assert response.status_code == 403
    assert response.json()["error_key"] == "err.host_only"
    assert api.get("/api/transcriptions").json() == {"jobs": []}


def test_language_context_resets_after_error_without_leaking_to_threads():
    from concurrent.futures import ThreadPoolExecutor

    from app import config, languages

    config.SETTINGS_PATH.write_text('{"language": "fr"}', encoding="utf-8")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="expected"):
            with languages.using_language("de"):
                assert languages.active_code() == "de"
                assert pool.submit(languages.active_code).result() == "fr"
                with languages.using_language("ca"):
                    assert languages.active_code() == "ca"
                assert languages.active_code() == "de"
                raise RuntimeError("expected")
        assert languages.active_code() == "fr"
        assert pool.submit(languages.active_code).result() == "fr"


def test_worker_restores_context_after_failed_job(monkeypatch):
    from app import languages

    # Drive the drain in this thread so its restored context is observable.
    monkeypatch.setattr(jobs, "_worker_running", True)
    seen = []

    def fail(jid):
        seen.append(languages.active_code())
        raise RuntimeError("expected")

    with languages.using_language("fr"):
        failed, _ = enqueue(fail, "context-failure", language="de")
        following, _ = enqueue(lambda jid: languages.active_code(), "context-next", language="en")
        jobs._drain_transcriptions()
        assert languages.active_code() == "fr"
    assert seen == ["de"]
    assert jobs.get(failed)["status"] == "error"
    assert jobs.get(following)["result"] == "en"


@pytest.mark.parametrize("session_language,global_language,accepted", [
    ("ca", "de", True), ("de", "ca", False),
])
def test_model_validation_uses_session_profile(api, monkeypatch, session_language,
                                               global_language, accepted):
    from app import config, languages, transcribe

    model = next(key for key in languages.PROFILES["ca"]["whisper_models"]
                 if key not in languages.PROFILES["de"]["whisper_models"])
    config.SETTINGS_PATH.write_text('{"language": "' + global_language + '"}', encoding="utf-8")
    monkeypatch.setattr(transcribe, "transcribe", lambda *args: [])
    sid = session("ModelProfile", session_language)
    response = api.post(f"/api/sessions/{sid}/transcribe", json={"model": model})
    result = response.json()
    if "job_id" in result:
        assert wait_done(result["job_id"])["status"] == "done"
    if accepted:
        assert response.status_code == 200
        assert "job_id" in result
    else:
        assert result.get("error_key") == "err.model_lang"


@pytest.mark.parametrize("use_sidecar", [False, True])
def test_session_language_survives_settings_changes(api, monkeypatch, tmp_path, use_sidecar):
    import json
    import sys
    from types import SimpleNamespace

    import app.main as main
    from app import languages, nlp, transcribe

    release_queue = threading.Event()
    queue_started = threading.Event()
    token_started = threading.Event()
    release_token = threading.Event()
    observed = []
    ids = []
    sid = session("Captured", "de")
    sidecar = tmp_path / "captured.srt"
    sidecar.write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n", encoding="utf-8")
    monkeypatch.setattr(main, "_find_sidecar_subs", lambda path: sidecar)
    monkeypatch.setattr(transcribe, "_MODELS", {})

    class Whisper:
        def __init__(self, model, **kwargs):
            observed.append(("model", languages.active_code()))

        def transcribe(self, path, *, language, **kwargs):
            observed.append(("whisper", language))
            return iter([SimpleNamespace(start=1, end=2, text="Hallo",
                                         avg_logprob=0, words=[])]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Whisper))

    def tokenize(text):
        observed.append(("token_before", languages.active_code()))
        token_started.set()
        assert release_token.wait(5)
        observed.append(("token_after", languages.profile()["wordfreq"]))
        return [{"lemma": languages.active_code()}]

    monkeypatch.setattr(nlp, "tokenize", tokenize)

    def blocker(jid):
        queue_started.set()
        assert release_queue.wait(5)

    try:
        blocker_id, _ = enqueue(blocker, "language-blocker")
        ids.append(blocker_id)
        assert queue_started.wait(2)
        response = api.post(f"/api/sessions/{sid}/transcribe",
                            json={"model": "small", "use_sidecar": use_sidecar})
        ids.append(response.json()["job_id"])
        assert jobs.get(ids[-1])["status"] == "queued"
        api.post("/api/settings", json={"language": "fr"})
        release_queue.set()
        assert token_started.wait(2)
        api.post("/api/settings", json={"language": "en"})
        assert languages.active_code() == "en"
        independent = jobs.start(lambda jid: languages.active_code())
        assert wait_done(independent)["result"] == "en"
    finally:
        release_queue.set()
        release_token.set()
        for jid in ids:
            wait_done(jid)
    assert jobs.get(ids[-1])["status"] == "done"
    assert observed == ([] if use_sidecar else [("model", "de"), ("whisper", "de")]) + [
        ("token_before", "de"), ("token_after", "de")]
    saved = json.loads(main.db.get_session(main.CON, sid)["transcript_json"])
    assert saved[0]["tokens"] == [{"lemma": "de"}]


def test_sidecar_transcription_still_updates_session(api, monkeypatch, tmp_path):
    import app.main as main
    from app import transcribe

    sid = session("Sidecar")
    sidecar = tmp_path / "sidecar.srt"
    sidecar.write_text("1\n00:00:01,000 --> 00:00:02,000\nHola\n", encoding="utf-8")
    monkeypatch.setattr(main, "_find_sidecar_subs", lambda path: sidecar)
    monkeypatch.setattr(transcribe, "tokens_for_existing", lambda segs: segs)

    def unexpected_whisper(*args):
        raise AssertionError("sidecar must not invoke Whisper")

    monkeypatch.setattr(transcribe, "transcribe", unexpected_whisper)
    response = api.post(f"/api/sessions/{sid}/transcribe",
                        json={"model": "small", "use_sidecar": True})
    job = wait_done(response.json()["job_id"])
    assert job["status"] == "done"
    assert job["result"] == {"segments": 1}
    assert main.db.get_session(main.CON, sid)["srt_source"] == "srt"
