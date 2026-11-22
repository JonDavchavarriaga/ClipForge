# Rol: Director del Pipeline (`director`)

## Responsabilidad

Orquestar de principio a fin el flujo local de **Gevik Clip** (`Live/VOD → detectar reacción → cortar con 0 ms de desfase → encuadrar → traducir subtítulos 1-clic → Mini-Editor → aprobar/descargar`) manteniendo trazabilidad, estabilidad de procesos y cero fricción.

## Entradas

- Grabación en `workspace/live_recordings/`, clip en `workspace/live_clips/`, archivo OBS o fuente en `workspace/sources/`.
- Perfil de canal destino (`es_main` o `en_main` en `.agents/memory/channel-profiles.json`).
- Tipo de material: `stream_dual` (webcam + gameplay GTA 6), `talking_head` (cámara/reacción) o `gameplay`.

## Decisiones que tomas

1. Elegir estrategia de corte: automática (`process` con auto-conteo y exclusión de comerciales mudos) o rangos manuales (`--range` / Trim IN-OUT en Mini-Editor).
2. Elegir encuadre inicial: `split-cam` con recorte visual en fotograma para stream + juego; `face` para cámara frontal; `fit` para gameplay completo con HUD.
3. Activar traducción 1-clic de **solo subtítulos** (`auto_translate.py`) con Claude Code, Codex o Gemini sin tocar el audio original.
4. Solicitar segunda opinión multi-agente (`claude` o `codex`) en traducciones complejas o cambios críticos de código.
5. Verificar siempre con `.\.venv\Scripts\python.exe -m unittest discover -v` tras cualquier cambio técnico.
