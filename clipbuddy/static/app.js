/* =====================================================================
   Clip Buddy — UI ("Suite" design), wired to the local API.
   App state · keyboard · moment timeline · real <video> · four screens.
   ===================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.6l2.8 6.1 6.6.7-4.9 4.5 1.4 6.5L12 17.1l-5.9 3.3 1.4-6.5-4.9-4.5 6.6-.7z"/></svg>';
const QUICK_TAGS = ['clutch', 'funny', 'fail', 'friends', 'fast', 'for later'];
const SESSION_GAP = 2 * 3600;
const now = () => Date.now() / 1000;

const api = async (url, body) => {
  // X-Clip-Buddy: the server only accepts changes carrying it (other websites can't add it)
  const r = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Clip-Buddy': '1' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

/* ---------- Formatting helpers ---------- */
const F = {
  pad: (n, w = 2) => String(Math.floor(n)).padStart(w, '0'),
  time(s) { s = Math.max(0, s || 0); const m = Math.floor(s / 60), r = Math.floor(s % 60); return `${m}:${F.pad(r)}`; },
  tc(s, fps = 60) { s = Math.max(0, s || 0); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = Math.floor(s % 60), f = Math.floor((s % 1) * fps); return `${F.pad(h)}:${F.pad(m)}:${F.pad(r)}:${F.pad(f)}`; },
  gb: b => (b / 1e9).toFixed(1),
  size(b) { b = b || 0; return b >= 0.95e9 ? (b / 1e9).toFixed(1) + ' GB' : Math.round(b / 1e6) + ' MB'; },
  len(s) { return s < 10 ? s.toFixed(1) + ' s' : F.time(s); },
  d: ts => new Date(ts * 1000),
  day: ts => F.d(ts).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).replace(',', ''),
  date: ts => F.d(ts).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }).replace(',', ''),
  clock: ts => { const d = F.d(ts); return `${F.pad(d.getHours())}:${F.pad(d.getMinutes())}`; },
  ago(ts) { if (!ts) return 'in a moment'; const d = (now() - ts) / 86400; if (d < 1 / 24) return 'just now'; if (d < 1) return Math.round(d * 24) + ' h ago'; if (d < 14) return Math.round(d) + (Math.round(d) === 1 ? ' day ago' : ' days ago'); return Math.round(d / 7) + ' weeks ago'; },
  // what the server's rename() will produce
  renamed(c) {
    const m = c.name.match(/^.+? \d{4}\.\d\d\.\d\d - \d\d\.\d\d\.\d\d(?:\.\d\d)?(?:\.DVR)?/);
    const base = m ? m[0] : c.name.replace(/\.mp4$/i, '').split(' - ')[0];
    return `${base} - ${c.title.trim().replace(/[<>:"/\\|?*]/g, '')}.mp4`;
  },
  codec: v => ({ h264: 'H.264', hevc: 'HEVC', av1: 'AV1' }[v] || String(v || '?').toUpperCase()),
  spec(c) { return `${c.height}p${Math.round(c.fps || 0)} ${F.codec(c.vcodec)} · ${Math.round(c.size * 8 / Math.max(c.duration, .1) / 1e6)} Mbit/s`; },
  kind: c => c.kind === 'replay' ? 'Instant replay' : 'Manual recording',
  plural: (n, w, p = w + 's') => `${n} ${n === 1 ? w : p}`,
  eta(sec) { const m = Math.round(sec / 60); return m < 1 ? 'under a minute' : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; },
};
const poster = c => `/thumb/${c.id}/poster?v=${c.size}`;
const strip = c => `/thumb/${c.id}/sprite?v=${c.size}`;

/* ---------- App state ---------- */
const QUEUES = [
  { key: 'oldest', label: 'Unreviewed · oldest first' },
  { key: 'newest', label: 'Unreviewed · newest first' },
  { key: 'biggest', label: 'Unreviewed · biggest first' },
  { key: 'junk', label: 'Likely junk first' },
  { key: 'expired', label: 'Expired 30-day clips' },
];

const App = {
  clips: [], trash: [], byId: {}, chains: [], sessions: [],
  summary: { drive: null, freed: 0, detection: { matched: 0, of: 0 }, background: { doing: null, toAnalyze: 0, toIndex: 0, analyzedRun: 0, eta: 0, queued: 0, failed: [] } },
  route: 'review', queueKey: 'oldest', customLabel: '', queue: [], pos: 0,
  speed: 1, muted: false, uiState: {}, undoStack: [], _l: {},
  on(ev, fn) { (this._l[ev] ??= []).push(fn); },
  emit(ev, a) { (this._l[ev] || []).forEach(f => f(a)); },

  /* ---- the <video> drives time ---- */
  video: null,
  get t() { return this.video.currentTime || 0; },
  set t(v) { this.video.currentTime = v; },
  get playing() { return !this.video.paused; },
  set playing(v) { if (v) this.video.play().catch(() => {}); else this.video.pause(); },

  async init() {
    this.video = $('#vid');
    ['play', 'pause', 'ended'].forEach(ev => this.video.addEventListener(ev, () => this.emit('transport')));
    const st = await api('/api/setup');
    if (!st.configured) await Setup.open(false);
    await this.refresh();
    this.loop();
    this.setQueue('oldest');
    this.poll();
  },

  /* ---- data from the server ---- */
  async refresh() {
    const [clips, chains, trash] = await Promise.all([api('/api/clips'), api('/api/chains'), api('/api/clips?status=trash')]);
    this.clips = clips.map(c => Object.assign(c, {
      tags: (c.tags || '').split(',').map(t => t.trim()).filter(Boolean),
      title: c.title || '', rating: c.rating || 0, analyzed: c.analyzed === 1, failed: c.analyzed === -1,
      quiet: !!c.quiet, trimmed: !!c.trimmed, compressed: !!c.compressed, busy: !!c.busy, sug_whole: !!c.sug_whole,
      analysis: c.analysis || { activity: [], alt: null },
    })).sort((a, b) => a.start_ts - b.start_ts);
    this.byId = Object.fromEntries(this.clips.map(c => [c.id, c]));
    this.chains = chains;
    this.trash = trash.map(t => ({ id: t.id, file: t.name, note: t.note || 'deleted', size: t.size, when: t.trashed_at, clip_id: t.id, pending: !t.trashed_at, protected: t.protected }));
    this.group();
  },
  async refreshStats() {
    const s = await api('/api/stats');
    const drive = (s.settings.inbox.match(/^[A-Za-z]:/) || [''])[0];
    this.summary.drive = s.disk ? { label: drive, total: s.disk.total, free: s.disk.free } : null;
    this.summary.freed = s.freed || 0;
    this.summary.detection = { matched: s.suggestions.hits, of: s.suggestions.n };
    this.summary.settings = s.settings;
  },
  // while background work is going on, reload when it changes; keep the clip on screen as it is
  async poll() {
    try {
      const st = await api('/api/status');
      const b = this.summary.background;
      b.doing = st.doing; b.toAnalyze = st.to_analyze; b.queued = st.jobs.filter(j => j.state === 'pending').length; b.failed = st.errors;
      b.toIndex = st.to_index || 0; b.analyzedRun = st.analyzed_run || 0; b.eta = st.eta_seconds || 0;
      b.jobsLeft = st.jobs.length; b.jobsEta = st.jobs_eta_seconds || 0;
      const sig = `${st.jobs.map(j => j.id + j.state).join(',')}|${st.to_analyze}|${st.to_index}|${st.trash.n}|${st.errors.length}`;
      if (sig !== this._sig) {
        const first = this._sig === undefined; this._sig = sig;
        if (!first) {
          const cur = this.clip, wasAnalyzing = cur && !cur.analyzed;
          await this.refresh();
          if (this.queueKey !== 'custom') {
            // rebuild so clips that arrived while the folder was being read join the queue; stay on the same clip
            const cur = this.queue[this.pos];
            this.queue = this.buildQueue(this.queueKey);
            const at = this.queue.indexOf(cur);
            if (at >= 0) this.pos = at; else if (this.pos >= this.queue.length) this.pos = Math.max(0, this.queue.length - 1);
            if (!cur && this.queue.length) this.load();
          }
          if (cur && wasAnalyzing && this.byId[cur.id]?.analyzed) { delete this.uiState[cur.id]; this.emit('change-soft'); }
        }
        if (this.route === 'space') await this.refreshStats();
        this.emit('data');
      }
      renderNav();
    } catch { $('#bgline').innerHTML = '<span class="fail">Clip Buddy server is not running — start Clip Buddy.bat</span>'; }
    setTimeout(() => this.poll(), 2000);
  },

  /* sessions = same game, gaps < 2 h · chains = back-to-back saves, from the server (same rule the join uses) */
  group() {
    const byGame = {}; this.sessions = [];
    this.clips.forEach(c => { delete c._chain; (byGame[c.game] ??= []).push(c); });
    Object.values(byGame).forEach(list => {
      list.sort((a, b) => a.start_ts - b.start_ts);
      let sess = [];
      const flush = () => { if (!sess.length) return; const S = { id: sess[0].id, game: sess[0].game, start: sess[0].start_ts, clips: sess }; sess.forEach(c => c._sess = S); this.sessions.push(S); sess = []; };
      list.forEach((c, i) => { if (i && c.start_ts - (list[i - 1].start_ts + list[i - 1].duration) > SESSION_GAP) flush(); sess.push(c); });
      flush();
    });
    this.chains.forEach(ch => ch.ids.forEach((id, i) => { const c = this.byId[id]; if (c) c._chain = { index: i + 1, count: ch.ids.length, ids: ch.ids }; }));
    this.sessions.sort((a, b) => b.start - a.start);
  },
  sessionInfo(c) {
    const live = c._sess.clips;
    return { day: F.day(c._sess.start), date: F.date(c._sess.start), index: live.indexOf(c) + 1, count: live.length, size: live.reduce((s, x) => s + x.size, 0), unreviewed: live.filter(x => x.status === 'new').length };
  },

  /* derived */
  get library() { return this.clips; },
  expired: c => c.status === 'temp' && c.expires && c.expires < now(),
  isJunk(c) { return c.status === 'new' && (c.quiet || c.duration < 5); },
  junkScore(c) { return (c.duration < 5 ? 2 : 0) + (c.quiet ? 1 : 0) - (c.strength ?? 5) / 10; },
  statusOf(c) {
    if (c.busy) return { key: 'working', label: 'Working…' };
    if (this.expired(c)) return { key: 'expired', label: '30 days up' };
    if (c.status === 'temp') return { key: 'temp', label: `30-day · ${Math.ceil((c.expires - now()) / 86400)} d left` };
    if (c.status === 'keep') return { key: 'keep', label: 'Kept' };
    return { key: 'new', label: 'Unreviewed' };
  },
  get clip() { return this.byId[this.queue[this.pos]] || null; },
  ui(c) {
    if (!this.uiState[c.id]) {
      const whole = c.sug_whole || c.trimmed || (c.status !== 'new' && c.keep_start == null) || !c.analyzed;
      this.uiState[c.id] = { s: c.keep_start ?? c.sug_start ?? 0, e: c.keep_end ?? c.sug_end ?? c.duration, whole };
    }
    return this.uiState[c.id];
  },

  /* queues */
  buildQueue(key) {
    const fresh = this.clips.filter(c => c.status === 'new' && !c.busy);
    switch (key) {
      case 'newest': return fresh.sort((a, b) => b.start_ts - a.start_ts).map(c => c.id);
      case 'biggest': return fresh.sort((a, b) => b.size - a.size).map(c => c.id);
      case 'junk': return fresh.sort((a, b) => this.junkScore(b) - this.junkScore(a)).map(c => c.id);
      case 'expired': return this.clips.filter(c => this.expired(c)).map(c => c.id);
      default: return fresh.map(c => c.id);
    }
  },
  setQueue(key) { this.queueKey = key; this.queue = this.buildQueue(key); this.pos = 0; this.load(); },
  setCustomQueue(ids, label) { this.queueKey = 'custom'; this.customLabel = label; this.queue = ids.slice(); this.pos = 0; this.load(); },
  openClip(id) {
    const i = this.queue.indexOf(id);
    if (i >= 0) { this.pos = i; this.load(); }
    else { const c = this.byId[id]; this.setCustomQueue([id], `Opened: ${c.title || F.clock(c.start_ts) + ' ' + c.game}`); }
    this.go('review');
  },
  load() {
    const c = this.clip, v = this.video;
    if (c) {
      const src = `/media/${c.id}?v=${c.size}`;
      if (v.dataset.src !== src) {
        const u = this.ui(c), start = u.whole ? (c.analyzed && c.duration > 30 && c.peak != null ? Math.max(0, c.peak - 8) : 0) : Math.max(0, u.s - 3);
        v.dataset.src = src; v.src = src; v.playbackRate = this.speed; v.muted = this.muted;
        v.onloadedmetadata = () => { v.currentTime = start; if (this.route === 'review') v.play().catch(() => {}); };
      }
    } else { v.pause(); v.removeAttribute('src'); delete v.dataset.src; v.load(); }
    // the clip on screen (and the next two) jump the analysis line if they still need it
    const waiting = this.queue.slice(this.pos, this.pos + 3).filter(id => this.byId[id] && !this.byId[id].analyzed && !this.byId[id].failed);
    if (waiting.length) api('/api/prioritize', { ids: waiting }).catch(() => {});
    this.emit('change');
  },
  go(route) {
    this.route = route;
    if (route !== 'review') this.playing = false; else if (this.clip) this.playing = true;
    if (route === 'space' || route === 'trash') this.refreshStats().then(() => this.emit('data'));
    this.emit('route'); this.emit('transport');
  },

  /* decisions: the server waits 8 s before touching the file, so U can cancel */
  async decide(kind) {
    const c = this.clip; if (!c) return;
    if (c.busy) return this.toast('This clip is being processed', 'undo');
    const u = this.ui(c), whole = u.whole;
    try {
      await api(`/api/clip/${c.id}/decide`, { action: kind, rating: c.rating, title: c.title, tags: c.tags.join(', '), start: whole ? null : u.s, end: whole ? null : u.e });
    } catch (e) { return this.toast(e.message, 'trash'); }
    this.undoStack.push({ type: 'decide', id: c.id, index: this.pos, prev: { status: c.status, expires: c.expires, keep_start: c.keep_start, keep_end: c.keep_end } });
    c.status = kind;
    if (kind === 'temp') c.expires = now() + 30 * 86400;
    if (kind !== 'trash') { c.keep_start = whole ? null : u.s; c.keep_end = whole ? null : u.e; }
    if (kind === 'trash') { delete this.byId[c.id]; this.clips = this.clips.filter(x => x !== c); this.trash.unshift({ id: c.id, file: c.name, note: 'deleted', size: c.size, when: null, clip_id: c.id, pending: true }); this.group(); }
    this.queue.splice(this.pos, 1);
    if (this.pos >= this.queue.length) this.pos = Math.max(0, this.queue.length - 1);
    this.emit('press', kind);
    this.toast({ keep: 'Kept', temp: 'Kept for 30 days', trash: 'Moved to Trash' }[kind], kind, true);
    this.load(); this.emit('data');
  },
  skip() {
    if (!this.queue.length) return;
    this.undoStack.push({ type: 'skip', from: this.pos });
    this.pos = (this.pos + 1) % this.queue.length;
    this.emit('press', 'skip'); this.toast('Skipped', 'skip', true); this.load();
  },
  async undo() {
    const u = this.undoStack.pop();
    this.emit('press', 'undo');
    if (!u) return this.toast('Nothing to undo', 'undo');
    if (u.type === 'skip') { this.pos = u.from; this.load(); return this.toast('Undone', 'undo'); }
    try { await api('/api/undo', { ids: u.type === 'bulk' ? u.ids : [u.id] }); }
    catch (e) { return this.toast(e.message, 'undo'); }
    await this.refresh();
    if (u.type === 'decide' && this.byId[u.id]) {
      delete this.uiState[u.id];
      if (this.route === 'review') { this.queue.splice(Math.min(u.index, this.queue.length), 0, u.id); this.pos = this.queue.indexOf(u.id); this.load(); }
    } else if (u.type === 'bulk' && this.queueKey !== 'custom') {
      const cur = this.queue[this.pos]; this.queue = this.buildQueue(this.queueKey); this.pos = Math.max(0, this.queue.indexOf(cur)); this.load();
    }
    this.toast('Undone', 'undo'); this.emit('data');
  },
  async trashMany(ids) {
    await api('/api/bulk', { action: 'trash', ids });
    this.undoStack.push({ type: 'bulk', ids });
    await this.refresh();
    this.queue = this.queue.filter(id => this.byId[id]); if (this.pos >= this.queue.length) this.pos = Math.max(0, this.queue.length - 1);
    this.toast(`${F.plural(ids.length, 'clip')} moved to Trash`, 'trash', true); this.load(); this.emit('data');
  },
  async restore(tid) {
    await api('/api/bulk', { action: 'restore', ids: [+tid] });
    this.toast('Restoring…', 'keep');
  },
  async emptyTrash(unknown = false) {
    const bytes = this.trash.filter(t => !t.pending && (unknown ? t.protected === 'unknown' : !t.protected)).reduce((s, t) => s + t.size, 0);
    await api('/api/trash/empty', { unknown });
    this.undoStack = this.undoStack.filter(u => u.type === 'skip');
    this.toast(`Deleting for good · ${F.size(bytes)}`, 'trash');
  },
  compressible() { return this.clips.filter(c => c.status === 'keep' && c.vcodec === 'h264' && !c.compressed && !c.busy && !(c.note || '').startsWith('Not compressed')); },
  async compressAll() {
    const list = this.compressible(); if (!list.length) return;
    await api('/api/bulk', { action: 'compress', ids: list.map(c => c.id) });
    this.toast(`Compressing ${F.plural(list.length, 'clip')} in the background…`, 'keep');
  },
  async join(ids) {
    if (ids.some(id => this.byId[id]?.busy)) return;
    try { await api('/api/merge', { ids }); } catch (e) { return this.toast(e.message, 'trash'); }
    ids.forEach(id => { if (this.byId[id]) this.byId[id].busy = true; });
    if (this.clip && ids.includes(this.clip.id)) { this.queue = this.queue.filter(id => !ids.includes(id)); if (this.pos >= this.queue.length) this.pos = Math.max(0, this.queue.length - 1); this.load(); }
    this.toast(`Joining ${ids.length} clips… the result shows up as one new clip`, 'keep'); this.emit('data');
  },
  toast(msg, kind, undo = false) { this.emit('toast', { msg, kind, undo }); },

  /* range */
  setRange(s, e) {
    const c = this.clip; if (!c) return; const u = this.ui(c), d = c.duration;
    s = Math.max(0, Math.min(s, d - 1)); e = Math.min(d, Math.max(e, s + 1));
    u.s = s; u.e = e; u.whole = s <= 0 && e >= d;
    this.emit('range');
  },
  toggleWhole() {
    const c = this.clip; if (!c) return; const u = this.ui(c);
    if (u.whole) {
      u.whole = false;
      if (u.s <= 0 && u.e >= c.duration) {
        const p = c.peak ?? c.duration / 2;
        [u.s, u.e] = c.sug_whole || c.trimmed ? [Math.max(0, p - 10), Math.min(c.duration, p + 5)] : [c.sug_start ?? 0, c.sug_end ?? c.duration];
      }
    } else u.whole = true;
    this.emit('press', 'whole'); this.emit('range');
  },
  jumpAlt() {
    const c = this.clip; if (!c || c.analysis.alt == null) return;
    const a = c.analysis.alt; this.setRange(Math.max(0, a - 10), Math.min(c.duration, a + 5)); this.seek(this.ui(c).s);
    this.emit('press', 'alt');
  },
  markIn() { const c = this.clip; if (!c) return; const u = this.ui(c); const e = u.whole ? c.duration : u.e; this.setRange(this.t, this.t >= e - 1 ? Math.min(c.duration, this.t + 5) : e); this.emit('press', 'in'); },
  markOut() { const c = this.clip; if (!c) return; const u = this.ui(c); const s = u.whole ? 0 : u.s; this.setRange(this.t <= s + 1 ? Math.max(0, this.t - 5) : s, this.t); this.emit('press', 'out'); },

  /* transport */
  seek(t) { const c = this.clip; if (!c) return; this.t = Math.max(0, Math.min(c.duration, t)); this.emit('tick'); },
  nudge(d) { this.seek(this.t + d); this.emit('press', d < 0 ? 'back' : 'fwd'); },
  togglePlay() { const c = this.clip; if (!c) return; if (!this.playing && this.t >= c.duration - .05) this.t = 0; this.playing = !this.playing; this.emit('press', 'play'); },
  cycleSpeed() { this.speed = { 1: 1.5, 1.5: 2, 2: 1 }[this.speed]; this.video.playbackRate = this.speed; this.emit('press', 'speed'); this.emit('transport'); },
  toggleMute() { this.muted = !this.muted; this.video.muted = this.muted; this.emit('press', 'mute'); this.emit('transport'); },

  /* metadata: rating and tags save right away; the title renames the file, so it goes with the decision
     (or on leaving the field, for clips that already have one) */
  saveMeta(c, body) { api(`/api/clip/${c.id}/meta`, body).catch(e => this.toast(e.message, 'trash')); },
  setRating(n) { const c = this.clip; if (!c) return; c.rating = c.rating === n ? 0 : n; this.saveMeta(c, { rating: c.rating }); this.emit('press', 'rate'); this.emit('meta'); },
  setTitle(s) { const c = this.clip; if (c) { c.title = s; this.emit('meta'); } },
  commitTitle() { const c = this.clip; if (c && c.status !== 'new') this.saveMeta(c, { title: c.title }); },
  toggleTag(tag) { const c = this.clip; if (!c) return; c.tags = c.tags.includes(tag) ? c.tags.filter(t => t !== tag) : [...c.tags, tag]; this.saveMeta(c, { tags: c.tags.join(', ') }); this.emit('meta'); },

  flags(c) {
    const f = [];
    if (c.busy) f.push({ key: 'busy', label: 'Working…', tone: 'busy' });
    if (!c.analyzed && !c.failed) f.push({ key: 'analyzing', label: 'Analyzing…', tone: 'busy' });
    if (c.failed) f.push({ key: 'failed', label: 'Could not analyze', tone: 'danger' });
    if (this.expired(c)) f.push({ key: 'expired', label: '30 days are up', tone: 'danger' });
    if (c.duration < 5) f.push({ key: 'tiny', label: `Tiny: ${c.duration.toFixed(1)} s (double-press?)`, tone: 'warn' });
    else if (c.quiet) f.push({ key: 'quiet', label: 'Nothing much happens', tone: 'warn' });
    if (c.analysis.alt != null) f.push({ key: 'alt', label: `Another busy moment at ${F.time(c.analysis.alt)}`, tone: 'alt' });
    if (c._chain) f.push({ key: 'chain', label: `Part ${c._chain.index} of ${c._chain.count} back-to-back saves`, tone: 'info', button: 'Join', action: 'joinChain' });
    if (c.trimmed) f.push({ key: 'trimmed', label: 'Already trimmed', tone: 'muted' });
    if (c.compressed) f.push({ key: 'compressed', label: 'Compressed', tone: 'muted' });
    else if ((c.note || '').startsWith('Not compressed')) f.push({ key: 'uncompressed', label: c.note.includes('compact') ? 'Not compressed: already compact' : 'Kept at full quality: compressing it would have shown', tone: 'muted' });
    return f;
  },
  joinChain() { const c = this.clip; if (c && c._chain) this.join(c._chain.ids); },
  readout(c) {
    const u = this.ui(c);
    if (u.whole) return { whole: true, of: F.len(c.duration) };
    return { whole: false, range: `${F.time(u.s)} – ${F.time(u.e)}`, len: `${Math.round(u.e - u.s)} s`, of: `${Math.round(c.duration)} s` };
  },

  loop() {
    const f = () => { if (this.route === 'review') this.emit('tick'); requestAnimationFrame(f); };
    requestAnimationFrame(f);
  },
};

/* ---------- Keyboard (Review only) ---------- */
document.addEventListener('keydown', e => {
  if (!$('#setup').hidden) { if (e.key === 'Escape' && Setup.canCancel) Setup.close(null); return; }
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') { if (e.key === 'Escape' || e.key === 'Enter') e.target.blur(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (App.route !== 'review') { if (k === 'u') { e.preventDefault(); App.undo(); } return; }
  const map = {
    k: () => App.decide('keep'), t: () => App.decide('temp'), d: () => App.decide('trash'), s: () => App.skip(), u: () => App.undo(),
    ' ': () => App.togglePlay(), ArrowLeft: () => App.nudge(-5), ArrowRight: () => App.nudge(5),
    '[': () => App.markIn(), ']': () => App.markOut(), w: () => App.toggleWhole(), a: () => App.jumpAlt(),
    r: () => App.cycleSpeed(), m: () => App.toggleMute(),
    1: () => App.setRating(1), 2: () => App.setRating(2), 3: () => App.setRating(3), 4: () => App.setRating(4), 5: () => App.setRating(5),
  };
  if (map[k]) { e.preventDefault(); if (e.target.tagName === 'BUTTON') e.target.blur(); map[k](); }
});

/* ---------- Moment timeline (canvas) ----------
   Colours come from CSS custom properties on the canvas element (--tl-*). */
class Timeline {
  constructor(canvas, opt = {}) {
    this.c = canvas; this.ctx = canvas.getContext('2d');
    this.o = Object.assign({ ruler: 'top', rulerH: 22, pad: 16 }, opt);
    this.drag = null; this.hoverX = null;
    new ResizeObserver(() => this.resize()).observe(canvas);
    canvas.addEventListener('pointerdown', e => this.down(e));
    canvas.addEventListener('pointermove', e => this.move(e));
    canvas.addEventListener('pointerup', e => this.up(e));
    canvas.addEventListener('pointercancel', e => this.up(e));
    canvas.addEventListener('pointerleave', () => { this.hoverX = null; });
    this.resize();
  }
  tokens() {
    const cs = getComputedStyle(this.c), g = n => cs.getPropertyValue('--tl-' + n).trim();
    const hex = h => { h = h.replace('#', ''); if (h.length === 3) h = [...h].map(x => x + x).join(''); return [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16)); };
    this.k = {
      bg: g('bg'), track: g('track'), heat: [hex(g('heat-0')), hex(g('heat-1')), hex(g('heat-2'))], dim: g('dim'),
      range: g('range'), rangeFill: g('range-fill'), handle: g('handle'), handleInk: g('handle-ink'),
      play: g('playhead'), peak: g('peak'), alt: g('alt'), tick: g('tick'), text: g('text'), font: g('font') || '11px monospace',
    };
  }
  ramp(v) { const [a, b, c] = this.k.heat, m = (p, q, f) => p.map((x, i) => Math.round(x + (q[i] - x) * f)); return `rgb(${v < .5 ? m(a, b, v * 2) : m(b, c, (v - .5) * 2)})`; }
  resize() {
    const r = this.c.getBoundingClientRect(), d = devicePixelRatio || 1;
    this.w = r.width; this.h = r.height;
    this.c.width = Math.max(1, Math.round(r.width * d)); this.c.height = Math.max(1, Math.round(r.height * d));
    this.ctx.setTransform(d, 0, 0, d, 0, 0); this.tokens();
  }
  get dur() { return App.clip ? App.clip.duration : 90; }
  x(t) { return this.o.pad + (t / this.dur) * (this.w - 2 * this.o.pad); }
  tAt(x) { return Math.max(0, Math.min(this.dur, ((x - this.o.pad) / (this.w - 2 * this.o.pad)) * this.dur)); }
  hit(x) {
    const c = App.clip; if (!c || !c.analyzed) return 'seek';
    const u = App.ui(c), s = u.whole ? 0 : u.s, e = u.whole ? c.duration : u.e;
    const ds = Math.abs(x - this.x(s)), de = Math.abs(x - this.x(e));
    return Math.min(ds, de) < 11 ? (ds <= de ? 'start' : 'end') : 'seek';
  }
  down(e) { if (!App.clip) return; this.drag = this.hit(e.offsetX); this.c.setPointerCapture(e.pointerId); this.wasPlaying = App.playing; if (this.drag !== 'seek') App.playing = false; this.apply(e.offsetX, e.shiftKey); }
  move(e) { this.hoverX = e.offsetX; if (!this.drag) { this.c.style.cursor = this.hit(e.offsetX) === 'seek' ? 'pointer' : 'ew-resize'; return; } this.apply(e.offsetX, e.shiftKey); }
  up() { if (this.drag && this.drag !== 'seek') App.playing = this.wasPlaying; this.drag = null; }
  apply(x, fine) {
    const c = App.clip, u = App.ui(c);
    let t = this.tAt(x); if (!fine && this.drag !== 'seek') t = Math.round(t * 2) / 2;   // 0.5 s = the clips' keyframe spacing
    if (this.drag === 'seek') App.seek(t);
    else if (this.drag === 'start') { App.setRange(t, u.whole ? c.duration : u.e); App.seek(App.ui(c).s); }
    else if (this.drag === 'end') { App.setRange(u.whole ? 0 : u.s, t); App.seek(App.ui(c).e); }
  }
  draw() {
    const { ctx, w, h, k, o } = this; if (!w) return;
    ctx.clearRect(0, 0, w, h); ctx.fillStyle = k.bg; ctx.fillRect(0, 0, w, h);
    const c = App.clip; if (!c) return;
    const R = o.rulerH, y0 = R, y1 = h, H = y1 - y0, X0 = this.x(0), X1 = this.x(c.duration);
    ctx.fillStyle = k.track; ctx.fillRect(X0, y0, X1 - X0, H);
    this.ruler(c);
    const a = c.analysis.activity;
    if (!c.analyzed || !a.length) { this.analyzing(X0, y0, X1, y1); this.playhead(y0, y1); return; }
    // mirrored activity bars (one per 0.5 s)
    const n = a.length, bw = (X1 - X0) / n, mid = (y0 + y1) / 2, gap = bw > 4 ? 1 : 0;
    for (let i = 0; i < n; i++) { const v = a[i], hh = Math.max(1, v * (H / 2 - 8)); ctx.fillStyle = this.ramp(v); ctx.fillRect(X0 + i * bw, mid - hh, Math.max(1, bw - gap), hh * 2); }
    ctx.fillStyle = k.tick; ctx.fillRect(X0, mid, X1 - X0, 1);
    // keep range
    const u = App.ui(c), s = u.whole ? 0 : u.s, e = u.whole ? c.duration : u.e, xs = this.x(s), xe = this.x(e);
    ctx.fillStyle = k.dim; ctx.fillRect(X0, y0, xs - X0, H); ctx.fillRect(xe, y0, X1 - xe, H);
    ctx.fillStyle = k.rangeFill; ctx.fillRect(xs, y0, xe - xs, H);
    ctx.fillStyle = k.range; ctx.fillRect(xs, y0, xe - xs, 2); ctx.fillRect(xs, y1 - 2, xe - xs, 2);
    // markers: suggested peak (solid) and alternative busy moment (hollow)
    ctx.font = k.font; ctx.textBaseline = 'top';
    if (c.peak != null) {
      const px = Math.round(this.x(c.peak)) + .5; ctx.fillStyle = k.peak;
      ctx.beginPath(); ctx.moveTo(px - 6, y0 + 2); ctx.lineTo(px + 6, y0 + 2); ctx.lineTo(px, y0 + 10); ctx.closePath(); ctx.fill();
      ctx.fillRect(px - .5, y0 + 2, 1, H - 4); ctx.fillText('PEAK', px + 7, y0 + 4);
    }
    if (c.analysis.alt != null) {
      const ax = Math.round(this.x(c.analysis.alt)) + .5; ctx.strokeStyle = k.alt; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(ax - 6, y0 + 2.5); ctx.lineTo(ax + 6, y0 + 2.5); ctx.lineTo(ax, y0 + 10.5); ctx.closePath(); ctx.stroke();
      ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(ax, y0 + 11); ctx.lineTo(ax, y1 - 2); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = k.alt; ctx.fillText('A', ax + 7, y0 + 4);
    }
    this.handle(xs, y0, y1, 'in'); this.handle(xe, y0, y1, 'out');
    if (this.hoverX != null && !this.drag && this.hoverX > X0 && this.hoverX < X1) {
      ctx.fillStyle = k.tick; ctx.fillRect(Math.round(this.hoverX), y0, 1, H);
      ctx.fillStyle = k.text; ctx.textBaseline = 'bottom';
      const lbl = F.time(this.tAt(this.hoverX)), tw = ctx.measureText(lbl).width;
      ctx.fillText(lbl, Math.min(this.hoverX + 5, X1 - tw - 2), y1 - 5);
    }
    this.playhead(y0, y1);
  }
  handle(x, y0, y1, side) {
    const { ctx, k } = this, H = y1 - y0, inn = side === 'in';
    ctx.fillStyle = k.handle;
    ctx.fillRect(x - (inn ? 0 : 2), y0, 2, H);
    ctx.fillRect(inn ? x : x - 8, y0, 8, 2); ctx.fillRect(inn ? x : x - 8, y1 - 2, 8, 2);
    const gx = inn ? x - 9 : x + 1; ctx.fillRect(gx, y0 + H / 2 - 13, 8, 26);
    ctx.fillStyle = k.handleInk; for (let i = -1; i <= 1; i++) ctx.fillRect(gx + 2, y0 + H / 2 + i * 5 - .5, 4, 1);
  }
  playhead(y0, y1) {
    const { ctx, k } = this, x = Math.round(this.x(App.t)) + .5;
    ctx.fillStyle = k.play; ctx.fillRect(x - .75, 0, 1.5, this.h);
    ctx.beginPath(); ctx.moveTo(x - 6, 0); ctx.lineTo(x + 6, 0); ctx.lineTo(x + 6, 9); ctx.lineTo(x, 15); ctx.lineTo(x - 6, 9); ctx.closePath(); ctx.fill();
  }
  ruler(c) {
    const { ctx, k, o } = this, R = o.rulerH, pps = (this.w - 2 * o.pad) / c.duration;
    const major = [5, 10, 15, 30, 60, 120].find(s => s * pps >= 64) || 300, minor = major >= 30 ? 5 : 1;
    ctx.font = k.font; ctx.textBaseline = 'middle';
    for (let s = 0; s <= c.duration + .001; s += minor) {
      const x = Math.round(this.x(s)) + .5, isMaj = Math.abs(s / major - Math.round(s / major)) < 1e-6, th = isMaj ? 7 : 3;
      if (minor * pps < 4 && !isMaj) continue;
      ctx.fillStyle = k.tick; ctx.fillRect(x - .5, R - th, 1, th);
      if (isMaj && x + 30 < this.w) { ctx.fillStyle = k.text; ctx.fillText(F.time(s), x + 3, R / 2 - 2); }
    }
  }
  analyzing(X0, y0, X1, y1) {
    const { ctx, k } = this, off = (performance.now() / 40) % 16;
    ctx.save(); ctx.beginPath(); ctx.rect(X0, y0, X1 - X0, y1 - y0); ctx.clip();
    ctx.strokeStyle = k.tick; ctx.lineWidth = 1;
    for (let x = X0 - (y1 - y0) + off; x < X1; x += 16) { ctx.beginPath(); ctx.moveTo(x, y1); ctx.lineTo(x + (y1 - y0), y0); ctx.stroke(); }
    ctx.restore();
    const msg = App.clip && App.clip.failed ? 'Could not analyze this clip: keeping the whole clip'
      : 'Analyzing this clip… the suggested moment appears in a few seconds'; ctx.font = k.font;
    const tw = ctx.measureText(msg).width; ctx.fillStyle = k.bg; ctx.fillRect((X0 + X1) / 2 - tw / 2 - 10, (y0 + y1) / 2 - 11, tw + 20, 22);
    ctx.fillStyle = k.text; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(msg, (X0 + X1) / 2, (y0 + y1) / 2); ctx.textAlign = 'left';
  }
}

/* ================= shell ================= */
const totalOf = list => list.reduce((s, c) => s + (c.size || 0), 0);
function routeFromHash() { const r = location.hash.slice(1).split('/')[0]; return ['review', 'library', 'space', 'trash'].includes(r) ? r : 'review'; }
window.addEventListener('hashchange', () => App.go(routeFromHash()));
App.on('route', () => {
  document.body.dataset.route = App.route;
  $$('.tab').forEach(t => t.classList.toggle('on', t.dataset.go === App.route));
  if (location.hash.slice(1).split('/')[0] !== App.route) history.replaceState(null, '', '#' + App.route);
  ({ library: renderLibrary, space: renderSpace, trash: renderTrash, review: () => { tl.resize(); render(); } })[App.route]();
});
function renderNav() {
  const lib = App.library, fresh = lib.filter(c => c.status === 'new'), tb = totalOf(App.trash), b = App.summary.background;
  $('#nReview').textContent = fresh.length;
  $('#nLibrary').textContent = lib.length;
  $('#nSpace').textContent = App.summary.drive ? Math.round(App.summary.drive.free / 1e9) + ' GB free' : '';
  $('#nTrash').textContent = App.trash.length ? F.size(tb) : 'empty';
  const fail = b.failed.length ? `<span class="fail" title="${esc(b.failed.map(f => `${f.kind}: ${f.error}`).join('\n'))}">${b.failed.length} failed</span>` : '';
  // two lanes run at once: your decisions (move, trim, compress) and reading/analyzing clips
  const about = s => (s > 30 ? ` · about ${F.eta(s)}` : '');
  const parts = [];
  if (b.jobsLeft) parts.push(`Applying your decisions · ${b.jobsLeft} left${about(b.jobsEta)}`);
  if (b.toIndex) parts.push(`Reading folder · ${b.toIndex} files left${about(b.eta)}`);
  else if (b.toAnalyze) parts.push(`Analyzing ${b.analyzedRun + 1} of ${b.analyzedRun + b.toAnalyze}${about(b.eta)}`);
  const working = parts.length > 0;
  const keepOpen = working
    ? '<span title="This work runs in the Clip Buddy window, not in this tab. You can close or reload this tab; keep that window open (minimized is fine).">keep the Clip Buddy window open</span>' : '';
  $('#bgline').innerHTML = (working ? '<i class="pulse"></i>' : '') +
    [...(working ? parts.map(esc) : ['idle']), fail, keepOpen].filter(Boolean).join(' · ');
}
App.on('data', () => { renderNav(); if (App.route === 'library') renderLibrary(); if (App.route === 'space') renderSpace(); if (App.route === 'trash') renderTrash(); if (App.route === 'review') { renderFlags(); renderNext(); renderPos(); } });

let toastTimer;
App.on('toast', ({ msg, kind, undo }) => {
  const t = $('#toast'), col = { keep: 'var(--keep)', temp: 'var(--temp)', trash: 'var(--del)' }[kind] || 'var(--ink-3)';
  t.style.setProperty('--c', col); $('#toastMsg').textContent = msg; $('#toastUndo').style.display = undo ? '' : 'none';
  t.classList.remove('show'); void t.offsetWidth; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 8000);
});
$('#toastUndo button').onclick = () => App.undo();
App.on('press', k => $$(`[data-key="${k}"]`).forEach(b => { b.classList.add('hit'); setTimeout(() => b.classList.remove('hit'), 200); }));

/* ================= review ================= */
const tl = new Timeline($('#tl'), { rulerH: 22, pad: 16 });
$('#queueSel').onchange = e => { App.setQueue(e.target.value); e.target.blur(); };
App.on('tick', () => { tl.draw(); const c = App.clip; if (c) $('#tc').textContent = F.tc(App.t, c.fps); });
App.on('change', () => { if (App.route === 'review') render(); renderNav(); });
App.on('change-soft', () => { if (App.route === 'review') render(); });
App.on('range', renderRange);
App.on('meta', renderMeta);
App.on('transport', renderTransport);

function renderPos() {
  const c = App.clip;
  $('#queueSel').innerHTML = QUEUES.map(q => `<option value="${q.key}">${q.label}</option>`).join('') + (App.queueKey === 'custom' ? `<option value="custom">${esc(App.customLabel)}</option>` : '');
  $('#queueSel').value = App.queueKey;
  $('#pos').innerHTML = c ? `<b>${App.pos + 1}</b> of ${App.queue.length} in queue` : `0 in queue`;
}
function render() {
  const c = App.clip;
  $('#review').classList.toggle('r-empty', !c);
  renderPos(); renderNext();
  if (!c) return;
  const name = c.name, dot = name.toLowerCase().lastIndexOf('.mp4');
  $('#fname').innerHTML = esc(name.slice(0, dot)) + '<span class="ext">.mp4</span>';
  $('#dur').textContent = '/ ' + F.tc(c.duration, c.fps);
  $('#game').textContent = c.game;
  $('#when').textContent = `${F.date(c.start_ts)} · ${F.clock(c.start_ts)}`;
  const s = App.sessionInfo(c);
  $('#props').innerHTML = `
    <dt>Source</dt><dd>${F.kind(c)}</dd>
    <dt>Session</dt><dd>${s.count > 1 ? `${s.day} · clip ${s.index} of ${s.count} · ${F.size(s.size)}` : `${s.day} · only clip`}</dd>
    <dt>Size</dt><dd>${F.size(c.size)}${c.orig_size ? ` <s>· was ${F.size(c.orig_size)}</s>` : ''}</dd>
    <dt>Length</dt><dd>${F.len(c.duration)}</dd>
    <dt>Format</dt><dd>${F.spec(c)}</dd>`;
  $('#title').value = c.title;
  renderFlags(); renderMeta(); renderRange(); renderTransport();
}
function renderFlags() {
  const c = App.clip; if (!c) return;
  $('#flags').innerHTML = App.flags(c).map(f => f.key === 'alt'
    ? `<button class="flag alt" onclick="App.jumpAlt()" title="Move the keep range to ${F.time(c.analysis.alt)}"><i></i>${esc(f.label)} — press <kbd>A</kbd></button>`
    : `<span class="flag ${f.tone}">${esc(f.label)}${f.button ? `<button class="act" onclick="App.${f.action}()">${f.button}</button>` : ''}</span>`).join('') || '<span class="none">No flags</span>';
  $('#altBtn').disabled = c.analysis.alt == null;
}
function renderRange() {
  const c = App.clip; if (!c) return; const r = App.readout(c), el = $('#readout');
  el.className = 'readout' + (r.whole ? ' whole' : '');
  el.innerHTML = r.whole ? `Keeping the whole clip <span class="of">(${r.of})</span>` : `Keeping ${r.range} <span class="of">(${r.len} of ${r.of})</span>`;
  $('#wholeBtn').classList.toggle('on', r.whole);
}
function renderMeta() {
  const c = App.clip; if (!c) return;
  $('#stars').innerHTML = [1, 2, 3, 4, 5].map(n => `<button class="star ${n <= c.rating ? 'on' : ''}" title="${n} star${n > 1 ? 's' : ''} (key ${n})" onclick="App.setRating(${n})">${STAR}</button>`).join('');
  const custom = c.tags.filter(t => !QUICK_TAGS.includes(t));
  $('#tags').innerHTML = [...QUICK_TAGS, ...custom].map(t => `<button class="tag ${c.tags.includes(t) ? 'on' : ''}" data-tag="${esc(t)}">${esc(t)}</button>`).join('');
  $('#rename').innerHTML = c.title.trim() ? `renames to <b>${esc(F.renamed(c))}</b>` : 'Typing a title renames the file';
}
function renderTransport() {
  $('#playLbl').textContent = App.playing ? 'Pause' : 'Play';
  $('#speedLbl').textContent = App.speed + '×'; $('#speedBtn').classList.toggle('on', App.speed !== 1);
  $('#muteLbl').textContent = App.muted ? 'Muted' : 'Sound on'; $('#muteBtn').classList.toggle('on', App.muted);
}
function renderNext() {
  const ids = App.queue.slice(App.pos + 1, App.pos + 4);
  $('#next').innerHTML = ids.map((id, i) => { const c = App.byId[id]; if (!c) return ''; return `<button class="nrow" data-i="${App.pos + 1 + i}"><img src="${poster(c)}" alt="" onerror="this.style.visibility='hidden'"><span><b>${esc(c.title || c.game)}</b><span>${F.day(c.start_ts)} · ${F.clock(c.start_ts)} · ${F.size(c.size)}</span></span></button>`; }).join('') || '<span class="none">End of queue</span>';
}
$('#next').onclick = e => { const b = e.target.closest('.nrow'); if (b) { App.pos = +b.dataset.i; App.load(); } };
$('#tags').onclick = e => { const b = e.target.closest('.tag'); if (b) App.toggleTag(b.dataset.tag); };
$('#title').oninput = e => App.setTitle(e.target.value);
$('#title').onchange = () => App.commitTitle();

/* ================= library ================= */
const lib = { game: 'all', status: 'all', sort: 'session', q: '' };
const STATUSES = [['all', 'All'], ['new', 'Unreviewed'], ['keep', 'Kept'], ['temp', '30-day'], ['expired', '30 days up'], ['junk', 'Likely junk'], ['working', 'Working']];
function libMatch(c) {
  if (lib.game !== 'all' && c.game !== lib.game) return false;
  const st = App.statusOf(c).key;
  if (lib.status === 'junk' ? !App.isJunk(c) : lib.status !== 'all' && st !== lib.status) return false;
  if (lib.q) { const q = lib.q.toLowerCase(); if (![c.name, c.title, ...c.tags].join(' ').toLowerCase().includes(q)) return false; }
  return true;
}
function card(c) {
  const st = App.statusOf(c), title = c.title ? `<span class="ttl">${esc(c.title)}</span>` : `<span class="ttl time">${F.clock(c.start_ts)}</span>`;
  const part = c._chain ? `<span class="part">part ${c._chain.index}/${c._chain.count}</span>` : '';
  const stars = c.rating ? `<span class="mini-stars">${'★'.repeat(c.rating)}</span>` : '';
  return `<button class="card st-${st.key}" data-id="${c.id}" title="${esc(c.name)}">
    <div class="poster" style="background-image:url('${poster(c)}')"></div><div class="scrub"><i></i></div>
    <div class="cmeta"><div class="r1">${title}<span class="durn">${F.len(c.duration)}</span></div>
    <div class="r2"><span class="st ${st.key}">${st.label}</span><span>${F.size(c.size)}</span>${stars}${part}</div></div></button>`;
}
function renderLibrary() {
  const all = App.library;
  const games = [...new Set(all.map(c => c.game))].sort((a, b) => all.filter(c => c.game === b).length - all.filter(c => c.game === a).length);
  $('#fGame').innerHTML = `<option value="all">All games · ${all.length}</option>` + games.map(g => `<option value="${esc(g)}">${esc(g)} · ${all.filter(c => c.game === g).length}</option>`).join('');
  $('#fGame').value = games.includes(lib.game) ? lib.game : 'all';
  const inGame = all.filter(c => lib.game === 'all' || c.game === lib.game);
  const count = k => k === 'all' ? inGame.length : k === 'junk' ? inGame.filter(c => App.isJunk(c)).length : inGame.filter(c => App.statusOf(c).key === k).length;
  $('#fStatus').innerHTML = STATUSES.map(([k, l]) => `<button data-st="${k}" class="${lib.status === k ? 'on' : ''}">${l}<span class="n">${count(k)}</span></button>`).join('');
  $('#fSort').value = lib.sort;
  const shown = all.filter(libMatch);
  $('#libTotal').textContent = `${F.plural(shown.length, 'clip')} · ${F.size(totalOf(shown))}`;
  const jb = $('#junkbar');
  jb.classList.toggle('show', lib.status === 'junk' && shown.length > 0);
  $('#junkText').textContent = `Tiny or quiet clips nobody reviewed yet. Hover a few to check before you bin them.`;
  $('#junkBtn').textContent = `Move ${shown.length} shown to Trash (${F.size(totalOf(shown))})`;
  $('#junkBtn').onclick = () => App.trashMany(shown.map(c => c.id));
  let html = '';
  if (!shown.length) html = `<div class="lib-empty">${all.length ? 'No clips match these filters.' : 'No clips yet. New clips in the NVIDIA folder show up here within a minute.'}</div>`;
  else if (lib.sort === 'session') {
    App.sessions.forEach(S => {
      const cl = S.clips.filter(libMatch); if (!cl.length) return;
      const live = S.clips, unrev = live.filter(c => c.status === 'new');
      const chain = live.find(c => c._chain && c._chain.index === 1 && !c.busy);
      html += `<section class="sess"><header class="sess-head">
        <h2>${esc(S.game)} <span>· ${F.date(S.start)}</span></h2>
        <span class="meta">${F.plural(live.length, 'clip')} · ${F.size(totalOf(live))} · <b>${unrev.length} unreviewed</b></span>
        <div class="acts">${chain ? `<button class="btn" data-join="${chain._chain.ids.join(',')}">Join ${chain._chain.count} back-to-back clips</button>` : ''}
        <button class="btn primary" data-review="${S.id}">Review session</button></div></header>
        <div class="grid">${cl.map(card).join('')}</div></section>`;
    });
  } else {
    const sorters = { biggest: (a, b) => b.size - a.size, rated: (a, b) => b.rating - a.rating || b.start_ts - a.start_ts, longest: (a, b) => b.duration - a.duration };
    html = `<section class="sess"><div class="grid">${shown.sort(sorters[lib.sort]).map(card).join('')}</div></section>`;
  }
  const keep = $('#libScroll').scrollTop;
  $('#libScroll').innerHTML = html;
  $('#libScroll').scrollTop = keep;
}
$('#fGame').onchange = e => { lib.game = e.target.value; renderLibrary(); };
$('#fSort').onchange = e => { lib.sort = e.target.value; renderLibrary(); };
$('#libSearch').oninput = e => { lib.q = e.target.value.trim(); renderLibrary(); };
$('#fStatus').onclick = e => { const b = e.target.closest('[data-st]'); if (b) { lib.status = b.dataset.st; renderLibrary(); } };
$('#libScroll').onclick = e => {
  const j = e.target.closest('[data-join]'); if (j) return App.join(j.dataset.join.split(',').map(Number));
  const r = e.target.closest('[data-review]');
  if (r) {
    const S = App.sessions.find(s => s.id === +r.dataset.review), live = S.clips, fresh = live.filter(c => c.status === 'new');
    App.setCustomQueue((fresh.length ? fresh : live).map(c => c.id), `Session · ${S.game} · ${F.day(S.start)}`); return App.go('review');
  }
  const c = e.target.closest('.card'); if (c) App.openClip(+c.dataset.id);
};
// hover-scrub: one row of 40 tiles → background-size 4000% 100%, position from mouse x
$('#libScroll').addEventListener('mousemove', e => {
  const p = e.target.closest('.poster'); if (!p) return;
  const cardEl = p.parentElement, c = App.byId[+cardEl.dataset.id]; if (!c) return;
  if (!p.dataset.scrub) { p.dataset.poster = p.style.backgroundImage; p.style.backgroundImage = `url('${strip(c)}')`; p.style.backgroundSize = '4000% 100%'; p.dataset.scrub = 1; }
  const r = p.getBoundingClientRect(), i = Math.max(0, Math.min(39, Math.floor((e.clientX - r.left) / r.width * 40)));
  p.style.backgroundPosition = `${(i / 39) * 100}% 0`;
  cardEl.querySelector('.scrub i').style.width = ((i + 1) / 40 * 100) + '%';
});
$('#libScroll').addEventListener('mouseout', e => {
  const p = e.target.closest('.poster'); if (!p || p.contains(e.relatedTarget)) return;
  if (p.dataset.scrub) { p.style.backgroundImage = p.dataset.poster; p.style.backgroundSize = ''; p.style.backgroundPosition = ''; delete p.dataset.scrub; p.parentElement.querySelector('.scrub i').style.width = 0; }
});

/* ================= space ================= */
function renderSpace() {
  const all = App.library, d = App.summary.drive || { label: '', total: 1, free: 0 }, by = s => all.filter(c => c.status === s);
  const fresh = by('new'), kept = by('keep');
  const libB = totalOf(all), trashB = totalOf(App.trash), other = Math.max(0, d.total - d.free - libB - trashB);
  const pct = b => (b / d.total * 100).toFixed(2) + '%';
  const junk = all.filter(c => App.isJunk(c)), expired = all.filter(c => App.expired(c)), unc = App.compressible();
  const ways = [
    { n: junk.length, b: totalOf(junk), t: `${F.plural(junk.length, 'clip')} look like junk`, x: 'Tiny double-presses, or nothing much happens. Not reviewed yet.', btn: 'Review them', fn: "App.setQueue('junk');App.go('review')" },
    { n: expired.length, b: totalOf(expired), t: `${F.plural(expired.length, 'clip')} whose 30 days are up`, x: 'You kept these for 30 days. Decide now: keep or delete.', btn: 'Review them', fn: "App.setQueue('expired');App.go('review')" },
    { n: unc.length, b: totalOf(unc) * (1 - 1 / 4.5), t: `${F.plural(unc.length, 'kept clip')} not compressed yet`, x: `About 4.5× smaller, visually the same. Saves this much of ${F.size(totalOf(unc))}.`, btn: 'Compress all', fn: 'App.compressAll()' },
    { n: App.trash.length, b: trashB, t: `Trash is holding ${F.size(trashB)}`, x: 'Still on disk until you empty it.', btn: 'Open Trash', fn: "App.go('trash')" },
    { n: fresh.length, b: totalOf(fresh), t: `${F.plural(fresh.length, 'clip')} waiting for review`, x: 'Most of these are probably junk. A few seconds each.', btn: 'Start reviewing', fn: "App.setQueue('oldest');App.go('review')", primary: true },
  ].filter(w => w.n > 0).sort((a, b) => b.b - a.b);
  const games = [...new Set(all.map(c => c.game))].map(g => { const l = all.filter(c => c.game === g); return { g, n: l.length, k: totalOf(l.filter(c => c.status === 'keep')), t: totalOf(l.filter(c => c.status === 'temp')), u: totalOf(l.filter(c => c.status === 'new')), tot: totalOf(l) }; }).sort((a, b) => b.tot - a.tot);
  const max = Math.max(1, ...games.map(g => g.tot));
  const det = App.summary.detection;
  const big = b => b >= 1e9 || !b ? `${F.gb(b)}<small>GB</small>` : `${Math.round(b / 1e6)}<small>MB</small>`;
  $('#spacePage').innerHTML = `
    <div class="page-h"><h1>Space</h1><span class="sub">${esc(d.label)} · ${F.plural(all.length, 'clip')}</span></div>
    <div class="bign">
      <div class="bn"><span class="cap">Clips in library</span><span class="v">${big(libB)}</span><span class="d">${F.plural(all.length, 'clip')}</span></div>
      <div class="bn"><span class="cap">Waiting for review</span><span class="v">${big(totalOf(fresh))}</span><span class="d">${F.plural(fresh.length, 'clip')} nobody has looked at</span></div>
      <div class="bn keep"><span class="cap">Kept</span><span class="v">${big(totalOf(kept))}</span><span class="d">${F.plural(kept.length, 'clip')}</span></div>
      <div class="bn acc"><span class="cap">Freed so far</span><span class="v">${big(App.summary.freed)}</span><span class="d">${trashB ? `+ ${F.size(trashB)} more when you empty Trash` : 'Trash is empty'}</span></div>
    </div>
    ${App.summary.drive ? `<div class="drive">
      <div class="drive-h"><span class="cap">Drive ${esc(d.label)}</span><span class="free"><b>${Math.round(d.free / 1e9)} GB free</b> of ${Math.round(d.total / 1e9)} GB</span></div>
      <div class="dbar"><i class="clips" style="width:${pct(libB)}"></i><i class="trash" style="width:${pct(trashB)}"></i><i class="other" style="width:${pct(other)}"></i></div>
      <div class="legend"><span><i style="background:var(--accent)"></i>Clips <b>${F.size(libB)}</b></span><span><i style="background:var(--del)"></i>Trash <b>${F.size(trashB)}</b></span><span><i style="background:#3a3e44"></i>Everything else <b>${Math.round(other / 1e9)} GB</b></span><span><i style="border:1px solid var(--rule-2)"></i>Free <b>${Math.round(d.free / 1e9)} GB</b></span></div>
    </div>` : ''}
    <div class="cols">
      <div class="panel"><span class="cap">Ways to free space</span>
        ${ways.map(w => `<div class="way"><span class="sz">${w.b >= 1e9 ? `${F.gb(w.b)}<small> GB</small>` : `${Math.round(w.b / 1e6)}<small> MB</small>`}</span><div><div class="t">${w.t}</div><div class="x">${w.x}</div></div><button class="btn ${w.primary ? 'primary' : ''}" onclick="${w.fn}">${w.btn}</button></div>`).join('') || '<div class="way"><span></span><div class="t">Nothing to free right now.</div></div>'}
      </div>
      <div class="panel"><span class="cap">By game</span>
        ${games.map(g => `<div class="game-row"><div class="nm">${esc(g.g)}<span>${F.plural(g.n, 'clip')}</span></div>
          <div class="sbar" title="kept ${F.size(g.k)} · 30-day ${F.size(g.t)} · unreviewed ${F.size(g.u)}"><i class="k" style="width:${g.k / max * 100}%"></i><i class="t" style="width:${g.t / max * 100}%"></i><i class="n" style="width:${g.u / max * 100}%"></i></div>
          <span class="tot">${F.size(g.tot)}</span></div>`).join('')}
        <div class="legend"><span><i style="background:var(--keep)"></i>Kept</span><span><i style="background:var(--temp)"></i>30-day</span><span><i style="background:var(--unrev)"></i>Unreviewed</span></div>
      </div>
    </div>
    <div class="folder-row"><div><span class="cap">Clips folder</span><div class="p">${esc(App.summary.settings?.inbox || '')}</div>
      <div class="x">Kept clips: ${esc(App.summary.settings?.keepers || '')} · Trash: ${esc(App.summary.settings?.trash || '')}</div>
      <div class="x">Compression: ${App.summary.settings?.compress_keepers ? `HEVC on ${esc(App.summary.settings?.encoder || '…')}` : 'off'}</div></div>
      <button class="btn" onclick="Setup.open(true).then(ok => ok && App.reload())">Change folder</button></div>
    <div class="footnote">${det.of
      ? `<span class="ratio">${det.matched}/${det.of}</span><div class="dots">${Array.from({ length: Math.min(det.of, 60) }, (_, i) => `<i class="${i < det.matched ? '' : 'miss'}"></i>`).join('')}</div><span>Moment detection matched what you kept on ${det.matched} of ${det.of} clips.</span>`
      : `<span>Keep a few clips and this shows how often the suggested range matched what you chose.</span>`}</div>`;
}

/* ================= trash ================= */
const WHY = {
  unknown: 'origin unknown · may be a kept clip. Restore it, or delete it below',
  needed: 'the only copy of a kept clip · kept safe',
};
function renderTrash() {
  const items = App.trash, moved = items.filter(t => !t.pending), gone = moved.filter(t => !t.protected), b = totalOf(gone);
  const unknown = moved.filter(t => t.protected === 'unknown'), safe = moved.filter(t => t.protected === 'needed');
  const folder = App.summary.settings?.trash;
  $('#trashSub').textContent = (items.length ? `${F.plural(moved.length, 'file')} · ${F.size(b)} · still on disk until you empty it` : '')
    + (folder ? `${items.length ? ' · ' : ''}folder: ${folder}` : '');
  $('#trashList').innerHTML = items.length ? `<table><thead><tr><th>File</th><th>Why it's here</th><th class="th-r">Size</th><th>When</th><th></th></tr></thead><tbody>
    ${items.map(t => `<tr><td class="file">${esc(t.file)}</td><td class="note ${t.note === 'deleted' ? 'deleted' : ''} ${t.protected ? 'protected' : ''}">${esc(WHY[t.protected] || t.note)}</td><td class="num">${F.size(t.size)}</td><td class="when">${t.pending ? 'in a moment' : F.ago(t.when)}</td><td class="act">${t.pending ? '<span class="trash-pending">U to undo</span>' : `<button class="btn" data-restore="${t.id}">Restore</button>`}</td></tr>`).join('')}</tbody></table>`
    : `<div class="trash-empty"><h2>Trash is empty</h2>Deleted clips and replaced originals land here first, so nothing is gone until you say so.</div>`;
  $('#danger').style.display = gone.length ? '' : 'none';
  $('#dangerText').textContent = `Permanently deletes ${F.plural(gone.length, 'file')} (${F.size(b)}) from disk. This is the only step in Clip Buddy that can't be undone.`
    + (moved.length < items.length ? ` Clips still in their undo window are not included.` : '')
    + (safe.length ? ` ${F.plural(safe.length, 'file')} that ${safe.length > 1 ? 'are' : 'is'} the only copy of a kept clip ${safe.length > 1 ? 'are' : 'is'} kept.` : '');
  $('#holdLbl').textContent = `Empty trash (${F.size(b)})`;
  $('#danger2').style.display = unknown.length ? '' : 'none';
  $('#dangerText2').textContent = `Clip Buddy has no record of how ${F.plural(unknown.length, 'file')} (${F.size(totalOf(unknown))}) got here, `
    + `for example after using another copy of the app. They may be clips you kept, so Empty trash leaves them. Restore the ones you want first.`;
  $('#holdLbl2').textContent = `Delete ${F.plural(unknown.length, 'file')} of unknown origin`;
}
$('#trashList').onclick = e => { const r = e.target.closest('[data-restore]'); if (r) App.restore(r.dataset.restore); };
function holdToConfirm(btn, fn) {
  let timer;
  const start = e => { if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return; if (e.repeat || timer) return; e.preventDefault(); btn.classList.add('holding'); timer = setTimeout(() => { cancel(); fn(); }, 1600); };
  const cancel = () => { clearTimeout(timer); timer = null; btn.classList.remove('holding'); };
  btn.addEventListener('pointerdown', start); btn.addEventListener('keydown', start);
  ['pointerup', 'pointerleave', 'keyup', 'blur'].forEach(ev => btn.addEventListener(ev, cancel));
}
holdToConfirm($('#holdBtn'), () => App.emptyTrash());
holdToConfirm($('#holdBtn2'), () => App.emptyTrash(true));

/* ================= setup: choose the clips folder ================= */
const Setup = {
  canCancel: false, _done: null, _t: null, _checked: null,
  open(canCancel) {
    this.canCancel = canCancel;
    $('#setupCancel').hidden = !canCancel;
    $('#setup').hidden = false;
    App.video && App.video.pause();
    this.load();
    return new Promise(res => (this._done = res));
  },
  close(result) { $('#setup').hidden = true; const d = this._done; this._done = null; d && d(result); },
  async load() {
    const s = await api('/api/setup');
    $('#setupCands').innerHTML = s.candidates.map(c => `<button class="cand" data-p="${esc(c.path)}"><span class="p">${esc(c.path)}</span><span class="n">${c.clips >= 5000 ? '5000+' : c.clips} clips</span></button>`).join('');
    const start = s.inbox || (s.candidates[0] && s.candidates[0].path) || '';
    this.set(start);
    if (!start) $('#setupInfo').innerHTML = 'No clips found in the usual places. Use <b>Browse…</b> to pick the folder.';
  },
  set(path) { $('#setupPath').value = path; this.check(); },
  check() {
    clearTimeout(this._t);
    const path = $('#setupPath').value.trim();
    $$('.cand').forEach(b => b.classList.toggle('on', b.dataset.p.toLowerCase() === path.toLowerCase()));
    $('#setupGo').disabled = true; this._checked = null;
    if (!path) { $('#setupInfo').textContent = ''; return; }
    this._t = setTimeout(async () => {
      try {
        const r = await api('/api/setup/check', { inbox: path });
        if ($('#setupPath').value.trim() !== path) return;
        this._checked = r.path;
        $('#setupInfo').innerHTML = `<b>${r.clips >= 5000 ? '5000+' : r.clips} clips</b> in this folder and its subfolders.<br>` +
          `Kept clips go to <span class="mono">${esc(r.keepers)}</span>, deleted ones to <span class="mono">${esc(r.trash)}</span>.`;
        $('#setupGo').disabled = false;
      } catch (e) { $('#setupInfo').innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
    }, 250);
  },
  async go() {
    if (!this._checked) return;
    $('#setupGo').disabled = true;
    try { await api('/api/setup', { inbox: this._checked }); this.close(true); }
    catch (e) { $('#setupInfo').innerHTML = `<span class="bad">${esc(e.message)}</span>`; $('#setupGo').disabled = false; }
  },
  async browse() {
    const b = $('#setupBrowse'); b.disabled = true; b.textContent = 'Picker open…';
    try { const r = await api('/api/setup/browse', {}); if (r.path) this.set(r.path); }
    catch (e) { $('#setupInfo').innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
    finally { b.disabled = false; b.textContent = 'Browse…'; }
  },
};
$('#setupCands').onclick = e => { const b = e.target.closest('.cand'); if (b) Setup.set(b.dataset.p); };
$('#setupPath').oninput = () => Setup.check();
$('#setupPath').onkeydown = e => { if (e.key === 'Enter') Setup.go(); };
$('#setupBrowse').onclick = () => Setup.browse();
$('#setupGo').onclick = () => Setup.go();
$('#setupCancel').onclick = () => Setup.close(null);

// after switching folders: start over with the new library
App.reload = async function () {
  this.uiState = {}; this.undoStack = [];
  await this.refresh(); await this.refreshStats();
  this.setQueue('oldest'); this.emit('data');
  this.toast('Folder changed · new clips are being scanned', 'keep');
};

/* ================= boot ================= */
App.init().then(() => {
  renderNav();
  const link = location.hash.match(/^#review\/(\d+)$/);   // #review/<id> opens that clip
  const game = location.hash.match(/^#library\/(.+)$/);  // #library/<game> opens the library filtered to it
  if (game) lib.game = decodeURIComponent(game[1]);
  if (link && App.byId[+link[1]]) App.openClip(+link[1]); else App.go(routeFromHash());
});
