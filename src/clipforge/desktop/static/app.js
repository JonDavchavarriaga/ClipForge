const TOKEN = document.querySelector('meta[name="clipforge-token"]')?.content || '';

const state = {
  batches: [],
  selectedBatchId: '',
  selectedClipId: '',
  currentBundle: null,
  activeReviewJobId: null,
  currentEmbedUrl: '',
};

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showToast(msg, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = isError ? '#ef4444' : '#f97316';
  el.classList.remove('hidden');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add('hidden'), 4500);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-ClipForge-Token'] = TOKEN;
  }
  const resp = await fetch(path, { ...options, headers });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

function switchTab(name) {
  document.querySelectorAll('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
  if (name === 'library') {
    refreshLibrary();
    refreshLogs();
  }
}

document.querySelectorAll('.nav-btn').forEach((btn) => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

function formatDuration(secs) {
  const s = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${rem}s`;
  return `${rem}s`;
}

function mountLiveEmbed(embedUrl, rawUrl) {
  const wrap = document.getElementById('live-embed-wrap');
  const extLink = document.getElementById('live-open-ext');
  if (rawUrl) {
    extLink.href = rawUrl;
    extLink.classList.remove('hidden');
  }
  if (!embedUrl) return;
  if (state.currentEmbedUrl === embedUrl) return;
  state.currentEmbedUrl = embedUrl;
  wrap.innerHTML = '';
  const iframe = document.createElement('iframe');
  iframe.src = embedUrl;
  iframe.allow = 'autoplay; fullscreen; picture-in-picture';
  iframe.allowFullscreen = true;
  wrap.appendChild(iframe);
}

function renderLiveStatus(live) {
  const pill = document.getElementById('live-pill');
  const startBtn = document.getElementById('live-start-btn');
  const stopBtn = document.getElementById('live-stop-btn');
  const cutBtns = document.querySelectorAll('.live-cut-btn');
  const errBanner = document.getElementById('live-error-banner');

  if (live.running) {
    const autoTxt = live.auto_clip ? ' · AUTO-CLIP' : '';
    pill.textContent = `EN VIVO · ${(live.platform || 'STREAM').toUpperCase()}${autoTxt}`;
    pill.className = 'pill pill-live';
  } else {
    pill.textContent = 'INACTIVO';
    pill.className = 'pill pill-idle';
  }
  startBtn.disabled = live.running;
  stopBtn.disabled = !live.running;
  cutBtns.forEach((b) => { b.disabled = !live.running || live.buffered_segments < 1; });

  if (live.embed_url) {
    mountLiveEmbed(live.embed_url, live.url);
  }

  if (live.last_error) {
    errBanner.textContent = `Último error (guardado en logs): ${live.last_error}`;
    errBanner.classList.remove('hidden');
  } else {
    errBanner.classList.add('hidden');
  }

  document.getElementById('live-buffered').textContent = formatDuration(live.buffered_seconds || 0);
  document.getElementById('live-db').textContent = `${live.last_db ?? -60}`;
  document.getElementById('live-clip-count').textContent = String((live.clips || []).length);

  const list = document.getElementById('live-clips-list');
  if (!live.clips || !live.clips.length) {
    list.innerHTML = '<div class="muted">Los clips manuales y automáticos aparecerán aquí y en <code>workspace/live_clips/</code>.</div>';
    return;
  }
  list.innerHTML = '';
  live.clips.slice().reverse().forEach((clip) => {
    const row = document.createElement('div');
    row.className = 'list-item';
    const badgeClass = clip.mode === 'auto' ? 'auto' : 'ok';
    const badgeLabel = clip.mode === 'auto' ? '⚡ AUTO' : 'MANUAL';
    row.innerHTML = `
      <div>
        <strong>${esc(clip.clip_id)}</strong> (${Number(clip.window_seconds)}s)
        <div class="muted" style="font-size:11px;">${esc(clip.reason || '')}</div>
      </div>
      <span class="badge ${badgeClass}">${badgeLabel}</span>
    `;
    row.addEventListener('click', () => {
      document.getElementById('proc-source').value = clip.path;
      switchTab('process');
      showToast(`Cargado ${clip.clip_id} para generar 9:16`);
    });
    list.appendChild(row);
  });
}

// Presets in Tab 2 (Simple generation)
const PRESETS = {
  'gta-split': { layout: 'split-cam', cam: 'auto', style: 'highlight' },
  'talking': { layout: 'face', cam: 'auto', style: 'highlight' },
  'gameplay': { layout: 'fit', cam: 'auto', style: 'neon' },
};

document.querySelectorAll('.preset-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.preset-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    const cfg = PRESETS[btn.dataset.preset];
    if (cfg) {
      document.getElementById('proc-layout').value = cfg.layout;
      document.getElementById('proc-cam-region').value = cfg.cam;
      document.getElementById('proc-caption-style').value = cfg.style;
    }
  });
});

async function refreshStatus() {
  try {
    const data = await api('/api/status');
    const badges = document.getElementById('agent-badges');
    badges.innerHTML = `
      <span class="badge ok">Gemini Antigravity</span>
      <span class="badge ${data.agents.claude ? 'ok' : 'off'}">Claude ${data.agents.claude ? 'OK' : 'N/D'}</span>
      <span class="badge ${data.agents.codex ? 'ok' : 'off'}">Codex ${data.agents.codex ? 'OK' : 'N/D'}</span>
    `;
    if (data.workspace?.root) {
      document.getElementById('workspace-root-path').textContent = `Carpeta local: ${data.workspace.root}`;
    }
    renderLiveStatus(data.live);
  } catch (err) {
    console.error(err);
  }
}

async function refreshSources() {
  try {
    const data = await api('/api/sources');
    const list = document.getElementById('sources-list');
    if (!data.sources.length) {
      list.innerHTML = '<div class="muted">Aún no hay videos en tu carpeta local.</div>';
      return;
    }
    list.innerHTML = '';
    data.sources.forEach((src) => {
      const row = document.createElement('div');
      row.className = 'list-item';
      const tag = src.mode === 'auto' ? '<span class="badge auto">AUTO</span> ' : '';
      row.innerHTML = `<span>${tag}${esc(src.relative)}</span><span class="muted">${esc(src.size_mb)} MB</span>`;
      row.addEventListener('click', () => {
        document.getElementById('proc-source').value = src.path;
        showToast(`Seleccionado: ${src.name}`);
      });
      list.appendChild(row);
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

async function refreshLibrary() {
  try {
    const lib = await api('/api/library');
    document.getElementById('workspace-root-path').textContent = `Carpeta local: ${lib.paths.root}`;

    const renderList = (elId, items, emptyMsg) => {
      const el = document.getElementById(elId);
      if (!items.length) {
        el.innerHTML = `<div class="muted">${emptyMsg}</div>`;
        return;
      }
      el.innerHTML = '';
      items.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
          <div>
            <strong>${esc(item.name)}</strong>
            <div class="muted" style="font-size:11px;">${esc(item.relative)} · ${esc(item.size_mb)} MB</div>
          </div>
          <span class="badge ok">Procesar →</span>
        `;
        row.addEventListener('click', () => {
          document.getElementById('proc-source').value = item.path;
          switchTab('process');
          showToast(`Cargado ${item.name}`);
        });
        el.appendChild(row);
      });
    };

    renderList('lib-recordings', lib.live_recordings, 'Cuando detengas un live con "Guardar live completo", aparecerá aquí.');
    renderList('lib-live-clips', lib.live_clips, 'Aún no hay clips en workspace/live_clips/.');
  } catch (err) {
    showToast(err.message, true);
  }
}

async function refreshLogs() {
  try {
    const data = await api('/api/logs');
    const errBox = document.getElementById('errors-list');
    if (!data.errors.length) {
      errBox.innerHTML = '<div class="muted">Sin errores registrados 🎉</div>';
    } else {
      errBox.innerHTML = '';
      data.errors.forEach((e) => {
        const card = document.createElement('div');
        card.className = 'error-card';
        card.innerHTML = `
          <div><strong>${esc(e.source)}</strong> · <span class="muted">${esc(e.timestamp)}</span></div>
          <div style="margin-top:4px;">${esc(e.error_type)}: ${esc(e.message)}</div>
        `;
        errBox.appendChild(card);
      });
    }
    const logPre = document.getElementById('system-log-box');
    logPre.textContent = data.logs.length ? data.logs.join('\n') : 'Sin líneas de log todavía.';
  } catch (err) {
    console.error(err);
  }
}

async function refreshBatches() {
  try {
    const data = await api('/api/batches');
    state.batches = data.batches;
    const select = document.getElementById('batch-select');
    select.innerHTML = '';
    if (!state.batches.length) {
      select.innerHTML = '<option value="">Sin lotes aún</option>';
      document.getElementById('clip-selector').innerHTML = '<div class="muted">Genera tu primer clip en la pestaña "Generar Clips 9:16".</div>';
      return;
    }
    state.batches.forEach((b) => {
      const opt = document.createElement('option');
      opt.value = b.id;
      opt.textContent = `${b.id} (${b.clips.length} clips)`;
      select.appendChild(opt);
    });
    if (!state.selectedBatchId || !state.batches.some((b) => b.id === state.selectedBatchId)) {
      state.selectedBatchId = state.batches[0].id;
    }
    select.value = state.selectedBatchId;
    renderClipList();
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderClipList() {
  const batch = state.batches.find((b) => b.id === state.selectedBatchId);
  const box = document.getElementById('clip-selector');
  box.innerHTML = '';
  if (!batch || !batch.clips.length) {
    box.innerHTML = '<div class="muted">Este lote no tiene clips.</div>';
    return;
  }
  if (!state.selectedClipId || !batch.clips.some((c) => c.id === state.selectedClipId)) {
    state.selectedClipId = batch.clips[0].id;
  }
  batch.clips.forEach((clip) => {
    const row = document.createElement('div');
    row.className = `list-item ${clip.id === state.selectedClipId ? 'selected' : ''}`;
    const approved = clip.approval ? '<span class="badge ok">aprobado</span>' : '<span class="badge off">pendiente</span>';
    row.innerHTML = `<span><strong>${esc(clip.id)}</strong> · ${esc(clip.layout || '')} (${Math.round(clip.end - clip.start)}s)</span>${approved}`;
    row.addEventListener('click', () => {
      state.selectedClipId = clip.id;
      renderClipList();
      loadSelectedClip();
    });
    box.appendChild(row);
  });
  loadSelectedClip();
}

async function loadSelectedClip() {
  if (!state.selectedBatchId || !state.selectedClipId) return;
  const player = document.getElementById('clip-player');
  player.src = `/api/media?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(state.selectedClipId)}&t=${Date.now()}`;
  document.getElementById('pub-selected-info').textContent = `Seleccionado: ${state.selectedBatchId} / ${state.selectedClipId}`;

  const dst = document.getElementById('tr-dst').value;
  try {
    const res = await api(`/api/translation?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(state.selectedClipId)}&target_lang=${encodeURIComponent(dst)}`);
    if (res.exists && res.bundle) {
      state.currentBundle = res.bundle;
      renderCuesEditor();
    } else {
      state.currentBundle = null;
      document.getElementById('cues-editor').innerHTML = res.has_srt
        ? '<div class="muted">Pulsa "Empaquetar SRT" para traducir o ajustar subtítulos.</div>'
        : '<div class="muted">Este clip no tiene archivo captions.srt.</div>';
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderCuesEditor() {
  const container = document.getElementById('cues-editor');
  if (!state.currentBundle || !state.currentBundle.cues) {
    container.innerHTML = '<div class="muted">Sin bloques cargados.</div>';
    return;
  }
  container.innerHTML = '';
  state.currentBundle.cues.forEach((cue) => {
    const row = document.createElement('div');
    row.className = 'cue-row';
    const countSpanId = `count-${Number(cue.id)}`;
    const len = (cue.translated_text || '').length;
    row.innerHTML = `
      <div class="cue-meta">
        <span>#${Number(cue.id)} · ${Number(cue.start).toFixed(2)}s → ${Number(cue.end).toFixed(2)}s</span>
        <span id="${countSpanId}" class="${len > 36 ? 'char-warn' : ''}">${len}/36 chars</span>
      </div>
      <div class="cue-src">Original: ${esc(cue.source_text)}</div>
      <input type="text" data-cue-id="${Number(cue.id)}" value="${esc(cue.translated_text || '')}" placeholder="Escribe la traducción natural aquí..." />
    `;
    const input = row.querySelector('input');
    input.addEventListener('input', (e) => {
      cue.translated_text = e.target.value;
      const counter = document.getElementById(countSpanId);
      counter.textContent = `${cue.translated_text.length}/36 chars`;
      counter.className = cue.translated_text.length > 36 ? 'char-warn' : '';
    });
    container.appendChild(row);
  });
}

async function refreshJobs() {
  try {
    const data = await api('/api/jobs');
    const box = document.getElementById('job-list');
    if (!data.jobs.length) {
      box.innerHTML = '<div class="muted">Sin trabajos activos</div>';
      return;
    }
    box.innerHTML = '';
    data.jobs.slice(0, 8).forEach((job) => {
      const div = document.createElement('div');
      div.className = `job-item ${esc(job.status)}`;
      const lastLog = job.error || (job.logs && job.logs[job.logs.length - 1]) || job.status;
      div.innerHTML = `<strong>${esc(job.label)}</strong><div class="muted">${esc(job.status)} · ${esc(lastLog)}</div>`;
      box.appendChild(div);

      if (job.id === state.activeReviewJobId && job.status === 'done' && job.result?.reports?.length) {
        state.activeReviewJobId = null;
        const reportBox = document.getElementById('agent-report-box');
        document.getElementById('agent-report-text').textContent = job.result.reports.map((r) => r.content).join('\n\n---\n\n');
        reportBox.classList.remove('hidden');
        showToast('Revisión de agente completada');
      }
    });
  } catch (err) {
    console.error(err);
  }
}

// Live controls
document.getElementById('live-watch-btn').addEventListener('click', async () => {
  try {
    const res = await api('/api/live/embed', {
      method: 'POST',
      body: JSON.stringify({ url: document.getElementById('live-url').value }),
    });
    if (res.embed.embed_url) {
      mountLiveEmbed(res.embed.embed_url, res.embed.url);
      showToast(`Viendo ${res.embed.platform}/${res.embed.channel}`);
    } else {
      showToast('Esta URL no soporta reproductor embebido, pero sí puedes grabarla.', true);
    }
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('live-start-btn').addEventListener('click', async () => {
  try {
    const url = document.getElementById('live-url').value;
    // Load the live embed immediately even if yt-dlp takes a couple seconds
    api('/api/live/embed', { method: 'POST', body: JSON.stringify({ url }) })
      .then((r) => { if (r.embed?.embed_url) mountLiveEmbed(r.embed.embed_url, r.embed.url); })
      .catch(() => {});

    const res = await api('/api/live/start', {
      method: 'POST',
      body: JSON.stringify({
        url,
        quality: document.getElementById('live-quality').value,
        save_full_recording: document.getElementById('live-save-full').checked,
        auto_clip: document.getElementById('live-autoclip').checked,
        auto_clip_seconds: Number(document.getElementById('live-autoclip-seconds').value),
        auto_sensitivity: document.getElementById('live-sensitivity').value,
        auto_process: document.getElementById('live-auto-process').checked,
      }),
    });
    renderLiveStatus(res.live);
    showToast('Stream conectado y grabando en workspace/live_recordings/');
  } catch (err) {
    showToast(err.message, true);
    refreshLogs();
  }
});

document.getElementById('live-autoclip').addEventListener('change', async () => {
  try {
    const res = await api('/api/live/autoclip', {
      method: 'POST',
      body: JSON.stringify({
        auto_clip: document.getElementById('live-autoclip').checked,
        auto_clip_seconds: Number(document.getElementById('live-autoclip-seconds').value),
        auto_sensitivity: document.getElementById('live-sensitivity').value,
      }),
    });
    renderLiveStatus(res.live);
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('live-stop-btn').addEventListener('click', async () => {
  try {
    showToast('Deteniendo y ensamblando grabación completa...');
    const res = await api('/api/live/stop', { method: 'POST', body: '{}' });
    renderLiveStatus(res.live);
    refreshSources();
    refreshLibrary();
    showToast(res.live.full_recording_path ? 'Grabación completa guardada en workspace/live_recordings/' : 'Captura detenida');
  } catch (err) {
    showToast(err.message, true);
  }
});

document.querySelectorAll('.live-cut-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    try {
      const res = await api('/api/live/clip', {
        method: 'POST',
        body: JSON.stringify({
          window_seconds: Number(btn.dataset.seconds),
          auto_process: document.getElementById('live-auto-process').checked,
        }),
      });
      renderLiveStatus(res.live);
      refreshSources();
      refreshJobs();
      showToast(`Clip guardado en workspace/live_clips/: ${res.clip.clip_id}`);
    } catch (err) {
      showToast(err.message, true);
    }
  });
});

// Simple Process VOD
document.getElementById('proc-start-btn').addEventListener('click', async () => {
  try {
    const res = await api('/api/process', {
      method: 'POST',
      body: JSON.stringify({
        source: document.getElementById('proc-source').value,
        layout: document.getElementById('proc-layout').value,
        cam_region: document.getElementById('proc-cam-region').value,
        caption_style: document.getElementById('proc-caption-style').value,
        language: document.getElementById('proc-language').value,
        model: document.getElementById('proc-model').value,
        count: Number(document.getElementById('proc-count').value),
        min_seconds: Number(document.getElementById('proc-min').value),
        max_seconds: Number(document.getElementById('proc-max').value),
        ranges: document.getElementById('proc-ranges').value,
        denoise: document.getElementById('proc-denoise').checked,
      }),
    });
    refreshJobs();
    showToast(`Generando clips (${res.job.id})...`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Translation & Agent review
document.getElementById('batch-select').addEventListener('change', (e) => {
  state.selectedBatchId = e.target.value;
  state.selectedClipId = '';
  renderClipList();
});

function requireSelectedClip() {
  if (!state.selectedBatchId || !state.selectedClipId) {
    showToast('Primero genera y selecciona un clip 9:16 en "Revisar y Traducir".', true);
    return false;
  }
  return true;
}

document.getElementById('tr-pack-btn').addEventListener('click', async () => {
  if (!requireSelectedClip()) return;
  try {
    const dst = document.getElementById('tr-dst').value;
    const res = await api('/api/translation/pack', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        source_lang: document.getElementById('tr-src').value,
        target_lang: dst,
        channel: dst === 'es' ? 'es_main' : 'en_main',
        context: document.getElementById('tr-context').value,
      }),
    });
    state.currentBundle = res.bundle;
    renderCuesEditor();
    showToast(`Paquete listo (${res.bundle.cues.length} bloques)`);
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('tr-save-btn').addEventListener('click', async () => {
  if (!state.currentBundle) return showToast('Empaqueta primero un SRT.', true);
  try {
    await api('/api/translation/save', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        bundle: state.currentBundle,
      }),
    });
    showToast('Borrador guardado');
  } catch (err) {
    showToast(err.message, true);
  }
});

async function triggerAgentReview(agent) {
  if (!requireSelectedClip()) return;
  if (state.currentBundle) {
    await api('/api/translation/save', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        bundle: state.currentBundle,
      }),
    });
  }
  const res = await api('/api/agent-review', {
    method: 'POST',
    body: JSON.stringify({
      agent,
      batch: state.selectedBatchId,
      clip: state.selectedClipId,
      target_lang: document.getElementById('tr-dst').value,
      task: 'Revisa naturalidad EN↔ES, tono gamer/stream, glosario y límite de 36 caracteres por línea.',
    }),
  });
  state.activeReviewJobId = res.job.id;
  refreshJobs();
  showToast(`Enviado a ${agent} (${res.job.id})...`);
}

document.getElementById('tr-claude-btn').addEventListener('click', () => triggerAgentReview('claude').catch((e) => showToast(e.message, true)));
document.getElementById('tr-codex-btn').addEventListener('click', () => triggerAgentReview('codex').catch((e) => showToast(e.message, true)));

document.getElementById('tr-apply-btn').addEventListener('click', async () => {
  if (!state.currentBundle) return showToast('Empaqueta primero un SRT.', true);
  try {
    const res = await api('/api/translation/apply', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        bundle: state.currentBundle,
        ass_style: document.getElementById('tr-style').value,
        rerender: true,
      }),
    });
    refreshJobs();
    const warnMsg = res.warnings.length ? ` (${res.warnings.length} avisos)` : '';
    showToast(`Traducción aplicada y re-renderizando${warnMsg}`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Publish
document.getElementById('pub-approve-btn').addEventListener('click', async () => {
  if (!requireSelectedClip()) return;
  try {
    const platforms = Array.from(document.querySelectorAll('.pub-platform:checked')).map((el) => el.value);
    await api('/api/approve', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        title: document.getElementById('pub-title').value,
        caption: document.getElementById('pub-caption').value,
        publish_at: document.getElementById('pub-schedule').value,
        platforms,
      }),
    });
    refreshBatches();
    showToast('Clip aprobado');
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('pub-queue-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId) {
    showToast('Selecciona un lote primero.', true);
    return;
  }
  try {
    const res = await api('/api/queue', {
      method: 'POST',
      body: JSON.stringify({ batch: state.selectedBatchId }),
    });
    document.getElementById('pub-queue-result').textContent = res.message;
    showToast('publicacion.csv exportado');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Library & folders
document.querySelectorAll('.open-folder-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    try {
      const res = await api('/api/open-folder', {
        method: 'POST',
        body: JSON.stringify({ folder: btn.dataset.folder }),
      });
      showToast(`Abierto: ${res.opened}`);
    } catch (err) {
      showToast(err.message, true);
    }
  });
});

document.getElementById('refresh-sources-btn').addEventListener('click', refreshSources);
document.getElementById('refresh-batches-btn').addEventListener('click', refreshBatches);
document.getElementById('refresh-library-btn').addEventListener('click', () => { refreshLibrary(); refreshLogs(); });
document.getElementById('shutdown-btn').addEventListener('click', async () => {
  try {
    await api('/api/shutdown', { method: 'POST', body: '{}' });
    showToast('Servidor local apagado. Puedes cerrar esta ventana.');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Initial load & polling
refreshStatus();
refreshSources();
refreshBatches();
refreshJobs();
setInterval(() => {
  api('/api/live/status').then((d) => renderLiveStatus(d.live)).catch(() => {});
  refreshJobs();
}, 2500);
