"""Career-fact projection of a saved profile for evidence matching.

Only work history, saved work evidence, education, skills and roles are used. Contact details,
drafting preferences and application history never reach the matcher.
"""
from __future__ import annotations


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def profile_sources(profile: dict) -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    cv_facts = profile.get('cv_career_facts') or {}
    for kind in ('experiences', 'education'):
        for index, entry in enumerate((cv_facts.get(kind) or [])[:40]):
            if not isinstance(entry, dict):
                continue
            title = _text(entry.get('title'))
            description = _text(entry.get('description'))[:10000]
            if title:
                sources.append((f'cv.{kind}[{index}].title', title))
            if description:
                sources.append((f'cv.{kind}[{index}]', f'{title}: {description}' if title else description))
    for index, entry in enumerate(((profile.get('application_details') or {}).get('experiences') or [])[:40]):
        if isinstance(entry, dict) and _text(entry.get('description')):
            title = _text(entry.get('title'))
            text = _text(entry.get('description'))
            sources.append((f'experience[{index}]', f'{title}: {text}' if title else text))
    if _text(profile.get('summary')):
        sources.append(('summary', _text(profile.get('summary'))))
    # Work evidence the user saved on the Profile page (quotes of what they actually did).
    for index, entry in enumerate((profile.get('duty_evidence') or [])[:20]):
        quote = _text(entry.get('quote')) if isinstance(entry, dict) else ''
        if quote:
            reference = _text(entry.get('reference'))
            sources.append((f'duty_evidence[{index}]', f'{reference}: {quote}' if reference else quote))
    for name, evidence in (profile.get('skill_evidence') or {}).items():
        note = evidence.get('note') if isinstance(evidence, dict) else None
        if isinstance(note, str) and note.strip():
            sources.append((f'skill_evidence.{name}', f'{name}: {note.strip()}'))
    for row in profile.get('content_sources') or []:
        if isinstance(row, (list, tuple)) and len(row) == 2 and _text(row[1]):
            sources.append((str(row[0]), _text(row[1])))
    for entry in profile.get('education') or []:
        if isinstance(entry, dict):
            parts = [_text(entry.get(key)) for key in ('level', 'field', 'status') if _text(entry.get(key))]
            if parts:
                sources.append(('education', ' '.join(parts)))
    for entry in profile.get('certifications') or []:
        text = _text(entry if isinstance(entry, str) else (entry or {}).get('name') if isinstance(entry, dict) else '')
        if text:
            sources.append(('certifications', text))
    skills = [skill for skill in profile.get('skills') or [] if isinstance(skill, str) and skill.strip()]
    for start in range(0, len(skills), 6):
        sources.append(('skills', 'Skills: ' + ', '.join(skills[start:start + 6])))
    return tuple(dict.fromkeys(sources))


def profile_roles(profile: dict) -> list[str]:
    """Desired roles first, then secondary roles and past job titles."""
    roles = [_text(role) for role in (profile.get('desired_roles') or []) if _text(role)]
    roles += [_text(role) for role in (profile.get('secondary_roles') or []) if _text(role)]
    for entry in ((profile.get('cv_career_facts') or {}).get('experiences') or [])[:12]:
        if isinstance(entry, dict) and _text(entry.get('title')):
            roles.append(_text(entry.get('title')))
    return list(dict.fromkeys(roles))[:24]
