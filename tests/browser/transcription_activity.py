"""Optional UI regression: .venv/bin/python tests/browser/transcription_activity.py.
Uses the running dev app (LM_TEST_URL), local frontend files, and controlled jobs.
No transcription POST reaches the server. Requires optional Playwright Chromium.
"""
import os
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get('LM_TEST_URL', 'http://127.0.0.1:18979')


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.page = self.browser.new_page(viewport={'width': 412, 'height': 915},
                                          is_mobile=True, has_touch=True)
        self.jobs = [dict(id='job-a', session_id='test-a', title='Podcast A',
                         model='small', status='running', progress=.42,
                         message='Transcribing…', queue_position=None),
                     dict(id='job-b', session_id='test-b', title='Podcast B',
                         model='small', status='queued', progress=0,
                         message='', queue_position=1)]
        def static(route):
            from urllib.parse import urlparse
            path = urlparse(route.request.url).path
            file = ROOT / 'static' / ('index.html' if path == '/' else path.lstrip('/'))
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.continue_()
        self.page.route(BASE + '/**', static)
        # Mutations are all intercepted: running tests cannot change the dev library.
        self.page.route('**/api/**', lambda r: r.fallback() if r.request.method == 'GET'
                        else r.fulfill(json={}))
        def fake_session(route):
            sid = route.request.url.rsplit('/', 1)[-1]
            route.fulfill(json=dict(id=sid, title='Test audio', source_type='local',
                is_audio=True, media_url='/test-audio.wav', duration_secs=45,
                transcript=[dict(start=0, end=5, text='Hallo Welt', tokens=[
                    dict(t='Hallo', lemma='hallo', is_word=True, ws=' ', pos='', zipf=4),
                    dict(t='Welt', lemma='welt', is_word=True, ws='', pos='', zipf=4)])]))
        self.page.route('**/api/sessions/test-*', fake_session)
        import io
        import wave
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b'\0\0' * 8000)
        self.page.route('**/test-audio.wav', lambda r: r.fulfill(body=audio.getvalue(), content_type='audio/wav'))
        self.page.route('**/api/transcriptions', lambda r: r.fulfill(json={'jobs': self.jobs}))
        self.page.goto(BASE)

    def tearDown(self):
        self.browser.close()
        self.pw.stop()

    def test_dropdown_recovers_active_queue_on_load(self):
        button = self.page.locator('#transcription-toggle')
        button.wait_for(timeout=3000)
        self.assertEqual(button.get_attribute('aria-expanded'), 'false')
        button.click()
        self.page.get_by_text('Podcast A', exact=True).wait_for()
        self.page.get_by_text('Podcast B', exact=True).wait_for()
        self.assertEqual(self.page.locator('#transcription-count').inner_text(), '2')
        self.assertIn('42%', self.page.locator('#transcription-panel').inner_text())
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#transcription-panel').is_hidden())
        self.assertTrue(button.evaluate('(el) => el === document.activeElement'))
        self.assertTrue(self.page.locator('#progress-pill').is_hidden())

    def test_submit_is_background_and_navigation_does_not_jump(self):
        self.jobs = []
        self.page.evaluate('TRANSCRIPTIONS.refresh()')
        self.page.evaluate("openSession('test-a')")
        self.page.locator('#audio-tools').click()
        posts = []
        def submit(route):
            posts.append(route.request.url)
            self.jobs = [dict(id='job-c', session_id='test-a', title='Test clip',
                             model='small', status='running', progress=.3,
                             message='Transcribing…', queue_position=None)]
            route.fulfill(json={'job_id': 'job-c'})
        self.page.route('**/api/sessions/*/transcribe', submit)
        self.page.route('**/api/jobs/*', lambda r: r.fulfill(json=self.jobs[0]))
        self.page.locator('#transcribe-btn').click()
        self.page.wait_for_timeout(250)
        self.assertEqual(len(posts), 1)
        self.assertTrue(self.page.locator('#progress-pill').is_hidden())
        self.assertTrue(self.page.locator('#transcription-toggle').is_visible())
        self.page.locator('#back').click()
        self.jobs[0].update(status='done', progress=1, result={'session_id': 'test-a'})
        self.page.evaluate('TRANSCRIPTIONS.refresh()')
        self.page.wait_for_timeout(900)
        self.assertTrue(self.page.locator('#home').is_visible())
        self.page.locator('#transcription-toggle').click()
        self.page.get_by_text('Test clip', exact=True).click()
        self.page.wait_for_function("SESSION?.id === 'test-a' && !document.getElementById('player').hidden")

    def test_refresh_waits_for_coalesced_follow_up(self):
        self.page.evaluate('TRANSCRIPTIONS.refresh()')
        self.page.evaluate("""() => {
            window.queueReads = [];
            const original = window.fetch;
            window.fetch = (url, opts) => url === '/api/transcriptions'
                ? new Promise(resolve => queueReads.push(jobs => resolve(
                    new Response(JSON.stringify({jobs}), {status: 200}))))
                : original(url, opts);
            window.refreshDone = [];
            TRANSCRIPTIONS.refresh().then(() => refreshDone.push('first'));
            TRANSCRIPTIONS.refresh().then(() => refreshDone.push('second'));
            TRANSCRIPTIONS.refresh().then(() => refreshDone.push('third'));
        }""")
        self.assertEqual(self.page.evaluate('refreshDone'), [])
        self.assertEqual(self.page.evaluate('queueReads.length'), 1)
        self.page.evaluate('queueReads[0]([])')
        self.page.wait_for_function('queueReads.length === 2')
        self.assertEqual(self.page.evaluate('refreshDone'), [])
        self.page.evaluate('(jobs) => queueReads[1](jobs)', self.jobs)
        self.page.wait_for_function('refreshDone.length === 3')
        self.assertEqual(self.page.evaluate('queueReads.length'), 2)
        self.assertEqual(self.page.locator('#transcription-count').inner_text(), '2')

    def test_pending_post_survives_refresh_and_session_navigation(self):
        self.jobs = []
        self.page.evaluate('TRANSCRIPTIONS.refresh()')
        self.page.evaluate("openSession('test-a')")
        self.page.locator('#audio-tools').click()
        self.page.evaluate("""() => {
            window.queueReads = [];
            window.postCount = 0;
            window.jobPolls = 0;
            const original = window.fetch;
            window.fetch = (url, opts) => {
                if (url === '/api/transcriptions') return new Promise(resolve =>
                    queueReads.push(jobs => resolve(new Response(JSON.stringify({jobs})))));
                if (url.endsWith('/transcribe')) {
                    postCount++;
                    return new Promise(resolve => window.finishPost = () => resolve(
                        new Response(JSON.stringify({job_id: 'job-c'}))));
                }
                if (url.startsWith('/api/jobs/')) jobPolls++;
                return original(url, opts);
            };
            const button = document.getElementById('transcribe-btn');
            const submit = button.onclick;
            window.submitDone = false;
            button.onclick = () => submit().then(() => window.submitDone = true);
        }""")
        button = self.page.locator('#transcribe-btn')
        button.click()
        self.assertTrue(button.is_disabled())
        self.page.evaluate('() => { TRANSCRIPTIONS.refresh(); }')
        self.page.evaluate('queueReads[0]([])')
        self.page.wait_for_function('document.getElementById("transcription-count").hidden')
        self.assertTrue(button.is_disabled(), 'queue refresh must not unlock a pending POST')
        self.page.evaluate("openSession('test-b')")
        self.assertFalse(button.is_disabled(), 'another session is not locked by this POST')
        self.page.evaluate("openSession('test-a')")
        self.assertTrue(button.is_disabled(), 'pending state must survive navigation')
        self.page.evaluate('document.getElementById("transcribe-btn").click()')
        self.assertEqual(self.page.evaluate('postCount'), 1)
        # A pre-POST snapshot completes after the POST: it must trigger a new read.
        self.page.evaluate('() => { TRANSCRIPTIONS.refresh(); }')
        self.page.evaluate('finishPost()')
        self.assertFalse(self.page.evaluate('submitDone'))
        self.page.evaluate('queueReads[1]([])')
        self.page.wait_for_function('queueReads.length === 3')
        self.assertFalse(self.page.evaluate('submitDone'))
        self.assertTrue(button.is_disabled())
        job = dict(id='job-c', session_id='test-a', title='New submission',
                   status='queued', progress=0, queue_position=1)
        self.page.evaluate('(job) => queueReads[2]([job])', job)
        self.page.wait_for_function('submitDone')
        self.assertEqual(self.page.locator('#transcription-count').inner_text(), '1')
        self.assertTrue(button.is_disabled())
        self.assertEqual(self.page.evaluate('jobPolls'), 0)
        self.assertTrue(self.page.locator('#progress-pill').is_hidden())
        # Completed jobs must release the lock, not leave submission state behind.
        job['status'] = 'done'
        self.page.evaluate('() => { TRANSCRIPTIONS.refresh(); }')
        self.page.evaluate('(job) => queueReads[3]([job])', job)
        self.page.wait_for_function('!document.getElementById("transcribe-btn").disabled')

    def test_closed_toggle_space_does_not_trigger_player_shortcuts(self):
        self.page.evaluate("openSession('test-a')")
        self.page.evaluate("""() => {
            window.playCalls = 0;
            playMedia = () => { playCalls++; };
        }""")
        toggle = self.page.locator('#transcription-toggle')
        toggle.focus()
        self.page.keyboard.press('Space')
        self.assertEqual(self.page.evaluate('playCalls'), 0)
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.assertTrue(self.page.locator('#transcription-close').evaluate(
            '(el) => el === document.activeElement'))
        # Space on a panel button retains its native action without playing media.
        self.page.keyboard.press('Space')
        self.assertTrue(self.page.locator('#transcription-panel').is_hidden())
        self.assertTrue(toggle.evaluate('(el) => el === document.activeElement'))
        self.assertEqual(self.page.evaluate('playCalls'), 0)
        self.page.keyboard.press('Enter')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#transcription-panel').is_hidden())
        # Shortcuts still work outside the activity widget.
        self.page.locator('#back').focus()
        self.page.keyboard.press('Space')
        self.assertEqual(self.page.evaluate('playCalls'), 1)

    def test_mobile_queue_layout_and_error_recovery(self):
        self.page.evaluate("openSession('test-a')")
        self.page.locator('#transcription-toggle').click()
        self.assertTrue(self.page.locator('#player-top #transcription-toggle').is_visible())
        self.page.wait_for_function("document.getElementById('transcription-panel').textContent.includes('42%')")
        box = self.page.locator('#transcription-panel').bounding_box()
        assert box is not None
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 412)
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 412)
        self.assertLessEqual(box['y'] + box['height'], 915)
        self.jobs[0].update(status='error', error='Decoder failed')
        self.page.evaluate('TRANSCRIPTIONS.refresh()')
        self.assertIn('Decoder failed', self.page.locator('#transcription-panel').inner_text())
        self.page.route('**/api/transcriptions', lambda r: r.fulfill(status=503, json={'error': 'offline'}))
        self.page.evaluate('TRANSCRIPTIONS.refresh()')
        self.assertTrue(self.page.locator('#transcription-offline').is_visible())
        self.assertIn('Podcast B', self.page.locator('#transcription-panel').inner_text())
        self.page.set_viewport_size({'width': 1200, 'height': 800})
        self.page.wait_for_function("!!document.querySelector('header #transcription-toggle')")
        self.page.locator('#transcription-close').click()
        self.assertTrue(self.page.locator('#transcription-panel').is_hidden())


if __name__ == '__main__':
    unittest.main()
