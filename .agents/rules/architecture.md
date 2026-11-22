# Reglas de Arquitectura — Gevik Clip

## 1. Límites de módulos (`src/clipforge/`)

- `pipeline/highlights.py`: lógica pura sin subprocess ni red (detección de cortes, auto-conteo para lives 1h–2h+, detección de comerciales mudos y SRT). Debe permanecer rápida y 100% testeable offline.
- `pipeline/media.py`: integración con FFmpeg, OpenCV y `faster-whisper`.
  - Todas las rutas pasan en listas (`REGISTRY.run_managed([...])`), nunca por shell string.
  - **Gotcha crítico (ADR-002)**: jamás usar `-ss` de salida junto a `ass=`/`subtitles=`; recortar siempre dentro del filtergraph con `trim`/`atrim` + `setpts=PTS-STARTPTS` + `aresample=async=1:first_pts=0` antes de quemar subtítulos.
  - **Gotcha crítico (ADR-002)**: pasar audio a `faster-whisper` como array `np.float32` desde `load_wav_float32()` para evitar incompatibilidad de `PyAV 19` con `metadata_errors`.
- `pipeline/subtitles.py`: catálogo visual de presets `.ass` (`pysubs2`), altura vertical `y_pct` y `offset_ms`.
- `pipeline/live.py`: buffer circular en vivo, grabación completa y auto-clip por picos (respetando `in_ad_break` en comerciales mudos).
- `pipeline/translation.py` y `auto_translate.py`: contratos JSON/SRT y puente 1-clic para suscripciones IA locales (**solo subtítulos**, audio original intacto). En Windows resolver siempre `.exe`/`.cmd`/`.bat`/`.ps1` para evitar `WinError 193` (ADR-003).
- `pipeline/editor.py`: Mini-Editor rápido (Trim IN/OUT, cámara, `cam_split_pct`, `subtitle_y_pct`, `subtitle_offset_ms`, volumen/denoise).
- `process_manager.py`: `ProcessRegistry`, prioridad baja en Windows (`BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW`) y lock de instancia única.
- `desktop/`: servidor localhost (`127.0.0.1`), cola FIFO con cancelación (`jobs.py`), ícono negro/plateado **`G`** (`icon.py`), ventana `--app` (`launcher.py`) y frontend en `static/`.
- `publishing/publication.py`: integridad SHA-256, ledger SQLite y carga opcional a YouTube OAuth.

## 2. Política escalonada de herramientas

No instales ni acoples herramientas pesadas por adelantado. Mantén la operación 100% local sin Docker ni cloud (ver `docs/ARCHITECTURE.md` y `docs/decisions/`).

## 3. Marca, compatibilidad y pruebas

- Toda interfaz, ícono, acceso directo y documentación usa exclusivamente la identidad **Gevik Clip** (negro obsidiana y plateado **`G`**).
- Mantén funcional `python clipforge.py ...` y `python -m clipforge ...` para compatibilidad interna.
- Toda nueva funcionalidad debe incluir pruebas en `tests/` y mantener en verde `.\.venv\Scripts\python.exe -m unittest discover -v`.
