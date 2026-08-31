# Release process

Python and npm packages have independent versions. Stable tags are:

- `python-vX.Y.Z` for `orcestr-commerce-solana`;
- `commerce-solana-core-vX.Y.Z` for `@orcestr/commerce-solana-core`;
- `commerce-solana-react-vX.Y.Z` for `@orcestr/commerce-solana-react`;
- `commerce-solana-ui-vX.Y.Z` for `@orcestr/commerce-solana-ui`.

Before tagging, merge a clean pull request to `main`, run the targeted backend/frontend checks,
inspect every wheel, sdist and npm tarball, confirm exact internal npm dependency versions and
update changelogs. Release workflows verify that a tag version matches its manifest and publish
only artifacts built from that tag. The Python workflow checks metadata, imports the built wheel
from an isolated environment, and promotes those exact verified distributions to the publish job.
The npm workflow refuses to release a package until each
exact `@orcestr/commerce-solana-*` dependency in its manifest already exists in the registry.

For a later version bump, run the release helper with a package name and
`<patch|minor|major>` on a feature/fix branch, for example
`node scripts/release.mjs core patch`. The helper only updates manifests and lockfiles; it never
commits, pushes or tags. Review the generated diff and merge it through a pull request, then
create the release tag on the exact merged commit.

## First npm release

The first version of each npm package is published manually from the merged commit because the
package must exist before its Trusted Publisher can be configured. Authenticate with
`npm login --auth-type=web`, build the workspace, repeat `npm pack --dry-run`, and publish in
dependency order: core, React, UI. Each command uses `--access public`. Create and push the
matching signed/annotated tags only for the exact published commit.

The npm workflow treats an already-existing matching package version as an idempotent first
release and does not try to overwrite it.

## Trusted Publishing

After the packages exist, configure an npm Trusted Publisher for repository
`Artasov/orcestr-commerce-solana`, workflow `npm-release.yml`, and environment `npm` for every
package. The workflow runs on GitHub-hosted Node 24, requests `id-token: write`, uses current
npm 11 and publishes with provenance. Do not add a long-lived npm token.

Before the Python project exists, create a pending PyPI Trusted Publisher with owner `Artasov`,
repository `orcestr-commerce-solana`, workflow `release.yml`, and environment `pypi`. Do this
before pushing a Python tag. GitHub releases are created only after the corresponding registry
artifact is present.

The GitHub `pypi` and `npm` environments accept deployments from release tags only
(`python-v*` and `commerce-solana-*-v*`). If a failed release is re-run through
`workflow_dispatch`, start that run from the existing release tag rather than from `main`.
