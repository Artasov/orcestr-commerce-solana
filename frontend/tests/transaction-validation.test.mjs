import assert from "node:assert/strict";
import test from "node:test";

import {
  TOKEN_2022_PROGRAM_ADDRESS,
  encodeBase58,
  parseRawAmount,
  parseSolanaPaymentIntent,
  validateInspectedTransaction,
} from "@orcestr/commerce-solana-core";

import { intent, MINT, RECIPIENT, REFERENCE } from "./fixtures.mjs";

const SOURCE_TOKEN_ACCOUNT = "SysvarC1ock11111111111111111111111111111111";

function expected() {
  return {
    payerAddress: MINT,
    sourceTokenAccount: SOURCE_TOKEN_ACCOUNT,
    settlement: parseSolanaPaymentIntent(intent()).settlement,
  };
}

function inspected(overrides = {}) {
  return {
    transactionVersion: 0,
    feePayer: MINT,
    signerAddresses: [MINT],
    referenceAddresses: [REFERENCE],
    issuanceMemo: "orcestr-issuance:00000000-0000-4000-8000-000000000004",
    memo: null,
    transfers: [
      {
        instructionLevel: "top_level",
        assetKind: "token",
        tokenProgram: TOKEN_2022_PROGRAM_ADDRESS,
        mint: MINT,
        sourceTokenAccount: SOURCE_TOKEN_ACCOUNT,
        recipientAddress: null,
        recipientTokenAccount: RECIPIENT,
        rawAmount: parseRawAmount("2500000000"),
        decimals: 6,
      },
    ],
    ...overrides,
  };
}

test("accepts one exact top-level Token-2022 transfer", () => {
  assert.doesNotThrow(() =>
    validateInspectedTransaction(inspected(), expected()),
  );
});

test("rejects extra, inner and altered transfers before wallet signing", () => {
  assert.throws(
    () =>
      validateInspectedTransaction(
        inspected({
          transfers: [...inspected().transfers, ...inspected().transfers],
        }),
        expected(),
      ),
    /does not match/u,
  );
  assert.throws(
    () =>
      validateInspectedTransaction(
        inspected({
          transfers: [
            { ...inspected().transfers[0], instructionLevel: "inner" },
          ],
        }),
        expected(),
      ),
    /does not match/u,
  );
  assert.throws(
    () =>
      validateInspectedTransaction(
        inspected({
          transfers: [
            { ...inspected().transfers[0], rawAmount: parseRawAmount("1") },
          ],
        }),
        expected(),
      ),
    /does not match/u,
  );
  assert.throws(
    () =>
      validateInspectedTransaction(
        inspected({
          transfers: [
            {
              ...inspected().transfers[0],
              sourceTokenAccount: RECIPIENT,
            },
          ],
        }),
        expected(),
      ),
    /does not match/u,
  );
});

test("requires a canonical UUIDv4 issuance memo", () => {
  assert.throws(
    () =>
      validateInspectedTransaction(
        inspected({
          issuanceMemo: "orcestr-issuance:00000000-0000-1000-8000-000000000004",
        }),
        expected(),
      ),
    /does not match/u,
  );
});

test("encodes raw signature bytes as base58", () => {
  assert.equal(encodeBase58(Uint8Array.of(0)), "1");
  assert.equal(encodeBase58(Uint8Array.of(0, 0)), "11");
  assert.equal(encodeBase58(Uint8Array.of(1)), "2");
});
