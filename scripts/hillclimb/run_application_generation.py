#!/usr/bin/env python3
"""Run the deployed application-preparation workflow against a private scenario."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')
_JOB_FIELDS = {
    'title', 'company_name', 'description', 'url', 'location_text', 'salary',
    'work_arrangement', 'employment_type', 'schedule', 'is_remote', 'seniority',
    'department', 'date_posted',
}


@dataclass(frozen=True)
class Scenario:
    path: Path
    cv_path: Path
    data: dict


class RunProgress:
    """Record deployment progress and private diagnostic checkpoints for one run."""

    usage_limit_percent = 50

    def __init__(self, directory=None):
        self.stages = []
        self.directory = directory

    def __call__(self, stage):
        self.stages.append(stage)
        if self.directory is not None:
            _write_json(self.directory / 'progress.json', {'stage': stage, 'stages': self.stages})

    def load(self, checkpoint):
        return None

    def checkpoint(self, checkpoint, value):
        # A fresh hillclimb run must always exercise every generation pass.
        if self.directory is not None:
            folder = self.directory / 'checkpoints'
            folder.mkdir(mode=0o700, exist_ok=True)
            _write_json(folder / f'{checkpoint}.json', value)

    def cancelled(self):
        return False


def _identifier(value, label):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f'{label} must contain only letters, numbers, dots, underscores, or hyphens.')
    return value


def load_scenario(path) -> Scenario:
    path = Path(path).expanduser().resolve(strict=True)
    if path.stat().st_size > 1_000_000:
        raise ValueError('Scenario JSON must be 1 MB or smaller.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Scenario must be a version 1 JSON object.')
    _identifier(data.get('scenario_id'), 'scenario_id')

    cv_ref = data.get('cv_file')
    if not isinstance(cv_ref, str) or not cv_ref or Path(cv_ref).is_absolute():
        raise ValueError('cv_file must be a relative path beside the scenario JSON.')
    cv_path = (path.parent / cv_ref).resolve(strict=True)
    if not cv_path.is_relative_to(path.parent) or cv_path.suffix.lower() != '.tex':
        raise ValueError('cv_file must point to a .tex file inside the scenario directory.')
    if cv_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('CV files must be 5 MB or smaller.')

    profile_data = data.get('profile')
    if not isinstance(profile_data, dict):
        raise ValueError('profile must be a profile object.')
    if not isinstance(profile_data.get('name'), str) or not profile_data['name'].strip():
        raise ValueError('profile.name must be a nonempty string.')

    job = data.get('job')
    if not isinstance(job, dict) or not {'title', 'company_name', 'description', 'url'} <= set(job):
        raise ValueError('job must include title, company_name, description, and url.')
    if set(job) - _JOB_FIELDS:
        raise ValueError('job contains unsupported fields.')
    for key in ('title', 'company_name', 'url'):
        if not isinstance(job[key], str) or not job[key].strip():
            raise ValueError(f'job.{key} must be a nonempty string.')
    if job['description'] is not None and not isinstance(job['description'], str):
        raise ValueError('job.description must be text or null.')
    if not isinstance(data.get('language', 'en'), str) or data.get('language', 'en') not in {'en', 'de'}:
        raise ValueError('language must be en or de.')
    if not isinstance(data.get('application_notes', ''), str):
        raise ValueError('application_notes must be text.')
    if len(data.get('application_notes', '')) > 5000:
        raise ValueError('application_notes must be 5,000 characters or shorter.')
    if data.get('mode', 'full') not in {'full', 'fast'}:
        raise ValueError('mode must be full or fast.')
    return Scenario(path=path, cv_path=cv_path, data=data)


def result_directory(output_root, scenario_id, candidate, run_id):
    scenario_id = _identifier(scenario_id, 'scenario_id')
    candidate = _identifier(candidate, 'candidate')
    run_id = _identifier(run_id, 'run_id')
    root = Path(output_root).expanduser().absolute()
    if root.is_symlink():
        raise ValueError('The private output root cannot be a symlink.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    directory = root / scenario_id / candidate / run_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    directory.chmod(0o700)
    return directory


def _build_database(scenario, engine):
    from sqlalchemy.orm import Session
    from hiring_scraper.app import application_generation as generation, models, profiles

    models.Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    profile = profiles.create_profile(profiles.ProfileRequest.model_validate(scenario.data['profile']), session)
    cv_bytes = scenario.cv_path.read_bytes()
    profiles.store_profile_cv(session, profile['id'], scenario.cv_path.name, cv_bytes, scenario.data.get('language', 'en'))

    job_data = scenario.data['job']
    company = models.Company(source='hillclimb', source_id=scenario.data['scenario_id'], name=job_data['company_name'])
    feed = models.JobFeed(company=company, provider='hillclimb', status='parsed',
                          feed_url=job_data['url'], board_url=job_data['url'])
    fields = {key: value for key, value in job_data.items() if key not in {'company_name', 'title', 'description', 'url', 'location_text'}}
    job = models.Job(feed=feed, external_id=scenario.data['scenario_id'], title=job_data['title'],
                     description=job_data['description'], url=job_data['url'],
                     location_text=job_data.get('location_text'), **fields)
    session.add(job)
    session.commit()

    # A browser obtains revisions from a separate read of the saved records.
    # Round-trip here too: SQLite reloads timezone-aware values as naive values,
    # so in-memory upload timestamps cannot be used as stable revision tokens.
    session.expire_all()
    cv = profiles.require_cv(profile['id'], session)
    current_profile = profiles.require_profile(profile['id'], session)
    request = generation.PrepareApplicationRequest(
        job_id=job.id,
        cv_updated_at=profiles.cv_metadata(cv)['updated_at'],
        profile_updated_at=current_profile.updated_at.isoformat(),
        language=scenario.data.get('language', 'en'),
        notes=scenario.data.get('application_notes', ''),
        mode=scenario.data.get('mode', 'full'),
        model=scenario.data.get('model'),
    )
    return session, profile['id'], request


def _code_fingerprint():
    paths = (
        ROOT / 'hiring_scraper/app/application_generation.py',
        ROOT / 'hiring_scraper/app/application_prompt.txt',
        ROOT / 'hiring_scraper/app/codex_runtime.py',
        ROOT / 'hiring_scraper/app/application_documents.py',
        ROOT / 'hiring_scraper/app/cv_documents.py',
        ROOT / 'hiring_scraper/app/latex_documents.py',
        ROOT / 'hiring_scraper/app/latex_template.json',
        ROOT / 'hiring_scraper/app/profile_llm.py',
        ROOT / 'hiring_scraper/app/profiles.py',
        ROOT / 'hiring_scraper/app/requirement_evidence.py',
        ROOT / 'hiring_scraper/app/tex_cv.py',
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o600)


def run(scenario_path, *, output_root, candidate, run_id, mode=None, model=None):
    scenario = load_scenario(scenario_path)
    candidate = _identifier(candidate, 'candidate')
    run_id = _identifier(run_id, 'run_id')
    directory = result_directory(output_root, scenario.data['scenario_id'], candidate, run_id)
    started = datetime.now(timezone.utc).isoformat()
    progress = RunProgress(directory)
    result = {
        'scenario_id': scenario.data['scenario_id'], 'candidate': candidate, 'run_id': run_id,
        'started_at': started, 'code_fingerprint': _code_fingerprint(), 'status': 'running',
        'scenario_sha256': hashlib.sha256(scenario.path.read_bytes()).hexdigest(),
        'base_cv_sha256': hashlib.sha256(scenario.cv_path.read_bytes()).hexdigest(),
    }
    engine = None
    session = None
    try:
        selected_mode = mode or scenario.data.get('mode', 'full')
        if selected_mode not in {'full', 'fast'}:
            raise ValueError('mode must be full or fast.')
        selected_model = model if model is not None else scenario.data.get('model')
        result['requested_model'] = selected_model
        request_changes = {'mode': selected_mode}
        if selected_model is not None:
            request_changes['model'] = selected_model
        from fastapi import HTTPException
        from hiring_scraper.app import codex_runtime
        quota = codex_runtime.subscription_usage()
        if (not quota.get('available') or not quota.get('allowed') or
                quota.get('used_percent') is None or quota['used_percent'] >= progress.usage_limit_percent):
            raise HTTPException(429, 'Benchmark paused: subscription usage is unavailable or at its existing limit.')
        # In-memory records and temporary database are independent for each process.
        with tempfile.TemporaryDirectory(prefix='.fixture-', dir=directory) as scratch:
            from sqlalchemy import create_engine
            from hiring_scraper.app import application_generation as generation

            database_path = Path(scratch) / 'fixture.sqlite3'
            engine = create_engine(f'sqlite:///{database_path}', connect_args={'check_same_thread': False})
            session, profile_id, request = _build_database(scenario, engine)
            request = generation.PrepareApplicationRequest(**{**request.model_dump(), **request_changes})
            # The async job route calls this same worker after it has queued and
            # snapshotted the request. Keep the worker's queue/retry behavior here.
            response = generation._prepare_application(profile_id, request, session, progress=progress)
            _write_json(directory / 'application.json', response['package'])
        result.update({
            'status': 'completed', 'provider': response.get('provider'), 'model': response.get('model'),
            'mode': selected_mode, 'stages': progress.stages,
            'finished_at': datetime.now(timezone.utc).isoformat(),
            'package_file': 'application.json',
        })
        return_code = 0
    except Exception as error:
        result.update({
            'status': 'failed', 'mode': mode or scenario.data.get('mode', 'full'),
            'stages': progress.stages, 'error_type': type(error).__name__,
            'http_status': getattr(error, 'status_code', None),
            'error_detail': error.detail if getattr(error, 'status_code', None) is not None and isinstance(getattr(error, 'detail', None), str) else None,
            'finished_at': datetime.now(timezone.utc).isoformat(),
        })
        return_code = 1
    finally:
        if session is not None:
            session.close()
        if engine is not None:
            engine.dispose()
        _write_json(directory / 'run.json', result)
    return return_code, directory


def main(argv=None):
    default_output = Path(os.environ.get(
        'RADIUS_HILLCLIMB_RESULTS',
        str(Path.home() / '.local/state/radius/application-hillclimb'),
    ))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', required=True, help='Private scenario.json; CV path is relative to this file.')
    parser.add_argument('--candidate', required=True, help='Candidate label, unique per code variant.')
    parser.add_argument('--run-id', required=True, help='Unique run label; a result folder is never overwritten.')
    parser.add_argument('--output-root', default=str(default_output), help='Private artifact root (default: Radius local state).')
    parser.add_argument('--mode', choices=('full', 'fast'), help='Defaults to the scenario mode or full deployment workflow.')
    parser.add_argument('--model', help='Optional model override; defaults to Radius deployment setting.')
    args = parser.parse_args(argv)
    try:
        exit_code, directory = run(args.scenario, output_root=args.output_root, candidate=args.candidate,
                                   run_id=args.run_id, mode=args.mode, model=args.model)
    except FileExistsError:
        parser.error('That candidate/run-id result directory already exists; choose a new run-id.')
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(f'Could not start the benchmark ({type(error).__name__}). Check the scenario and private output path.')
    if exit_code:
        print(f'Application generation failed; private run metadata: {directory / "run.json"}', file=sys.stderr)
        return exit_code
    print(f'Application package: {directory / "application.json"}')
    print(f'Run metadata: {directory / "run.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
