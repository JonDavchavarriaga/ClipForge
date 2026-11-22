"""Tests for Gevik Clip professional features: sync, mini-editor, 1-click AI translation, clip manager, lifecycle."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from clipforge.cli import process
from clipforge.desktop.icon import generate_ico
from clipforge.desktop.jobs import JobManager
from clipforge.desktop.server import create_server
from clipforge.logging_utils import close_logging
from clipforge.pipeline.auto_translate import (
    available_translators,
    extract_json_object,
    translate_clip_with_agent,
)
from clipforge.pipeline.editor import edit_clip
from clipforge.pipeline.highlights import Cue, WordTiming, read_srt
from clipforge.pipeline.media import extract_clip_wav, ffmpeg, generate_thumbnail, probe
from clipforge.pipeline.subtitles import style_catalog, write_ass
from clipforge.process_manager import (
    ProcessRegistry,
    clear_instance_lock,
    instance_lock_path,
    write_instance_lock,
)
from types import SimpleNamespace


def _make_test_video(path: Path, seconds: int = 8):
    cmd = [
        ffmpeg(),
        '-y',
        '-f', 'lavfi', '-i', f'testsrc=duration={seconds}:size=1280x720:rate=30',
        '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest',
        str(path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GevikProTests(unittest.TestCase):
    def tearDown(self):
        close_logging()

    def test_extract_json_object_and_translators(self):
        self.assertEqual(extract_json_object('{"a": 1}')['a'], 1)
        fenced = 'Claro, aquí tienes:\n```json\n{"target_lang": "es", "cues": []}\n```'
        self.assertEqual(extract_json_object(fenced)['target_lang'], 'es')
        prose = 'Resultado final: {"ok": true} listo.'
        self.assertTrue(extract_json_object(prose)['ok'])
        with self.assertRaises(ValueError):
            extract_json_object('sin json aquí')

        translators = available_translators()
        self.assertEqual(set(translators.keys()), {'claude', 'codex', 'gemini'})
        for key in ('claude', 'codex', 'gemini'):
            self.assertIn('available', translators[key])
            self.assertIn('label', translators[key])

    def test_translate_clip_with_agent_preserves_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / 'captions.srt'
            srt.write_text(
                '1\n00:00:00,200 --> 00:00:02,400\nNo way we just hit that shot\n\n'
                '2\n00:00:02,600 --> 00:00:04,800\nClip that right now!\n',
                encoding='utf-8',
            )
            fake_reply = json.dumps({
                'source_lang': 'en',
                'target_lang': 'es',
                'cues': [
                    {
                        'id': 1,
                        'start': 0.2,
                        'end': 2.4,
                        'source_text': 'No way we just hit that shot',
                        'translated_text': '¡No puede ser ese disparo!',
                    },
                    {
                        'id': 2,
                        'start': 2.6,
                        'end': 4.8,
                        'source_text': 'Clip that right now!',
                        'translated_text': '¡Clipea eso ya mismo!',
                    },
                ],
            })
            with mock.patch('clipforge.pipeline.auto_translate.invoke_translator_engine', return_value=fake_reply):
                cues, out_srt, warnings, parsed = translate_clip_with_agent(
                    srt, 'en', 'es', engine='gemini', context='GTA 6',
                )
            self.assertEqual(len(cues), 2)
            self.assertAlmostEqual(cues[0].start, 0.2, places=3)
            self.assertAlmostEqual(cues[0].end, 2.4, places=3)
            self.assertEqual(cues[1].text, '¡Clipea eso ya mismo!')
            self.assertTrue(out_srt.is_file())
            self.assertTrue((Path(tmp) / 'translation-es.json').is_file())
            self.assertEqual(parsed['target_lang'], 'es')
            self.assertEqual(len(read_srt(out_srt)), 2)

    def test_style_catalog_and_ass_offset_and_positions(self):
        catalog = style_catalog()
        ids = [item['id'] for item in catalog]
        for expected in ('highlight', 'neon', 'fire', 'silver', 'bold', 'none'):
            self.assertIn(expected, ids)

        cues = [
            Cue(1.0, 2.5, 'Hola mundo', (WordTiming(1.0, 1.7, 'Hola'), WordTiming(1.7, 2.5, 'mundo'))),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out_fire = Path(tmp) / 'fire.ass'
            write_ass(cues, out_fire, 'fire', layout='split-cam', offset_ms=250, position='top')
            text_fire = out_fire.read_text(encoding='utf-8')
            self.assertIn('0:00:01.25', text_fire)
            self.assertIn('0:00:02.75', text_fire)

            out_silver = Path(tmp) / 'silver.ass'
            write_ass(cues, out_silver, 'silver', layout='center', offset_ms=-200, position='middle')
            text_silver = out_silver.read_text(encoding='utf-8')
            self.assertIn('0:00:00.80', text_silver)

    def test_sync_extract_wav_thumbnail_and_mini_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'sample.mp4'
            _make_test_video(src, seconds=8)
            srt = root / 'sample.srt'
            srt.write_text(
                '1\n00:00:01,000 --> 00:00:03,500\nFirst line here\n\n'
                '2\n00:00:04,000 --> 00:00:06,500\nSecond line here\n',
                encoding='utf-8',
            )
            out_dir = root / 'rendered'
            args = SimpleNamespace(
                source=src,
                output=out_dir,
                count=1,
                min_seconds=2,
                max_seconds=6,
                range=['00:00:01,00:00:07'],
                layout='split-cam',
                cam_region='0.7,0.65,0.28,0.3',
                caption_style='highlight',
                preview_first=False,
                srt=srt,
                skip_asr=True,
                model='tiny',
                language='en',
                denoise=False,
                audio_track=0,
                rights='local',
                rights_note='',
            )
            batch_dir = process(args)
            manifest = json.loads((batch_dir / 'manifest.json').read_text(encoding='utf-8'))
            self.assertIn('display_name', manifest)
            clip = manifest['clips'][0]
            self.assertEqual(clip['review_status'], 'pending')
            self.assertTrue((batch_dir / 'clip-01' / 'thumb.jpg').is_file())

            # Test clip-exact WAV extraction
            wav = root / 'exact.wav'
            extract_clip_wav(src, 2.0, 5.0, wav, 0)
            self.assertTrue(wav.is_file() and wav.stat().st_size > 1000)

            # Test integrated Mini-Editor: Trim IN/OUT + silver style + top position + offset + volume
            edited = edit_clip(
                batch_dir,
                'clip-01',
                start=2.0,
                end=5.5,
                caption_style='silver',
                subtitle_position='top',
                subtitle_offset_ms=120,
                volume_db=3.0,
                denoise=False,
            )
            self.assertAlmostEqual(edited['start'], 2.0, places=2)
            self.assertAlmostEqual(edited['end'], 5.5, places=2)
            self.assertEqual(edited['caption_style'], 'silver')
            self.assertEqual(edited['subtitle_position'], 'top')
            self.assertEqual(edited['subtitle_offset_ms'], 120)
            self.assertEqual(edited['volume_db'], 3.0)
            edited_info = probe(batch_dir / 'clip-01' / 'video.mp4')
            self.assertAlmostEqual(edited_info.duration, 3.5, delta=0.4)
            self.assertEqual((edited_info.width, edited_info.height), (1080, 1920))

    def test_process_registry_cancel_and_instance_lock_and_icon(self):
        registry = ProcessRegistry()
        cancel_ev = threading.Event()
        timer = threading.Timer(0.25, cancel_ev.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                registry.run_managed(
                    [sys.executable, '-c', 'import time; time.sleep(10)'],
                    cancel_event=cancel_ev,
                )
        finally:
            timer.cancel()

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            write_instance_lock(ws, 'http://127.0.0.1:8765', 8765)
            self.assertTrue(instance_lock_path(ws).is_file())
            clear_instance_lock(ws)
            self.assertFalse(instance_lock_path(ws).is_file())

            ico_path = ws / 'gevik-clip.ico'
            generate_ico(ico_path)
            self.assertTrue(ico_path.is_file() and ico_path.stat().st_size > 2000)

    def test_job_manager_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                jm = JobManager(logs_dir=Path(tmp))
                started = threading.Event()

                def slow_job(progress, cancel_event):
                    started.set()
                    for _ in range(100):
                        if cancel_event.is_set():
                            raise InterruptedError('Cancelado en test')
                        time.sleep(0.05)
                    return {'ok': True}

                job = jm.submit('test', 'Trabajo lento', slow_job)
                self.assertTrue(started.wait(timeout=3))
                res = jm.cancel(job.id)
                self.assertTrue(res['cancelled'])
                deadline = time.time() + 3
                while time.time() < deadline:
                    snap = jm.get(job.id)
                    if snap['status'] == 'cancelled':
                        break
                    time.sleep(0.05)
                self.assertEqual(jm.get(job.id)['status'], 'cancelled')
            finally:
                close_logging()

    def test_desktop_api_manager_download_thumb_security_and_purge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / 'workspace'
            src = root / 'demo.mp4'
            _make_test_video(src, seconds=4)
            server, state = create_server('127.0.0.1', 0, root_dir=ROOT, workspace_dir=ws)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{port}'
            try:
                # Ping + security headers
                with urlopen(f'{base}/api/ping') as resp:
                    self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
                    self.assertEqual(resp.headers.get('Referrer-Policy'), 'no-referrer')
                    self.assertEqual(resp.headers.get('X-Frame-Options'), 'SAMEORIGIN')
                    ping = json.loads(resp.read().decode('utf-8'))
                    self.assertEqual(ping['app'], 'Gevik Clip')

                # Status includes visual style_catalog and translators
                with urlopen(f'{base}/api/status') as resp:
                    status = json.loads(resp.read().decode('utf-8'))
                    self.assertEqual(status['app_name'], 'Gevik Clip')
                    self.assertGreaterEqual(len(status['style_catalog']), 6)
                    self.assertIn('gemini', status['translators'])

                # Seed a batch with 1 clip
                batch_dir = state.outputs_dir / 'lote-demo123'
                clip_dir = batch_dir / 'clip-01'
                clip_dir.mkdir(parents=True)
                (clip_dir / 'video.mp4').write_bytes(src.read_bytes())
                generate_thumbnail(clip_dir / 'video.mp4', clip_dir / 'thumb.jpg')
                (batch_dir / 'manifest.json').write_text(json.dumps({
                    'display_name': 'Twitch · Demo · Hoy',
                    'source': str(src),
                    'rights': 'local',
                    'audio_track': 0,
                    'duration': 4.0,
                    'status': 'complete',
                    'clips': [{
                        'id': 'clip-01',
                        'display_title': 'Jugada Epica',
                        'review_status': 'pending',
                        'start': 0.0,
                        'end': 4.0,
                        'layout': 'split-cam',
                        'caption_style': 'highlight',
                        'video': 'clip-01/video.mp4',
                        'thumb': 'clip-01/thumb.jpg',
                    }],
                }), encoding='utf-8')

                # Thumbnail endpoint
                with urlopen(f'{base}/api/thumb?batch=lote-demo123&clip=clip-01') as resp:
                    self.assertEqual(resp.headers.get('Content-Type'), 'image/jpeg')
                    self.assertGreater(len(resp.read()), 500)

                # Direct 1-click download endpoint with Content-Disposition
                with urlopen(f'{base}/api/download?batch=lote-demo123&clip=clip-01') as resp:
                    cd = resp.headers.get('Content-Disposition', '')
                    self.assertIn('attachment', cd)
                    self.assertIn('GevikClip_Jugada_Epica.mp4', cd)
                    self.assertGreater(len(resp.read()), 1000)

                # Update status to discarded then purge-discarded
                def post(endpoint, body):
                    req = Request(
                        f'{base}{endpoint}',
                        data=json.dumps(body).encode('utf-8'),
                        headers={'Content-Type': 'application/json', 'X-ClipForge-Token': state.token},
                        method='POST',
                    )
                    with urlopen(req) as r:
                        return json.loads(r.read().decode('utf-8'))

                upd = post('/api/clip/status', {'batch': 'lote-demo123', 'clip': 'clip-01', 'status': 'discarded'})
                self.assertEqual(upd['clip']['review_status'], 'discarded')

                purged = post('/api/clip/purge-discarded', {'batch': 'lote-demo123'})
                self.assertEqual(purged['purged'], 1)
                self.assertFalse(clip_dir.exists())
            finally:
                server.shutdown()
                server.server_close()
                close_logging()


if __name__ == '__main__':
    unittest.main()
