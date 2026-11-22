---
name: vod-to-clips
description: >-
  Run the local VOD-to-clips pipeline in ClipForge (download optional authorized
  source, transcribe with faster-whisper, detect reactions/highlights or apply
  manual ranges, frame for gameplay or webcam, and render 9:16 MP4 clips). Use
  when turning a long recording or stream into vertical short candidates.
---

# VOD to Clips

Sigue este flujo para transformar una grabación larga (OBS o fuente autorizada) en un lote de clips verticales.

## Pasos

1. **Verificar fuente y derechos**:
   - Material propio (OBS): `--rights own`.
   - Material autorizado de terceros: `--rights licensed --rights-note "Titular y referencia"`.
2. **Ingesta opcional por URL autorizada**:
   ```powershell
   python clipforge.py download "<URL>" --output ".\fuentes\sesion-01" --rights licensed --rights-note "<referencia>"
   ```
3. **Elegir layout y opciones según el contenido**:
   - **Stream con cámara + gameplay (ej. GTA 6)**: `--layout split-cam --cam-region auto` (o `bottom-right`, `top-right`, `bottom-left`, `top-left`, o coordenadas normalizadas `x,y,w,h`).
   - **Gameplay puro sin cámara (HUD importante)**: `--layout fit` (y `--range INICIO,FIN` si la jugada clave no tiene mucha voz).
   - **Stream hablando a cámara / reacción**: `--layout face --denoise`.
   - **Subtítulos dinámicos `.ass`**: `--caption-style highlight` (por defecto, resalta palabra a palabra), `neon` (estilo gaming en mayúsculas), `bold` o `srt` clásico.
   - **Audio en idioma extranjero (para traducir después)**: pasa `--language en` (o `--language auto`) para transcribir primero en el idioma original con máxima fidelidad.
4. **Procesar lote**:
   ```powershell
   .\.venv\Scripts\python.exe clipforge.py process "<ruta_video>" --rights own --count 3 --layout split-cam --cam-region auto --caption-style highlight --language en --denoise
   ```
5. **Revisar resultados**:
   ```powershell
   python clipforge.py review ".\salidas\lote-XXXXXXXX"
   ```

## Verificación

- Confirma que exista `manifest.json` en la carpeta del lote (si falta, el lote quedó incompleto y no se debe aprobar).
- Confirma que cada `clip-XX/` tenga `video.mp4` y `captions.srt` (si había diálogo).
