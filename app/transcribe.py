"""faster-whisper transcription with word timestamps + spaCy tokens."""
import tempfile
from contextlib import ExitStack
from pathlib import Path

from . import jobs, media, nlp

_MODELS: dict[str, object] = {}
CHUNK_SECONDS = 15 * 60


def _model(key: str):
    from faster_whisper import WhisperModel

    from . import languages
    code = languages.active_code()
    models = languages.PROFILES[code]["whisper_models"]
    cache_key = (code, key)
    if cache_key not in _MODELS:
        _MODELS[cache_key] = WhisperModel(models.get(key) or key,
                                          device="cpu", compute_type="int8")
    return _MODELS[cache_key]


def transcribe(jid: str, media_path: str, model_key: str,
               duration: float, *, localize: bool = False) -> list[dict]:
    """Return segments: {start,end,text,logprob,words:[{w,start,end}],tokens:[...]}"""
    from . import languages
    jobs.set_progress(jid, 0.01, "Cargando modelo… (la primera vez se descarga)",
                      key="job.model")
    language = languages.active_code()
    model = _model(model_key)
    # el modelo ya está: lo que viene ahora (VAD sobre todo el audio) tarda
    # minutos en un capítulo largo, así que el mensaje debe reflejarlo — antes
    # se quedaba en «Cargando modelo…» y parecía que no avanzaba
    jobs.set_progress(jid, 0.02, "Analizando el audio… (puede tardar unos minutos)",
                      key="job.audio")
    out = []

    def consume(source: str | Path, offset: float):
        segments, _info = model.transcribe(
            str(source), language=language, beam_size=5,
            word_timestamps=True, vad_filter=True)
        for seg in segments:
            text = seg.text.strip()
            words = [{"w": w.word.strip(), "start": offset + w.start,
                      "end": offset + w.end} for w in (seg.words or [])]
            absolute_end = offset + seg.end
            out.append({"start": offset + seg.start, "end": absolute_end,
                        "text": text, "logprob": seg.avg_logprob,
                        "words": words, "tokens": nlp.tokenize(text)})
            if duration:
                jobs.set_progress(
                    jid, round(min(0.99, absolute_end / duration), 3),
                    "Transcribiendo…", key="job.transcribing")

    with ExitStack() as stack:
        source: str | Path = media_path
        temp_dir = None
        if localize or duration > CHUNK_SECONDS:
            temp_dir = Path(stack.enter_context(
                tempfile.TemporaryDirectory(prefix="linguaminer-whisper-")))
        if localize:
            assert temp_dir is not None
            source = temp_dir / "source.wav"
            jobs.set_progress(jid, 0.02, "Descargando audio…", key="job.audio")
            media.transcription_source(media_path, source)

        if duration <= CHUNK_SECONDS:
            consume(source, 0.0)
        else:
            assert temp_dir is not None
            start = 0.0
            index = 0
            while start < duration:
                chunk_duration = min(float(CHUNK_SECONDS), duration - start)
                chunk = temp_dir / f"chunk-{index:04d}.wav"
                jobs.set_progress(
                    jid, round(min(0.99, start / duration), 3),
                    "Preparando fragmento de audio…", key="job.audio")
                media.transcription_chunk(str(source), start, chunk_duration, chunk)
                consume(chunk, start)
                chunk.unlink(missing_ok=True)
                start += chunk_duration
                index += 1
    return out


def tokens_for_existing(segs: list[dict]) -> list[dict]:
    """Add tokens to segments parsed from an .srt (no word timestamps)."""
    for s in segs:
        s["words"] = []
        s["logprob"] = 0.0
        s["tokens"] = nlp.tokenize(s["text"])
    return segs
