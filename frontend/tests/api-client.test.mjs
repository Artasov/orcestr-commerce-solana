import assert from "node:assert/strict";
import test from "node:test";

import {
  SolanaCommerceClient,
  SolanaCommerceHttpError,
  createAuthenticatedFetchExecutor,
  defaultSolanaCommerceRoutes,
} from "@orcestr/commerce-solana-core";

import { intent, ORDER_ID, PAYMENT_ID } from "./fixtures.mjs";

test("uses host authenticated executor and marks capability responses", async () => {
  const requests = [];
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/",
    executor: async (request) => {
      requests.push(request);
      return intent();
    },
  });

  const created = await client.createPaymentIntent({
    orderPublicId: ORDER_ID,
    paymentOptionId: "orce_mainnet",
    idempotencyKey: "checkout-click-01",
  });
  assert.equal(created.paymentPublicId, PAYMENT_ID);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.href, "https://app.example.com/api/commerce/solana/payment-intents");
  assert.equal(requests[0].responseSensitivity, "capability");
  assert.deepEqual(requests[0].body, {
    order_public_id: ORDER_ID,
    payment_option_id: "orce_mainnet",
    idempotency_key: "checkout-click-01",
  });
});

test("allows loopback HTTP but rejects insecure remote API base", () => {
  const executor = async () => intent();
  assert.doesNotThrow(
    () => new SolanaCommerceClient({ baseUrl: "http://localhost:8000", executor }),
  );
  assert.throws(
    () => new SolanaCommerceClient({ baseUrl: "http://payments.example.com", executor }),
    /must be HTTPS/u,
  );
});

test("sends typed cancellation idempotency and reason fields", async () => {
  const requests = [];
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/v1/",
    executor: async (request) => {
      requests.push(request);
      return intent({ state: "cancelled", action: null });
    },
  });

  await client.cancelPaymentIntent({
    paymentPublicId: PAYMENT_ID,
    reason: "user_closed_checkout",
    idempotencyKey: "cancel-click-01",
  });

  assert.equal(
    requests[0].url.href,
    `https://app.example.com/api/v1/commerce/solana/payment-intents/${PAYMENT_ID}/cancel`,
  );
  assert.deepEqual(requests[0].body, {
    reason: "user_closed_checkout",
    idempotency_key: "cancel-click-01",
  });
});

test("reissues the short-lived addon action without changing intent identity", async () => {
  const requests = [];
  const action = {
    kind: "solana_transaction_request",
    uri: "solana:https://pay.example.com/transaction?capability=fresh-secret",
    expires_at: "2026-08-31T10:07:00Z",
  };
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/v1/",
    executor: async (request) => {
      requests.push(request);
      return action;
    },
  });

  const parsed = await client.issuePaymentIntentAction(PAYMENT_ID);
  assert.equal(parsed.uri, action.uri);
  assert.equal(
    requests[0].url.href,
    `https://app.example.com/api/v1/commerce/solana/payment-intents/${PAYMENT_ID}/actions`,
  );
  assert.equal(requests[0].responseSensitivity, "capability");
  assert.equal(requests[0].body, null);
});

test("implements the stable CommerceXL 0.3.2 payment flow", async () => {
  const requests = [];
  const paymentOption = {
    id: "solana:orce-mainnet",
    label: "ORCESTR on Solana",
    action_kind: "solana_transaction_request",
    details: { symbol: "ORCESTR" },
    amount: "2500.125",
    currency: "ORCESTR",
    payment_system: "solana",
    provider_kind: "solana",
  };
  const action = {
    kind: "solana_transaction_request",
    uri: "solana:https://pay.example.com/transaction?capability=secret",
    expires_at: "2026-08-31T10:05:00Z",
    payload: {},
  };
  const payment = {
    id: PAYMENT_ID,
    order_id: ORDER_ID,
    attempt_no: 1,
    amount: "1000.00",
    currency: "RUB",
    payment_system: "solana",
    provider_kind: "solana",
    payment_option_id: paymentOption.id,
    state: "requires_action",
    action,
    reason_code: null,
    revision: 1,
    expires_at: "2026-08-31T10:15:00Z",
    created_at: "2026-08-31T10:00:00Z",
    updated_at: "2026-08-31T10:00:00Z",
  };
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/v1/",
    executor: async (request) => {
      requests.push(request);
      if (request.url.pathname.endsWith("/payment-options/")) {
        return { options: [paymentOption] };
      }
      if (request.url.pathname.endsWith("/checkout-action/")) return action;
      return payment;
    },
  });

  const listed = await client.listCommercePaymentOptions(ORDER_ID);
  assert.equal(listed.options[0].providerKind, "solana");
  assert.equal(listed.options[0].amount, "2500.125");
  assert.equal(listed.options[0].currency, "ORCESTR");
  const created = await client.createCommercePaymentAttempt({
    orderPublicId: ORDER_ID,
    paymentOptionId: paymentOption.id,
    idempotencyKey: "commerce-attempt-01",
  });
  assert.equal(created.id, PAYMENT_ID);
  await client.getCommercePayment(PAYMENT_ID);
  const reissued = await client.issueCommerceCheckoutAction(PAYMENT_ID);
  assert.equal(reissued.kind, "solana_transaction_request");

  assert.deepEqual(
    requests.map((request) => request.url.pathname),
    [
      `/api/v1/commerce/orders/${ORDER_ID}/payment-options/`,
      `/api/v1/commerce/orders/${ORDER_ID}/payment-attempts/`,
      `/api/v1/commerce/payments/${PAYMENT_ID}/`,
      `/api/v1/commerce/payments/${PAYMENT_ID}/checkout-action/`,
    ],
  );
  assert.deepEqual(requests[1].body, {
    payment_option_id: paymentOption.id,
  });
  assert.equal(requests[1].headers["Idempotency-Key"], "commerce-attempt-01");
});

test("authenticated fetch rejects redirects and suppresses cache and referrer", async () => {
  const calls = [];
  const authFetch = async (input, init) => {
    calls.push({ input, init });
    return new Response(JSON.stringify(intent()), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/",
    executor: createAuthenticatedFetchExecutor(authFetch),
  });

  await client.getPaymentIntent(PAYMENT_ID);
  assert.equal(calls[0].input.href, `https://app.example.com/api/commerce/solana/payment-intents/${PAYMENT_ID}`);
  assert.equal(calls[0].init.redirect, "error");
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(calls[0].init.referrerPolicy, "no-referrer");
  assert.equal(calls[0].init.credentials, "include");
});

test("custom routes cannot escape the configured API origin through backslashes", async () => {
  let executed = false;
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/",
    executor: async () => {
      executed = true;
      return intent();
    },
    routes: {
      ...defaultSolanaCommerceRoutes,
      intent: () => "/\\evil.example/steal",
    },
  });

  await assert.rejects(
    () => client.getPaymentIntent(PAYMENT_ID),
    /absolute-path reference/u,
  );
  assert.equal(executed, false);
});

test("custom routes cannot traverse above the configured API base path", async () => {
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/v1/",
    executor: async () => intent(),
    routes: {
      ...defaultSolanaCommerceRoutes,
      intent: () => "/../admin/session",
    },
  });

  await assert.rejects(
    () => client.getPaymentIntent(PAYMENT_ID),
    /configured API base path/u,
  );
});

test("capability endpoint errors do not retain backend response bodies", async () => {
  const secret = "solana:https://pay.example.com/transaction?capability=secret";
  const client = new SolanaCommerceClient({
    baseUrl: "https://app.example.com/api/",
    executor: createAuthenticatedFetchExecutor(async () =>
      new Response(JSON.stringify({ code: "expired", uri: secret }), {
        status: 410,
        headers: { "content-type": "application/json" },
      }),
    ),
  });

  await assert.rejects(
    () => client.getPaymentIntent(PAYMENT_ID),
    (error) => {
      assert.ok(error instanceof SolanaCommerceHttpError);
      assert.equal(error.responseBody, "<redacted>");
      assert.doesNotMatch(JSON.stringify(error), /capability=secret/u);
      return true;
    },
  );
});
