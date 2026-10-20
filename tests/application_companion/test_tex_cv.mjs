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
