import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from clipforge.pipeline.highlights import Candidate, clip_cues, make_srt, read_srt, seconds, suggest
from clipforge.pipeline.live import LiveRecorder
from clipforge.pipeline.media import (
    CamRegion,
    audio_preview,
    detect_cam_region,
    face_commands,
    parse_cam_region,
    probe,
    render,
    speech,
)
from clipforge.pipeline.subtitles import ASS_PRESETS, CAPTION_STYLES, write_ass
from clipforge.pipeline.translation import (
    apply_translated_bundle,
    build_translation_bundle,
    validate_language,
    write_cues_srt,
)
from clipforge.publishing.publication import (
    PLATFORMS,
    approved_clip,
    export_queue,
    reconcile_youtube,
    resolve_clip,
    save_json,
    upload_youtube,
    utc_date,
    verify_channel_id,
    youtube_connect,
)

LAYOUTS = ('face', 'center', 'fit', 'split-cam')


def provenance(args):
    kind = getattr(args, 'rights', None) or 'local'
    note = (getattr(args, 'rights_note', None) or '').strip()
    if kind == 'licensed' and not note:
        raise ValueError('Indica el titular y la referencia de la licencia con --rights-note.')
    return {'kind': kind, 'note': note}


def download(args):
    rights = provenance(args)
    if not args.url.startswith(('https://', 'http://')):
        raise ValueError('Indica un enlace HTTP o HTTPS de la fuente autorizada.')
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ingesta-', dir=args.output) as temporary:
        directory = Path(temporary).resolve()
        result = subprocess.run(
            [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '-f', 'b[ext=mp4]/b',
             '-o', str(directory / 'fuente.%(ext)s'), '--', args.url], check=False,
        )
        if result.returncode:
            raise ValueError('La descarga falló. Puedes usar el archivo original exportado por tu plataforma u OBS.')
        files = list(directory.glob('fuente.*'))
        if len(files) != 1:
            raise ValueError('yt-dlp no devolvió un único archivo de video utilizable.')
        source = files[0]
        destination = args.output / source.name
        if destination.exists():
            raise ValueError('La carpeta ya tiene una fuente con ese nombre. Elige otra con --output.')
        source.rename(destination)
        save_json(args.output / 'origen.json', {'url': args.url, 'rights': rights, 'archivo': destination.name})
    print(destination.resolve())


def manual_ranges(values, duration):
    selected = []
    for value in values:
        if value.count(',') != 1:
            raise ValueError('Usa --range INICIO,FIN. Para decimales, usa punto.')
        start, end = map(seconds, value.split(','))
        if not 0 <= start < end <= duration + 0.01 or not 1 <= end - start <= 180:
            raise ValueError('Cada rango debe incluir de 1 a 180 segundos dentro del video.')
        selected.append(Candidate(start, min(end, duration), None, 'Rango manual'))
    return selected


def _serialize_cam(region: CamRegion | None) -> dict | None:
    if region is None:
        return None
    return {'x': region.x, 'y': region.y, 'w': region.w, 'h': region.h}


def process(args):
    rights = provenance(args)
    source = args.source.resolve(strict=True)
    info = probe(source)
    if args.audio_track is not None and not 0 <= args.audio_track < info.audio_tracks:
        raise ValueError(f'La fuente tiene {info.audio_tracks} pistas de audio. La numeración empieza en 0.')
    track = args.audio_track if args.audio_track is not None else (0 if info.audio_tracks else None)
    manual = manual_ranges(args.range, info.duration)
    if not 1 <= args.count <= 50 or not 1 <= args.min_seconds <= args.max_seconds <= 180:
        raise ValueError('Usa 1 a 50 clips y un intervalo de duración entre 1 y 180 segundos.')
    requested_cam = parse_cam_region(args.cam_region) if args.layout == 'split-cam' else None
    cues = read_srt(args.srt) if args.srt else []
    args.output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='lote-', dir=args.output)).resolve()
    clips = []
    try:
        with tempfile.TemporaryDirectory(prefix='clipforge-audio-') as directory:
            energy = []
            needs_asr = not args.srt and not args.skip_asr
            if track is not None and (not manual or needs_asr):
                wav = Path(directory) / 'voz.wav'
                print('Analizando audio…', flush=True)
                energy = audio_preview(source, wav, track)
                if needs_asr:
                    print(f'Transcribiendo con {args.model}…', flush=True)
                    cues = speech(wav, args.model, args.language)
            candidates = manual or suggest(cues, energy, info.duration, args.count, args.min_seconds, args.max_seconds)
            for index, candidate in enumerate(candidates, 1):
                clip_id = f'clip-{index:02}'
                clip_dir = folder / clip_id
                clip_dir.mkdir()
                layout = args.layout
                cam_region = None
                if layout == 'face':
                    found = face_commands(source, candidate.start, candidate.end - candidate.start, clip_dir / 'follow.txt')
                    if not found:
                        layout = 'center'
                elif layout == 'split-cam':
                    cam_region = requested_cam or detect_cam_region(source, candidate.start, candidate.end - candidate.start)

                rebased = clip_cues(cues, candidate.start, candidate.end)
                captions_srt = make_srt(cues, candidate.start, candidate.end)
                captions_file = None
                captions_ass_rel = None
                if captions_srt:
                    srt_target = clip_dir / 'captions.srt'
                    srt_target.write_text(captions_srt, encoding='utf-8')
                    captions_file = srt_target
                    if args.caption_style != 'srt':
                        ass_target = clip_dir / 'captions.ass'
                        write_ass(rebased, ass_target, args.caption_style, layout=layout)
                        captions_file = ass_target
                        captions_ass_rel = f'{clip_id}/captions.ass'

                target = clip_dir / 'video.mp4'
                print(f'{clip_id} · {candidate.start:.1f}s–{candidate.end:.1f}s · {layout} · {args.caption_style}', flush=True)
                try:
                    render(
                        source,
                        target,
                        candidate.start,
                        candidate.end,
                        track,
                        layout,
                        bool(captions_srt),
                        args.denoise,
                        captions_file=captions_file,
                        cam_region=cam_region,
                    )
                finally:
                    (clip_dir / 'follow.txt').unlink(missing_ok=True)
                clips.append({
                    'id': clip_id,
                    'start': round(candidate.start, 3),
                    'end': round(candidate.end, 3),
                    'score': round(candidate.score, 3) if candidate.score is not None else None,
                    'reason': candidate.reason,
                    'layout': layout,
                    'cam_region': _serialize_cam(cam_region),
                    'caption_style': args.caption_style,
                    'video': f'{clip_id}/video.mp4',
                    'captions': f'{clip_id}/captions.srt' if captions_srt else None,
                    'captions_ass': captions_ass_rel,
                })
        save_json(folder / 'manifest.json', {'source': str(source), 'rights': rights, 'audio_track': track, 'clips': clips})
    except Exception:
        if not clips:
            shutil.rmtree(folder, ignore_errors=True)
        else:
            print(f'El lote quedó incompleto en {folder}. Los lotes incompletos no se pueden aprobar.', file=sys.stderr)
        raise
    print(folder)


def read_run(path):
    folder = path.resolve(strict=True)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    clips = manifest['clips']
    for clip in clips:
        approval_file = folder / clip['id'] / 'approval.json'
        if approval_file.is_file():
            clip['approval'] = json.loads(approval_file.read_text(encoding='utf-8'))
    return folder, clips


def rerender(args):
    folder = args.run.resolve(strict=True)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    clip = next((item for item in manifest['clips'] if item['id'] == args.clip), None)
    if clip is None:
        raise ValueError('El identificador del clip no aparece en el lote.')
    source = Path(manifest['source']).resolve(strict=True)
    track = manifest.get('audio_track')
    clip_dir = folder / clip['id']
    layout = args.layout or clip.get('layout', 'face')
    style = args.caption_style or clip.get('caption_style', 'highlight')

    cam_region = None
    if layout == 'split-cam':
        if args.cam_region:
            cam_region = parse_cam_region(args.cam_region) or detect_cam_region(
                source, clip['start'], clip['end'] - clip['start']
            )
        elif clip.get('cam_region'):
            raw = clip['cam_region']
            cam_region = CamRegion(raw['x'], raw['y'], raw['w'], raw['h'])
        else:
            cam_region = detect_cam_region(source, clip['start'], clip['end'] - clip['start'])
    elif layout == 'face':
        found = face_commands(source, clip['start'], clip['end'] - clip['start'], clip_dir / 'follow.txt')
        if not found:
            layout = 'center'

    captions_file = None
    srt_source = args.srt.resolve(strict=True) if args.srt else (
        (folder / clip['captions']).resolve() if clip.get('captions') else None
    )
    if srt_source and srt_source.is_file():
        if style != 'srt':
            cues = read_srt(srt_source)
            ass_name = 'captions.rendered.ass' if args.srt else 'captions.ass'
            captions_file = write_ass(cues, clip_dir / ass_name, style, layout=layout)
            clip['captions_ass'] = f'{clip["id"]}/{ass_name}'
        else:
            target_srt = clip_dir / 'captions.rendered.srt'
            if srt_source != target_srt:
                shutil.copyfile(srt_source, target_srt)
            captions_file = target_srt

    target = clip_dir / 'video.mp4'
    try:
        render(
            source,
            target,
            clip['start'],
            clip['end'],
            track,
            layout,
            captions_file is not None,
            args.denoise,
            captions_file=captions_file,
            cam_region=cam_region,
        )
    finally:
        (clip_dir / 'follow.txt').unlink(missing_ok=True)

    clip['layout'] = layout
    clip['cam_region'] = _serialize_cam(cam_region)
    clip['caption_style'] = style
    save_json(manifest_path, manifest)
    print(f'{clip["id"]} re-renderizado ({layout} · {style}) · {target}')
    if (clip_dir / 'approval.json').is_file():
        print('Aviso: el video cambió; vuelve a ejecutar `approve` antes de publicar.')


def review(args):
    folder, clips = read_run(args.run)
    for clip in clips:
        mark = 'aprobado' if clip.get('approval') else 'pendiente'
        print(f'{clip["id"]} | {clip["start"]:.2f}–{clip["end"]:.2f}s | {mark} | {clip["reason"]}')
        print(folder / clip['video'])


def approve(args):
    folder, clips = read_run(args.run)
    clip = next((item for item in clips if item['id'] == args.clip), None)
    if clip is None:
        raise ValueError('El identificador del clip no aparece en el lote.')
    title, caption = args.title.strip(), args.caption.strip()
    if not 1 <= len(title) <= 100 or len(caption.encode('utf-8')) > 5000 or any(ch in title + caption for ch in '<>'):
        raise ValueError('Usa un título de hasta 100 caracteres, texto de hasta 5000 bytes y evita < y >.')
    _, digest = resolve_clip(folder, clip)
    approval = {'sha256': digest, 'title': title, 'caption': caption,
                'platforms': list(dict.fromkeys(args.platform)), 'publish_at': utc_date(args.publish_at, future=True),
                'approved_at': datetime.now(timezone.utc).isoformat()}
    save_json(folder / clip['id'] / 'approval.json', approval)
    print(f'{clip["id"]} aprobado para {", ".join(approval["platforms"])}.')


def queue(args):
    folder, clips = read_run(args.run)
    path, count = export_queue(folder, [clip for clip in clips if clip.get('approval')])
    print(f'{count} entregas · {path}')


def translate_pack(args):
    srt_path = args.srt.resolve(strict=True)
    cues = read_srt(srt_path)
    bundle = build_translation_bundle(
        cues,
        args.source_lang,
        args.target_lang,
        channel=args.channel or '',
        context=args.context or '',
    )
    target = args.output.resolve() if args.output else srt_path.with_name(f'translation-{args.target_lang}.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    save_json(target, bundle)
    print(f'{len(cues)} bloques exportados · {target}')


def translate_apply(args):
    srt_path = args.srt.resolve(strict=True)
    bundle_path = args.bundle.resolve(strict=True)
    original_cues = read_srt(srt_path)
    bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
    target_lang = validate_language(bundle.get('target_lang', 'es'))
    translated_cues, warnings = apply_translated_bundle(original_cues, bundle)
    destination = args.output.resolve() if args.output else srt_path.with_name(f'captions.{target_lang}.srt')
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_cues_srt(translated_cues, destination)
    print(f'{len(translated_cues)} bloques escritos · {destination}')
    if args.ass_style:
        ass_path = destination.with_suffix('.ass')
        write_ass(translated_cues, ass_path, args.ass_style, layout=args.layout)
        print(f'ASS ({args.ass_style}) escrito · {ass_path}')
    for warning in warnings:
        print(f'Aviso: {warning}')


def youtube(args):
    folder, clips = read_run(args.run)
    channel = verify_channel_id(args.channel_id)
    selected = [clip for clip in clips if clip.get('approval') and 'youtube' in clip['approval']['platforms']
                and (not args.clip or clip['id'] in args.clip)]
    if not selected or set(args.clip) - {clip['id'] for clip in selected}:
        raise ValueError('Selecciona clips aprobados para YouTube.')
    batch = []
    for clip in selected:
        path, digest = approved_clip(folder, clip)
        date = clip['approval']['publish_at'] if args.use_schedule else None
        if args.use_schedule and not date:
            raise ValueError(f'{clip["id"]} necesita una fecha de publicación aprobada.')
        batch.append((clip, path, digest, utc_date(date, future=True)))
    ledger = args.ledger.resolve()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    api = youtube_connect(args.client.resolve(strict=True), channel)
    try:
        for clip, path, digest, date in batch:
            video_id, created = upload_youtube(api, ledger, channel, path, digest, clip['approval'], date, args.kids == 'yes')
            status = 'cargado; verifica su programación en Studio' if date else 'cargado como privado'
            print(f'{clip["id"]} · {status if created else "carga registrada"} · https://youtu.be/{video_id}')
    finally:
        api.close()


def resolve_upload(args):
    folder, clips = read_run(args.run)
    clip = next((item for item in clips if item['id'] == args.clip), None)
    if clip is None:
        raise ValueError('El identificador del clip no aparece en el lote.')
    _, digest = resolve_clip(folder, clip)
    channel = verify_channel_id(args.channel_id)
    if args.video_id and not re.fullmatch(r'[A-Za-z0-9_-]{11}', args.video_id):
        raise ValueError('Indica solo el ID del video de 11 caracteres.')
    reconcile_youtube(args.ledger.resolve(strict=True), channel, digest, args.video_id)
    print('Carga existente registrada.' if args.video_id else 'Intento liberado para volver a cargar.')


def live_capture(args):
    rights = provenance(args)
    recorder = LiveRecorder(
        args.output,
        segment_seconds=args.segment_seconds,
        buffer_minutes=args.buffer_minutes,
    )
    status = recorder.start(args.url, rights['kind'], rights['note'], quality=args.quality)
    print(f'Buffer en vivo iniciado ({status["platform"]}) · {status["session_dir"]}')
    print('Comandos en consola: presiona ENTER para guardar los últimos '
          f'{args.clip_seconds}s, escribe un número de segundos (ej. 60), o "q" para salir.')
    try:
        while True:
            raw = input('[live] > ').strip().lower()
            if raw in {'q', 'quit', 'exit', 'stop'}:
                break
            seconds_window = args.clip_seconds if not raw else int(raw)
            try:
                clip = recorder.save_clip(seconds_window)
                print(f'Clip guardado ({clip.window_seconds}s) · {clip.path}')
            except ValueError as err:
                print(f'Aviso: {err}')
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        recorder.stop()
        print('Captura en vivo detenida.')


def launch_app(args):
    from clipforge.desktop.launcher import run_desktop_app

    run_desktop_app(port=args.port, open_window=not args.no_browser)


def add_rights(parser):
    parser.add_argument(
        '--rights',
        choices=['local', 'own', 'licensed'],
        default='local',
        help='Etiqueta opcional de procedencia (local por defecto).',
    )
    parser.add_argument('--rights-note', help='Nota opcional de licencia cuando corresponda.')


def main():
    parser = argparse.ArgumentParser(description='Convierte videos largos en clips verticales y prepara su publicación.')
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('download', help='Descarga una fuente autorizada con yt-dlp.')
    fetch.add_argument('url')
    fetch.add_argument('--output', type=Path, required=True)
    add_rights(fetch)
    fetch.set_defaults(handler=download)
    edit = commands.add_parser('process', help='Detecta, recorta y exporta un lote de clips.')
    edit.add_argument('source', type=Path)
    edit.add_argument('--output', type=Path, default=Path('salidas'))
    edit.add_argument('--count', type=int, default=3)
    edit.add_argument('--min-seconds', type=int, default=25)
    edit.add_argument('--max-seconds', type=int, default=55)
    edit.add_argument('--range', action='append', default=[], help='Rango INICIO,FIN. Se puede repetir.')
    edit.add_argument('--layout', choices=LAYOUTS, default='face',
                      help='face, center, fit o split-cam (webcam arriba + gameplay abajo).')
    edit.add_argument('--cam-region', default='auto',
                      help='Para split-cam: auto, top-left, top-right, bottom-left, bottom-right o x,y,w,h.')
    edit.add_argument('--caption-style', choices=CAPTION_STYLES, default='highlight',
                      help='Estilo de subtítulos: highlight (ASS palabra a palabra), neon, bold o srt clásico.')
    edit.add_argument('--srt', type=Path, help='Subtítulos existentes con los tiempos del video largo.')
    edit.add_argument('--skip-asr', action='store_true', help='Omite transcripción y usa SRT o energía de audio.')
    edit.add_argument('--model', default='small', help='Modelo faster-whisper, por ejemplo small, base o una ruta local.')
    edit.add_argument('--language', default='es', help='Idioma Whisper, por ejemplo es, en o auto.')
    edit.add_argument('--denoise', action='store_true', help='Reducción de ruido adicional con FFmpeg.')
    edit.add_argument('--audio-track', type=int, help='Índice de pista de audio de OBS u otra fuente, desde 0.')
    add_rights(edit)
    edit.set_defaults(handler=process)
    re_edit = commands.add_parser('rerender', help='Re-renderiza un clip con nuevo SRT traducido, estilo ASS o layout.')
    re_edit.add_argument('run', type=Path)
    re_edit.add_argument('clip')
    re_edit.add_argument('--srt', type=Path, help='SRT del clip (por ejemplo captions.es.srt generado por translate-apply).')
    re_edit.add_argument('--caption-style', choices=CAPTION_STYLES, help='Estilo de subtítulos a aplicar.')
    re_edit.add_argument('--layout', choices=LAYOUTS, help='Sobrescribe el layout del clip.')
    re_edit.add_argument('--cam-region', help='Región de webcam para split-cam.')
    re_edit.add_argument('--denoise', action='store_true', help='Reducción de ruido adicional con FFmpeg.')
    re_edit.set_defaults(handler=rerender)
    inspection = commands.add_parser('review', help='Lista clips y su estado de aprobación.')
    inspection.add_argument('run', type=Path)
    inspection.set_defaults(handler=review)
    approval = commands.add_parser('approve', help='Aprueba un clip, textos y destinos.')
    approval.add_argument('run', type=Path)
    approval.add_argument('clip')
    approval.add_argument('--title', required=True)
    approval.add_argument('--caption', default='')
    approval.add_argument('--platform', action='append', choices=PLATFORMS, required=True)
    approval.add_argument('--publish-at', help='Fecha opcional en ISO 8601 con zona horaria.')
    approval.set_defaults(handler=approve)
    listing = commands.add_parser('queue', help='Exporta los clips aprobados y sus destinos como CSV.')
    listing.add_argument('run', type=Path)
    listing.set_defaults(handler=queue)
    t_pack = commands.add_parser('translate-pack', help='Exporta un SRT a JSON estructurado para traducción EN↔ES.')
    t_pack.add_argument('srt', type=Path)
    t_pack.add_argument('--source-lang', default='en')
    t_pack.add_argument('--target-lang', required=True)
    t_pack.add_argument('--channel', default='', help='Perfil de canal en .agents/memory/channel-profiles.json.')
    t_pack.add_argument('--context', default='', help='Contexto breve del juego o stream.')
    t_pack.add_argument('--output', type=Path)
    t_pack.set_defaults(handler=translate_pack)
    t_apply = commands.add_parser('translate-apply', help='Valida y aplica un JSON traducido manteniendo timestamps exactos.')
    t_apply.add_argument('srt', type=Path)
    t_apply.add_argument('bundle', type=Path)
    t_apply.add_argument('--output', type=Path)
    t_apply.add_argument('--ass-style', choices=sorted(ASS_PRESETS), help='Opcional: genera también un .ass animado.')
    t_apply.add_argument('--layout', choices=LAYOUTS, default='face', help='Layout para ajustar margen del .ass.')
    t_apply.set_defaults(handler=translate_apply)
    posting = commands.add_parser('youtube', help='Carga clips aprobados a un canal autorizado de YouTube.')
    posting.add_argument('run', type=Path)
    posting.add_argument('--client', type=Path, required=True, help='JSON OAuth de escritorio de Google Cloud.')
    posting.add_argument('--channel-id', required=True)
    posting.add_argument('--clip', action='append', default=[])
    posting.add_argument('--kids', choices=['yes', 'no'], required=True, help='Si el contenido va dirigido a niños.')
    posting.add_argument('--ledger', type=Path, default=Path('salidas') / 'youtube.sqlite3')
    posting.add_argument('--use-schedule', action='store_true', help='Solicita a YouTube las fechas aprobadas.')
    posting.set_defaults(handler=youtube)
    recovery = commands.add_parser('youtube-resolve', help='Reconcilia un intento tras verificar el resultado en Studio.')
    recovery.add_argument('run', type=Path)
    recovery.add_argument('clip')
    recovery.add_argument('--channel-id', required=True)
    recovery.add_argument('--ledger', type=Path, default=Path('salidas') / 'youtube.sqlite3')
    resolution = recovery.add_mutually_exclusive_group(required=True)
    resolution.add_argument('--video-id', help='ID de la carga existente confirmada en Studio.')
    resolution.add_argument('--confirm-absent', action='store_true', help='Confirma que Studio no recibió el video.')
    recovery.set_defaults(handler=resolve_upload)
    live_cmd = commands.add_parser('live', help='Graba un buffer en vivo (Twitch/Kick/YouTube) y saca clips al instante.')
    live_cmd.add_argument('url', help='URL del stream en vivo.')
    live_cmd.add_argument('--output', type=Path, default=Path('fuentes'))
    live_cmd.add_argument('--clip-seconds', type=int, default=45, help='Duración por defecto al sacar clip.')
    live_cmd.add_argument('--buffer-minutes', type=int, default=15, help='Minutos máximos en el buffer circular.')
    live_cmd.add_argument('--segment-seconds', type=int, default=5, help='Duración de cada segmento TS.')
    live_cmd.add_argument('--quality', default='best', help='best o altura máxima (ej. 1080, 720).')
    add_rights(live_cmd)
    live_cmd.set_defaults(handler=live_capture)
    app_cmd = commands.add_parser('app', help='Inicia la aplicación de escritorio local de ClipForge.')
    app_cmd.add_argument('--port', type=int, default=8765)
    app_cmd.add_argument('--no-browser', action='store_true', help='Inicia solo el servidor local sin abrir ventana.')
    app_cmd.set_defaults(handler=launch_app)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
