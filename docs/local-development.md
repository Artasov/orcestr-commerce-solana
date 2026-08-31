# Local development

The repository is designed to sit beside `commercexl`, `orcestr`, `orcestr-auth` and
`orcestr-ui` in one development directory.

## Backend package

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv build
```

To test an unreleased checkout contract, install the neighboring CommerceXL worktree as an
editable dependency in the local environment only. Do not commit editable paths or generated
wheel files. The Orcestr consumer uses its helper and `ORCESTR_COMMERCE_SOLANA_LOCAL` to select
`../../orcestr-commerce-solana/backend`; its committed dependency remains a registry version.

## Frontend workspace

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run pack:dry-run
```

The consumer-side local-library selector replaces all three package dependencies together:
core, React and UI. Never mix a local package with registry versions of its internal peers.
Restore registry dependencies before committing the consumer manifest and lockfile.

## Configuration

Use devnet or a local validator for development. Keep RPC URLs, recipient wallets and feature
flags in host configuration, not package source. A development asset must still be a real
Token-2022 mint registered by exact address and verified on the selected cluster.

No server or merchant private key is needed for normal checkout. Browser wallet extensions
remain user-controlled. Automated tests use deterministic serialized fixtures and local
signers that contain no production material.
