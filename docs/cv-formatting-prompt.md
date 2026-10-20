# Format your base CVs

Copy the prompt below into ChatGPT and attach your original `.tex` CV. Review both outputs before uploading them into the matching language slots.

```text
Update my attached original LaTeX CV and create two complete base files: cv-en.tex in English and cv-de.tex in German. Preserve every verified fact, name, employer, qualification and date. Translate descriptions and headings idiomatically, without inventing achievements or dates. Ask me for any missing required metadata; do not guess it.
Mark EVERY work and study entry using these exact non-printing comments, each on its own line:
% radius:entry {"id":"work-1","kind":"work","company":"Exact employer","title":"Exact role","location":"Known location or empty string","start":"2021-03","end":null,"current":true}
\subsection*{Exact role at Exact employer}
Known location and dates.\par
% radius:responsibilities
\begin{itemize}
\item A verified responsibility or achievement.
\end{itemize}
% radius:end
Use kind "study" for education, company for the institution and title for the qualification. Keep the same unique stable IDs in both languages and all future tailored CVs. Dates must be known YYYY or YYYY-MM, without inventing months. For completed entries current=false and end is a known date; for ongoing entries current=true and end=null. Include all eight JSON keys, no extra keys, on one line. Keep company names untranslated. Use an empty location if unknown.
Place all responsibility/achievement bullets (or study details) between radius:responsibilities and radius:end. Include only plain escaped text and the commands itemize, item, textbf, emph and par in that block. No custom macros, external inputs, nested entry markers or duplicated metadata. Do not use ^^ character sequences. The visible heading and dates precede the responsibilities marker; metadata comments must match them. Return complete UTF-8 .tex files, with documentclass, begin{document} and end{document}, not snippets. Do not create a separate profile responsibility section. I will review and upload each file in its matching language slot.
```

Starter files: [English](../frontend/public/cv-templates/cv-en.tex) and [German](../frontend/public/cv-templates/cv-de.tex). Replace every placeholder and YYYY-MM with your verified facts before uploading; the untouched starter files intentionally fail date validation. Remove example entries that do not apply. Keep IDs identical between translations. A responsibilities block may be empty if there are no verified details.

The base CV is never executed during extraction. Responsibility blocks support plain escaped text, `itemize`, `item`, `textbf`, `emph`, and `par`. Unsupported macros are rejected so extraction cannot silently drop content. Generated application PDFs use the supplied restricted Radius LaTeX template.

Automatic language selection first honors an explicit English/German choice, then a request to apply in either language, then common posting-language words (English when ambiguous). Check the selected language before preparing an application; use the language override for ambiguous or bilingual postings. Missing language versions are reported rather than silently replaced.

Both translations must contain the same history IDs, employers/institutions and dates. If you change those underlying facts, remove both saved base CVs and upload the updated pair. Text, role names and locations can be translated or refined without that reset.
