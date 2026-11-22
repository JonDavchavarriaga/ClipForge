import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clipforge.cli import rerender
from clipforge.pipeline.highlights import Cue, WordTiming, clip_cues
from clipforge.pipeline.media import (
    CAM_PRESETS,
    CamRegion,
    ffmpeg,
    parse_cam_region,
    probe,
    render,
    run,
)
from clipforge.pipeline.subtitles import build_ass, estimate_word_timings, sanitize_ass_text, write_ass
from clipforge.publishing.publication import save_json


class CamRegionTests(unittest.TestCase):
    def test_parse_presets_and_custom_coords(self):
        self.assertIsNone(parse_cam_region('auto'))
        self.assertEqual(parse_cam_region('top-right'), CAM_PRESETS['top-right'])
        custom = parse_cam_region('0.65,0.60,0.30,0.35')
        self.assertEqual(custom, CamRegion(0.65, 0.60, 0.30, 0.35))

    def test_rejects_invalid_cam_regions(self):
        with self.assertRaises(ValueError):
            parse_cam_region('invalid-preset')
        with self.assertRaises(ValueError):
            parse_cam_region('0.8,0.8,0.5,0.5')
        with self.assertRaises(ValueError):
            parse_cam_region('0.1,0.1,0.02,0.2')


class AssSubtitleTests(unittest.TestCase):
    def test_word_timings_use_whisper_words_and_sanitize(self):
        cue = Cue(
            1.0,
            2.4,
            'No {way} bro',
            words=(
                WordTiming(1.0, 1.3, 'No'),
                WordTiming(1.3, 1.8, '{way}'),
                WordTiming(1.8, 2.4, 'bro'),
            ),
        )
        rebased = clip_cues([cue], 1.0, 3.0)
        self.assertEqual(len(rebased), 1)
        self.assertAlmostEqual(rebased[0].words[0].start, 0.0)
        timings = estimate_word_timings(rebased[0])
        self.assertEqual([w.text for w in timings], ['No', '｛way｝', 'bro'])
        self.assertEqual(sanitize_ass_text(r'{\b1}hack'), '｛＼b1｝hack')

    def test_build_ass_highlight_and_neon_from_translated_srt(self):
        cues = [Cue(0.0, 1.8, '¡Qué jugada en Vice City!')]
        subs = build_ass(cues, 'highlight', layout='split-cam')
        self.assertEqual(subs.info['PlayResX'], '1080')
        self.assertEqual(subs.info['PlayResY'], '1920')
        self.assertEqual(subs.styles['Default'].marginv, 380)
        # 5 words -> 5 word-highlight events
        self.assertEqual(len(subs.events), 5)
        self.assertIn(r'\1c&H0015CCFA&', subs.events[0].text)

        neon = build_ass(cues, 'neon', layout='face')
        self.assertEqual(neon.styles['Default'].marginv, 320)
        self.assertIn('VICE', neon.events[-1].text)

        bold = build_ass(cues, 'bold', layout='fit')
        self.assertEqual(len(bold.events), 1)


class SplitCamAndRerenderTests(unittest.TestCase):
    def test_render_split_cam_with_animated_ass_and_rerender(self):
        try:
            ffmpeg()
        except ValueError as error:
            self.skipTest(str(error))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'gta6_stream.mp4'
            run([
                '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                '-c:v', 'libx264', '-c:a', 'aac', source,
            ])
            clip_dir = root / 'lote' / 'clip-01'
            clip_dir.mkdir(parents=True)
            cues = [
                Cue(0.0, 1.0, '¡No puede ser!', words=(
                    WordTiming(0.0, 0.3, '¡No'),
                    WordTiming(0.3, 0.65, 'puede'),
                    WordTiming(0.65, 1.0, 'ser!'),
                )),
            ]
            ass_file = write_ass(cues, clip_dir / 'captions.ass', 'highlight', layout='split-cam')
            output = clip_dir / 'video.mp4'
            render(
                source,
                output,
                0.0,
                1.0,
                0,
                'split-cam',
                True,
                False,
                captions_file=ass_file,
                cam_region=CAM_PRESETS['bottom-right'],
            )
            info = probe(output)
            self.assertEqual((info.width, info.height), (1080, 1920))
            self.assertEqual(info.audio_tracks, 1)
            self.assertAlmostEqual(info.duration, 1.0, delta=0.1)

            # Test rerender with a translated SRT + neon ASS style
            save_json(root / 'lote' / 'manifest.json', {
                'source': str(source),
                'audio_track': 0,
                'clips': [{
                    'id': 'clip-01',
                    'start': 0.0,
                    'end': 1.0,
                    'layout': 'split-cam',
                    'cam_region': {'x': 0.66, 'y': 0.62, 'w': 0.34, 'h': 0.38},
                    'caption_style': 'highlight',
                    'video': 'clip-01/video.mp4',
                    'captions': None,
                }],
            })
            es_srt = root / 'captions.es.srt'
            es_srt.write_text('1\n00:00:00,000 --> 00:00:00,900\n¡Clipeen esto ya!\n', encoding='utf-8')
            rerender(SimpleNamespace(
                run=root / 'lote',
                clip='clip-01',
                srt=es_srt,
                caption_style='neon',
                layout='split-cam',
                cam_region='top-right',
                denoise=False,
            ))
            re_info = probe(output)
            self.assertEqual((re_info.width, re_info.height), (1080, 1920))
            self.assertTrue((clip_dir / 'captions.rendered.ass').is_file())


if __name__ == '__main__':
    unittest.main()
