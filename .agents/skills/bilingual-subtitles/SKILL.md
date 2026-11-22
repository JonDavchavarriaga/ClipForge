---
name: bilingual-subtitles
description: >-
  Translate and localize Gevik Clip subtitles between English and Spanish
  (EN->ES or ES/other->EN) in 1 click using installed local AI subscriptions
  (Claude Code, Codex, Gemini) while keeping original audio untouched and
  preserving exact timestamps. Use when preparing bilingual captions for a clip.
---

# Bilingual Subtitles (EN ↔ ES) — Gevik Clip

Este procedimiento traduce **únicamente los subtítulos** de un clip completo con naturalidad gamer, manteniendo el **audio original intacto** y timestamps 100% preservados.

## Requisitos previos

- Leer `.agents/rules/translation-style.md` y `.agents/memory/glossary-gaming.md`.
- Tener `captions.srt` en la carpeta del clip y al menos un CLI IA local instalado (`claude.exe`, `codex.cmd` o `gemini`).

## Flujo recomendado (1 clic desde Gevik Clip)

1. Abre **🎬 Clips y Mini-Editor** y selecciona el clip.
2. En **🌐 Traducir Subtítulos en 1 Clic (IA de tu PC)** elige idioma origen/destino, motor disponible (`Claude Code`, `Codex` o `Gemini`) y contexto opcional (ej. `GTA 6 reacción`).
3. Pulsa **✨ Traducir clip completo y aplicar subtítulos**.
   - `src/clipforge/pipeline/auto_translate.py` envía el clip completo en contexto, valida los timestamps con `apply_translated_bundle`, guarda `captions.<lang>.srt` + `translation-<lang>.json` y re-renderiza solo la capa visual `.ass` sin alterar el audio.

## Flujo alternativo por CLI

```powershell
# 1. Empaquetar SRT a JSON con timestamps congelados
.\.venv\Scripts\python.exe clipforge.py translate-pack ".\workspace\rendered_clips\lote-XXXX\clip-01\captions.srt" --source-lang en --target-lang es --channel es_main --context "GTA 6"

# 2. (Opcional) Pedir revisión cruzada a Claude Code o Codex
.\.venv\Scripts\python.exe .agents/skills/multi-agent-review/scripts/agent_review.py --agent claude --role qa-reviewer --input ".\workspace\rendered_clips\lote-XXXX\clip-01\translation-es.json" --task "Revisa naturalidad EN->ES y longitud <=36 chars."

# 3. Validar y aplicar timestamps exactos
.\.venv\Scripts\python.exe clipforge.py translate-apply ".\workspace\rendered_clips\lote-XXXX\clip-01\captions.srt" ".\workspace\rendered_clips\lote-XXXX\clip-01\translation-es.json" --ass-style highlight --layout split-cam

# 4. Re-renderizar solo los subtítulos
.\.venv\Scripts\python.exe clipforge.py rerender ".\workspace\rendered_clips\lote-XXXX" clip-01 --srt ".\workspace\rendered_clips\lote-XXXX\clip-01\captions.es.srt" --caption-style highlight
```

## Verificación

- El conteo de bloques y timestamps de `captions.<lang>.srt` coincide 1 a 1 con `captions.srt`.
- El audio original del `video.mp4` permanece intacto y sincronizado con los nuevos subtítulos.
