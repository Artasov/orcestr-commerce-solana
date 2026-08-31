import {
  SOLANA_GENESIS_HASHES,
  TOKEN_2022_PROGRAM_ADDRESS,
  type SolanaCheckoutAction,
  type SolanaCluster,
  type SolanaPaymentIntent,
  type SolanaPaymentOption,
  type SolanaPaymentOptionsResponse,
  type SolanaPaymentReasonCode,
  type SolanaPaymentStatus,
  type SolanaPaymentUpdatedEvent,
  type SolanaRequiredCommitment,
  type SolanaSettlementQuote,
  type SolanaSettlementSnapshot,
  type TransactionRequestInfo,
  type TransactionRequestPayload,
} from "./contracts.js";
import {
  compareRawAmounts,
  parseDecimalAmount,
  parseRawAmount,
  rawAmountToDecimal,
} from "./amounts.js";
import { parseSolanaPayUri } from "./uri.js";
import {
  invalid,
  readAddress,
  readArray,
  readHttpsUrl,
  readInteger,
  readIsoTimestamp,
  readLiteral,
  readRecord,
  readSignature,
  readString,
  readUuid,
} from "./validation.js";

const CLUSTERS = ["mainnet-beta", "devnet", "testnet"] as const;
const STATUSES = [
  "preparing",
  "waiting",
  "observed",
  "confirmed",
  "paid",
  "expired",
  "cancelled",
  "failed",
  "review",
] as const;
const COMMITMENTS = ["finalized"] as const;
const ROUNDING_MODES = ["exact", "down", "half_up", "up"] as const;
const MAX_U64 = 18_446_744_073_709_551_615n;
const RECIPIENT_POLICY_VERSION_PATTERN =
  /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$/u;

export function parseSolanaCluster(value: unknown, path = "cluster"): SolanaCluster {
  return readLiteral(value, CLUSTERS, path);
}

export function parseSolanaPaymentStatus(
  value: unknown,
  path = "state",
): SolanaPaymentStatus {
  return readLiteral(value, STATUSES, path);
}

export function parseSolanaPaymentOption(value: unknown): SolanaPaymentOption {
  const data = readRecord(value, "payment_option");
  const decimals = readDecimals(data.decimals, "payment_option.decimals");
  const mint =
    data.mint === null ? null : readAddress(data.mint, "payment_option.mint");
  const minimumRawAmount = parsePositiveU64RawAmount(
    data.minimum_raw_amount,
    "payment_option.minimum_raw_amount",
  );
  const maximumRawAmount = parsePositiveU64RawAmount(
    data.maximum_raw_amount,
    "payment_option.maximum_raw_amount",
  );
  if (compareRawAmounts(minimumRawAmount, maximumRawAmount) > 0) {
    invalid(
      "payment_option",
      "minimum_raw_amount less than or equal to maximum_raw_amount",
      value,
    );
  }
  if (mint === null && decimals !== 9) {
    invalid("payment_option", "native SOL option with 9 decimals", value);
  }
  return {
    id: readString(data.id, "payment_option.id"),
    label: readString(data.label, "payment_option.label"),
    symbol: readString(data.symbol, "payment_option.symbol"),
    decimals,
    mint,
    minimumRawAmount,
    maximumRawAmount,
    requiredCommitment: parseCommitment(
      data.required_commitment,
      "payment_option.required_commitment",
    ),
  };
}

export function parseSolanaPaymentOptionsResponse(
  value: unknown,
): SolanaPaymentOptionsResponse {
  const data = readRecord(value, "payment_options");
  return {
    options: readArray(data.options, "payment_options.options").map((item) =>
      parseSolanaPaymentOption(item),
    ),
  };
}

export function parseSolanaPaymentIntent(value: unknown): SolanaPaymentIntent {
  const data = readRecord(value, "payment_intent");
  return {
    publicId: readUuid(data.public_id, "payment_intent.public_id"),
    paymentPublicId: readUuid(
      data.payment_public_id,
      "payment_intent.payment_public_id",
    ),
    orderPublicId: readUuid(
      data.order_public_id,
      "payment_intent.order_public_id",
    ),
    revision: readInteger(data.revision, "payment_intent.revision"),
    state: parseSolanaPaymentStatus(data.state, "payment_intent.state"),
    reasonCode: parseReasonCode(data.reason_code, "payment_intent.reason_code"),
    settlement: parseSolanaSettlementSnapshot(data.settlement),
    candidateSignature: parseNullableSignature(
      data.candidate_signature,
      "payment_intent.candidate_signature",
    ),
    verifiedSignature: parseNullableSignature(
      data.verified_signature,
      "payment_intent.verified_signature",
    ),
    action:
      data.action === undefined || data.action === null
        ? null
        : parseCheckoutAction(data.action),
    createdAt: readIsoTimestamp(data.created_at, "payment_intent.created_at"),
    updatedAt: readIsoTimestamp(data.updated_at, "payment_intent.updated_at"),
    expiresAt: readIsoTimestamp(data.expires_at, "payment_intent.expires_at"),
  };
}

export function parseSolanaSettlementSnapshot(
  value: unknown,
): SolanaSettlementSnapshot {
  const path = "payment_intent.settlement";
  const data = readRecord(value, path);
  const cluster = parseSolanaCluster(data.cluster, `${path}.cluster`);
  const genesisHash = readString(data.genesis_hash, `${path}.genesis_hash`);
  if (genesisHash !== SOLANA_GENESIS_HASHES[cluster]) {
    invalid(
      `${path}.genesis_hash`,
      `the canonical ${cluster} genesis hash`,
      genesisHash,
    );
  }
  const kind = readLiteral(data.kind, ["native", "token"], `${path}.kind`);
  const decimals = readDecimals(data.decimals, `${path}.decimals`);
  const mint = data.mint === null ? null : readAddress(data.mint, `${path}.mint`);
  const tokenProgram =
    data.token_program === null
      ? null
      : readLiteral(
          data.token_program,
          [TOKEN_2022_PROGRAM_ADDRESS],
          `${path}.token_program`,
        );
  const recipientTokenAccount =
    data.recipient_token_account === null
      ? null
      : readAddress(data.recipient_token_account, `${path}.recipient_token_account`);
  const expectedRawAmount = parsePositiveU64RawAmount(
    data.expected_raw_amount,
    `${path}.expected_raw_amount`,
  );
  const displayAmount = parseDecimalAmount(data.display_amount);
  if (displayAmount !== rawAmountToDecimal(expectedRawAmount, decimals)) {
    invalid(
      `${path}.display_amount`,
      "an exact decimal representation of expected_raw_amount",
      data.display_amount,
    );
  }

  if (kind === "native") {
    if (
      decimals !== 9 ||
      mint !== null ||
      tokenProgram !== null ||
      recipientTokenAccount !== null
    ) {
      invalid(path, "canonical native SOL settlement", value);
    }
  } else if (
    mint === null ||
    tokenProgram !== TOKEN_2022_PROGRAM_ADDRESS ||
    recipientTokenAccount === null
  ) {
    invalid(path, "canonical Token-2022 settlement", value);
  }

  return {
    cluster,
    genesisHash,
    assetOptionId: readString(data.asset_option_id, `${path}.asset_option_id`),
    kind,
    assetName: readString(data.asset_name, `${path}.asset_name`),
    assetSymbol: readString(data.asset_symbol, `${path}.asset_symbol`),
    mint,
    tokenProgram,
    decimals,
    recipientWallet: readAddress(data.recipient_wallet, `${path}.recipient_wallet`),
    recipientPolicyVersion: parseRecipientPolicyVersion(
      data.recipient_policy_version,
      `${path}.recipient_policy_version`,
    ),
    recipientTokenAccount,
    expectedRawAmount,
    displayAmount,
    reference: readAddress(data.reference, `${path}.reference`),
    requiredCommitment: parseCommitment(
      data.required_commitment,
      `${path}.required_commitment`,
    ),
    quote: parseQuote(data.quote, `${path}.quote`),
    memo:
      data.memo === null ? null : readString(data.memo, `${path}.memo`),
  };
}

function parseRecipientPolicyVersion(value: unknown, path: string): string {
  const version = readString(value, path);
  if (!RECIPIENT_POLICY_VERSION_PATTERN.test(version)) {
    invalid(path, "canonical recipient policy version", value);
  }
  return version;
}

export function parseSolanaCheckoutAction(value: unknown): SolanaCheckoutAction {
  return parseCheckoutAction(value);
}

export function parseSolanaPaymentUpdatedEvent(
  value: unknown,
): SolanaPaymentUpdatedEvent {
  const data = readRecord(value, "payment_event");
  return {
    event: readLiteral(
      data.event,
      ["commerce.payment.updated"],
      "payment_event.event",
    ),
    orderPublicId: readUuid(
      data.order_public_id,
      "payment_event.order_public_id",
    ),
    paymentPublicId: readUuid(
      data.payment_public_id,
      "payment_event.payment_public_id",
    ),
    revision: readInteger(data.revision, "payment_event.revision"),
  };
}

export function parseTransactionRequestInfo(value: unknown): TransactionRequestInfo {
  const data = readRecord(value, "transaction_request_info");
  return {
    label: readString(data.label, "transaction_request_info.label"),
    icon:
      data.icon === undefined || data.icon === null
        ? null
        : readHttpsUrl(data.icon, "transaction_request_info.icon"),
  };
}

export function parseTransactionRequestPayload(
  value: unknown,
): TransactionRequestPayload {
  const data = readRecord(value, "transaction_request");
  const transaction = readString(
    data.transaction,
    "transaction_request.transaction",
  );
  if (!/^[A-Za-z0-9+/]+={0,2}$/u.test(transaction)) {
    invalid("transaction_request.transaction", "base64 string", "<redacted>");
  }
  return {
    transaction,
    message:
      data.message === undefined || data.message === null
        ? null
        : readString(data.message, "transaction_request.message"),
  };
}

function parseQuote(value: unknown, path: string): SolanaSettlementQuote {
  const data = readRecord(value, path);
  return {
    source: readString(data.source, `${path}.source`),
    version: readString(data.version, `${path}.version`),
    commercialAmount: parseDecimalAmount(data.commercial_amount),
    commercialCurrency: readString(
      data.commercial_currency,
      `${path}.commercial_currency`,
    ),
    rateNumerator:
      data.rate_numerator === null ? null : parseRawAmount(data.rate_numerator),
    rateDenominator:
      data.rate_denominator === null
        ? null
        : parsePositiveRawAmount(data.rate_denominator, `${path}.rate_denominator`),
    rounding: readLiteral(data.rounding, ROUNDING_MODES, `${path}.rounding`),
  };
}

function parseCheckoutAction(value: unknown): SolanaCheckoutAction {
  const data = readRecord(value, "checkout_action");
  const kind = readLiteral(
    data.kind,
    ["solana_transaction_request"],
    "checkout_action.kind",
  );
  return {
    kind,
    uri: parseSolanaPayUri(data.uri, kind),
    expiresAt: readIsoTimestamp(data.expires_at, "checkout_action.expires_at"),
  };
}

function parseReasonCode(
  value: unknown,
  path: string,
): SolanaPaymentReasonCode | null {
  if (value === undefined || value === null) return null;
  return readString(value, path) as SolanaPaymentReasonCode;
}

function parseNullableSignature(value: unknown, path: string): string | null {
  return value === undefined || value === null ? null : readSignature(value, path);
}

function parseCommitment(
  value: unknown,
  path: string,
): SolanaRequiredCommitment {
  return readLiteral(value, COMMITMENTS, path);
}

function readDecimals(value: unknown, path: string): number {
  const decimals = readInteger(value, path);
  if (decimals > 255) invalid(path, "integer at most 255", decimals);
  return decimals;
}

function parsePositiveRawAmount(value: unknown, path: string) {
  const raw = parseRawAmount(value);
  if (raw === "0") invalid(path, "positive integer string", value);
  return raw;
}

function parsePositiveU64RawAmount(value: unknown, path: string) {
  let raw: ReturnType<typeof parseRawAmount>;
  try {
    raw = parseRawAmount(value);
  } catch {
    invalid(path, "positive u64 integer string", value);
  }
  const numeric = BigInt(raw);
  if (numeric === 0n || numeric > MAX_U64) {
    invalid(path, "positive u64 integer string", value);
  }
  return raw;
}
