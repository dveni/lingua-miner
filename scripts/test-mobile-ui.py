"""Optional real Chromium acceptance: uv pip install playwright; python -m playwright install chromium.
Run against isolated uvicorn with scripts/test-mobile-ui.py --url http://127.0.0.1:18977.
Fixtures stub dictionary/session APIs, not the real app DOM, playback, gestures or layout.
"""
import argparse
import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:18977')
    parser.add_argument('--before', action='store_true')
    args = parser.parse_args()
    out = ROOT / '.qa-evidence'
    out.mkdir(exist_ok=True)
    media = ROOT / '.qa-data/media/test-audio.mp3'
    media.parent.mkdir(parents=True, exist_ok=True)
    if not media.exists():
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', 'sine=frequency=330:duration=90', '-y', str(media)], check=True)
    words = ['Und', 'da', 'haben', 'einige', 'geschrieben']
    tokens = [dict(t=w, lemma=w.lower(), is_word=True, ws=' ' if i < 4 else '',
                   pos='VERB', zipf=5) for i, w in enumerate(words)]
    segs = [dict(start=i * 2, end=i * 2 + 1.9, text=' '.join(words), tokens=tokens)
            for i in range(45)]
    session = dict(id='qa', title='Mobile audio acceptance', source_type='local',
                   is_audio=True, media_url='/media/test-audio.mp3', transcript=segs,
                   duration_secs=90, word_statuses={'und': 'known', 'da': 'learning',
                                                   'haben': 'known', 'geschrieben': 'ignored'})
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={'width': 412, 'height': 915},
                                      is_mobile=True, has_touch=True, device_scale_factor=1,
                                      service_workers='block')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/sessions/qa', lambda r: r.fulfill(json=session))
        # Serve real media through FastAPI FileResponse (HTTP Range seeking).
        lookups = []
        def lookup(route):
            body = route.request.post_data_json
            lookups.append(body)
            route.fulfill(json=dict(lemma=body['selection'], pos='VERB', zipf=4,
                                    senses=[], active=-1, sentence_es='', word_es='',
                                    ipa='', tts=False, glosses=[], userdefs=[]))
        page.route('**/api/lookup', lookup)
        previews = []
        def preview(route):
            body = route.request.post_data_json
            previews.append(body)
            route.fulfill(json=dict(paraula=body['selection'], paraula_es='translation',
                                    frase=segs[body['segment_index']]['text'], frase_es='sentence',
                                    lema=body['selection'], pos='VERB', freq_rank='common',
                                    freq_zipf=4.0, font='qa', senses=[], audio_file='', image_file=''))
        page.route('**/api/cards/preview', preview)
        page.route('**/api/examples?*', lambda r: r.fulfill(json={'examples': []}))
        page.goto(args.url)
        page.wait_for_function('typeof openSession === "function" && SETTINGS !== null')
        page.evaluate('openSession("qa")')
        page.wait_for_function('V.readyState >= 2')
        page.evaluate('gotoSeg(3)')
        page.wait_for_function('CUR === 3 && V.currentTime >= 6')
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / ('before.png' if args.before else 'after.png')))
        if args.before:
            print('Before screenshot:', out / 'before.png')
            browser.close()
            return
        assert page.locator('body').evaluate('(el)=>el.classList.contains("audio-mobile")')
        assert page.locator('#side-panel').is_visible()
        assert not page.locator('#video').is_visible()
        assert page.locator('#controls').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        panel = page.locator('#side-panel').bounding_box()
        ctl = page.locator('#controls').bounding_box()
        assert panel and ctl
        assert panel['y'] + panel['height'] <= ctl['y'] + 1
        assert panel['height'] > 915 * .5
        page.evaluate('wakeControls(); V.pause()')
        active = page.locator('.seg.active').bounding_box()
        assert active
        assert panel['y'] <= active['y'] and active['y'] + active['height'] <= panel['y'] + panel['height']
        print(json.dumps({'viewport': [412, 915], 'panel': panel, 'controls': ctl, 'errors': errors}))
        # Real clock progress, seek slider, and subtitle navigation.
        page.evaluate('V.play()')
        page.wait_for_function('CUR === 4', timeout=5000)
        page.evaluate('V.pause(); $("seek").value = 500; $("seek").onchange()')
        page.wait_for_function('CUR === 22')
        page.wait_for_timeout(350)
        page.locator('#next-btn').tap()
        page.wait_for_function('CUR === 23')
        page.locator('#prev-btn').tap()
        page.wait_for_function('CUR === 22')
        page.locator('#replay-btn').tap()
        page.wait_for_function('V.currentTime >= 44 && V.currentTime < 45')
        page.evaluate('V.pause()')
        cdp = context.new_cdp_session(page)
        def touch(kind, x: float = 0, y: float = 0):
            cdp.send('Input.dispatchTouchEvent', dict(type=kind, touchPoints=(
                [] if kind in ('touchEnd', 'touchCancel') else
                [dict(x=x, y=y, id=1, radiusX=2, radiusY=2)])))
        # A pre-threshold swipe browses naturally; later subtitle changes do not yank it back.
        touch('touchStart', 350, 500)
        touch('touchMove', 350, 400)
        touch('touchMove', 350, 280)
        touch('touchEnd')
        page.wait_for_timeout(700)
        assert page.locator('#audio-follow').get_attribute('aria-pressed') == 'false'
        # Let native fling momentum finish before measuring follow suppression.
        page.wait_for_function('''() => {
          const top = document.getElementById('side-panel').scrollTop;
          const now = performance.now();
          if (!window.qaScroll || qaScroll.top !== top) window.qaScroll = {top, at: now};
          return now - qaScroll.at > 180;
        }''', timeout=5000)
        scroll = page.locator('#side-panel').evaluate('(e)=>e.scrollTop')
        page.evaluate('setCur(30)')
        page.wait_for_timeout(300)
        print('Browse positions', scroll, page.locator('#side-panel').evaluate('(e)=>e.scrollTop'), page.evaluate('CUR'))
        assert abs(page.locator('#side-panel').evaluate('(e)=>e.scrollTop') - scroll) < 2
        assert page.locator('#audio-current').is_visible()
        held = page.locator('#audio-current-text .t').nth(1).bounding_box()
        assert held
        touch('touchStart', held['x'] + 3, held['y'] + 4)
        page.wait_for_timeout(100)
        page.evaluate('setCur(-1)')
        assert page.locator('#audio-current').is_visible(), 'held preview must survive subtitle gaps'
        touch('touchCancel')
        page.evaluate('setCur(22)')
        page.locator('#side-panel').focus()
        page.keyboard.press('ArrowDown')
        assert page.locator('#audio-follow').get_attribute('aria-pressed') == 'false'
        assert not page.locator('#word-pop').is_visible()
        page.locator('#audio-follow').tap()
        page.wait_for_timeout(300)
        page.evaluate('gotoSeg(3); V.pause()')
        page.wait_for_timeout(350)
        # Background of a row seeks while word taps still open the dictionary.
        page.locator('#seg-4 .time').tap()
        page.wait_for_function('CUR === 4')
        page.evaluate('V.pause()')
        page.wait_for_timeout(400)
        page.locator('#seg-4 .t').nth(1).tap()
        page.wait_for_function('POP?.selection === "da"')
        assert lookups[-1]['selection'] == 'da'
        page.locator('#wp-close').tap()
        page.wait_for_timeout(300)
        # Real CDP touch streams, not dispatched synthetic PointerEvents.
        for reverse in (False, True):
            row = page.locator('#seg-4 .t')
            anchor, end = (3, 1) if reverse else (1, 3)
            a, b = row.nth(anchor).bounding_box(), row.nth(end).bounding_box()
            assert a and b
            touch('touchStart', a['x'] + a['width']/2, a['y'] + a['height']/2)
            page.wait_for_timeout(480)
            touch('touchMove', b['x'] + b['width']/2, b['y'] + b['height']/2)
            page.wait_for_timeout(100)
            assert page.locator('#seg-4 .touch-expression-selected').count() == 3
            page.screenshot(path=str(out / ('selection-backward.png' if reverse else 'selection-forward.png')))
            touch('touchEnd')
            page.wait_for_function('POP?.selection === "da haben einige"')
            assert page.locator('#wp-word').inner_text() == 'da haben einige'
            assert lookups[-1] == dict(selection='da haben einige', sentence='Und da haben einige geschrieben',
                                      session_id='qa', segment_index=4)
            if reverse:
                page.locator('#wp-edit').tap()
                page.wait_for_function('!$("card-panel").hidden')
                assert previews[-1] == dict(session_id='qa', segment_index=4,
                                           selection='da haben einige', pad_before=0,
                                           pad_after=0, offset=0)
                assert page.locator('#c-paraula').input_value() == 'da haben einige'
                page.set_viewport_size({'width': 320, 'height': 640})
                editor = page.locator('#card-panel').bounding_box()
                assert editor and editor['x'] >= 0 and editor['y'] >= 0
                assert editor['x'] + editor['width'] <= 320 and editor['y'] + editor['height'] <= 640
                page.set_viewport_size({'width': 412, 'height': 915})
                page.locator('#c-cancel').tap()
            else:
                page.locator('#wp-close').tap()
            page.wait_for_timeout(350)
        # Cancellation never opens another popup, and native selection handles stay absent.
        a = page.locator('#seg-4 .t').nth(1).bounding_box()
        assert a
        touch('touchStart', a['x'] + 4, a['y'] + 5)
        page.wait_for_timeout(480)
        touch('touchCancel')
        page.wait_for_timeout(100)
        assert not page.locator('#word-pop').is_visible()
        assert page.evaluate('getSelection().toString()') == ''
        # Opening audio tools resizes the transcript rather than covering it.
        page.locator('#audio-tools').tap()
        assert page.locator('#transcribe-bar').is_visible()
        page.wait_for_timeout(100)
        assert page.locator('.seg.active').is_visible()
        page.locator('#audio-tools').tap()
        page.emulate_media(reduced_motion='reduce')
        # Final sentence and orientation: controls always have their own flow space.
        page.evaluate('gotoSeg(44); V.pause()')
        page.wait_for_timeout(400)
        last = page.locator('#seg-44').bounding_box()
        ctl = page.locator('#controls').bounding_box()
        assert last and ctl and last['y'] + last['height'] <= ctl['y']
        page.set_viewport_size({'width': 915, 'height': 412})
        page.wait_for_timeout(400)
        pbox, cbox = page.locator('#side-panel').bounding_box(), page.locator('#controls').bounding_box()
        assert pbox and cbox and pbox['y'] + pbox['height'] <= cbox['y']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(out / 'landscape.png'))
        session['is_audio'] = False
        page.evaluate('openSession("qa")')
        assert not page.locator('body').evaluate('(e)=>e.classList.contains("audio-mobile")')
        assert page.locator('#video').is_visible()
        # A cold online session acquires canonical audio metadata with its fresh URL.
        session['source_type'] = 'stream'
        page.route('**/api/sessions/qa/stream-url?*', lambda r: r.fulfill(json=dict(
            url='/media/test-audio.mp3', is_audio=True, is_hls=False, heights=[], height=0)))
        page.evaluate('openSession("qa")')
        assert page.locator('body').evaluate('(e)=>e.classList.contains("audio-mobile")')
        # Empty audio still exposes transcription/SRT and condensed mode cannot throw.
        session['source_type'] = 'local'
        session['is_audio'] = True
        session['transcript'] = []
        page.evaluate('openSession("qa")')
        assert page.locator('#transcribe-bar').is_visible()
        page.evaluate('setCondensed(true); playMedia()')
        page.wait_for_timeout(300)
        page.evaluate('V.pause(); setCondensed(false)')
        page.evaluate('Object.defineProperty(V, "duration", {configurable: true, value: Infinity}); V.dispatchEvent(new Event("durationchange"))')
        assert page.locator('#seek').is_disabled(), 'unbounded live audio must not seek to Infinity'
        assert page.locator('#time-dur').inner_text() == '—'
        page.evaluate('delete V.duration; V.dispatchEvent(new Event("durationchange"))')
        session['transcript'] = segs
        # Desktop uses the unchanged stage and native mouse drag selection.
        desktop = browser.new_context(viewport={'width': 1280, 'height': 900}, service_workers='block')
        dp = desktop.new_page()
        dp.on('pageerror', lambda e: errors.append(str(e)))
        session['is_audio'] = True
        dp.route('**/api/sessions/qa', lambda r: r.fulfill(json=session))
        dp.route('**/api/lookup', lookup)
        dp.route('**/api/examples?*', lambda r: r.fulfill(json={'examples': []}))
        dp.goto(args.url)
        dp.wait_for_function('typeof openSession === "function" && SETTINGS !== null')
        dp.evaluate('openSession("qa")')
        dp.wait_for_function('V.readyState >= 2')
        assert not dp.locator('body').evaluate('(e)=>e.classList.contains("audio-mobile")')
        dp.locator('#browser-btn').click()
        dp.evaluate('setCur(0)')
        row = dp.locator('#seg-0 .t')
        a, b = row.nth(1).bounding_box(), row.nth(3).bounding_box()
        assert a and b
        dp.mouse.move(a['x'] + 1, a['y'] + a['height']/2)
        dp.mouse.down()
        dp.mouse.move(b['x'] + b['width'] - 1, b['y'] + b['height']/2, steps=8)
        dp.mouse.up()
        assert dp.evaluate('getSelection().toString().trim()') == 'da haben einige'
        # Existing desktop pipeline consumes native selection on token click.
        dp.locator('#seg-0 .t').nth(3).dispatch_event('click')
        dp.wait_for_function('POP?.selection === "da haben einige"')
        assert dp.locator('#wp-word').inner_text() == 'da haben einige'
        dp.screenshot(path=str(out / 'desktop.png'))
        dp.evaluate('closePopup()')
        dp.keyboard.press('d')
        dp.wait_for_function('CUR === 1')
        # Separately exercise the real service worker (no routed API fixtures).
        pwa = browser.new_context()
        pp = pwa.new_page()
        pp.goto(args.url)
        pp.evaluate('navigator.serviceWorker.ready.then(() => true)')
        cached = pp.evaluate('''async () => {
          const urls = ['/audio-player.js', '/audio-player.css', '/touch-selection.js'];
          return Promise.all(urls.map(async u => !!(await caches.match(u))));
        }''')
        assert all(cached), cached
        print('PASS: real service worker caches all new presentation/gesture assets')
        assert not errors, errors
        print('PASS: clock, scrub, navigation, follow, browsing, tap, forward/backward real touch, card payload, cancellation, tools, reduced motion, final line, landscape, video exclusion, desktop drag and keyboard')
        browser.close()


if __name__ == '__main__':
    main()
