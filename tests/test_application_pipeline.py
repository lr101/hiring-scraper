import json
import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch

from fastapi import HTTPException

from hiring_scraper.app import application_pipeline as pipeline
from hiring_scraper.app.application_pdf import RenderResult
from hiring_scraper.documents import CvDocument


def inputs(mode='full'):
    cv = CvDocument.model_validate({
        'language': 'en',
        'header': {'name': 'Dara Finch', 'contacts': [{'kind': 'location', 'text': 'Port Alder'}]},
        'headline': {'id': 'u-headline', 'text': 'Workshop designer'},
        'sections': [
            {'id': 's-profile', 'title': 'Profile', 'kind': 'profile'},
            {'id': 's-work', 'title': 'Experience', 'kind': 'experience', 'entries': [{
                'id': 'work-1', 'kind': 'work',
                'locked': {'company': 'Sedge Workshop', 'title': 'Designer',
                           'location': 'Port Alder', 'start': '2020', 'end': '2022'},
                'intro': [{'id': 'u-intro', 'text': 'Public workshops and exhibits.'}],
                'units': [{'id': 'u-base-bullet', 'text': 'Designed a workshop with librarians.'}],
            }]},
            {'id': 's-skills', 'title': 'Skills', 'kind': 'skills',
             'units': [{'id': 'u-skill', 'text': 'Facilitation'}]},
        ],
    })
    return pipeline.PreparationInput(
        cv.model_dump(), {}, {'title': 'Designer', 'company_name': 'Wren Museum',
                              'location': 'Loam Bay', 'description': 'Plan hands-on exhibits.'},
        'en', mode, date(2026, 3, 12),
    )


def cv_reply():
    return {
        'headline': {'text': 'Exhibit and workshop designer', 'sources': ['cv:headline']},
        'profile': [{'text': 'I design workshops with librarians.', 'sources': ['cv:work-1:b1']}],
        'entries': [{'entry_id': 'work-1', 'bullets': [
            {'text': 'Designed a public workshop with librarians.', 'lead_in': '', 'sources': ['cv:work-1:b1']},
        ]}],
        'skills': [{'section_id': 's-skills', 'unit_ids': ['u-skill'], 'hidden_ids': []}],
    }


def letter_reply():
    return {
        'subject': {'text': 'Designer'},
        'contact_name': '',
        'paragraphs': [
            {'role': 'motivation', 'sources': ['job'],
             'text': 'I am drawn to the museum because your exhibits ask visitors to make something for themselves. '
                     'I would like to design activities that make it easy for visitors to try a new material, '
                     'ask a question and take that interest home with them.'},
            {'role': 'experience', 'sources': ['cv:work-1:b1'],
             'text': 'At Sedge Workshop I worked with librarians to design a public workshop. '
                     'That experience is the reason I am interested in how exhibit teams set visitors up '
                     'to participate. A small decision about what to put on the table can make it easier '
                     'for people to join in at their own pace.'},
            {'role': 'bring', 'sources': ['job'],
             'text': 'I would like to learn how your staff test activities with visitors before an exhibit opens. '
                     'I would bring an eye for the practical details, from the way the first instruction '
                     'is worded to how people know they are welcome to explore the materials. '
                     'I would also ask the floor staff what they hear when people get stuck during an activity.'},
        ],
        'closing': {'text': 'I would welcome a conversation about your upcoming exhibits.'},
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.inp = inputs()
        self.checkpoints = {}
        self.published = []
        self.stages = []
        self.hooks = pipeline.StageHooks(self.checkpoints.get, self.checkpoints.__setitem__,
                                         lambda *args: self.published.append(args),
                                         self.stages.append, lambda: False)

    def run_with(self, cv=None, letter=None, review=None, render=None):
        replies = iter((cv if cv is not None else cv_reply(), letter or letter_reply(),
                        review or {'findings': [], 'patches': []}))
        return pipeline.prepare_documents(self.inp, lambda prompt, schema: next(replies), self.hooks, render)

    def test_stage_order_checkpoints_and_publication(self):
        expected = iter([('cv', cv_reply()), ('letter', letter_reply()),
                         ('review', {'findings': [], 'patches': []})])

        def generate(prompt, schema):
            stage, reply = next(expected)
            self.assertEqual(self.stages[-1], stage)
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(set(schema['required']), set(schema['properties']))
            return reply

        result = pipeline.prepare_documents(self.inp, generate, self.hooks)
        self.assertEqual(self.stages, ['cv', 'letter', 'review', 'finalize'])
        self.assertEqual([kind for kind, *_ in self.published], ['cv', 'letter', 'cv', 'letter'])
        self.assertEqual(self.published[-1][1], result.letter)
        self.assertEqual(set(self.checkpoints), {f'{s}:{pipeline.input_digest(self.inp)}'
                                                for s in ['cv', 'letter', 'review']})
        self.published.clear()
        resumed = pipeline.prepare_documents(self.inp, lambda *_: self.fail('generation on resume'), self.hooks)
        self.assertEqual((resumed.cv, resumed.letter, resumed.findings), (result.cv, result.letter, result.findings))
        self.assertEqual(self.published, [])

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_retries_retryable_and_invalid_response(self):
        self.inp.mode = 'fast'
        attempts = []

        def generate(prompt, schema):
            attempts.append(prompt)
            match len(attempts):
                case 1:
                    raise pipeline.GenerationError('busy', retryable=True)
                case 2:
                    return {'headline': 42}
                case 3:
                    self.assertIn('Your previous answer was rejected:', prompt)
                    return cv_reply()
                case _:
                    return letter_reply()

        result = pipeline.prepare_documents(self.inp, generate, self.hooks)
        self.assertEqual(result.telemetry['attempts'], {'cv': 3, 'letter': 1})
        self.assertEqual([p[0] for p in self.published], ['cv', 'letter'])

    def test_non_retryable_propagates(self):
        def generate(*_):
            raise pipeline.GenerationError('unauthorized')

        with self.assertRaises(pipeline.GenerationError) as error:
            pipeline.prepare_documents(self.inp, generate, self.hooks)
        self.assertEqual(error.exception.code, 'unauthorized')
        self.assertEqual(self.stages, ['cv'])
        self.assertEqual(self.published, [])

    def test_cv_checkpoint_survives_non_retryable_letter_failure(self):
        def generate(prompt, schema):
            if self.stages[-1] == 'cv':
                return cv_reply()
            raise pipeline.GenerationError('not_connected')

        with self.assertRaises(pipeline.GenerationError):
            pipeline.prepare_documents(self.inp, generate, self.hooks)
        self.assertEqual([p[0] for p in self.published], ['cv'])
        replies = iter([letter_reply(), {'findings': [], 'patches': []}])
        result = pipeline.prepare_documents(self.inp, lambda *_: next(replies), self.hooks)
        self.assertEqual(result.stages['cv']['state'], 'resumed')
        self.assertEqual([p[0] for p in self.published], ['cv', 'letter', 'cv', 'letter'])

    def test_locked_facts_and_unknown_ids(self):
        cv = cv_reply()
        cv['entries'].append({'entry_id': 'invented', 'bullets': []})
        cv['skills'].append({'section_id': 's-unknown', 'unit_ids': [], 'hidden_ids': []})
        cv['skills'][0]['unit_ids'].append('u-unknown')
        cv['headline']['sources'].append('cv:unknown')
        result = self.run_with(cv=cv)
        base = self.inp.base_cv
        entry = result.cv['sections'][1]['entries'][0]
        self.assertEqual(entry['locked'], base['sections'][1]['entries'][0]['locked'])
        self.assertEqual(entry['intro'], base['sections'][1]['entries'][0]['intro'])
        self.assertEqual(result.cv['header'], base['header'])
        self.assertEqual(entry['units'][0]['origin'], 'ai')
        self.assertEqual(result.cv['headline']['origin'], 'ai')
        self.assertEqual(result.letter['sender']['name'], base['header']['name'])
        self.assertEqual(result.letter['signature'], base['header']['name'])
        self.assertEqual(result.letter['recipient_lines'], ['Wren Museum', 'Loam Bay'])
        self.assertEqual(result.letter['place_date'], 'Port Alder, 12 March 2026')
        self.assertEqual(result.letter['closing']['origin'], 'source')
        self.assertEqual([f['code'] for f in result.findings].count('review_format'), 3)
        self.assertIn('review_grounding', [f['code'] for f in result.findings])

    def test_letter_uses_posting_contact_and_cleans_sender_and_recipient(self):
        self.inp.base_cv['header']['name'] = 'DARA VON FINCH'
        self.inp.job.update(location='Loam Bay Hybrid (Wren Museum GmbH)',
                            description='Questions? Contact Morgan Alder from our recruiting team.')
        reply = letter_reply()
        reply['contact_name'] = 'Morgan Alder'
        result = self.run_with(letter=reply)
        self.assertEqual(result.letter['recipient_lines'], ['Wren Museum'])
        self.assertEqual(result.letter['sender']['name'], 'Dara von Finch')
        self.assertEqual(result.letter['signature'], 'Dara von Finch')
        self.assertEqual(result.letter['salutation']['text'], 'Dear Morgan Alder,')

    def test_letter_validates_full_name_and_localizes_team_greeting(self):
        self.inp.job['description'] = 'Recruiting: Morgan. Collaborate with designer Avery Frost.'
        base = CvDocument.model_validate(self.inp.base_cv)
        for name, language, expected in [
            ('Morgan', 'en', 'Dear Wren Museum Recruiting Team,'),
            ('Morgan Frost', 'de', 'Sehr geehrtes Wren Museum-Recruiting-Team,'),
            ('Morgan Alder', 'de', 'Guten Tag Morgan Alder,'),
        ]:
            with self.subTest(name=name, language=language):
                if name == 'Morgan Alder':
                    self.inp.job['description'] = 'Kontakt: Morgan Alder'
                self.inp.language = language
                reply = {**letter_reply(), 'contact_name': name}
                letter = pipeline._letter_draft(reply, self.inp, base)
                self.assertEqual(letter.salutation.text, expected)

    def test_unknown_entry_does_not_replace_source_bullets(self):
        cv = cv_reply()
        cv['entries'][0]['entry_id'] = 'invented'
        with patch.object(pipeline, 'SLEEP', lambda _: None):
            result = self.run_with(cv=cv)
        self.assertEqual(result.cv['sections'][1], self.inp.base_cv['sections'][1])
        self.assertEqual([f['code'] for f in result.findings], ['review_format'])

    def test_auto_apply_requires_untouched_ai_unit_and_no_new_high_finding(self):
        patches = [
            ('cv', 'u-headline', 'Workshop and exhibit designer', 'rephrase'),
            ('cv', 'u-headline', 'Another headline', 'shorten'),
            ('cv', 'u-ai-work-1-b1', 'Designed 17 workshops.', 'shorten'),
            ('cv', 'u-intro', 'Different work.', 'rephrase'),
            ('letter', 'u-letter-p1', 'Different motivation.', 'content'),
            ('letter', 'u-missing', 'Unknown target.', 'rephrase'),
        ]
        result = self.run_with(review={'findings': [], 'patches': [
            dict(document=d, unit_id=u, text=t, kind=k) for d, u, t, k in patches
        ]})
        self.assertEqual(result.cv['headline']['text'], patches[0][2])
        self.assertEqual(result.cv['headline']['sources'], ['cv:headline'])
        self.assertEqual(len(result.telemetry['applied_patches']), 1)
        self.assertEqual(result.letter['paragraphs'][0]['text'], letter_reply()['paragraphs'][0]['text'])
        self.assertEqual(len(result.findings), 5)
        self.assertNotIn('unsupported_number', [f['code'] for f in result.findings])

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_letter_falls_back_to_base_cv_after_exhaustion(self):
        self.inp.mode = 'fast'

        def generate(prompt, schema):
            if self.stages[-1] == 'cv':
                raise pipeline.GenerationError('busy', retryable=True)
            self.assertIn('CV UNITS', prompt)
            self.assertIn('Designed a workshop with librarians.', prompt)
            return letter_reply()

        result = pipeline.prepare_documents(self.inp, generate, self.hooks)
        self.assertEqual(result.cv, self.inp.base_cv)
        self.assertEqual(result.telemetry['attempts']['cv'], 3)
        self.assertNotIn(f'cv:{pipeline.input_digest(self.inp)}', self.checkpoints)
        self.assertEqual([p[0] for p in self.published], ['cv', 'letter'])

    def test_render_findings_and_final_render_of_patched_unit(self):
        rendered = []

        def render(kind, doc):
            rendered.append(kind)
            return RenderResult(b'', 3, '', [{'code': 'glyphs', 'status': 'failed', 'detail': '1 glyph'}],
                                True, {'beyond_limit_units': ['u-headline'] if kind == 'cv' else []})

        result = self.run_with(render=render, review={'findings': [], 'patches': [{
            'document': 'cv', 'unit_id': 'u-headline', 'kind': 'shorten', 'text': 'Workshop designer',
        }]})
        self.assertEqual(rendered, ['cv', 'letter', 'cv'])
        self.assertEqual({f['code'] for f in result.findings}, {'extra_page', 'review_format', 'overflow'})
        self.assertEqual(result.telemetry['render']['cv']['pages'], 3)

    def test_personalization_is_separated_from_facts(self):
        self.inp.mode = 'fast'
        self.inp.profile['application_personalization'] = {
            'career_evidence': 'Led a seminar on clay glazes.',
            'voice_notes': 'Use direct verbs.',
            'wording_to_avoid': 'team player',
            'style_exemplars': [{'language': 'de', 'kind': 'letter', 'text': 'German-only sample'}] + [
                {'language': 'en', 'kind': 'letter', 'text': f'Example {n}'} for n in range(4)
            ],
        }
        variant = deepcopy(self.inp)
        variant.profile['application_personalization']['voice_notes'] = 'Other voice.'
        self.assertEqual(pipeline.input_digest(self.inp), pipeline.input_digest(variant))

        def generate(prompt, schema):
            self.assertIn('tone only', prompt)
            self.assertIn('team player', prompt)
            self.assertIn('Example 2', prompt)
            self.assertNotIn('Example 3', prompt)
            self.assertNotIn('German-only sample', prompt)
            self.assertEqual(json.loads(prompt.split('\nEVIDENCE\n')[1].split('\n\n')[0])['evidence:1'],
                             'Led a seminar on clay glazes.')
            return cv_reply() if self.stages[-1] == 'cv' else letter_reply()

        pipeline.prepare_documents(self.inp, generate, self.hooks)

    def test_revise_unit_returns_findings_and_preserves_input(self):
        before = deepcopy(self.inp.base_cv)
        revision = pipeline.revise_unit(before, 'cv', 'u-headline', 'Make it more specific.', self.inp,
                                        lambda *_: {'text': 'Designed 17 workshops', 'sources': ['cv:headline']})
        self.assertEqual(revision['unit_id'], 'u-headline')
        self.assertIn('unsupported_number', [f['code'] for f in revision['findings']])
        self.assertEqual(before, self.inp.base_cv)
        with self.assertRaises(KeyError):
            pipeline.revise_unit(before, 'cv', 'u-missing', '', self.inp, lambda *_: self.fail('unknown unit'))

    @patch('hiring_scraper.app.codex_runtime.generate')
    def test_codex_adapter_uses_runtime_defaults_and_maps_errors(self, runtime):
        cancelled = Mock(return_value=False)
        generate = pipeline.codex_generate(model='example-model', cancelled=cancelled)
        schema = {'type': 'object'}
        runtime.return_value = {}
        self.assertEqual(generate('Fictional prompt', schema), {})
        runtime.assert_called_once_with('Fictional prompt', schema, model='example-model', cancelled=cancelled)

        for status, retryable in [(401, False), (409, True), (429, False), (502, True), (503, False), (504, True)]:
            with self.subTest(status=status):
                runtime.side_effect = HTTPException(status, 'Provider detail')
                with self.assertRaises(pipeline.GenerationError) as error:
                    generate('Fictional prompt', schema)
                self.assertEqual(error.exception.retryable, retryable)
                self.assertNotIn('Provider detail', str(error.exception))
