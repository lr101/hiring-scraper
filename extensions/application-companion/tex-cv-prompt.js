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

export const cvFormattingPromptDe = String.raw`Überarbeite meinen beigefügten ursprünglichen LaTeX-Lebenslauf und erstelle zwei vollständige Dateien: cv-en.tex auf Englisch und cv-de.tex auf Deutsch. Bewahre alle belegten Fakten, Namen, Arbeitgeber, Abschlüsse und Daten. Übersetze Überschriften und Beschreibungen idiomatisch. Erfinde keine Erfolge oder Daten. Frage nach fehlenden erforderlichen Metadaten, statt sie zu erraten.
Markiere JEDEN Berufs- und Ausbildungseintrag mit diesen nicht gedruckten Kommentaren, jeweils auf einer eigenen Zeile:
% radius:entry {"id":"work-1","kind":"work","company":"Exakter Arbeitgeber","title":"Exakte Position","location":"Bekannter Ort oder leere Zeichenfolge","start":"2021-03","end":null,"current":true}
\subsection*{Exakte Position bei Exakter Arbeitgeber}
Bekannter Ort und Zeitraum.\par
% radius:responsibilities
\begin{itemize}
\item Eine belegte Aufgabe oder ein belegter Erfolg.
\end{itemize}
% radius:end
Verwende kind "study" für Ausbildung und Studium, company für die Institution und title für den Abschluss. Behalte in beiden Sprachen und allen späteren Fassungen dieselben eindeutigen stabilen IDs. Daten müssen bekannt sein, im Format YYYY oder YYYY-MM; erfinde keine Monate. Abgeschlossene Einträge: current=false und bekanntes end; laufende Einträge: current=true und end=null. Alle acht JSON-Schlüssel müssen auf einer Zeile stehen, ohne zusätzliche Schlüssel. Übersetze Arbeitgebernamen nicht; verwende einen leeren Ort, wenn er unbekannt ist.
Alle Aufgaben, Erfolge und Ausbildungsdetails stehen zwischen radius:responsibilities und radius:end. Nutze dort nur korrekt maskierten einfachen Text und die Befehle itemize, item, textbf, emph und par. Keine eigenen Makros, externen Eingaben, verschachtelten Marker oder doppelten Metadaten. Keine ^^-Zeichenfolgen. Sichtbare Überschrift und Daten stehen vor dem Aufgabenmarker und stimmen mit den Metadaten überein. Gib vollständige UTF-8-.tex-Dateien mit documentclass, begin{document} und end{document} zurück, keine Ausschnitte. Erstelle keinen gesonderten Abschnitt mit Profilaufgaben. Ich prüfe die Dateien und lade jede im passenden Sprachbereich hoch.`;
