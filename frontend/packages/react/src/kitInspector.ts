import {
  SolanaCommerceError,
  TOKEN_2022_PROGRAM_ADDRESS,
  parseRawAmount,
  type InspectedSolanaTransaction,
  type InspectedSolanaTransfer,
  type SolanaTransactionInspector,
} from "@orcestr/commerce-solana-core";
import {
  address,
  getAddressEncoder,
  getCompiledTransactionMessageDecoder,
  getInstructionsFromCompiledTransactionMessage,
  getProgramDerivedAddress,
  getTransactionDecoder,
  isSignerRole,
  isWritableRole,
  type ResolvedInstruction,
} from "@solana/kit";

const SYSTEM_PROGRAM_ADDRESS = "11111111111111111111111111111111";
const MEMO_PROGRAM_ADDRESS = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
const ISSUANCE_MEMO_PATTERN =
  /^orcestr-issuance:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const SYSTEM_TRANSFER_DISCRIMINATOR = 2;
const TOKEN_TRANSFER_CHECKED_DISCRIMINATOR = 12;
const ASSOCIATED_TOKEN_PROGRAM_ADDRESS =
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
type ByteSequence = {
  readonly length: number;
  readonly [index: number]: number | undefined;
};

export const kitTransactionInspector: SolanaTransactionInspector = (
  serializedTransaction,
) => {
  try {
    const transaction = getTransactionDecoder().decode(serializedTransaction);
    const message = getCompiledTransactionMessageDecoder().decode(
      transaction.messageBytes,
    );
    if (message.version !== 0) mismatch("transaction_version");
    if (message.addressTableLookups && message.addressTableLookups.length > 0) {
      mismatch("address_table_lookup");
    }

    const feePayer = message.staticAccounts[0];
    if (!feePayer || message.header.numSignerAccounts !== 1)
      mismatch("fee_payer");
    const signerAddresses = message.staticAccounts
      .slice(0, message.header.numSignerAccounts)
      .map(String);
    const transfers: InspectedSolanaTransfer[] = [];
    const referenceAddresses: string[] = [];
    let issuanceMemo: string | null = null;
    let memo: string | null = null;

    for (const [
      instructionIndex,
      instruction,
    ] of getInstructionsFromCompiledTransactionMessage(message).entries()) {
      if (instruction.programAddress === MEMO_PROGRAM_ADDRESS) {
        if (transfers.length > 0) mismatch("memo_order");
        const value = inspectMemoInstruction(instruction, String(feePayer));
        if (issuanceMemo === null) {
          if (instructionIndex !== 0 || !ISSUANCE_MEMO_PATTERN.test(value)) {
            mismatch("issuance_memo");
          }
          issuanceMemo = value;
        } else {
          if (memo !== null) mismatch("multiple_settlement_memos");
          memo = value;
        }
        continue;
      }
      if (issuanceMemo === null) mismatch("issuance_memo");
      const transfer = inspectPaymentInstruction(instruction, String(feePayer));
      transfers.push(transfer.transfer);
      referenceAddresses.push(transfer.reference);
    }
    if (issuanceMemo === null) mismatch("issuance_memo");
    if (transfers.length !== 1) mismatch("transfer_count");

    return {
      transactionVersion: 0,
      feePayer: String(feePayer),
      signerAddresses,
      referenceAddresses,
      issuanceMemo,
      memo,
      transfers,
    } satisfies InspectedSolanaTransaction;
  } catch (error) {
    if (
      error instanceof SolanaCommerceError &&
      error.code === "transaction_mismatch"
    ) {
      throw error;
    }
    throw new SolanaCommerceError(
      "transaction_mismatch",
      "Unable to decode the wallet transaction safely.",
      { field: "wire_transaction" },
      { cause: error },
    );
  }
};

export function createKitTransactionInspector(): SolanaTransactionInspector {
  return kitTransactionInspector;
}

export async function deriveToken2022AssociatedTokenAddress(
  ownerAddress: string,
  mintAddress: string,
): Promise<string> {
  try {
    const encoder = getAddressEncoder();
    const [derivedAddress] = await getProgramDerivedAddress({
      programAddress: address(ASSOCIATED_TOKEN_PROGRAM_ADDRESS),
      seeds: [
        encoder.encode(address(ownerAddress)),
        encoder.encode(address(TOKEN_2022_PROGRAM_ADDRESS)),
        encoder.encode(address(mintAddress)),
      ],
    });
    return String(derivedAddress);
  } catch (error) {
    throw new SolanaCommerceError(
      "transaction_mismatch",
      "Unable to derive the payer Token-2022 associated token account.",
      { field: "source_token_account" },
      { cause: error },
    );
  }
}

function inspectPaymentInstruction(
  instruction: ResolvedInstruction,
  feePayer: string,
): { readonly transfer: InspectedSolanaTransfer; readonly reference: string } {
  if (instruction.programAddress === SYSTEM_PROGRAM_ADDRESS) {
    return inspectNativeTransfer(instruction, feePayer);
  }
  if (instruction.programAddress === TOKEN_2022_PROGRAM_ADDRESS) {
    return inspectTokenTransfer(instruction, feePayer);
  }
  mismatch("unsupported_program");
}

function inspectNativeTransfer(
  instruction: ResolvedInstruction,
  feePayer: string,
): { readonly transfer: InspectedSolanaTransfer; readonly reference: string } {
  const accounts = instruction.accounts ?? [];
  const data = instruction.data ?? new Uint8Array();
  if (
    accounts.length !== 3 ||
    data.length !== 12 ||
    readU32LittleEndian(data, 0) !== SYSTEM_TRANSFER_DISCRIMINATOR ||
    String(accounts[0]?.address) !== feePayer ||
    !accounts[0] ||
    !isSignerRole(accounts[0].role) ||
    !isWritableRole(accounts[0].role)
  ) {
    mismatch("native_transfer");
  }
  const recipient = accounts[1];
  const reference = accounts[2];
  if (
    !recipient ||
    !reference ||
    !isWritableRole(recipient.role) ||
    isWritableRole(reference.role) ||
    isSignerRole(reference.role)
  ) {
    mismatch("native_transfer_accounts");
  }
  return {
    transfer: {
      instructionLevel: "top_level",
      assetKind: "native",
      tokenProgram: null,
      mint: null,
      sourceTokenAccount: null,
      recipientAddress: String(recipient.address),
      recipientTokenAccount: null,
      rawAmount: parseRawAmount(readU64LittleEndian(data, 4).toString()),
      decimals: 9,
    },
    reference: String(reference.address),
  };
}

function inspectTokenTransfer(
  instruction: ResolvedInstruction,
  feePayer: string,
): { readonly transfer: InspectedSolanaTransfer; readonly reference: string } {
  const accounts = instruction.accounts ?? [];
  const data = instruction.data ?? new Uint8Array();
  if (
    accounts.length !== 5 ||
    data.length !== 10 ||
    data[0] !== TOKEN_TRANSFER_CHECKED_DISCRIMINATOR ||
    String(accounts[3]?.address) !== feePayer ||
    !accounts[3] ||
    !isSignerRole(accounts[3].role)
  ) {
    mismatch("token_transfer_checked");
  }
  const mint = accounts[1];
  const recipientTokenAccount = accounts[2];
  const reference = accounts[4];
  const sourceTokenAccount = accounts[0];
  if (
    !sourceTokenAccount ||
    !mint ||
    !recipientTokenAccount ||
    !reference ||
    !isWritableRole(sourceTokenAccount.role) ||
    isWritableRole(mint.role) ||
    isSignerRole(mint.role) ||
    !isWritableRole(recipientTokenAccount.role) ||
    isWritableRole(reference.role) ||
    isSignerRole(reference.role)
  ) {
    mismatch("token_transfer_accounts");
  }
  return {
    transfer: {
      instructionLevel: "top_level",
      assetKind: "token",
      tokenProgram: TOKEN_2022_PROGRAM_ADDRESS,
      mint: String(mint.address),
      sourceTokenAccount: String(sourceTokenAccount.address),
      recipientAddress: null,
      recipientTokenAccount: String(recipientTokenAccount.address),
      rawAmount: parseRawAmount(readU64LittleEndian(data, 1).toString()),
      decimals: data[9] ?? -1,
    },
    reference: String(reference.address),
  };
}

function inspectMemoInstruction(
  instruction: ResolvedInstruction,
  feePayer: string,
): string {
  const accounts = instruction.accounts ?? [];
  const data = instruction.data ?? new Uint8Array();
  if (
    accounts.length !== 1 ||
    String(accounts[0]?.address) !== feePayer ||
    !accounts[0] ||
    !isSignerRole(accounts[0].role)
  ) {
    mismatch("memo");
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(data);
  } catch (error) {
    throw new SolanaCommerceError(
      "transaction_mismatch",
      "Wallet transaction contains an invalid memo.",
      { field: "memo" },
      { cause: error },
    );
  }
}

function readU32LittleEndian(bytes: ByteSequence, offset: number): number {
  return (
    ((bytes[offset] ?? 0) |
      ((bytes[offset + 1] ?? 0) << 8) |
      ((bytes[offset + 2] ?? 0) << 16) |
      ((bytes[offset + 3] ?? 0) << 24)) >>>
    0
  );
}

function readU64LittleEndian(bytes: ByteSequence, offset: number): bigint {
  let value = 0n;
  for (let index = 7; index >= 0; index -= 1) {
    value = (value << 8n) | BigInt(bytes[offset + index] ?? 0);
  }
  return value;
}

function mismatch(field: string): never {
  throw new SolanaCommerceError(
    "transaction_mismatch",
    "Wallet transaction does not match the supported Solana payment shape.",
    { field },
  );
}
