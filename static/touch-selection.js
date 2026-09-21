/* Touch-only expression gestures; lookup and mining belong to the caller. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TouchSelection = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function phrase(tokens, anchor, current) {
    const lo = Math.min(anchor, current), hi = Math.max(anchor, current);
    const hasWs = tokens[0] && tokens[0].ws !== undefined;
    let text = '';
    for (let i = lo; i <= hi; i++) {
      if (i > lo) text += hasWs ? (tokens[i - 1].ws || '') : (tokens[i].is_word ? ' ' : '');
      text += tokens[i].t;
    }
    return text;
  }
  function bind(container, { tokens, onSelect, onInteraction = () => {} }) {
    const doc = container.ownerDocument, win = doc.defaultView;
    let gesture = null, observer = null, transient = [];
    let touchUntil = 0, clickUntil = 0;
    const now = () => win.Date.now();
    function pointerDown(ev) {
      if (ev.pointerType === 'mouse') {
        cancel();
        touchUntil = clickUntil = 0;
        container.classList.remove('touch-selection-touch');
      } else if (ev.pointerType === 'touch' && tokenAt(ev.target)) {
        container.classList.add('touch-selection-touch');
      }
    }
    function contextMenu(ev) {
      if (container.classList.contains('touch-selection-touch') && tokenAt(ev.target)) ev.preventDefault();
    }
    function click(ev) {
      if (now() >= clickUntil || ev.detail === 0 || ev.sourceCapabilities?.firesTouchEvents === false || !tokenAt(ev.target)) return;
      clickUntil = 0;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }
    function listen(target, type, fn, options) {
      target.addEventListener(type, fn, options);
      transient.push(() => target.removeEventListener(type, fn, options));
    }
    function valid() { return gesture && container.isConnected && gesture.el.isConnected && container.contains(gesture.el); }
    function cancel() { finish(false); }
    function tokenAt(node) {
      const el = node && node.closest && node.closest('.t');
      if (!el || !container.contains(el)) return null;
      const raw = el.dataset.tokenIndex;
      if (!/^(0|[1-9]\d*)$/.test(raw || '')) return null;
      const index = Number(raw);
      return index < tokens.length ? { el, index } : null;
    }
    function paint() {
      const lo = Math.min(gesture.index, gesture.current), hi = Math.max(gesture.index, gesture.current);
      for (const el of container.querySelectorAll('.t')) {
        const token = tokenAt(el);
        el.classList.toggle('touch-expression-selected', !!token && token.index >= lo && token.index <= hi);
      }
    }
    function finish(commit) {
      if (!gesture) return;
      const g = gesture;
      gesture = null;
      touchUntil = now() + 1000;
      if (g.active) clickUntil = now() + 800;
      win.clearTimeout(g.timer);
      for (const remove of transient) remove();
      transient = [];
      if (observer) { observer.disconnect(); observer = null; }
      for (const el of container.querySelectorAll('.t')) el.classList.remove('touch-expression-selected');
      onInteraction(false);
      if (commit && g.active && container.isConnected && g.el.isConnected && container.contains(g.el)) {
        onSelect(phrase(tokens, g.index, g.current), g.el);
      }
    }
    function move(ev) {
      if (!gesture) return;
      if (!valid() || ev.touches.length !== 1) { cancel(); return; }
      const t = Array.from(ev.touches).find(t => t.identifier === gesture.id);
      if (!t) return;
      if (!gesture.active && Math.hypot(t.clientX - gesture.x, t.clientY - gesture.y) > 10) {
        finish(false);
        return;
      }
      if (gesture.active) {
        ev.preventDefault();
        const hit = tokenAt(doc.elementFromPoint(t.clientX, t.clientY));
        if (hit) { gesture.current = hit.index; paint(); }
      }
    }
    function end(ev) {
      if (gesture && Array.from(ev.changedTouches).some(t => t.identifier === gesture.id)) finish(true);
    }
    function start(ev) {
      cancel();
      clickUntil = 0;
      touchUntil = now() + 1000;
      const hit = tokenAt(ev.target);
      if (!hit || ev.touches.length !== 1) return;
      container.classList.add('touch-selection-touch');
      const t = ev.touches[0];
      gesture = { ...hit, current:hit.index, id:t.identifier, x:t.clientX, y:t.clientY, active:false };
      listen(doc, 'touchmove', move, {passive:false});
      listen(doc, 'touchend', end);
      listen(doc, 'touchcancel', cancel);
      listen(doc, 'expression-reset', cancel);
      listen(doc, 'touchstart', ev => { if (ev.touches.length !== 1) cancel(); }, {passive:true});
      listen(doc, 'keydown', ev => { if (ev.key === 'Escape') cancel(); });
      // Chrome releases implicit pointer capture before delivering touchend.
      listen(doc, 'pointerup', ev => { if (ev.pointerType === 'touch' && gesture) gesture.releasing = true; });
      listen(doc, 'lostpointercapture', () => { if (gesture && !gesture.releasing) cancel(); });
      listen(doc, 'pointercancel', cancel);
      listen(win, 'blur', cancel);
      listen(win, 'pagehide', cancel);
      listen(doc, 'visibilitychange', () => { if (doc.hidden) cancel(); });
      observer = new win.MutationObserver(() => { if (!valid()) cancel(); });
      observer.observe(doc.documentElement, {childList:true, subtree:true});
      gesture.timer = win.setTimeout(() => {
        if (!valid()) { cancel(); return; }
        gesture.active = true;
        paint();
      }, 420);
      onInteraction(true);
    }
    container.addEventListener('touchstart', start, {passive:true});
    container.addEventListener('click', click, true);
    container.addEventListener('pointerdown', pointerDown);
    container.addEventListener('contextmenu', contextMenu);
    return {
      ignoreHover(ev) { return !!gesture || now() < touchUntil || !!ev?.sourceCapabilities?.firesTouchEvents; },
      destroy() {
        cancel();
        container.removeEventListener('touchstart', start);
        container.removeEventListener('click', click, true);
        container.removeEventListener('pointerdown', pointerDown);
        container.removeEventListener('contextmenu', contextMenu);
        container.classList.remove('touch-selection-touch');
      }
    };
  }
  return { phrase, bind };
});
