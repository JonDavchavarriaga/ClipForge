import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from clipforge.desktop.icon import generate_ico
from clipforge.desktop.server import create_server
from clipforge.logging_utils import (
    close_logging,
    configure_logging,
    ensure_safe_stdio,
    read_recent_errors,
    read_recent_logs,
    record_error,
)
from clipforge.pipeline.live import (
    LiveRecorder,
    concat_segments_to_mp4,
    detect_audio_spike,
    detect_platform,
    parse_stream_embed,
    validate_stream_url,
)
from clipforge.pipeline.media import ffmpeg, probe, run
from clipforge.publishing.publication import save_json
from clipforge.storage import resolve_workspace


class LiveClipperTests(unittest.TestCase):
    def tearDown(self):
        close_logging()
    def test_detect_platform_embed_and_validate_url(self):
        self.assertEqual(detect_platform('https://www.twitch.tv/streamer'), 'twitch')
        self.assertEqual(detect_platform('https://kick.com/streamer'), 'kick')
        self.assertEqual(detect_platform('https://www.youtube.com/watch?v=abc'), 'youtube')
        self.assertEqual(detect_platform('https://example.org/live.m3u8'), 'stream')
        self.assertEqual(validate_stream_url(' https://kick.com/demo '), 'https://kick.com/demo')

        tw = parse_stream_embed('https://www.twitch.tv/elrubius')
        self.assertIn('player.twitch.tv/?channel=elrubius', tw['embed_url'])
        kk = parse_stream_embed('https://kick.com/westcol')
        self.assertEqual(kk['embed_url'], 'https://player.kick.com/westcol')
        yt = parse_stream_embed('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertIn('youtube.com/embed/dQw4w9WgXcQ', yt['embed_url'])

        with self.assertRaises(ValueError):
            validate_stream_url('ftp://invalid.local/stream')

    def test_detect_audio_spike_for_autoclip(self):
        baseline = [-28.0, -27.5, -29.0, -28.2, -27.8]
        triggered, delta = detect_audio_spike(-21.0, baseline, sensitivity='medium')
        self.assertTrue(triggered)
        self.assertGreater(delta, 5.0)
        quiet, _ = detect_audio_spike(-27.0, baseline, sensitivity='medium')
        self.assertFalse(quiet)

    def test_live_recorder_cuts_clip_and_assembles_full_recording(self):
        try:
            ffmpeg()
        except ValueError as error:
            self.skipTest(str(error))

        with tempfile.TemporaryDirectory() as directory:
            ws = resolve_workspace(Path(directory) / 'workspace')
            recorder = LiveRecorder(
                ws.live_recordings_dir,
                clips_root=ws.live_clips_dir,
                logs_dir=ws.logs_dir,
                segment_seconds=2,
                buffer_minutes=5,
            )
            session_dir = ws.live_recordings_dir / 'twitch-test-session'
            buffer_dir = session_dir / '_buffer'
            buffer_dir.mkdir(parents=True)
            for idx in range(3):
                seg = buffer_dir / f'seg_{idx:06d}.ts'
                run([
                    '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                    '-c:v', 'libx264', '-c:a', 'aac', '-f', 'mpegts', seg,
                ])
            recorder._session_dir = session_dir
            recorder._buffer_dir = buffer_dir
            recorder._platform = 'twitch'
            recorder._channel = 'testchan'
            recorder._url = 'https://twitch.tv/testchan'
            recorder._save_full_recording = True

            record = recorder.save_clip(5, mode='auto', reason='Pico de reacción')
            mp4 = Path(record.path)
            self.assertTrue(mp4.is_file())
            self.assertTrue(mp4.is_relative_to(ws.live_clips_dir))
            info = probe(mp4)
            self.assertGreaterEqual(info.duration, 2.5)
            self.assertTrue((mp4.parent / 'clip.json').is_file())

            # Stopping assembles full_recording.mp4 in live_recordings/
            stopped = recorder.stop()
            full_mp4 = Path(stopped['full_recording_path'])
            self.assertTrue(full_mp4.is_file())
            self.assertEqual(full_mp4.name, 'full_recording.mp4')
            self.assertGreaterEqual(probe(full_mp4).duration, 2.5)
            close_logging()

    def test_concat_rejects_empty_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                concat_segments_to_mp4([], Path(directory) / 'out.mp4')


class StorageAndLoggingTests(unittest.TestCase):
    def test_workspace_folders_and_structured_error_log(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = resolve_workspace(Path(directory) / 'workspace')
            self.assertTrue(ws.logs_dir.is_dir())
            self.assertTrue(ws.live_recordings_dir.is_dir())
            self.assertTrue(ws.live_clips_dir.is_dir())
            self.assertTrue(ws.rendered_clips_dir.is_dir())
            configure_logging(ws.logs_dir)
            try:
                raise RuntimeError('Fallo simulado de Whisper')
            except RuntimeError as exc:
                record_error('test.unit', exc, context={'clip': 'clip-01'}, logs_dir=ws.logs_dir)

            errors = read_recent_errors(ws.logs_dir)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]['source'], 'test.unit')
            self.assertIn('Fallo simulado de Whisper', errors[0]['message'])
            logs = read_recent_logs(ws.logs_dir)
            self.assertTrue(any('Fallo simulado de Whisper' in line for line in logs))
            close_logging()

    def test_safe_stdio_guards_pythonw_none_streams(self):
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            ensure_safe_stdio()
            self.assertIsNotNone(sys.stdout)
            self.assertIsNotNone(sys.stderr)
            sys.stderr.write('ok')
        finally:
            sys.stdout, sys.stderr = old_out, old_err


class DesktopIconAndServerTests(unittest.TestCase):
    def test_generate_valid_ico(self):
        with tempfile.TemporaryDirectory() as directory:
            ico = generate_ico(Path(directory) / 'gevik-clip.ico', sizes=(16, 32, 64))
            raw = ico.read_bytes()
            self.assertEqual(raw[:6], b'\x00\x00\x01\x00\x03\x00')
            self.assertGreater(len(raw), 500)

    def test_desktop_api_embed_logs_library_translation_and_video_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ws_root = root / 'workspace'
            lote = ws_root / 'rendered_clips' / 'lote-demo'
            clip_dir = lote / 'clip-01'
            clip_dir.mkdir(parents=True)
            video_bytes = b'0123456789abcdef' * 10
            (clip_dir / 'video.mp4').write_bytes(video_bytes)
            (clip_dir / 'captions.srt').write_text(
                '1\n00:00:00,000 --> 00:00:01,500\nNo way bro!\n',
                encoding='utf-8',
            )
            save_json(lote / 'manifest.json', {
                'source': str(root / 'source.mp4'),
                'rights': {'kind': 'local', 'note': ''},
                'audio_track': 0,
                'clips': [{
                    'id': 'clip-01',
                    'start': 0.0,
                    'end': 1.5,
                    'layout': 'split-cam',
                    'video': 'clip-01/video.mp4',
                    'captions': 'clip-01/captions.srt',
                }],
            })

            server, state = create_server('127.0.0.1', 0, root_dir=root, workspace_dir=ws_root)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{port}'
            headers = {'Content-Type': 'application/json', 'X-ClipForge-Token': state.token}
            try:
                html = urlopen(f'{base}/').read().decode('utf-8')
                self.assertIn(state.token, html)

                # Embed endpoint
                embed_req = Request(
                    f'{base}/api/live/embed',
                    data=json.dumps({'url': 'https://kick.com/westcol'}).encode(),
                    headers=headers,
                    method='POST',
                )
                embed = json.loads(urlopen(embed_req).read().decode('utf-8'))['embed']
                self.assertEqual(embed['embed_url'], 'https://player.kick.com/westcol')

                # Trigger a controlled 400 error and verify it is saved to errors.jsonl
                err_req = Request(
                    f'{base}/api/process',
                    data=json.dumps({'source': ''}).encode(),
                    headers=headers,
                    method='POST',
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(err_req)
                self.assertEqual(ctx.exception.code, 400)

                logs_payload = json.loads(urlopen(f'{base}/api/logs').read().decode('utf-8'))
                self.assertGreaterEqual(len(logs_payload['errors']), 1)

                # Library endpoint
                lib = json.loads(urlopen(f'{base}/api/library').read().decode('utf-8'))
                self.assertIn('live_recordings_dir', lib['paths'])

                # Batches + CSRF check
                batches = json.loads(urlopen(f'{base}/api/batches').read().decode('utf-8'))['batches']
                self.assertEqual(len(batches), 1)
                bad_req = Request(f'{base}/api/queue', data=b'{"batch":"lote-demo"}', method='POST')
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(bad_req)
                self.assertEqual(ctx.exception.code, 403)

                # Pack -> Apply -> Approve -> Queue -> Range 206
                pack_req = Request(
                    f'{base}/api/translation/pack',
                    data=json.dumps({'batch': 'lote-demo', 'clip': 'clip-01', 'source_lang': 'en', 'target_lang': 'es'}).encode(),
                    headers=headers,
                    method='POST',
                )
                bundle = json.loads(urlopen(pack_req).read().decode('utf-8'))['bundle']
                bundle['cues'][0]['translated_text'] = '¡No puede ser!'

                apply_req = Request(
                    f'{base}/api/translation/apply',
                    data=json.dumps({
                        'batch': 'lote-demo',
                        'clip': 'clip-01',
                        'bundle': bundle,
                        'ass_style': 'highlight',
                        'rerender': False,
                    }).encode(),
                    headers=headers,
                    method='POST',
                )
                applied = json.loads(urlopen(apply_req).read().decode('utf-8'))
                self.assertTrue(Path(applied['srt']).is_file())
                self.assertTrue((clip_dir / 'captions.es.ass').is_file())

                app_req = Request(
                    f'{base}/api/approve',
                    data=json.dumps({
                        'batch': 'lote-demo',
                        'clip': 'clip-01',
                        'title': 'Clip de prueba',
                        'caption': 'Hola #shorts',
                        'platforms': ['youtube', 'tiktok'],
                    }).encode(),
                    headers=headers,
                    method='POST',
                )
                approved = json.loads(urlopen(app_req).read().decode('utf-8'))
                self.assertIn('sha256', approved['approval'])

                q_req = Request(
                    f'{base}/api/queue',
                    data=json.dumps({'batch': 'lote-demo'}).encode(),
                    headers=headers,
                    method='POST',
                )
                queued = json.loads(urlopen(q_req).read().decode('utf-8'))
                self.assertTrue(Path(queued['csv']).is_file())

                range_req = Request(f'{base}/api/media?batch=lote-demo&clip=clip-01', headers={'Range': 'bytes=0-15'})
                with urlopen(range_req) as resp:
                    self.assertEqual(resp.status, 206)
                    self.assertEqual(resp.read(), video_bytes[:16])
            finally:
                server.shutdown()
                server.server_close()
                close_logging()


if __name__ == '__main__':
    unittest.main()
