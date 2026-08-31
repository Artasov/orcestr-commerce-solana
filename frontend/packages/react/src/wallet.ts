"use client";

import {
  createTransactionRequest,
  decodeBase64Transaction,
  encodeBase58,
  isSolanaCheckoutAction,
  isSolanaCheckoutActionActive,
  SolanaCommerceError,
  validateInspectedTransaction,
  type PublicTransactionRequestExecutor,
  type SolanaCluster,
  type SolanaPaymentIntent,
  type SolanaPaymentReasonCode,
  type SolanaTransactionInspector,
} from "@orcestr/commerce-solana-core";
import {
  SolanaSignAndSendTransaction,
  type SolanaSignAndSendTransactionFeature,
} from "@solana/wallet-standard-features";
import {
  getWallets,
  StandardConnect,
  type StandardConnectFeature,
  type Wallet,
  type WalletAccount,
  type WalletWithFeatures,
} from "@wallet-standard/core";
import { useCallback, useEffect, useState } from "react";

import { useSolanaCommerceClient } from "./provider.js";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { solanaCommerceQueryKeys } from "./queryKeys.js";
import {
  deriveToken2022AssociatedTokenAddress,
  kitTransactionInspector,
} from "./kitInspector.js";

type CompatibleWallet = WalletWithFeatures<
  StandardConnectFeature & SolanaSignAndSendTransactionFeature
>;

export type WalletStandardConnection = {
  readonly wallets: readonly Wallet[];
  readonly wallet: Wallet | null;
  readonly account: WalletAccount | null;
  readonly connecting: boolean;
  readonly error: WalletPaymentError | null;
  readonly connect: (wallet: Wallet, cluster: SolanaCluster) => Promise<void>;
  readonly disconnectLocal: () => void;
};

export type SubmitWalletPaymentInput = {
  readonly wallet: Wallet;
  readonly account: WalletAccount;
};

export type SubmitWalletPaymentOptions = {
  readonly publicExecutor: PublicTransactionRequestExecutor;
  readonly intent: SolanaPaymentIntent | null;
  readonly transactionInspector?: SolanaTransactionInspector;
};

export class WalletPaymentError extends Error {
  readonly reasonCode: SolanaPaymentReasonCode;

  constructor(
    reasonCode: SolanaPaymentReasonCode,
    message: string,
    cause?: unknown,
  ) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "WalletPaymentError";
    this.reasonCode = reasonCode;
  }
}

export function useWalletStandardConnection(): WalletStandardConnection {
  const [wallets, setWallets] = useState<readonly Wallet[]>([]);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [account, setAccount] = useState<WalletAccount | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<WalletPaymentError | null>(null);

  useEffect(() => {
    const registry = getWallets();
    const refresh = () => setWallets(registry.get().filter(isCompatibleWallet));
    refresh();
    const offRegister = registry.on("register", refresh);
    const offUnregister = registry.on(
      "unregister",
      (...unregisteredWallets) => {
        refresh();
        if (wallet && unregisteredWallets.includes(wallet)) {
          setWallet(null);
          setAccount(null);
        }
      },
    );
    return () => {
      offRegister();
      offUnregister();
    };
  }, [wallet]);

  const connect = useCallback(
    async (nextWallet: Wallet, cluster: SolanaCluster) => {
      setConnecting(true);
      setError(null);
      try {
        if (!isCompatibleWallet(nextWallet)) {
          throw new WalletPaymentError(
            "wallet_unsupported_transaction_version",
            "Wallet does not support the required Wallet Standard features.",
          );
        }
        const result = await nextWallet.features[StandardConnect].connect();
        const chain = clusterToWalletChain(cluster);
        const nextAccount = result.accounts.find(
          (candidate) =>
            candidate.chains.includes(chain) &&
            candidate.features.includes(SolanaSignAndSendTransaction),
        );
        if (!nextAccount) {
          throw new WalletPaymentError(
            "wallet_network_mismatch",
            "Wallet has no account for the selected Solana cluster.",
          );
        }
        setWallet(nextWallet);
        setAccount(nextAccount);
      } catch (caught) {
        const nextError = classifyWalletError(caught);
        setError(nextError);
        throw nextError;
      } finally {
        setConnecting(false);
      }
    },
    [],
  );

  const disconnectLocal = useCallback(() => {
    setWallet(null);
    setAccount(null);
    setError(null);
  }, []);

  return {
    wallets,
    wallet,
    account,
    connecting,
    error,
    connect,
    disconnectLocal,
  };
}

export function useSubmitWalletPayment(options: SubmitWalletPaymentOptions) {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: async ({ wallet, account }: SubmitWalletPaymentInput) => {
      const intent = options.intent;
      if (intent === null) {
        throw new WalletPaymentError(
          "verification_pending",
          "The payment intent is not available for wallet submission.",
        );
      }
      if (!isSolanaCheckoutAction(intent.action)) {
        throw new WalletPaymentError(
          "unsupported_instruction",
          "Connected-wallet payment requires a transaction-request action.",
        );
      }
      if (!isSolanaCheckoutActionActive(intent.action)) {
        throw new WalletPaymentError(
          "capability_expired",
          "The transaction-request action has expired.",
        );
      }
      if (intent.state !== "preparing" && intent.state !== "waiting") {
        throw new WalletPaymentError(
          "verification_pending",
          "The payment is no longer waiting for another wallet submission.",
        );
      }
      if (!isCompatibleWallet(wallet)) {
        throw new WalletPaymentError(
          "wallet_unsupported_transaction_version",
          "Wallet does not support signing and sending Solana transactions.",
        );
      }
      const chain = clusterToWalletChain(intent.settlement.cluster);
      if (!account.chains.includes(chain)) {
        throw new WalletPaymentError(
          "wallet_network_mismatch",
          "Connected account does not support the payment cluster.",
        );
      }
      if (!account.features.includes(SolanaSignAndSendTransaction)) {
        throw new WalletPaymentError(
          "wallet_unsupported_transaction_version",
          "Connected account cannot sign and send Solana transactions.",
        );
      }
      const feature = wallet.features[SolanaSignAndSendTransaction];
      if (!feature.supportedTransactionVersions.includes(0)) {
        throw new WalletPaymentError(
          "wallet_unsupported_transaction_version",
          "Wallet does not support version 0 transactions.",
        );
      }

      try {
        const request = await createTransactionRequest(
          options.publicExecutor,
          intent.action.uri,
          account.address,
        );
        const transaction = decodeBase64Transaction(request.transaction);
        const transactionForInspection = transaction.slice();
        const inspected = await (
          options.transactionInspector ?? kitTransactionInspector
        )(transactionForInspection);
        const sourceTokenAccount =
          intent.settlement.kind === "token" && intent.settlement.mint !== null
            ? await deriveToken2022AssociatedTokenAddress(
                account.address,
                intent.settlement.mint,
              )
            : null;
        validateInspectedTransaction(inspected, {
          payerAddress: account.address,
          sourceTokenAccount,
          settlement: intent.settlement,
        });
        const outputs = await feature.signAndSendTransaction({
          account,
          chain,
          transaction,
          options: {
            preflightCommitment: "confirmed",
            maxRetries: 3,
            skipPreflight: false,
          },
        });
        const output = outputs[0];
        if (!output || outputs.length !== 1 || output.signature.length !== 64) {
          throw new WalletPaymentError(
            "unsupported_instruction",
            "Wallet returned an invalid transaction signature.",
          );
        }
        const signature = encodeBase58(output.signature);
        const updatedIntent = await client.submitCandidateSignature({
          paymentPublicId: intent.paymentPublicId,
          signature,
        });
        queryClient.setQueryData(
          solanaCommerceQueryKeys.intent(updatedIntent.paymentPublicId),
          { ...updatedIntent, action: null },
        );
      } catch (caught) {
        throw classifyWalletError(caught);
      }
    },
  });
}

export function isCompatibleWallet(wallet: Wallet): wallet is CompatibleWallet {
  const connect = wallet.features[StandardConnect];
  const send = wallet.features[SolanaSignAndSendTransaction];
  return (
    typeof connect === "object" &&
    connect !== null &&
    "version" in connect &&
    connect.version === "1.0.0" &&
    "connect" in connect &&
    typeof connect.connect === "function" &&
    typeof send === "object" &&
    send !== null &&
    "version" in send &&
    send.version === "1.0.0" &&
    "supportedTransactionVersions" in send &&
    Array.isArray(send.supportedTransactionVersions) &&
    send.supportedTransactionVersions.includes(0) &&
    "signAndSendTransaction" in send &&
    typeof send.signAndSendTransaction === "function"
  );
}

export function clusterToWalletChain(
  cluster: SolanaCluster,
): `solana:${"mainnet" | "devnet" | "testnet"}` {
  return cluster === "mainnet-beta" ? "solana:mainnet" : `solana:${cluster}`;
}

function classifyWalletError(error: unknown): WalletPaymentError {
  if (error instanceof WalletPaymentError) return error;
  if (error instanceof SolanaCommerceError) {
    if (error.code === "http_error") {
      return new WalletPaymentError(
        "verification_pending",
        "The payment service could not confirm the wallet submission.",
        error,
      );
    }
    return new WalletPaymentError(
      "unsupported_instruction",
      "The payment transaction failed the local safety check.",
      error,
    );
  }
  return new WalletPaymentError(
    "wallet_rejected",
    "Wallet did not approve the payment transaction.",
    error,
  );
}
