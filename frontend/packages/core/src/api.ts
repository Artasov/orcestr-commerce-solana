import type {
  CancelSolanaPaymentIntentInput,
  CreateSolanaPaymentIntentInput,
  SolanaCheckoutAction,
  SolanaPaymentIntent,
  SolanaPaymentOptionsResponse,
  SubmitCandidateSignatureInput,
  TransactionRequestInfo,
  TransactionRequestPayload,
} from "./contracts.js";
import { SolanaCommerceHttpError } from "./errors.js";
import type {
  CommerceCheckoutAction,
  CreateCommercePaymentAttemptInput,
  CommercePayment,
  CommercePaymentOptionsResponse,
} from "./commerce.js";
import {
  parseCommerceCheckoutAction,
  parseCommercePayment,
  parseCommercePaymentOptionsResponse,
} from "./commerce.js";
import {
  parseSolanaCheckoutAction,
  parseSolanaPaymentIntent,
  parseSolanaPaymentOptionsResponse,
  parseTransactionRequestInfo,
  parseTransactionRequestPayload,
} from "./parsers.js";
import { extractTransactionRequestUrl } from "./uri.js";
import { readAddress, readSignature, readString, readUuid } from "./validation.js";

export type ApiResponseSensitivity = "standard" | "capability";

export type AuthenticatedApiRequest = {
  readonly method: "GET" | "POST";
  readonly url: URL;
  readonly body: unknown | null;
  readonly headers: Readonly<Record<string, string>>;
  readonly signal: AbortSignal | null;
  readonly responseSensitivity: ApiResponseSensitivity;
};

export type AuthenticatedApiExecutor = (
  request: AuthenticatedApiRequest,
) => Promise<unknown>;

export type AuthFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export type PublicTransactionRequest = {
  readonly method: "GET" | "POST";
  readonly url: URL;
  readonly body: unknown | null;
  readonly signal: AbortSignal | null;
};

export type PublicTransactionRequestExecutor = (
  request: PublicTransactionRequest,
) => Promise<unknown>;

export type SolanaCommerceRoutes = {
  readonly paymentOptions: (orderPublicId: string) => string;
  readonly createIntent: string;
  readonly intent: (paymentPublicId: string) => string;
  readonly candidateSignature: (paymentPublicId: string) => string;
  readonly cancelIntent: (paymentPublicId: string) => string;
  readonly issueAction: (paymentPublicId: string) => string;
};

export type CommerceCoreRoutes = {
  readonly paymentOptions: (orderPublicId: string) => string;
  readonly createPaymentAttempt: (orderPublicId: string) => string;
  readonly payment: (paymentPublicId: string) => string;
  readonly checkoutAction: (paymentPublicId: string) => string;
};

export type SolanaCommerceClientOptions = {
  readonly baseUrl: string;
  readonly executor: AuthenticatedApiExecutor;
  readonly routes?: SolanaCommerceRoutes;
  readonly commerceRoutes?: CommerceCoreRoutes;
};

export const defaultSolanaCommerceRoutes: SolanaCommerceRoutes = {
  paymentOptions: (orderPublicId) =>
    `/commerce/solana/orders/${encodeURIComponent(orderPublicId)}/payment-options`,
  createIntent: "/commerce/solana/payment-intents",
  intent: (paymentPublicId) =>
    `/commerce/solana/payment-intents/${encodeURIComponent(paymentPublicId)}`,
  candidateSignature: (paymentPublicId) =>
    `/commerce/solana/payment-intents/${encodeURIComponent(paymentPublicId)}/candidate-signatures`,
  cancelIntent: (paymentPublicId) =>
    `/commerce/solana/payment-intents/${encodeURIComponent(paymentPublicId)}/cancel`,
  issueAction: (paymentPublicId) =>
    `/commerce/solana/payment-intents/${encodeURIComponent(paymentPublicId)}/actions`,
};

export const defaultCommerceCoreRoutes: CommerceCoreRoutes = {
  paymentOptions: (orderPublicId) =>
    `/commerce/orders/${encodeURIComponent(orderPublicId)}/payment-options/`,
  createPaymentAttempt: (orderPublicId) =>
    `/commerce/orders/${encodeURIComponent(orderPublicId)}/payment-attempts/`,
  payment: (paymentPublicId) =>
    `/commerce/payments/${encodeURIComponent(paymentPublicId)}/`,
  checkoutAction: (paymentPublicId) =>
    `/commerce/payments/${encodeURIComponent(paymentPublicId)}/checkout-action/`,
};

export class SolanaCommerceClient {
  readonly #baseUrl: URL;
  readonly #executor: AuthenticatedApiExecutor;
  readonly #routes: SolanaCommerceRoutes;
  readonly #commerceRoutes: CommerceCoreRoutes;

  constructor(options: SolanaCommerceClientOptions) {
    this.#baseUrl = parseApiBaseUrl(options.baseUrl);
    this.#executor = options.executor;
    this.#routes = options.routes ?? defaultSolanaCommerceRoutes;
    this.#commerceRoutes = options.commerceRoutes ?? defaultCommerceCoreRoutes;
  }

  async listPaymentOptions(
    orderPublicId: string,
    signal: AbortSignal | null = null,
  ): Promise<SolanaPaymentOptionsResponse> {
    const id = readUuid(orderPublicId, "orderPublicId");
    const response = await this.#request(
      "GET",
      this.#routes.paymentOptions(id),
      null,
      signal,
      "standard",
    );
    return parseSolanaPaymentOptionsResponse(response);
  }

  async createPaymentIntent(
    input: CreateSolanaPaymentIntentInput,
    signal: AbortSignal | null = null,
  ): Promise<SolanaPaymentIntent> {
    const response = await this.#request(
      "POST",
      this.#routes.createIntent,
      {
        order_public_id: readUuid(input.orderPublicId, "orderPublicId"),
        payment_option_id: readString(
          input.paymentOptionId,
          "paymentOptionId",
        ),
        idempotency_key: readString(input.idempotencyKey, "idempotencyKey"),
      },
      signal,
      "capability",
    );
    return parseSolanaPaymentIntent(response);
  }

  async getPaymentIntent(
    paymentPublicId: string,
    signal: AbortSignal | null = null,
  ): Promise<SolanaPaymentIntent> {
    const response = await this.#request(
      "GET",
      this.#routes.intent(readUuid(paymentPublicId, "paymentPublicId")),
      null,
      signal,
      "capability",
    );
    return parseSolanaPaymentIntent(response);
  }

  async submitCandidateSignature(
    input: SubmitCandidateSignatureInput,
    signal: AbortSignal | null = null,
  ): Promise<SolanaPaymentIntent> {
    const paymentPublicId = readUuid(
      input.paymentPublicId,
      "paymentPublicId",
    );
    const response = await this.#request(
      "POST",
      this.#routes.candidateSignature(paymentPublicId),
      { signature: readSignature(input.signature, "signature") },
      signal,
      "standard",
    );
    return parseSolanaPaymentIntent(response);
  }

  async cancelPaymentIntent(
    input: CancelSolanaPaymentIntentInput,
    signal: AbortSignal | null = null,
  ): Promise<SolanaPaymentIntent> {
    const paymentPublicId = readUuid(input.paymentPublicId, "paymentPublicId");
    const response = await this.#request(
      "POST",
      this.#routes.cancelIntent(paymentPublicId),
      {
        reason: readString(input.reason, "reason"),
        idempotency_key: readString(input.idempotencyKey, "idempotencyKey"),
      },
      signal,
      "standard",
    );
    return parseSolanaPaymentIntent(response);
  }

  async issuePaymentIntentAction(
    paymentPublicId: string,
    signal: AbortSignal | null = null,
  ): Promise<SolanaCheckoutAction> {
    const response = await this.#request(
      "POST",
      this.#routes.issueAction(readUuid(paymentPublicId, "paymentPublicId")),
      null,
      signal,
      "capability",
    );
    return parseSolanaCheckoutAction(response);
  }

  async listCommercePaymentOptions(
    orderPublicId: string,
    signal: AbortSignal | null = null,
  ): Promise<CommercePaymentOptionsResponse> {
    const response = await this.#request(
      "GET",
      this.#commerceRoutes.paymentOptions(readUuid(orderPublicId, "orderPublicId")),
      null,
      signal,
      "standard",
    );
    return parseCommercePaymentOptionsResponse(response);
  }

  async createCommercePaymentAttempt(
    input: CreateCommercePaymentAttemptInput,
    signal: AbortSignal | null = null,
  ): Promise<CommercePayment> {
    const response = await this.#request(
      "POST",
      this.#commerceRoutes.createPaymentAttempt(
        readUuid(input.orderPublicId, "orderPublicId"),
      ),
      { payment_option_id: readString(input.paymentOptionId, "paymentOptionId") },
      signal,
      "capability",
      { "Idempotency-Key": readString(input.idempotencyKey, "idempotencyKey") },
    );
    return parseCommercePayment(response);
  }

  async getCommercePayment(
    paymentPublicId: string,
    signal: AbortSignal | null = null,
  ): Promise<CommercePayment> {
    const response = await this.#request(
      "GET",
      this.#commerceRoutes.payment(readUuid(paymentPublicId, "paymentPublicId")),
      null,
      signal,
      "standard",
    );
    return parseCommercePayment(response);
  }

  async issueCommerceCheckoutAction(
    paymentPublicId: string,
    signal: AbortSignal | null = null,
  ): Promise<CommerceCheckoutAction> {
    const response = await this.#request(
      "POST",
      this.#commerceRoutes.checkoutAction(
        readUuid(paymentPublicId, "paymentPublicId"),
      ),
      null,
      signal,
      "capability",
    );
    return parseCommerceCheckoutAction(response);
  }

  async #request(
    method: "GET" | "POST",
    path: string,
    body: unknown | null,
    signal: AbortSignal | null,
    responseSensitivity: ApiResponseSensitivity,
    headers: Readonly<Record<string, string>> = {},
  ): Promise<unknown> {
    return this.#executor({
      method,
      url: resolveApiUrl(path, this.#baseUrl),
      body,
      headers,
      signal,
      responseSensitivity,
    });
  }
}

export function createAuthenticatedFetchExecutor(
  authFetch: AuthFetch,
): AuthenticatedApiExecutor {
  return async (request) =>
    executeJsonRequest(authFetch, request, "include");
}

export function createPublicTransactionRequestExecutor(
  fetchImplementation: typeof fetch = globalThis.fetch,
): PublicTransactionRequestExecutor {
  return async (request) =>
    executeJsonRequest(fetchImplementation, request, "omit");
}

export async function getTransactionRequestInfo(
  executor: PublicTransactionRequestExecutor,
  canonicalUri: string,
  signal: AbortSignal | null = null,
): Promise<TransactionRequestInfo> {
  const response = await executor({
    method: "GET",
    url: extractTransactionRequestUrl(canonicalUri),
    body: null,
    signal,
  });
  return parseTransactionRequestInfo(response);
}

export async function createTransactionRequest(
  executor: PublicTransactionRequestExecutor,
  canonicalUri: string,
  payerAddress: string,
  signal: AbortSignal | null = null,
): Promise<TransactionRequestPayload> {
  const response = await executor({
    method: "POST",
    url: extractTransactionRequestUrl(canonicalUri),
    body: { account: readAddress(payerAddress, "payerAddress") },
    signal,
  });
  return parseTransactionRequestPayload(response);
}

async function executeJsonRequest(
  fetchImplementation: AuthFetch,
  request: AuthenticatedApiRequest | PublicTransactionRequest,
  credentials: RequestCredentials,
): Promise<unknown> {
  const headers = new Headers({ Accept: "application/json" });
  if ("headers" in request) {
    for (const [name, value] of Object.entries(request.headers)) {
      headers.set(name, value);
    }
  }
  if (request.body !== null) headers.set("Content-Type", "application/json");
  const init: RequestInit = {
    method: request.method,
    credentials,
    cache: "no-store",
    referrerPolicy: "no-referrer",
    redirect: "error",
    headers,
  };
  if (request.body !== null) init.body = JSON.stringify(request.body);
  if (request.signal !== null) init.signal = request.signal;

  const response = await fetchImplementation(request.url, init);
  const body = await readJsonResponse(response);
  if (!response.ok) {
    const sensitiveResponse =
      !("responseSensitivity" in request) ||
      request.responseSensitivity === "capability";
    throw new SolanaCommerceHttpError(
      response.status,
      sensitiveResponse ? "<redacted>" : body,
    );
  }
  return body;
}

async function readJsonResponse(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    if (response.status === 204) return null;
    throw new SolanaCommerceHttpError(response.status, null);
  }
  try {
    return await response.json();
  } catch (error) {
    throw new SolanaCommerceHttpError(response.status, {
      code: "invalid_json_response",
      cause: error instanceof Error ? error.message : "Unknown JSON error",
    });
  }
}

function parseApiBaseUrl(value: string): URL {
  const baseUrl = new URL(value.endsWith("/") ? value : `${value}/`);
  const localHttp =
    baseUrl.protocol === "http:" &&
    ["localhost", "127.0.0.1", "[::1]"].includes(baseUrl.hostname);
  if (
    (baseUrl.protocol !== "https:" && !localHttp) ||
    baseUrl.username ||
    baseUrl.password ||
    baseUrl.hash ||
    baseUrl.search
  ) {
    throw new TypeError(
      "baseUrl must be HTTPS, or loopback HTTP for local development.",
    );
  }
  return baseUrl;
}

function normalizeApiPath(path: string): string {
  if (
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    /[\u0000-\u001f\u007f]/u.test(path)
  ) {
    throw new TypeError("API route must be an absolute-path reference.");
  }
  return path.slice(1);
}

function resolveApiUrl(path: string, baseUrl: URL): URL {
  const url = new URL(normalizeApiPath(path), baseUrl);
  if (
    url.origin !== baseUrl.origin ||
    !url.pathname.startsWith(baseUrl.pathname)
  ) {
    throw new TypeError(
      "API route must stay under the configured API base path.",
    );
  }
  return url;
}
