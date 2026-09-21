# Mobile audio and touch expressions — implementation notes

## Existing architecture (inspection before implementation)

Base: main at aec2187; work branch feat/mobile-audio-transcript. Vanilla JS in static/app.js, markup in static/index.html, dark responsive CSS in static/style.css. Python FastAPI routes in app/main.py; SQLite sessions in app/db.py; ffmpeg helpers in app/media.py. Existing tests are pytest plus Ruff; there is no browser testing framework/package.json.

Session source_type is local/youtube/url/stream/text, not a media kind. Session detail supplies media_url; stream-url resolves a fresh online URL. app/stream.py already detects is_audio when selecting audio-only formats, but that fact is dropped before the frontend. Local playback accepts several audio containers and remuxes others; extension-only classification would miss audio in MP4 and mistake cover art for video.

One HTML video element (#video, JS V) handles local media, direct URLs, and HLS (native Safari or vendored hls.js). timeupdate subtracts OFFSET and finds a timed SEGS entry; setCur updates CUR, transcript .active and the overlay. Auto-pause and condensed playback live in that same handler. gotoSeg, prevSeg, nextSeg, replaySeg and the seek slider already share this clock. Resume position is periodically saved.

renderSegs produces one .seg per subtitle in #side-panel/#subs. Each has a timestamp and tokenHtml(seg), preserving token whitespace, vocabulary classes and recommended-word classes. scrollBrowserTo currently centers every changed subtitle with no manual-browsing guard. The video overlay uses the same token renderer. Controls are an auto-hiding absolute overlay inside #video-wrap, which lifts/obscures subtitles on small displays.

bindTokenEvents handles click and mouse hover. Desktop expression selection is browser-native drag selection, read with window.getSelection() on token click (also used by keyboard mining). openPopup posts selection + sentence + session_id + segment_index to /api/lookup. mineFromPopup -> mineQuick -> /api/cards/mine; editFromPopup -> mine -> /api/cards/preview -> existing editor -> /api/cards. Backend preview builds segment audio/image and existing Anki payloads. Touch must call openPopup rather than invent card payloads.

## Design decisions

- Retain one media element, transcript, control set and synchronization engine.
- Activate transcript-first presentation only with canonical is_audio and a small viewport, including coarse-pointer phone landscape; no UA sniffing.
- Transcript and compact controls are siblings in normal flex flow; controls never overlay transcript. Use dynamic viewport height and safe-area padding.
- Manual browsing uses an explicit Follow button (no unpredictable idle snap). A compact tokenized current-line context remains available while browsing. Direct touch/selection and popup/editor interactions block follow.
- Follow runs on subtitle change or explicit restoration/resize, not every timeupdate. Reduced motion disables smooth scrolling.
- Touch selection remains inside one sentence and uses structured token indexes, preserving punctuation/Unicode and desktop semantics. Gesture state is separate from mining.

## Implementation and verification

### Media classification

`media.is_audio_only` inspects ffprobe stream metadata: at least one audio track and no non-attached-picture video track. It correctly handles audio remuxed to MP4, cover art, and the accepted audio containers without filename guesses. Unknown/probe failure stays in the existing layout. Probes time out after five seconds; local cache keys include canonical path, size and nanosecond mtime, URL cache buckets last four minutes. Stream session detail uses existing resolver cache; `/stream-url` exposes its authoritative `is_audio`, which updates `SESSION` before playback. No DB migration or new production dependency. An unknown/infinite live duration displays an em dash and disables the scrubber instead of attempting to seek to Infinity.

### Presentation and interaction

`AudioPlayer.mount` owns presentation/follow state, not playback. Audio-only sessions use the layout at <=700px, or coarse-pointer <=1024px (phone landscape). The same transcript and controls remain in normal flex flow. The stage uses `100dvh`, safe-area padding, bounded scroll containers and a ResizeObserver. At 412x915 the transcript measured 703px tall; the 144px controls begin below it. Opening tools resizes the transcript. Video and desktop audio retain the old stage.

`setCur` notifies follow only on actual subtitle changes. Active lines center slightly above the midpoint. Explicit seeks/follow restoration are immediate; normal transitions can be smooth unless reduced motion is set. Swipes, wheel and focused transcript navigation enter manual browsing, which lasts until Follow or an explicit seek. A tokenized current-line preview remains visible while browsing. Pending/active selection, finger contact, dictionary popup and card editor suspend automatic motion. A held preview freezes visibility as well as content across subtitle gaps.

`TouchSelection.bind` waits 420ms and cancels pending holds after 10px movement. Actual Touch Events allow normal initial vertical scrolling while non-passive active `touchmove` prevents scrolling during a selected drag; Pointer Events are used for device/native-selection handling and cancellation. Merely changing `touch-action` after a hold would not reliably work in Chrome. Hit testing uses `elementFromPoint`, token indexes and a same-sentence contiguous min/max range. Phrase construction uses structured `t/ws`, retaining punctuation and Unicode. Release calls existing `openPopup` exactly once; preview/mining/Anki request shapes are unchanged. Quick taps and native desktop text selection remain intact. Cancellation covers touch/pointer cancel, Escape, capture loss, blur, page hiding, detached DOM and explicit session changes. No separate stylus-only gesture was added.

### Changed files

- Backend: `app/media.py`, `app/main.py`.
- Frontend: `static/app.js`, `static/index.html`, `static/style.css`, `static/i18n.js`, `static/sw.js`; new `static/audio-player.js`, `static/audio-player.css`, `static/touch-selection.js`.
- Verification: `tests/test_audio_mode.py`, `tests/frontend/audio-player.test.cjs`, `tests/frontend/touch-selection.test.cjs`, `scripts/test-mobile-ui.py`, `.github/workflows/ci.yml`.
- Notes/local QA exclusions: this file and `.gitignore`.

### Tests

- Added `tests/test_audio_mode.py`: API classification, resolver cache, local/direct metadata, timeout/errors, cover art, supported/remuxed containers, cache invalidation; includes actual ffmpeg-generated audio-only and video MP4 smoke tests (skip only if system ffmpeg/ffprobe is absent).
- Added Node built-in tests in `tests/frontend/`: audio opt-in/follow state, forward/backward phrase ranges, Unicode/punctuation/whitespace, threshold/slop, hover/click suppression, lifecycle cleanup, token rendering and existing popup integration. CI now runs these without npm dependencies.
- Added optional `scripts/test-mobile-ui.py`, using Playwright only as a development tool, not a project/production dependency. Real Chromium DOM, media playback, Range seeking and CDP touch input; dictionary/session/card APIs are explicitly fixture-backed. Desktop native mouse drag is verified, then its selected text is passed through the existing token-click handler. A separate unmocked browser context verifies installation/caching of the new PWA shell assets.
- Browser assertions cover 412x915 portrait, 915x412 landscape, 320x640 editor bounds, active-line/control separation, actual clock changes, scrub/navigation/replay, browsing suppression, follow restoration, forward/backward highlighted touch ranges, quick tap, cancellation, card preview payload/editor, subtitle-gap holding, keyboard scrolling, tools, reduced motion, video exclusion, cold stream metadata, no-transcript audio, desktop keyboard and native selection. No page errors in passing run.
- Read-only review found editor inherited fixed dimensions, held-preview disappearance at gaps, and focused arrow keys bubbling to global shortcuts. Each was reproduced with a failing browser assertion, fixed, and rerun successfully.

Executed commands:

    .venv/bin/ruff check app/ tests/ scripts/test-mobile-ui.py
    .venv/bin/python -m pytest tests/
    # Also run with explicit verbosity: .venv/bin/python -m pytest tests/ -o addopts='' -q
    node --test tests/frontend/*.test.cjs
    .venv/bin/python scripts/test-mobile-ui.py
    git diff --check

Results: Ruff clean; 347 Python tests passed (4 existing/dependency deprecation warnings); 15 Node tests passed; browser assertions and service-worker cache checks passed; diff whitespace clean.

To reproduce the optional browser test, run from the repository (the isolated server must use the same `.qa-data` directory):

    uv pip install playwright
    .venv/bin/python -m playwright install chromium
    LINGUAMINER_DIR="$PWD/.qa-data" .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18977

In another terminal:

    .venv/bin/python scripts/test-mobile-ui.py

The script generates a 90-second sine-wave MP3 using system ffmpeg if absent and saves screenshots under `.qa-evidence/`. These are test fixtures, not podcast content. Before/after, forward/backward selection, landscape and desktop screenshots were captured.

### Verification boundaries / remaining device checks

No claim of a physical Android/PWA or iOS Safari run. Chromium mobile emulation verifies real touch input, not physical-device browser chrome, magnification or safe-area hardware. Live third-party radio/YouTube streams, an actual Whisper model transcription, external dictionary translation and sending a card into a real Anki collection were not performed. Their existing automated backend tests pass; new browser card tests assert unchanged payloads with explicit response fixtures. Share-mode permission tests pass in the existing suite. Extremely tall subtitle blocks remain scrollable rather than being truncated or scaled to illegibility. The current-line preview during manual browsing is also scrollable for long blocks. Unknown/unprobeable audio conservatively retains the legacy presentation. Nothing was deployed to CasaOS or pushed to GitHub.
