# Format your base CVs

Copy the prompt below into ChatGPT and attach your original `.tex` CV. Review both outputs before uploading them into the matching language slots.

```text
Update my attached original LaTeX CV and create two complete base files: cv-en.tex in English and cv-de.tex in German. Preserve the original document structure and layout: preamble, packages, fonts, images, coordinates, widths, spacing, page breaks, section order and bullet count. Do not replace the design with a new template. Preserve every verified fact, name, employer, qualification and date. Translate descriptions and headings idiomatically, without inventing achievements or dates. Ask me for any missing required metadata; do not guess it.
If the CV already includes one portrait, change only its image filename to portrait.png in both files (for example, \includegraphics{portrait.png}), retaining all existing sizing and placement options. Do not add a portrait if there is none. Keep portrait.png as the source placeholder.
If an existing profile introduction (Kurzprofil) is present, mark its prose with % radius:profile and % radius:endprofile on separate lines; leave its heading outside. For positioned introductions, use one inert RadiusProfile macro per existing line, defined in the profile block and called at the original position. Do not add a new introduction if none exists.
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
For a layout-heavy source whose descriptions use positioning commands, preserve the rendering commands in place. Move only each existing description text into a zero-argument macro defined on its own line inside its entry responsibility block, e.g. \newcommand{\RadiusWorkOneLineOne}{Exact original description text.}, and replace that text at its original location with \RadiusWorkOneLineOne{}. Use unique macro names beginning Radius followed by letters only. Keep one macro per existing fixed-position line or flowing bullet. Macro bodies may contain only the same inert text/formatting allowed below. Metadata and macro blocks may precede the document body; leave visible headings where they are. These macros are read as literal text, never executed by Radius. Do not duplicate descriptions in comments.
Place all responsibility/achievement bullets (or study details) between radius:responsibilities and radius:end. Include only plain escaped text and the commands itemize, item, textbf, emph and par in that block. Except for the literal Radius text macros above, no custom macros, external inputs, nested entry markers or duplicated metadata. Do not use ^^ character sequences. The visible heading and dates precede the responsibilities marker; metadata comments must match them. Return complete UTF-8 .tex files, with documentclass, begin{document} and end{document}, not snippets. Do not create a separate profile responsibility section. I will review and upload each file in its matching language slot.
```

Starter files: [English](../frontend/public/cv-templates/cv-en.tex) and [German](../frontend/public/cv-templates/cv-de.tex). Replace every placeholder and YYYY-MM with your verified facts before uploading; the untouched starter files intentionally fail date validation. Remove example entries that do not apply. Keep IDs identical between translations. A responsibilities block may be empty if there are no verified details.

Radius reads uploaded TeX as data. Responsibility blocks support escaped text and
`itemize`, `item`, `textbf`, `emph` and `par`. Unsupported content can raise
an import error. Review the structured base CV before preparing documents.

Use the same entry IDs, employers and dates in both translations. If those
facts change, remove the saved pair and upload the corrected sources. Select the
application language on the preparation page.

To include a portrait, save a PNG under **Edit profile → CVs & writing → CV photo
(optional)**. The shared CV view uses it in the editor and PDF when enabled.
See the [application preparation guide](application-preparation.md).
