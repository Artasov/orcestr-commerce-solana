import type { DecimalAmountString, SolanaCheckoutAction } from "./contracts.js";
import { parseDecimalAmount } from "./amounts.js";
import { parseSolanaPayUri } from "./uri.js";
import {
  invalid,
  readInteger,
  readIsoTimestamp,
  readRecord,
  readString,
  readUuid,
} from "./validation.js";

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

export type CommercePaymentState =
  | "created"
  | "requires_action"
  | "processing"
  | "observed"
  | "confirmed"
  | "paid"
  | "expired"
  | "cancelled"
  | "failed"
  | "review"
  | "refund_pending"
  | "refunded";

export type CommerceCheckoutAction = {
  readonly kind: string;
  readonly uri: string | null;
  readonly expiresAt: string | null;
  readonly payload: Readonly<Record<string, JsonValue>>;
};

export type CommercePaymentOption = {
  readonly id: string;
  readonly label: string;
  readonly actionKind: string;
  readonly details: Readonly<Record<string, JsonValue>>;
  readonly amount: DecimalAmountString;
  readonly currency: string;
  readonly paymentSystem: string;
  readonly providerKind: string;
};

export type CommercePaymentOptionsResponse = {
  readonly options: readonly CommercePaymentOption[];
};

export type CreateCommercePaymentAttemptInput = {
  readonly orderPublicId: string;
  readonly paymentOptionId: string;
  readonly idempotencyKey: string;
};

export type CommercePayment = {
  readonly id: string;
  readonly orderId: string;
  readonly attemptNo: number;
  readonly amount: DecimalAmountString;
  readonly currency: string;
  readonly paymentSystem: string;
  readonly providerKind: string;
  readonly paymentOptionId: string;
  readonly state: CommercePaymentState;
  readonly action: CommerceCheckoutAction | null;
  readonly reasonCode: string | null;
  readonly revision: number;
  readonly expiresAt: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
};

const PAYMENT_STATES: readonly CommercePaymentState[] = [
  "created",
  "requires_action",
  "processing",
  "observed",
  "confirmed",
  "paid",
  "expired",
  "cancelled",
  "failed",
  "review",
  "refund_pending",
  "refunded",
];

export function parseCommercePaymentOptionsResponse(
  value: unknown,
): CommercePaymentOptionsResponse {
  const data = readRecord(value, "commerce_payment_options");
  if (!Array.isArray(data.options)) {
    invalid("commerce_payment_options.options", "array", data.options);
  }
  return {
    options: data.options.map((option, index) =>
      parseCommercePaymentOption(option, `commerce_payment_options.options[${index}]`),
    ),
  };
}

export function parseCommercePayment(value: unknown): CommercePayment {
  const path = "commerce_payment";
  const data = readRecord(value, path);
  const state = readString(data.state, `${path}.state`);
  if (!PAYMENT_STATES.includes(state as CommercePaymentState)) {
    invalid(`${path}.state`, PAYMENT_STATES.join(" | "), state);
  }
  return {
    id: readUuid(data.id, `${path}.id`),
    orderId: readUuid(data.order_id, `${path}.order_id`),
    attemptNo: readInteger(data.attempt_no, `${path}.attempt_no`),
    amount: parseDecimalAmount(data.amount),
    currency: parseCommerceCurrency(data.currency, `${path}.currency`),
    paymentSystem: readString(data.payment_system, `${path}.payment_system`),
    providerKind: readString(data.provider_kind, `${path}.provider_kind`),
    paymentOptionId: readString(
      data.payment_option_id,
      `${path}.payment_option_id`,
    ),
    state: state as CommercePaymentState,
    action:
      data.action === undefined || data.action === null
        ? null
        : parseCommerceCheckoutAction(data.action),
    reasonCode:
      data.reason_code === undefined || data.reason_code === null
        ? null
        : readString(data.reason_code, `${path}.reason_code`),
    revision: readInteger(data.revision, `${path}.revision`),
    expiresAt:
      data.expires_at === undefined || data.expires_at === null
        ? null
        : readIsoTimestamp(data.expires_at, `${path}.expires_at`),
    createdAt: readIsoTimestamp(data.created_at, `${path}.created_at`),
    updatedAt: readIsoTimestamp(data.updated_at, `${path}.updated_at`),
  };
}

export function parseCommerceCheckoutAction(value: unknown): CommerceCheckoutAction {
  const path = "commerce_checkout_action";
  const data = readRecord(value, path);
  return {
    kind: readString(data.kind, `${path}.kind`),
    uri:
      data.uri === undefined || data.uri === null
        ? null
        : readString(data.uri, `${path}.uri`),
    expiresAt:
      data.expires_at === undefined || data.expires_at === null
        ? null
        : readIsoTimestamp(data.expires_at, `${path}.expires_at`),
    payload: readJsonObject(data.payload ?? {}, `${path}.payload`),
  };
}

export function toSolanaCheckoutAction(
  action: CommerceCheckoutAction,
): SolanaCheckoutAction {
  if (
    action.kind !== "solana_transaction_request"
  ) {
    invalid(
      "commerce_checkout_action.kind",
      "Solana checkout action kind",
      action.kind,
    );
  }
  if (action.uri === null || action.expiresAt === null) {
    invalid(
      "commerce_checkout_action",
      "Solana checkout action with URI and expiry",
      "<redacted>",
    );
  }
  return {
    kind: action.kind,
    uri: parseSolanaPayUri(action.uri, action.kind),
    expiresAt: action.expiresAt,
  };
}

export function selectSolanaCommerceOptions(
  response: CommercePaymentOptionsResponse,
): readonly CommercePaymentOption[] {
  return response.options.filter(
    (option) =>
      option.paymentSystem === "solana" &&
      option.providerKind === "solana" &&
      option.actionKind === "solana_transaction_request",
  );
}

function parseCommercePaymentOption(
  value: unknown,
  path: string,
): CommercePaymentOption {
  const data = readRecord(value, path);
  return {
    id: readString(data.id, `${path}.id`),
    label: readString(data.label, `${path}.label`),
    actionKind: readString(data.action_kind, `${path}.action_kind`),
    details: readJsonObject(data.details ?? {}, `${path}.details`),
    amount: parseDecimalAmount(data.amount),
    currency: parseCommerceCurrency(data.currency, `${path}.currency`),
    paymentSystem: readString(data.payment_system, `${path}.payment_system`),
    providerKind: readString(data.provider_kind, `${path}.provider_kind`),
  };
}

function parseCommerceCurrency(value: unknown, path: string): string {
  const currency = readString(value, path);
  if (
    currency.length > 12 ||
    currency !== currency.trim() ||
    currency !== currency.toUpperCase()
  ) {
    invalid(
      path,
      "a normalized uppercase CommerceXL currency of at most 12 characters",
      value,
    );
  }
  return currency;
}

function readJsonObject(
  value: unknown,
  path: string,
): Readonly<Record<string, JsonValue>> {
  const record = readRecord(value, path);
  return Object.fromEntries(
    Object.entries(record).map(([key, entry]) => [
      key,
      readJsonValue(entry, `${path}.${key}`),
    ]),
  );
}

function readJsonValue(value: unknown, path: string): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) {
    return value.map((entry, index) => readJsonValue(entry, `${path}[${index}]`));
  }
  if (typeof value === "object") return readJsonObject(value, path);
  invalid(path, "JSON value", value);
}
