# Transcription activity dropdown

The existing transcription button called `pollJob`, which kept the global progress
pill visible until Whisper finished and reopened `SESSION.id` afterward. That
also meant completion could unexpectedly reopen a different session after the
user navigated away. Other import/download/condensed jobs still use that pill;
only transcription progress moves into the new activity component.

## Presentation and behavior

- `static/transcription-activity.js` owns a single discovery poll and keyed DOM
  rows. The top-bar button shows the number of active jobs. Its dropdown lists
  the running job, queued positions, and recent completed/failed jobs.
- On mobile audio screens, the same button moves into `#player-top`; desktop,
  library and reader screens retain it in the global header. The bounded,
  scrollable dropdown is closed by its button, outside interaction or Escape.
- Starting a transcription captures the session ID, submits it, and returns to
  normal app use. There is no persistent transcription pill or automatic
  navigation on completion. Tap a row to open its session/transcript.
- Progress reflects existing Whisper milestones/segment progress, not an ETA.
  Slow model loading/VAD may leave the percentage unchanged for a while.
- Discovery recovers running jobs after a page reload. Network errors retain
  the last known list, show a retry notice and retry automatically.
- Strings use the existing Spanish/Catalan/English i18n; server titles and error
  messages are inserted with `textContent`, not interpolated HTML.

## Backend

`app/jobs.py` retains the existing job registry/polling API and adds a locked FIFO
for transcriptions. One worker processes transcription jobs, duplicate active
requests for the same session reuse a job, failures do not block later jobs,
and independent job types keep their original concurrency.

`GET /api/transcriptions` returns `{jobs: [...]}` for the current study language,
including session/title/model metadata, progress, status, localized-message keys
and one-based global queue positions. It retains all active jobs and at most 50
recent terminal entries in the response. Jobs remain process-local, matching the
existing architecture: restarting the server loses pending jobs and history,
not already saved transcripts. This is not a durable job scheduler.

The queue captures session language independently of the currently selected UI
study language so switching languages cannot retarget a waiting transcription.

## Verification commands

    .venv/bin/ruff check app/ tests/
    .venv/bin/python -m pytest tests/
    node --test tests/frontend/*.test.cjs
    .venv/bin/python tests/browser/transcription_activity.py

The optional browser script requires Playwright Chromium and a running app
(`LM_TEST_URL`, default `http://127.0.0.1:18979`). It serves local frontend files
and uses synthetic session/job responses, intercepting mutations to avoid
changing the dev library. Real transcription is verified separately on Dev.

The asset/cache version is bumped together with `pyproject.toml` and the service
worker shell to 1.33.3, including both new component files.

## Verified deployment

Deployed only to LinguaMiner Dev at
`https://casaos-zrh.panthera-dojo.ts.net:8979/`; the production app was not changed.
Prior Dev files are backed up under
`/DATA/AppData/lingua-miner-dev/backup-activity-20260921-222658`.

Verification results: Ruff passed, 359 Python tests passed (four existing
third-party deprecation warnings), 15 Node tests passed, and six Chromium
browser scenarios passed at a 412×915 touch viewport and desktop resize.

A real Whisper-small smoke test submitted the existing 45-second German clip
and a new five-second excerpt. Discovery showed one running job and one queued
at position 1; both completed with six and one transcript segments respectively.
The deployed mobile dropdown showed both results, without the persistent pill,
horizontal overflow, or JavaScript page errors. The five-second clip remains in
the Dev library as `German-podcast-5s.mp3`. Screenshots and API evidence are in
`.qa-evidence/activity-live-*` in the implementation workspace.
