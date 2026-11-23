import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipforge.pipeline.clip_insights import (
    TITLE_MAX_CHARS,
    evaluate_batch,
    heuristic_title,
    parse_review_reply,
    rank_clips,
    score_clip,
)
from clipforge.pipeline.highlights import Cue, WordTiming, group_words_into_cues
from clipforge.pipeline.media import resolve_auto_language
from clipforge.pipeline.subtitles import (
    ASS_PRESETS,
    balanced_lines,
    build_ass,
    max_line_chars,
    paginate_words,
)
from clipforge.publishing.publication import save_json


def _words(text: str, start: float = 0.0, step: float = 0.3) -> list[WordTiming]:
    out = []
    t = start
    for token in text.split():
        out.append(WordTiming(t, t + step * 0.9, token))
        t += step
    return out


class CueGroupingTests(unittest.TestCase):
    def test_breaks_on_sentence_punctuation(self):
        cues = group_words_into_cues(_words('Mira esto. No puede ser lo que pasó.'))
        self.assertEqual([c.text for c in cues], ['Mira esto.', 'No puede ser lo que pasó.'])

    def test_respects_word_and_char_limits(self):
        cues = group_words_into_cues(_words('uno dos tres cuatro cinco seis siete ocho nueve diez'))
        self.assertTrue(all(len(c.text.split()) <= 5 for c in cues))
        self.assertTrue(all(len(c.text) <= 30 for c in cues))

    def test_pause_starts_new_cue(self):
        words = _words('hola bro') + _words('ya volví', start=3.0)
        cues = group_words_into_cues(words)
        self.assertEqual([c.text for c in cues], ['hola bro', 'ya volví'])

    def test_merges_flashing_single_word_fragment(self):
        # 'mí.' would otherwise be a 1-word cue flashing for ~0.14s
        words = _words('Porque siempre me pasa esto a') + [WordTiming(1.85, 1.99, 'mí.')]
        cues = group_words_into_cues(words)
        self.assertFalse(any(c.text == 'mí.' for c in cues))
        self.assertTrue(cues[-1].text.endswith('mí.'))


class AutoLanguageTests(unittest.TestCase):
    def test_keeps_es_or_en_detection(self):
        self.assertEqual(resolve_auto_language('es', [('es', 0.9)]), 'es')
        self.assertEqual(resolve_auto_language('en', []), 'en')

    def test_wrong_language_falls_back_to_likelier_of_es_en(self):
        # Real case: a Spanish stream detected as Finnish / Greek over gameplay noise
        probs = [('fi', 0.41), ('el', 0.2), ('es', 0.15), ('en', 0.08)]
        self.assertEqual(resolve_auto_language('fi', probs), 'es')
        self.assertEqual(resolve_auto_language('el', [('el', 0.5), ('en', 0.3), ('es', 0.1)]), 'en')
        self.assertIn(resolve_auto_language('el', None), ('es', 'en'))


class CaptionLayoutTests(unittest.TestCase):
    def test_balanced_lines_avoid_orphan_word(self):
        tokens = 'helicóptero y nos destruye a todos.'.split()
        lines = balanced_lines(tokens, 20)
        self.assertEqual(len(lines), 2)
        top = ' '.join(tokens[i] for i in lines[0])
        bottom = ' '.join(tokens[i] for i in lines[1])
        self.assertLessEqual(abs(len(top) - len(bottom)), 8)
        self.assertGreater(len(lines[1]), 1)

    def test_pages_always_fit_safe_width(self):
        for preset in ASS_PRESETS.values():
            limit = max_line_chars(preset)
            text = 'Estábamos ganando tranquilamente y de repente aparece un tipo con un helicóptero y nos destruye'
            tokens = [t.upper() if preset.uppercase else t for t in text.split()]
            for page in paginate_words(tokens, limit):
                lines = balanced_lines([tokens[i] for i in page], limit)
                self.assertIsNotNone(lines, preset.id)
                self.assertLessEqual(len(lines), 2)

    def test_long_translated_cue_is_split_into_pages_without_flicker(self):
        cue = Cue(0.0, 6.0, 'Estábamos ganando tranquilamente y de repente aparece un tipo con un helicóptero y nos destruye a todos.')
        subs = build_ass([cue], 'highlight')
        texts = {e.text.replace(r'{\r}', '') for e in subs.events}
        self.assertGreater(len(texts), 1)
        for event in subs.events:
            self.assertLessEqual(event.text.count(r'\N'), 1)
            self.assertNotIn('fscx', event.text)
        # Events are contiguous (no blink between words/pages)
        ordered = sorted(subs.events, key=lambda e: e.start)
        for prev, nxt in zip(ordered, ordered[1:]):
            self.assertLessEqual(nxt.start - prev.end, 1)

    def test_page_lingers_and_never_overlaps_next(self):
        cues = [
            Cue(0.0, 1.0, 'Hola bro.', words=(WordTiming(0.0, 0.4, 'Hola'), WordTiming(0.4, 1.0, 'bro.'))),
            Cue(1.2, 2.0, 'Mira esto', words=(WordTiming(1.2, 1.6, 'Mira'), WordTiming(1.6, 2.0, 'esto'))),
        ]
        subs = build_ass(cues, 'bold')
        self.assertEqual(len(subs.events), 2)
        self.assertEqual(subs.events[0].end, subs.events[1].start)  # gap < 0.5s is filled
        self.assertEqual(subs.events[1].end, 2250)  # last page lingers 0.25s


class TitleAndScoreTests(unittest.TestCase):
    def _cues(self):
        return [
            Cue(0.3, 2.0, 'Bro, mira esto.'),
            Cue(2.2, 5.0, '¡No puede ser lo que acaba de pasar!'),
            Cue(5.5, 9.0, 'Estábamos ganando tranquilamente.'),
            Cue(9.2, 18.5, 'Y de repente aparece un helicóptero y nos destruye a todos.'),
        ]

    def test_heuristic_title_picks_hooky_sentence(self):
        title = heuristic_title(self._cues())
        self.assertIn('No puede ser', title)
        self.assertLessEqual(len(title), TITLE_MAX_CHARS)
        self.assertEqual(heuristic_title([], 'Clip 03'), 'Clip 03')

    def test_title_is_trimmed_at_word_boundary(self):
        long = Cue(0, 5, 'esta es una frase larguísima que no tiene ningún final claro y sigue y sigue sin parar nunca jamás')
        title = heuristic_title([long])
        self.assertLessEqual(len(title), TITLE_MAX_CHARS)
        self.assertTrue(title.endswith('…'))
        self.assertTrue(title[0].isupper())

    def test_good_clip_scores_higher_than_silent_one(self):
        good = score_clip(self._cues(), 19.0, 0.6)
        silent = score_clip([Cue(12.0, 13.0, 'eh')], 40.0, 0.1)
        self.assertGreater(good['score'], silent['score'] + 20)
        self.assertTrue(good['strengths'])
        self.assertTrue(any('gancho' in w.lower() for w in silent['weaknesses']))
        self.assertTrue(0 <= silent['score'] <= 100)

    def test_rank_ignores_discarded(self):
        clips = [
            {'id': 'clip-01', 'quality': {'score': 50}},
            {'id': 'clip-02', 'quality': {'score': 90}, 'review_status': 'discarded'},
            {'id': 'clip-03', 'quality': {'score': 70}},
        ]
        self.assertEqual(rank_clips(clips), {'clip-03': 1, 'clip-01': 2})

    def test_parse_review_reply_validates_ids_and_clamps(self):
        raw = {'clips': [
            {'id': 'clip-01', 'title': '"#Esto es BRUTAL"', 'score': 140, 'reason': 'Buen gancho'},
            {'id': 'clip-99', 'title': 'inyectado', 'score': 10},
        ], 'best': 'clip-01'}
        results, best = parse_review_reply(raw, {'clip-01', 'clip-02'})
        self.assertEqual(set(results), {'clip-01'})
        self.assertEqual(results['clip-01']['score'], 100)
        self.assertEqual(results['clip-01']['title'], 'Esto es BRUTAL')
        self.assertEqual(best, 'clip-01')


class EvaluateBatchTests(unittest.TestCase):
    def _make_batch(self, root: Path) -> Path:
        folder = root / 'lote-test'
        texts = {
            'clip-01': '1\n00:00:05,000 --> 00:00:06,000\neh\n',
            'clip-02': (
                '1\n00:00:00,200 --> 00:00:02,000\n¡No puede ser, bro!\n\n'
                '2\n00:00:02,100 --> 00:00:19,500\nNos destruyeron a todos con un helicóptero.\n'
            ),
        }
        clips = []
        for cid, srt in texts.items():
            (folder / cid).mkdir(parents=True)
            (folder / cid / 'captions.srt').write_text(srt, encoding='utf-8')
            clips.append({'id': cid, 'start': 0.0, 'end': 20.0, 'score': 0.4, 'display_title': cid})
        clips[0]['title_source'] = 'user'
        clips[0]['display_title'] = 'Mi título'
        save_json(folder / 'manifest.json', {'source': 'x.mp4', 'clips': clips})
        return folder

    def test_local_evaluation_ranks_and_keeps_user_title(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = self._make_batch(Path(directory))
            result = evaluate_batch(folder)
            self.assertEqual(result['best_clip'], 'clip-02')
            self.assertEqual(result['evaluated_with'], 'local')
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            by_id = {c['id']: c for c in manifest['clips']}
            self.assertEqual(by_id['clip-01']['display_title'], 'Mi título')
            self.assertIn('No puede ser', by_id['clip-02']['display_title'])

    def test_ai_evaluation_blends_scores_and_sets_titles(self):
        reply = json.dumps({'clips': [
            {'id': 'clip-01', 'title': 'Nada pasa', 'score': 95, 'reason': 'Sorprendente'},
            {'id': 'clip-02', 'title': 'Helicóptero nos arruina la partida', 'score': 40, 'reason': 'Ok'},
        ], 'best': 'clip-01'})
        with tempfile.TemporaryDirectory() as directory:
            folder = self._make_batch(Path(directory))
            with patch('clipforge.pipeline.auto_translate.invoke_translator_engine', return_value=reply) as mocked:
                result = evaluate_batch(folder, engine='claude')
            mocked.assert_called_once()
            self.assertIn('clip-02', mocked.call_args.args[1])
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            by_id = {c['id']: c for c in manifest['clips']}
            self.assertEqual(by_id['clip-01']['display_title'], 'Mi título')  # user title preserved
            self.assertEqual(by_id['clip-02']['display_title'], 'Helicóptero nos arruina la partida')
            self.assertEqual(by_id['clip-02']['title_source'], 'ai')
            self.assertEqual(by_id['clip-01']['quality']['ai_score'], 95)
            self.assertEqual(by_id['clip-01']['quality']['source'], 'ai+local')
            self.assertEqual(result['evaluated_with'], 'claude')
            self.assertEqual(manifest['best_clip'], result['ranking'][0]['id'])

    def test_ai_failure_is_warning_in_non_strict_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = self._make_batch(Path(directory))
            with patch('clipforge.pipeline.auto_translate.invoke_translator_engine', side_effect=RuntimeError('sin sesión')):
                result = evaluate_batch(folder, engine='codex', strict=False)
                self.assertIn('sin sesión', result['warning'])
                self.assertEqual(result['evaluated_with'], 'local')
                with self.assertRaises(RuntimeError):
                    evaluate_batch(folder, engine='codex', strict=True)


if __name__ == '__main__':
    unittest.main()
