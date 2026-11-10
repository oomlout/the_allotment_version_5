(() => {
  const root = document.getElementById('test-dashboard');
  if (!root) return;
  let runs = [], catalog = {}, batch = {}, revision = 0, pollFlight = null, timer, followed = null;
  const pending = new Map(), errors = new Map(), openedRuns = new Set();
  const rows = [...root.querySelectorAll('.card-test-mode')];
  const linkedCard=document.getElementById(location.hash.slice(1));
  if(linkedCard&&root.contains(linkedCard)&&linkedCard.matches('.platform-card'))linkedCard.querySelector('.card-test').open=true;
  const key = row => `${row.dataset.platform}/${row.dataset.mode}`;
  const field = (row, name) => row.querySelector(`[data-${name}]`);
  const runFor = row => runs.find(r => r.platform === row.dataset.platform && (r.mode || 'prepare') === row.dataset.mode && r.active)
    || runs.find(r => r.platform === row.dataset.platform && (r.mode || 'prepare') === row.dataset.mode);
  const batchActive = () => ['running','stopping'].includes(batch.status);
  const desktopBusy = () => batchActive() || runs.some(r => r.active) || [...pending.values()].includes('start');
  const labels = {untested:'Untested',passed:'Passed',not_passed:'Not passed',blocked:'Blocked',pending:'Queued',cancelled:'Not run',skipped:'Not run'};
  function text(node, value) { if (node.textContent !== value) node.textContent = value; }
  function message(node, value) { text(node, value || ''); node.hidden = !value; }
  function stateLabel(run) {
    if (run.current === false) return 'Instructions changed';
    if (!run.active) return {prepared:'Ready to review',inspected:'Ready to review',failed:'Failed',stopped:'Stopped',uncertain:'Needs review'}[run.status] || run.status;
    if (run.status === 'stopping') return 'Stopping…';
    return {handoff:'Click the Chrome window to use',connecting:'Opening Chrome',switching:'Countdown',countdown:'Countdown',paused:'Paused'}[run.phase] || run.operation || 'Running';
  }
  function render() {
    const busy = desktopBusy();
    for (const row of rows) {
      const id = key(row), mode = row.dataset.mode, run = runFor(row), review = catalog[row.dataset.platform]?.[mode];
      const action = pending.get(id);
      const entry = batchActive() && batch.mode === mode ? batch.items?.find(i => i.platform === row.dataset.platform) : null;
      const queued = entry?.status === 'pending';
      const active = !!run?.active;
      const current = run && run.current !== false;
      const reviewed = review?.test_id === run?.id && !!run?.id;
      let label = review?.eligible === false ? 'Blocked' : run ? stateLabel(run) : labels[review?.state] || 'Untested';
      if (review?.state === 'passed' && (!run || !active && current && (reviewed || run.approved))) label = 'Passed';
      if (reviewed && !active && current) label = labels[review.state] || label;
      row.dataset.outcome = label === 'Passed' ? 'passed' : ['Failed','Not passed'].includes(label) ? 'failed' : '';
      if (active && !openedRuns.has(run.id)) {
        row.closest('.card-test').open = true;
        openedRuns.add(run.id);
      }
      if (queued) label = 'Queued';
      if (action) label = {start:'Starting…',stop:'Stopping…',pause:'Updating…',passed:'Saving Pass…',not_passed:'Saving Not passed…'}[action];
      text(field(row,'status'),label);
      row.classList.toggle('test-running',active || action === 'start');
      row.classList.toggle('test-queued',queued);
      const start = field(row,'start');
      text(start,action === 'start' ? 'Starting…' : run ? 'Run again' : 'Run');
      start.disabled = busy || !!action || review?.eligible === false;
      const canReview = !!run && !active && current && !queued;
      const canPass = mode === 'inspect' ? run?.status === 'inspected' : run?.can_approve;
      for (const [name,verdict] of [['pass','passed'],['not-pass','not_passed']]) {
        const button = field(row,name);button.hidden = !canReview;
        button.disabled = busy || !!action || (name === 'pass' && !canPass) || (reviewed && review.state === verdict);
        button.setAttribute('aria-pressed',String(reviewed && review.state === verdict));
        button.title = busy ? 'Review after the running sequence finishes' : '';
      }
      const countdown = active && ['handoff','switching','countdown','paused'].includes(run.phase);
      field(row,'pause').hidden = !countdown || run.phase==='handoff'; field(row,'pause').disabled = !!action;
      text(field(row,'pause'),run?.phase === 'paused' ? 'Resume' : 'Pause');
      field(row,'stop').hidden = !active; field(row,'stop').disabled = !!action;
      message(field(row,'detail'),active && run.step ? `Step ${run.step} of ${run.total_steps}` : !active && canReview && busy ? 'Review when the sequence finishes.' : '');
      message(field(row,'target'),active ? [run.site_host,run.expected_account,run.target].filter(Boolean).join(' · ') : '');
      field(row,'timer').hidden = !countdown;
      message(field(row,'error'),errors.get(id) || (review?.eligible === false ? review.reason : run?.error || run?.warning || ''));
    }
    for (const card of root.querySelectorAll('.platform-card')) {
      const name = card.id.replace('destination-',''), review = catalog[name]?.prepare;
      const badge = card.querySelector('.platform-title .badge');
      if (review) {
        text(badge, labels[review.state] || 'Untested');
        badge.className = `badge ${review.state === 'passed' ? 'succeeded' : 'draft'}`;
        const enabled = card.querySelector(`[name="${name}_enabled"]`);
        enabled.disabled = review.state !== 'passed'; if (enabled.disabled) enabled.checked = false;
      }
      card.classList.toggle('test-active-card',!!card.querySelector('.test-running'));
      for (const result of card.querySelectorAll('[data-result-mode]')) {
        const outcome = card.querySelector(`[data-mode="${result.dataset.resultMode}"]`).dataset.outcome;
        result.dataset.outcome = outcome;
        result.hidden = !outcome;
        const label = `${result.dataset.resultMode === 'prepare' ? 'Prepare' : 'Inspect'}: ${outcome === 'passed' ? 'Passed' : 'Failed'}`;
        result.title = label;
        result.setAttribute('aria-label',label);
      }
    }
    root.querySelectorAll('[data-test-all]').forEach(button => {button.disabled = busy || pending.has('all');});
    const stop = root.querySelector('[data-stop-all]');stop.hidden = !batchActive();stop.disabled = pending.has('all');
    const mode = batch.mode, items = batch.items || [], runnable = items.filter(i => i.status !== 'blocked');
    const active = items.find(i => i.status === 'running');
    const done = runnable.filter(i => !['pending','running'].includes(i.status)).length;
    let summary = 'Choose Prepare or Inspect to run all untested platforms.';
    if (pending.has('all')) summary = pending.get('all') === 'start' ? 'Starting sequence…' : 'Stopping sequence…';
    else if (batchActive()) summary = `${mode === 'inspect' ? 'Inspect' : 'Prepare'} · ${done} / ${runnable.length} finished${active ? ` · ${active.name}` : ''}`;
    else if (batch.status) summary = `${batch.status === 'complete' ? 'Finished' : 'Stopped'} · ${done} / ${runnable.length} · Review each card.`;
    text(root.querySelector('[data-batch-status]'),summary);
    message(root.querySelector('[data-batch-error]'),errors.get('all') || batch.error || '');
    if (batchActive() && active && active.test_id !== followed) {
      followed = active.test_id;
      document.getElementById(`destination-${active.platform}`)?.scrollIntoView({block:'nearest',behavior:'smooth'});
    }
    updateTimers();
  }
  function updateTimers() {
    for (const row of rows) {
      const node = field(row,'timer'), run = runFor(row); if (node.hidden || !run) continue;
      const seconds = run.phase === 'paused' ? run.countdown_remaining : (Date.parse(run.countdown_until)-Date.now())/1000;
      text(node,`${Math.max(0,Math.ceil(seconds || 0))} seconds`);
      if(run.phase==='handoff')text(field(row,'start'),`Choose Chrome · ${Math.max(0,Math.ceil(seconds || 0))}s`);
    }
    const handoff=runs.find(r=>r.active&&r.phase==='handoff');
    root.querySelectorAll('[data-test-all]').forEach(button=>{
      const seconds=handoff?Math.max(0,Math.ceil((Date.parse(handoff.countdown_until)-Date.now())/1000)):0;
      text(button,batchActive()&&batch.mode===button.dataset.testAll&&handoff?`Choose Chrome · ${seconds}s`:button.dataset.testAll==='prepare'?'Prepare':'Inspect');
    });
  }
  async function jsonFetch(url, options = {}) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),15000);
    try {
      const response = await fetch(url,{cache:'no-store',...options,signal:controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Request failed.');
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('Response timed out. Check the current status before retrying. F8 stops desktop input.'); throw error; }
    finally {clearTimeout(timeout);}
  }
  function poll() {
    clearTimeout(timer);
    if (pollFlight) return pollFlight;
    const started = revision;
    pollFlight = (async () => {
      try {
        const data = await jsonFetch(root.dataset.statusUrl);
        if (started !== revision) return; // An older poll must not undo an acknowledged click.
        runs = data.runs || []; catalog = data.catalog || {}; batch = data.batch || {};
        render();
      } catch (error) {message(root.querySelector('[data-batch-error]'),`Status unavailable: ${error.message}`);}
      finally {pollFlight = null; timer = setTimeout(poll,desktopBusy() ? 1000 : 3000);}
    })();
    return pollFlight;
  }
  async function action(id, kind, url, values, accept) {
    if (pending.has(id)) return;
    pending.set(id,kind); errors.delete(id); revision++; render();
    try {
      const data = await jsonFetch(url,{method:'POST',headers:{Accept:'application/json'},body:new URLSearchParams({csrf:root.dataset.csrf,...values})});
      revision++; accept?.(data);
    } catch (error) {revision++;errors.set(id,error.message);}
    finally {pending.delete(id);render();void poll();}
  }
  for (const row of rows) {
    const platform = row.dataset.platform, mode = row.dataset.mode, id = key(row);
    field(row,'start').onclick = () => action(id,'start',root.dataset.startUrl,{platform,intent:`${mode}_test`},data => {
      runs.unshift({id:data.id,platform,mode,active:true,current:true,status:'queued',phase:'connecting'});
    });
    for (const [name,verdict] of [['pass','passed'],['not-pass','not_passed']]) field(row,name).onclick = () => {
      const run = runFor(row); if (!run || desktopBusy()) return;
      void action(id,verdict,`${root.dataset.baseUrl}${run.id}/review`,{verdict},data => {
        const record = data.review;
        if (!record || record.test_id !== run.id || record.verdict !== verdict) throw new Error('Review was not confirmed. Refresh status.');
        catalog[platform] ||= {};catalog[platform][mode] = {...catalog[platform][mode],...record,state:record.verdict};
        run.approved = mode === 'prepare' && verdict === 'passed';
        if (run.approved) document.getElementById(`destination-${platform}`).querySelector(`[name="${platform}_enabled"]`).checked = true;
      });
    };
    field(row,'stop').onclick = () => {
      const run=runFor(row);if (!run) return;
      void action(id,'stop',`${root.dataset.baseUrl}${batchActive() ? 'all-untested' : run.id}/stop`,{},() => {run.status='stopping';});
    };
    field(row,'pause').onclick = () => {
      const run=runFor(row);if (!run) return;
      const next=run.phase === 'paused' ? 'resume' : 'pause';
      void action(id,'pause',`${root.dataset.baseUrl}${run.id}/countdown`,{action:next},() => {run.phase=next === 'pause' ? 'paused' : 'switching';});
    };
  }
  root.querySelectorAll('[data-test-all]').forEach(button => {button.onclick = () => action('all','start',`${root.dataset.baseUrl}all-untested/start`,{mode:button.dataset.testAll},data => {batch=data.batch;followed=null;});});
  root.querySelector('[data-stop-all]').onclick = () => action('all','stop',`${root.dataset.baseUrl}all-untested/stop`,{},() => {batch.status='stopping';});
  setInterval(updateTimers,250);
  void poll();
})();
