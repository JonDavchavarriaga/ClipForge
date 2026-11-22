import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from clipforge.cli import continue_batch, process, retest_preview_clip
from clipforge.desktop.jobs import JobManager
from clipforge.logging_utils import close_logging, configure_logging
from clipforge.pipeline.highlights import Cue, auto_clip_count, detect_muted_ranges, suggest
from clipforge.pipeline.media import extract_frame_jpg, ffmpeg, probe, run


class CommercialMuteAndAutoCountTests(unittest.TestCase):
    def test_detects_muted_commercial_blocks_and_excludes_them(self):
        # 180s stream where seconds 60..119 are muted Twitch commercials (energy=0.0)
        energy = [0.55] * 60 + [0.01] * 60 + [0.60] * 60
        muted = detect_muted_ranges(energy, threshold=0.04, min_silence_seconds=8)
        self.assertEqual(muted, [(60.0, 120.0)])

        cues = [
            Cue(t, t + 2.0, '¡Mira esta jugada!')
            for t in list(range(2, 56, 3)) + list(range(122, 176, 3))
        ]
        selected = suggest(cues, energy, 180.0, 0, 20, 35, muted_ranges=muted)
        self.assertGreaterEqual(len(selected), 1)
        for cand in selected:
            # No selected clip may overlap significantly with the 60..120 muted ad block
            overlap = max(0.0, min(cand.end, 120.0) - max(cand.start, 60.0))
            self.assertLessEqual(overlap / (cand.end - cand.start), 0.15)

    def test_auto_clip_count_scales_for_1h_and_2h_streams(self):
        self.assertEqual(auto_clip_count(120, []), 1)
        self.assertEqual(auto_clip_count(600, []), 3)
        # 1 hour stream with 6 minutes of muted Twitch ads -> ~54 usable min -> ~10 clips
        one_hour = auto_clip_count(3600, [(600, 780), (1800, 1980)])
        self.assertTrue(8 <= one_hour <= 12, f'unexpected 1h count: {one_hour}')
        # 2 hour stream -> capped cleanly at <= 18
        two_hours = auto_clip_count(7200, [])
        self.assertTrue(14 <= two_hours <= 18, f'unexpected 2h count: {two_hours}')


class PreviewCalibrationAndQueueTests(unittest.TestCase):
    def tearDown(self):
        close_logging()

    def test_preview_first_retest_camera_and_continue_batch(self):
        try:
            ffmpeg()
        except ValueError as error:
            self.skipTest(str(error))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs_dir = root / 'logs'
            configure_logging(logs_dir)
            source = root / 'stream.mp4'
            run([
                '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=6',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
                '-c:v', 'libx264', '-c:a', 'aac', source,
            ])
            srt = root / 'subs.srt'
            srt.write_text(
                '1\n00:00:00,200 --> 00:00:02,000\nPrimera reaccion\n\n'
                '2\n00:00:03,200 --> 00:00:05,500\nSegunda reaccion\n',
                encoding='utf-8',
            )
            stages = []
            args = SimpleNamespace(
                source=source,
                output=root / 'rendered',
                count=2,
                min_seconds=2,
                max_seconds=3,
                range=['0,2.5', '3,5.5'],
                layout='split-cam',
                cam_region='bottom-right',
                caption_style='highlight',
                preview_first=True,
                srt=srt,
                skip_asr=True,
                model='base',
                language='es',
                denoise=False,
                audio_track=0,
                rights='local',
                rights_note='',
            )
            folder = process(args, progress_cb=lambda s, p, d: stages.append((s, p)))
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['status'], 'awaiting_preview_approval')
            self.assertEqual(manifest['planned_total'], 2)
            self.assertEqual(manifest['remaining_count'], 1)
            self.assertEqual(len(manifest['clips']), 1)
            self.assertTrue((folder / 'plan.json').is_file())

            # Extract a preview JPEG frame for visual snipping-tool selection
            frame_jpg = extract_frame_jpg(source, 1.0, root / 'frame.jpg')
            self.assertEqual(frame_jpg.read_bytes()[:3], b'\xff\xd8\xff')

            # Record SHA-256 before retest and prove retest_preview_clip actually overwrites video.mp4
            clip_mp4 = folder / 'clip-01' / 'video.mp4'
            hash_before = hashlib.sha256(clip_mp4.read_bytes()).hexdigest()

            # Adjust camera box on clip-01 only
            updated = retest_preview_clip(folder, cam_region_spec='top-left')
            self.assertEqual(updated['clips'][0]['cam_region']['x'], 0.0)
            self.assertEqual(updated['clips'][0]['cam_region']['y'], 0.0)
            hash_after = hashlib.sha256(clip_mp4.read_bytes()).hexdigest()
            self.assertNotEqual(hash_before, hash_after)

            # Approve camera -> generate remaining clips reusing top-left without Whisper
            final_manifest = continue_batch(folder)
            self.assertEqual(final_manifest['status'], 'complete')
            self.assertEqual(final_manifest['remaining_count'], 0)
            self.assertEqual(len(final_manifest['clips']), 2)
            self.assertEqual(final_manifest['clips'][1]['cam_region']['x'], 0.0)
            self.assertEqual(probe(folder / 'clip-02' / 'video.mp4').width, 1080)
            # Rich disk telemetry logged in events.jsonl
            self.assertTrue((logs_dir / 'events.jsonl').is_file())
            close_logging()

    def test_fifo_queue_executes_jobs_sequentially_with_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            logs_dir = Path(directory) / 'logs'
            configure_logging(logs_dir)
            mgr = JobManager(logs_dir=logs_dir)
            order = []

            def task(name):
                def _run(progress):
                    progress('Trabajando', 50, f'Ejecutando {name}')
                    time.sleep(0.05)
                    order.append(name)
                    progress('Listo', 100, f'Terminó {name}')
                    return {'name': name}
                return _run

            j1 = mgr.submit('test', 'Uno', task('one'))
            j2 = mgr.submit('test', 'Dos', task('two'))
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if mgr.get(j2.id)['status'] == 'done':
                    break
                time.sleep(0.02)

            self.assertEqual(order, ['one', 'two'])
            self.assertEqual(mgr.get(j1.id)['percent'], 100)
            self.assertEqual(mgr.get(j2.id)['status'], 'done')
            snap = mgr.snapshot()
            self.assertEqual(snap['queued_count'], 0)
            close_logging()


if __name__ == '__main__':
    unittest.main()
