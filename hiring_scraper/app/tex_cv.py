"""Read explicit history markers as data; never execute uploaded TeX."""
import json
import re


def _description(text):
    escapes = []
    def escape(match):
        escapes.append(match[1] or {'textbackslash': '\\', 'textasciitilde': '~', 'textasciicircum': '^'}[match[2]])
        return f'\x00{len(escapes) - 1}\x00'
    text = re.sub(r'\\([&%$#_{}])|\\(textbackslash|textasciitilde|textasciicircum)\{\}', escape, text)
    text = re.sub(r'%[^\n]*', '', text)
    text = re.sub(r'\\(?:begin|end)\{itemize\}', '', text)
    text = re.sub(r'\\(?:item|par)\b', '\n', text)
    text = re.sub(r'\\(?:textbf|emph)\{', '{', text)
    if re.search(r'\\[a-zA-Z]', text):
        raise ValueError('Use plain text, itemize, textbf, emph and par inside responsibility blocks.')
    text = re.sub(r'[{}]', '', text)
    text = re.sub(r'\x00(\d+)\x00', lambda m: escapes[int(m[1])], text)
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def parse_tex_cv(source):
    from hiring_scraper.app.profiles import ApplicationExperience
    if len(source) > 100000 or '\\begin{document}' not in source or '\\end{document}' not in source:
        raise ValueError('Use a complete LaTeX CV under 100,000 characters.')
    result = {'experiences': [], 'education': []}
    entry, description, ids = None, None, set()
    def fail():
        raise ValueError('Invalid CV history markers. Use the CV formatting prompt; check IDs, dates and entry boundaries.')
    for line in source.splitlines():
        marker = re.fullmatch(r'\s*% radius:(.*)', line)
        if marker:
            token = marker[1]
            if token.startswith('entry '):
                if entry is not None:
                    fail()
                try:
                    data = json.loads(token[6:])
                    if not isinstance(data, dict) or set(data) != {'id', 'kind', 'company', 'title', 'location', 'start', 'end', 'current'}:
                        fail()
                    kind = data.pop('kind')
                    if kind not in {'work', 'study'}:
                        fail()
                    entry = ApplicationExperience.model_validate(data, strict=True).model_dump()
                except (ValueError, TypeError):
                    fail()
                if entry['id'] in ids:
                    fail()
                ids.add(entry['id'])
                description = None
            elif token == 'responsibilities':
                if entry is None or description is not None:
                    fail()
                description = []
            elif token == 'end':
                if entry is None or description is None:
                    fail()
                entry['description'] = _description('\n'.join(description))
                if len(entry['description']) > 10000:
                    fail()
                result['experiences' if kind == 'work' else 'education'].append(entry)
                entry, description = None, None
            else:
                fail()
        elif description is not None:
            description.append(line)
    if entry is not None or not ids or any(len(rows) > 20 for rows in result.values()):
        fail()
    return result


def history_facts(history):
    """Translation-invariant identity; titles, places and descriptions may translate."""
    return {kind: sorted((row['id'], row['company'], row['start'], row['end'] or '', row['current'])
                         for row in history[kind]) for kind in ('experiences', 'education')}
