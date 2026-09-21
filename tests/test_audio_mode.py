"""Canonical media-kind detection, independent of origin and extension."""
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import media


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main, stream
    con = main.db.connect(tmp_path / "audio.db")
    monkeypatch.setattr(main, "CON", con)
    monkeypatch.setattr(stream, "_CACHE", {})
    client = TestClient(main.app)
    yield main, client
    client.close()
    con.close()


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='optional real media smoke test requires system ffmpeg/ffprobe')
@pytest.mark.parametrize('video', [False, True])
def test_real_mp4_tracks_reach_session_api(api, tmp_path, video):
    main, client = api
    path = tmp_path / 'tracks.mp4'
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
           '-i', 'sine=frequency=330:duration=0.3']
    if video:
        cmd += ['-f', 'lavfi', '-i', 'color=size=32x32:rate=5:duration=0.3',
                '-c:v', 'mpeg4']
    cmd += ['-c:a', 'aac', '-shortest', '-y', str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=15)
    sid = session(main, 'local', path)
    assert client.get(f'/api/sessions/{sid}').json()['is_audio'] is not video


def session(main, source_type, path, **kwargs):
    return main.db.create_session(main.CON, title="Audio", source_type=source_type,
        media_path=str(path), srt_source="none", model_size="-", duration_secs=3,
        transcript_json="[]", **kwargs)


@pytest.mark.parametrize("origin", ["local", "youtube", "url"])
def test_session_detail_reports_media_kind_not_origin(api, tmp_path, monkeypatch, origin):
    main, client = api
    path = tmp_path / "remuxed.mp4"
    path.touch()
    if origin == "url":
        path = "https://cdn.example/session.mp4"
    probe(monkeypatch, [{"codec_type": "audio"}])
    sid = session(main, origin, path)
    result = client.get(f"/api/sessions/{sid}")
    assert result.status_code == 200
    assert result.json().get("is_audio") is True


@pytest.mark.parametrize("origin", ["text", "local", "stream"])
def test_session_unknown_is_false_without_resolving(api, monkeypatch, origin):
    main, client = api
    resolve = Mock(side_effect=AssertionError("detail must not resolve a page"))
    monkeypatch.setattr(main.stream, "resolve", resolve)
    sid = session(main, origin, "", page_url="https://site.example/unknown")
    assert client.get(f"/api/sessions/{sid}").json().get("is_audio") is False
    resolve.assert_not_called()


@pytest.mark.parametrize("is_audio", [True, False])
def test_resolved_stream_metadata_reaches_both_apis_without_duplicate_fetch(api, monkeypatch, is_audio):
    main, client = api
    fmt = {"protocol": "https", "url": "https://cdn.example/track", "acodec": "aac",
           "vcodec": "none" if is_audio else "h264", "height": 0 if is_audio else 720}
    extract = Mock(return_value={"title": "Track", "duration": 3, "formats": [fmt]})
    monkeypatch.setattr(main.stream, "_extract", extract)
    run = Mock(side_effect=AssertionError("resolved metadata needs no ffprobe"))
    monkeypatch.setattr(media.subprocess, "run", run)
    page = "https://site.example/episode"
    sid = session(main, "stream", fmt["url"], page_url=page, stream_height=fmt["height"])
    assert client.get(f"/api/sessions/{sid}").json()["is_audio"] is False
    extract.assert_not_called()
    result = client.get(f"/api/sessions/{sid}/stream-url").json()
    assert result.get("is_audio") is is_audio
    assert result["url"] == fmt["url"]
    assert result["is_hls"] is False
    assert client.get(f"/api/sessions/{sid}").json()["is_audio"] is is_audio
    assert client.get(f"/api/sessions/{sid}/stream-url").json()["is_audio"] is is_audio
    assert len(main.stream.stream_url(page)) == 3  # public tuple contract unchanged
    assert extract.call_count == 1
    run.assert_not_called()


def test_local_probe_cache_tracks_path_mtime_and_size(tmp_path, monkeypatch):
    import os
    path = tmp_path / "audio.mp4"
    path.write_bytes(b"a")
    run = probe(monkeypatch, [{"codec_type": "audio"}])
    assert media.is_audio_only(path) is True
    assert media.is_audio_only(str(path)) is True
    assert run.call_count == 1
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1000))
    assert media.is_audio_only(path) is True
    assert run.call_count == 2
    st = path.stat()
    path.write_bytes(b"longer")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert media.is_audio_only(path) is True
    assert run.call_count == 3
    other = tmp_path / "other.mp4"
    other.write_bytes(b"longer")
    assert media.is_audio_only(other) is True
    assert run.call_count == 4


def test_missing_local_file_never_probes(tmp_path, monkeypatch):
    run = probe(monkeypatch, [{"codec_type": "audio"}])
    assert media.is_audio_only(tmp_path / "absent.mp3") is False
    run.assert_not_called()


def test_direct_url_probe_is_bounded_and_cached_including_errors(monkeypatch):
    run = probe(monkeypatch, [{"codec_type": "audio"}])
    url = "https://cdn.example/audio-in-container.mp4?token=a"
    assert media.is_audio_only(url) is True
    assert media.is_audio_only(url) is True
    assert run.call_count == 1
    assert run.call_args.kwargs["timeout"] <= 5
    run.side_effect = subprocess.TimeoutExpired("ffprobe", 5)
    assert media.is_audio_only(url + "failure") is False
    assert media.is_audio_only(url + "failure") is False
    assert run.call_count == 2


def probe(monkeypatch, streams):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"streams": streams})))
    monkeypatch.setattr(media.subprocess, "run", run)
    monkeypatch.setattr(media.shutil, "which", lambda name: name)
    return run


def test_local_audio_uses_bounded_codec_probe(tmp_path, monkeypatch):
    path = tmp_path / "remuxed.mp4"
    path.write_bytes(b"media")
    run = probe(monkeypatch, [{"codec_type": "audio"}])
    assert getattr(media, "is_audio_only", lambda _: False)(path) is True
    assert 0 < run.call_args.kwargs["timeout"] <= 5
    assert "stream=codec_type:stream_disposition=attached_pic" in run.call_args.args[0]


@pytest.mark.parametrize("suffix", [".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".opus", ".mp4", ".webm"])
def test_cover_art_is_not_video(tmp_path, monkeypatch, suffix):
    path = tmp_path / ("audio" + suffix)
    path.touch()
    probe(monkeypatch, [{"codec_type": "audio"},
                        {"codec_type": "video", "disposition": {"attached_pic": 1}}])
    assert media.is_audio_only(path) is True


@pytest.mark.parametrize("streams", [[], [{"codec_type": "video"}],
    [{"codec_type": "audio"}, {"codec_type": "video", "disposition": {"attached_pic": 0}}],
    [{"codec_type": "video", "disposition": {"attached_pic": 1}}]])
def test_real_video_or_no_audio_is_not_audio(tmp_path, monkeypatch, streams):
    path = tmp_path / "misleading.mp3"
    path.touch()
    probe(monkeypatch, streams)
    assert media.is_audio_only(path) is False


@pytest.mark.parametrize("failure", [OSError("missing"), subprocess.TimeoutExpired("ffprobe", 5),
                                      "garbage", "{}", '{"streams": null}', '{"streams": [null]}',
                                      "nonzero"])
def test_probe_errors_are_conservatively_false(tmp_path, monkeypatch, failure):
    path = tmp_path / "broken.mp3"
    path.touch()
    run = probe(monkeypatch, [{"codec_type": "audio"}])
    if isinstance(failure, Exception):
        run.side_effect = failure
    elif failure == "nonzero":
        run.return_value.returncode = 1
    else:
        run.return_value.stdout = failure
    assert media.is_audio_only(path) is False
