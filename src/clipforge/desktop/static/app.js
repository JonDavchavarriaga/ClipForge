const TOKEN = document.querySelector('meta[name="clipforge-token"]')?.content || '';

const state = {
  batches: [],
  selectedBatchId: '',
  selectedClipId: '',
  currentBundle: null,
  activeReviewJobId: null,
  currentEmbedUrl: '',
  completedJobIds: new Set(),
  latestReadyBatchId: '',
  procCustomCam: '',
  previewCustomCam: '',
};

const cropper = {
  mode: 'process', // 'process' | 'preview'
  offset: 0,
  dragging: false,
  startX: 0,
  startY: 0,
  rect: null, // normalized {x, y, w, h}
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
  showToast._t = setTimeout(() => el.classList.add('hidden'), 4800);
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
    throw new Error('No se pudo conectar con el servidor local de ClipForge. Si lo apagaste, vuelve a abrir ClipForge desde tu escritorio.');
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

function openBatchInReview(batchId) {
  if (batchId) {
    state.selectedBatchId = batchId;
    state.selectedClipId = 'clip-01';
  }
  document.getElementById('queue-drawer').classList.add('hidden');
  switchTab('review');
}

document.getElementById('queue-jump-area').addEventListener('click', () => {
  if (state.latestReadyBatchId) {
    openBatchInReview(state.latestReadyBatchId);
  } else if (state.batches.length) {
    openBatchInReview(state.batches[0].id);
  } else {
    switchTab('review');
  }
});

document.getElementById('queue-go-review-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  openBatchInReview(state.latestReadyBatchId || (state.batches[0]?.id || ''));
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

// --- INTERACTIVE SNIPPING-TOOL WEBCAM CROPPER ---
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

  // Dim outside the selection (Windows Snipping Tool style)
  ctx.fillStyle = 'rgba(0, 0, 0, 0.58)';
  ctx.fillRect(0, 0, w, h);
  ctx.clearRect(rx, ry, rw, rh);

  // Crisp glowing orange frame
  ctx.strokeStyle = '#f97316';
  ctx.lineWidth = 3;
  ctx.strokeRect(rx, ry, rw, rh);

  // Corner handles
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
  if (cropper.mode === 'preview') {
    url = `/api/frame?batch=${encodeURIComponent(state.selectedBatchId)}&t=${cropper.offset}&_=${Date.now()}`;
  } else {
    const src = document.getElementById('proc-source').value.trim();
    url = `/api/frame?source=${encodeURIComponent(src)}&t=${cropper.offset}&_=${Date.now()}`;
  }
  label.textContent = 'Cargando fotograma del stream...';
  applyBtn.disabled = !cropper.rect;
  img.onload = () => {
    drawCropOverlay();
    label.textContent = cropper.rect
      ? 'Recorte listo. Puedes volver a arrastrar o pulsar "Usar este recorte".'
      : 'Arrastra con el mouse alrededor de tu cámara (como Recortes de Windows).';
  };
  img.onerror = () => {
    label.textContent = 'No se pudo cargar el fotograma. Verifica el video seleccionado.';
  };
  img.src = url;
}

function openCropperModal(mode) {
  if (mode === 'process' && !document.getElementById('proc-source').value.trim()) {
    return showToast('Primero selecciona un video de la lista derecha.', true);
  }
  if (mode === 'preview' && !state.selectedBatchId) {
    return showToast('Primero selecciona un lote.', true);
  }
  cropper.mode = mode;
  cropper.offset = mode === 'preview' ? 2 : 5;
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
  const x = Math.min(cropper.startX, curX);
  const y = Math.min(cropper.startY, curY);
  const w = Math.abs(curX - cropper.startX);
  const h = Math.abs(curY - cropper.startY);
  cropper.rect = { x, y, w, h };
  drawCropOverlay();
});

cropCanvas.addEventListener('pointerup', () => {
  if (!cropper.dragging) return;
  cropper.dragging = false;
  if (!cropper.rect || cropper.rect.w < 0.08 || cropper.rect.h < 0.08) {
    cropper.rect = null;
    drawCropOverlay();
    document.getElementById('cropper-apply-btn').disabled = true;
    document.getElementById('cropper-coords-label').textContent = 'Haz un recuadro un poco más grande alrededor de tu cámara.';
    return;
  }
  document.getElementById('cropper-apply-btn').disabled = false;
  document.getElementById('cropper-coords-label').textContent =
    `✓ Recorte seleccionado (${Math.round(cropper.rect.w * 100)}% × ${Math.round(cropper.rect.h * 100)}%)`;
});

document.getElementById('cropper-next-frame-btn').addEventListener('click', () => {
  cropper.offset += 15;
  loadCropperFrame();
});
document.getElementById('cropper-close-btn').addEventListener('click', closeCropperModal);
document.getElementById('cropper-cancel-btn').addEventListener('click', closeCropperModal);
document.getElementById('proc-open-cropper-btn').addEventListener('click', () => openCropperModal('process'));
document.getElementById('preview-open-cropper-btn').addEventListener('click', () => openCropperModal('preview'));

document.getElementById('cropper-apply-btn').addEventListener('click', async () => {
  if (!cropper.rect) return;
  const spec = [cropper.rect.x, cropper.rect.y, cropper.rect.w, cropper.rect.h]
    .map((v) => v.toFixed(4))
    .join(',');
  closeCropperModal();

  if (cropper.mode === 'process') {
    state.procCustomCam = spec;
    document.getElementById('proc-layout').value = 'split-cam';
    document.getElementById('proc-cam-status').textContent = '✓ Cámara recortada manualmente (lista para split-cam)';
    showToast('Recorte de cámara guardado para generar clips');
    return;
  }

  // Preview mode: immediately re-render clip-01 with the exact snipped webcam box!
  state.previewCustomCam = spec;
  document.getElementById('preview-layout').value = 'split-cam';
  releaseVideoPlayer();
  try {
    await api('/api/batch/retest-preview', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        layout: 'split-cam',
        cam_region: spec,
      }),
    });
    refreshQueue();
    showToast('Aplicando tu recorte de cámara en clip-01...');
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
        document.getElementById('proc-cam-status').textContent = 'Cámara: detección automática (o recórtala en el fotograma)';
        document.getElementById('proc-preview-first').checked = isFull || src.size_mb > 45;
        showToast(`Seleccionado: ${src.relative}`);
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
          state.procCustomCam = '';
          document.getElementById('proc-cam-status').textContent = 'Cámara: detección automática (o recórtala en el fotograma)';
          document.getElementById('proc-preview-first').checked = item.name === 'full_recording.mp4' || item.size_mb > 45;
          switchTab('process');
          showToast(`Cargado ${item.name}`);
        });
        el.appendChild(row);
      });
    };
    renderList('lib-recordings', lib.live_recordings, 'Cuando detengas un live con "Guardar live completo", aparecerá aquí.');
    renderList('lib-live-clips', lib.live_clips, 'Aún no hay clips en vivo guardados.');
  } catch (err) {
    showToast(err.message, true);
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
      document.getElementById('clip-selector').innerHTML = '<div class="muted">Genera tu primer clip en "Generar Clips 9:16".</div>';
      document.getElementById('preview-approval-card').classList.add('hidden');
      return;
    }
    state.batches.forEach((b) => {
      const opt = document.createElement('option');
      opt.value = b.id;
      const suffix = b.status === 'awaiting_preview_approval'
        ? ` — ⏳ Prueba cámara (${b.clips.length}/${b.planned_total})`
        : ` (${b.clips.length} clips)`;
      opt.textContent = `${b.id}${suffix}`;
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
  const mutedInfo = mutedCount ? ` · Se saltaron ${mutedCount} bloque(s) mudo(s)/comerciales.` : '';
  document.getElementById('preview-approval-summary').textContent =
    `Clip 1 de ${batch.planned_total} listo. Si la cámara no enfoca tu cara, pulsa "✂️ Recortar cámara en el fotograma" y dibuja el cuadro con el mouse.${mutedInfo}`;
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
    const itemsBox = document.getElementById('queue-items');

    const active = snap.active;
    const pendingTotal = (active ? 1 : 0) + (snap.queued_count || 0);
    countEl.textContent = String(pendingTotal);

    const readyJob = snap.jobs.find((j) => j.status === 'done' && j.result?.batch_id);
    if (readyJob) {
      state.latestReadyBatchId = readyJob.result.batch_id;
      goReviewBtn.classList.remove('hidden');
      goReviewBtn.textContent = readyJob.result.status === 'awaiting_preview_approval'
        ? '🎯 Revisar cámara →'
        : '🎬 Ir a Revisar →';
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
      dot.className = `q-dot ${last.status === 'failed' ? 'failed' : 'done'}`;
      headline.textContent = last.status === 'failed'
        ? `⚠️ Error en: ${last.label}`
        : `✅ Listo: ${last.label} (clic aquí para revisar)`;
      detail.textContent = last.detail || last.stage;
      pctEl.textContent = last.status === 'failed' ? '!' : '100%';
      fill.style.width = '100%';
    } else {
      bar.classList.remove('running');
      dot.className = 'q-dot idle';
      headline.textContent = 'Cola lista · Sin procesos en curso';
      detail.textContent = 'Las tareas se ejecutan en orden. Haz clic aquí cuando haya clips listos para ir a Revisar.';
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
            : (job.status === 'done' ? `✅ Listo (${formatDuration(job.elapsed_seconds)})` : '❌ Error'));
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
          row.addEventListener('click', () => openBatchInReview(job.result.batch_id));
          const openBtn = document.createElement('button');
          openBtn.className = 'btn btn-primary btn-compact';
          openBtn.textContent = job.result.status === 'awaiting_preview_approval' ? 'Revisar cámara →' : 'Revisar →';
          openBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openBatchInReview(job.result.batch_id);
          });
          row.lastElementChild.appendChild(openBtn);
        }
        itemsBox.appendChild(row);

        if (job.status === 'done' && !state.completedJobIds.has(job.id)) {
          state.completedJobIds.add(job.id);
          if (job.id === state.activeReviewJobId && job.result?.reports?.length) {
            state.activeReviewJobId = null;
            document.getElementById('agent-report-text').textContent =
              job.result.reports.map((r) => r.content).join('\n\n---\n\n');
            document.getElementById('agent-report-box').classList.remove('hidden');
            showToast('Revisión de agente completada');
          } else if (job.result?.batch_id) {
            refreshBatches();
            if (job.kind === 'retest-preview') {
              loadSelectedClip();
              showToast('Encuadre de cámara actualizado en clip-01');
            } else if (job.result.status === 'awaiting_preview_approval') {
              showToast(`Clip 1 listo para aprobar cámara (${job.result.batch_id})`);
            } else {
              showToast(`Lote terminado: ${job.result.batch_id}`);
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
    showToast(`Añadido a la cola: ${res.job.label}`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Camera Calibration: Retest clip-01 & Continue batch
document.getElementById('preview-retest-btn').addEventListener('click', async () => {
  if (!state.selectedBatchId) return;
  releaseVideoPlayer();
  try {
    const preset = document.getElementById('preview-cam-preset').value;
    state.previewCustomCam = '';
    await api('/api/batch/retest-preview', {
      method: 'POST',
      body: JSON.stringify({
        batch: state.selectedBatchId,
        layout: document.getElementById('preview-layout').value,
        cam_region: preset,
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
    showToast('Generando los clips restantes en la cola...');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Translation & Agent review
document.getElementById('batch-select').addEventListener('change', (e) => {
  state.selectedBatchId = e.target.value;
  state.selectedClipId = '';
  state.previewCustomCam = '';
  renderClipList();
});

function requireSelectedClip() {
  if (!state.selectedBatchId || !state.selectedClipId) {
    showToast('Primero genera y selecciona un clip en "Revisar y Traducir".', true);
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
  if (!requireSelectedClip() || !state.currentBundle) return showToast('Empaqueta primero un SRT.', true);
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
  refreshQueue();
  showToast(`Enviado a ${agent} en la cola...`);
}

document.getElementById('tr-claude-btn').addEventListener('click', () => triggerAgentReview('claude').catch((e) => showToast(e.message, true)));
document.getElementById('tr-codex-btn').addEventListener('click', () => triggerAgentReview('codex').catch((e) => showToast(e.message, true)));

document.getElementById('tr-apply-btn').addEventListener('click', async () => {
  if (!requireSelectedClip() || !state.currentBundle) return showToast('Empaqueta primero un SRT.', true);
  releaseVideoPlayer();
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
    refreshQueue();
    const warnMsg = res.warnings.length ? ` (${res.warnings.length} avisos)` : '';
    showToast(`Traducción en cola para re-renderizar${warnMsg}`);
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
  if (!state.selectedBatchId) return showToast('Selecciona un lote primero.', true);
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

// Folders
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
document.getElementById('refresh-library-btn').addEventListener('click', refreshLibrary);
document.getElementById('shutdown-btn').addEventListener('click', async () => {
  try {
    await api('/api/shutdown', { method: 'POST', body: '{}' });
    showToast('Servidor local apagado. Puedes cerrar esta ventana.');
  } catch (err) {
    showToast(err.message, true);
  }
});

// Initial load & live polling
refreshStatus();
refreshSources();
refreshBatches();
refreshQueue();
setInterval(() => {
  api('/api/live/status').then((d) => renderLiveStatus(d.live)).catch(() => {});
  refreshQueue();
}, 2000);
