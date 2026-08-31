import { SolanaCommerceError } from "./errors.js";

const BASE58_ALPHABET =
  "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const BASE58_DIGITS = new Map(
  [...BASE58_ALPHABET].map((character, index) => [character, index]),
);

export function decodeBase58(value: string): Uint8Array {
  if (value.length === 0) return new Uint8Array();
  let numericValue = 0n;
  for (const character of value) {
    const digit = BASE58_DIGITS.get(character);
    if (digit === undefined) invalidBase58();
    numericValue = numericValue * 58n + BigInt(digit);
  }

  const decoded: number[] = [];
  while (numericValue > 0n) {
    decoded.push(Number(numericValue & 255n));
    numericValue >>= 8n;
  }
  decoded.reverse();

  let leadingZeroCount = 0;
  while (value[leadingZeroCount] === BASE58_ALPHABET[0]) {
    leadingZeroCount += 1;
  }
  const bytes = new Uint8Array(leadingZeroCount + decoded.length);
  bytes.set(decoded, leadingZeroCount);
  return bytes;
}

export function encodeBase58(bytes: Uint8Array): string {
  if (bytes.length === 0) return "";
  const digits = [0];
  for (const byte of bytes) {
    let carry = byte;
    for (let index = 0; index < digits.length; index += 1) {
      const value = (digits[index] ?? 0) * 256 + carry;
      digits[index] = value % 58;
      carry = Math.floor(value / 58);
    }
    while (carry > 0) {
      digits.push(carry % 58);
      carry = Math.floor(carry / 58);
    }
  }

  let leadingZeroCount = 0;
  while (
    leadingZeroCount < bytes.length - 1 &&
    bytes[leadingZeroCount] === 0
  ) {
    leadingZeroCount += 1;
  }
  return `${BASE58_ALPHABET[0]?.repeat(leadingZeroCount) ?? ""}${digits
    .reverse()
    .map((digit) => BASE58_ALPHABET[digit])
    .join("")}`;
}

function invalidBase58(): never {
  throw new SolanaCommerceError(
    "invalid_contract",
    "Value must use canonical base58 encoding.",
  );
}
