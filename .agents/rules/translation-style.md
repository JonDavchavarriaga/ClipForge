# Reglas de Traducción y Subtítulos Bilingües (EN ↔ ES) — Gevik Clip

Estas reglas aplican cuando se traducen clips de **inglés a español** (canal hispano) o de **español/otros idiomas a inglés** (canal angloparlante). **La traducción afecta únicamente a los subtítulos; el audio original del video siempre se mantiene intacto.**

## 1. Naturalidad sobre literalidad

- Traduce el **clip completo en contexto** capturando el impacto emocional y el ritmo oral del streamer o gamer, no frases aisladas palabra por palabra.
- Evita español neutro robótico o calcos literales (ej. no traduzcas "no way!" como "de ninguna manera" en medio de una jugada de GTA 6; usa "¡No puede ser!" o "¡Ni loco!").
- Consulta siempre `.agents/memory/glossary-gaming.md` para respetar términos que la comunidad gamer deja en inglés (*spawn*, *clutch*, *nerf*, *HUD*, *NPC*, *lobby*).

## 2. Restricciones técnicas para formato vertical (9:16)

- **Máximo 2 líneas por bloque** (`\n` solo si mejora la lectura).
- **Máximo 36 caracteres por línea** (recomendado 24–32 para que el texto grande en Shorts/TikTok no invada los bordes ni botones).
- **Velocidad de lectura objetivo**: ≤ 20–22 caracteres por segundo (condensa muletillas como "uh", "like", "o sea" cuando el hablante va rápido).
- **Timestamps inmutables**: nunca cambies `id`, `start`, `end` ni la cantidad de bloques del paquete `translation-*.json`.

## 3. Consistencia de doble canal

- **Canal ES (`es_main`)**: español latinoamericano natural y dinámico (entendible en Colombia, México, LATAM y España).
- **Canal EN (`en_main`)**: inglés conversacional moderno de YouTube/Twitch (*don't*, *that's*, *gonna* cuando encaja con el hablante).

## 4. Traducción automática 1-clic y revisión multi-agente

- En la app **Gevik Clip** (pestaña **Clips y Mini-Editor**), el botón **✨ Traducir clip completo y aplicar subtítulos** envía el paquete JSON completo a tu suscripción local (**Claude Code**, **Codex** o **Gemini**), valida los timestamps con `apply_translated_bundle` y re-renderiza solo la capa visual `.ass` conservando el audio original y tu posición vertical (`subtitle_y_pct`).
- Para auditoría manual opcional, usa `.agents/skills/multi-agent-review/SKILL.md`.
