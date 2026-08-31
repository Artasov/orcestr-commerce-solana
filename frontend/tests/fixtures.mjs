export const INTENT_ID = "00000000-0000-4000-8000-000000000001";
export const PAYMENT_ID = "00000000-0000-4000-8000-000000000002";
export const ORDER_ID = "00000000-0000-4000-8000-000000000003";
export const MINT = "HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump";
export const RECIPIENT = "11111111111111111111111111111111";
export const REFERENCE = "SysvarRent111111111111111111111111111111111";
export const SIGNATURE =
  "5ikgUnvJVKrj2EHsQr63szVouh8qenbMUsQXGrbnYbBECMjffoQ7WFZX4h7bM4aAtdbNrh4L6pTxd6xCV9h5Rpns";
export const TOKEN_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

export function settlement() {
  return {
    cluster: "mainnet-beta",
    genesis_hash: "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    asset_option_id: "orcestr_mainnet",
    kind: "token",
    asset_name: "Orcestr",
    asset_symbol: "ORCESTR",
    mint: MINT,
    token_program: TOKEN_PROGRAM,
    decimals: 6,
    recipient_wallet: RECIPIENT,
    recipient_policy_version: "beauty-tenant-wallet:v1",
    recipient_token_account: RECIPIENT,
    expected_raw_amount: "2500000000",
    display_amount: "2500",
    reference: REFERENCE,
    required_commitment: "finalized",
    quote: {
      source: "fixed_orcestr_credit_tariff",
      version: "beauty-v1",
      commercial_amount: "1000.00",
      commercial_currency: "RUB",
      rate_numerator: "5",
      rate_denominator: "2",
      rounding: "down",
    },
    memo: null,
  };
}

export function intent(overrides = {}) {
  return {
    public_id: INTENT_ID,
    payment_public_id: PAYMENT_ID,
    order_public_id: ORDER_ID,
    state: "waiting",
    settlement: settlement(),
    action: {
      kind: "solana_transaction_request",
      uri: "solana:https://pay.example.com/transaction?capability=secret",
      expires_at: "2026-08-31T10:05:00Z",
    },
    candidate_signature: null,
    verified_signature: null,
    reason_code: null,
    revision: 3,
    expires_at: "2026-08-31T10:15:00Z",
    created_at: "2026-08-31T10:00:00Z",
    updated_at: "2026-08-31T10:01:00Z",
    ...overrides,
  };
}
