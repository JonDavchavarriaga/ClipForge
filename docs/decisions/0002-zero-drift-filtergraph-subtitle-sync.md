# ADR-002: Sincronización Exacta de Subtítulos (Cero Desfase) en Grabaciones VFR

## Status
Accepted

## Date
2026-11-21

## Context
Al recortar clips de grabaciones largas de Twitch/Kick (`full_recording.mp4` concatenadas desde segmentos `.ts`, frecuentemente con tasa de fotogramas variable VFR ~58.75 fps), se observaron dos problemas reales verificados en producción:
1. **Desfase de 1–4 segundos donde los subtítulos aparecían antes del audio**: al usar `-ss` de salida después de `-i` en FFmpeg, el grafo `-filter_complex` (incluido `setpts=PTS-STARTPTS` y el quemado `ass=captions.ass`) se ejecutaba *antes* de que FFmpeg descartara los primeros 2.0 segundos en el muxer de salida. El texto quedaba quemado exactamente 2.0 segundos adelantado respecto al audio final.
2. **Incompatibilidad `faster-whisper` 1.2.1 + `PyAV` 19.0.2**: `faster_whisper.audio.decode_audio()` llamaba a `av.open(..., metadata_errors="ignore")`, argumento eliminado en PyAV 19, provocando `TypeError` al transcribir.

## Decision
1. **Recorte dentro del filtergraph (nunca `-ss` de salida junto a `ass=`)**:
   - Usar `_seek_window(start, duration)` en `src/clipforge/pipeline/media.py` para hacer fast-seek rápido `-ss {pre}` antes de `-i` y realizar el recorte fino *dentro* del filtergraph:
     - Video: `trim=start={fine}:duration={duration},setpts=PTS-STARTPTS,scale=...,setsar=1,fps=30,...` y quemar `ass=...` **siempre al final del grafo**, después de `trim` y `setpts`.
     - Audio: `atrim=start={fine}:duration={duration},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,...`.
2. **Alineación exacta por clip (`extract_clip_wav`)**:
   - Extraer el WAV 16 kHz mono del clip exacto usando exactamente el mismo `-ss {pre}` + `atrim`/`asetpts`/`aresample` que `render()`, y transcribirlo con `WhisperModel` cacheado para obtener timestamps 0-based idénticos al audio final del MP4.
   - Al recalibrar cámara (`retest_preview_clip`), reutilizar el `captions.srt` ya alineado sin re-transcribir ni reintroducir deriva.
3. **Bypass definitivo de `PyAV` en `faster-whisper`**:
   - Leer el WAV PCM 16 kHz con la stdlib `wave` + `numpy.float32` (`load_wav_float32()`) y pasar el array 1D directamente a `model.transcribe(audio, ...)`.
4. **Control manual fino en el Mini-Editor**:
   - Exponer `subtitle_y_pct` (12%–90%), `cam_split_pct` (20%–52%) y `subtitle_offset_ms` (`-2000` a `+2000 ms`) con guías visuales sobre el reproductor 9:16.

## Alternatives Considered

### Re-encodear todo el `full_recording.mp4` a CFR 60 fps al terminar el live
- **Pros**: Elimina timestamps VFR de origen.
- **Cons**: Tardaría decenas de minutos en CPU para streams de 2 horas y duplicaría el espacio en disco.
- **Rejected**: El recorte dentro del filtergraph resuelve el problema en pocos segundos por clip sin tocar la grabación original.

### Depender de `stable-ts` o degradar `PyAV`
- **Pros**: Parche rápido para `av.open`.
- **Cons**: Fija versiones viejas de ruedas binarias en Windows y no soluciona el bug del `-ss` de salida antes de `ass=`.
- **Rejected**: `load_wav_float32()` elimina por completo la dependencia de `av.open` y es más rápido y determinista.

## Consequences
- **Regla crítica para futuros cambios**: jamás colocar `-ss` después de `-i` cuando el comando incluya filtros `ass=` o `subtitles=`.
- Cada clip generado mantiene sincronización de 0 ms entre audio y subtítulos, incluso en streams largos concatenados de Twitch.
- El usuario conserva control total sobre altura exacta de subtítulos, tamaño de cámara y offset fino desde el Mini-Editor.
