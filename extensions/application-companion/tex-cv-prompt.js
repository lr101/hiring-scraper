export const cvFormattingPrompt = String.raw`Update my attached original LaTeX CV and create two complete base files: cv-en.tex in English and cv-de.tex in German. Preserve every verified fact, name, employer, qualification and date. Translate descriptions and headings idiomatically, without inventing achievements or dates. Ask me for any missing required metadata; do not guess it.
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
Place all responsibility/achievement bullets (or study details) between radius:responsibilities and radius:end. Include only plain escaped text and the commands itemize, item, textbf, emph and par in that block. No custom macros, external inputs, nested entry markers or duplicated metadata. Do not use ^^ character sequences. The visible heading and dates precede the responsibilities marker; metadata comments must match them. Return complete UTF-8 .tex files, with documentclass, begin{document} and end{document}, not snippets. Do not create a separate profile responsibility section. I will review and upload each file in its matching language slot.`;

export const cvTailoringInstructions = String.raw`CV HISTORY CONTRACT:
Preserve every % radius:entry, % radius:responsibilities and % radius:end marker in the selected base CV. Keep stable IDs, kinds, employer/institution names, dates and current status unchanged. Translate titles, locations and descriptions into the chosen application language as needed. Keep visible headings and dates consistent with metadata. Tailor only supported responsibilities/achievements inside their own marked blocks, using the permitted inert LaTeX commands. Keep work and study separate. Include all original entries; do not silently omit history. These non-printing marker comments are permitted in the CV body. Return the full marked CV in latex_documents.cv. The extension extracts complete work/study records and responsibility text from these blocks automatically; no separate experience_bullets request is needed.`;
