---
name: bilingual-subtitles
description: >-
  Translate and localize ClipForge clip subtitles between English and Spanish
  (EN->ES for the Spanish channel, ES/other->EN for the English channel) with
  strict timestamp preservation, gaming glossary adherence, and optional
  cross-agent review via Claude Code or Codex. Use when preparing bilingual
  captions for a clip.
---

# Bilingual Subtitles (EN ↔ ES)

Este procedimiento garantiza traducciones naturales de gaming/streams sin romper sincronización de tiempos.

## Requisitos previos

- Leer `.agents/rules/translation-style.md` y `.agents/memory/glossary-gaming.md`.
- Tener el archivo `captions.srt` del clip (en el idioma original).

## Pasos

1. **Empaquetar el SRT a JSON seguro**:
   ```powershell
   # Ejemplo: clip en inglés -> subtítulos en español para canal hispano
   python clipforge.py translate-pack ".\salidas\lote-XXXX\clip-01\captions.srt" --source-lang en --target-lang es --channel es_main --context "GTA 6 reacción en stream"
   ```
   Esto crea `translation-es.json` junto al SRT con todos los bloques numerados y tiempos congelados.

2. **Generar borrador traducido**:
   - Completa únicamente `translated_text` en cada bloque del JSON siguiendo `.agents/roles/translator-es-en.md`.
   - Mantén máximo 2 líneas y ≤ 36 caracteres por línea.

3. **Revisión cruzada multi-agente (recomendado)**:
   - Envía `translation-es.json` a **Claude Code** o **Codex** usando el skill [multi-agent-review](../multi-agent-review/SKILL.md):
     ```powershell
     python .agents/skills/multi-agent-review/scripts/agent_review.py --agent claude --role qa-reviewer --input ".\salidas\lote-XXXX\clip-01\translation-es.json" --task "Revisa la naturalidad EN->ES para público gamer hispano, longitud de líneas y jerga."
     ```
   - Aplica las mejoras sugeridas sobre el JSON.

4. **Validar y generar el SRT (y ASS animado opcional) traducido**:
   ```powershell
   .\.venv\Scripts\python.exe clipforge.py translate-apply ".\salidas\lote-XXXX\clip-01\captions.srt" ".\salidas\lote-XXXX\clip-01\translation-es.json" --ass-style highlight --layout split-cam
   ```
   Revisa cualquier `Aviso:` de longitud o velocidad de lectura (cps) y ajusta el texto si es necesario.

5. **Re-renderizar el clip con los subtítulos traducidos en 1 comando**:
   ```powershell
   .\.venv\Scripts\python.exe clipforge.py rerender ".\salidas\lote-XXXX" clip-01 --srt ".\salidas\lote-XXXX\clip-01\captions.es.srt" --caption-style highlight
   ```

## Verificación

- `translate-apply` termina con código 0 y sin avisos críticos.
- El conteo de bloques y timestamps en `captions.<lang>.srt` coincide 1 a 1 con `captions.srt`.
