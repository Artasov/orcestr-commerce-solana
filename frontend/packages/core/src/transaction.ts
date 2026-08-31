import {
  TOKEN_2022_PROGRAM_ADDRESS,
  type InspectedSolanaTransaction,
  type TransactionValidationSnapshot,
} from "./contracts.js";
import { SolanaCommerceError } from "./errors.js";

const MAX_SERIALIZED_TRANSACTION_BYTES = 1232;
const ISSUANCE_MEMO_PATTERN =
  /^orcestr-issuance:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

export function decodeBase64Transaction(encoded: string): Uint8Array {
  if (
    encoded.length === 0 ||
    encoded.length > 4096 ||
    !/^[A-Za-z0-9+/]+={0,2}$/u.test(encoded)
  ) {
    invalidTransaction("Serialized transaction is not canonical base64.");
  }

  let binary: string;
  try {
    binary = atob(encoded);
  } catch (error) {
    throw new SolanaCommerceError(
      "invalid_contract",
      "Serialized transaction is not valid base64.",
      {},
      { cause: error },
    );
  }
  if (binary.length === 0 || binary.length > MAX_SERIALIZED_TRANSACTION_BYTES) {
    invalidTransaction("Serialized transaction has an invalid size.");
  }
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export function validateInspectedTransaction(
  transaction: InspectedSolanaTransaction,
  expected: TransactionValidationSnapshot,
): void {
  if (transaction.transactionVersion !== 0) {
    mismatch("transaction_version");
  }
  if (
    transaction.feePayer !== expected.payerAddress ||
    !transaction.signerAddresses.includes(expected.payerAddress)
  ) {
    mismatch("payer");
  }
  if (!transaction.referenceAddresses.includes(expected.settlement.reference)) {
    mismatch("reference");
  }
  if (!ISSUANCE_MEMO_PATTERN.test(transaction.issuanceMemo)) {
    mismatch("issuance_memo");
  }
  if (transaction.memo !== expected.settlement.memo) mismatch("memo");
  if (transaction.transfers.length !== 1) mismatch("transfer_count");

  const transfer = transaction.transfers[0];
  if (!transfer || transfer.instructionLevel !== "top_level") {
    mismatch("instruction_level");
  }
  if (
    transfer.rawAmount !== expected.settlement.expectedRawAmount ||
    transfer.decimals !== expected.settlement.decimals ||
    transfer.assetKind !== expected.settlement.kind
  ) {
    mismatch("settlement");
  }

  if (expected.settlement.kind === "token") {
    if (
      transfer.tokenProgram !== TOKEN_2022_PROGRAM_ADDRESS ||
      transfer.mint !== expected.settlement.mint ||
      transfer.sourceTokenAccount !== expected.sourceTokenAccount ||
      expected.sourceTokenAccount === null ||
      transfer.recipientAddress !== null ||
      transfer.recipientTokenAccount !==
        expected.settlement.recipientTokenAccount ||
      expected.settlement.recipientTokenAccount === null
    ) {
      mismatch("token_2022_settlement");
    }
    return;
  }

  if (
    transfer.recipientAddress !== expected.settlement.recipientWallet ||
    transfer.sourceTokenAccount !== null ||
    expected.sourceTokenAccount !== null ||
    transfer.tokenProgram !== null ||
    transfer.mint !== null ||
    transfer.recipientTokenAccount !== null ||
    expected.settlement.recipientTokenAccount !== null
  ) {
    mismatch("native_sol_settlement");
  }
}

function invalidTransaction(message: string): never {
  throw new SolanaCommerceError("invalid_contract", message);
}

function mismatch(field: string): never {
  throw new SolanaCommerceError(
    "transaction_mismatch",
    "Wallet transaction does not match the server settlement snapshot.",
    { field },
  );
}
