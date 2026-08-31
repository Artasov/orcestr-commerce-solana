import { SolanaCommerceError } from "./errors.js";
import { decodeBase58 } from "./base58.js";

export function readRecord(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    invalid(path, "object", value);
  }
  return value as Record<string, unknown>;
}

export function readArray(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) invalid(path, "array", value);
  return value;
}

export function readString(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    invalid(path, "non-empty string", value);
  }
  return value;
}

export function readNullableString(value: unknown, path: string): string | null {
  return value === null ? null : readString(value, path);
}

export function readInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    invalid(path, "non-negative safe integer", value);
  }
  return value as number;
}

export function readLiteral<T extends string>(
  value: unknown,
  allowed: readonly T[],
  path: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    invalid(path, allowed.join(" | "), value);
  }
  return value as T;
}

export function readIsoTimestamp(value: unknown, path: string): string {
  const timestamp = readString(value, path);
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.test(
      timestamp,
    ) ||
    Number.isNaN(Date.parse(timestamp))
  ) {
    invalid(path, "ISO-8601 timestamp", value);
  }
  return timestamp;
}

export function readAddress(value: unknown, path: string): string {
  const address = readString(value, path);
  if (
    !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/u.test(address) ||
    decodeBase58(address).length !== 32
  ) {
    invalid(path, "base58 Solana address", value);
  }
  return address;
}

export function readSignature(value: unknown, path: string): string {
  const signature = readString(value, path);
  if (
    !/^[1-9A-HJ-NP-Za-km-z]{64,88}$/u.test(signature) ||
    decodeBase58(signature).length !== 64
  ) {
    invalid(path, "base58 Solana signature", value);
  }
  return signature;
}

export function readUuid(value: unknown, path: string): string {
  const uuid = readString(value, path);
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(
      uuid,
    )
  ) {
    invalid(path, "UUID", value);
  }
  return uuid;
}

export function readHttpsUrl(value: unknown, path: string): string {
  const raw = readString(value, path);
  let url: URL;
  try {
    url = new URL(raw);
  } catch (error) {
    throw new SolanaCommerceError(
      "invalid_contract",
      `${path} must be an absolute HTTPS URL.`,
      { path },
      { cause: error },
    );
  }
  if (url.protocol !== "https:" || url.username || url.password || url.hash) {
    invalid(path, "absolute HTTPS URL without credentials or fragment", value);
  }
  return url.toString();
}

export function invalid(path: string, expected: string, value: unknown): never {
  throw new SolanaCommerceError(
    "invalid_contract",
    `${path} must be ${expected}.`,
    { path, receivedType: describeValueType(value) },
  );
}

function describeValueType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}
