import assert from "node:assert/strict";
import test from "node:test";
import {parseSolanaPaymentIntent} from "@orcestr/commerce-solana-core";
import {mergeIntentAction} from "../packages/react/dist/intentAction.js";
import {intent} from "./fixtures.mjs";

const now = Date.parse("2026-08-31T10:02:00Z");
const cached = parseSolanaPaymentIntent(intent());
const status = parseSolanaPaymentIntent(intent({action:null, revision:4}));
test("status and focus refresh preserve the same still-live QR capability", () => {
  const merged = mergeIntentAction(status, cached, now);
  assert.equal(merged.action, cached.action);
  assert.equal(merged.revision, 4);
  assert.equal(mergeIntentAction({...status, revision:5}, merged, now).action, cached.action);
});
test("settlement and quote key order cannot remove a valid QR capability", () => {
  const reordered = {...cached, settlement: Object.fromEntries(Object.entries(cached.settlement).reverse())};
  reordered.settlement.quote = Object.fromEntries(Object.entries(cached.settlement.quote).reverse());
  assert.equal(mergeIntentAction(status, reordered, now).action, cached.action);
});
test("expired and terminal payments never recover a cached capability", () => {
  assert.equal(mergeIntentAction(status, cached, Date.parse(cached.action.expiresAt)).action, null);
  for (const state of ["observed", "confirmed", "paid", "cancelled", "expired", "failed"]) {
    assert.equal(mergeIntentAction({...status, state}, cached, now).action, null);
  }
});
test("another payment, changed settlement and old revisions never inherit an action", () => {
  for (const next of [{...status, publicId:"different"}, {...status, paymentPublicId:"different"},
    {...status, orderPublicId:"different"}, {...status, revision:2},
    {...status, settlement:{...status.settlement, recipientWallet:"different"}},
    {...status, settlement:{...status.settlement, expectedRawAmount:"1"}}]) {
    assert.equal(mergeIntentAction(next, cached, now), next);
  }
  assert.equal(mergeIntentAction(status, undefined, now), status);
  const fresh = {...status, action:{...cached.action, uri:"solana:https://pay.example.com/fresh"}};
  assert.equal(mergeIntentAction(fresh, cached, now), fresh);
});
