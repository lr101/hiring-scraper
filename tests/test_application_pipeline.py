import json
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

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
        'profile': {'text': 'Workshop designer focused on public participation.', 'sources': ['cv:work-1:b1']},
        'entries': {'work-1': {
            'order': ['u-base-bullet'],
            'bullets': {
                'u-base-bullet': {
                    'action': 'rewrite',
                    'text': 'Designed a public workshop with librarians.',
                    'sources': ['cv:work-1:b1'],
                },
            },
        }},
        'skills': [{'section_id': 's-skills', 'unit_ids': ['u-skill'], 'hidden_ids': [], 'additions': []}],
    }


def draft_cv(reply, base, evidence=None, posting=''):
    _, response, slot_map = pipeline._cv_contract(base)
    return pipeline._cv_from_slots(reply, response, slot_map, base, evidence or {}, posting)


def letter_reply():
    return {
        'subject': {'text': 'Designer'},
        'contact_name': '',
        'paragraphs': [
            {'role': 'motivation', 'sources': ['job'],
             'text': 'I am drawn to the museum because your exhibits ask visitors to make something for themselves. '
                     'I am eager to design activities that make it easy for visitors to try a new material, '
                     'ask a question and take that interest home with them.'},
            {'role': 'experience', 'sources': ['cv:work-1:b1'],
             'text': 'At Sedge Workshop I worked with librarians to design a public workshop. '
                     'That experience is the reason I am interested in how exhibit teams set visitors up '
                     'to participate. A small decision about what to put on the table can make it easier '
                     'for people to join in at their own pace.'},
            {'role': 'bring', 'sources': ['job'],
             'text': 'I am curious how your staff test activities with visitors before an exhibit opens. '
                     'My focus is on the practical details, from the way the first instruction '
                     'is worded to how people know they are welcome to explore the materials. '
                     'I also ask the floor staff what they hear when people get stuck during an activity.'},
        ],
        'closing': {'text': 'I would welcome a conversation about your upcoming exhibits.'},
    }


def slot_letter_reply(schema, reply):
    """Translate the shared prose fixtures to the generated employer-slot schema."""
    defs = schema['$defs']
    fields = defs['LetterExamples']['properties']
    examples = dict.fromkeys(fields)
    fit = None
    for paragraph in reply['paragraphs'][1:]:
        if paragraph['role'] == 'bring':
            fit = {'text': paragraph['text'], 'sources': list(paragraph['sources'])}
            continue
        source = paragraph['sources'][0]
        for slot, field in fields.items():
            if any(source in anchor.get('enum', [anchor.get('const')])
                   for option in field['anyOf'] if '$ref' in option
                   for anchor in [defs[option['$ref'].rsplit('/', 1)[-1]]['properties']['anchor']]):
                examples[slot] = {
                    'anchor': source, 'priority': 1,
                    'sentences': [{'text': paragraph['text'], 'source': source}],
                }
                break
    return {'subject': reply['subject'], 'contact_name': reply['contact_name'],
            'motivation': {'text': reply['paragraphs'][0]['text'], 'sources': list(reply['paragraphs'][0]['sources'])},
            'examples': examples, 'fit': fit, 'closing': reply['closing']}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.inp = inputs()
        self.checkpoints = {}
        self.published = []
        self.stages = []
        self.hooks = pipeline.StageHooks(self.checkpoints.get, self.commit_stage,
                                         self.stages.append, lambda: False)

    def commit_stage(self, key, checkpoint, outputs):
        self.published.extend(outputs)
        if checkpoint is not None:
            self.checkpoints[key] = checkpoint

    def prepare(self, generate, hooks, render=None):
        def generate_for_schema(prompt, schema):
            reply = generate(prompt, schema)
            if 'LetterExamples' in schema.get('$defs', {}) and 'paragraphs' in reply:
                return slot_letter_reply(schema, reply)
            return reply

        return pipeline.prepare_documents(self.inp, generate_for_schema, hooks, render)

    def run_with(self, cv=None, letter=None, review=None, render=None):
        replies = iter((cv if cv is not None else cv_reply(), letter or letter_reply(),
                        review or {'findings': [], 'patches': []}))
        return self.prepare(lambda prompt, schema: next(replies), self.hooks, render)

    def test_input_digest_changes_with_effective_voice(self):
        initial = pipeline.input_digest(self.inp)
        for personalization in (
            {'voice_notes': 'Fictional preference for short sentences'},
            {'wording_to_avoid': ['rock star']},
            {'style_exemplars': [{'language': 'en', 'kind': 'letter', 'text': 'Fictional sample.'}]},
        ):
            with self.subTest(personalization=personalization):
                changed = replace(self.inp, profile={'application_personalization': personalization})
                self.assertNotEqual(pipeline.input_digest(changed), initial)

    def test_stage_order_checkpoints_and_publication(self):
        expected = iter([('cv', cv_reply()), ('letter', letter_reply()),
                         ('review', {'findings': [], 'patches': []})])

        def generate(prompt, schema):
            stage, reply = next(expected)
            self.assertEqual(self.stages[-1], stage)
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(set(schema['required']), set(schema['properties']))
            return reply

        result = self.prepare(generate, self.hooks)
        self.assertEqual(self.stages, ['cv', 'letter', 'review', 'finalize'])
        self.assertEqual([kind for kind, *_ in self.published], ['cv', 'letter', 'cv', 'letter'])
        self.assertEqual(self.published[-1][1], result.letter)
        self.assertEqual(set(self.checkpoints), {f'{s}:{pipeline.input_digest(self.inp)}'
                                                for s in ['cv', 'letter', 'review']})
        self.published.clear()
        resumed = self.prepare(lambda *_: self.fail('generation on resume'), self.hooks)
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

        result = self.prepare(generate, self.hooks)
        self.assertEqual(result.telemetry['attempts'], {'cv': 3, 'letter': 1})
        self.assertEqual([p[0] for p in self.published], ['cv', 'letter'])

    def test_non_retryable_propagates(self):
        def generate(*_):
            raise pipeline.GenerationError('unauthorized')

        with self.assertRaises(pipeline.GenerationError) as error:
            self.prepare(generate, self.hooks)
        self.assertEqual(error.exception.code, 'unauthorized')
        self.assertEqual(self.stages, ['cv'])
        self.assertEqual(self.published, [])

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_cv_timeout_propagates_without_publishing_the_base_cv(self):
        def generate(*_):
            raise pipeline.GenerationError('timeout', retryable=True)

        with self.assertRaises(pipeline.GenerationError) as error:
            self.prepare(generate, self.hooks)
        self.assertEqual(error.exception.code, 'timeout')
        self.assertEqual(self.stages, ['cv'])
        self.assertEqual(self.published, [])

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_review_timeout_retains_the_two_drafts_and_retries_only_review(self):
        def generate(*_):
            stage = self.stages[-1]
            if stage == 'review':
                raise pipeline.GenerationError('timeout', retryable=True)
            return cv_reply() if stage == 'cv' else letter_reply()

        with self.assertRaises(pipeline.GenerationError) as error:
            self.prepare(generate, self.hooks)
        self.assertEqual(error.exception.code, 'timeout')
        self.assertEqual([kind for kind, *_ in self.published], ['cv', 'letter'])

        def review(*_):
            self.assertEqual(self.stages[-1], 'review')
            return {'findings': [], 'patches': []}

        resumed = self.prepare(review, self.hooks)
        self.assertEqual(resumed.stages['cv']['state'], 'resumed')
        self.assertEqual(resumed.stages['letter']['state'], 'resumed')
        self.assertEqual(resumed.stages['review']['state'], 'completed')

    def test_cv_checkpoint_survives_non_retryable_letter_failure(self):
        def generate(prompt, schema):
            if self.stages[-1] == 'cv':
                return cv_reply()
            raise pipeline.GenerationError('not_connected')

        with self.assertRaises(pipeline.GenerationError):
            self.prepare(generate, self.hooks)
        self.assertEqual([p[0] for p in self.published], ['cv'])
        replies = iter([letter_reply(), {'findings': [], 'patches': []}])
        result = self.prepare(lambda *_: next(replies), self.hooks)
        self.assertEqual(result.stages['cv']['state'], 'resumed')
        self.assertEqual([p[0] for p in self.published], ['cv', 'letter', 'cv', 'letter'])

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_locked_facts_and_unknown_ids(self):
        cv = cv_reply()
        cv['skills'].append({'section_id': 's-unknown', 'unit_ids': [], 'hidden_ids': [], 'additions': []})
        cv['skills'][0]['unit_ids'].append('u-unknown')
        cv['headline']['sources'].append('cv:unknown')
        unknown_id = cv_reply()
        unknown_id['skills'].append({'section_id': 's-unknown', 'unit_ids': [], 'hidden_ids': [], 'additions': []})
        replies = iter([cv, unknown_id, cv_reply(), letter_reply(), {'findings': [], 'patches': []}])
        result = self.prepare(lambda *_: next(replies), self.hooks)
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
        self.assertEqual(result.telemetry['attempts']['cv'], 3)
        self.assertEqual([item['reasons'] for item in result.telemetry['attempt_failures']['cv']],
                         [['review_grounding'], ['cv_unknown_id']])

    def test_letter_uses_posting_contact_and_cleans_sender_and_recipient(self):
        self.inp.base_cv['header']['name'] = 'DARA VON FINCH'
        self.inp.job.update(company_name='Wren Museum (Wren Museum GmbH)',
                            location='Loam Bay Hybrid (Wren Museum GmbH)',
                            description='Questions? Contact Morgan Alder from our recruiting team.')
        reply = letter_reply()
        reply['contact_name'] = 'Morgan Alder'
        result = self.run_with(letter=reply)
        self.assertEqual(result.letter['recipient_lines'], ['Wren Museum', 'Loam Bay'])
        self.assertEqual(result.letter['sender']['name'], 'Dara von Finch')
        self.assertEqual(result.letter['signature'], 'Dara von Finch')
        self.assertEqual(result.letter['salutation']['text'], 'Dear Morgan Alder,')

    def test_new_documents_use_a4_and_greeting_strips_company_suffixes(self):
        self.inp.base_cv['page'] = 'letter'
        base = CvDocument.model_validate(self.inp.base_cv)
        for company, expected in [
            ('Wren Museum GmbH & Co. KG', 'Dear Wren Museum Hiring Team,'),
            ('Alder Labs GmbH', 'Dear Alder Labs Hiring Team,'),
            ('Bramble Tech AG', 'Dear Bramble Tech Hiring Team,'),
            ('Cedar Systems SE', 'Dear Cedar Systems Hiring Team,'),
            ('Dune Works Inc.', 'Dear Dune Works Hiring Team,'),
            ('Elm Studio B.V.', 'Dear Elm Studio Hiring Team,'),
            ('Fern Society e.V.', 'Dear Fern Society Hiring Team,'),
            ('Gorse Partners Ltd.', 'Dear Gorse Partners Hiring Team,'),
        ]:
            with self.subTest(company=company):
                self.inp.job['company_name'] = company
                letter = pipeline._letter_draft(letter_reply(), self.inp, base)
                self.assertEqual(letter.page, 'a4')
                self.assertEqual(letter.salutation.text, expected)
                self.assertNotIn('-Hiring', letter.salutation.text)
                self.assertNotIn('-Recruiting', letter.salutation.text)
        self.inp.job['company_name'] = 'Wren Museum GmbH & Co. KG'
        result = self.run_with()
        self.assertEqual(result.cv['page'], 'a4')
        self.assertEqual(result.letter['page'], 'a4')

    def test_duplicate_employer_safety_net_flags_legacy_style_paragraphs(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        duplicate = letter_reply()
        duplicate['paragraphs'][2]['sources'].append('cv:work-1:i1')
        letter = pipeline._letter_draft(duplicate, self.inp, base)
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertIn('letter_duplicate_employer', {issue.code for issue in issues})

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_advisory_style_reaches_review_without_retry_and_can_be_fixed(self):
        cv = cv_reply()
        cv['profile']['text'] = 'Workshop designer creating workshops. The workshops welcome visitors.'
        letter = letter_reply()
        letter['paragraphs'][2]['text'] += ' I would help.'
        original = letter['paragraphs'][2]['text']
        repeat = ' I would also listen.'
        letter['paragraphs'][2]['text'] += repeat

        def generate(prompt, schema):
            stage = self.stages[-1]
            if stage == 'cv':
                return cv
            if stage == 'letter':
                return letter
            findings = json.loads(prompt.split('\nFINDINGS\n')[1].split('\n\n')[0])
            self.assertTrue(any(f['severity'] == 'medium' and 'repeats a content word' in f['message']
                                for f in findings))
            self.assertTrue(any(f['severity'] == 'medium' and 'more than one conditional' in f['message']
                                for f in findings))
            return {'findings': [], 'patches': [
                {'document': 'letter', 'unit_id': 'u-letter-p3', 'kind': 'rephrase', 'text': original},
            ]}

        result = self.prepare(generate, self.hooks)
        self.assertEqual(result.telemetry['attempts'], {'cv': 1, 'letter': 1, 'review': 1})
        self.assertEqual(result.letter['paragraphs'][2]['text'], original)
        self.assertFalse(any('more than one conditional' in f['message'] for f in result.findings))

    def test_letter_checks_german_conditionals_and_english_contractions_vs_possessives(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        # Possessives are allowed; contractions like don't / let's / it's are rejected.
        possessive = letter_reply()
        possessive['paragraphs'][0]['text'] += " The museum's visitors explore new tools."
        letter = pipeline._letter_draft(possessive, self.inp, base)
        self.assertIn("museum's", letter.paragraphs[0].text)
        for bad in ("We don't rush visitors.", "It's a practical approach.", "Let's test the layout."):
            with self.subTest(bad=bad):
                draft = letter_reply()
                draft['paragraphs'][1]['text'] += ' ' + bad
                with self.assertRaises(pipeline._InvalidResponse):
                    pipeline._letter_draft(draft, self.inp, base)
        self.inp.language = 'de'
        reply = letter_reply()
        reply['paragraphs'][2]['text'] += (
            ' Ich würde prüfen, wie Besucher die Stationen nutzen, und wir würden das Feedback auswerten.'
        )
        letter = pipeline._letter_draft(reply, self.inp, base)
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertTrue({'letter_conditional_count'} <= {issue.code for issue in issues})


    def test_competencies_require_posting_keywords_and_cited_support(self):
        self.inp.job['description'] = 'Workshop design and tracking. Experience with Rust.'
        self.inp.base_cv['sections'][1]['entries'][0]['units'][0]['text'] = (
            'Designed a workshop with librarians and tracked visitor attendance.'
        )
        cv = cv_reply()
        cv['entries']['work-1']['bullets']['u-base-bullet']['text'] = (
            'Designed a public workshop with librarians and tracked visitor attendance.'
        )
        cv['skills'][0]['additions'] = [
            {'text': 'Workshop design', 'sources': ['cv:headline']},
            {'text': 'Attendance tracking', 'sources': ['cv:work-1:b1']},
            {'text': 'Rust', 'sources': ['cv:headline']},
            {'text': 'Rust', 'sources': ['job']},
        ]
        result = self.run_with(cv=cv)
        units = result.cv['sections'][2]['units']
        self.assertEqual([unit['text'] for unit in units], ['Workshop design', 'Attendance tracking', 'Facilitation'])
        self.assertEqual(units[0]['sources'], ['cv:headline'])
        self.assertEqual(units[1]['sources'], ['cv:work-1:b1'])
        self.assertEqual([f['code'] for f in result.findings], ['review_grounding', 'review_grounding'])

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_cv_retries_explicit_first_person_and_preserves_project_lead_in(self):
        self.inp.mode = 'fast'
        entry = self.inp.base_cv['sections'][1]['entries'][0]
        entry['units'][0]['lead_in'] = 'Exhibit:'
        cv = cv_reply()
        cv['entries']['work-1']['bullets']['u-base-bullet']['lead_in'] = 'Exhibit:'
        first_person = deepcopy(cv)
        first_person['profile']['text'] = "I'm a workshop designer."
        extra_bullet = deepcopy(cv)
        extra_bullet['entries']['work-1']['bullets']['u-extra'] = {
            'action': 'rewrite', 'text': 'Worked with visiting teachers.', 'sources': ['cv:work-1:b1'],
        }
        replies = iter([first_person, extra_bullet, cv, letter_reply()])
        result = self.prepare(lambda *_: next(replies), self.hooks)
        self.assertEqual(result.telemetry['attempts']['cv'], 3)
        self.assertEqual(result.cv['sections'][1]['entries'][0]['units'][0]['lead_in'], 'Exhibit:')

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_cv_headline_length_and_roman_numeral_profile_check(self):
        self.inp.mode = 'fast'
        long_headline = cv_reply()
        long_headline['headline']['text'] = 'Workshop and Public Exhibition Designer Focused on Interactive Museum Activities'
        roman_ok = cv_reply()
        roman_ok['profile']['text'] = 'Workshop designer experienced in Phase I exhibition concepts and facilitation.'
        prompts = []

        def generate(prompt, schema):
            prompts.append(prompt)
            return replies[len(prompts) - 1]

        replies = [long_headline, roman_ok, letter_reply()]
        result = self.prepare(generate, self.hooks)
        self.assertEqual(result.telemetry['attempts']['cv'], 2)
        self.assertIn('headline', prompts[1].lower())
        self.assertIn('Phase I', result.cv['sections'][0]['units'][0]['text'])

    def test_base_cv_keeps_distinct_bullets_after_labelled_projects(self):
        self.inp.base_cv['sections'][1]['entries'][0]['units'] = [
            {'id': 'u-p1', 'lead_in': 'Lantern Project:', 'text': 'Built tactile models for visitors',
             'sources': ['cv:work-1:b1']},
            {'id': 'u-p2', 'lead_in': '', 'text': 'Tested prototypes with school groups.',
             'sources': ['cv:work-1:b2']},
            {'id': 'u-p3', 'lead_in': 'Archive Map:', 'text': 'Catalogued workshop drawings.',
             'sources': ['cv:work-1:b3']},
        ]
        normalized = pipeline._new_cv(CvDocument.model_validate(self.inp.base_cv))
        units = normalized.sections[1].entries[0].units
        self.assertEqual([unit.id for unit in units], ['u-p1', 'u-p2', 'u-p3'])

    def test_rewritten_bullet_flags_omitted_distinctive_details(self):
        self.inp.base_cv['sections'][1]['entries'][0]['units'][0]['text'] = (
            'Designed a multilingual workshop with librarians and illustrated activity cards.'
        )
        result = self.run_with()
        self.assertTrue(any(f['code'] == 'review_coverage' and f['severity'] == 'info'
                            and f['unit_id'] == 'u-base-bullet' for f in result.findings))

    def test_review_cannot_reintroduce_letter_contractions_or_first_person_cv_profile(self):
        result = self.run_with(review={'findings': [], 'patches': [
            {'document': 'letter', 'unit_id': 'u-letter-closing', 'kind': 'rephrase',
             'text': 'I’d welcome a conversation about your upcoming exhibits.'},
            {'document': 'cv', 'unit_id': 'u-ai-profile-1', 'kind': 'rephrase',
             'text': 'I design workshops focused on public participation.'},
        ]})
        self.assertEqual(result.telemetry['applied_patches'], [])
        self.assertEqual(result.cv['sections'][0]['units'][0]['text'], cv_reply()['profile']['text'])
        self.assertEqual(result.letter['paragraphs'][-1]['text'], letter_reply()['closing']['text'])

    def test_cv_slot_contract_forbids_dropping_a_project_and_supports_keep_action(self):
        self.inp.mode = 'fast'
        self.inp.base_cv['sections'].append({
            'id': 's-education', 'kind': 'education', 'title': 'Education', 'entries': [{
                'id': 'study-1', 'kind': 'study',
                'locked': {'company': 'Alder Institute', 'title': 'Design', 'start': '2018', 'end': '2020'},
                'intro': [{'id': 'u-study-intro', 'text': 'Selected projects: Wayfinding & Lighting'}],
                'units': [
                    {'id': 'u-study-p1', 'lead_in': 'Wayfinding:', 'text': 'Mapped gallery signage with visitors.'},
                    {'id': 'u-study-p2', 'lead_in': 'Lighting:', 'text': 'Tested display lamps with curators.'},
                ],
            }],
        })
        base = CvDocument.model_validate(self.inp.base_cv)
        _, response, slot_map = pipeline._cv_contract(base)
        incomplete = cv_reply()
        incomplete['entries']['study-1'] = {
            'order': ['u-study-p1'],
            'bullets': {
                'u-study-p1': {
                    'action': 'rewrite', 'lead_in': 'Wayfinding:',
                    'text': 'Mapped gallery signage with museum visitors.', 'sources': ['cv:study-1:b1'],
                },
            },
        }
        with self.assertRaises(ValidationError):
            response.model_validate(incomplete)

        complete = deepcopy(incomplete)
        complete['entries']['study-1']['order'] = ['u-study-p2', 'u-study-p1']
        complete['entries']['study-1']['bullets']['u-study-p2'] = {'action': 'keep'}
        result = self.run_with(cv=complete)
        self.assertEqual(result.telemetry['attempts']['cv'], 1)
        self.assertEqual(result.telemetry['attempt_failures'], {})
        entry = result.cv['sections'][-1]['entries'][0]
        self.assertEqual([u['id'] for u in entry['units']], ['u-study-p2', 'u-study-p1'])
        kept, rewritten = entry['units']
        self.assertEqual(kept['origin'], 'source')
        self.assertEqual(kept['sources'], ['cv:study-1:b2'])
        self.assertEqual(kept['lead_in'], 'Lighting:')
        self.assertEqual(kept['text'], 'Tested display lamps with curators.')
        self.assertEqual(rewritten['origin'], 'ai')
        self.assertEqual(rewritten['lead_in'], 'Wayfinding:')

    def test_generated_cv_enables_available_portrait(self):
        self.inp.base_cv['photo'] = False
        self.assertTrue(self.run_with().cv['photo'])

    def test_cv_slot_order_omission_still_retains_every_base_bullet(self):
        units = self.inp.base_cv['sections'][1]['entries'][0]['units']
        units.append({'id': 'u-visit', 'text': 'Surveyed visiting teachers about exhibition access.'})
        base = CvDocument.model_validate(self.inp.base_cv)
        _, response, slot_map = pipeline._cv_contract(base)
        reply = cv_reply()
        reply['entries']['work-1']['order'] = ['u-visit']
        reply['entries']['work-1']['bullets']['u-visit'] = {'action': 'keep'}
        doc, _ = pipeline._cv_from_slots(reply, response, slot_map, base, pipeline.build_evidence(base), '')
        self.assertEqual([u.id for u in doc.sections[1].entries[0].units], ['u-visit', 'u-base-bullet'])
        self.assertEqual(doc.sections[1].entries[0].units[0].origin, 'source')
        self.assertEqual(doc.sections[1].entries[0].units[0].sources, ['cv:work-1:b2'])

    def test_cv_rewrite_always_prepends_base_source_and_preserves_project_lead_in(self):
        self.inp.base_cv['sections'][1]['entries'][0]['units'][0]['lead_in'] = 'Gallery Guide:'
        base = CvDocument.model_validate(self.inp.base_cv)
        _, response, slot_map = pipeline._cv_contract(base)
        reply = cv_reply()
        reply['entries']['work-1']['bullets']['u-base-bullet'] = {
            'action': 'rewrite',
            'lead_in': '',
            'text': 'Gallery Guide: Designed a public workshop with librarians.',
            'sources': [],
        }
        doc, _ = pipeline._cv_from_slots(reply, response, slot_map, base, pipeline.build_evidence(base), '')
        bullet = doc.sections[1].entries[0].units[0]
        self.assertEqual(bullet.sources, ['cv:work-1:b1'])
        self.assertEqual(bullet.lead_in, 'Gallery Guide:')
        self.assertEqual(bullet.text, 'Designed a public workshop with librarians.')

    def test_cv_keeps_skills_explicitly_named_in_visible_bullets(self):
        self.inp.mode = 'fast'
        bullets = self.inp.base_cv['sections'][1]['entries'][0]['units']
        bullets[0]['text'] += ' Used Python and C++.'
        bullets.append({'id': 'u-hidden', 'text': 'Sketched a sign in Figma.', 'hidden': True})
        skills = self.inp.base_cv['sections'][2]['units']
        skills.extend([
            {'id': 'u-python', 'text': 'Python (Basics)'},
            {'id': 'u-cpp', 'text': 'C++'},
            {'id': 'u-c', 'text': 'C'},
            {'id': 'u-figma', 'text': 'Figma'},
        ])
        reply = cv_reply()
        reply['entries']['work-1']['bullets']['u-base-bullet']['text'] = (
            'Designed a workshop with librarians using Python and C++.'
        )
        reply['skills'][0]['hidden_ids'] = ['u-python', 'u-cpp', 'u-c', 'u-figma']
        kept = self.run_with(cv=reply).cv['sections'][2]['units']
        by_id = {unit['id']: unit['hidden'] for unit in kept}
        self.assertFalse(by_id['u-python'])
        self.assertFalse(by_id['u-cpp'])
        self.assertTrue(by_id['u-c'])
        self.assertTrue(by_id['u-figma'])

    def test_study_degree_moves_to_the_header_without_losing_the_intro_details(self):
        self.inp.base_cv['sections'].append({
            'id': 's-education', 'kind': 'education', 'title': 'Education', 'entries': [
                {
                    'id': 'study-1', 'kind': 'study', 'label': 'HOCHSCHULE',
                    'locked': {'company': 'University', 'title': 'Alder Institute',
                               'start': '2018', 'end': '2020'},
                    'intro': [{'id': 'u-study-intro', 'text': 'M.Sc. Design – Focus on museum accessibility.'}],
                    'units': [
                        {'id': 'u-study-sub', 'text': 'Selected projects:'},
                        {'id': 'u-study-p1', 'lead_in': 'Wayfinding:', 'text': 'Mapped gallery signage with visitors.'},
                    ],
                },
                {
                    'id': 'study-2', 'kind': 'study', 'label': 'PRAKTIKUM',
                    'locked': {'company': 'INTERNSHIP', 'title': 'Applied Craft Fellowship',
                               'start': '2019', 'end': '2019'},
                    'intro': [],
                },
            ],
        })
        cv = cv_reply()
        cv['entries']['study-1'] = {
            'order': ['u-study-p1'],
            'bullets': {
                'u-study-p1': {
                    'action': 'rewrite', 'lead_in': 'Wayfinding:',
                    'text': 'Mapped gallery signage with visitors.', 'sources': ['cv:study-1:b2'],
                },
            },
        }
        result = self.run_with(cv=cv)
        entry1, entry2 = result.cv['sections'][-1]['entries']
        self.assertEqual(entry1['label'], '')
        self.assertEqual(entry1['locked']['company'], 'Alder Institute')
        self.assertEqual(entry1['locked']['title'], 'M.Sc. Design')
        self.assertEqual(entry1['intro'][0]['text'], 'Focus on museum accessibility.')
        self.assertEqual(entry1['intro'][1]['text'], 'Selected projects:')
        self.assertEqual(len(entry1['units']), 1)
        self.assertEqual(entry2['label'], '')
        self.assertEqual(entry2['locked']['company'], 'Applied Craft Fellowship')
        self.assertEqual(entry2['locked']['title'], 'Internship')

    def test_letter_keeps_city_with_region_and_omits_remote_only_location(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        for location, expected in [
            ('Loam Bay, CA-US, United States', ['Wren Museum', 'Loam Bay']),
            ('Loam Bay (Hybrid Remote)', ['Wren Museum', 'Loam Bay']),
            ('Fully Remote (Canada)', ['Wren Museum']),
            ('Remote, Loam Bay', ['Wren Museum']),
        ]:
            with self.subTest(location=location):
                self.inp.job['location'] = location
                letter = pipeline._letter_draft(letter_reply(), self.inp, base)
                self.assertEqual(letter.recipient_lines, expected)

    def test_letter_validates_full_name_and_localizes_team_greeting(self):
        self.inp.job['description'] = 'Recruiting: Morgan. Collaborate with designer Avery Frost.'
        base = CvDocument.model_validate(self.inp.base_cv)
        for name, language, expected in [
            ('Morgan', 'en', 'Dear Morgan,'),
            ('Morgan', 'de', 'Sehr geehrte Damen und Herren,'),
            ('Morgan Frost', 'de', 'Sehr geehrte Damen und Herren,'),
            ('', 'de', 'Sehr geehrte Damen und Herren,'),
            ('Morgan Alder', 'de', 'Guten Tag Morgan Alder,'),
            ('Morgan Alder', 'en', 'Dear Morgan Alder,'),
        ]:
            with self.subTest(name=name, language=language):
                if name == 'Morgan Alder':
                    self.inp.job['description'] = 'Kontakt: Morgan Alder'
                self.inp.language = language
                reply = {**letter_reply(), 'contact_name': name}
                letter = pipeline._letter_draft(reply, self.inp, base)
                self.assertEqual(letter.salutation.text, expected)

    def test_email_alone_does_not_supply_a_first_name(self):
        self.inp.job['description'] = 'Recruiting: Morgan@example.test'
        self.assertEqual(pipeline._salutation(self.inp, 'Wren Museum', 'Morgan'),
                         'Dear Wren Museum Hiring Team,')

    def test_letter_checks_company_back_references_and_repeated_phrases(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        reply = letter_reply()
        reply['paragraphs'][1]['text'] += ' Wren Museum would benefit from clear instructions for visitors.'
        reply['paragraphs'][2]['text'] += ' For Wren Museum, I could write clear instructions for visitors.'
        letter = pipeline._letter_draft(reply, self.inp, base)
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertTrue({'letter_company_mentions', 'letter_repeated_phrase'} <= {issue.code for issue in issues})


    def test_letter_checks_consecutive_openings_and_experience_length(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        reply = letter_reply()
        reply['paragraphs'][1]['text'] = (
            'I planned a workshop. I invited librarians. I demonstrated the first activity. '
            'The group then tried the activity independently, with sheets of paper and samples laid out '
            'on tables near the entrance to the public gallery. Everyone finished a small model to take home, '
            'where they could try the same activity with friends.'
        )
        letter = pipeline._letter_draft(reply, self.inp, base)
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertTrue({'letter_repeated_opening', 'letter_experience_length'} <= {issue.code for issue in issues})


    def test_german_letter_checks_repeated_fronted_first_person_openings(self):
        self.inp.language = 'de'
        base = CvDocument.model_validate(self.inp.base_cv)
        reply = letter_reply()
        reply['paragraphs'][1]['text'] += (
            ' Mit zwei Werkstätten plante ich eine Ausstellung. '
            'Im Anschluss erstellte ich Modelle. '
            'Für die Eröffnung lud ich die Nachbarschaft ein.'
        )
        letter = pipeline._letter_draft(reply, self.inp, base)
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertTrue({'letter_repeated_opening'} <= {issue.code for issue in issues})


    def test_cv_rejects_a_separate_projects_summary(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        reply = cv_reply()
        reply['profile'] = [
            reply['profile'],
            {'text': 'Selected projects: Library workshop.', 'sources': ['cv:work-1:b1']},
        ]
        with self.assertRaises(ValidationError):
            draft_cv(reply, base)

    def test_cv_checks_repeated_profile_terms_and_phrases_within_an_entry(self):
        self.inp.base_cv['sections'][1]['entries'][0]['units'].append(
            {'id': 'u-guide', 'text': 'Prepared a guide for visiting librarians.'},
        )
        base = CvDocument.model_validate(self.inp.base_cv)
        reply = cv_reply()
        reply['profile']['text'] = (
            'Designer focused on visitor workshops. The work connects workshops to exhibits. '
            'Each workshop starts with a concrete goal.'
        )
        reply['entries']['work-1'] = {
            'order': ['u-base-bullet', 'u-guide'],
            'bullets': {
                'u-base-bullet': {
                    'action': 'rewrite',
                    'text': 'Designed activities for library visitors after hours.',
                    'sources': ['cv:work-1:b1'],
                },
                'u-guide': {
                    'action': 'rewrite',
                    'text': 'Prepared a guide for library visitors after hours.',
                    'sources': ['cv:work-1:b2'],
                },
            },
        }
        doc, _ = draft_cv(reply, base)
        codes = {issue.code for issue in pipeline._cv_prose_issues(doc)}
        self.assertTrue({'cv_profile_repetition', 'cv_repeated_bullet_phrase'} <= codes)


    def test_cv_normalizes_legacy_internship_titles_and_preserves_tailored_qualified_skill_order(self):
        for language, label, qualifier in [('en', 'Internship', 'Basics'), ('de', 'Praktikum', 'Grundlagen')]:
            with self.subTest(language=language):
                data = deepcopy(self.inp.base_cv)
                data['language'] = language
                entry = data['sections'][1]['entries'][0]
                entry.update(kind='study', label=label)
                entry['locked']['title'] = entry['locked']['company']
                units = data['sections'][2]['units']
                units.insert(0, {'id': 'u-basics', 'lead_in': qualifier + ':', 'text': 'Clay & Glazes'})
                base = CvDocument.model_validate(data)
                normalized = pipeline._new_cv(base)
                self.assertEqual(normalized.sections[1].entries[0].locked.title, label)
                self.assertEqual([u.text for u in normalized.sections[2].units],
                                 ['Facilitation', f'Clay & Glazes ({qualifier})'])
                self.assertEqual(normalized.sections[2].units[-1].lead_in, '')
                reply = cv_reply()
                reply['skills'][0]['unit_ids'].insert(0, 'u-basics')
                drafted, _ = draft_cv(reply, base)
                self.assertEqual(drafted.sections[2].units[0].id, 'u-basics')
                self.assertEqual(drafted.sections[2].units[0].text, f'Clay & Glazes ({qualifier})')

    def test_cv_profile_run_on_and_cross_paragraph_letter_conditionals_flagged(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        reply = cv_reply()
        reply['profile']['text'] = (
            'Exhibition designer with practical studio experience coordinating community workshops, '
            'building clear prototype displays, testing accessible visitor guidance, documenting '
            'repeatable setup routines, and aligning local library partners across regional cultural '
            'programmes and seasonal family events.'
        )
        doc, _ = draft_cv(reply, base)
        codes = {issue.code for issue in pipeline._cv_prose_issues(doc)}
        self.assertIn('cv_profile_run_on', codes)

        letter_data = letter_reply()
        letter_data['paragraphs'][0]['text'] += ' I would value joining the programme.'
        letter_data['paragraphs'][2]['text'] += ' I would bring structured prototyping habits.'
        letter = pipeline._letter_draft(letter_data, self.inp, base)
        letter_issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertTrue(any(
            issue.code == 'letter_conditional_count' and issue.unit_id == 'u-letter-p3'
            for issue in letter_issues
        ))

    def test_grounding_finding_stays_visible_without_a_rewrite_or_retry(self):
        review = {'findings': [{
            'document': 'letter', 'unit_id': 'u-letter-p2', 'category': 'grounding',
            'severity': 'high', 'message': 'The claim exceeds its citations.', 'suggestion': '',
        }], 'patches': []}
        result = self.run_with(review=review)
        self.assertEqual(result.telemetry['attempts']['review'], 1)
        self.assertEqual(result.letter['paragraphs'][1]['text'], letter_reply()['paragraphs'][1]['text'])
        self.assertTrue(any(f['code'] == 'review_grounding' and f['severity'] == 'high'
                            for f in result.findings))

    def test_letter_receives_one_evidence_slot_per_employer_and_no_tailored_text(self):
        self.inp.mode = 'fast'
        entries = self.inp.base_cv['sections'][1]['entries']
        duplicate_employer = deepcopy(entries[0])
        duplicate_employer.update(id='work-2', units=[{
            'id': 'u-other-bullet', 'text': 'Set up tactile objects for gallery talks.',
        }])
        duplicate_employer['locked']['company'] = 'Sedge Workshop GmbH'
        duplicate_employer['intro'] = []
        other_employer = deepcopy(duplicate_employer)
        other_employer.update(id='work-3', units=[{
            'id': 'u-third-bullet', 'text': 'Catalogued tools in the public archive.',
        }])
        other_employer['locked']['company'] = 'Woodlark Archive'
        other_employer['locked']['location'] = 'North Cove'
        entries.extend([duplicate_employer, other_employer])
        self.inp.profile['application_personalization'] = {
            'career_evidence': '\n'.join([
                'At Woodlark Archive, grouped donated materials by use.',
                'At Sedge Workshop, supported weekend programs.',
                'Helped catalogue exhibit labels.',
            ]),
        }
        cv = cv_reply()
        cv['entries']['work-1']['bullets']['u-base-bullet']['text'] = (
            'Designed library activities for hands-on workshops.'
        )
        cv['entries']['work-2'] = {
            'order': ['u-other-bullet'],
            'bullets': {'u-other-bullet': {'action': 'keep'}},
        }
        cv['entries']['work-3'] = {
            'order': ['u-third-bullet'],
            'bullets': {'u-third-bullet': {'action': 'keep'}},
        }

        def generate(prompt, schema):
            if self.stages[-1] == 'cv':
                return cv
            props = schema['$defs']['LetterExamples']['properties']
            self.assertEqual(set(props), {'work-1', 'work-3'})
            self.assertNotIn(cv['entries']['work-1']['bullets']['u-base-bullet']['text'], prompt)
            self.assertIn(duplicate_employer['units'][0]['text'], prompt)
            self.assertIn(other_employer['units'][0]['text'], prompt)
            groups = json.loads(prompt.split('\nEXAMPLE SLOTS\n')[1])
            self.assertEqual(set(groups['work-3']['supplemental']), {'evidence:1'})
            self.assertEqual(groups['work-3']['entries']['work-3']['location'], 'North Cove')
            self.assertEqual(groups['work-1']['supplemental'], {})
            self.assertNotIn('Helped catalogue exhibit labels.', prompt)
            return {
                'subject': {'text': 'Designer'}, 'contact_name': '',
                'motivation': {'text': letter_reply()['paragraphs'][0]['text'], 'sources': ['job']},
                'examples': {'work-1': {
                    'anchor': 'cv:work-1:b1', 'priority': 1,
                    'sentences': [{'text': letter_reply()['paragraphs'][1]['text'], 'source': 'cv:work-1:b1'}],
                }, 'work-3': None},
                'fit': {'text': letter_reply()['paragraphs'][2]['text'], 'sources': ['job']},
                'closing': letter_reply()['closing'],
            }

        result = self.prepare(generate, self.hooks)
        self.assertEqual(result.telemetry['attempts'], {'cv': 1, 'letter': 1})
        self.assertEqual(result.telemetry['attempt_failures'], {})
        self.assertEqual(result.letter['paragraphs'][1]['sources'], ['cv:work-1:b1', 'job'])

    def test_letter_response_schema_rejects_cross_entry_citations(self):
        entries = self.inp.base_cv['sections'][1]['entries']
        other = deepcopy(entries[0])
        other.update(id='work-2', intro=[], units=[{
            'id': 'u-archive-bullet', 'text': 'Logged donated books.',
        }])
        other['locked']['company'] = 'Sedge Workshop GmbH'
        entries.append(other)
        base = CvDocument.model_validate(self.inp.base_cv)
        evidence = pipeline.build_evidence(base, self.inp.profile)
        groups, response = pipeline._letter_contract(base, evidence)
        self.assertEqual(list(groups), ['work-1'])
        output = slot_letter_reply(response.model_json_schema(), letter_reply())
        response.model_validate(output)
        output['examples']['work-1']['sentences'][0]['source'] = 'cv:work-2:b1'
        with self.assertRaises(ValidationError):
            response.model_validate(output)

    def test_letter_flags_repeated_content_stems_and_repeated_bridge_verbs(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        for language, first, second, code in (
            ('en', 'I converted sketches into tactile prototypes.',
             'Converting the sketches to tactile prototypes would suit this role.', 'letter_repeated_motif'),
            ('de', 'Daran möchte ich anknüpfen.', 'Im Museum knüpfe ich an diese Arbeit an.', 'letter_repeated_bridge'),
        ):
            with self.subTest(language=language):
                self.inp.language = language
                letter = pipeline._letter_draft(letter_reply(), self.inp, base)
                letter.paragraphs[0].text = first
                letter.paragraphs[1].text = second
                issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
                self.assertIn((code, 'u-letter-p2'), {(issue.code, issue.unit_id) for issue in issues})

    def test_profile_flags_adjacent_repeated_experience(self):
        self.assertTrue(pipeline.editorial.repeated_profile_term(
            'Designer with experience in gallery exhibits. Workshop experience includes tactile displays.'
        ))
        self.assertFalse(pipeline.editorial.repeated_profile_term(
            'Designer with experience in gallery exhibits. Workshop planning includes tactile displays.'
        ))

    def test_en_subject_uses_posting_title(self):
        result = self.run_with()
        self.assertEqual(result.letter['subject']['text'], 'Application for Designer')

    def test_cv_flags_dropped_alphanumeric_source_detail(self):
        unit = pipeline.Unit(id='u-project', text='Built CAD prototypes.', sources=['cv:study-1:b1'])
        missing = pipeline.editorial.omitted_critical_details(
            unit, {'cv:study-1:b1': 'Built 3D CAD prototypes.'}, 'study-1',
        )
        self.assertEqual(missing, {'3d'})

    def test_review_patch_rejects_newly_introduced_repeated_letter_ending(self):
        # Paragraph 1 ends with 'I am eager to ...'; patching paragraph 3 to end with the same 3-word opener is rejected.
        patched_p3 = (
            'I am curious how your staff test activities with visitors before an exhibit opens. '
            'I am eager to bring practical setup habits to your gallery floor.'
        )
        result = self.run_with(review={
            'findings': [],
            'patches': [{'document': 'letter', 'unit_id': 'u-letter-p3', 'kind': 'rephrase', 'text': patched_p3}],
        })
        self.assertEqual(result.telemetry['applied_patches'], [])
        self.assertEqual(result.letter['paragraphs'][2]['text'], letter_reply()['paragraphs'][2]['text'])

    def test_unknown_entry_or_dropped_bullet_is_rejected_by_cv_schema(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        _, response, _ = pipeline._cv_contract(base)

        unknown_entry = cv_reply()
        unknown_entry['entries']['invented'] = unknown_entry['entries'].pop('work-1')
        with self.assertRaises(ValidationError):
            response.model_validate(unknown_entry)

        dropped_bullet = cv_reply()
        dropped_bullet['entries']['work-1']['bullets'] = {}
        with self.assertRaises(ValidationError):
            response.model_validate(dropped_bullet)

    def test_auto_apply_requires_untouched_ai_unit_and_no_new_high_finding(self):
        patches = [
            ('cv', 'u-headline', 'Workshop and exhibit designer', 'rephrase'),
            ('cv', 'u-headline', 'Another headline', 'shorten'),
            ('cv', 'u-base-bullet', 'Designed 17 workshops.', 'shorten'),
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
    def test_render_findings_and_final_render_of_patched_unit(self):
        rendered = []

        def render(kind, doc):
            rendered.append(kind)
            return RenderResult(b'', 3, '', [{'code': 'glyphs', 'status': 'failed', 'detail': '1 glyph'}],
                                True, {'beyond_limit_units': ['u-headline'] if kind == 'cv' else []})

        review = {'findings': [], 'patches': [{
            'document': 'cv', 'unit_id': 'u-headline', 'kind': 'shorten', 'text': 'Workshop designer',
        }]}
        replies = iter([cv_reply(), cv_reply(), cv_reply(), letter_reply(), review])
        result = self.prepare(lambda *_: next(replies), self.hooks, render)
        self.assertEqual(rendered, ['cv', 'cv', 'cv', 'letter', 'cv'])
        self.assertEqual({f['code'] for f in result.findings}, {'extra_page', 'review_format', 'overflow'})
        self.assertEqual(result.telemetry['render']['cv']['pages'], 3)

    def test_review_cannot_push_a_fitting_cv_over_two_pages(self):
        def render(kind, doc):
            pages = 1
            if kind == 'cv':
                pages = 3 if doc['headline']['text'] == 'Public exhibit and workshop designer' else 2
            return RenderResult(b'', pages, '', [], pages > 2)

        result = self.run_with(render=render, review={'findings': [], 'patches': [{
            'document': 'cv', 'unit_id': 'u-headline', 'kind': 'rephrase',
            'text': 'Public exhibit and workshop designer',
        }]})
        self.assertEqual(result.telemetry['applied_patches'], [])
        self.assertEqual(result.telemetry['render']['cv']['pages'], 2)

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_cv_retries_overflow_without_dropping_bullets(self):
        self.inp.mode = 'fast'
        shorter = cv_reply()
        shorter['entries']['work-1']['bullets']['u-base-bullet'] = {'action': 'keep'}
        replies = iter([cv_reply(), shorter, letter_reply()])

        def render(kind, doc):
            pages = 1
            if kind == 'cv':
                bullet = doc['sections'][1]['entries'][0]['units'][0]
                pages = 3 if 'public' in bullet['text'] else 2
            return RenderResult(b'', pages, '', [], pages > 2)

        result = self.prepare(lambda *_: next(replies), self.hooks, render)
        self.assertEqual(result.telemetry['attempts']['cv'], 2)
        self.assertEqual(result.telemetry['render']['cv']['pages'], 2)
        self.assertEqual(result.cv['sections'][1]['entries'][0]['units'][0]['sources'], ['cv:work-1:b1'])
        self.assertEqual(result.cv['sections'][1]['entries'][0]['units'][0]['origin'], 'source')

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
        self.assertNotEqual(pipeline.input_digest(self.inp), pipeline.input_digest(variant))

        def generate(prompt, schema):
            self.assertIn('tone only', prompt)
            self.assertIn('team player', prompt)
            self.assertIn('Example 2', prompt)
            self.assertNotIn('Example 3', prompt)
            self.assertNotIn('German-only sample', prompt)
            if self.stages[-1] == 'cv':
                self.assertEqual(json.loads(prompt.split('\nEVIDENCE\n')[1].split('\n\n')[0])['evidence:1'],
                                 'Led a seminar on clay glazes.')
            else:
                self.assertNotIn('Led a seminar on clay glazes.', prompt)
            return cv_reply() if self.stages[-1] == 'cv' else letter_reply()

        self.prepare(generate, self.hooks)

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

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_letter_ngram_ignores_proper_names_quoted_titles_and_stopword_runs(self):
        self.inp.mode = 'fast'
        long_org = 'Northern Harbor Botanical Conservatory and Research Institute'
        quoted = '"Handbook of Inclusive Tactile Museum Guideways for Families"'
        self.inp.base_cv['sections'][1]['entries'][0]['locked']['company'] = long_org
        cv = cv_reply()
        cv['entries']['work-1']['bullets']['u-base-bullet']['text'] = (
            f'Co-authored {quoted} at {long_org}, guided stewardship of a city-wide catalog and archive initiative, '
            'brought curators, archivists, printers, and docents together to test gallery guides, '
            'and worked with the team on the design of the public gallery.'
        )
        ok_letter = letter_reply()
        ok_letter['paragraphs'][1]['text'] = (
            f'While contributing to {quoted} at {long_org}, I handled stewardship of a city-wide catalog and archive initiative. '
            'In that effort I brought curators, archivists, printers, and docents together to review hands-on prompts. '
            'Later I worked with the team on the design of the visitor tables to see which prompts invited curiosity. '
            'That work directly informs how I structure clear interactive activities for museum guests.'
        )
        result = self.run_with(cv=cv, letter=ok_letter)
        self.assertEqual(result.telemetry['attempts']['letter'], 1)
        self.assertEqual(result.telemetry['attempt_failures'], {})

        # The safety net still reports coincidental overlap with the independently generated CV.
        self.checkpoints.clear()
        cv2 = cv_reply()
        copied_clause = 'built modular tactile exhibition kits with visiting schoolteachers and curators'
        cv2['entries']['work-1']['bullets']['u-base-bullet']['text'] = f'Successfully {copied_clause}.'
        bad_letter = letter_reply()
        bad_letter['paragraphs'][1]['text'] += f' I {copied_clause}.'

        result2 = self.run_with(cv=cv2, letter=bad_letter)
        self.assertEqual(result2.telemetry['attempts']['letter'], 1)
        self.assertEqual(result2.telemetry['attempt_failures'], {})
        self.assertTrue(any(f['document'] == 'letter' and 'closely repeats' in f['message']
                            for f in result2.findings))

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_letter_fallback_publishes_best_attempt_after_prose_or_timeout_exhaustion(self):
        best_attempt = letter_reply()
        best_attempt['paragraphs'][1]['text'] += " I'd adapt the setup."
        worse_attempt = letter_reply()
        worse_attempt['paragraphs'][1]['text'] += " I'd adapt it, I would test it, and I would refine it."
        fixed_text = letter_reply()['paragraphs'][1]['text'] + ' I would adapt the setup.'
        calls = {'letter': 0}

        def generate(prompt, schema):
            stage = self.stages[-1]
            if stage == 'cv':
                return cv_reply()
            if stage == 'letter':
                calls['letter'] += 1
                if calls['letter'] == 1:
                    return best_attempt
                if calls['letter'] == 2:
                    return worse_attempt
                raise pipeline.GenerationError('timeout', retryable=True)
            return {
                'findings': [],
                'patches': [{'document': 'letter', 'unit_id': 'u-letter-p2', 'kind': 'rephrase', 'text': fixed_text}],
            }

        result = self.prepare(generate, self.hooks)
        self.assertEqual(result.stages['letter']['state'], 'fallback')
        self.assertEqual(result.telemetry['attempts']['letter'], 3)
        self.assertEqual(result.telemetry['attempt_failures']['letter'], [
            {'attempt': 1, 'code': 'invalid_response', 'reasons': ['letter_contraction']},
            {'attempt': 2, 'code': 'invalid_response', 'reasons': ['letter_contraction']},
            {'attempt': 3, 'code': 'timeout', 'reasons': ['timeout']},
        ])
        # Review stage was allowed to fix the fallback prose issue and clear the prose finding.
        self.assertEqual(result.letter['paragraphs'][1]['text'], fixed_text)
        self.assertEqual([f for f in result.findings if f['document'] == 'letter' and f['code'] == 'review_style'], [])
        # Telemetry never contains document text.
        self.assertNotIn('adapt the setup', json.dumps(result.telemetry))

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_cv_prose_fallback_publishes_best_attempt_and_letter_uses_original_evidence(self):
        self.inp.mode = 'fast'
        long_cv = cv_reply()
        long_cv['headline']['text'] = 'Exhibit, Workshop and Participatory Museum Activity Designer for Public Spaces'
        long_cv['entries']['work-1']['bullets']['u-base-bullet']['text'] = (
            'Tailored bullet that should not replace base CV units on fallback.'
        )
        letter_prompts = []

        def generate(prompt, schema):
            if self.stages[-1] == 'cv':
                return long_cv
            letter_prompts.append(prompt)
            return letter_reply()

        result = self.prepare(generate, self.hooks)
        self.assertEqual(result.stages['cv']['state'], 'fallback')
        self.assertEqual(result.cv['headline']['text'], long_cv['headline']['text'])
        self.assertTrue(any(f['code'] == 'review_format' and f['unit_id'] == 'u-headline' for f in result.findings))
        self.assertEqual(len(result.telemetry['attempt_failures']['cv']), 3)
        self.assertTrue(all(
            item['reasons'] == ['cv_headline_too_long'] for item in result.telemetry['attempt_failures']['cv']
        ))
        self.assertIn('Designed a workshop with librarians.', letter_prompts[0])
        self.assertNotIn('Tailored bullet that should not replace', letter_prompts[0])

    def test_prompt_omits_job_url(self):
        self.inp.mode = 'fast'
        self.inp.job['url'] = 'https://jobs.example.test/designer-j9876.html'
        seen_prompts = []

        def generate(prompt, schema):
            seen_prompts.append(prompt)
            return cv_reply() if self.stages[-1] == 'cv' else letter_reply()

        self.prepare(generate, self.hooks)
        self.assertTrue(seen_prompts)
        for prompt in seen_prompts:
            self.assertNotIn('j9876', prompt)
            self.assertNotIn('jobs.example.test', prompt)

    def test_profile_voice_rejects_imperatives_and_verb_first_fragments(self):
        from hiring_scraper.documents import editorial

        self.assertIsNotNone(editorial.profile_voice_issue('Combine visitor interviews with prototyping.', 'en'))
        self.assertIsNotNone(editorial.profile_voice_issue(
            'Workshop designer with museum roots. Turn feedback into clear plans.', 'en',
        ))
        self.assertIsNone(editorial.profile_voice_issue(
            'Exhibition designer with a background in civic workshops. Strong experience across visitor research.', 'en',
        ))
        self.assertIsNone(editorial.profile_voice_issue(
            'Design engineer focused on participatory museum programs.', 'en',
        ))
        self.assertIsNone(editorial.profile_voice_issue(
            'Selected projects: Wayfinding prototype and library workshop series.', 'en',
        ))
        self.assertIsNotNone(editorial.profile_voice_issue('Bringe Erfahrung in der Werkstattplanung mit.', 'de'))
        self.assertIsNotNone(editorial.profile_voice_issue(
            'Ausstellungsplanerin mit Schwerpunkt Beteiligung. Verfüge über Praxis in Prototypen.', 'de',
        ))
        self.assertIsNone(editorial.profile_voice_issue(
            'Ausstellungsplanerin mit Erfahrung in Besucherforschung und Prototypenbau.', 'de',
        ))
        self.assertIsNone(editorial.profile_voice_issue(
            'Relevante Projekte: Orientierungssystem und Werkstattreihe.', 'de',
        ))

    def test_repeated_opening_verbs_anchor_a_finding_to_an_ai_bullet(self):
        self.inp.base_cv['sections'][1]['entries'][0]['units'] = [
            {'id': f'u-w1-b{i}', 'text': f'Built museum exhibit guide {i} with librarians.'}
            for i in range(1, 5)
        ]
        bad_cv = cv_reply()
        bad_cv['entries']['work-1'] = {
            'order': [f'u-w1-b{i}' for i in range(1, 5)],
            'bullets': {
                f'u-w1-b{i}': {
                    'action': 'rewrite',
                    'text': f'Coordinated museum exhibit guide {i} with librarians.',
                    'sources': [f'cv:work-1:b{i}'],
                }
                for i in range(1, 5)
            },
        }
        bad_cv['entries']['work-1']['bullets']['u-w1-b4']['text'] = (
            'Designed museum exhibit guide 4 with librarians.'
        )
        base = CvDocument.model_validate(self.inp.base_cv)
        evidence = pipeline.build_evidence(base, self.inp.profile)
        drafted, _ = draft_cv(bad_cv, base, evidence, self.inp.job['description'])
        findings = [issue for issue in pipeline._cv_prose_issues(drafted) if issue.code == 'cv_repeated_opening_verb']
        self.assertEqual([issue.unit_id for issue in findings], ['u-w1-b3'])

        # Distinct-verb bullets retain all four base sources.
        good_cv = cv_reply()
        good_cv['entries']['work-1'] = {
            'order': [f'u-w1-b{i}' for i in range(1, 5)],
            'bullets': {
                'u-w1-b1': {'action': 'rewrite', 'text': 'Coordinated museum exhibit guide 1 with librarians.', 'sources': ['cv:work-1:b1']},
                'u-w1-b2': {'action': 'rewrite', 'text': 'Structured museum exhibit guide 2 with librarians.', 'sources': ['cv:work-1:b2']},
                'u-w1-b3': {'action': 'rewrite', 'text': 'Mapped museum exhibit guide 3 with librarians.', 'sources': ['cv:work-1:b3']},
                'u-w1-b4': {'action': 'rewrite', 'text': 'Designed museum exhibit guide 4 with librarians.', 'sources': ['cv:work-1:b4']},
            },
        }
        doc, _ = draft_cv(good_cv, base, evidence, self.inp.job['description'])
        self.assertEqual(len(doc.sections[1].entries[0].units), 4)

    def test_german_compound_hyphen_and_awkward_verb_checks(self):
        from hiring_scraper.documents import editorial

        self.assertTrue(editorial.missing_german_compound_hyphens(
            'Analysierte Markt und Nutzerbedürfnisse für neue Ausstellungsformate.',
        ))
        self.assertFalse(editorial.missing_german_compound_hyphens(
            'Analysierte Markt- und Nutzerbedürfnisse für neue Ausstellungsformate.',
        ))
        self.assertTrue(editorial.missing_german_compound_hyphens(
            'Stimmte Entwicklungs und Fertigungsteams ab.',
        ))
        self.assertFalse(editorial.missing_german_compound_hyphens(
            'Stimmte Entwicklungs- und Fertigungsteams ab.',
        ))
        self.assertFalse(editorial.missing_german_compound_hyphens(
            'Arbeitete mit Designern und Besuchergruppen im Museum.',
        ))
        self.assertTrue(editorial.has_awkward_de_verb_phrase(
            'Leitete die Koordination zwischen Werkstatt und Kuratorium.',
        ))
        self.assertFalse(editorial.has_awkward_de_verb_phrase(
            'Koordinierte die Zusammenarbeit zwischen Werkstatt und Kuratorium.',
        ))

    @patch.object(pipeline, 'SLEEP', lambda _: None)
    def test_new_qualifiers_and_unsupported_numbers_require_corrected_drafts(self):
        self.inp.mode = 'fast'
        cv = cv_reply()
        cv['entries']['work-1']['bullets']['u-base-bullet']['text'] = (
            'Designed a technical software workshop with librarians for public exhibits.'
        )
        letter = letter_reply()
        letter['paragraphs'][1]['text'] += ' The workshop attracted 860 visitors.'
        replies = iter([cv, cv_reply(), letter, letter_reply()])
        result = self.prepare(lambda *_: next(replies), self.hooks)
        self.assertEqual(result.telemetry['attempts'], {'cv': 2, 'letter': 2})
        self.assertEqual(result.telemetry['attempt_failures'], {
            'cv': [{'attempt': 1, 'code': 'invalid_response', 'reasons': ['unsupported_qualifier']}],
            'letter': [{'attempt': 1, 'code': 'invalid_response', 'reasons': ['unsupported_number']}],
        })
        self.assertNotIn('860', self.published[1][1]['paragraphs'][1]['text'])


    def test_competency_supports_german_compounds_and_rejects_long_phrases(self):
        from hiring_scraper.documents import editorial

        self.assertTrue(editorial.supports_competency(
            'Marktrecherche',
            ['Führte Recherche im Markt für modulare Systeme durch.'],
            'Gesucht wird Erfahrung in Marktrecherche und Produktentwicklung.',
        ))
        self.assertTrue(editorial.supports_competency(
            'Kostenkalkulation',
            ['Erstellte Kalkulation der Kosten für Baugruppen.'],
            'Verantwortung für Kostenkalkulation und Lieferanten.',
        ))
        self.assertFalse(editorial.supports_competency(
            'Strukturierte Analyse von Markt und Anforderungen im Team',
            ['Analysierte Markt und Anforderungen im Team.'],
            'Analyse von Markt und Anforderungen',
        ))

        base = CvDocument.model_validate(self.inp.base_cv)
        evidence = pipeline.build_evidence(base, self.inp.profile)
        long_tag_cv = cv_reply()
        long_tag_cv['skills'][0]['additions'] = [{
            'text': 'Participatory public museum workshop facilitation planning',
            'sources': ['cv:work-1:b1'],
        }]
        with self.assertRaises(pipeline._InvalidResponse) as ctx:
            draft_cv(long_tag_cv, base, evidence, self.inp.job['description'])
        self.assertIn('cv_competency_too_long', ctx.exception.codes)

    def test_letter_flags_repeated_endings_and_normalizes_german_salutation_case(self):
        from hiring_scraper.documents import editorial

        self.assertEqual(
            editorial.normalize_de_salutation_opening(
                'Sehr geehrte Damen und Herren,',
                'Mit meiner Erfahrung im Ausstellungsbau unterstütze ich Ihr Team.',
            ),
            'mit meiner Erfahrung im Ausstellungsbau unterstütze ich Ihr Team.',
        )
        self.assertEqual(
            editorial.normalize_de_salutation_opening(
                'Sehr geehrte Damen und Herren,',
                'Ausstellungen für die Öffentlichkeit reizen mich besonders.',
            ),
            'Ausstellungen für die Öffentlichkeit reizen mich besonders.',
        )
        self.assertEqual(
            editorial.normalize_de_salutation_opening(
                'Guten Tag Morgan Alder,',
                'Sie suchen eine Planerin für modulare Werkstätten.',
            ),
            'Sie suchen eine Planerin für modulare Werkstätten.',
        )

        base = CvDocument.model_validate(self.inp.base_cv)
        reply = letter_reply()
        reply['paragraphs'][1]['text'] += ' This experience prepares me for your program team.'
        reply['paragraphs'][2]['text'] += ' This experience supports your launch schedule.'
        letter = pipeline._letter_draft(reply, self.inp, base)
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertTrue({'letter_repeated_ending'} <= {issue.code for issue in issues})


    def test_third_person_cv_pronouns_rejected_and_distinct_labelled_reordering_allowed(self):
        from hiring_scraper.documents import editorial

        self.assertTrue(editorial.has_third_person_applicant(
            'Präzisierte Anforderungen aus der Prüfung, die sie koordinierte.', 'de',
        ))
        self.assertFalse(editorial.has_third_person_applicant(
            'Koordinierte Konzeptprüfungen und präzisierte Anforderungen.', 'de',
        ))
        self.assertTrue(editorial.has_third_person_applicant(
            'Built prototypes that she tested with gallery visitors.', 'en',
        ))
        self.assertFalse(editorial.has_third_person_applicant(
            'Built prototypes and tested them with gallery visitors.', 'en',
        ))

        # Reordering a labelled project bullet ahead of a distinct unlabelled base bullet is NOT a split project.
        self.inp.base_cv['sections'][1]['entries'][0]['units'] = [
            {'id': 'u-w1-b1', 'lead_in': '', 'text': 'Participated in an annual gallery seminar.'},
            {'id': 'u-w1-b2', 'lead_in': 'Wayfinding:', 'text': 'Built tactile museum signage.'},
        ]
        base = CvDocument.model_validate(self.inp.base_cv)
        evidence = pipeline.build_evidence(base, self.inp.profile)
        reordered = cv_reply()
        reordered['entries']['work-1'] = {
            'order': ['u-w1-b2', 'u-w1-b1'],
            'bullets': {
                'u-w1-b1': {'action': 'keep'},
                'u-w1-b2': {
                    'action': 'rewrite',
                    'lead_in': 'Wayfinding:',
                    'text': 'Built tactile museum signage for visitors.',
                    'sources': ['cv:work-1:b2'],
                },
            },
        }
        doc, _ = draft_cv(reordered, base, evidence, self.inp.job['description'])
        units = doc.sections[1].entries[0].units
        self.assertEqual([u.id for u in units], ['u-w1-b2', 'u-w1-b1'])
        self.assertEqual(units[0].origin, 'ai')
        self.assertEqual(units[0].lead_in, 'Wayfinding:')
        self.assertEqual(units[1].origin, 'source')
        self.assertEqual(units[1].text, 'Participated in an annual gallery seminar.')
        self.assertEqual(units[1].sources, ['cv:work-1:b1'])

    def test_cv_keep_action_stays_verbatim_and_review_cannot_patch_it(self):
        cv = cv_reply()
        cv['entries']['work-1']['bullets']['u-base-bullet'] = {'action': 'keep'}
        result = self.run_with(
            cv=cv,
            review={
                'findings': [],
                'patches': [{
                    'document': 'cv',
                    'unit_id': 'u-base-bullet',
                    'kind': 'rephrase',
                    'text': 'Oversaw public workshops with librarians.',
                }],
            },
        )
        bullet = result.cv['sections'][1]['entries'][0]['units'][0]
        self.assertEqual(bullet['origin'], 'source')
        self.assertEqual(bullet['text'], 'Designed a workshop with librarians.')
        self.assertEqual(bullet['sources'], ['cv:work-1:b1'])
        self.assertEqual(result.telemetry['applied_patches'], [])

    def test_letter_subjects_are_deterministic_for_de_and_en(self):
        base = CvDocument.model_validate(self.inp.base_cv)
        for language, title, expected in [
            ('de', 'Ausstellungsplanerin (m/w/d)', 'Bewerbung als Ausstellungsplanerin (m/w/d)'),
            ('de', 'Bewerbung als Ausstellungsplanerin', 'Bewerbung als Ausstellungsplanerin'),
            ('en', 'Workshop Designer', 'Application for Workshop Designer'),
            ('en', 'Application for Workshop Designer', 'Application for Workshop Designer'),
        ]:
            with self.subTest(language=language, title=title):
                self.inp.language = language
                self.inp.job['title'] = title
                letter = pipeline._letter_draft(letter_reply(), self.inp, base)
                self.assertEqual(letter.subject.text, expected)

    def test_letter_motivation_sources_do_not_trigger_duplicate_employer_and_fit_cites_languages(self):
        self.inp.profile['language_levels'] = {'English': 'C1', 'German': 'native'}
        base = CvDocument.model_validate(self.inp.base_cv)
        evidence = pipeline.build_evidence(base, self.inp.profile)
        groups, response = pipeline._letter_contract(base, evidence)
        self.assertIn('profile:language_levels', evidence)
        reply = {
            'subject': {'text': 'Designer'},
            'contact_name': '',
            'motivation': {
                'text': letter_reply()['paragraphs'][0]['text'],
                'sources': ['cv:work-1:b1', 'job'],
            },
            'examples': {'work-1': {
                'anchor': 'cv:work-1:b1',
                'priority': 1,
                'sentences': [{'text': letter_reply()['paragraphs'][1]['text'], 'source': 'cv:work-1:b1'}],
            }},
            'fit': {
                'text': letter_reply()['paragraphs'][2]['text'],
                'sources': ['profile:language_levels', 'job'],
            },
            'closing': letter_reply()['closing'],
        }
        letter = pipeline._letter_from_slots(reply, response, groups, evidence, self.inp, base, base)
        self.assertEqual(letter.paragraphs[0].sources, ['cv:work-1:b1', 'job'])
        self.assertEqual(letter.paragraphs[2].sources, ['profile:language_levels', 'job'])
        issues = pipeline._letter_prose_issues(letter, base, base, 'Wren Museum')
        self.assertNotIn('letter_duplicate_employer', {issue.code for issue in issues})

    def test_review_patch_rejects_cv_profile_style_regressions(self):
        result = self.run_with(review={
            'findings': [],
            'patches': [{
                'document': 'cv',
                'unit_id': 'u-ai-profile-1',
                'kind': 'rephrase',
                'text': 'Combine visitor interviews with public workshop prototyping.',
            }],
        })
        self.assertEqual(result.telemetry['applied_patches'], [])
        self.assertEqual(
            result.cv['sections'][0]['units'][0]['text'],
            cv_reply()['profile']['text'],
        )
