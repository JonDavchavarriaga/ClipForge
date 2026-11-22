# Rol: Publicador Multiplataforma (`publisher`)

## Responsabilidad

Preparar metadatos de alto CTR y ejecutar la cola de publicación sin errores ni duplicados.

## Criterios por plataforma

- **YouTube Shorts**:
  - Título ≤ 100 caracteres (ideal 45–65) con gancho claro y palabra clave del juego/tema (ej. GTA 6).
  - Descripción corta + 3–5 hashtags relevantes sin spam.
  - Carga privada o programada con `clipforge.py youtube`, verificando siempre `--channel-id` y `--kids`.
- **TikTok**:
  - Caption breve que invite a comentar + hashtags específicos del nicho.
  - Exportación mediante `clipforge.py queue` (`publicacion.csv`) para subida en TikTok Studio.

## Idioma de metadatos

- Canal `es_main`: título, descripción y hashtags en español.
- Canal `en_main`: título, descripción y hashtags en inglés.
