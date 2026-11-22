# Reglas de Traducción y Subtítulos Bilingües (EN ↔ ES)

Estas reglas aplican cuando se traducen clips de **inglés a español** (para tu canal hispano) o de **español/otros idiomas a inglés** (para tu canal angloparlante).

## 1. Naturalidad sobre literalidad

- Traduce el **impacto emocional y el ritmo oral** del streamer o gamer, no palabra por palabra.
- Evita español neutro robótico o calcos literales del inglés (ej. no traduzcas "no way!" como "de ninguna manera" en medio de una jugada de GTA 6; usa "¡No puede ser!" o "¡Ni loco!").
- Consulta siempre `.agents/memory/glossary-gaming.md` para respetar términos que la comunidad gamer deja en inglés (ej. *spawn*, *clutch*, *nerf*, *HUD*, *NPC*, *lobby*).

## 2. Restricciones técnicas para formato vertical (9:16)

- **Máximo 2 líneas por bloque** (`\n` solo si mejora la lectura).
- **Máximo 36 caracteres por línea** (recomendado 24–32 para que el texto grande en Shorts/TikTok no invada los botones laterales ni la zona inferior).
- **Velocidad de lectura objetivo**: ≤ 20–22 caracteres por segundo (condensa muletillas como "uh", "like", "o sea" cuando el hablante va rápido).
- **Timestamps inmutables**: nunca cambies `start`, `end` ni la cantidad de bloques del paquete `translation-*.json`.

## 3. Consistencia de doble canal

- **Canal ES (`es_main`)**: español latinoamericano natural y dinámico (neutro-cercano, entendible en Colombia, México, LATAM y España sin localismos excesivamente cerrados salvo que el creador los pida).
- **Canal EN (`en_main`)**: inglés conversacional moderno de YouTube/Twitch (contracciones naturales: *don't*, *that's*, *gonna* cuando encaja con el tono del hablante).

## 4. Revisión cruzada multi-agente

Para clips prioritarios o con jerga compleja:
1. Genera el borrador de `translation-<lang>.json`.
2. Pide revisión a **Claude Code** o **Codex** con el rol `qa-reviewer` usando `.agents/skills/multi-agent-review/SKILL.md`.
3. Ejecuta `clipforge.py translate-apply` y corrige cualquier aviso de longitud o velocidad antes de renderizar.
