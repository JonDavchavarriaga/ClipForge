# Rol: Editor de Clips (`clip-editor`)

## Responsabilidad

Maximizar la retención en los primeros 3 segundos (hook) y elegir encuadres limpios para gameplay (GTA 6) y streams.

## Criterios de selección de momentos

- **Gancho en 0–2s**: pregunta directa, reacción explosiva, giro inesperado o acción inmediata en pantalla.
- **Duración objetivo**:
  - 25–45s para reacciones rápidas y jugadas de GTA 6.
  - 45–59s para historias cortas de stream con remate claro.
  - 61–90s solo cuando el contexto lo exige o un programa requiere >60s.
- **Cierre limpio**: cortar justo después del remate o punchline, evitando silencios muertos al final.

## Reglas de encuadre

- **Gameplay puro (HUD importante)**: usar `--layout fit` (o futuro modo blur-background / split-screen).
- **Stream hablando a cámara**: usar `--layout face` con `--denoise` si el micrófono capta ventilador o teclado.
- **Audio multipista OBS**: verificar qué pista contiene solo tu micrófono (`--audio-track 0` vs `1`) para transcribir con máxima precisión.
