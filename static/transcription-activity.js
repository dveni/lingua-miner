/* Server-owned transcription queue. No per-job polling loops or blocking toasts. */
(function (root) {
  function mount({api, t, session, openSession}) {
    const $ = id => document.getElementById(id);
    const host = $('transcription-activity'), toggle = $('transcription-toggle');
    const panel = $('transcription-panel'), list = $('transcription-list');
    const home = host.parentElement;
    let jobs = [], timer, refreshPromise = null, refreshAgain = false, offline = false;
    const active = job => ['queued', 'running'].includes(job.status);
    const submissions = new Set();
    function syncButton() {
      const sid = session()?.id;
      $('transcribe-btn').disabled = submissions.has(sid) || jobs.some(j => active(j) && j.session_id === sid);
    }
    function setSubmitting(sid, pending) {
      if (pending) submissions.add(sid);
      else submissions.delete(sid);
      syncButton();
    }
    function position() {
      const mobile = document.body.classList.contains('audio-mobile');
      const parent = mobile ? $('player-top') : home;
      if (host.parentElement !== parent) parent.append(host);
      if (!panel.hidden) {
        const rect = toggle.getBoundingClientRect();
        panel.style.top = `${rect.bottom + 8}px`;
        panel.style.maxHeight = `calc(100dvh - ${rect.bottom + 24}px - env(safe-area-inset-bottom))`;
      }
    }
    function close(focus = false) {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      if (focus) toggle.focus();
    }
    function render() {
      const count = jobs.filter(active).length;
      $('transcription-count').textContent = String(count);
      $('transcription-count').hidden = !count;
      toggle.classList.toggle('working', count > 0);
      toggle.title = t('activity.title');
      toggle.setAttribute('aria-label', t('activity.title') + (count ? ` (${count})` : ''));
      $('transcription-heading').textContent = t('activity.title');
      $('transcription-close').setAttribute('aria-label', t('activity.close'));
      $('transcription-note').textContent = t('activity.note');
      $('transcription-empty').hidden = jobs.length > 0;
      $('transcription-empty').textContent = t('activity.empty');
      $('transcription-offline').hidden = !offline;
      $('transcription-offline').textContent = t('activity.offline');
      const keep = new Set(jobs.map(j => j.id));
      for (const row of [...list.children]) if (!keep.has(row.dataset.id)) row.remove();
      const ordered = [...jobs].sort((a, b) => {
        const rank = j => j.status === 'running' ? 0 : j.status === 'queued' ? 1 : 2;
        return rank(a) - rank(b) || (a.queue_position || 0) - (b.queue_position || 0);
      });
      ordered.forEach((job, index) => {
        let row = [...list.children].find(el => el.dataset.id === job.id);
        if (!row) {
          row = document.createElement('li');
          row.dataset.id = job.id;
          // Dynamic server strings below use textContent, never HTML.
          row.innerHTML = '<button class="transcription-name" type="button"></button><div class="transcription-status"></div><progress max="1"></progress><small></small>';
          row.querySelector('button').onclick = () => { close(); openSession(job.session_id); };
        }
        if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
        row.dataset.status = job.status;
        row.querySelector('button').textContent = job.title;
        const pct = Math.round(Math.max(0, Math.min(1, Number(job.progress) || 0)) * 100);
        const label = job.status === 'queued' ? t('activity.queued', job.queue_position)
          : job.status === 'running' ? t('activity.running', pct)
          : job.status === 'done' ? t('activity.done') : t('activity.failed');
        row.querySelector('.transcription-status').textContent = label;
        const bar = row.querySelector('progress');
        bar.hidden = job.status !== 'running';
        bar.value = pct / 100;
        bar.setAttribute('aria-label', job.title + ': ' + label);
        const key = job.key || job.message_key;
        row.querySelector('small').textContent = job.status === 'error'
          ? (job.error || job.message || t('activity.failed'))
          : job.status === 'running' ? (key ? t(key, ...(job.args || job.message_args || [])) : job.message || '')
          : job.model || '';
      });
      syncButton();
      position();
    }
    function refresh() {
      if (refreshPromise) {
        // A request made during a read may follow a POST that read cannot see.
        refreshAgain = true;
        return refreshPromise;
      }
      clearTimeout(timer);
      refreshPromise = (async () => {
        do {
          refreshAgain = false;
          try {
            const data = await api('/api/transcriptions');
            if (!Array.isArray(data.jobs)) throw new Error('Invalid queue response');
            jobs = data.jobs;
            offline = false;
          } catch (_) { offline = true; }
          render();
        } while (refreshAgain);
      })().finally(() => {
        refreshPromise = null;
        timer = setTimeout(refresh, jobs.some(active) || !panel.hidden ? 2000 : 10000);
      });
      return refreshPromise;
    }
    toggle.onclick = () => {
      const show = panel.hidden;
      panel.hidden = !show;
      toggle.setAttribute('aria-expanded', String(show));
      if (show) { render(); refresh(); $('transcription-close').focus(); }
    };
    $('transcription-close').onclick = () => close(true);
    document.addEventListener('pointerdown', e => {
      if (!host.contains(e.target) && !panel.contains(e.target)) close();
    });
    // Avoid player keyboard shortcuts while interacting with the dropdown.
    document.addEventListener('keydown', e => {
      if (!host.contains(e.target) && !panel.contains(e.target) && (panel.hidden || e.key !== 'Escape')) return;
      e.stopImmediatePropagation();
      if (e.key === 'Escape') { e.preventDefault(); close(true); }
    }, true);
    document.addEventListener('focusin', e => {
      if (!panel.contains(e.target) && !host.contains(e.target)) close();
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, {passive: true});
    new MutationObserver(position).observe(document.body, {attributes: true, attributeFilter: ['class']});
    refresh();
    return {refresh, syncButton, setSubmitting};
  }
  root.TranscriptionActivity = {mount};
})(window);
