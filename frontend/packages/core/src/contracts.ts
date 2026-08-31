export const TOKEN_2022_PROGRAM_ADDRESS =
  "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" as const;

export type SolanaCluster = "mainnet-beta" | "devnet" | "testnet";

export const SOLANA_GENESIS_HASHES: Readonly<Record<SolanaCluster, string>> = {
  "mainnet-beta": "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
  devnet: "EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
  testnet: "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY",
};

export type SolanaPaymentStatus =
  | "preparing"
  | "waiting"
  | "observed"
  | "confirmed"
  | "paid"
  | "expired"
  | "cancelled"
  | "failed"
  | "review";

export type KnownSolanaPaymentReasonCode =
  | "invalid_address"
  | "invalid_amount"
  | "invalid_configuration"
  | "wrong_cluster"
  | "rpc_temporarily_unavailable"
  | "rpc_invalid_response"
  | "asset_not_found"
  | "asset_inactive"
  | "wrong_mint_owner"
  | "legacy_token_program"
  | "unsupported_token_extension"
  | "unsafe_mint_authority"
  | "unsafe_freeze_authority"
  | "invalid_token_account"
  | "capability_invalid"
  | "capability_expired"
  | "intent_expired"
  | "transaction_not_found"
  | "transaction_failed"
  | "transaction_not_final"
  | "transaction_decode_failed"
  | "signature_mismatch"
  | "signature_already_used"
  | "issuance_mismatch"
  | "wrong_program"
  | "wrong_mint"
  | "wrong_recipient"
  | "wrong_amount"
  | "wrong_decimals"
  | "wrong_decimals_in_instruction"
  | "invalid_balance_delta"
  | "multiple_payment_instructions"
  | "reference_missing"
  | "unsupported_instruction"
  | "unsupported_cpi_or_swap"
  | "late_payment"
  | "block_time_unavailable"
  | "wallet_rejected"
  | "wallet_not_found"
  | "wallet_network_mismatch"
  | "wallet_unsupported_transaction_version"
  | "verification_pending";

export type SolanaPaymentReasonCode =
  | KnownSolanaPaymentReasonCode
  | (string & { readonly __unknownSolanaReasonCode: unique symbol });

export type DecimalAmountString = string & {
  readonly __decimalAmount: unique symbol;
};

export type RawAmountString = string & {
  readonly __rawAmount: unique symbol;
};

export type Token2022Asset = {
  readonly kind: "token";
  readonly mint: string;
  readonly tokenProgram: typeof TOKEN_2022_PROGRAM_ADDRESS;
  readonly decimals: number;
  readonly symbol: string;
  readonly name: string | null;
};

export type NativeSolAsset = {
  readonly kind: "native";
  readonly mint: null;
  readonly tokenProgram: null;
  readonly decimals: 9;
  readonly symbol: "SOL";
  readonly name: string | null;
};

export type SolanaSettlementAsset = Token2022Asset | NativeSolAsset;

export type SolanaRequiredCommitment = "finalized";

export type SolanaSettlementQuote = {
  readonly source: string;
  readonly version: string;
  readonly commercialAmount: DecimalAmountString;
  readonly commercialCurrency: string;
  readonly rateNumerator: RawAmountString | null;
  readonly rateDenominator: RawAmountString | null;
  readonly rounding: "down" | "half_up" | "up";
};

export type SolanaPaymentOption = {
  readonly id: string;
  readonly label: string;
  readonly symbol: string;
  readonly decimals: number;
  readonly mint: string | null;
  readonly minimumRawAmount: RawAmountString;
  readonly maximumRawAmount: RawAmountString;
  readonly requiredCommitment: SolanaRequiredCommitment;
};

export type SolanaSettlementSnapshot = {
  readonly cluster: SolanaCluster;
  readonly genesisHash: string;
  readonly assetOptionId: string;
  readonly kind: "native" | "token";
  readonly assetName: string;
  readonly assetSymbol: string;
  readonly mint: string | null;
  readonly tokenProgram: typeof TOKEN_2022_PROGRAM_ADDRESS | null;
  readonly decimals: number;
  readonly recipientWallet: string;
  readonly recipientPolicyVersion: string;
  readonly recipientTokenAccount: string | null;
  readonly expectedRawAmount: RawAmountString;
  readonly displayAmount: DecimalAmountString;
  readonly reference: string;
  readonly requiredCommitment: SolanaRequiredCommitment;
  readonly quote: SolanaSettlementQuote;
  readonly memo: string | null;
};

export type SolanaTransactionRequestAction = {
  readonly kind: "solana_transaction_request";
  readonly uri: string;
  readonly expiresAt: string;
};

export type SolanaCheckoutAction = SolanaTransactionRequestAction;

export type SolanaPaymentIntent = {
  readonly publicId: string;
  readonly paymentPublicId: string;
  readonly orderPublicId: string;
  readonly revision: number;
  readonly state: SolanaPaymentStatus;
  readonly reasonCode: SolanaPaymentReasonCode | null;
  readonly settlement: SolanaSettlementSnapshot;
  readonly candidateSignature: string | null;
  readonly verifiedSignature: string | null;
  readonly action: SolanaCheckoutAction | null;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly expiresAt: string;
};

export type SolanaPaymentOptionsResponse = {
  readonly options: readonly SolanaPaymentOption[];
};

export type CreateSolanaPaymentIntentInput = {
  readonly orderPublicId: string;
  readonly paymentOptionId: string;
  readonly idempotencyKey: string;
};

export type SubmitCandidateSignatureInput = {
  readonly paymentPublicId: string;
  readonly signature: string;
};

export type CancelSolanaPaymentIntentInput = {
  readonly paymentPublicId: string;
  readonly reason: string;
  readonly idempotencyKey: string;
};

export type SolanaPaymentUpdatedEvent = {
  readonly event: "commerce.payment.updated";
  readonly orderPublicId: string;
  readonly paymentPublicId: string;
  readonly revision: number;
};

export type TransactionRequestInfo = {
  readonly label: string;
  readonly icon: string | null;
};

export type TransactionRequestPayload = {
  readonly transaction: string;
  readonly message: string | null;
};

export type InspectedSolanaTransfer = {
  readonly instructionLevel: "top_level" | "inner";
  readonly assetKind: "native" | "token";
  readonly tokenProgram: string | null;
  readonly mint: string | null;
  readonly sourceTokenAccount: string | null;
  readonly recipientAddress: string | null;
  readonly recipientTokenAccount: string | null;
  readonly rawAmount: RawAmountString;
  readonly decimals: number;
};

export type InspectedSolanaTransaction = {
  readonly transactionVersion: 0;
  readonly feePayer: string;
  readonly signerAddresses: readonly string[];
  readonly referenceAddresses: readonly string[];
  readonly issuanceMemo: string;
  readonly memo: string | null;
  readonly transfers: readonly InspectedSolanaTransfer[];
};

export type SolanaTransactionInspector = (
  serializedTransaction: Uint8Array,
) => Promise<InspectedSolanaTransaction> | InspectedSolanaTransaction;

export type TransactionValidationSnapshot = {
  readonly payerAddress: string;
  readonly sourceTokenAccount: string | null;
  readonly settlement: SolanaSettlementSnapshot;
};
