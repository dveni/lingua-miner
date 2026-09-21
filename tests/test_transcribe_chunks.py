"""Long-media transcription is bounded and strictly sequential."""
from types import SimpleNamespace

from app import languages, transcribe


def test_long_media_is_transcribed_in_sequential_chunks_with_absolute_timestamps(
        monkeypatch, tmp_path):
    extracted = []
    active = False

    def extract(src, start, duration, out):
        nonlocal active
        assert active is False, "the previous Whisper iterator must be exhausted first"
        extracted.append((src, start, duration, out.name))
        out.write_bytes(b"chunk")

    class Model:
        def transcribe(self, path, **kwargs):
            nonlocal active
            assert active is False
            active = True
            chunk_index = len(extracted) - 1

            def segments():
                nonlocal active
                try:
                    yield SimpleNamespace(
                        start=1.0, end=2.0, text=f" chunk {chunk_index} ",
                        avg_logprob=-0.1,
                        words=[SimpleNamespace(word=" Wort ", start=1.1, end=1.8)],
                    )
                finally:
                    active = False

            return segments(), SimpleNamespace()

    progress = []
    monkeypatch.setattr(transcribe, "_model", lambda key: Model())
    monkeypatch.setattr(transcribe.media, "transcription_chunk", extract)
    monkeypatch.setattr(transcribe.nlp, "tokenize", lambda text: [{"text": text}])
    monkeypatch.setattr(transcribe.jobs, "set_progress",
                        lambda jid, p, message="", **kwargs: progress.append(p))

    result = transcribe.transcribe("job", "/media/long.mp3", "small", 1900)

    assert [(start, duration) for _, start, duration, _ in extracted] == [
        (0.0, 900.0), (900.0, 900.0), (1800.0, 100.0)]
    assert [segment["start"] for segment in result] == [1.0, 901.0, 1801.0]
    assert [segment["end"] for segment in result] == [2.0, 902.0, 1802.0]
    assert [segment["words"][0]["start"] for segment in result] == [1.1, 901.1, 1801.1]
    assert active is False
    assert progress[-1] == round(1802 / 1900, 3)


def test_long_media_pins_language_before_processing_chunks(monkeypatch):
    languages_iter = iter(["de", "fr"])
    observed = []

    class Model:
        def transcribe(self, path, **kwargs):
            observed.append(kwargs["language"])
            return iter(()), SimpleNamespace()

    monkeypatch.setattr(transcribe, "_model", lambda key: Model())
    monkeypatch.setattr(languages, "active_code", lambda: next(languages_iter))
    monkeypatch.setattr(
        transcribe.media, "transcription_chunk",
        lambda src, start, duration, out: out.write_bytes(b"chunk"))
    monkeypatch.setattr(transcribe.jobs, "set_progress", lambda *args, **kwargs: None)

    transcribe.transcribe("job", "/media/long.mp3", "small", 901)

    assert observed == ["de", "de"]


def test_long_remote_media_is_localized_once_before_chunking(monkeypatch):
    localized = []
    chunk_sources = []

    class Model:
        def transcribe(self, path, **kwargs):
            return iter(()), SimpleNamespace()

    def localize(src, out):
        localized.append(src)
        out.write_bytes(b"local audio")

    def extract(src, start, duration, out):
        chunk_sources.append(src)
        out.write_bytes(b"chunk")

    monkeypatch.setattr(transcribe, "_model", lambda key: Model())
    monkeypatch.setattr(transcribe.media, "transcription_source", localize,
                        raising=False)
    monkeypatch.setattr(transcribe.media, "transcription_chunk", extract)
    monkeypatch.setattr(transcribe.jobs, "set_progress", lambda *args, **kwargs: None)

    transcribe.transcribe("job", "https://cdn.example/expiring", "small", 901,
                          localize=True)

    assert localized == ["https://cdn.example/expiring"]
    assert len(chunk_sources) == 2
    assert all(not str(source).startswith("https://") for source in chunk_sources)
    assert len(set(map(str, chunk_sources))) == 1
