const TOKEN = document.querySelector('meta[name="clipforge-token"]')?.content || '';

const state = {
  batches: [],
  styleCatalog: [],
  selectedBatchId: '',
  selectedClipId: '',
  clipFilter: 'active',
  currentBundle: null,
  currentEmbedUrl: '',
  completedJobIds: new Set(),
  latestReadyBatchId: '',
  procCustomCam: '',
  previewCustomCam: '',
  edCustomCam: '',
};

const cropper = {
  mode: 'process', // 'process' | 'preview' | 'editor'
  offset: 0,
  dragging: false,
  startX: 0,
  startY: 0,
  rect: null,
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
  el.style.borderColor = isError ? '#ef4444' : '#e2e8f0';
  el.classList.remove('hidden');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add('hidden'), 4600);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-ClipForge-Token'] = TOKEN;
  }
  let resp;
  try {
    resp = await fetch(path, { ...options, headers });
  } catch (networkErr) {
    throw new Error('No se pudo conectar con Gevik Clip. Si lo cerraste, vuelve a abrirlo desde tu escritorio.');
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

function switchTab(name) {
  document.querySelectorAll('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
  if (name === 'library') refreshLibrary();
  if (name === 'review') refreshBatches();
}

document.querySelectorAll('.nav-btn').forEach((btn) => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

document.getElementById('queue-toggle-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  document.getElementById('queue-drawer').classList.toggle('hidden');
});

function openBatchInReview(batchId, clipId = '') {
  if (batchId) {
    state.selectedBatchId = batchId;
    state.selectedClipId = clipId || 'clip-01';
  }
  document.getElementById('queue-drawer').classList.add('hidden');
  switchTab('review');
}

document.getElementById('queue-jump-area').addEventListener('click', () => {
  openBatchInReview(state.latestReadyBatchId || (state.batches[0]?.id || ''));
});

document.getElementById('queue-go-review-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  openBatchInReview(state.latestReadyBatchId || (state.batches[0]?.id || ''));
});

document.getElementById('queue-cancel-btn').addEventListener('click', async (e) => {
  e.stopPropagation();
  try {
    await api('/api/jobs/cancel', { method: 'POST', body: '{}' });
    showToast('Cancelando tarea activa...');
    refreshQueue();
  } catch (err) {
    showToast(err.message, true);
  }
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

function releaseVideoPlayer() {
  const player = document.getElementById('clip-player');
  player.pause();
  player.removeAttribute('src');
  player.load();
}

// --- VISUAL SUBTITLE STYLE CARDS ---
function renderStyleGrid(containerId, hiddenInputId, activeId) {
  const box = document.getElementById(containerId);
  const hidden = document.getElementById(hiddenInputId);
  if (!box || !state.styleCatalog.length) return;
  hidden.value = activeId || 'highlight';
  box.innerHTML = '';
  state.styleCatalog.forEach((style) => {
    const card = document.createElement('div');
    card.className = `style-card ${style.id === hidden.value ? 'active' : ''}`;
    const upperStyle = style.uppercase ? 'text-transform:uppercase;' : '';
    const activeSpan = `<span style="color:${esc(style.accent_hex)};${style.word_highlight ? 'font-size:12.5px;' : ''}">${esc(style.sample_active)}</span>`;
    card.innerHTML = `
      <div class="style-preview" style="${upperStyle}">${esc(style.sample_before)} ${activeSpan} ${esc(style.sample_after)}</div>
      <div class="style-name">${esc(style.label)}</div>
    `;
    card.title = style.description;
    card.addEventListener('click', () => {
      hidden.value = style.id;
      box.querySelectorAll('.style-card').forEach((c) => c.classList.remove('active'));
      card.classList.add('active');
    });
    box.appendChild(card);
  });
}

function mountLiveEmbed(embedUrl, rawUrl) {
  const wrap = document.getElementById('live-embed-wrap');
  const extLink = document.getElementById('live-open-ext');
  if (rawUrl) {
    extLink.href = rawUrl;
    extLink.classList.remove('hidden');
  }
  if (!embedUrl || state.currentEmbedUrl === embedUrl) return;
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
  const adBanner = document.getElementById('ad-break-pill');

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

  adBanner.classList.toggle('hidden', !live.in_ad_break);
  if (live.embed_url) mountLiveEmbed(live.embed_url, live.url);

  document.getElementById('live-buffered').textContent = formatDuration(live.buffered_seconds || 0);
  document.getElementById('live-db').textContent = `${live.last_db ?? -60}`;
  document.getElementById('live-clip-count').textContent = String((live.clips || []).length);

  const list = document.getElementById('live-clips-list');
  if (!live.clips || !live.clips.length) {
    list.innerHTML = '<div class="muted">Los clips manuales y automáticos aparecerán aquí.</div>';
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
      showToast(`Cargado ${clip.clip_id}`);
    });
    list.appendChild(row);
  });
}

// --- SNIPPING-TOOL WEBCAM CROPPER ---
function drawCropOverlay() {
  const img = document.getElementById('cropper-img');
  const canvas = document.getElementById('cropper-canvas');
  const w = img.clientWidth;
  const h = img.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  if (!cropper.rect) return;
  const rx = cropper.rect.x * w;
  const ry = cropper.rect.y * h;
  const rw = cropper.rect.w * w;
  const rh = cropper.rect.h * h;

  ctx.fillStyle = 'rgba(0, 0, 0, 0.62)';
  ctx.fillRect(0, 0, w, h);
  ctx.clearRect(rx, ry, rw, rh);

  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 3;
  ctx.strokeRect(rx, ry, rw, rh);

  ctx.fillStyle = '#ffffff';
  const hs = 8;
  [[rx, ry], [rx + rw, ry], [rx, ry + rh], [rx + rw, ry + rh]].forEach(([cx, cy]) => {
    ctx.fillRect(cx - hs / 2, cy - hs / 2, hs, hs);
  });
}

function loadCropperFrame() {
  const img = document.getElementById('cropper-img');
  const applyBtn = document.getElementById('cropper-apply-btn');
  const label = document.getElementById('cropper-coords-label');
  let url = '';
  if (cropper.mode === 'preview' || cropper.mode === 'editor') {
    url = `/api/frame?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(state.selectedClipId)}&t=${cropper.offset}&_=${Date.now()}`;
  } else {
    const src = document.getElementById('proc-source').value.trim();
    url = `/api/frame?source=${encodeURIComponent(src)}&t=${cropper.offset}&_=${Date.now()}`;
  }
  label.textContent = 'Cargando fotograma...';
  applyBtn.disabled = !cropper.rect;
  img.onload = () => {
    drawCropOverlay();
    label.textContent = 'Arrastra con el mouse alrededor de tu cámara.';
  };
  img.onerror = () => {
    label.textContent = 'No se pudo cargar el fotograma.';
  };
  img.src = url;
}

function openCropperModal(mode) {
  if (mode === 'process' && !document.getElementById('proc-source').value.trim()) {
    return showToast('Primero selecciona un video de la lista derecha.', true);
  }
  if ((mode === 'preview' || mode === 'editor') && !state.selectedBatchId) {
    return showToast('Primero selecciona un clip.', true);
  }
  cropper.mode = mode;
  cropper.offset = mode === 'process' ? 5 : 2;
  cropper.rect = null;
  document.getElementById('cropper-apply-btn').disabled = true;
  document.getElementById('cropper-modal').classList.remove('hidden');
  loadCropperFrame();
}

function closeCropperModal() {
  document.getElementById('cropper-modal').classList.add('hidden');
  cropper.dragging = false;
}

const cropCanvas = document.getElementById('cropper-canvas');
cropCanvas.addEventListener('pointerdown', (e) => {
  const box = cropCanvas.getBoundingClientRect();
  cropper.dragging = true;
  cropper.startX = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
  cropper.startY = Math.max(0, Math.min(1, (e.clientY - box.top) / box.height));
  cropCanvas.setPointerCapture(e.pointerId);
});

cropCanvas.addEventListener('pointermove', (e) => {
  if (!cropper.dragging) return;
  const box = cropCanvas.getBoundingClientRect();
  const curX = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
  const curY = Math.max(0, Math.min(1, (e.clientY - box.top) / box.height));
  cropper.rect = {
    x: Math.min(cropper.startX, curX),
    y: Math.min(cropper.startY, curY),
    w: Math.abs(curX - cropper.startX),
    h: Math.abs(curY - cropper.startY),
  };
  drawCropOverlay();
});

cropCanvas.addEventListener('pointerup', () => {
  if (!cropper.dragging) return;
  cropper.dragging = false;
  if (!cropper.rect || cropper.rect.w < 0.08 || cropper.rect.h < 0.08) {
    cropper.rect = null;
    drawCropOverlay();
    document.getElementById('cropper-apply-btn').disabled = true;
    document.getElementById('cropper-coords-label').textContent = 'Haz un recuadro un poco más grande.';
    return;
  }
  document.getElementById('cropper-apply-btn').disabled = false;
  document.getElementById('cropper-coords-label').textContent =
    `✓ Recorte listo (${Math.round(cropper.rect.w * 100)}% × ${Math.round(cropper.rect.h * 100)}%)`;
});

document.getElementById('cropper-next-frame-btn').addEventListener('click', () => { cropper.offset += 15; loadCropperFrame(); });
document.getElementById('cropper-close-btn').addEventListener('click', closeCropperModal);
document.getElementById('cropper-cancel-btn').addEventListener('click', closeCropperModal);
document.getElementById('proc-open-cropper-btn').addEventListener('click', () => openCropperModal('process'));
document.getElementById('preview-open-cropper-btn').addEventListener('click', () => openCropperModal('preview'));
document.getElementById('ed-crop-cam-btn').addEventListener('click', () => openCropperModal('editor'));

document.getElementById('cropper-apply-btn').addEventListener('click', async () => {
  if (!cropper.rect) return;
  const spec = [cropper.rect.x, cropper.rect.y, cropper.rect.w, cropper.rect.h].map((v) => v.toFixed(4)).join(',');
  closeCropperModal();

  if (cropper.mode === 'process') {
    state.procCustomCam = spec;
    document.getElementById('proc-layout').value = 'split-cam';
    document.getElementById('proc-cam-status').textContent = '✓ Cámara recortada manualmente';
    showToast('Recorte de cámara guardado');
    return;
  }
  if (cropper.mode === 'editor') {
    state.edCustomCam = spec;
    document.getElementById('ed-layout').value = 'split-cam';
    showToast('Cámara lista — pulsa "💾 Aplicar edición" para renderizar');
    return;
  }

  state.previewCustomCam = spec;
  document.getElementById('preview-layout').value = 'split-cam';
  releaseVideoPlayer();
  try {
    await api('/api/batch/retest-preview', {
      method: 'POST',
      body: JSON.stringify({ batch: state.selectedBatchId, layout: 'split-cam', cam_region: spec }),
    });
    refreshQueue();
    showToast('Aplicando recorte en clip-01...');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Presets in Tab 2
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
      renderStyleGrid('proc-style-grid', 'proc-caption-style', cfg.style);
    }
  });
});

async function refreshStatus() {
  try {
    const data = await api('/api/status');
    state.styleCatalog = data.style_catalog || [];
    renderStyleGrid('proc-style-grid', 'proc-caption-style', document.getElementById('proc-caption-style').value || 'highlight');
    renderStyleGrid('ed-style-grid', 'ed-caption-style', document.getElementById('ed-caption-style').value || 'highlight');

    const tr = data.translators || {};
    const badges = document.getElementById('agent-badges');
    badges.innerHTML = `
      <span class="badge ${tr.claude?.available ? 'ok' : 'off'}">Claude ${tr.claude?.available ? 'OK' : 'N/D'}</span>
      <span class="badge ${tr.codex?.available ? 'ok' : 'off'}">Codex ${tr.codex?.available ? 'OK' : 'N/D'}</span>
      <span class="badge ${tr.gemini?.available ? 'ok' : 'off'}">Gemini ${tr.gemini?.available ? 'OK' : 'Instalar CLI'}</span>
    `;
    const engineSelect = document.getElementById('tr-engine');
    if (engineSelect) {
      Array.from(engineSelect.options).forEach((opt) => {
        const info = tr[opt.value];
        if (!info) return;
        opt.textContent = info.available ? `${info.label} ✓` : `${info.label} (no instalado)`;
      });
      const currentOk = tr[engineSelect.value]?.available;
      if (!currentOk) {
        const firstReady = ['claude', 'codex', 'gemini'].find((k) => tr[k]?.available);
        if (firstReady) engineSelect.value = firstReady;
      }
    }
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
      list.innerHTML = '<div class="muted">Aún no hay videos en tu biblioteca local.</div>';
      return;
    }
    list.innerHTML = '';
    data.sources.forEach((src) => {
      const row = document.createElement('div');
      row.className = 'list-item';
      const isFull = src.name === 'full_recording.mp4';
      const tag = isFull
        ? '<span class="badge warn">LIVE COMPLETO</span> '
        : (src.mode === 'auto' ? '<span class="badge auto">AUTO</span> ' : '');
      row.innerHTML = `<span>${tag}${esc(src.relative)}</span><span class="muted">${esc(src.size_mb)} MB</span>`;
      row.addEventListener('click', () => {
        document.getElementById('proc-source').value = src.path;
        state.procCustomCam = '';
        document.getElementById('proc-cam-status').textContent = 'Cámara: automática (recorta en fotograma para precisión)';
        document.getElementById('proc-preview-first').checked = isFull || src.size_mb > 45;
        showToast(`Seleccionado: ${src.relative}`);
      });
      list.appendChild(row);
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

function formatSpace(mb) {
  const n = Number(mb || 0);
  return n >= 1024 ? `${(n / 1024).toFixed(2)} GB` : `${n.toFixed(1)} MB`;
}

async function refreshLibrary() {
  try {
    const lib = await api('/api/library');
    document.getElementById('workspace-root-path').textContent = `Carpeta local: ${lib.paths.root}`;
    const recSizeEl = document.getElementById('lib-rec-size');
    const clipsSizeEl = document.getElementById('lib-clips-size');
    if (recSizeEl) recSizeEl.textContent = formatSpace(lib.live_recordings_mb);
    if (clipsSizeEl) clipsSizeEl.textContent = formatSpace(lib.live_clips_mb);

    const renderList = (elId, items, emptyMsg, isFullRec) => {
      const el = document.getElementById(elId);
      if (!items.length) {
        el.innerHTML = `<div class="muted">${emptyMsg}</div>`;
        return;
      }
      el.innerHTML = '';
      items.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'list-item';
        const procBadge = item.processed ? '<span class="badge ok">✓ Procesado</span>' : '';
        row.innerHTML = `
          <div style="min-width:0;flex:1;">
            <strong style="word-break:break-word;">${esc(item.name)}</strong>
            <div class="muted" style="font-size:11px;">${esc(item.relative)} · ${esc(formatSpace(item.size_mb))}</div>
          </div>
          <div class="lib-actions">
            ${procBadge}
            <button type="button" class="btn btn-outline btn-compact lib-use-btn">Procesar →</button>
            <button type="button" class="btn btn-danger btn-compact lib-del-btn" title="Borrar del disco para liberar espacio">🗑️</button>
          </div>
        `;
        row.querySelector('.lib-use-btn').addEventListener('click', (e) => {
          e.stopPropagation();
          document.getElementById('proc-source').value = item.path;
          state.procCustomCam = '';
          document.getElementById('proc-preview-first').checked = item.name === 'full_recording.mp4' || item.size_mb > 45;
          switchTab('process');
          showToast(`Cargado ${item.name}`);
        });
        row.querySelector('.lib-del-btn').addEventListener('click', async (e) => {
          e.stopPropagation();
          const label = isFullRec ? `el live completo "${item.relative}" (${formatSpace(item.size_mb)})` : `"${item.name}" (${formatSpace(item.size_mb)})`;
          if (!window.confirm(`¿Borrar definitivamente ${label} del disco para liberar espacio?`)) return;
          try {
            const res = await api('/api/library/delete', {
              method: 'POST',
              body: JSON.stringify({ path: item.path }),
            });
            showToast(`Eliminado · ${formatSpace(res.freed_mb)} liberados`);
            refreshLibrary();
            refreshSources();
          } catch (err) {
            showToast(err.message, true);
          }
        });
        el.appendChild(row);
      });
    };
    renderList('lib-recordings', lib.live_recordings, 'Cuando detengas un live con "Guardar live completo", aparecerá aquí.', true);
    renderList('lib-live-clips', lib.live_clips, 'Aún no hay clips en vivo guardados.', false);
  } catch (err) {
    showToast(err.message, true);
  }
}

// --- CLIP MANAGER & FILTERS ---
document.querySelectorAll('.filter-pill').forEach((pill) => {
  pill.addEventListener('click', () => {
    document.querySelectorAll('.filter-pill').forEach((p) => p.classList.remove('active'));
    pill.classList.add('active');
    state.clipFilter = pill.dataset.filter;
    renderClipList();
  });
});

function statusLabel(st) {
  if (st === 'approved') return { text: 'Aprobado', cls: 'ok' };
  if (st === 'discarded') return { text: 'Descartado', cls: 'danger' };
  if (st === 'published') return { text: 'Publicado', cls: 'auto' };
  return { text: 'Por revisar', cls: 'warn' };
}

function getSelectedClipObj() {
  const batch = state.batches.find((b) => b.id === state.selectedBatchId);
  if (!batch) return null;
  return batch.clips.find((c) => c.id === state.selectedClipId) || null;
}

async function refreshBatches() {
  try {
    const data = await api('/api/batches');
    state.batches = data.batches;
    const select = document.getElementById('batch-select');
    select.innerHTML = '';
    if (!state.batches.length) {
      select.innerHTML = '<option value="">Sin proyectos aún</option>';
      document.getElementById('clip-selector').innerHTML = '<div class="muted">Genera tu primer clip en "Generar Clips 9:16".</div>';
      document.getElementById('preview-approval-card').classList.add('hidden');
      return;
    }
    state.batches.forEach((b) => {
      const opt = document.createElement('option');
      opt.value = b.id;
      const pending = b.clips.filter((c) => c.review_status === 'pending').length;
      const suffix = b.status === 'awaiting_preview_approval'
        ? ` · ⏳ Prueba cámara (${b.clips.length}/${b.planned_total})`
        : ` (${b.clips.length} clips · ${pending} por revisar)`;
      opt.textContent = `${b.display_name || b.id}${suffix}`;
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

function renderPreviewApprovalBanner(batch) {
  const card = document.getElementById('preview-approval-card');
  if (!batch || batch.status !== 'awaiting_preview_approval' || !batch.remaining_count) {
    card.classList.add('hidden');
    return;
  }
  card.classList.remove('hidden');
  const mutedCount = (batch.muted_ranges || []).length;
  const mutedInfo = mutedCount ? ` · Se saltaron ${mutedCount} bloque(s) comerciales mudos.` : '';
  document.getElementById('preview-approval-summary').textContent =
    `Clip 1 de ${batch.planned_total} listo. Si la cámara no enfoca tu cara, pulsa "✂️ Recortar cámara en fotograma".${mutedInfo}`;
  document.getElementById('preview-continue-btn').textContent =
    `✅ Cámara OK — Generar los ${batch.remaining_count} clips restantes`;
  const firstClip = batch.clips[0];
  if (firstClip?.layout) document.getElementById('preview-layout').value = firstClip.layout;
}

function renderClipList() {
  const batch = state.batches.find((b) => b.id === state.selectedBatchId);
  renderPreviewApprovalBanner(batch);
  const box = document.getElementById('clip-selector');
  box.innerHTML = '';
  if (!batch || !batch.clips.length) {
    box.innerHTML = '<div class="muted">Este proyecto no tiene clips.</div>';
    return;
  }
  const filtered = batch.clips.filter((c) => {
    if (state.clipFilter === 'active') return c.review_status !== 'discarded';
    return c.review_status === state.clipFilter;
  });
  if (!filtered.length) {
    box.innerHTML = '<div class="muted">No hay clips en este filtro.</div>';
    return;
  }
  if (!state.selectedClipId || !filtered.some((c) => c.id === state.selectedClipId)) {
    state.selectedClipId = filtered[0].id;
  }
  filtered.forEach((clip) => {
    const st = statusLabel(clip.review_status);
    const card = document.createElement('div');
    card.className = `clip-card ${clip.id === state.selectedClipId ? 'selected' : ''}`;
    const thumbUrl = `/api/thumb?batch=${encodeURIComponent(batch.id)}&clip=${encodeURIComponent(clip.id)}&_=${Math.round(clip.end * 10)}`;
    card.innerHTML = `
      <img class="clip-thumb" src="${thumbUrl}" alt="${esc(clip.id)}" />
      <div>
        <div class="clip-card-title">${esc(clip.display_title || clip.id)}</div>
        <div class="clip-card-meta">
          <span>${Math.round(clip.end - clip.start)}s</span>
          <span class="badge ${st.cls}">${st.text}</span>
        </div>
      </div>
    `;
    card.addEventListener('click', () => {
      state.selectedClipId = clip.id;
      renderClipList();
      loadSelectedClip();
    });
    box.appendChild(card);
  });
  loadSelectedClip();
}

async function loadSelectedClip() {
  const clip = getSelectedClipObj();
  if (!state.selectedBatchId || !clip) return;

  const player = document.getElementById('clip-player');
  player.src = `/api/media?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(clip.id)}&t=${Date.now()}`;

  document.getElementById('clip-title-input').value = clip.display_title || clip.id;
  const st = statusLabel(clip.review_status);
  const badge = document.getElementById('clip-status-badge');
  badge.textContent = st.text;
  badge.className = `badge ${st.cls}`;

  const discardBtn = document.getElementById('clip-discard-btn');
  discardBtn.textContent = clip.review_status === 'discarded' ? '♻️ Restaurar' : '🗑️ Descartar';

  // Populate Mini-Editor fields
  state.edCustomCam = '';
  document.getElementById('ed-start').value = Number(clip.start).toFixed(1);
  document.getElementById('ed-end').value = Number(clip.end).toFixed(1);
  document.getElementById('ed-layout').value = clip.layout || 'split-cam';
  const defaultY = clip.subtitle_y_pct != null
    ? Number(clip.subtitle_y_pct)
    : (clip.subtitle_position === 'top' ? 18 : (clip.subtitle_position === 'middle' ? 54 : 80));
  document.getElementById('ed-sub-y').value = String(defaultY);
  document.getElementById('ed-sub-y-val').textContent = `${defaultY}%`;
  const splitPct = Number(clip.cam_split_pct || 33);
  document.getElementById('ed-split').value = String(splitPct);
  document.getElementById('ed-split-val').textContent = `${splitPct}%`;
  document.getElementById('ed-offset').value = String(clip.subtitle_offset_ms || 0);
  document.getElementById('ed-offset-val').textContent = `${clip.subtitle_offset_ms || 0} ms`;
  document.getElementById('ed-vol').value = String(clip.volume_db || 0);
  document.getElementById('ed-vol-val').textContent = `${clip.volume_db || 0} dB`;
  document.getElementById('ed-denoise').checked = clip.denoise !== false;
  renderStyleGrid('ed-style-grid', 'ed-caption-style', clip.caption_style || 'highlight');

  document.getElementById('pub-selected-info').textContent = `Clip activo: ${clip.display_title || clip.id}`;
  if (!document.getElementById('pub-title').value) {
    document.getElementById('pub-title').value = clip.display_title || '';
  }
}

// Live guide overlay on 9:16 video player when positioning camera split or subtitles
function showLayoutGuides() {
  const overlay = document.getElementById('player-guide-overlay');
  const camLine = document.getElementById('guide-line-cam');
  const subLine = document.getElementById('guide-line-sub');
  if (!overlay) return;
  const layout = document.getElementById('ed-layout').value;
  const splitPct = Number(document.getElementById('ed-split').value || 33);
  const subY = Number(document.getElementById('ed-sub-y').value || 80);
  camLine.style.display = layout === 'split-cam' ? 'flex' : 'none';
  camLine.style.top = `${splitPct}%`;
  subLine.style.top = `${subY}%`;
  overlay.classList.remove('hidden');
  clearTimeout(showLayoutGuides._t);
  showLayoutGuides._t = setTimeout(() => overlay.classList.add('hidden'), 2600);
}

// Live slider labels in Mini-Editor
document.getElementById('ed-sub-y').addEventListener('input', (e) => {
  document.getElementById('ed-sub-y-val').textContent = `${e.target.value}%`;
  showLayoutGuides();
});
document.querySelectorAll('.pos-preset-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.getElementById('ed-sub-y').value = btn.dataset.y;
    document.getElementById('ed-sub-y-val').textContent = `${btn.dataset.y}%`;
    showLayoutGuides();
  });
});
document.getElementById('ed-split').addEventListener('input', (e) => {
  document.getElementById('ed-split-val').textContent = `${e.target.value}%`;
  document.getElementById('ed-layout').value = 'split-cam';
  showLayoutGuides();
});
document.getElementById('ed-offset').addEventListener('input', (e) => {
  document.getElementById('ed-offset-val').textContent = `${e.target.value} ms`;
});
document.getElementById('ed-vol').addEventListener('input', (e) => {
  document.getElementById('ed-vol-val').textContent = `${e.target.value} dB`;
});

// Mark IN / OUT from video player current time
document.getElementById('ed-mark-in-btn').addEventListener('click', () => {
  const clip = getSelectedClipObj();
  if (!clip) return;
  const t = document.getElementById('clip-player').currentTime || 0;
  document.getElementById('ed-start').value = (Number(clip.start) + t).toFixed(1);
  showToast(`Nuevo inicio marcado (+${t.toFixed(1)}s)`);
});
document.getElementById('ed-mark-out-btn').addEventListener('click', () => {
  const clip = getSelectedClipObj();
  if (!clip) return;
  const t = document.getElementById('clip-player').currentTime || 0;
  if (t < 1.0) return showToast('Avanza al menos 1 segundo para marcar el final.', true);
  document.getElementById('ed-end').value = (Number(clip.start) + t).toFixed(1);
  showToast(`Nuevo final marcado (+${t.toFixed(1)}s)`);
});

// Clip quick actions (Download, Reveal, Rename, Approve, Discard)
document.getElementById('clip-download-btn').addEventListener('click', () => {
  if (!state.selectedBatchId || !state.selectedClipId) return showToast('Selecciona un clip.', true);
  window.location.href = `/api/download?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(state.selectedClipId)}`;
});

document.getElementById('clip-reveal-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId || !state.selectedClipId) return;
  try {
    await api('/api/clip/reveal', { method: 'POST', body: JSON.stringify({ batch: state.selectedBatchId, clip: state.selectedClipId }) });
  } catch (err) {
    showToast(err.message, true);
  }
});

async function updateClipStatus(newStatus, customTitle = undefined) {
  if (!state.selectedBatchId || !state.selectedClipId) return;
  try {
    await api('/api/clip/status', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        status: newStatus || undefined,
        display_title: customTitle,
      }),
    });
    await refreshBatches();
    if (newStatus) showToast(`Estado actualizado: ${statusLabel(newStatus).text}`);
  } catch (err) {
    showToast(err.message, true);
  }
}

document.getElementById('clip-title-input').addEventListener('change', (e) => {
  updateClipStatus('', e.target.value);
});
document.getElementById('clip-approve-quick-btn').addEventListener('click', () => updateClipStatus('approved'));
document.getElementById('clip-discard-btn').addEventListener('click', () => {
  const clip = getSelectedClipObj();
  if (!clip) return;
  updateClipStatus(clip.review_status === 'discarded' ? 'pending' : 'discarded');
});

document.getElementById('purge-discarded-btn').addEventListener('click', async () => {
  try {
    const res = await api('/api/clip/purge-discarded', { method: 'POST', body: '{}' });
    refreshBatches();
    showToast(res.purged ? `Se liberó espacio borrando ${res.purged} clip(s) descartado(s).` : 'No había clips descartados.');
  } catch (err) {
    showToast(err.message, true);
  }
});

// 1-Click Automatic Natural Translation (Claude / Codex / Gemini)
document.getElementById('tr-auto-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId || !state.selectedClipId) return showToast('Selecciona un clip primero.', true);
  releaseVideoPlayer();
  try {
    const engine = document.getElementById('tr-engine').value;
    const res = await api('/api/clip/auto-translate', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        source_lang: document.getElementById('tr-src').value,
        target_lang: document.getElementById('tr-dst').value,
        engine,
        caption_style: document.getElementById('ed-caption-style').value || 'highlight',
        context: document.getElementById('tr-context').value,
      }),
    });
    refreshQueue();
    showToast(`Traduciendo clip completo con ${engine} en la cola...`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Optional manual text editor inside <details>
function renderCuesEditor() {
  const container = document.getElementById('cues-editor');
  if (!state.currentBundle?.cues) return;
  container.innerHTML = '';
  state.currentBundle.cues.forEach((cue) => {
    const row = document.createElement('div');
    row.className = 'cue-row';
    const len = (cue.translated_text || '').length;
    row.innerHTML = `
      <div class="cue-meta"><span>#${Number(cue.id)} · ${Number(cue.start).toFixed(2)}s</span><span class="${len > 36 ? 'char-warn' : ''}">${len}/36</span></div>
      <div class="cue-src">${esc(cue.source_text)}</div>
      <input type="text" value="${esc(cue.translated_text || '')}" />
    `;
    row.querySelector('input').addEventListener('input', (e) => { cue.translated_text = e.target.value; });
    container.appendChild(row);
  });
}

document.getElementById('tr-pack-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId || !state.selectedClipId) return showToast('Selecciona un clip.', true);
  try {
    const dst = document.getElementById('tr-dst').value;
    const existing = await api(`/api/translation?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(state.selectedClipId)}&target_lang=${encodeURIComponent(dst)}`);
    if (existing.exists && existing.bundle) {
      state.currentBundle = existing.bundle;
    } else {
      const res = await api('/api/translation/pack', {
        method: 'POST',
        body: JSON.stringify({
          batch: state.selectedBatchId,
          clip: state.selectedClipId,
          source_lang: document.getElementById('tr-src').value,
          target_lang: dst,
          context: document.getElementById('tr-context').value,
        }),
      });
      state.currentBundle = res.bundle;
    }
    renderCuesEditor();
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('tr-apply-manual-btn').addEventListener('click', async () => {
  if (!state.currentBundle) return showToast('Carga primero los bloques.', true);
  releaseVideoPlayer();
  try {
    await api('/api/translation/apply', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        bundle: state.currentBundle,
        ass_style: document.getElementById('ed-caption-style').value || 'highlight',
        rerender: true,
      }),
    });
    refreshQueue();
    showToast('Re-renderizando clip con texto ajustado...');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Mini-Editor Apply
document.getElementById('ed-apply-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId || !state.selectedClipId) return showToast('Selecciona un clip.', true);
  releaseVideoPlayer();
  try {
    await api('/api/clip/edit', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        clip: state.selectedClipId,
        start: Number(document.getElementById('ed-start').value),
        end: Number(document.getElementById('ed-end').value),
        layout: document.getElementById('ed-layout').value,
        cam_region: state.edCustomCam || undefined,
        cam_split_pct: Number(document.getElementById('ed-split').value),
        caption_style: document.getElementById('ed-caption-style').value,
        subtitle_y_pct: Number(document.getElementById('ed-sub-y').value),
        subtitle_offset_ms: Number(document.getElementById('ed-offset').value),
        volume_db: Number(document.getElementById('ed-vol').value),
        denoise: document.getElementById('ed-denoise').checked,
      }),
    });
    refreshQueue();
    showToast('Aplicando edición en la cola...');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Queue polling
async function refreshQueue() {
  try {
    const snap = await api('/api/jobs');
    const bar = document.getElementById('queue-bar');
    const dot = document.getElementById('queue-dot');
    const headline = document.getElementById('queue-headline');
    const detail = document.getElementById('queue-detail');
    const pctEl = document.getElementById('queue-percent');
    const fill = document.getElementById('queue-progress-fill');
    const countEl = document.getElementById('queue-count');
    const goReviewBtn = document.getElementById('queue-go-review-btn');
    const cancelBtn = document.getElementById('queue-cancel-btn');
    const itemsBox = document.getElementById('queue-items');

    const active = snap.active;
    const pendingTotal = (active ? 1 : 0) + (snap.queued_count || 0);
    countEl.textContent = String(pendingTotal);
    cancelBtn.classList.toggle('hidden', !active);

    const readyJob = snap.jobs.find((j) => j.status === 'done' && j.result?.batch_id);
    if (readyJob) {
      state.latestReadyBatchId = readyJob.result.batch_id;
      goReviewBtn.classList.remove('hidden');
      goReviewBtn.textContent = readyJob.result.status === 'awaiting_preview_approval' ? '🎯 Revisar cámara →' : '🎬 Ir a Clips →';
    } else if (state.batches.length) {
      state.latestReadyBatchId = state.batches[0].id;
      goReviewBtn.classList.remove('hidden');
    } else {
      goReviewBtn.classList.add('hidden');
    }

    if (active) {
      bar.classList.add('running');
      dot.className = 'q-dot running';
      headline.textContent = `⚙️ ${active.stage} · ${active.label} (${formatDuration(active.elapsed_seconds)})`;
      detail.textContent = active.detail || 'Procesando...';
      pctEl.textContent = `${active.percent}%`;
      fill.style.width = `${active.percent}%`;
    } else if (snap.jobs.length) {
      bar.classList.remove('running');
      const last = snap.jobs[0];
      dot.className = `q-dot ${last.status === 'failed' ? 'failed' : (last.status === 'cancelled' ? 'idle' : 'done')}`;
      headline.textContent = last.status === 'failed'
        ? `⚠️ Error en: ${last.label}`
        : (last.status === 'cancelled' ? `⏹️ Cancelado: ${last.label}` : `✅ Listo: ${last.label}`);
      detail.textContent = last.detail || last.stage;
      pctEl.textContent = last.status === 'failed' ? '!' : (last.status === 'cancelled' ? '—' : '100%');
      fill.style.width = last.status === 'done' ? '100%' : '0%';
    } else {
      bar.classList.remove('running');
      dot.className = 'q-dot idle';
      headline.textContent = 'Cola lista · Sin procesos en curso';
      detail.textContent = 'Gevik Clip trabaja en baja prioridad para no afectar tus juegos ni streams.';
      pctEl.textContent = '0%';
      fill.style.width = '0%';
    }

    if (!snap.jobs.length) {
      itemsBox.innerHTML = '<div class="muted">No hay tareas en la cola todavía.</div>';
    } else {
      itemsBox.innerHTML = '';
      snap.jobs.forEach((job) => {
        const row = document.createElement('div');
        row.className = `queue-row ${esc(job.status)}`;
        const statusTag = job.status === 'running'
          ? `⚙️ ${job.percent}% · ${esc(job.stage)}`
          : (job.status === 'queued'
            ? `⏳ #${job.queue_position} En cola`
            : (job.status === 'done' ? `✅ Listo (${formatDuration(job.elapsed_seconds)})` : (job.status === 'cancelled' ? '⏹️ Cancelado' : '❌ Error')));
        row.innerHTML = `
          <div>
            <strong>${esc(job.label)}</strong>
            <div class="muted">${esc(job.detail || job.stage)}</div>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="badge ${job.status === 'done' ? 'ok' : (job.status === 'running' ? 'auto' : 'off')}">${statusTag}</span>
          </div>
        `;
        if (job.status === 'done' && job.result?.batch_id) {
          row.style.cursor = 'pointer';
          row.addEventListener('click', () => openBatchInReview(job.result.batch_id, job.result.clip_id || 'clip-01'));
          const openBtn = document.createElement('button');
          openBtn.className = 'btn btn-silver btn-compact';
          openBtn.textContent = 'Abrir →';
          openBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openBatchInReview(job.result.batch_id, job.result.clip_id || 'clip-01');
          });
          row.lastElementChild.appendChild(openBtn);
        }
        itemsBox.appendChild(row);

        if (job.status === 'done' && !state.completedJobIds.has(job.id)) {
          state.completedJobIds.add(job.id);
          if (job.result?.batch_id) {
            refreshBatches();
            if (['retest-preview', 'edit', 'auto-translate', 'rerender'].includes(job.kind)) {
              loadSelectedClip();
              showToast(`Listo: ${job.label}`);
            } else if (job.result.status === 'awaiting_preview_approval') {
              showToast(`Clip 1 listo para aprobar cámara`);
            } else {
              showToast(`Proyecto listo: ${job.result.display_name || job.result.batch_id}`);
            }
          }
        }
      });
    }
  } catch (err) {
    console.error(err);
  }
}

// Live controls
document.getElementById('live-watch-btn').addEventListener('click', async () => {
  try {
    const res = await api('/api/live/embed', { method: 'POST', body: JSON.stringify({ url: document.getElementById('live-url').value }) });
    if (res.embed.embed_url) mountLiveEmbed(res.embed.embed_url, res.embed.url);
    else showToast('Esta URL no soporta reproductor embebido, pero sí puedes grabarla.', true);
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('live-start-btn').addEventListener('click', async () => {
  try {
    const url = document.getElementById('live-url').value;
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
    showToast('Stream conectado y grabando');
  } catch (err) {
    showToast(err.message, true);
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
    showToast('Deteniendo y guardando grabación completa...');
    const res = await api('/api/live/stop', { method: 'POST', body: '{}' });
    renderLiveStatus(res.live);
    refreshSources();
    refreshLibrary();
    showToast(res.live.full_recording_path ? 'Grabación completa guardada' : 'Captura detenida');
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
      refreshQueue();
      showToast(`Clip guardado: ${res.clip.clip_id}`);
    } catch (err) {
      showToast(err.message, true);
    }
  });
});

// Process VOD -> Queue
document.getElementById('proc-start-btn').addEventListener('click', async () => {
  try {
    const camRegion = state.procCustomCam || document.getElementById('proc-cam-region').value;
    const res = await api('/api/process', {
      method: 'POST',
      body: JSON.stringify({
        source: document.getElementById('proc-source').value,
        layout: document.getElementById('proc-layout').value,
        cam_region: camRegion,
        caption_style: document.getElementById('proc-caption-style').value,
        language: document.getElementById('proc-language').value,
        model: document.getElementById('proc-model').value,
        count: Number(document.getElementById('proc-count').value),
        preview_first: document.getElementById('proc-preview-first').checked,
        min_seconds: Number(document.getElementById('proc-min').value),
        max_seconds: Number(document.getElementById('proc-max').value),
        ranges: document.getElementById('proc-ranges').value,
        denoise: document.getElementById('proc-denoise').checked,
      }),
    });
    refreshQueue();
    showToast(`En cola: ${res.job.label}`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Camera Calibration
document.getElementById('preview-retest-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId) return;
  releaseVideoPlayer();
  try {
    state.previewCustomCam = '';
    await api('/api/batch/retest-preview', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        layout: document.getElementById('preview-layout').value,
        cam_region: document.getElementById('preview-cam-preset').value,
      }),
    });
    refreshQueue();
    showToast('Re-probando cámara en clip-01...');
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('preview-continue-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId) return;
  try {
    await api('/api/batch/continue', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        layout: document.getElementById('preview-layout').value,
        cam_region: state.previewCustomCam || undefined,
      }),
    });
    refreshQueue();
    showToast('Generando clips restantes en la cola...');
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('batch-select').addEventListener('change', (e) => {
  state.selectedBatchId = e.target.value;
  state.selectedClipId = '';
  state.previewCustomCam = '';
  renderClipList();
});

// Publish
document.getElementById('pub-approve-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId || !state.selectedClipId) return showToast('Selecciona un clip.', true);
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
    showToast('Clip aprobado para publicar');
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('pub-download-btn')?.addEventListener('click', () => {
  if (!state.selectedBatchId || !state.selectedClipId) return showToast('Selecciona un clip.', true);
  window.location.href = `/api/download?batch=${encodeURIComponent(state.selectedBatchId)}&clip=${encodeURIComponent(state.selectedClipId)}`;
});

// Folders & Full Clean Shutdown
document.querySelectorAll('.open-folder-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    try {
      const res = await api('/api/open-folder', { method: 'POST', body: JSON.stringify({ folder: btn.dataset.folder }) });
      showToast(`Abierto: ${res.opened}`);
    } catch (err) {
      showToast(err.message, true);
    }
  });
});

document.getElementById('refresh-sources-btn').addEventListener('click', refreshSources);
document.getElementById('refresh-batches-btn').addEventListener('click', refreshBatches);
document.getElementById('refresh-library-btn').addEventListener('click', refreshLibrary);

document.getElementById('shutdown-btn').addEventListener('click', async () => {
  try {
    await api('/api/shutdown', { method: 'POST', body: '{}' });
  } catch (_) {}
  window.close();
  document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;background:#07080b;color:#cbd5e1;font-family:Segoe UI,sans-serif;">Gevik Clip se cerró correctamente. Ya puedes cerrar esta pestaña.</div>';
});

// Initial load & polling
refreshStatus();
refreshSources();
refreshBatches();
refreshQueue();
setInterval(() => {
  api('/api/live/status').then((d) => renderLiveStatus(d.live)).catch(() => {});
  refreshQueue();
}, 2000);
