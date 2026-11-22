# Hoja de Ruta — ClipForge

## Fase 1 — Base limpia y arquitectura agéntica (Completada)

- [x] Estructura modular `src/clipforge/` + `tests/` + `pyproject.toml` manteniendo compatibilidad con `python clipforge.py`.
- [x] Pipeline local funcional: ingesta (`yt-dlp`), detección de momentos, `faster-whisper`, OpenCV face-tracking, render vertical 1080×1920, aprobación SHA-256, cola CSV y carga idempotente a YouTube.
- [x] Empaquetado y validación de subtítulos bilingües EN↔ES (`translate-pack` / `translate-apply`).
- [x] Carpeta `.agents/` compartida para Gemini Antigravity, Claude Code y Codex CLI (`agent_review.py`).

## Fase 2 — Automatización de clips GTA 6, Streams y Subtítulos Pro

- [x] Nuevo layout `split-cam` (webcam recortada arriba + gameplay 16:9 abajo para streams de GTA 6, con detección automática OpenCV o presets `top-left`/`top-right`/`bottom-left`/`bottom-right`/`x,y,w,h`).
- [x] Plantillas `.ass` dinámicas con `pysubs2` (`highlight`, `neon`, `bold`) usando timestamps por palabra de `faster-whisper` e interpolación automática para subtítulos traducidos EN↔ES.
- [x] Subcomando `rerender` para volver a quemar subtítulos traducidos (`.srt` → `.ass`) o cambiar layout en un clip puntual sin reprocesar todo el VOD.
- [ ] Detección mejorada de hooks en inglés y español + detección de picos visuales/audio en gameplay.
- [ ] Flujo de 1 comando para traducir y pedir doble revisión (`claude` + `codex`) sobre `translation-<lang>.json` y re-renderizar automáticamente.

## Fase 3 — App de Escritorio Local con Logo en el Escritorio y Captura en Vivo

- [x] Captura en vivo con buffer circular sin recodificar (`clipforge.py live` y pestaña **En Vivo** para Twitch / Kick / YouTube Live + guardado de últimos 30s/45s/60s/90s al instante).
- [x] Interfaz local de escritorio (`clipforge.py app` en ventana nativa Edge/Chrome `--app`) para:
  - Capturar clips en vivo o procesar VODs locales con `split-cam` y subtítulos `.ass`.
  - Previsualizar clips 9:16, editar subtítulos EN↔ES lado a lado y disparar revisión de Claude Code / Codex con un clic.
  - Aprobar clips con SHA-256 y exportar la cola CSV para TikTok Studio / YouTube Shorts.
- [x] Ícono multi-resolución `assets/icons/clipforge.ico` y script `scripts/create_desktop_shortcut.ps1` para el acceso directo en el escritorio de Windows.

## Fase 4 — Mejoras opcionales bajo demanda

- [ ] Limpieza de voz avanzada (`DeepFilterNet` / `Demucs`) como flag opcional `--enhance-audio`.
- [ ] Diarización (`pyannote.audio`) y multi-tracking (`Norfair` / `Supervision`) para podcasts/collabs.
- [ ] Upscaling/interpolación puntual (`Real-ESRGAN` / `RIFE`) solo para clips destacados.
