# Arquitectura Agéntica de ClipForge (`.agents/`)

Esta carpeta centraliza reglas, roles, skills, memoria y contratos de intercambio (**handoffs**) para que **Gemini Antigravity**, **Claude Code** y **OpenAI Codex** colaboren sobre el mismo repositorio sin duplicar instrucciones.

## Estructura

```text
.agents/
├── README.md                 # Este mapa
├── rules/                    # Reglas técnicas, editoriales y de seguridad
│   ├── architecture.md
│   ├── translation-style.md
│   └── safety-and-rights.md
├── roles/                    # Perfiles de especialista (reutilizables por cualquier agente)
│   ├── director.md
│   ├── clip-editor.md
│   ├── translator-es-en.md
│   ├── qa-reviewer.md
│   └── publisher.md
├── skills/                   # Procedimientos paso a paso (estándar SKILL.md)
│   ├── vod-to-clips/
│   ├── bilingual-subtitles/
│   ├── multi-agent-review/
│   └── publish-workflow/
├── workflows/                # Contratos entre etapas del pipeline
│   └── vod-pipeline.md
├── memory/                   # Conocimiento persistente de tus canales y glosario
│   ├── channel-profiles.json
│   └── glossary-gaming.md
└── handoffs/                 # Artefactos temporales de revisión multi-agente (en .gitignore)
```

## Cómo colaboran los 3 agentes en tu PC

| Agente instalado | Rol principal | Invocación local |
|---|---|---|
| **Gemini Antigravity** | Director / implementador principal y orquestador | Sesión interactiva en el IDE |
| **Claude Code (`claude`)** | Revisor de localización EN↔ES, naturalidad, hooks y estilo | `claude -p` vía `agent_review.py` |
| **OpenAI Codex (`codex`)** | Revisor técnico de código, edge cases, validación JSON/SRT y segunda opinión de traducción | `codex exec -` vía `agent_review.py` |

## Regla de oro del handoff

Nunca pases archivos binarios de video a un LLM de texto. Intercambia únicamente **JSON ligeros** (`translation-*.json`, `manifest.json`, diffs de código) en `.agents/handoffs/` y guarda cada veredicto con fecha y agente autor.
