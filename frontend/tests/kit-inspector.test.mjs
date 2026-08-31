import assert from "node:assert/strict";
import test from "node:test";

import {
  TOKEN_2022_PROGRAM_ADDRESS,
  decodeBase64Transaction,
  parseDecimalAmount,
  parseRawAmount,
  validateInspectedTransaction,
} from "@orcestr/commerce-solana-core";
import {
  deriveToken2022AssociatedTokenAddress,
  kitTransactionInspector,
} from "@orcestr/commerce-solana-react";

const PAYER = "AKnL4NNf3DGWZJS6cPknBuEGnVsV4A4m5tgebLHaRSZ9";
const RECIPIENT = "9hSR6S7WPtxmTojgo6GG3k4yDPecgJY292j7xrsUGWBu";
const REFERENCE = "GyGKxMyg1p9SsHfm15MkNUu1u9TN2JtTspcdmrtGUdse";
const DESTINATION = "8SFqwqnq4whPhs8icwHA2hQg3hUoN1qrCLK1SBx3WKwe";
const SOURCE_TOKEN_ACCOUNT = "9DdB3X4caQd5hpDRnzNg58Kzvx82VKtmAfJggyjgD8eY";
const NON_ASSOCIATED_SOURCE = "EdmxWPmx2WH6WgFfTdu9xfkYf3k1g5wD1zccTVySEEh1";
const MINT = "HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump";
const ISSUANCE_MEMO = "orcestr-issuance:00000000-0000-4000-8000-000000000004";
const MEMO = "payment:00000000-0000-4000-8000-000000000002";
const NATIVE_TRANSACTION =
  "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQADBYqI4910CfGV/VLbLTy6XXLKZwm/HZQSG/N0iAG0D29cgTl3Dqh9F19Wo1Rmw0x+zMuNipG07jeiXfYPW4/Js5QAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVKU1qZKSEGTSTocWDaOHx8NbXdvJK7geQfqEBBBUSN7UkoxijRwsbq6QM4kFmVYSlZJzpcY/k2NsFGFKyHN9EAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIDAQA1b3JjZXN0ci1pc3N1YW5jZTowMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDQCAwABBAwCAAAAFc1bBwAAAAAA";
const TOKEN_TRANSACTION =
  "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQAEB4qI4910CfGV/VLbLTy6XXLKZwm/HZQSG/N0iAG0D29cbnoc3Smwt4/ROvTFWY/v9O8qlxZuPKby5Pv8zYBQW/F6GV3COM1RF8sFWqAqPEl7AsUf6sWsROWtJS3qEWQgjQVKU1qZKSEGTSTocWDaOHx8NbXdvJK7geQfqEBBBUSNBt324e51j94YQl285GzN2rYa/E2DuQ0n/r35KNihi/ztSSjGKNHCxurpAziQWZVhKVknOlxj+TY2wUYUrIc30fyPKA2lLH7Pv7HPV/kfgfL44vp9H8dmjp2vj7bjeTlPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAwEANW9yY2VzdHItaXNzdWFuY2U6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDA0AwEALHBheW1lbnQ6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAyBAUCBgEABQoMAPkClQAAAAAGAA==";
const NON_ASSOCIATED_SOURCE_TRANSACTION =
  "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQAEB4qI4910CfGV/VLbLTy6XXLKZwm/HZQSG/N0iAG0D29cbnoc3Smwt4/ROvTFWY/v9O8qlxZuPKby5Pv8zYBQW/HKk6wXBRhwcdZ7g8f/Dv6BCOjsRTBXXXcmh5Mz29q+fAVKU1qZKSEGTSTocWDaOHx8NbXdvJK7geQfqEBBBUSNBt324e51j94YQl285GzN2rYa/E2DuQ0n/r35KNihi/ztSSjGKNHCxurpAziQWZVhKVknOlxj+TY2wUYUrIc30fyPKA2lLH7Pv7HPV/kfgfL44vp9H8dmjp2vj7bjeTlPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAwEANW9yY2VzdHItaXNzdWFuY2U6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDA0AwEALHBheW1lbnQ6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAyBAUCBgEABQoMAPkClQAAAAAGAA==";
const EXTRA_MEMO_TRANSACTION =
  "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQAEB4qI4910CfGV/VLbLTy6XXLKZwm/HZQSG/N0iAG0D29cbnoc3Smwt4/ROvTFWY/v9O8qlxZuPKby5Pv8zYBQW/F6GV3COM1RF8sFWqAqPEl7AsUf6sWsROWtJS3qEWQgjQVKU1qZKSEGTSTocWDaOHx8NbXdvJK7geQfqEBBBUSNBt324e51j94YQl285GzN2rYa/E2DuQ0n/r35KNihi/ztSSjGKNHCxurpAziQWZVhKVknOlxj+TY2wUYUrIc30fyPKA2lLH7Pv7HPV/kfgfL44vp9H8dmjp2vj7bjeTlPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAwEANW9yY2VzdHItaXNzdWFuY2U6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDA0AwEALHBheW1lbnQ6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAyAwEACnVuZXhwZWN0ZWQEBQIGAQAFCgwA+QKVAAAAAAYA";
const MISSING_ISSUANCE_TRANSACTION =
  "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQAEB4qI4910CfGV/VLbLTy6XXLKZwm/HZQSG/N0iAG0D29cbnoc3Smwt4/ROvTFWY/v9O8qlxZuPKby5Pv8zYBQW/F6GV3COM1RF8sFWqAqPEl7AsUf6sWsROWtJS3qEWQgjQVKU1qZKSEGTSTocWDaOHx8NbXdvJK7geQfqEBBBUSNBt324e51j94YQl285GzN2rYa/E2DuQ0n/r35KNihi/ztSSjGKNHCxurpAziQWZVhKVknOlxj+TY2wUYUrIc30fyPKA2lLH7Pv7HPV/kfgfL44vp9H8dmjp2vj7bjeTlPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAwEALHBheW1lbnQ6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAyBAUCBgEABQoMAPkClQAAAAAGAA==";
const MEMO_AFTER_PAYMENT_TRANSACTION =
  "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQAEB4qI4910CfGV/VLbLTy6XXLKZwm/HZQSG/N0iAG0D29cbnoc3Smwt4/ROvTFWY/v9O8qlxZuPKby5Pv8zYBQW/F6GV3COM1RF8sFWqAqPEl7AsUf6sWsROWtJS3qEWQgjQVKU1qZKSEGTSTocWDaOHx8NbXdvJK7geQfqEBBBUSNBt324e51j94YQl285GzN2rYa/E2DuQ0n/r35KNihi/ztSSjGKNHCxurpAziQWZVhKVknOlxj+TY2wUYUrIc30fyPKA2lLH7Pv7HPV/kfgfL44vp9H8dmjp2vj7bjeTlPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAwEANW9yY2VzdHItaXNzdWFuY2U6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDA0BAUCBgEABQoMAPkClQAAAAAGAwEALHBheW1lbnQ6MDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAyAA==";

test("inspects a frozen native SOL v0 transaction", async () => {
  const inspected = await kitTransactionInspector(
    decodeBase64Transaction(NATIVE_TRANSACTION),
  );
  assert.equal(inspected.feePayer, PAYER);
  assert.equal(inspected.issuanceMemo, ISSUANCE_MEMO);
  assert.equal(inspected.memo, null);
  assert.equal(inspected.transfers[0]?.assetKind, "native");
  assert.equal(inspected.transfers[0]?.rawAmount, "123456789");
  validateInspectedTransaction(inspected, {
    payerAddress: PAYER,
    sourceTokenAccount: null,
    settlement: settlement({
      kind: "native",
      assetName: "Solana",
      assetSymbol: "SOL",
      mint: null,
      tokenProgram: null,
      decimals: 9,
      recipientWallet: RECIPIENT,
      recipientTokenAccount: null,
      expectedRawAmount: parseRawAmount("123456789"),
      displayAmount: parseDecimalAmount("0.123456789"),
      reference: REFERENCE,
      memo: null,
    }),
  });
});

test("inspects a frozen Token-2022 TransferChecked v0 transaction and exact memo", async () => {
  const inspected = await kitTransactionInspector(
    decodeBase64Transaction(TOKEN_TRANSACTION),
  );
  assert.equal(inspected.issuanceMemo, ISSUANCE_MEMO);
  assert.equal(inspected.memo, MEMO);
  assert.equal(inspected.transfers[0]?.assetKind, "token");
  assert.equal(inspected.transfers[0]?.mint, MINT);
  assert.equal(
    inspected.transfers[0]?.sourceTokenAccount,
    SOURCE_TOKEN_ACCOUNT,
  );
  assert.equal(inspected.transfers[0]?.recipientTokenAccount, DESTINATION);
  assert.equal(inspected.transfers[0]?.rawAmount, "2500000000");

  const expected = {
    payerAddress: PAYER,
    sourceTokenAccount: await deriveToken2022AssociatedTokenAddress(
      PAYER,
      MINT,
    ),
    settlement: settlement({
      kind: "token",
      assetName: "Orcestr",
      assetSymbol: "ORCESTR",
      mint: MINT,
      tokenProgram: TOKEN_2022_PROGRAM_ADDRESS,
      decimals: 6,
      recipientWallet: RECIPIENT,
      recipientTokenAccount: DESTINATION,
      expectedRawAmount: parseRawAmount("2500000000"),
      displayAmount: parseDecimalAmount("2500"),
      reference: REFERENCE,
      memo: MEMO,
    }),
  };
  assert.doesNotThrow(() => validateInspectedTransaction(inspected, expected));
  assert.throws(
    () =>
      validateInspectedTransaction(inspected, {
        ...expected,
        settlement: { ...expected.settlement, memo: "payment:altered" },
      }),
    /does not match/u,
  );
  assert.equal(expected.sourceTokenAccount, SOURCE_TOKEN_ACCOUNT);
});

test("rejects a Token-2022 transfer from a non-associated payer account", async () => {
  const inspected = await kitTransactionInspector(
    decodeBase64Transaction(NON_ASSOCIATED_SOURCE_TRANSACTION),
  );
  assert.equal(
    inspected.transfers[0]?.sourceTokenAccount,
    NON_ASSOCIATED_SOURCE,
  );
  assert.throws(
    () =>
      validateInspectedTransaction(inspected, {
        payerAddress: PAYER,
        sourceTokenAccount: SOURCE_TOKEN_ACCOUNT,
        settlement: settlement({
          kind: "token",
          assetName: "Orcestr",
          assetSymbol: "ORCESTR",
          mint: MINT,
          tokenProgram: TOKEN_2022_PROGRAM_ADDRESS,
          decimals: 6,
          recipientWallet: RECIPIENT,
          recipientTokenAccount: DESTINATION,
          expectedRawAmount: parseRawAmount("2500000000"),
          displayAmount: parseDecimalAmount("2500"),
          reference: REFERENCE,
          memo: MEMO,
        }),
      }),
    /does not match/u,
  );
});

test("rejects missing, extra, and out-of-order signed memos", () => {
  for (const transaction of [
    MISSING_ISSUANCE_TRANSACTION,
    EXTRA_MEMO_TRANSACTION,
    MEMO_AFTER_PAYMENT_TRANSACTION,
  ]) {
    assert.throws(
      () => kitTransactionInspector(decodeBase64Transaction(transaction)),
      /supported Solana payment shape/u,
    );
  }
});

function settlement(overrides) {
  return {
    cluster: "mainnet-beta",
    genesisHash: "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    assetOptionId: "fixture",
    recipientPolicyVersion: "fixture:v1",
    requiredCommitment: "finalized",
    quote: {
      source: "fixture",
      version: "1",
      commercialAmount: parseDecimalAmount("1"),
      commercialCurrency: "USD",
      rateNumerator: null,
      rateDenominator: null,
      rounding: "down",
    },
    ...overrides,
  };
}
