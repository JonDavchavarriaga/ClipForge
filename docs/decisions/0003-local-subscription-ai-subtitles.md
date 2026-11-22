# ADR-003: Traducción 1-Clic de Subtítulos con Suscripciones IA Locales (Sin Alterar Audio)

## Status
Accepted

## Date
2026-11-21

## Context
El usuario tiene suscripciones personales instaladas en su PC (**Claude Code**, **OpenAI Codex** y opcionalmente **Gemini CLI**) y quiere traducir clips completos entre inglés y español (**EN ↔ ES**) de forma natural para gaming y streams, sin tener que editar manualmente frase por frase ni pagar APIs adicionales por separado.

Además, el objetivo editorial es **cambiar únicamente los subtítulos** y mantener el **audio original del streamer 100% intacto**.

Durante las pruebas en Windows se detectaron fallos concretos en `workspace/logs/errors.jsonl`:
- `shutil.which('codex')` devolvía el shim Unix sin extensión `...\npm\codex`, causando `OSError: [WinError 193] %1 no es una aplicación Win32 válida`.
- `codex exec` fallaba fuera de un repositorio Git sin `--skip-git-repo-check`.
- `claude -p` requería `--output-format text` y limpiar `CLAUDECODE` si se heredaba de una sesión padre.

## Decision
1. **Puente unificado para CLIs de suscripción (`src/clipforge/pipeline/auto_translate.py`)**:
   - Enviar el paquete JSON completo del clip (`captions.srt` empaquetado con contexto, `.agents/rules/translation-style.md` y `.agents/memory/glossary-gaming.md`) en una sola llamada para lograr coherencia global de tono y jerga.
   - Resolver binarios en Windows priorizando `.exe`, `.cmd`, `.bat`, `.ps1` (`resolve_cli_executable` + `build_cli_command`), envolviendo `.cmd` con `cmd.exe /d /s /c` y `.ps1` con `powershell.exe -NoProfile -ExecutionPolicy Bypass -File`.
   - Comandos estándar:
     - **Claude Code**: `claude.exe -p --output-format text` (sin `CLAUDECODE` en el entorno).
     - **Codex (ChatGPT)**: `cmd.exe /d /s /c codex.cmd exec --skip-git-repo-check --ephemeral -s read-only -`.
     - **Gemini**: `gemini` CLI (`@google/gemini-cli` con login de cuenta Google) si está instalado, con fallback opcional a `GEMINI_API_KEY`.
2. **Solo subtítulos, nunca doblaje ni alteración de audio**:
   - La traducción valida estrictamente la integridad de timestamps (`apply_translated_bundle`), escribe `captions.<lang>.srt` + `translation-<lang>.json` y re-renderiza solo la capa visual `.ass` preservando la pista de audio original.
3. **Detección automática en la UI**:
   - `/api/status` reporta disponibilidad real de cada motor en Windows y el selector de la interfaz elige automáticamente un motor instalado y funcional.

## Alternatives Considered

### Integrar SDKs pagos por API (`anthropic`, `openai`) con claves manuales
- **Pros**: Llamadas HTTP directas.
- **Cons**: Obliga al usuario a pagar facturación API separada pese a tener suscripciones activas en su PC y añade gestión de secretos API.
- **Rejected**: Usar las herramientas CLI locales ya autenticadas aprovecha las suscripciones existentes sin guardar claves en el proyecto.

### Traducción offline con modelos locales pequeños (Argos / MarianMT)
- **Pros**: Funciona sin internet.
- **Cons**: Traducciones robóticas literales que destruyen el humor, el sarcasmo y la jerga gamer (ej. GTA 6, Twitch).
- **Rejected**: La naturalidad editorial es crítica para retención en Shorts y TikTok.

## Consequences
- Traducción completa de un clip en 1 clic con preservación garantizada de timestamps y audio original intacto.
- Sin `WinError 193` en Windows ni errores por carpetas sin `.git`.
- Si el usuario instala `npm i -g @google/gemini-cli` e inicia sesión con su cuenta de Google, el motor **Gemini** se activa automáticamente sin cambiar código.
