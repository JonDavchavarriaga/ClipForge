---
name: vod-to-clips
description: >-
  Run the local VOD-to-clips pipeline in Gevik Clip (transcribe with
  faster-whisper, skip muted commercials, auto-scale clip count, calibrate
  webcam crop visually, and render zero-drift 9:16 MP4 clips). Use when turning
  a long recording or stream into vertical short candidates.
---

# VOD to Clips (Gevik Clip)

Sigue este flujo para transformar una grabación larga (live de 1h–2h+ en `workspace/live_recordings/`, clip en vivo u OBS) en clips verticales 9:16 sin desfase de subtítulos.

## Pasos

1. **Elegir fuente**:
   - Desde la app **Gevik Clip**: pestaña **⚡ Generar Clips** o botón `Procesar →` en **📁 Biblioteca**.
   - Por CLI: cualquier archivo local (por defecto `--rights local`).
2. **Elegir encuadre y estilo visual**:
   - **Stream con cámara + gameplay (GTA 6)**: `--layout split-cam` + recorte visual en fotograma (`--cam-region x,y,w,h` o `auto`).
   - **Gameplay puro sin cámara**: `--layout fit`.
   - **Hablando a cámara**: `--layout face --denoise`.
   - **Estilos `.ass` disponibles**: `highlight` (Amarillo Viral), `neon` (Cian Gaming), `fire` (Impacto Naranja), `silver` (Gevik Plata), `bold` (Blanco Limpio) o `none`.
3. **Procesar lote (con validación rápida en 1er clip para lives largos)**:
   ```powershell
   .\.venv\Scripts\python.exe clipforge.py process "<ruta_video>" --count 0 --layout split-cam --cam-region auto --caption-style highlight --language auto --denoise
   ```
   - `--count 0` calcula automáticamente la cantidad ideal de clips según la duración y omite bloques comerciales mudos.
   - `extract_clip_wav` + `trim`/`atrim` en filtergraph garantizan 0 ms de desfase entre audio y subtítulos (ver `docs/decisions/0002-zero-drift-filtergraph-subtitle-sync.md`).
4. **Afinar en Mini-Editor y limpiar espacio**:
   - Ajusta `IN`/`OUT`, altura vertical `subtitle_y_pct`, tamaño de cámara `cam_split_pct` o estilo visual en **🎬 Clips y Mini-Editor**.
   - Una vez descargados los clips buenos, borra el live completo ya procesado desde **📁 Biblioteca** para liberar disco.

## Verificación

- Existe `manifest.json` y `plan.json` en `workspace/rendered_clips/lote-XXXXXXXX/`.
- Cada `clip-XX/` contiene `video.mp4`, `thumb.jpg` y `captions.srt` / `.ass` (si hubo voz).
