"""Strict subscription quota interpretation; unknown windows never grant access."""
import math
import time


class UsageUnavailable(ValueError):
    pass


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def evaluate(payload, limit_percent=50):
    result = {'available': False, 'allowed': False, 'reason': 'usage_unavailable',
              'used_percent': None, 'resets_at': None, 'window_minutes': 300,
              'limit_percent': limit_percent}
    if not isinstance(payload, dict):
        return result
    buckets = []
    legacy = payload.get('rateLimits')
    mapped = payload.get('rateLimitsByLimitId')
    if legacy is not None:
        if not isinstance(legacy, dict):
            return result
        buckets.append((legacy.get('limitId') or 'codex', legacy))
    if mapped is not None:
        if not isinstance(mapped, dict):
            return result
        buckets.extend(mapped.items())
    applicable = []
    for identifier, bucket in buckets:
        if not isinstance(identifier, str) or not isinstance(bucket, dict):
            return result
        if bucket.get('limitId') not in (None, identifier):
            return result
        if identifier in ('codex', 'luna', 'codex-luna', 'gpt-6-luna'):
            applicable.append(bucket)
        elif identifier.startswith('gpt-') and 'luna' not in identifier:
            # Explicit limits for another model cannot describe Luna usage.
            continue
        else:
            # An undocumented pool might also constrain this model.
            return result
    if not applicable:
        result['reason'] = 'five_hour_window_unavailable'
        return result
    five_hour = []
    exhausted = False
    now = time.time()
    for bucket in applicable:
        if all(bucket.get(name) is None for name in ('primary', 'secondary')):
            return result
        for name in ('primary', 'secondary'):
            window = bucket.get(name)
            if window is None:
                continue
            if not isinstance(window, dict):
                return result
            used, minutes, resets = (window.get(key) for key in
                                    ('usedPercent', 'windowDurationMins', 'resetsAt'))
            if (not _number(used) or not 0 <= used <= 100 or
                    type(minutes) is not int or minutes <= 0 or
                    not _number(resets) or resets <= now):
                return result
            if minutes == 300:
                five_hour.append((used, resets))
            elif minutes > 300 and used >= 100:
                exhausted = True
    if not five_hour:
        result['reason'] = 'five_hour_window_unavailable'
        return result
    used, resets = max(five_hour, key=lambda entry: (entry[0], entry[1]))
    allowed = used < limit_percent and not exhausted
    result.update(available=True, allowed=allowed, reason='ready' if allowed else 'usage_limit_reached',
                  used_percent=used, resets_at=resets)
    return result
