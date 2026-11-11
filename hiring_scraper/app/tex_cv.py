"""Read explicit history markers as data; never execute uploaded TeX."""
import json
import re
from functools import lru_cache


@lru_cache(maxsize=128)
def _tex_language_pairs(source):
    """Read explicit language sections only; never execute or infer from duties."""
    from hiring_scraper.matching import LANGUAGE_PATTERNS, LANGUAGE_LEVEL
    if not isinstance(source, str) or len(source) > 100000:
        return ()
    source = re.sub(r'(?<!\\)%[^\n]*', '', source)
    sections = list(re.finditer(r'\\(?:cvsection|(?:sub)?section)\*?\s*\{([^{}\n]+)\}', source, re.I))
    found = {}
    allowed = {'textbf', 'emph', 'textit', 'textnormal', 'textsf', 'textrm', 'small',
               'footnotesize', 'normalsize', 'quad', 'qquad', 'hfill', 'hspace',
               'vspace', 'par', 'noindent', 'textbar'}
    for index, heading in enumerate(sections):
        # A heading inside a conditional or macro/group cannot establish visible
        # text without executing TeX. Keep this data-only fallback conservative.
        prefix = source[:heading.start()]
        depth = 0
        for command in re.findall(r'\\(?:if[A-Za-z]*|fi)\b', prefix):
            depth = max(0, depth - 1) if command == r'\fi' else depth + 1
        braces = re.sub(r'\\[{}]', '', prefix)
        if depth or braces.count('{') != braces.count('}'):
            continue
        if heading[1].strip().casefold() not in {'languages', 'language skills', 'sprachen', 'sprachkenntnisse', 'idiomas', 'langues'}:
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(source)
        document_end = source.find(r'\end{document}', heading.end())
        if document_end >= 0:
            end = min(end, document_end)
        text = re.sub(r'(?<!\\)%[^\n]*', '', source[heading.end():end])
        commands = set(re.findall(r'\\([A-Za-z]+)', text))
        if commands - allowed:
            continue  # Hidden conditionals/macros cannot establish a fact.
        text = re.sub(r'\\[A-Za-z]+\*?(?:\[[^\]]*\])?', ' ', text)
        text = re.sub(r'[{}~\\|&]', ' ', text)
        hits = sorted((hit.start(), hit.end(), name) for name, pattern in LANGUAGE_PATTERNS.items()
                      for hit in pattern.finditer(text))
        for offset, (_, stop, name) in enumerate(hits):
            next_start = hits[offset + 1][0] if offset + 1 < len(hits) else len(text)
            level = LANGUAGE_LEVEL.search(text[stop:next_start])
            if level:
                value = level.group().upper()
                value = value if re.fullmatch(r'[ABC][12]', value) else 'native'
                found.setdefault(name, set()).add(value)
    return tuple((name, next(iter(values))) for name, values in found.items() if len(values) == 1)


def parse_tex_languages(source):
    return dict(_tex_language_pairs(source))


def cv_language_fallback(cv, declared_levels):
    if cv is None:
        return {}
    variants = cv.variants or {}
    canonical = variants.get('en') or variants.get('de')
    source = canonical.get('text', '') if canonical else cv.extracted_text
    declared = {name.casefold() for name in (declared_levels or {})}
    return {name: level for name, level in parse_tex_languages(source).items() if name.casefold() not in declared}


def _description(text):
    text = re.sub(r'^\\newcommand\{\\Radius[A-Za-z]+\}\{(.*)\}\s*$', r'\1', text, flags=re.MULTILINE)
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
    profile, profile_seen = None, False
    def fail():
        raise ValueError('Invalid CV history markers. Use the CV formatting prompt; check IDs, dates and entry boundaries.')
    for line in source.splitlines():
        marker = re.fullmatch(r'\s*% radius:(.*)', line)
        if marker:
            token = marker[1]
            if token == 'profile':
                if entry is not None or profile is not None or profile_seen:
                    fail()
                profile, profile_seen = [], True
            elif token == 'endprofile':
                if profile is None:
                    fail()
                if len(_description('\n'.join(profile))) > 10000:
                    fail()
                profile = None
            elif token.startswith('entry '):
                if entry is not None or profile is not None:
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
        elif profile is not None:
            profile.append(line)
        elif description is not None:
            description.append(line)
    if entry is not None or profile is not None or not ids or any(len(rows) > 20 for rows in result.values()):
        fail()
    return result


def history_facts(history):
    """Translation-invariant identity; titles, places and descriptions may translate."""
    return {kind: sorted((row['id'], row['company'], row['start'], row['end'] or '', row['current'])
                         for row in history[kind]) for kind in ('experiences', 'education')}


def editable_macro_slots(source):
    """Return unique single-line macro bodies only when all editable content uses them."""
    parse_tex_cv(source)
    slots, editable = {}, False
    pattern = re.compile(r'^\\newcommand\{\\(Radius[A-Za-z]+)\}\{(.*)\}[ \t]*$')
    for line in source.splitlines():
        if re.fullmatch(r'\s*% radius:(?:responsibilities|profile)', line):
            editable = True
        elif re.fullmatch(r'\s*% radius:(?:end|endprofile)', line):
            editable = False
        elif editable and line.strip():
            match = pattern.fullmatch(line)
            if not match or match[1] in slots:
                return {}
            slots[match[1]] = match[2]
    return slots


def apply_macro_edits(source, edits, *, original=None):
    slots = editable_macro_slots(source)
    replacements = {}
    for edit in edits:
        name, value = edit['name'], edit['text']
        if name not in slots or name in replacements or '\n' in value or '\r' in value:
            raise ValueError('Invalid CV macro edits.')
        # Ignore escaped symbols while rejecting TeX comments, alignment and math
        # syntax that would alter or break the surrounding fixed layout.
        unescaped = re.sub(r'\\.', '', value)
        if re.search(r'[%&$#_^~]', unescaped):
            raise ValueError('Escape LaTeX special characters using the examples in the brief.')
        replacements[name] = value
    pattern = re.compile(r'^(\\newcommand\{\\(Radius[A-Za-z]+)\}\{)(.*)(\}[ \t]*)$')
    lines, editable = [], False
    for line in source.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        ending = line[len(body):]
        if re.fullmatch(r'\s*% radius:(?:responsibilities|profile)', body):
            editable = True
        elif re.fullmatch(r'\s*% radius:(?:end|endprofile)', body):
            editable = False
        elif editable:
            match = pattern.fullmatch(body)
            if match and match[2] in replacements:
                body = match[1] + replacements[match[2]] + match[4]
        lines.append(body + ending)
    result = ''.join(lines)
    validate_tailored_cv(original if original is not None else source, result)
    return result


def validate_tailored_cv(original, tailored):
    """Only marked description and profile prose may change; uploaded source is never executed."""
    if "^^" in tailored:
        raise ValueError("LaTeX character-code escapes are not supported in tailored CVs.")
    history = parse_tex_cv(tailored)
    base = parse_tex_cv(original)
    if history_facts(base) != history_facts(history):
        raise ValueError('Returned CV changed work/study facts or dates.')
    def skeleton(source):
        editable = False
        lines = []
        for line in source.replace('\r\n', '\n').rstrip('\n').split('\n'):
            if re.fullmatch(r'\s*% radius:(?:responsibilities|profile)', line):
                editable = True
            elif re.fullmatch(r'\s*% radius:(?:end|endprofile)', line):
                editable = False
            elif editable:
                line = re.sub(r'\\[&%$#_{}]', '', line)
                line = ''.join(re.findall(r'\\[A-Za-z]+|\\.|[{}]', line))
            lines.append(line)
        return '\n'.join(lines)
    if skeleton(original) != skeleton(tailored):
        raise ValueError('Returned CV changed its structure or layout. Edit only existing text inside marked responsibility or profile blocks.')
    macro = r'^\\newcommand\{(\\Radius[A-Za-z]+)\}\{(.*)\}[ \t]*$'
    before = re.findall(macro, original.replace('\r\n', '\n'), re.M)
    after = re.findall(macro, tailored.replace('\r\n', '\n'), re.M)
    if len(before) != len(after) or any(len(new[1]) > len(old[1]) for old, new in zip(before, after)):
        raise ValueError('Returned CV description exceeds its original text-length budget. Shorten it to fit the existing layout.')
    return history


@lru_cache(maxsize=128)
def _career_facts_json(source):
    try:
        facts = parse_tex_cv(source)
    except ValueError:
        return '{}'
    return json.dumps({kind: [{key: value for key, value in row.items()
                              if key not in ('id', 'cv_bullet_ids')} for row in rows]
                       for kind, rows in facts.items() if rows}, ensure_ascii=False)


def career_facts_from_tex(source):
    """Explicit work/study records only; return a fresh projection of cached data."""
    return json.loads(_career_facts_json(source))


def cv_career_fallback(cv):
    if cv is None:
        return {}
    variants = cv.variants or {}
    canonical = variants.get('en') or variants.get('de')
    source = canonical.get('text', '') if canonical else cv.extracted_text
    return career_facts_from_tex(source)
