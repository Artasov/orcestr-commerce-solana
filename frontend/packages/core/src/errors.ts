export type SolanaCommerceErrorCode =
  | "invalid_contract"
  | "invalid_amount"
  | "invalid_uri"
  | "http_error"
  | "transaction_mismatch";

export class SolanaCommerceError extends Error {
  readonly code: SolanaCommerceErrorCode;
  readonly details: Readonly<Record<string, unknown>>;

  constructor(
    code: SolanaCommerceErrorCode,
    message: string,
    details: Readonly<Record<string, unknown>> = {},
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "SolanaCommerceError";
    this.code = code;
    this.details = details;
  }
}

export class SolanaCommerceHttpError extends SolanaCommerceError {
  readonly status: number;
  readonly responseBody: unknown;

  constructor(status: number, responseBody: unknown) {
    super("http_error", `Solana Commerce request failed with HTTP ${status}.`, {
      status,
    });
    this.name = "SolanaCommerceHttpError";
    this.status = status;
    this.responseBody = responseBody;
  }
}
