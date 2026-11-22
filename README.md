# ClipForge

Proyecto local en Python para convertir una grabación larga en clips verticales, revisar los resultados y preparar su publicación en YouTube Shorts, TikTok, Instagram Reels y Facebook Reels. La edición se ejecuta en el equipo. La primera descarga del modelo de voz y las cargas en plataformas requieren internet.

**Punto de partida para Colombia.** Usa YouTube como ruta de monetización directa y TikTok, Instagram y Facebook para distribuir el mismo original limpio. En Facebook, activa la monetización si el Panel profesional te da acceso. Prueba segmentos propios antes de invertir en infraestructura o volumen de publicación.

## Estructura del proyecto y arquitectura agéntica

El repositorio está organizado en un paquete Python modular (`src/clipforge/`) junto con una arquitectura agéntica compartida (`.agents/`) compatible con **Gemini Antigravity**, **Claude Code** y **OpenAI Codex**:

- `src/clipforge/pipeline/`: detección de momentos (`highlights.py`), audio/video/Whisper/OpenCV (`media.py`) y flujo bilingüe EN↔ES (`translation.py`).
- `src/clipforge/publishing/`: aprobación SHA-256, exportación de cola CSV y carga idempotente a YouTube (`publication.py`).
- `src/clipforge/desktop/`: base del lanzador local para evolucionar a la app de escritorio con acceso directo e ícono (`assets/icons/clipforge.svg` y `scripts/create_desktop_shortcut.ps1`).
- `.agents/`: reglas, roles (`director`, `clip-editor`, `translator-es-en`, `qa-reviewer`, `publisher`), skills (`vod-to-clips`, `bilingual-subtitles`, `multi-agent-review`, `publish-workflow`), glosario gaming y perfiles de canal (`es_main` / `en_main`).
- `docs/ARCHITECTURE.md` y `docs/ROADMAP.md`: clasificación escalonada de las 22 herramientas open-source y plan por fases.

### App de escritorio local, logo en tu escritorio y Captura en Vivo (Twitch / Kick / YouTube)

```powershell
# Abrir ClipForge Studio en ventana de escritorio local
.\.venv\Scripts\python.exe clipforge.py app

# Crear el acceso directo con logo en tu escritorio de Windows
powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1

# También puedes usar el buffer en vivo desde consola (presiona ENTER para guardar los últimos 45s)
.\.venv\Scripts\python.exe clipforge.py live 'https://www.twitch.tv/tu_canal' --rights own --clip-seconds 45
```

### Flujo bilingüe EN ↔ ES, `split-cam` y subtítulos dinámicos `.ass`

```powershell
# 1. Procesar un stream de gameplay + webcam (ej. GTA 6) en formato split-screen con subtítulos dinámicos palabra a palabra
& $py clipforge.py process 'C:\Videos\stream_gta6.mp4' --rights own --layout split-cam --cam-region auto --caption-style highlight --language en --denoise

# 2. Exportar captions.srt a un paquete JSON con timestamps congelados
& $py clipforge.py translate-pack '.\salidas\lote-XXXXXXXX\clip-01\captions.srt' --source-lang en --target-lang es --channel es_main --context 'GTA 6 reacción'

# 3. Revisar traducción o código con Claude Code y/o Codex CLI (usa --dry-run para inspeccionar el prompt)
& $py .agents/skills/multi-agent-review/scripts/agent_review.py --agent claude --role qa-reviewer --input '.\salidas\lote-XXXXXXXX\clip-01\translation-es.json' --task 'Revisa naturalidad EN->ES y longitud de líneas'

# 4. Validar y aplicar traducciones preservando los timestamps exactos (+ generar .ass opcional)
& $py clipforge.py translate-apply '.\salidas\lote-XXXXXXXX\clip-01\captions.srt' '.\salidas\lote-XXXXXXXX\clip-01\translation-es.json' --ass-style highlight --layout split-cam

# 5. Re-renderizar el clip con los subtítulos traducidos y animados sin volver a transcribir el VOD
& $py clipforge.py rerender '.\salidas\lote-XXXXXXXX' clip-01 --srt '.\salidas\lote-XXXXXXXX\clip-01\captions.es.srt' --caption-style highlight
```

## Del video largo a la publicación

1. Graba material propio con OBS o importa un archivo que tengas autorización para recortar, distribuir y monetizar. Guarda los permisos del video y la música. `download` permite importar una URL autorizada con yt-dlp. Una exportación original es la vía más simple si una plataforma restringe la descarga.
2. `process` genera candidatos según densidad de voz, energía de audio y señales de apertura, como una pregunta. Intenta mantener el inicio y el final cerca de límites del diálogo. Puedes fijar momentos exactos con `--range`. Si el material carece de diálogo y energía útil, indica rangos manuales.
3. El encuadre `face` sigue la cara más cercana a la detección anterior, suaviza el movimiento y vuelve al centro cuando la pierde. `center` centra el corte y `fit` conserva el cuadro completo dentro del lienzo vertical. Para partidas donde importa el HUD, comienza con `fit`. El detector funciona mejor con una cara frontal clara y con una sola persona visible.
4. faster-whisper transcribe en CPU y agrupa palabras en bloques de lectura. Puedes sustituir esa etapa con `--srt`. FFmpeg incrusta esos subtítulos, exporta un SRT aparte y genera MP4 1080 × 1920 a 30 fps, con H.264 y audio AAC si la fuente tiene audio. Aplica filtro pasaaltos y normalización, con reducción de ruido opcional. Elige una mezcla de OBS o selecciona `--audio-track 1` para usar la segunda pista.
5. Abre el MP4 y revisa el gancho, el corte, los nombres propios, el encuadre y los derechos. `approve` guarda el título, texto, destinos, fecha opcional y la huella del archivo aprobado. Si cambias el MP4, debes aprobarlo de nuevo. `queue` genera `publicacion.csv` con una fila por clip y destino.
6. `youtube` carga el lote aprobado con OAuth. TikTok, Instagram y Facebook usan su programador oficial con los datos del CSV. La cola es un archivo de trabajo editable y las publicaciones de esos destinos se registran en sus respectivos paneles.

## Instalación en Windows

Extrae el ZIP, abre PowerShell en la carpeta `ClipForge` y ejecuta este bloque con Python 3.11 o 3.12 de 64 bits instalado.

```powershell
py -3.12 -m venv .venv
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
& $py clipforge.py --help
```

Puedes sustituir `py -3.12` por `python` si apunta a la versión indicada. El programa usa `FFMPEG_BINARY` o `ffmpeg` en el PATH si están configurados. También puede usar el ejecutable que trae `imageio-ffmpeg`. El binario necesita los filtros `subtitles`, `sendcmd` y `loudnorm`, y el codificador `libx264`. Si usas un FFmpeg propio y falla `subtitles`, usa una compilación que incluya libass. faster-whisper descarga el modelo `small` al primer uso. `--model base` reduce la carga en CPU o se puede usar una ruta local a un modelo compatible.

## Primera prueba con una grabación propia

```powershell
# Importa tu archivo largo desde OBS o tu editor
& $py clipforge.py process 'C:\Videos\grabacion.mp4' --rights own --count 3 --layout face --denoise

# También puedes fijar un corte. fit mantiene visible el cuadro original.
& $py clipforge.py process 'C:\Videos\grabacion.mp4' --rights own --layout fit --range '00:02:10,00:02:45'

# Sustituye este nombre por la carpeta de lote que imprime process
$lote = '.\salidas\lote-XXXXXXXX'
& $py clipforge.py review $lote
& $py clipforge.py approve $lote clip-01 --title 'La maniobra que cambió la partida' --caption 'Te cuento qué pasó antes del giro.' --platform youtube --platform tiktok --platform instagram --platform facebook
& $py clipforge.py queue $lote
```

El perfil principal propone clips de 25 a 55 segundos. Prueba `--min-seconds 61 --max-seconds 90` con fuentes suficientemente largas si el tema necesita más contexto o la cuenta entra en un programa con duración mínima. El proceso puede devolver menos candidatos si los cortes se solapan o la fuente es corta. Usa `--skip-asr` para buscar picos de audio sin descargar un modelo, o `--srt C:\Videos\subtitulos.srt` para reutilizar subtítulos sincronizados con la fuente.

```powershell
# Ejemplo con una descarga autorizada. Sustituye la URL por la fuente real.
& $py clipforge.py download 'https://example.org/video' --output '.\fuentes\episodio01' --rights licensed --rights-note 'Titular y referencia de la licencia'
```

`download` conserva un archivo con URL y procedencia. El subcomando `process` pide que confirmes los derechos de la fuente utilizada. Las plataformas pueden separar video y audio en formatos de mayor resolución. La descarga prioriza formatos combinados y, para YouTube, la disponibilidad puede depender de los requisitos de JavaScript de [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Publicación y monetización

| Destino | Flujo del prototipo | Opción de ingresos |
|---|---|---|
| YouTube Shorts | Carga privada por API. Puedes solicitar programación con fecha aprobada o programar en Studio. | Colombia está en el YPP ampliado. Por la vía Shorts, 500 suscriptores, tres publicaciones públicas válidas y 3 millones de vistas públicas calificadas de Shorts en 90 días permiten postularse al nivel de financiación de fans y Shopping. Para reparto publicitario por esta vía, 1.000 suscriptores y 10 millones de vistas públicas calificadas de Shorts en 90 días. Aplican la revisión del canal y la aceptación del módulo de Shorts. [Programa ampliado](https://support.google.com/youtube/answer/13429240?hl=es-419) · [Monetización Shorts](https://support.google.com/youtube/answer/12504220) |
| TikTok | Exportación y programación en [TikTok Studio](https://www.tiktok.com/tiktokstudio/upload), según la disponibilidad de la cuenta. TikTok rechaza Direct Post para utilidades que publican únicamente en cuentas propias o del equipo. [Guías de Direct Post](https://developers.tiktok.com/docs/en/content-sharing-guidelines?enter_method=left_navigation) · [Programador](https://ads.tiktok.com/business/es-LA/blog/introducing-video-scheduler-now-you-can-plan-tiktoks-in-advance) | No se confirmó el acceso a Creator Rewards para cuentas de Colombia en las fuentes oficiales consultadas. TikTok confirmó México y Brasil en su anuncio regional. El programa exige región elegible y videos originales de más de un minuto. Usa TikTok como canal de distribución y reserva pagos por vistas para cuentas con acceso confirmado. [Anuncio regional](https://newsroom.tiktok.com/programa-recompensas-creadores-tiktok-mexico?lang=es-419) · [Requisitos del formato](https://support.tiktok.com/en/business-and-creator/creator-rewards-program/how-is-the-creator-rewards-program-different-from-the-tiktok-creator-fund) |
| Facebook Reels | Exportación a [Meta Business Suite](https://business.facebook.com/). | Facebook Content Monetization remunera formatos elegibles, incluidos Reels, y funciona por invitación. Desde el Panel profesional puedes expresar interés. [Meta](https://about.fb.com/news/2026/03/creator-fast-track-grow-your-audience-earn-money-on-facebook/) |
| Instagram Reels | Exportación a Business Suite con una cuenta profesional compatible. Para una ampliación, Meta documenta publicación de Reels por API para cuentas profesionales. [API de Instagram](https://developers.facebook.com/documentation/instagram-platform/content-publishing) | Colaboraciones, afiliación y las funciones que aparezcan en la sección de monetización de la cuenta. |

Los derechos de uso y la elegibilidad de monetización se revisan por separado. YouTube aplica su revisión de contenido reutilizado al canal completo, incluso si el titular dio permiso. Facebook también limita el contenido con cambios menores. La edición automática ayuda a aprovechar material propio de OBS, entrevistas y tutoriales. Para formatos usando material de terceros con licencia, aporta análisis, contexto o producción editorial sustancial. [Políticas de YouTube](https://support.google.com/youtube/answer/1311392) · [Originalidad en Facebook](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/)

### Activar la carga de YouTube

1. Habilita YouTube Data API v3 en Google Cloud, configura la pantalla de consentimiento y agrega tu usuario de prueba si corresponde. Crea un cliente OAuth de tipo **Aplicación de escritorio** y coloca el JSON en un lugar privado. El inicio de sesión usa una redirección a localhost y los tokens quedan en memoria durante el comando.
2. Obtén el ID de tu canal, que empieza por `UC`. El comando verifica ese ID contra el canal autorizado. `--kids` registra si el video está dirigido a niños. Usa `--clip` para un único video o ejecútalo sin ese argumento para el lote aprobado de YouTube.

```powershell
$canal = 'UC_REEMPLAZA_CON_EL_ID'
& $py clipforge.py youtube $lote --client 'client_secret.json' --channel-id $canal --clip clip-01 --kids no
```

3. Para programar, aprueba el clip con `--publish-at '2027-01-15T18:00:00-05:00'` y agrega `--use-schedule` al comando `youtube`. Usa una fecha futura con el horario local de Colombia, y verifica el resultado de la primera carga en Studio. El cronograma del CSV se exporta en UTC. Si necesitas programar un video que ya se cargó en privado, usa YouTube Studio. [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert) · [publishAt](https://developers.google.com/youtube/data/reference/rest/v3/videos#status.publishAt)
4. Conserva `salidas/youtube.sqlite3` al mover el proyecto. El registro evita reenviar el mismo archivo al mismo canal, incluso si reemplazas la fecha o el título. Ante una carga incierta, revisa Studio cuando haya terminado el proceso local. Con `youtube-resolve $lote clip-01 --channel-id $canal --video-id ID_DEL_VIDEO` registras una carga existente. Si verificaste que Studio no recibió el video, usa `--confirm-absent` y después repite la carga. Ambos comandos aceptan `--ledger` si elegiste guardar el registro en otra ruta.

## Decisiones del proyecto

| Componente | Herramientas y decisión |
|---|---|
| Captura e ingesta | OBS para gameplay y reacciones. yt-dlp para fuentes autorizadas. |
| Selección y transcripción | Reglas locales para proponer cortes y faster-whisper para voz, usando el modelo Whisper. `whisper.cpp` es otra opción para una estación sin dependencias de Python complejas. AI Shorts Generator sirve como referencia de selección de momentos y stable-ts puede ayudar si necesitas sincronía más fina. |
| Encuadre | OpenCV para detección y continuidad espacial. Norfair o Supervision pueden aportar seguimiento más sólido cuando varias personas se cruzan. pyannote.audio identifica turnos de voz y puede complementar una futura selección de cámara. |
| Audio y subtítulos | FFmpeg para audio y libass para incrustar los SRT. DeepFilterNet aporta reducción de ruido especializada. Demucs puede ayudar con fuentes de audio mezcladas. pysubs2 tiene sentido si incorporas plantillas ASS editables. |
| Edición y exportación | FFmpeg renderiza el formato final. Kdenlive, Shotcut y OpenCut quedan disponibles para ajustes manuales. PyAV y Editly serían candidatos para composiciones o procesamiento más fino. Mediabunny tiene sentido si más adelante necesitas exportación desde una interfaz web. |
| Mejora de imagen | Real-ESRGAN y RIFE son etapas opcionales para material con problemas de resolución o fluidez. Evalúalas con casos reales antes de añadir su costo de cómputo. |

## Piloto y ampliación

- Produce un primer lote de entre tres y diez cortes de un video propio. Registra cuántos se aprueban, cuánto tarda el procesamiento, qué correcciones se repiten y qué cambia al alternar entre 25–55 y 61–90 segundos.
- Publica primero donde ya tienes acceso. Conserva por clip su plataforma, enlace, fecha, retención, tiempo medio, alcance y conversiones. Compara cortes del mismo tipo y separa cifras de alcance de vistas calificadas para ingresos.
- Si el volumen justifica alojar el flujo, usa FastAPI para cargar fuentes y aprobar clips, un worker de FFmpeg/Whisper para el trabajo pesado y almacenamiento de objetos. Pasar de CLI a panel también permite programar Meta con un adaptador oficial y mantener en un solo lugar los estados de las publicaciones. Prueba permisos de cada cuenta antes de poner a correr el adaptador en producción.

## Límites de esta versión

El puntaje sugiere momentos para revisión. El gameplay con sucesos visuales silenciosos se beneficia de `--range`. En conversaciones con varias caras, el encuadre sigue continuidad espacial y puede necesitar corrección manual. Hay tres diseños básicos, sin composición independiente de webcam sobre gameplay. El MP4 y el SRT quedan juntos en cada subcarpeta. Si modificas subtítulos después de renderizar, vuelve a procesar con el SRT corregido para actualizar los subtítulos incrustados.

La cola de publicación requiere tu aprobación editorial. La carga de YouTube necesita credenciales y un canal real. TikTok y Meta se programan desde sus paneles en esta versión. Las referencias de plataformas se revisaron el 21 de noviembre de 2026.

## Verificación local

```powershell
& $py -m unittest discover -v
```

Las pruebas cubren la selección de cortes, la sincronización de SRT, rangos fuera de la fuente, aprobación, exportación de cola y cargas repetidas con un cliente simulado. La prueba de render usa un video sintético si FFmpeg está disponible.
