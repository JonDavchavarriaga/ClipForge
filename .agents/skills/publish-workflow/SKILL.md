---
name: publish-workflow
description: >-
  Review rendered clips, record editorial approval with SHA-256 verification,
  export the multi-platform CSV queue (YouTube Shorts, TikTok, Reels), and
  upload privately or scheduled to YouTube with idempotent SQLite tracking. Use
  when finalizing and shipping a clip batch.
---

# Publish Workflow

## Pasos

1. **Inspeccionar el lote**:
   ```powershell
   python clipforge.py review ".\salidas\lote-XXXXXXXX"
   ```
   Abre los MP4 y confirma encuadre, audio, subtítulos EN/ES y derechos.

2. **Aprobar cada clip listo** (genera huella SHA-256 en `approval.json`):
   ```powershell
   python clipforge.py approve ".\salidas\lote-XXXXXXXX" clip-01 --title "El giro inesperado en GTA 6" --caption "Mira esta reacción al final #gta6 #shorts" --platform youtube --platform tiktok
   ```
   Opcional: añade `--publish-at "2027-01-15T18:00:00-05:00"` para programar.

3. **Generar la cola multiplataforma**:
   ```powershell
   python clipforge.py queue ".\salidas\lote-XXXXXXXX"
   ```
   Crea `publicacion.csv` (protegido contra inyección de fórmulas) para subir en TikTok Studio / Business Suite.

4. **Subir a YouTube Shorts (privado o programado)**:
   ```powershell
   python clipforge.py youtube ".\salidas\lote-XXXXXXXX" --client "client_secret.json" --channel-id "UC_TU_CANAL" --clip clip-01 --kids no
   ```
   Si el intento se interrumpe, revisa Studio y reconcilia con `youtube-resolve --video-id ...` o `--confirm-absent`.

## Verificación

- Si modificas el MP4 tras aprobar, vuelve a ejecutar `approve` (el hash SHA-256 bloqueará cargas inconsistentes).
- Verifica en YouTube Studio que el video quedó privado o programado en la fecha esperada.
