#!/usr/bin/env python3
"""Generate and render a private application-document evaluation case."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from hiring_scraper.app import codex_runtime  # noqa: E402
from hiring_scraper.app.application_pdf import export_verified, render_document  # noqa: E402
from hiring_scraper.app.application_pipeline import (  # noqa: E402
    PIPELINE_VERSION,
    Generate,
    GenerationError,
    PreparationInput,
    StageHooks,
    prepare_documents,
)
from hiring_scraper.documents import import_docx, import_marked_tex  # noqa: E402


def private_path(path: Path) -> Path:
    private = ROOT / 'data' / 'private'
    resolved = path.resolve()
    if resolved == private or not resolved.is_relative_to(private):
        raise ValueError('Paths must resolve inside data/private.')
    return resolved


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object.')
    return data


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def cli_generate(model: str, workspace: Path) -> Generate:
    workspace = private_path(workspace)
    state = private_path(Path(os.environ.get('RADIUS_CODEX_STATE_DIR') or ROOT / 'data' / 'private' / 'codex'))

    def generate(prompt: str, schema: dict) -> dict:
        with TemporaryDirectory(prefix='codex-', dir=workspace) as directory:
            cwd = Path(directory)
            schema_file, response_file = cwd / 'schema.json', cwd / 'response.json'
            write_json(schema_file, schema)
            command = [
                *codex_runtime._command('exec'), '--ignore-user-config', '--ignore-rules',
                '--ephemeral', '--sandbox', 'read-only', '--skip-git-repo-check',
                '--json', '--color', 'never', '--model', model, '--output-schema', str(schema_file),
                '--output-last-message', str(response_file), '-c', 'approval_policy="never"',
                '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0',
                '-c', 'features.skip_host_skill_discovery=true',
                '-c', 'log_dir=' + json.dumps(str(cwd / 'logs')),
            ]
            for feature in codex_runtime._disabled:
                command += ['-c', f'features.{feature}=false']
            command.append('-')
            try:
                # CLI output can contain application text. Capture it in memory and report fixed codes.
                environment = codex_runtime._environment(state, cwd)
                environment['TMPDIR'] = str(cwd)
                result = subprocess.run(command, input=prompt, encoding='utf-8', errors='replace',
                                        cwd=cwd, env=environment,
                                        capture_output=True, timeout=300)
                if result.returncode:
                    error = (result.stderr[-65536:] + result.stdout[-65536:]).lower()
                    if 'model is not supported' in error or 'model_not_found' in error:
                        raise GenerationError('model_unavailable')
                    if any(marker in error for marker in (
                        'usage limit', 'usage_limit_reached', 'insufficient_quota',
                    )):
                        raise GenerationError('codex_usage_limit')
                    if '401 unauthorized' in error or 'not logged in' in error:
                        raise GenerationError('not_connected')
                    raise GenerationError('codex_exec_failed', retryable=True)
            except FileNotFoundError:
                raise GenerationError('codex_unavailable') from None
            except subprocess.TimeoutExpired:
                raise GenerationError('timeout', retryable=True) from None
            try:
                return read_json(response_file)
            except (OSError, ValueError):
                raise GenerationError('invalid_json', retryable=True) from None

    return generate


def run(args: argparse.Namespace) -> None:
    started = monotonic()
    case = read_json(private_path(args.case))
    job = case['job']
    profile = read_json(private_path(args.profile))
    out = private_path(args.out)
    base_path = private_path(args.base_tex or args.base_docx)
    language = case.get('language')
    base = (import_marked_tex(base_path.read_text(encoding='utf-8'), language=language) if args.base_tex
            else import_docx(base_path.read_bytes(), language=language))
    photo = private_path(args.photo).read_bytes() if args.photo else None
    prompts_dir = args.prompts_dir.resolve() if args.prompts_dir else None
    inp = PreparationInput(base.model_dump(), profile, job, base.language, args.mode, prompts_dir=prompts_dir)

    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    checkpoints = {}
    latest_render = {}

    def render(kind, body):
        latest = latest_render.get(kind)
        if latest is not None and latest[0] == body:
            return latest[1]
        result = render_document(kind, body, photo_png=photo if kind == 'cv' else None)
        latest_render[kind] = (body, result)
        return result

    def commit_stage(key, checkpoint, _outputs):
        if checkpoint is not None:
            checkpoints[key] = checkpoint

    hooks = StageHooks(checkpoints.get, commit_stage, lambda _: None, lambda: False)
    with TemporaryDirectory(prefix='.run-', dir=out) as directory:
        workspace = Path(directory)
        result = prepare_documents(inp, cli_generate(args.model, workspace), hooks, render)
        write_json(workspace / 'job.json', job)
        write_json(workspace / 'findings.json', result.findings)
        pages = {}
        verified = {}
        photo_used = False
        for kind, body in (('cv', result.cv), ('letter', result.letter)):
            output = render(kind, body)
            pages[kind] = output.pages
            verified[kind] = export_verified(output.checks)
            if kind == 'cv':
                photo_used = output.telemetry['photo_used']
            write_json(workspace / f'{kind}.json', body)
            (workspace / f'{kind}.pdf').write_bytes(output.pdf)
            (workspace / f'{kind}.txt').write_text(output.text, encoding='utf-8')
        telemetry = {
            **result.telemetry, 'stages': result.stages, 'base_path': str(base_path),
            'pipeline_version': PIPELINE_VERSION,
            'prompts_dir': str(prompts_dir) if prompts_dir else 'default',
            'model': args.model, 'mode': args.mode, 'pages': pages,
            'export_verified': verified, 'photo_used': photo_used,
            'seconds': round(monotonic() - started, 3),
            'finding_counts': dict(Counter(f['code'] for f in result.findings)),
        }
        write_json(workspace / 'telemetry.json', telemetry)
        for artifact in workspace.iterdir():
            artifact.chmod(0o600)
            artifact.replace(out / artifact.name)

    print(json.dumps({
        'out': str(out), 'pages': pages, 'export_verified': verified,
        'seconds': telemetry['seconds'], 'photo_used': photo_used,
        'stage_seconds': telemetry['stage_seconds'], 'attempts': telemetry['attempts'],
        'attempt_failures': telemetry.get('attempt_failures', {}),
        'stage_states': {name: s.get('state') for name, s in result.stages.items()},
        'applied_patches': len(telemetry['applied_patches']), 'finding_counts': telemetry['finding_counts'],
    }, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, type=Path)
    base = parser.add_mutually_exclusive_group(required=True)
    base.add_argument('--base-tex', type=Path)
    base.add_argument('--base-docx', type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--model', default='gpt-6.2-sol')
    parser.add_argument('--mode', choices=('full', 'fast'), default='full')
    parser.add_argument('--photo', type=Path)
    parser.add_argument('--prompts-dir', type=Path, default=None)
    try:
        run(parser.parse_args())
    except Exception as error:
        code = getattr(error, 'code', type(error).__name__)
        reasons = getattr(error, 'reasons', ()) or ()
        suffix = f': {", ".join(reasons)}' if reasons else ''
        print(f'Evaluation failed ({code}{suffix}).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
