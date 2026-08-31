# Beauty integration example

The first Orcestr consumer is Beauty subscription checkout. A tenant owner selects a published
Beauty tariff and the ORCESTR payment option. The server creates a CommerceXL order from its own
catalogue price, then creates a Solana payment attempt; the client never posts amount, currency,
mint or recipient.

Each Beauty product keeps independent RUB, USD and ORCESTR price rows in the CommerceXL catalogue.
The selected currency is copied into the immutable order, so later admin edits affect only new
orders. The ORCESTR option uses `OrderSnapshotSettlementQuoteProvider` with the exact
`solana_orcestr -> ORCESTR` allowlist. It converts the frozen human ORCESTR decimal to six-decimal
raw units with `rounding="exact"`; no ORCESTR product price is read from environment variables.

The host wires Orcestr Auth actor, tenant ownership and CSRF dependencies into the add-on
FastAPI adapter. Commerce and Solana rows use the control database. A platform route is used so
Beauty-only tenants are not incorrectly gated by the Deliveries module.

The frontend mounts the Solana checkout UI inside the existing Beauty account/settings flow.
It receives the application's authenticated fetch, `QueryClient`, wallet-standard adapter and
shared WebSocket event source. Reconnect or a payment event invalidates the private order and
subscription queries; no `refetchInterval` is added.

Only backend state can display final success. Once a finalized payment is applied, the existing
subscription application service grants that plan's monthly credits exactly once and sets
automatic renewal to false. The same first integration also supports standalone AI credit packs:
their existing ledger effect is applied exactly once only after the Solana payment reaches the
required finality. Admin manual top-up remains a distinct privileged operation.

Neither flow interprets a missing redirect URL as a free payment. A checkout response always
contains an explicit typed action (`redirect` for T-Bank or `solana_transaction_request` for
wallet/QR checkout), and the product effect runs only after the corresponding CommerceXL payment
attempt is marked paid.
