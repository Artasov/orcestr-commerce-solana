import assert from "node:assert/strict";
import test from "node:test";

import {
  clusterToWalletChain,
  isCompatibleWallet,
  solanaCommerceQueryKeys,
  subscribeToSolanaPaymentEvents,
} from "@orcestr/commerce-solana-react";
import { QueryClient } from "@tanstack/react-query";
import {
  solanaCommerceMessages,
  isSafeWalletIconDataUrl,
  solanaPaymentRendererDescriptor,
} from "@orcestr/commerce-solana-ui";
import { PAYMENT_ID } from "./fixtures.mjs";

test("query keys and wallet chains are deterministic", () => {
  assert.deepEqual(solanaCommerceQueryKeys.intent(PAYMENT_ID), [
    "commerce",
    "solana",
    "payment-intent",
    PAYMENT_ID,
  ]);
  assert.equal(clusterToWalletChain("mainnet-beta"), "solana:mainnet");
  assert.equal(clusterToWalletChain("devnet"), "solana:devnet");
});

test("wallet discovery accepts only the supported Wallet Standard v0 shape", () => {
  const wallet = {
    features: {
      "standard:connect": {
        version: "1.0.0",
        connect: async () => ({ accounts: [] }),
      },
      "solana:signAndSendTransaction": {
        version: "1.0.0",
        supportedTransactionVersions: [0],
        signAndSendTransaction: async () => [],
      },
    },
  };
  assert.equal(isCompatibleWallet(wallet), true);
  assert.equal(
    isCompatibleWallet({
      ...wallet,
      features: {
        ...wallet.features,
        "solana:signAndSendTransaction": {
          ...wallet.features["solana:signAndSendTransaction"],
          supportedTransactionVersions: ["legacy"],
        },
      },
    }),
    false,
  );
  assert.equal(
    isCompatibleWallet({
      ...wallet,
      features: {
        ...wallet.features,
        "standard:connect": {
          connect: wallet.features["standard:connect"].connect,
        },
      },
    }),
    false,
  );
});

test("RU and EN catalogs cover identical statuses and reasons", () => {
  assert.deepEqual(
    Object.keys(solanaCommerceMessages.ru.status).sort(),
    Object.keys(solanaCommerceMessages.en.status).sort(),
  );
  assert.deepEqual(
    Object.keys(solanaCommerceMessages.ru.reason).sort(),
    Object.keys(solanaCommerceMessages.en.reason).sort(),
  );
});

test("wallet icons cannot trigger remote image requests", () => {
  assert.equal(
    isSafeWalletIconDataUrl("data:image/png;base64,iVBORw0KGgo="),
    true,
  );
  assert.equal(isSafeWalletIconDataUrl("https://tracker.example/icon.png"), false);
  assert.equal(isSafeWalletIconDataUrl(`data:image/png;base64,${"A".repeat(131_072)}`), false);
});

test("renderer accepts only Solana checkout action kinds", () => {
  const action = {
    kind: "solana_transaction_request",
    uri: "solana:https://pay.example.com/transaction?capability=secret",
    expiresAt: "2099-08-31T10:05:00Z",
  };
  assert.equal(
    solanaPaymentRendererDescriptor.supportsAction(action),
    true,
  );
  assert.equal(
    solanaPaymentRendererDescriptor.supportsAction({ ...action, uri: "javascript:alert(1)" }),
    false,
  );
  assert.equal(
    solanaPaymentRendererDescriptor.supportsAction({ kind: action.kind }),
    false,
  );
});

test("bridges valid fresh shared events to host invalidation callback once", async () => {
  const queryClient = new QueryClient();
  let paymentListener = null;
  let reconnectListener = null;
  let unsubscribed = 0;
  const received = [];
  const errors = [];
  const unsubscribe = subscribeToSolanaPaymentEvents(
    queryClient,
    {
      subscribe(listener) {
        paymentListener = listener;
        return () => {
          unsubscribed += 1;
        };
      },
      subscribeReconnect(listener) {
        reconnectListener = listener;
        return () => {
          unsubscribed += 1;
        };
      },
    },
    (event) => received.push(event),
    (error) => errors.push(error),
  );
  queryClient.setQueryData(solanaCommerceQueryKeys.intent(PAYMENT_ID), {
    revision: 4,
  });

  const payload = {
    event: "commerce.payment.updated",
    order_public_id: "00000000-0000-4000-8000-000000000003",
    payment_public_id: PAYMENT_ID,
    revision: 4,
  };
  paymentListener(payload);
  paymentListener(payload);
  await Promise.resolve();
  assert.equal(received.length, 1);
  assert.equal(
    queryClient.getQueryState(solanaCommerceQueryKeys.intent(PAYMENT_ID))
      ?.isInvalidated,
    true,
  );

  paymentListener({ ...payload, event: "commerce.order.updated" });
  assert.equal(errors.length, 1);
  reconnectListener();
  unsubscribe();
  assert.equal(unsubscribed, 2);
});
