import type { DecimalAmountString, RawAmountString } from "./contracts.js";
import { SolanaCommerceError } from "./errors.js";

const DECIMAL_PATTERN = /^(?:0|[1-9]\d*)(?:\.\d+)?$/u;
const RAW_PATTERN = /^(?:0|[1-9]\d*)$/u;

export function parseDecimalAmount(value: unknown): DecimalAmountString {
  if (typeof value !== "string" || !DECIMAL_PATTERN.test(value)) {
    throw new SolanaCommerceError(
      "invalid_amount",
      "Decimal amount must be a non-negative base-10 string without exponent notation.",
      { value },
    );
  }
  return value as DecimalAmountString;
}

export function parseRawAmount(value: unknown): RawAmountString {
  if (typeof value !== "string" || !RAW_PATTERN.test(value)) {
    throw new SolanaCommerceError(
      "invalid_amount",
      "Raw amount must be a non-negative integer string.",
      { value },
    );
  }
  return value as RawAmountString;
}

export function rawAmountToDecimal(
  rawAmount: RawAmountString | string,
  decimals: number,
): DecimalAmountString {
  const raw = parseRawAmount(rawAmount);
  validateDecimals(decimals);
  if (decimals === 0) return raw as unknown as DecimalAmountString;

  const padded = raw.padStart(decimals + 1, "0");
  const integer = padded.slice(0, -decimals);
  const fraction = padded.slice(-decimals).replace(/0+$/u, "");
  return (fraction.length > 0 ? `${integer}.${fraction}` : integer) as DecimalAmountString;
}

export function decimalAmountToRaw(
  decimalAmount: DecimalAmountString | string,
  decimals: number,
): RawAmountString {
  const value = parseDecimalAmount(decimalAmount);
  validateDecimals(decimals);
  const [integer = "0", fraction = ""] = value.split(".");
  if (fraction.length > decimals) {
    throw new SolanaCommerceError(
      "invalid_amount",
      `Amount has more than ${decimals} fractional digits.`,
      { value, decimals },
    );
  }
  const raw = `${integer}${fraction.padEnd(decimals, "0")}`.replace(
    /^0+(?=\d)/u,
    "",
  );
  return parseRawAmount(raw);
}

export function compareRawAmounts(
  left: RawAmountString | string,
  right: RawAmountString | string,
): -1 | 0 | 1 {
  const leftValue = BigInt(parseRawAmount(left));
  const rightValue = BigInt(parseRawAmount(right));
  return leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0;
}

function validateDecimals(decimals: number): void {
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 255) {
    throw new SolanaCommerceError(
      "invalid_amount",
      "Decimals must be an integer between 0 and 255.",
      { decimals },
    );
  }
}
