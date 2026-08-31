import assert from "node:assert/strict";
import test from "node:test";

import {
  TOKEN_2022_PROGRAM_ADDRESS,
  decimalAmountToRaw,
  parseDecimalAmount,
  parseRawAmount,
  parseSolanaCheckoutAction,
  parseSolanaPaymentIntent,
  parseSolanaPaymentOptionsResponse,
  parseSolanaPaymentUpdatedEvent,
  parseCommercePayment,
  SolanaCommerceError,
  selectSolanaCommerceOptions,
  rawAmountToDecimal,
} from "@orcestr/commerce-solana-core";

import {
  intent,
  MINT,
  ORDER_ID,
  PAYMENT_ID,
  RECIPIENT,
  TOKEN_PROGRAM,
} from "./fixtures.mjs";

test("parses a Token-2022 intent with string amounts", () => {
  const parsed = parseSolanaPaymentIntent(intent());
  assert.equal(parsed.settlement.kind, "token");
  assert.equal(parsed.settlement.mint, MINT);
  assert.equal(parsed.settlement.tokenProgram, TOKEN_2022_PROGRAM_ADDRESS);
  assert.equal(
    parsed.settlement.recipientPolicyVersion,
    "beauty-tenant-wallet:v1",
  );
  assert.equal(parsed.settlement.expectedRawAmount, "2500000000");
  assert.equal(parsed.action?.kind, "solana_transaction_request");
});

test("rejects every other token program", () => {
  const payload = intent();
  payload.settlement = {
    ...payload.settlement,
    token_program: "11111111111111111111111111111111",
  };
  assert.throws(
    () => parseSolanaPaymentIntent(payload),
    /payment_intent\.settlement\.token_program/u,
  );
  assert.notEqual(TOKEN_PROGRAM, "11111111111111111111111111111111");
});

test("keeps arbitrary precision amounts outside Number", () => {
  const raw = parseRawAmount("123456789012345678901234567890");
  assert.equal(rawAmountToDecimal(raw, 6), "123456789012345678901234.56789");
  assert.equal(
    decimalAmountToRaw(parseDecimalAmount("123456789012345678901234.56789"), 6),
    raw,
  );
  assert.throws(() => decimalAmountToRaw("1.0000001", 6), /fractional digits/u);
  assert.throws(() => parseDecimalAmount("1e6"), /base-10 string/u);
});

test("parses only the shared payment update envelope", () => {
  assert.deepEqual(
    parseSolanaPaymentUpdatedEvent({
      event: "commerce.payment.updated",
      order_public_id: ORDER_ID,
      payment_public_id: PAYMENT_ID,
      revision: 4,
    }),
    {
      event: "commerce.payment.updated",
      orderPublicId: ORDER_ID,
      paymentPublicId: PAYMENT_ID,
      revision: 4,
    },
  );
  assert.throws(
    () =>
      parseSolanaPaymentUpdatedEvent({
        event: "commerce.order.updated",
        order_public_id: ORDER_ID,
        payment_public_id: PAYMENT_ID,
        revision: 4,
      }),
    /payment_event\.event/u,
  );
});

test("parses the CommerceXL 0.3.1 payment envelope and selects Solana options", () => {
  const payment = parseCommercePayment({
    id: PAYMENT_ID,
    order_id: ORDER_ID,
    attempt_no: 1,
    amount: "1000.00",
    currency: "RUB",
    payment_system: "solana",
    provider_kind: "solana",
    payment_option_id: "solana:orce-mainnet",
    state: "requires_action",
    action: null,
    reason_code: null,
    revision: 1,
    expires_at: null,
    created_at: "2026-08-31T10:00:00Z",
    updated_at: "2026-08-31T10:00:00Z",
  });
  assert.equal(payment.amount, "1000.00");
  assert.deepEqual(
    selectSolanaCommerceOptions({
      options: [
        {
          id: "balance",
          label: "Balance",
          actionKind: "completed",
          details: {},
          paymentSystem: "balance",
          providerKind: "builtin",
        },
        {
          id: "solana",
          label: "Solana",
          actionKind: "solana_transaction_request",
          details: {},
          paymentSystem: "solana",
          providerKind: "solana",
        },
        {
          id: "solana-future-action",
          label: "Unsupported Solana action",
          actionKind: "redirect",
          details: {},
          paymentSystem: "solana",
          providerKind: "solana",
        },
      ],
    }).map((option) => option.id),
    ["solana"],
  );
});

test("rejects timestamps without an explicit UTC offset", () => {
  const payload = intent({ updated_at: "2026-08-31T10:01:00" });
  assert.throws(() => parseSolanaPaymentIntent(payload), /updated_at/u);
});

test("binds every settlement to the exact cluster genesis hash", () => {
  const payload = intent();
  payload.settlement = {
    ...payload.settlement,
    cluster: "devnet",
  };
  assert.throws(
    () => parseSolanaPaymentIntent(payload),
    /genesis_hash/u,
  );
});

test("requires positive u64 settlement amounts and exact display amount", () => {
  for (const expectedRawAmount of ["0", "18446744073709551616"]) {
    const payload = intent();
    payload.settlement = {
      ...payload.settlement,
      expected_raw_amount: expectedRawAmount,
    };
    assert.throws(
      () => parseSolanaPaymentIntent(payload),
      /expected_raw_amount/u,
    );
  }

  const mismatchedDisplay = intent();
  mismatchedDisplay.settlement = {
    ...mismatchedDisplay.settlement,
    display_amount: "2500.000001",
  };
  assert.throws(
    () => parseSolanaPaymentIntent(mismatchedDisplay),
    /display_amount/u,
  );
  const nonCanonicalDisplay = intent();
  nonCanonicalDisplay.settlement = {
    ...nonCanonicalDisplay.settlement,
    display_amount: "2500.0",
  };
  assert.throws(
    () => parseSolanaPaymentIntent(nonCanonicalDisplay),
    /display_amount/u,
  );
});

test("requires consistent positive u64 payment option bounds", () => {
  const option = {
    id: "orce-mainnet",
    label: "ORCESTR",
    symbol: "ORCESTR",
    decimals: 6,
    mint: MINT,
    minimum_raw_amount: "100",
    maximum_raw_amount: "99",
    required_commitment: "finalized",
  };
  assert.throws(
    () => parseSolanaPaymentOptionsResponse({ options: [option] }),
    /minimum_raw_amount/u,
  );
  assert.throws(
    () =>
      parseSolanaPaymentOptionsResponse({
        options: [{ ...option, minimum_raw_amount: "0", maximum_raw_amount: "100" }],
      }),
    /minimum_raw_amount/u,
  );
  assert.throws(
    () =>
      parseSolanaPaymentOptionsResponse({
        options: [
          {
            ...option,
            minimum_raw_amount: "1",
            maximum_raw_amount: "100",
            required_commitment: "confirmed",
          },
        ],
      }),
    /required_commitment/u,
  );

  const confirmedSettlement = intent();
  confirmedSettlement.settlement = {
    ...confirmedSettlement.settlement,
    required_commitment: "confirmed",
  };
  assert.throws(
    () => parseSolanaPaymentIntent(confirmedSettlement),
    /required_commitment/u,
  );
});

test("decodes addresses and signatures to their canonical byte lengths", () => {
  const invalidAddress = intent();
  invalidAddress.settlement = {
    ...invalidAddress.settlement,
    mint: "z".repeat(44),
  };
  assert.throws(
    () => parseSolanaPaymentIntent(invalidAddress),
    /payment_intent\.settlement\.mint/u,
  );

  assert.throws(
    () =>
      parseSolanaPaymentIntent({
        ...intent(),
        candidate_signature: "z".repeat(88),
      }),
    /candidate_signature/u,
  );
});

test("rejects the unimplemented static transfer action", () => {
  assert.throws(
    () =>
      parseSolanaCheckoutAction({
        kind: "solana_transfer_request",
        uri: `solana:${RECIPIENT}?amount=1`,
        expires_at: "2026-08-31T10:05:00Z",
      }),
    /checkout_action\.kind/u,
  );
});

test("contract errors never retain raw capability values", () => {
  const secret = "solana:https://pay.example.com/transaction?capability=secret";
  const payload = intent();
  payload.action = [secret];
  assert.throws(
    () => parseSolanaPaymentIntent(payload),
    (error) => {
      assert.ok(error instanceof SolanaCommerceError);
      assert.doesNotMatch(JSON.stringify(error.details), /capability=secret/u);
      assert.equal(error.details.receivedType, "array");
      return true;
    },
  );
});

test("requires canonical immutable recipient resolver provenance", () => {
  for (const recipientPolicyVersion of [
    undefined,
    "",
    "contains spaces",
    "é",
    "x".repeat(101),
  ]) {
    const payload = intent();
    payload.settlement = {
      ...payload.settlement,
      recipient_policy_version: recipientPolicyVersion,
    };
    assert.throws(
      () => parseSolanaPaymentIntent(payload),
      /recipient_policy_version/u,
    );
  }
});

test("drops future checkout action fields at the API parser boundary", () => {
  assert.deepEqual(
    parseSolanaCheckoutAction({
      kind: "solana_transaction_request",
      uri: "solana:https://pay.example.com/transaction?capability=secret",
      expires_at: "2099-08-31T10:05:00Z",
      html: "<img src=https://tracker.example>",
      redirect_uri: "https://tracker.example",
    }),
    {
      kind: "solana_transaction_request",
      uri: "solana:https://pay.example.com/transaction?capability=secret",
      expiresAt: "2099-08-31T10:05:00Z",
    },
  );
});
