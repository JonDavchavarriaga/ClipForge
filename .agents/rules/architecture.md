# Reglas de Arquitectura — ClipForge

## 1. Límites de módulos

- `src/clipforge/pipeline/highlights.py`: lógica pura sin subprocess ni red (detección de cortes, parseo y formateo SRT). Debe permanecer rápida y 100% testeable offline.
- `src/clipforge/pipeline/media.py`: integración con FFmpeg, OpenCV y `faster-whisper`. Todas las rutas pasan como argumentos en listas (`subprocess.run([...])`), nunca por shell string.
- `src/clipforge/pipeline/translation.py`: contratos JSON/SRT para traducción EN↔ES y validación determinística de legibilidad. Sin dependencias de SDKs externos.
- `src/clipforge/publishing/publication.py`: integridad SHA-256, CSV seguro contra inyección de fórmulas, ledger SQLite y cliente YouTube OAuth.
- `src/clipforge/cli.py`: capa delgada de entrada/salida y validación de flags; no meter lógica de procesamiento pesada aquí.

## 2. Política escalonada para las 22 herramientas open-source

No instales ni acoples herramientas pesadas por adelantado. Sigue el orden definido en `docs/ARCHITECTURE.md`:

- **Nivel 1 (Activo hoy)**: OBS Studio, `yt-dlp`, FFmpeg + `libass`, `faster-whisper`, OpenCV, Python stdlib.
- **Nivel 2 (Siguiente iteración cuando el flujo base esté validado)**: `stable-ts` (palabras karaoke exactas), `pysubs2` (plantillas ASS dinámicas), `DeepFilterNet` o `Demucs` (limpieza de audio/separación de juego y voz), composición dual webcam + gameplay (split-screen para GTA 6).
- **Nivel 3 (Solo bajo demanda por clip específico)**: `pyannote.audio`, `Supervision` + `Norfair`, `Real-ESRGAN`, `RIFE`, `PyAV`, `Editly`, `Mediabunny`, `OpenCut` / `Kdenlive` / `Shotcut` para retoque manual.

## 3. Regla de compatibilidad

- Mantén funcional `python clipforge.py ...` y `python -m clipforge ...`.
- Toda nueva funcionalidad en `src/clipforge/` debe incluir pruebas unitarias en `tests/`.
