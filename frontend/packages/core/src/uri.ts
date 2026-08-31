import type { SolanaCheckoutAction } from "./contracts.js";
import { decodeBase58 } from "./base58.js";
import { SolanaCommerceError } from "./errors.js";

const SOLANA_PREFIX = "solana:";
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/u;
const SOLANA_ADDRESS_PATTERN = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/u;
const ISO_TIMESTAMP_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u;

export function parseSolanaPayUri(
  uri: unknown,
  expectedKind?: SolanaCheckoutAction["kind"],
): string {
  if (
    typeof uri !== "string" ||
    uri.length <= SOLANA_PREFIX.length ||
    uri.length > 4096 ||
    CONTROL_CHARACTER_PATTERN.test(uri) ||
    !uri.startsWith(SOLANA_PREFIX)
  ) {
    invalidUri();
  }

  const payload = uri.slice(SOLANA_PREFIX.length);
  validateTransactionRequestUrl(payload);
  if (expectedKind !== undefined && expectedKind !== "solana_transaction_request") {
    invalidUri();
  }
  return uri;
}

export function extractTransactionRequestUrl(uri: string): URL {
  const canonical = parseSolanaPayUri(uri, "solana_transaction_request");
  return new URL(canonical.slice(SOLANA_PREFIX.length));
}

export function createSolanaPayHref(action: SolanaCheckoutAction): string {
  return parseSolanaPayUri(action.uri, action.kind);
}

export function isSolanaCheckoutAction(
  value: unknown,
): value is SolanaCheckoutAction {
  if (typeof value !== "object" || value === null) return false;
  try {
    const kind = Reflect.get(value, "kind");
    const uri = Reflect.get(value, "uri");
    const expiresAt = Reflect.get(value, "expiresAt");
    if (
      kind !== "solana_transaction_request" ||
      typeof uri !== "string" ||
      typeof expiresAt !== "string" ||
      !ISO_TIMESTAMP_PATTERN.test(expiresAt) ||
      Number.isNaN(Date.parse(expiresAt))
    ) {
      return false;
    }
    parseSolanaPayUri(uri, kind);
    return true;
  } catch {
    return false;
  }
}

export function isSolanaCheckoutActionActive(
  value: unknown,
  now = Date.now(),
): value is SolanaCheckoutAction {
  return (
    Number.isFinite(now) &&
    isSolanaCheckoutAction(value) &&
    Date.parse(value.expiresAt) > now
  );
}

export function redactSolanaPayUri(uri: unknown): string {
  if (typeof uri !== "string" || !uri.startsWith(SOLANA_PREFIX)) {
    return "<redacted>";
  }
  return `${SOLANA_PREFIX}<redacted>`;
}

export function shortenSolanaAddress(
  address: string,
  visibleCharacters = 4,
): string {
  if (
    !SOLANA_ADDRESS_PATTERN.test(address) ||
    decodeBase58(address).length !== 32
  ) {
    invalidUri();
  }
  if (!Number.isInteger(visibleCharacters) || visibleCharacters < 2) {
    throw new SolanaCommerceError(
      "invalid_uri",
      "Visible address characters must be an integer of at least 2.",
    );
  }
  if (address.length <= visibleCharacters * 2 + 1) return address;
  return `${address.slice(0, visibleCharacters)}…${address.slice(-visibleCharacters)}`;
}

function validateTransactionRequestUrl(rawUrl: string): void {
  if (!rawUrl.startsWith("https://") && !rawUrl.startsWith("http://")) {
    invalidUri();
  }
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    invalidUri();
  }
  const loopbackHttp =
    url.protocol === "http:" &&
    ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (
    (url.protocol !== "https:" && !loopbackHttp) ||
    url.username.length > 0 ||
    url.password.length > 0 ||
    url.hash.length > 0 ||
    url.href !== rawUrl
  ) {
    invalidUri();
  }
}

function invalidUri(): never {
  throw new SolanaCommerceError(
    "invalid_uri",
    "Invalid canonical Solana Pay URI.",
  );
}
