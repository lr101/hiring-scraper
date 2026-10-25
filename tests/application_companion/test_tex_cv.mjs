import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseTexCV, selectCVLanguage } from '../../extensions/application-companion/tex-cv.js';
const source = String.raw`\documentclass{article}
\begin{document}
% radius:entry {"id":"work-1","kind":"work","company":"R&D Labs","title":"Engineer","location":"Bonn","start":"2021","end":null,"current":true}
\subsection*{Engineer}
% radius:responsibilities
\begin{itemize}
\item Built \textbf{R\&D} tools with 20\% less waste.
\item Coordinated research.
\end{itemize}
% radius:end
% radius:entry {"id":"study-1","kind":"study","company":"University","title":"MSc","location":"Bonn","start":"2018","end":"2020","current":false}
\subsection*{MSc}
% radius:responsibilities
Studied computer science.\par
% radius:end
\end{document}`;
test('extracts work and study separately, with responsibilities from rendered body', () => {
 const cv = parseTexCV(source);
 assert.equal(cv.experiences[0].company, 'R&D Labs');
 assert.equal(cv.experiences[0].description, 'Built R&D tools with 20% less waste.\nCoordinated research.');
 assert.equal(cv.education[0].title, 'MSc');
 assert.equal(cv.education[0].description, 'Studied computer science.');
});
test('rejects broken boundaries, duplicate IDs, invalid dates and unmarked prose', () => {
 for (const bad of [source.replace('% radius:end', ''), source.replace('study-1', 'work-1'), source.replace('"2021"', '"2021-15"'), 'Plain CV']) assert.throws(() => parseTexCV(bad));
});
test('chooses explicit application language ahead of posting language and requires that base', () => {
 assert.equal(selectCVLanguage('English', 'Bitte senden Sie Ihre Bewerbung auf Deutsch.'), 'en');
 assert.equal(selectCVLanguage('auto', 'Please apply in German. We are looking for an engineer.'), 'de');
 assert.equal(selectCVLanguage('auto', 'Bitte senden Sie Ihre Bewerbung auf Englisch.'), 'en');
 assert.equal(selectCVLanguage('auto', 'Wir suchen einen Entwickler für unser Team.'), 'de');
 assert.equal(selectCVLanguage('auto', 'We are looking for an engineer.'), 'en');
});
export { source };

import { validateProfile } from '../../extensions/application-companion/profile.js';
import { connectSources } from '../../extensions/application-companion/sources.js';
test('marked CV replaces stale manual history and survives profile validation', () => {
 const profile = validateProfile({ cv_text: source, base_cvs: { en: source }, experiences: [] });
 assert.equal(profile.experiences[0].id, 'work-1');
 assert.equal(profile.education[0].id, 'study-1');
 assert.equal(validateProfile(profile).education[0].title, 'MSc');
});
test('returned marked CV automatically supplies tailored descriptions and preserves original profile', () => {
 const base = validateProfile({ cv_text: source, base_cvs: { en: source } });
 const pack = {job_id: 1, profile_id: 2, cv_updated_at: 'v1', cv: 'CV', cover_letter: 'Dear team', application_answers: 'None', latex_documents: { cv: source.replace('Coordinated research.', 'Coordinated relevant research.') } };
 const connected = connectSources(base, source, pack, 'https://example.org/apply');
 assert.match(connected.experiences[0].description, /Coordinated relevant research/);
 assert.match(base.experiences[0].description, /Coordinated research/);
 const changedDates = {...pack, latex_documents: {cv: source.replace('"2021"', '"2019"')}};
 assert.throws(() => connectSources(base, source, changedDates, 'https://example.org/apply'), /facts|dates|history/i);
});
import { withCVLanguage } from '../../extensions/application-companion/tex-cv.js';
test('selects German base facts for an unprepared German form and blocks missing bases', () => {
 const german = source.replace('Engineer', 'Entwickler');
 const result = withCVLanguage({ base_cvs: { en: source, de: german } }, 'de-DE');
 assert.equal(result.experiences[0].title, 'Entwickler');
 assert.equal(result.education[0].id, 'study-1');
 assert.throws(() => withCVLanguage({ base_cvs: { en: source } }, 'de'), /German/);
});
test('marked base CV rejects a returned CV without the complete marker contract', () => {
 const base = validateProfile({cv_text: source});
 const pack = {job_id: 1, profile_id: 2, cv_updated_at: 'v1', cv: 'CV', cover_letter: 'Dear team', application_answers: 'None'};
 assert.throws(() => connectSources(base, source, pack, 'https://example.org/apply'), /marked|LaTeX/i);
 assert.throws(() => connectSources(base, source, {...pack, latex_documents: { cv: String.raw`\begin{document}Unmarked\end{document}` }}, 'https://example.org/apply'), /marker/i);
});
test('base CV translations share stable identity before they can be saved', () => {
 assert.throws(() => validateProfile({base_cvs: {en: source, de: source.replace('work-1', 'different-id')}}), /facts|dates|history/i);
});

import { validateTailoredCV } from '../../extensions/application-companion/tex-cv.js';
test('tailoring rejects layout, headings, metadata and entry-order changes outside descriptions', () => {
 for (const changed of [
  source.replace('article', 'report'),
  source.replace('\\subsection*{Engineer}', '\\subsection*{Developer}'),
  source.replace('"location":"Bonn"', '"location":"Berlin"'),
  source.replace('\\begin{itemize}', '\\begin{itemize}\n\\item Added a new bullet.'),
 ]) assert.throws(() => validateTailoredCV(source, changed), /structure|description|layout/i);
 assert.equal(validateTailoredCV(source, source.replace('Coordinated research.', 'Coordinated product research.')).experiences[0].description.includes('product research'), true);
});
test('layout-preserving description macros extract text and only their prose may change', () => {
 const compact = source.replace('\\begin{itemize}\n\\item Built \\textbf{R\\&D} tools with 20\\% less waste.\n\\item Coordinated research.\n\\end{itemize}', String.raw`\newcommand{\RadiusWorkOne}{Built R\&D tools with 20\% less waste.}
\newcommand{\RadiusWorkTwo}{Coordinated research.}`);
 assert.equal(parseTexCV(compact).experiences[0].description, 'Built R&D tools with 20% less waste.\nCoordinated research.');
 assert.match(validateTailoredCV(compact, compact.replace('Coordinated research.', 'Led user research.')).experiences[0].description, /Led user research/);
 assert.throws(() => validateTailoredCV(compact, compact.replace('RadiusWorkTwo', 'RadiusOther')), /structure|description|layout/i);
 assert.throws(() => parseTexCV(compact.replace('Coordinated research.', '\\input{secret}')), /plain|responsibility/i);
});

test('preserved sources normalize CRLF and enforce each fixed-layout macro text budget', () => {
 assert.doesNotThrow(() => validateTailoredCV((source + '\n').replace(/\n/g, '\r\n'), source + '\n'));
 const macro = source.replace('\\begin{itemize}\n\\item Built \\textbf{R\\&D} tools with 20\\% less waste.\n\\item Coordinated research.\n\\end{itemize}', String.raw`\newcommand{\RadiusWorkOne}{Coordinated research.}`);
 assert.throws(() => validateTailoredCV(macro, macro.replace('Coordinated research.', 'Coordinated research. '.repeat(100))), /budget|length|fit/i);
 assert.match(validateTailoredCV(macro, macro.replace('Coordinated research.', 'Led user research.')).experiences[0].description, /Led user research/);
});

test('tailors a marked Kurzprofil while preserving contacts, layout, history and line budgets', async () => {
 const { validateTailoredCV } = await import('../../extensions/application-companion/tex-cv.js');
 const profile = String.raw`Alex Example\par
alex@example.org\par
% radius:profile
\newcommand{\RadiusProfileA}{Engineer building useful research tools.}
% radius:endprofile
\RadiusProfileA{}\par
`;
 const original = source.replace('\\begin{document}', '\\begin{document}\n' + profile);
 const tailored = original.replace('Engineer building useful research tools.', 'Engineer coordinating research tools.');
 assert.equal(validateTailoredCV(original, tailored).experiences[0].company, 'R&D Labs');
 for (const altered of [tailored.replace('Alex Example','Other Name'), tailored.replace('alex@example.org','other@example.org'), tailored.replace('RadiusProfileA','RadiusProfileB'), tailored.replace('Engineer coordinating research tools.', 'Very long '.repeat(30)), tailored.replace('% radius:endprofile',''), tailored.replace('% radius:profile','% radius:profile\n% radius:profile')]) assert.throws(() => validateTailoredCV(original, altered));
 assert.throws(() => parseTexCV(original.replace('Engineer building useful research tools.', '\\input{secret}')));
 assert.equal(parseTexCV(original).experiences.length, 1);
});

test('English and German base CVs allow translated headings and prose while retaining career identities', async () => {
 const { validateProfile } = await import('../../extensions/application-companion/profile.js');
 const german = source.replaceAll('Engineer', 'Entwicklung').replace('Coordinated research.', 'Koordinierte Forschung.').replace('Studied computer science.', 'Studierte Informatik.');
 const profile = validateProfile({base_cvs:{en:source,de:german}});
 assert.equal(profile.base_cvs.de,german);
 assert.throws(()=>validateProfile({base_cvs:{en:source,de:german.replace('R&D Labs','Other employer')}}),/facts|dates|history/i);
 assert.throws(()=>validateProfile({base_cvs:{en:source,de:german.replace('"2021"','"2019"')}}),/facts|dates|history/i);
 assert.throws(()=>validateTailoredCV(source,german),/structure|layout/i);
});
