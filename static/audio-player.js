/* Audio presentation only: playback and mining stay in app.js. */
(function (root) {
  function isAudioMode(session, smallScreen) {
    return !!(session && session.source_type !== 'text' && session.is_audio === true && smallScreen);
  }
  class FollowState {
    constructor() { this.reset(); }
    reset() { this.index = -1; this.browsing = false; this.blockers = new Set(); }
    get canFollow() { return !this.browsing && !this.blockers.size; }
    change(index) {
      const changed = index !== this.index;
      this.index = index;
      return changed && this.canFollow;
    }
    browse() { this.browsing = true; }
    resume() { this.browsing = false; }
    block(reason, active) { active ? this.blockers.add(reason) : this.blockers.delete(reason); }
  }
  function mount({session, current, renderCurrent}) {
    const $ = id => document.getElementById(id);
    const viewport = matchMedia('(max-width: 700px), (pointer: coarse) and (max-width: 1024px)');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const state = new FollowState();
    const panel = $('side-panel'), context = $('audio-current');
    let enabled = false, wasHidden = true, start = null, frame = 0;
    let previewIndex = -2;
    function blocked() {
      return !$('word-pop').hidden || !$('card-panel').hidden;
    }
    function paintContext() {
      $('audio-follow').classList.toggle('on', !state.browsing);
      $('audio-follow').setAttribute('aria-pressed', String(!state.browsing));
      // Freeze the entire preview, not just its tokens, while a finger holds it.
      // A subtitle gap must not remove the target before touchend/touchcancel.
      if (enabled && state.blockers.has('expression')) return;
      context.hidden = !enabled || !state.browsing || current() < 0;
      if (!context.hidden && previewIndex !== current()) {
        previewIndex = current();
        renderCurrent($('audio-current-text'), previewIndex);
      }
    }
    function follow(smooth = false) {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!enabled || !state.canFollow || blocked()) return;
        const row = $('seg-' + current());
        if (!row) return;
        const r = row.getBoundingClientRect(), p = panel.getBoundingClientRect();
        const top = panel.scrollTop + r.top - p.top - Math.max(12, (panel.clientHeight - r.height) * .42);
        panel.scrollTo({top, behavior: smooth && !reduced.matches ? 'smooth' : 'instant'});
      });
    }
    function browse() {
      if (!enabled || state.blockers.has('expression')) return;
      if (!state.browsing) panel.scrollTo({top: panel.scrollTop, behavior: 'instant'});
      state.browse();
      paintContext();
    }
    function resume() { state.resume(); paintContext(); follow(); }
    function sync() {
      const next = isAudioMode(session(), viewport.matches) && !$('player').hidden;
      if (next !== enabled) {
        enabled = next;
        document.body.classList.toggle('audio-mobile', enabled);
        if (enabled) {
          wasHidden = panel.hidden;
          panel.hidden = false;
          // Audio always opens on the interactive transcript, not the word list.
          $('tab-subs').click();
        } else { panel.hidden = wasHidden; }
      }
      $('audio-title').textContent = session()?.title || '';
      $('player').classList.toggle('audio-no-transcript', !(session()?.transcript?.length));
      paintContext(); follow();
    }
    panel.addEventListener('pointerdown', e => {
      if (!enabled) return;
      start = {x: e.clientX, y: e.clientY};
      state.block('finger', true);
      // Stop an in-flight smooth scroll before a word is held.
      panel.scrollTo({top: panel.scrollTop, behavior: 'instant'});
    });
    panel.addEventListener('pointermove', e => {
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) browse();
    });
    function release() {
      start = null; state.block('finger', false);
      // Delay until the click handler has opened its popup.
      follow();
    }
    document.addEventListener('pointerup', release);
    document.addEventListener('pointercancel', release);
    // Touch scrolling may cancel pointer events before momentum starts.
    panel.addEventListener('touchmove', browse, {passive: true});
    panel.addEventListener('wheel', browse, {passive: true});
    panel.addEventListener('keydown', e => {
      if (enabled && ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) {
        // Focused transcript navigation must not reach global replay/subtitle shortcuts.
        e.stopPropagation();
        browse();
      }
    });
    document.addEventListener('expression-interaction', e => {
      state.block('expression', e.detail.active);
      if (!e.detail.active) { paintContext(); follow(); }
    });
    $('audio-follow').onclick = resume;
    $('audio-tools').onclick = () => {
      const expanded = $('player').classList.toggle('audio-tools-open');
      $('audio-tools').setAttribute('aria-expanded', String(expanded));
    };
    const observer = new ResizeObserver(() => follow());
    observer.observe(panel);
    const popObserver = new MutationObserver(() => { if (!blocked()) follow(); });
    for (const id of ['word-pop', 'card-panel']) popObserver.observe($(id), {attributes: true, attributeFilter: ['hidden']});
    viewport.addEventListener('change', sync);
    return {
      get enabled() { return enabled; },
      sync,
      reset() {
        state.reset(); start = null; previewIndex = -2;
        $('player').classList.remove('audio-tools-open');
        $('audio-tools').setAttribute('aria-expanded', 'false');
        sync();
      },
      changed(i) { const scroll = state.change(i); paintContext(); if (scroll) follow(true); },
      refresh() { previewIndex = -2; paintContext(); follow(); },
      seek() { resume(); },
    };
  }
  const api = {isAudioMode, FollowState, mount};
  if (typeof module !== 'undefined') module.exports = api;
  root.AudioPlayer = api;
})(typeof window !== 'undefined' ? window : globalThis);
