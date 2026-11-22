# Contrato del Pipeline de Gevik Clip (Live / VOD → Clip → Descarga)

```text
[Live (Twitch / Kick / YouTube) o OBS / Video local]
      │  workspace/live_recordings/<sesion>/full_recording.mp4
      │  workspace/live_clips/<clip>.mp4
      ▼
[1. Generar Clips]  (UI "⚡ Generar Clips" o clipforge.py process)
      │  Detecta comerciales mudos, calcula auto-cantidad y recorta cámara visualmente
      │  Alineación exacta por clip (trim/atrim en filtergraph -> 0 ms de desfase)
      │  workspace/rendered_clips/lote-XXXX/manifest.json + plan.json
      │  workspace/rendered_clips/lote-XXXX/clip-01/video.mp4 + thumb.jpg + captions.srt/.ass
      ▼
[2. Calibración rápida (opcional si preview_first=true)]
      │  Revisa clip-01 -> recorta cámara en fotograma -> "Generar clips restantes"
      ▼
[3. Clips, Traducción 1-Clic y Mini-Editor]
      │  Traduce SOLO subtítulos (EN ↔ ES) con Claude / Codex / Gemini (audio intacto)
      │  Mini-Editor: Trim IN/OUT, estilo visual, altura Y%, tamaño cámara split%, sincronía ms, audio
      │  Estados: Por revisar / Aprobado / Descartado (con vaciado de descartados)
      ▼
[4. Descarga y Gestión de Espacio]
      │  Descarga directa MP4 en 1 clic -> subir a YouTube Shorts / TikTok Studio
      │  (Opcional CLI: clipforge.py youtube con OAuth + youtube.sqlite3)
      │  Biblioteca: borrar lives completos ya procesados y clips viejos para liberar disco
```

## Reglas de transición entre etapas

- Ninguna etapa modifica el video fuente original ni el audio original al traducir subtítulos.
- `translation-<lang>.json` siempre conserva exactamente el mismo número de bloques y timestamps que `captions.srt`.
- `approval.json` se invalida automáticamente si cambia el hash SHA-256 de `video.mp4`.
- Los logs detallados se guardan únicamente en `workspace/logs/` (`clipforge.log`, `events.jsonl`, `errors.jsonl`).
