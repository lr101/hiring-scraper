# Maintainer checklist

These settings require a repository administrator; committing workflow files does
not configure them automatically.

## Repository settings

- Protect `main` with a branch ruleset: require pull requests, at least one approval,
  and the **Container checks** status. Require resolved review conversations and
  block force pushes and branch deletion. Keep bypass access limited.
- Protect `v*` tags against updates and deletion and limit creation to release
  maintainers. Releases are created by pushing prepared tags, not through the
  GitHub release editor.
- Enable private vulnerability reporting under **Settings → Code security** and
  provide a private maintainer contact for security and conduct reports.
- Keep Actions' default workflow token permission read-only. The image publish job
  requests `packages: write` and the release job requests `contents: write` only
  where needed. Allow the pinned actions used by the workflow.
- Allow repository Actions to publish to its GHCR packages. Set package visibility
  to public if anonymous pulls are intended.
- Enable Dependabot alerts and security updates in addition to the committed
  weekly version-update configuration.
- Keep the MIT license notice with distributed software. Document third-party
  data terms separately; software licensing does not replace them.
- Fill in the repository description, topics, and website if applicable. Enable
  Discussions only if maintainers plan to support that channel.

GitHub documents [rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
and [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository).

## Review and release

Check the PR's behavior, test evidence, changelog entry or explanation, migration
requirements, and privacy of submitted fixtures. Use [the release guide](ci.md#preparing-a-release)
for versioning, tags, publication, and failure recovery. Do not move published
release tags; ship a new patch release for corrections.
