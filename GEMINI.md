# ClipForge — Gemini Antigravity Entrypoint

Lee y sigue [AGENTS.md](./AGENTS.md) como fuente única de verdad del proyecto.

## Rol recomendado de Gemini Antigravity en este workspace

- Orquestación principal del desarrollo, arquitectura y pipeline local (`.agents/roles/director.md`).
- Uso progresivo de las skills en `.agents/skills/` (`vod-to-clips`, `bilingual-subtitles`, `multi-agent-review`, `publish-workflow`).
- Coordinación con **Claude Code** (`claude -p`) y **Codex CLI** (`codex exec -`) mediante `.agents/skills/multi-agent-review/scripts/agent_review.py` para doble revisión de traducciones y código.
