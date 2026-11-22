# Contrato del Pipeline VOD → Clip → Publicación

```text
[OBS / yt-dlp]
      │  fuentes/<sesion>/fuente.mp4 + origen.json
      ▼
[1. Análisis y Transcripción]  (clipforge.py process)
      │  salidas/lote-XXXX/manifest.json
      │  salidas/lote-XXXX/clip-01/video.mp4
      │  salidas/lote-XXXX/clip-01/captions.srt
      ▼
[2. Traducción EN ↔ ES (opcional)]
      │  clipforge.py translate-pack -> translation-<lang>.json
      │  Borrador (Antigravity / translator-es-en)
      │  Revisión cruzada (Claude Code / Codex vía agent_review.py -> .agents/handoffs/)
      │  clipforge.py translate-apply -> captions.<lang>.srt
      │  Re-render final con subtítulos traducidos
      ▼
[3. Revisión Humana y Aprobación]
      │  clipforge.py review -> inspección del MP4
      │  clipforge.py approve -> approval.json (con sha256)
      ▼
[4. Distribución]
      │  clipforge.py queue -> publicacion.csv (TikTok Studio / Reels)
      │  clipforge.py youtube -> carga privada/programada + salidas/youtube.sqlite3
```

## Reglas de transición entre etapas

- Ninguna etapa puede sobrescribir el video fuente original en `fuentes/`.
- `translation-<lang>.json` siempre debe conservar el mismo número de bloques y timestamps que `captions.srt`.
- `approval.json` queda invalidado automáticamente si `video.mp4` cambia de hash SHA-256.
- Los archivos generados por revisiones entre agentes viven únicamente en `.agents/handoffs/`.
