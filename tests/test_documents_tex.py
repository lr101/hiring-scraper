import unittest
from pathlib import Path

from hiring_scraper.app.tex_cv import career_facts_from_tex, history_facts, parse_tex_cv, parse_tex_languages
from hiring_scraper.documents import (
    apply_patch,
    facts_from_document,
    import_marked_tex,
    project_tex,
    unit_by_id,
    walk_units,
)

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'documents'


def _normalize_facts(facts: dict) -> dict:
    return {
        kind: [
            {**row, 'description': ' '.join(row['description'].replace('--', '–').replace('~', ' ').split())}
            for row in rows
        ]
        for kind, rows in facts.items()
    }


class ImportMarkedTexTests(unittest.TestCase):
    def test_imports_two_page_fixed_and_flowing_german_fixture(self):
        source = (FIXTURES / 'marked_de_cv.tex').read_text(encoding='utf-8')
        doc = import_marked_tex(source)

        self.assertEqual(doc.format, 'radius-cv-v2')
        self.assertEqual(doc.language, 'de')
        self.assertEqual(doc.page, 'letter')
        self.assertTrue(doc.photo)
        self.assertEqual(doc.header.name, 'MAREN LINDNER')
        self.assertEqual(doc.headline.id, 'u-headline')
        self.assertEqual(doc.headline.text, 'Operations & Prozessanalyse | Logistik | Qualitätsmanagement')

        self.assertEqual([(c.kind, c.text, c.href) for c in doc.header.contacts], [
            ('phone', '+49 151 00001234', ''),
            ('location', 'Bremen, DE', ''),
            ('email', 'maren.lindner@example.test', 'mailto:maren.lindner@example.test'),
        ])

        # All imported units have origin='source' and deterministic IDs
        for unit in walk_units(doc):
            self.assertEqual(unit.origin, 'source')

        # Profile joins wrapped lines, removes soft hyphen (Prozess- + analyse), keeps suspended hyphen (Planungs- und)
        profile = unit_by_id(doc, 'u-profile-1')
        self.assertIsNotNone(profile)
        self.assertEqual(
            profile.text,
            'Wirtschaftsingenieurin mit Schwerpunkt auf Prozessanalyse, Planungs- und Steuerungsprozessen sowie bereichsübergreifender Koordination in der Logistik.',
        )

        # Experience entries preserve order, locked fields, joined bullets and LaTeX escapes
        exp_sec = next(s for s in doc.sections if s.kind == 'experience')
        self.assertEqual(exp_sec.title, 'BERUFSERFAHRUNG')
        self.assertEqual([e.id for e in exp_sec.entries], ['work-1', 'work-2'])
        self.assertEqual(exp_sec.entries[0].locked.company, 'NORDWIND LOGISTICS GMBH')
        self.assertEqual(exp_sec.entries[0].locked.start, '2022')
        self.assertEqual(exp_sec.entries[0].locked.end, '2025')
        self.assertEqual(len(exp_sec.entries[0].units), 2)
        self.assertEqual(
            exp_sec.entries[0].units[0].text,
            'Reduzierte Durchlaufzeiten um 35% durch standardisierte SOPs & automatisierte KPI-Berichte in Tableau.',
        )
        self.assertEqual(exp_sec.entries[0].units[0].id, 'u-work-1-b1')
        self.assertEqual(
            exp_sec.entries[1].units[0].text,
            'Erstellte SQL-Auswertungen und Power BI Dashboards für Budget_Tracking (#42, ca. 500$ Einsparung pro Lauf).',
        )

        # Education entries preserve label, intro prose and \textbf{Lead:} lead-ins
        edu_sec = next(s for s in doc.sections if s.kind == 'education')
        self.assertEqual(edu_sec.title, 'AUSBILDUNG')
        self.assertEqual([e.id for e in edu_sec.entries], ['study-1', 'study-2'])
        study1, study2 = edu_sec.entries
        self.assertEqual(study1.label, 'HOCHSCHULE')
        self.assertEqual(len(study1.intro), 3)
        self.assertIn('B.Sc. Wirtschaftsingenieurwesen – Schwerpunkte:', study1.intro[0].text)
        self.assertEqual(study1.intro[1].lead_in, 'Anerkennung:')
        self.assertIn('Vollständig anerkannter Bachelorabschluss', study1.intro[1].text)
        self.assertEqual(study1.intro[2].text, 'Ausgewählte Projekte: Logistik & Qualität')
        self.assertEqual(len(study1.units), 2)
        self.assertEqual(study1.units[0].lead_in, 'Küstendock – Lageroptimierung:')
        self.assertIn('18% kürzere Wege.', study1.units[0].text)
        self.assertEqual(study1.units[1].lead_in, 'WeserPack – Qualitätsprüfung & WBS:')
        self.assertEqual(study2.label, 'PRAKTIKUM')
        self.assertEqual(len(study2.units), 1)

        # Remaining page-2 sections: publications, competencies, tools, minipage languages & certs
        by_title = {s.title: s for s in doc.sections}
        self.assertIn('VERÖFFENTLICHUNGEN', by_title)
        self.assertEqual(by_title['VERÖFFENTLICHUNGEN'].kind, 'text')
        self.assertEqual(by_title['VERÖFFENTLICHUNGEN'].layout, 'paragraphs')
        self.assertEqual(len(by_title['VERÖFFENTLICHUNGEN'].units), 2)
        self.assertIn('“Modular Warehouse Telemetry” — Mitautorenschaft', by_title['VERÖFFENTLICHUNGEN'].units[0].text)

        self.assertEqual(by_title['KERNKOMPETENZEN'].kind, 'skills')
        self.assertEqual(by_title['KERNKOMPETENZEN'].layout, 'inline')
        self.assertEqual(len(by_title['KERNKOMPETENZEN'].units), 5)

        self.assertEqual(by_title['TOOLS & SYSTEME'].kind, 'skills')
        self.assertEqual(by_title['TOOLS & SYSTEME'].layout, 'inline')
        self.assertEqual([u.text for u in by_title['TOOLS & SYSTEME'].units], [
            'Tableau', 'Power BI', 'SQL', 'Jira', 'Kanban', 'RFID-Systeme',
        ])

        self.assertEqual(by_title['SPRACHEN'].kind, 'list')
        self.assertEqual(by_title['SPRACHEN'].layout, 'bullets')
        self.assertEqual([u.text for u in by_title['SPRACHEN'].units], [
            'Deutsch – Muttersprache', 'Englisch – C1', 'Niederländisch – B1',
        ])

        self.assertEqual(by_title['ZERTIFIKATE & AUSZEICHNUNGEN'].kind, 'list')
        self.assertEqual(len(by_title['ZERTIFIKATE & AUSZEICHNUNGEN'].units), 2)

    def test_imports_flowing_english_a4_fixture(self):
        source = (FIXTURES / 'flowing_en_cv.tex').read_text(encoding='utf-8')
        doc = import_marked_tex(source)

        self.assertEqual(doc.language, 'en')
        self.assertEqual(doc.page, 'a4')
        self.assertFalse(doc.photo)
        self.assertEqual(doc.header.name, 'Jonas Weber')
        self.assertEqual(len(doc.header.contacts), 4)
        self.assertEqual(doc.header.contacts[3].kind, 'link')
        self.assertEqual(doc.header.contacts[3].href, 'https://example.test/jonas')

        exp_sec = next(s for s in doc.sections if s.kind == 'experience')
        self.assertTrue(exp_sec.entries[0].locked.current)
        self.assertIsNone(exp_sec.entries[0].locked.end)
        self.assertEqual(len(exp_sec.entries[0].units), 2)

        edu_sec = next(s for s in doc.sections if s.kind == 'education')
        self.assertEqual(edu_sec.entries[0].units[0].lead_in, 'PortFlow Capstone:')

    def test_rejects_unknown_or_dangerous_structure_with_line_number(self):
        base = (FIXTURES / 'flowing_en_cv.tex').read_text(encoding='utf-8')
        cases = [
            ('unknown body command', base.replace(r'\end{document}', '\\mysterycmd{oops}\n\\end{document}')),
            ('unknown environment', base.replace(r'\end{document}', '\\begin{tabular}{ll}\na & b\n\\end{tabular}\n\\end{document}')),
            ('preamble input', base.replace(r'\pagestyle{empty}', r'\input{/etc/passwd}')),
            ('char code escape', base.replace('Jonas Weber', 'Jonas^^0aWeber')),
            ('undefined macro', base.replace(r'\RadiusWorkA{}', r'\RadiusMissing{}')),
            ('broken marker json', base.replace('"current": true', '"current": broken')),
            ('unclosed entry', base.replace('% radius:end\n', '', 1)),
            ('missing document', '% radius:entry {}\n'),
        ]
        for label, bad_source in cases:
            with self.subTest(case=label):
                with self.assertRaises(ImportError) as ctx:
                    import_marked_tex(bad_source)
                self.assertRegex(str(ctx.exception), r'Line \d+:', f'{label} error missing line number: {ctx.exception}')


class ProjectTexInvariantTests(unittest.TestCase):
    def test_flowing_projection_matches_doc_and_original_facts_strictly(self):
        source = (FIXTURES / 'flowing_en_cv.tex').read_text(encoding='utf-8')
        doc = import_marked_tex(source)
        projected = project_tex(doc)

        self.assertNotIn(r'\put(', projected)
        self.assertNotIn(r'\AddToShipoutPictureFG', projected)
        self.assertIn('% radius:profile', projected)
        self.assertIn('% radius:entry ', projected)
        self.assertIn('% radius:responsibilities', projected)
        self.assertIn('% radius:end', projected)

        self.assertEqual(career_facts_from_tex(projected), facts_from_document(doc))
        self.assertEqual(career_facts_from_tex(projected), career_facts_from_tex(source))
        self.assertEqual(history_facts(parse_tex_cv(projected)), history_facts(parse_tex_cv(source)))
        self.assertEqual(parse_tex_languages(projected), {'German': 'native', 'English': 'C1'})

    def test_fixed_coordinate_import_projection_invariant_and_round_trip(self):
        source = (FIXTURES / 'marked_de_cv.tex').read_text(encoding='utf-8')
        doc = import_marked_tex(source)
        projected = project_tex(doc)

        self.assertNotIn(r'\put(', projected)
        self.assertEqual(career_facts_from_tex(projected), facts_from_document(doc))
        self.assertEqual(history_facts(parse_tex_cv(projected)), history_facts(parse_tex_cv(source)))
        self.assertEqual(_normalize_facts(career_facts_from_tex(projected)), _normalize_facts(career_facts_from_tex(source)))
        self.assertEqual(parse_tex_languages(projected), {'German': 'native', 'English': 'C1', 'Dutch': 'B1'})

        # Round-trip importing projected flowing LaTeX preserves facts verbatim
        reimported = import_marked_tex(projected)
        self.assertEqual(career_facts_from_tex(project_tex(reimported)), career_facts_from_tex(projected))

    def test_special_characters_and_edits_preserve_doc_facts_invariant(self):
        source = (FIXTURES / 'flowing_en_cv.tex').read_text(encoding='utf-8')
        doc = import_marked_tex(source)
        special_text = r'Tracked R&D 100% $500 #1_rate {set} ~approx ^2 path\bin.'
        edited = apply_patch(doc, 'u-work-1-b1', special_text, 'user')
        edited = apply_patch(edited, 'u-work-1-b2', 'Hidden bullet.', 'user', hidden=True)

        projected = project_tex(edited)
        self.assertNotIn('^^', projected)
        self.assertEqual(career_facts_from_tex(projected), facts_from_document(edited))
        self.assertEqual(career_facts_from_tex(projected)['experiences'][0]['description'], special_text)


if __name__ == '__main__':
    unittest.main()
