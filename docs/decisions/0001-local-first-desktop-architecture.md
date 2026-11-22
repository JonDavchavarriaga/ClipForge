# ADR-001: Arquitectura de Escritorio Local-First y Rebrand a Gevik Clip

## Status
Accepted

## Date
2026-11-21

## Context
Gevik Clip es una aplicación personal 100% local en Windows para capturar streams en vivo (Twitch, Kick, YouTube Live), generar clips verticales 9:16 con subtítulos bilingües (EN ↔ ES) y editarlos rápidamente sin subir grabaciones de 1–2 horas a la nube ni consumir recursos que afecten juegos o streams activos.

El proyecto nació bajo el nombre interno `ClipForge` (paquete Python `src/clipforge/` y carpeta del workspace `d:\Code\ClipForge`) y evolucionó hacia la marca **Gevik Clip** (identidad negro obsidiana y plateado metálico con monograma **`G`**).

Restricciones clave:
- Cero Docker, cero servidores cloud, cero dependencias Node/Electron pesadas en runtime.
- Arrancar desde un acceso directo en el Escritorio (`pythonw.exe` sin consola) sin crashear si `sys.stdout`/`sys.stderr` son `None`.
- Evitar instancias duplicadas y procesos huérfanos de FFmpeg al cerrar la app.
- No romper el entorno `.venv`, las pruebas ni las importaciones existentes al renombrar la marca.

## Decision
1. **Frontend local + servidor HTTP stdlib**: ejecutar un servidor `ThreadingHTTPServer` exclusivamente en `127.0.0.1` protegido por token de sesión `X-ClipForge-Token` y cabeceras de seguridad (`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN`), servido en ventana de escritorio nativa mediante Edge/Chrome `--app`.
2. **Gestión profesional de procesos (`ProcessRegistry`)**: centralizar todos los subprocesos (FFmpeg, `yt-dlp`, CLIs de IA y ventana UI) en `src/clipforge/process_manager.py` con prioridad `BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW`, bloqueo de instancia única (`.gevik_instance.json`), cancelación instantánea desde la cola y cierre total limpio (`Cerrar Gevik Clip`).
3. **Separación marca visible vs. paquete interno**: adoptar **Gevik Clip** en toda la experiencia de usuario, logotipo (`assets/icons/gevik-clip.svg` y `gevik-clip.ico`), acceso directo (`Gevik Clip.lnk`), documentación y arquitectura `.agents/`, conservando `src/clipforge/` y `clipforge.py` internamente para no romper `.venv` ni rutas previas.

## Alternatives Considered

### Electron / Tauri
- **Pros**: Empaquetado nativo independiente.
- **Cons**: Añade toolchain Node/Rust, cientos de MB de runtime y mayor consumo de RAM junto a OBS/juegos.
- **Rejected**: La ventana `--app` de Edge/Chrome ya viene instalada en Windows y ofrece aceleración por hardware para video 9:16 con sobrecosto mínimo.

### Renombrar la carpeta del proyecto y el paquete Python (`src/gevikclip/`)
- **Pros**: Coincidencia 1:1 del identificador interno con el nombre comercial.
- **Cons**: Invalida el `.venv` actual en Windows, las rutas en `workspace/` y la compatibilidad de scripts existentes sin aportar ningún beneficio al usuario final.
- **Rejected**: Mantener `src/clipforge/` como nombre interno del paquete evita migraciones destructivas innecesarias.

## Consequences
- Arranque instantáneo desde `Gevik Clip.lnk` en el Escritorio sin ventana de consola (`ensure_safe_stdio()` protege `pythonw.exe`).
- La app nunca deja procesos FFmpeg zombis al cancelar o cerrar, y descarga `WhisperModel` de RAM tras inactividad.
- Toda nueva UI, documentación o asset gráfico debe usar exclusivamente la identidad **Gevik Clip** (negro y plateado **`G`**).
