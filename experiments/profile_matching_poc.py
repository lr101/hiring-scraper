"""Repeatable fixture evaluation; optional live detail hydration, no real user data.

DATABASE_URL=sqlite:////tmp/profile-poc.sqlite3 .venv/bin/python -m experiments.profile_matching_poc \
  --output reports/profile-matching-poc-results.json [--fetch-details]
"""
import argparse
import hashlib
from collections import Counter
import json
from pathlib import Path
import re
import time

from sqlalchemy import select
from hiring_scraper.app import api
from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.enrichment import backfill
from hiring_scraper.app.models import Job
from hiring_scraper.app.profiles import ProfileRequest, create_profile, list_profiles
from hiring_scraper.app.seed import FIXTURES, import_fixture

PROFILES = [
    {'name': 'Demo · Software development', 'skills': ['Python', 'Java', 'SQL', 'Docker', 'Kubernetes', 'Git'],
     'desired_roles': ['Software developer', 'Backend'], 'experience_years': 5,
     'languages': ['German','English'], 'excluded_terms': ['Praktikum','Werkstudent','Ausbildung','Intern']},
    {'name': 'Demo · Accounting', 'skills': ['Accounting','Financial reporting','DATEV','Excel','SAP'],
     'desired_roles': ['Accounting','Buchhaltung'], 'experience_years': 5,
     'languages': ['German','English'], 'excluded_terms': ['Praktikum','Werkstudent','Ausbildung','Intern']},
]


def scope(session, **kwargs):
    return api.list_jobs(49.0068705,8.4034195,15,None,None,'Karlsruhe',0,10000,session=session,**kwargs)


def evaluate():
    with SessionLocal() as session:
        jobs = session.scalars(select(Job)).all()
        coverage = {'jobs':len(jobs)}
        for field in ['description','employment_type','seniority','salary','work_arrangement']:
            coverage[field] = sum(bool(getattr(job,field)) for job in jobs)
        coverage['substantial_descriptions'] = sum(len(job.description or '') >= 350 for job in jobs)
        coverage['recognized_skills'] = sum(bool(job.enrichment.get('skills')) for job in jobs)
        coverage['inferred_levels'] = sum(bool(job.enrichment.get('seniority')) for job in jobs)
        coverage['explicit_minimum_experience'] = sum(bool(job.enrichment.get('experience_years')) for job in jobs)
        area = scope(session)
        results = {'coverage':coverage, 'area_jobs':area['total'], 'profiles':[]}
        for profile in list_profiles(session)['items']:
            started = time.monotonic()
            page = scope(session, profile_id=profile['id'])
            elapsed = round(time.monotonic()-started,3)
            def keyword(job):
                text = job['title'].casefold() + ' ' + str(job.get('description') or job.get('description_preview') or '').casefold()
                return sum(bool(re.search(r'\b'+re.escape(term.casefold())+r'\b',text)) for term in profile['skills']+profile['desired_roles'])
            # Baseline sees the same full source descriptions, not truncated previews.
            descriptions={job.id:job.description for job in jobs}
            baseline = sorted([{**job,'description':descriptions[job['id']]} for job in area['items']],key=lambda job:(-keyword(job),job['id']))[:10]
            def compact(job):
                match=job.get('profile_match') or {}
                return {'id':job['id'],'title':job['title'],'company':job['company_name'],
                        'score':match.get('score'),'skills':match.get('matched_skills'),
                        'uncertain':match.get('uncertain'),'reasons':match.get('reasons')}
            results['profiles'].append({'name':profile['name'],'eligible_jobs':page['total'],
                'strong_overlap':sum(job['profile_match']['score']>=65 for job in page['items']),
                'some_overlap':sum(job['profile_match']['score']>=30 for job in page['items']),
                'uncertain_jobs':sum(job['profile_match']['uncertain'] for job in page['items']),
                'query_seconds':elapsed,'keyword_top_10':[compact(job) for job in baseline],
                'evidence_top_10':[compact(job) for job in page['items'][:10]]})
        return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('reports/profile-matching-poc-results.json'))
    parser.add_argument('--fetch-details',action='store_true')
    parser.add_argument('--replay-details',type=Path,help='Replay a directory of existing HTTP captures without live requests')
    args=parser.parse_args()
    # Only use an isolated test DB: this script imports the dated research fixture.
    from hiring_scraper.app.database import DATABASE_URL
    if not DATABASE_URL.startswith('sqlite:////tmp/'):
        parser.error('Use an isolated DATABASE_URL=sqlite:////tmp/... database')
    from sqlalchemy.engine import make_url
    database_path=Path(make_url(DATABASE_URL).database)
    if database_path.exists():
        parser.error('Choose a fresh /tmp database path for a reproducible fixture evaluation')
    imported=import_fixture(FIXTURES/'karlsruhe-osm-candidates.csv',FIXTURES/'karlsruhe-career-enrichment.json')
    with SessionLocal() as session:
        names={p['name'] for p in list_profiles(session)['items']}
        for profile in PROFILES:
            if profile['name'] not in names: create_profile(ProfileRequest(**profile),session)
    from hiring_scraper import matching
    results={'rule_version':matching.VERSION,
        'matching_source_sha256':hashlib.sha256(Path(matching.__file__).read_bytes()).hexdigest(),
        'fixture':imported,'before':evaluate()}
    if args.fetch_details:
        client=None
        if args.replay_details:
            from hiring_scraper.http import Client
            client=Client('/tmp/hiring-profile-enrichment-replay',cache_from=args.replay_details,offline_only=True)
        results['detail_pass']=backfill(20,fetch_details=True,client=client)
        results['after']=evaluate()
        with SessionLocal() as session:
            results['detail_examples']=[{'id':job.id,'title':job.title,'description_chars':len(job.description or ''),
                'method':job.raw_metadata.get('description_method'),
                'status':job.raw_metadata['detail_enrichment_attempt']['status'],
                'http_state':job.raw_metadata['detail_enrichment_attempt']['http_state']}
                for job in session.scalars(select(Job).order_by(Job.id)).all() if job.raw_metadata.get('detail_enrichment_attempt')]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'coverage':results['before']['coverage'],
                      'detail_pass':results.get('detail_pass')},ensure_ascii=False))


if __name__=='__main__': main()
