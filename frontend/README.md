# Orcestr Commerce Solana frontend

TypeScript workspace for reusable Solana payment contracts, React Query integration, Wallet Standard support, and accessible Orcestr UI checkout components.

The packages accept native SOL and explicitly configured Token-2022 assets through backend-issued transaction requests. Version 0.1 has no static-transfer or legacy-token path. The packages do not contain private keys, create an authentication client, open a WebSocket, or poll payment status.

## Packages

- `@orcestr/commerce-solana-core` — runtime-validated API contracts, exact amount helpers, transports, Solana Pay URI handling, and transaction snapshot validation.
- `@orcestr/commerce-solana-react` — host-owned React Query integration, shared payment events, Wallet Standard discovery, and the built-in Solana Kit transaction inspector.
- `@orcestr/commerce-solana-ui` — controlled checkout, QR code, wallet and asset selectors, lifecycle states, and complete English/Russian catalogs.

## Development

From this directory:

```bash
npm install
npm run typecheck
npm test
npm run build
npm run pack:dry-run
```

Use `npm install`, not a global link, for normal development. Run this workspace build, temporarily use all three paths in the consumer, and run the consumer's `npm install` before starting its dev server:

```json
{
  "dependencies": {
    "@orcestr/commerce-solana-core": "file:../../orcestr-commerce-solana/frontend/packages/core",
    "@orcestr/commerce-solana-react": "file:../../orcestr-commerce-solana/frontend/packages/react",
    "@orcestr/commerce-solana-ui": "file:../../orcestr-commerce-solana/frontend/packages/ui"
  }
}
```

Supplying all three paths keeps exact internal `0.2.0` dependencies local. Restore registry versions before committing a release consumer.

Every package is ESM-only, ships declarations, and contains its own `LICENSE`, `NOTICE`, `TRADEMARKS.md`, and bilingual README in the npm tarball.

## First npm release

After `npm login` completes the browser authentication, verify and publish in dependency order:

```bash
npm test
npm run pack:dry-run
npm publish --workspace @orcestr/commerce-solana-core --access public
npm publish --workspace @orcestr/commerce-solana-react --access public
npm publish --workspace @orcestr/commerce-solana-ui --access public
```

Each `prepack` rebuilds its package and copies legal files into that package root. Later CI releases should use npm Trusted Publishing and the same core → react → UI order.
