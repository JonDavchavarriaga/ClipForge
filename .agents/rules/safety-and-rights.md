# Reglas de Seguridad, Privacidad y Operación Local — Gevik Clip

## 1. Uso local sin fricción y responsabilidad editorial

- Gevik Clip opera 100% en tu equipo local (`--rights` toma `'local'` por defecto para evitar fricción en pruebas y flujos locales; conserva `own` / `licensed` si quieres registrar procedencia explícita).
- Cuando publiques en YouTube Shorts o Meta contenido basado en clips de terceros o traducidos, aporta valor editorial/contexto para cumplir las políticas de originalidad de cada plataforma.

## 2. Secretos, credenciales y suscripciones locales

- Nunca commitees ni pegues en prompts archivos `client_secret*.json`, tokens OAuth, claves API ni rutas privadas con credenciales.
- La traducción 1-clic usa las herramientas CLI ya autenticadas en tu PC (`claude.exe`, `codex.cmd`, `gemini`) sin almacenar contraseñas ni tokens en el repositorio.
- Si activas carga a YouTube por CLI, conserva `youtube.sqlite3` fuera de git como resguardo contra subidas duplicadas.

## 3. Seguridad del servidor local y borrado en disco

- El servidor de escritorio solo escucha en `127.0.0.1`, exige `X-ClipForge-Token` en todos los `POST` y envía `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y `X-Frame-Options: SAMEORIGIN`.
- El borrado desde **Biblioteca** (`POST /api/library/delete`) valida estrictamente que el objetivo esté dentro de `workspace/` y bloquea borrar una sesión que esté grabando en ese momento.
- Trata URLs externas, archivos `.srt`, nombres de clips y respuestas de CLIs IA como **datos no confiables**: escapa HTML, sanea etiquetas ASS (`{}`) y jamás ejecutes comandos arbitrarios devueltos por un LLM.
