import assert from "node:assert/strict";
import test from "node:test";

import {
  createSolanaPayHref,
  extractTransactionRequestUrl,
  isSolanaCheckoutAction,
  isSolanaCheckoutActionActive,
  parseSolanaPayUri,
  redactSolanaPayUri,
} from "@orcestr/commerce-solana-core";

import { RECIPIENT } from "./fixtures.mjs";

test("accepts canonical transaction requests", () => {
  const transactionUri =
    "solana:https://pay.example.com/transaction?capability=very-secret";
  assert.equal(
    extractTransactionRequestUrl(transactionUri).hostname,
    "pay.example.com",
  );
  assert.equal(redactSolanaPayUri(transactionUri), "solana:<redacted>");
  assert.equal(
    createSolanaPayHref({
      kind: "solana_transaction_request",
      uri: transactionUri,
      expiresAt: "2026-08-31T10:05:00Z",
    }),
    transactionUri,
  );
});

test("allows only loopback HTTP for local transaction-request development", () => {
  for (const host of ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"]) {
    const uri = `solana:http://${host}/transaction?capability=dev-secret`;
    assert.equal(parseSolanaPayUri(uri), uri);
    assert.equal(extractTransactionRequestUrl(uri).protocol, "http:");
  }
  assert.throws(
    () =>
      parseSolanaPayUri(
        "solana:http://pay.example.com/transaction?capability=secret",
      ),
    /Invalid canonical/u,
  );
});

test("rejects unsupported static transfer requests", () => {
  assert.throws(
    () => parseSolanaPayUri(`solana:${RECIPIENT}?amount=1.5`),
    /Invalid canonical/u,
  );
});

test("rejects credential, fragment and cleartext capability URLs", () => {
  for (const uri of [
    "solana:https://user:password@pay.example.com/transaction",
    "solana:https://pay.example.com/transaction#secret",
    "solana:http://pay.example.com/transaction?capability=secret",
    "solana:javascript:alert(1)",
  ]) {
    assert.throws(() => parseSolanaPayUri(uri), /Invalid canonical/u);
  }
});

test("rejects non-canonical URL forms before QR or deep-link rendering", () => {
  for (const uri of [
    "solana:https://pay.example.com:443/transaction?capability=secret",
    "solana:https://PAY.example.com/transaction?capability=secret",
    "solana:https://pay.example.com/a/../transaction?capability=secret",
    "solana:https://pay.example.com/transaction?capability=secret ",
    "solana:https://pay.example.com\\@evil.example/transaction",
  ]) {
    assert.throws(() => parseSolanaPayUri(uri), /Invalid canonical/u);
  }
});

test("checkout action guards fail closed and enforce capability expiry", () => {
  const action = {
    kind: "solana_transaction_request",
    uri: "solana:https://pay.example.com/transaction?capability=secret",
    expiresAt: "2099-08-31T10:05:00Z",
  };
  assert.equal(isSolanaCheckoutAction(action), true);
  assert.equal(
    isSolanaCheckoutActionActive(action, Date.parse("2099-08-31T10:04:59Z")),
    true,
  );
  assert.equal(
    isSolanaCheckoutActionActive(action, Date.parse(action.expiresAt)),
    false,
  );

  for (const invalid of [
    { kind: "solana_transaction_request" },
    { ...action, expiresAt: "2099-08-31T10:05:00" },
    { ...action, uri: "solana:http://pay.example.com/transaction" },
    { ...action, kind: "solana_transfer_request" },
  ]) {
    assert.equal(isSolanaCheckoutAction(invalid), false);
    assert.equal(isSolanaCheckoutActionActive(invalid), false);
  }
});
