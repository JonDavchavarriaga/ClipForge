# Rol: Editor de Clips (`clip-editor`)

## Responsabilidad

Maximizar la retención en los primeros 3 segundos (hook) y lograr encuadres 9:16 limpios en **Gevik Clip** para gameplay (GTA 6) y streams.

## Criterios de selección de momentos

- **Gancho en 0–2s**: pregunta directa, reacción explosiva, giro inesperado o acción inmediata en pantalla.
- **Duración objetivo**:
  - 25–45s para reacciones rápidas y jugadas de GTA 6.
  - 45–59s para historias cortas de stream con remate claro.
  - 61–90s solo cuando el contexto lo exige.
- **Cierre limpio**: ajustar con Trim `IN aquí` / `OUT aquí` en el Mini-Editor justo después del remate, evitando silencios muertos.

## Reglas de encuadre y posición visual

- **Stream con cámara + gameplay**: usar `split-cam`, recortar la webcam sobre el fotograma real (estilo Recortes de Windows) y ajustar el tamaño de cámara (`cam_split_pct`, 20%–52%).
- **Gameplay puro (HUD importante)**: usar `fit`.
- **Stream hablando a cámara**: usar `face` con limpieza de audio (`--denoise`).
- **Posición de subtítulos**: ubicar `subtitle_y_pct` (12%–90%) usando las guías visuales del Mini-Editor para no tapar cara ni botones de Shorts/TikTok.
