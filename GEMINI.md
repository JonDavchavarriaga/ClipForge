# Gevik Clip — Gemini Antigravity Entrypoint

Lee y sigue [AGENTS.md](./AGENTS.md) y los ADRs en [docs/decisions/](./docs/decisions/) como fuente única de verdad del proyecto.

## Rol recomendado de Gemini Antigravity en este workspace

- Orquestación principal del desarrollo, arquitectura local-first, app de escritorio **Gevik Clip** y pruebas (`.agents/roles/director.md`).
- Uso progresivo de las skills en `.agents/skills/` (`vod-to-clips`, `bilingual-subtitles`, `multi-agent-review`, `publish-workflow`).
- Coordinación con **Claude Code** (`claude.exe -p --output-format text`) y **Codex CLI** (`codex.cmd exec --skip-git-repo-check --ephemeral -s read-only -`) mediante `auto_translate.py` y `.agents/skills/multi-agent-review/scripts/agent_review.py`.
