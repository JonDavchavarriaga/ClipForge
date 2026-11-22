import importlib.util
import tempfile
import unittest
from pathlib import Path

from clipforge.config import ROOT_DIR, get_channel_profile, load_channel_profiles
from clipforge.pipeline.highlights import Cue, read_srt
from clipforge.pipeline.translation import (
    apply_translated_bundle,
    build_translation_bundle,
    write_cues_srt,
)


def _load_agent_review_module():
    script = ROOT_DIR / '.agents' / 'skills' / 'multi-agent-review' / 'scripts' / 'agent_review.py'
    spec = importlib.util.spec_from_file_location('agent_review', script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class TranslationWorkflowTests(unittest.TestCase):
    def test_pack_apply_and_preserve_timestamps(self):
        cues = [Cue(0.0, 2.2, 'No way he hit that shot!'), Cue(2.5, 4.8, 'Chat, clip that right now!')]
        bundle = build_translation_bundle(cues, 'en', 'es', channel='es_main', context='GTA 6')
        self.assertEqual(len(bundle['cues']), 2)
        bundle['cues'][0]['translated_text'] = '¡No puede ser ese tiro!'
        bundle['cues'][1]['translated_text'] = '¡Chat, clipeen eso ya!'

        translated, warnings = apply_translated_bundle(cues, bundle)
        self.assertEqual(warnings, [])
        self.assertEqual([(c.start, c.end) for c in translated], [(0.0, 2.2), (2.5, 4.8)])

        with tempfile.TemporaryDirectory() as directory:
            out = write_cues_srt(translated, Path(directory) / 'captions.es.srt')
            reloaded = read_srt(out)
            self.assertEqual(len(reloaded), 2)
            self.assertEqual(reloaded[0].text, '¡No puede ser ese tiro!')

    def test_rejects_missing_or_mismatched_cues(self):
        cues = [Cue(0.0, 1.5, 'Watch this')]
        bundle = build_translation_bundle(cues, 'en', 'es')
        with self.assertRaisesRegex(ValueError, 'Falta "translated_text"'):
            apply_translated_bundle(cues, bundle)
        bundle['cues'].append({'id': 2, 'translated_text': 'Extra'})
        with self.assertRaisesRegex(ValueError, 'se esperaban 1'):
            apply_translated_bundle(cues, bundle)

    def test_warns_on_long_or_fast_lines(self):
        cues = [Cue(0.0, 1.0, 'Fast')]
        bundle = build_translation_bundle(cues, 'en', 'es')
        bundle['cues'][0]['translated_text'] = 'Esta línea es intencionalmente muchísimo más larga que el límite'
        _, warnings = apply_translated_bundle(cues, bundle)
        self.assertGreaterEqual(len(warnings), 2)


class ConfigAndAgentBridgeTests(unittest.TestCase):
    def test_channel_profiles_loaded(self):
        profiles = load_channel_profiles()
        self.assertIn('es_main', profiles['channels'])
        self.assertIn('en_main', profiles['channels'])
        es_channel = get_channel_profile('es_main')
        self.assertEqual(es_channel['audience_language'], 'es')

    def test_agent_review_blocks_secret_files_and_builds_prompt(self):
        agent_review = _load_agent_review_module()
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / 'client_secret_123.json'
            secret.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Bloqueado por seguridad'):
                agent_review.validate_input_path(secret)

            sample = Path(directory) / 'translation-es.json'
            sample.write_text('{"cues": []}', encoding='utf-8')
            prompt = agent_review.build_prompt('qa-reviewer', 'Revisa EN->ES', sample, include_glossary=True)
            self.assertIn('Revisor de Calidad', prompt)
            self.assertIn('Glosario Gaming', prompt)
            self.assertIn('Revisa EN->ES', prompt)


if __name__ == '__main__':
    unittest.main()
