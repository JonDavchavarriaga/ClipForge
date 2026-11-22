import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clipforge.cli import approve, manual_ranges, read_run
from clipforge.pipeline.highlights import Cue, make_srt, suggest
from clipforge.pipeline.media import audio_preview, ffmpeg, load_wav_float32, probe, render, run
from clipforge.publishing.publication import (
    approved_clip,
    export_queue,
    reconcile_youtube,
    save_json,
    upload_youtube,
    utc_date,
)


class HighlightTests(unittest.TestCase):
    def test_selection_limits_and_overlap(self):
        cues = [Cue(time, time + 2, '¿Por qué pasó esto?') for time in range(2, 115, 3)]
        selected = suggest(cues, [0.4] * 120, 120, 3, 25, 40)
        self.assertEqual(len(selected), 3)
        for candidate in selected:
            self.assertGreaterEqual(candidate.end - candidate.start, 25 - 1e-9)
            self.assertLessEqual(candidate.end - candidate.start, 40)
        chronological = sorted(selected, key=lambda clip: clip.start)
        for left, right in zip(chronological, chronological[1:]):
            self.assertLessEqual(left.end, right.start)

    def test_cues_are_cut_and_rebased(self):
        cues = [Cue(0.5, 2, 'Mira'), Cue(7, 9, 'Aquí')]
        output = make_srt(cues, 1, 8)
        self.assertIn('00:00:00,000 --> 00:00:01,000\nMira', output)
        self.assertIn('00:00:06,000 --> 00:00:07,000\nAquí', output)

    def test_rejects_bad_ranges_and_silence(self):
        with self.assertRaisesRegex(ValueError, 'dentro del video'):
            manual_ranges(['00:37,00:52'], 45)
        with self.assertRaisesRegex(ValueError, 'No se detectó'):
            suggest([], [0.0] * 90, 90, 3, 25, 55)


def fake_upload_module():
    return patch.dict('sys.modules', {'googleapiclient.http': SimpleNamespace(
        MediaFileUpload=lambda *args, **kwargs: SimpleNamespace(stream=lambda: io.BytesIO()),
    )})


class PublicationTests(unittest.TestCase):
    def test_approved_clip_queue_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'clip-01').mkdir()
            video = root / 'clip-01' / 'video.mp4'
            video.write_bytes(b'fixture')
            save_json(root / 'manifest.json', {'clips': [{'id': 'clip-01', 'video': 'clip-01/video.mp4'}]})
            _, initial = read_run(root)
            self.assertNotIn('approval', initial[0])
            approve(SimpleNamespace(run=root, clip='clip-01', title='Mi clip', caption='=1+1',
                                    platform=['youtube', 'tiktok', 'tiktok'], publish_at=None))
            root, clips = read_run(root)
            filename, count = export_queue(root, clips)
            self.assertEqual(count, 2)
            with filename.open(encoding='utf-8-sig', newline='') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([row['plataforma'] for row in rows], ['youtube', 'tiktok'])
            self.assertEqual(rows[0]['texto'], "'=1+1")
            video.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'cambió'):
                approved_clip(root, clips[0])

    def test_upload_is_private_and_deduplicated(self):
        api = Mock()
        insert = api.videos.return_value.insert
        insert.return_value.next_chunk.side_effect = [(object(), None), (None, {'id': 'abcdefghijk'})]
        approval = {'title': 'Mi clip', 'caption': 'Texto'}
        with tempfile.TemporaryDirectory() as directory, fake_upload_module():
            root = Path(directory)
            args = (api, root / 'youtube.sqlite3', 'channel', root / 'video.mp4', 'hash', approval, None, False)
            self.assertEqual(upload_youtube(*args), ('abcdefghijk', True))
            self.assertEqual(upload_youtube(*args), ('abcdefghijk', False))
            insert.assert_called_once()
            status = insert.call_args.kwargs['body']['status']
            self.assertEqual(status, {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False})

    def test_unknown_result_blocks_second_upload_until_reconciled(self):
        api = Mock()
        insert = api.videos.return_value.insert
        insert.return_value.next_chunk.side_effect = TimeoutError
        approval = {'title': 'Mi clip', 'caption': ''}
        with tempfile.TemporaryDirectory() as directory, fake_upload_module():
            root = Path(directory)
            ledger = root / 'youtube.sqlite3'
            args = (api, ledger, 'channel', root / 'video.mp4', 'hash', approval, None, False)
            with self.assertRaisesRegex(ValueError, 'Carga sin confirmar'):
                upload_youtube(*args)
            with self.assertRaisesRegex(ValueError, 'intento pendiente'):
                upload_youtube(*args)
            reconcile_youtube(ledger, 'channel', 'hash', 'abcdefghijk')
            self.assertEqual(upload_youtube(*args), ('abcdefghijk', False))
            insert.assert_called_once()

    def test_dates_convert_bogota_to_utc(self):
        self.assertEqual(utc_date('2027-01-15T18:00:00-05:00'), '2027-01-15T23:00:00Z')
        with self.assertRaisesRegex(ValueError, 'zona horaria'):
            utc_date('2027-01-15T18:00:00')


class VideoTest(unittest.TestCase):
    def test_render_vertical_video_with_audio_and_captions(self):
        try:
            ffmpeg()
        except ValueError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'input.mp4', root / 'output.mp4'
            run(['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=2',
                 '-f', 'lavfi', '-i', 'sine=frequency=450:duration=2', '-c:v', 'libx264', '-c:a', 'aac', source])
            (root / 'captions.srt').write_text(make_srt([Cue(0, 1, 'Prueba de subtítulos')], 0, 1), encoding='utf-8')
            render(source, output, 0, 1, 0, 'center', True, True)
            info = probe(output)
            self.assertEqual(info.audio_tracks, 1)
            self.assertAlmostEqual(info.duration, 1.0, delta=0.08)

            wav = root / 'preview.wav'
            audio_preview(source, wav, 0)
            samples = load_wav_float32(wav)
            self.assertEqual(str(samples.dtype), 'float32')
            self.assertGreater(samples.size, 15000)
            self.assertLessEqual(float(samples.max()), 1.0)
            self.assertGreaterEqual(float(samples.min()), -1.0)


if __name__ == '__main__':
    unittest.main()
