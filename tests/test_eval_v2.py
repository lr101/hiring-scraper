import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts' / 'eval') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts' / 'eval'))

import eval_v2  # noqa: E402


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _write_minimal_pdf(path: Path, pages: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    with path.open('wb') as handle:
        writer.write(handle)


def _write_minimal_docx(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    paras = ''.join(f'<w:p><w:r><w:t>{line}</w:t></w:r></w:p>' for line in lines)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{paras}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('word/document.xml', xml)


def _passing_checks() -> list[dict]:
    return [
        {'code': code, 'status': 'passed'}
        for code in ('text_extractable', 'glyphs', 'special_characters', 'name_present', 'contacts_present')
    ]


def _write_run(
    run_dir: Path,
    *,
    attempts: dict | None = None,
    attempt_failures: dict | None = None,
    cv_state: str = 'completed',
    cv_pages: int = 2,
    letter_pages: int = 1,
    export_cv: bool = True,
    export_letter: bool = True,
    use_render_checks: bool = False,
    findings: list[dict] | None = None,
    cv_text_body: str = 'Fictional CV content.',
    letter_text_body: str = 'Fictional cover letter content.',
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    telemetry = {
        'attempts': attempts if attempts is not None else {'cv': 1, 'letter': 1, 'review': 1},
        'attempt_failures': attempt_failures if attempt_failures is not None else {},
        'stages': {
            'cv': {'state': cv_state, 'attempts': 1},
            'letter': {'state': 'completed', 'attempts': 1},
            'review': {'state': 'completed', 'attempts': 1},
        },
        'pages': {'cv': cv_pages, 'letter': letter_pages},
        'render': {
            'cv': {'checks': _passing_checks() if export_cv else [{'code': 'glyphs', 'status': 'failed'}]},
            'letter': {'checks': _passing_checks() if export_letter else [{'code': 'glyphs', 'status': 'failed'}]},
        },
    }
    if not use_render_checks:
        telemetry['export_verified'] = {'cv': export_cv, 'letter': export_letter}
    _write_json(run_dir / 'telemetry.json', telemetry)
    _write_json(run_dir / 'findings.json', findings if findings is not None else [])
    (run_dir / 'cv.txt').write_text(cv_text_body, encoding='utf-8')
    (run_dir / 'letter.txt').write_text(letter_text_body, encoding='utf-8')
    _write_minimal_pdf(run_dir / 'cv.pdf', pages=cv_pages)
    _write_minimal_pdf(run_dir / 'letter.pdf', pages=letter_pages)


class EvalV2HardCheckTests(unittest.TestCase):
    def test_hard_checks_pass_and_failure_modes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / 'new' / 'case-a' / '1'
            _write_run(good, use_render_checks=True, findings=[
                {'document': 'cv', 'code': 'review_grounding', 'severity': 'medium', 'message': 'Minor wording note.'},
            ])
            res_good = eval_v2.evaluate_hard_checks(good)
            self.assertTrue(res_good['passed'])
            self.assertTrue(res_good['attempts_ok'])
            self.assertEqual(res_good['coverage_restores'], 0)
            self.assertTrue(res_good['pages_ok'])
            self.assertTrue(res_good['export_verified_ok'])
            self.assertEqual(res_good['high_grounding_findings'], 0)

            bad_attempts = root / 'new' / 'case-b' / '1'
            _write_run(bad_attempts, attempts={'cv': 2, 'letter': 1, 'review': 1})
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_attempts)['attempts_ok'])
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_attempts)['passed'])

            bad_failures = root / 'new' / 'case-c' / '1'
            _write_run(bad_failures, attempt_failures={'letter': [{'attempt': 1, 'code': 'invalid_response'}]})
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_failures)['attempts_ok'])

            bad_restore = root / 'new' / 'case-d' / '1'
            _write_run(bad_restore, cv_state='fallback')
            self.assertEqual(eval_v2.evaluate_hard_checks(bad_restore)['coverage_restores'], 1)
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_restore)['passed'])

            bad_pages = root / 'new' / 'case-e' / '1'
            _write_run(bad_pages, cv_pages=3, letter_pages=1)
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_pages)['pages_ok'])

            bad_letter_pages = root / 'new' / 'case-f' / '1'
            _write_run(bad_letter_pages, cv_pages=2, letter_pages=2)
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_letter_pages)['pages_ok'])

            bad_export = root / 'new' / 'case-g' / '1'
            _write_run(bad_export, export_letter=False)
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_export)['export_verified_ok'])

            bad_grounding = root / 'new' / 'case-h' / '1'
            _write_run(bad_grounding, findings=[
                {'document': 'letter', 'code': 'unsupported_number', 'severity': 'high', 'message': 'Unverified figure.'},
            ])
            self.assertEqual(eval_v2.evaluate_hard_checks(bad_grounding)['high_grounding_findings'], 1)
            self.assertFalse(eval_v2.evaluate_hard_checks(bad_grounding)['passed'])

            report = eval_v2.collect_hard_checks(root)
            self.assertEqual(report['by_version']['new']['total'], 8)
            self.assertEqual(report['by_version']['new']['passed'], 1)
            self.assertFalse(report['by_version']['new']['all_passed'])
            md = eval_v2.format_hard_checks_markdown(report)
            self.assertIn('| new | case-a | 1 |', md)


class EvalV2PackagingAndAggregationTests(unittest.TestCase):
    def test_pairwise_calibration_and_aggregation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases_dir = root / 'cases'
            runs_dir = root / 'runs'
            profile_path = root / 'profile.json'
            _write_json(profile_path, {'language_levels': {'en': 'C1', 'de': 'C2'}})

            base_en = root / 'base-en.docx'
            base_de = root / 'base-de.tex'
            _write_minimal_docx(base_en, ['Dara Finch', 'Workshop Designer'])
            base_de.write_text('% fictional base de\n', encoding='utf-8')

            user_cv = root / 'user-cv-en.docx'
            user_lt = root / 'user-letter-en.txt'
            _write_minimal_docx(user_cv, ['Dara Finch', 'Tailored Workshop CV'])
            user_lt.write_text('Dear Hiring Team,\nFictional user letter.\n', encoding='utf-8')

            _write_json(cases_dir / 'case-en.json', {
                'id': 'case-en',
                'language': 'en',
                'base_cv': str(base_en),
                'user_baseline': {'cv': str(user_cv), 'letter': str(user_lt)},
                'job': {'title': 'Workshop Lead', 'company_name': 'Wren Museum', 'location': 'Loam Bay',
                        'description': 'Lead public workshops.'},
            })
            _write_json(cases_dir / 'case-de.json', {
                'id': 'case-de',
                'language': 'de',
                'base_cv': str(base_de),
                'user_baseline': None,
                'job': {'title': 'Werkstattleitung', 'company_name': 'Fiktiv GmbH', 'location': 'Musterstadt',
                        'description': 'Workshops planen.'},
            })

            for version in ('previous', 'new'):
                for cid in ('case-en', 'case-de'):
                    for run_idx in (1, 2):
                        _write_run(
                            runs_dir / version / cid / str(run_idx),
                            cv_pages=2, letter_pages=1,
                            cv_text_body=f'CV text for {version} {cid} {run_idx}',
                            letter_text_body=f'Letter text for {version} {cid} {run_idx}',
                        )

            pairwise_dir = root / 'pairwise'
            rubric_out = root / 'JUDGE-PAIRWISE.md'
            summary = eval_v2.build_pairwise_package(
                cases_dir=cases_dir,
                runs_dir=runs_dir,
                out_dir=pairwise_dir,
                profile_path=profile_path,
                rubric_path=rubric_out,
                runs=(1, 2),
                seed=42,
                root=root,
                render_scale=1.0,
            )
            # case-en: 2 runs x 2 kinds x 2 comparisons = 8 pairs
            # case-de: 2 runs x 2 kinds x 1 comparison = 4 pairs
            self.assertEqual(summary['pair_count'], 12)
            self.assertEqual(summary['by_comparison'], {'new_vs_previous': 8, 'new_vs_user': 4})
            self.assertTrue(rubric_out.is_file())

            manifest_text = (pairwise_dir / 'manifest.json').read_text(encoding='utf-8')
            self.assertNotIn('new_vs_previous', manifest_text)
            self.assertNotIn('new_vs_user', manifest_text)
            self.assertNotIn('A_label', manifest_text)

            manifest = eval_v2.read_json(pairwise_dir / 'manifest.json')
            key_payload = eval_v2.read_json(pairwise_dir / 'key.private.json')
            self.assertEqual(len(manifest['pairs']), 12)
            self.assertEqual(len(key_payload['pairs']), 12)
            # Every generated side has rendered PNG pages.
            for pair in manifest['pairs']:
                meta = key_payload['pairs'][pair['pair_id']]
                for side in ('A', 'B'):
                    self.assertTrue(Path(pair[side]['text']).is_file())
                    if meta[f'{side}_label'] != 'user':
                        self.assertGreaterEqual(len(pair[side]['images']), 1)
                        self.assertTrue(Path(pair[side]['images'][0]).is_file())
                    else:
                        self.assertEqual(pair[side]['images'], [])

            # Calibration package only includes case-en (which has a user baseline).
            cal_dir = root / 'calibration'
            cal_summary = eval_v2.build_calibration_package(
                cases_dir=cases_dir, out_dir=cal_dir, profile_path=profile_path, root=root,
            )
            self.assertEqual(cal_summary['case_count'], 1)
            self.assertTrue((cal_dir / 'JUDGE-CALIBRATION.md').is_file())
            self.assertIn('Tailored Workshop CV', (cal_dir / 'cases' / 'case-en' / 'cv.txt').read_text(encoding='utf-8'))

            # Aggregation: simulate 3 judges where 2 prefer 'new' on 75% of new_vs_previous and 75% of new_vs_user.
            def make_judge(name: str, win_indices: set[int], tie_indices: set[int], flag_new: bool = False):
                pairs_out = []
                for idx, (pid, meta) in enumerate(sorted(key_payload['pairs'].items())):
                    new_side = 'A' if meta['A_label'] == 'new' else 'B'
                    other_side = 'B' if new_side == 'A' else 'A'
                    if idx in win_indices:
                        winner = new_side
                    elif idx in tie_indices:
                        winner = 'tie'
                    else:
                        winner = other_side
                    inv = {'A': [], 'B': []}
                    if flag_new and idx == 0:
                        inv[new_side] = [{'quote': 'fictional claim', 'explanation': 'not in base'}]
                    pairs_out.append({
                        'pair_id': pid,
                        'winner': winner,
                        'reasons': ['Better fit.'],
                        'invented_facts': inv,
                    })
                return {'judge': name, 'pairs': pairs_out, 'summary': 'ok'}

            all_idx = set(range(12))
            judges_pass = [
                make_judge('judge-1', win_indices=all_idx - {0, 1}, tie_indices={0}),
                make_judge('judge-2', win_indices=all_idx - {0, 1, 2}, tie_indices={1}),
                make_judge('judge-3', win_indices={0, 1, 2, 3, 4, 5, 6, 7}, tie_indices=set()),
            ]
            hc = eval_v2.collect_hard_checks(runs_dir)
            agg_pass = eval_v2.aggregate_judgements(
                key_payload=key_payload, judge_payloads=judges_pass, hard_checks=hc,
            )
            self.assertTrue(agg_pass['acceptance']['adopt'])
            self.assertTrue(agg_pass['acceptance']['vs_previous_ok'])
            self.assertTrue(agg_pass['acceptance']['vs_user_ok'])
            self.assertTrue(agg_pass['acceptance']['zero_invented_facts_ok'])
            self.assertTrue(agg_pass['acceptance']['hard_checks_ok'])
            md = eval_v2.format_aggregation_markdown(agg_pass)
            self.assertIn('**Adopt new prompts**: **True**', md)

            # Invented fact on `new` must block adoption even with winning rates.
            judges_inv = [
                make_judge('judge-1', win_indices=all_idx, tie_indices=set(), flag_new=True),
                make_judge('judge-2', win_indices=all_idx, tie_indices=set()),
            ]
            agg_inv = eval_v2.aggregate_judgements(
                key_payload=key_payload, judge_payloads=judges_inv, hard_checks=hc,
            )
            self.assertFalse(agg_inv['acceptance']['zero_invented_facts_ok'])
            self.assertFalse(agg_inv['acceptance']['adopt'])

            # Failing hard checks must block adoption.
            _write_run(runs_dir / 'new' / 'case-en' / '1', cv_pages=3)
            hc_bad = eval_v2.collect_hard_checks(runs_dir)
            agg_hc_bad = eval_v2.aggregate_judgements(
                key_payload=key_payload, judge_payloads=judges_pass, hard_checks=hc_bad,
            )
            self.assertFalse(agg_hc_bad['acceptance']['hard_checks_ok'])
            self.assertFalse(agg_hc_bad['acceptance']['adopt'])


if __name__ == '__main__':
    unittest.main()
