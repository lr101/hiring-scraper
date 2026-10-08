# Changelog

Notable changes to Radius are recorded here. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The application
version lives in `pyproject.toml`; the private frontend package has its own version.

This changelog starts with the repository maintenance setup. Earlier development
is available in the [commit history](https://github.com/lr101/hiring-scraper/commits/main/);
no historical releases have been reconstructed.

## [Unreleased]

### Added

- Contributor guide, community conduct policy, security reporting guidance, and structured bug and feature request forms.
- Changelog validation and GitHub Releases using curated notes after container checks and application image publication succeed.
- Maintainer checklist for release preparation and GitHub repository settings.
- MIT license for the software, with third-party data terms preserved.

### Changed

- Fresh Compose deployments start with an empty database; searches begin after a user adds a location in the app.

### Fixed

- Scheduled job refreshes revisit saved feeds and career pages instead of repeating company and homepage discovery; failed fetches retry the saved source with backoff.

- Feed scans import the SQLAlchemy relationship loader used to load existing job locations.
