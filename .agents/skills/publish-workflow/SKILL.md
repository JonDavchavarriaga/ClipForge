---
name: publish-workflow
description: >-
  Review rendered Gevik Clip shorts, manage clip review states (approved /
  discarded), download final 9:16 MP4 files directly for YouTube Shorts and
  TikTok Studio, or optionally upload via YouTube OAuth CLI with SHA-256 and
  SQLite deduplication. Use when finalizing and shipping a clip batch.
---

# Publish Workflow (Gevik Clip)

## Flujo principal (App de Escritorio)

1. **Revisar y depurar en `🎬 Clips y Mini-Editor`**:
   - Renombra cada clip bueno con su título gancho.
   - Marca **✅ Aprobar** en los clips listos y **🗑️ Descartar** en los que no sirvan.
   - Usa **🧹 Vaciar descartados** para liberar espacio en disco.
2. **Descargar MP4 en 1 clic**:
   - Pulsa **⬇️ Descargar MP4** para guardar `GevikClip_<titulo>.mp4` y subirlo directamente a **YouTube Studio** o **TikTok Studio** (accesos rápidos en la pestaña **🚀 Publicar**).
3. **Limpiar grabaciones procesadas**:
   - En **📁 Biblioteca**, borra los lives completos marcados como `✓ Procesado` para mantener espacio libre en disco.

## Flujo opcional por CLI (Carga directa privada/programada a YouTube)

```powershell
$py = '.\.venv\Scripts\python.exe'
$lote = '.\workspace\rendered_clips\lote-XXXXXXXX'

# 1. Aprobar con huella SHA-256 en approval.json
& $py clipforge.py approve $lote clip-01 --title "El giro inesperado en GTA 6 #shorts" --caption "Mira esta reacción #gta6 #shorts" --platform youtube

# 2. Subir en privado o programado (con deduplicación SQLite)
& $py clipforge.py youtube $lote --client "client_secret.json" --channel-id "UC_TU_CANAL" --clip clip-01 --kids no
```

## Verificación

- Si re-editas un MP4 tras aprobarlo por CLI, vuelve a ejecutar `approve` (el hash SHA-256 bloquea inconsistencias).
- Verifica en YouTube Studio que el video quedó privado o programado correctamente.
