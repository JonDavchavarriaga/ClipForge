import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from clipforge.pipeline.highlights import Cue, snap_clip_bounds, suggest
from clipforge.pipeline.live import auto_clip_cooldown, auto_clip_post_roll
from clipforge.pipeline.media import CAM_PRESETS, ffmpeg, probe, render, run
from clipforge.pipeline.subtitles import build_title_ass, write_title_ass
from clipforge.publishing.auto_publish import (
    PublishStore,
    pkce_pair,
    publish_clip,
    tiktok_chunk_plan,
    tiktok_connect_interactive,
    tiktok_upload_draft,
)
from clipforge.publishing.publication import save_json


def _speech(duration: float, every: float = 3.0) -> list[Cue]:
    cues = []
    t = 0.5
    n = 0
    while t + 2.0 < duration:
        n += 1
        text = f'frase número {n} que dice algo.' if n % 2 == 0 else f'esto sigue con la jugada {n}'
        cues.append(Cue(t, t + 2.2, text))
        t += every
    return cues


class LongClipTests(unittest.TestCase):
    def test_short_live_source_keeps_whole_clip(self):
        cues = _speech(75)
        energy = [0.5] * 75
        selected = suggest(cues, energy, 75.0, 0, 45, 90)
        self.assertEqual(len(selected), 1)
        self.assertGreaterEqual(selected[0].end - selected[0].start, 60)

    def test_long_source_prefers_long_clips(self):
        cues = _speech(600)
        energy = [0.5] * 600
        selected = suggest(cues, energy, 600.0, 3, 45, 90)
        self.assertTrue(selected)
        for clip in selected:
            self.assertGreaterEqual(clip.end - clip.start, 45 - 0.01)
            self.assertLessEqual(clip.end - clip.start, 90 + 0.5)

    def test_end_snaps_to_sentence_and_never_mid_caption(self):
        cues = [
            Cue(0.0, 2.0, 'Mira esto'),
            Cue(40.0, 43.0, 'y entonces nos persiguen.'),
            Cue(46.0, 49.0, 'corre corre que'),
        ]
        start, end = snap_clip_bounds(0.0, 47.0, cues, 120.0, 30, 90)
        self.assertAlmostEqual(end, 43.35, places=2)  # end of the sentence, not inside "corre corre que"
        start, end = snap_clip_bounds(1.0, 60.0, [Cue(0.5, 3.0, 'hola bro')], 120.0, 30, 90)
        self.assertLess(start, 0.5)  # never start in the middle of a caption

    def test_live_window_has_post_roll_after_spike(self):
        self.assertEqual(auto_clip_post_roll(75), 26)
        self.assertGreaterEqual(auto_clip_post_roll(45), 20)
        self.assertLessEqual(auto_clip_post_roll(120), 35)
        self.assertEqual(auto_clip_cooldown(75), 60)


class TitleBurnTests(unittest.TestCase):
    def test_title_ass_top_first_seconds_two_lines(self):
        subs = build_title_ass('Nos destruyen con un helicóptero en plena persecución {\\b1}', clip_duration=60)
        self.assertEqual(len(subs.events), 1)
        event = subs.events[0]
        self.assertEqual((event.start, event.end), (0, 5000))
        self.assertLessEqual(event.text.count(r'\N'), 1)
        self.assertNotIn('{\\b1}', event.text)
        self.assertEqual(subs.styles['Title'].alignment.value, 8)  # top center
        self.assertEqual(subs.styles['Title'].borderstyle, 3)      # dark box behind text

    def test_title_shorter_than_clip_and_empty_title(self):
        subs = build_title_ass('Hola', clip_duration=3)
        self.assertEqual(subs.events[0].end, 3000)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(write_title_ass('   ', Path(tmp) / 'title.ass'))

    def test_render_with_title_layer(self):
        try:
            ffmpeg()
        except ValueError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'src.mp4'
            run(['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=2',
                 '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', source])
            clip_dir = root / 'clip-01'
            clip_dir.mkdir()
            title = write_title_ass('¡No puede ser!', clip_dir / 'title.ass', clip_duration=1.5)
            out = clip_dir / 'video.mp4'
            render(source, out, 0.0, 1.5, 0, 'split-cam', False, False,
                   cam_region=CAM_PRESETS['bottom-right'], title_file=title)
            info = probe(out)
            self.assertEqual((info.width, info.height), (1080, 1920))


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.store = PublishStore(self.root / 'workspace')

    def tearDown(self):
        self.tmp.cleanup()

    def _batch(self, title='Helicóptero nos destruye'):
        folder = self.root / 'lote-x'
        (folder / 'clip-01').mkdir(parents=True)
        (folder / 'clip-01' / 'video.mp4').write_bytes(b'fake-mp4-bytes')
        save_json(folder / 'manifest.json', {'clips': [{
            'id': 'clip-01', 'video': 'clip-01/video.mp4', 'display_title': title, 'quality': {'score': 80},
        }]})
        return folder

    def test_settings_mask_secret_and_validate(self):
        self.store.update_settings({'tiktok': {'client_key': 'abc', 'client_secret': 'supersecret1234'}})
        status = self.store.public_status()
        self.assertEqual(status['tiktok']['client_key'], 'abc')
        self.assertNotIn('supersecret', status['tiktok']['client_secret'])
        self.assertTrue(status['tiktok']['client_secret'].endswith('1234'))
        # Masked value sent back by the UI must not overwrite the real secret
        self.store.update_settings({'tiktok': {'client_secret': status['tiktok']['client_secret']}})
        self.assertEqual(self.store.settings()['tiktok']['client_secret'], 'supersecret1234')
        with self.assertRaises(ValueError):
            self.store.update_settings({'youtube': {'client_secret': str(self.root / 'missing.json')}})
        self.assertTrue(str(self.store.dir).endswith('credentials'))

    def test_pkce_is_hex_sha256(self):
        verifier, challenge = pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(challenge, hashlib.sha256(verifier.encode()).hexdigest())

    def test_chunk_plan(self):
        self.assertEqual(tiktok_chunk_plan(30 * 1024 * 1024), (30 * 1024 * 1024, 1))
        size, count = tiktok_chunk_plan(100 * 1024 * 1024)
        self.assertEqual((size, count), (10 * 1024 * 1024, 10))

    def test_publish_clip_ledger_and_daily_limit(self):
        folder = self._batch()
        calls = []

        def fake_youtube():
            calls.append('yt')
            return {'id': f'vid{len(calls)}', 'url': 'https://studio.youtube.com/x', 'privacy': 'private'}

        uploaders = {'youtube': fake_youtube, 'tiktok': lambda: {'id': 'pub1', 'privacy': 'draft'}}
        first = publish_clip(self.store, folder, 'clip-01', ['youtube', 'tiktok'], uploaders=uploaders)
        self.assertEqual(first['youtube']['status'], 'uploaded')
        self.assertEqual(first['tiktok']['status'], 'uploaded')
        again = publish_clip(self.store, folder, 'clip-01', ['youtube'], uploaders=uploaders)
        self.assertEqual(again['youtube']['status'], 'already')
        self.assertEqual(calls, ['yt'])
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['clips'][0]['uploads']['tiktok']['id'], 'pub1')

        self.store.update_settings({'auto': {'daily_limit': 1}})
        (folder / 'clip-01' / 'video.mp4').write_bytes(b'another-render')
        limited = publish_clip(self.store, folder, 'clip-01', ['youtube'], uploaders=uploaders)
        self.assertEqual(limited['youtube']['status'], 'skipped')

    def test_errors_are_reported_per_platform(self):
        folder = self._batch()

        def boom():
            raise RuntimeError('quota exceeded')

        result = publish_clip(self.store, folder, 'clip-01', ['youtube', 'tiktok'],
                              uploaders={'youtube': boom, 'tiktok': lambda: {'id': 'p2'}})
        self.assertEqual(result['youtube']['status'], 'error')
        self.assertIn('quota', result['youtube']['error'])
        self.assertEqual(result['tiktok']['status'], 'uploaded')

    def test_tiktok_inbox_upload_flow(self):
        self.store.update_settings({'tiktok': {'client_key': 'ck', 'client_secret': 'cs'}})
        self.store.tiktok_token.write_text(json.dumps({
            'access_token': 'tok', 'refresh_token': 'r', 'expires_at': 9e12, 'refresh_expires_at': 9e12,
        }), encoding='utf-8')
        video = self.root / 'v.mp4'
        video.write_bytes(b'x' * 1000)
        seen = []

        def opener(req, timeout=0):
            seen.append(req)
            if req.full_url.endswith('/inbox/video/init/'):
                body = json.loads(req.data)
                self.assertEqual(body['source_info'], {'source': 'FILE_UPLOAD', 'video_size': 1000,
                                                       'chunk_size': 1000, 'total_chunk_count': 1})
                self.assertEqual(req.get_header('Authorization'), 'Bearer tok')
                return _FakeResponse(json.dumps({'data': {'publish_id': 'p_123', 'upload_url': 'https://up.example/u?x=1'},
                                                 'error': {'code': 'ok'}}).encode())
            self.assertEqual(req.get_method(), 'PUT')
            self.assertEqual(req.get_header('Content-range'), 'bytes 0-999/1000')
            return _FakeResponse(b'')

        result = tiktok_upload_draft(self.store, video, opener=opener)
        self.assertEqual(result['id'], 'p_123')
        self.assertEqual(result['privacy'], 'draft')
        self.assertEqual(len(seen), 2)

    def test_tiktok_connect_with_local_callback(self):
        self.store.update_settings({'tiktok': {'client_key': 'ck', 'client_secret': 'cs', 'redirect_port': 38771}})

        def fake_browser(url):
            query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            self.assertEqual(query['code_challenge_method'], 'S256')
            self.assertEqual(query['redirect_uri'], 'http://localhost:38771/callback/')
            self.assertIn('video.upload', query['scope'])

            def hit():
                urlopen(f'http://127.0.0.1:38771/callback/?code=abc&state={query["state"]}', timeout=5).read()
            threading.Thread(target=hit, daemon=True).start()

        def opener(req, timeout=0):
            form = parse_qs(req.data.decode())
            self.assertEqual(form['code'], ['abc'])
            self.assertEqual(form['grant_type'], ['authorization_code'])
            self.assertTrue(form['code_verifier'][0])
            return _FakeResponse(json.dumps({'access_token': 'A', 'refresh_token': 'R', 'expires_in': 86400,
                                             'refresh_expires_in': 999999, 'open_id': 'u1',
                                             'scope': 'video.upload'}).encode())

        info = tiktok_connect_interactive(self.store, open_url=fake_browser, timeout=10, opener=opener)
        self.assertEqual(info['open_id'], 'u1')
        saved = json.loads(self.store.tiktok_token.read_text(encoding='utf-8'))
        self.assertEqual(saved['access_token'], 'A')


class AutoPipelineDecisionTests(unittest.TestCase):
    def test_only_good_live_clips_are_rendered(self):
        from clipforge.desktop.server import ClipForgeAppState

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            state = ClipForgeAppState(workspace_dir=Path(tmp))
            submitted = []
            state.submit_auto_pipeline = lambda path, window, **kw: (
                submitted.append((path, window)) or type('J', (), {'id': 'job1'})())
            state.recorder._clips.append(__import__('clipforge.pipeline.live', fromlist=['LiveClipRecord'])
                                         .LiveClipRecord('c1', 'a.mp4', 75, 'now', 'twitch', 'u'))
            state.recorder._clips.append(__import__('clipforge.pipeline.live', fromlist=['LiveClipRecord'])
                                         .LiveClipRecord('c2', 'b.mp4', 75, 'now', 'twitch', 'u'))
            state.auto_process_config.update({'auto_render': 'score', 'min_score': 70})
            state._on_live_summary('c1', {'analysis': 'done', 'score': 55})
            state._on_live_summary('c2', {'analysis': 'done', 'score': 82})
            self.assertEqual(submitted, [('b.mp4', 75)])
            clips = {c['clip_id']: c for c in state.recorder.status()['clips']}
            self.assertIn('55/100', clips['c1']['auto_status'])
            self.assertEqual(clips['c2']['auto_status'], 'En cola para renderizar')
            state.summarizer.shutdown()


if __name__ == '__main__':
    unittest.main()
