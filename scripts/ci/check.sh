#!/usr/bin/env bash
set -euo pipefail
cd /app

printf '%s\n' 'Checking workflow and shell syntax'
actionlint .github/workflows/*.yml
shellcheck scripts/ci/*.sh docker/init-app-user.sh
python scripts/ci/release_notes.py

printf '%s\n' 'Compiling Python source and running regression tests'
python -m compileall -q hiring_scraper experiments scripts/ci tests
python -m unittest discover -s tests -v

printf '%s\n' 'Checking profile behavior with Node and the real Python matcher'
node tests/frontend_profile_defaults.mjs

printf '%s\n' 'Building the frontend with locked dependencies'
npm --prefix frontend run build
