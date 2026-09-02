"use client";

import type {
  KnownSolanaPaymentReasonCode,
  SolanaPaymentStatus,
} from "@orcestr/commerce-solana-core";
import {
  createContext,
  useContext,
  useMemo,
  type ReactNode,
} from "react";

export type SolanaCommerceLocale = "en" | "ru";

export type SolanaCommerceMessages = {
  readonly checkout: {
    readonly title: string;
    readonly description: string;
    readonly amount: string;
    readonly network: string;
    readonly mint: string;
    readonly nativeAsset: string;
    readonly recipient: string;
    readonly expires: string;
    readonly qrAlt: string;
    readonly qrLoading: string;
    readonly qrUnavailable: string;
    readonly unknownReason: string;
  };
  readonly actions: {
    readonly connectWallet: string;
    readonly connectingWallet: string;
    readonly payWithWallet: string;
    readonly paying: string;
    readonly openWallet: string;
    readonly copy: string;
    readonly copied: string;
    readonly cancel: string;
    readonly retry: string;
    readonly close: string;
  };
  readonly assetSelector: {
    readonly legend: string;
    readonly price: string;
  };
  readonly status: Record<SolanaPaymentStatus, string>;
  readonly statusDescription: Record<SolanaPaymentStatus, string>;
  readonly reason: Record<KnownSolanaPaymentReasonCode, string>;
};

export type SolanaCommerceMessageOverrides = {
  readonly [Section in keyof SolanaCommerceMessages]?: Partial<
    SolanaCommerceMessages[Section]
  >;
};

export const solanaCommerceMessages: Record<
  SolanaCommerceLocale,
  SolanaCommerceMessages
> = {
  en: {
    checkout: {
      title: "Pay with Solana",
      description:
        "Check the network, asset, amount and recipient before approving the transaction.",
      amount: "Amount",
      network: "Network",
      mint: "Token-2022 mint",
      nativeAsset: "Native SOL",
      recipient: "Recipient",
      expires: "Expires",
      qrAlt: "Solana payment QR code",
      qrLoading: "Generating payment QR code",
      qrUnavailable: "The QR code could not be generated.",
      unknownReason: "The backend reported a payment verification issue.",
    },
    actions: {
      connectWallet: "Connect wallet",
      connectingWallet: "Connecting...",
      payWithWallet: "Approve payment",
      paying: "Submitting...",
      openWallet: "Open wallet",
      copy: "Copy",
      copied: "Copied",
      cancel: "Cancel payment",
      retry: "Try again",
      close: "Close",
    },
    assetSelector: {
      legend: "Choose a settlement asset",
      price: "You pay",
    },
    status: {
      preparing: "Preparing payment",
      waiting: "Waiting for payment",
      observed: "Transaction detected",
      confirmed: "Transaction confirmed",
      paid: "Paid",
      expired: "Payment expired",
      cancelled: "Payment cancelled",
      failed: "Payment failed",
      review: "Payment under review",
    },
    statusDescription: {
      preparing: "The server is creating a short-lived payment request.",
      waiting: "Approve the exact transaction in your wallet.",
      observed: "The server found the transaction and is checking it.",
      confirmed: "The transaction is confirmed. Finalization is still pending.",
      paid: "The backend verified the payment and completed the order.",
      expired: "This request can no longer be used. Create a new payment attempt.",
      cancelled: "This payment attempt was cancelled.",
      failed: "The backend could not accept this payment.",
      review: "The payment needs manual review before the order can be completed.",
    },
    reason: {
      invalid_address: "A Solana address is invalid.",
      invalid_amount: "The payment amount is invalid.",
      invalid_configuration: "The payment provider is not configured correctly.",
      wrong_cluster: "The transaction belongs to a different Solana cluster.",
      rpc_temporarily_unavailable: "Solana RPC is temporarily unavailable.",
      rpc_invalid_response: "Solana RPC returned an invalid response.",
      asset_not_found: "The selected payment asset was not found.",
      asset_inactive: "The selected payment asset is no longer active.",
      wrong_mint_owner: "The mint is not owned by the required token program.",
      legacy_token_program: "The transaction uses an unsupported token program.",
      unsupported_token_extension: "This token extension is not accepted.",
      unsafe_mint_authority: "The mint authority violates the asset policy.",
      unsafe_freeze_authority: "The freeze authority violates the asset policy.",
      invalid_token_account: "The recipient token account is invalid.",
      capability_invalid: "The payment request is invalid.",
      capability_expired: "The payment request has expired.",
      intent_expired: "The payment attempt has expired.",
      transaction_not_found: "The transaction has not been found yet.",
      transaction_failed: "The transaction failed on-chain.",
      transaction_not_final: "The transaction has not reached required finality.",
      transaction_decode_failed: "The transaction could not be decoded safely.",
      signature_mismatch: "The transaction signature does not match.",
      signature_already_used: "This signature was already used for another payment.",
      issuance_mismatch: "The transaction does not match an issued request.",
      wrong_program: "The transaction uses a different program.",
      wrong_mint: "The transaction uses a different mint.",
      wrong_recipient: "The transaction pays a different recipient.",
      wrong_amount: "The transferred raw amount does not match.",
      wrong_decimals: "The mint decimals do not match the asset snapshot.",
      wrong_decimals_in_instruction: "The transfer instruction uses different decimals.",
      invalid_balance_delta: "The recipient balance delta does not match.",
      multiple_payment_instructions: "The transaction contains multiple payment instructions.",
      reference_missing: "The payment reference is missing from the transaction.",
      unsupported_instruction: "The transaction instruction is not supported.",
      unsupported_cpi_or_swap: "Program-mediated and swap payments are not accepted.",
      late_payment: "The payment arrived outside the accepted time window.",
      block_time_unavailable: "The transaction time cannot be verified yet.",
      wallet_rejected: "The wallet did not approve the transaction.",
      wallet_not_found: "No compatible Solana wallet was found.",
      wallet_network_mismatch: "The wallet is connected to another network.",
      wallet_unsupported_transaction_version:
        "The wallet cannot sign the required transaction version.",
      verification_pending: "Verification is still in progress.",
    },
  },
  ru: {
    checkout: {
      title: "Оплата через Solana",
      description:
        "Перед подтверждением проверьте сеть, актив, сумму и получателя.",
      amount: "Сумма",
      network: "Сеть",
      mint: "Mint Token-2022",
      nativeAsset: "Нативный SOL",
      recipient: "Получатель",
      expires: "Действует до",
      qrAlt: "QR-код оплаты через Solana",
      qrLoading: "Создаём QR-код оплаты",
      qrUnavailable: "Не удалось создать QR-код.",
      unknownReason: "Backend сообщил о проблеме при проверке платежа.",
    },
    actions: {
      connectWallet: "Подключить кошелёк",
      connectingWallet: "Подключаем...",
      payWithWallet: "Подтвердить оплату",
      paying: "Отправляем...",
      openWallet: "Открыть кошелёк",
      copy: "Копировать",
      copied: "Скопировано",
      cancel: "Отменить платёж",
      retry: "Попробовать снова",
      close: "Закрыть",
    },
    assetSelector: {
      legend: "Выберите актив для оплаты",
      price: "К оплате",
    },
    status: {
      preparing: "Готовим платёж",
      waiting: "Ожидаем оплату",
      observed: "Транзакция обнаружена",
      confirmed: "Транзакция подтверждена",
      paid: "Оплачено",
      expired: "Срок платежа истёк",
      cancelled: "Платёж отменён",
      failed: "Ошибка платежа",
      review: "Платёж на проверке",
    },
    statusDescription: {
      preparing: "Сервер формирует короткоживущий платёжный запрос.",
      waiting: "Подтвердите точную транзакцию в кошельке.",
      observed: "Сервер нашёл транзакцию и проверяет её.",
      confirmed: "Транзакция подтверждена, но ещё ожидает финализации.",
      paid: "Backend проверил платёж и выполнил заказ.",
      expired: "Этот запрос больше нельзя использовать. Создайте новую попытку.",
      cancelled: "Эта попытка оплаты отменена.",
      failed: "Backend не смог принять этот платёж.",
      review: "До выполнения заказа платёж должен проверить оператор.",
    },
    reason: {
      invalid_address: "Указан некорректный адрес Solana.",
      invalid_amount: "Указана некорректная сумма платежа.",
      invalid_configuration: "Платёжный провайдер настроен некорректно.",
      wrong_cluster: "Транзакция относится к другой сети Solana.",
      rpc_temporarily_unavailable: "Solana RPC временно недоступен.",
      rpc_invalid_response: "Solana RPC вернул некорректный ответ.",
      asset_not_found: "Выбранный платёжный актив не найден.",
      asset_inactive: "Выбранный платёжный актив больше не активен.",
      wrong_mint_owner: "Mint принадлежит другой токен-программе.",
      legacy_token_program: "Транзакция использует неподдерживаемую токен-программу.",
      unsupported_token_extension: "Это расширение токена не принимается.",
      unsafe_mint_authority: "Mint authority нарушает политику актива.",
      unsafe_freeze_authority: "Freeze authority нарушает политику актива.",
      invalid_token_account: "Токен-счёт получателя некорректен.",
      capability_invalid: "Платёжный запрос недействителен.",
      capability_expired: "Срок платёжного запроса истёк.",
      intent_expired: "Срок попытки оплаты истёк.",
      transaction_not_found: "Транзакция пока не найдена.",
      transaction_failed: "Транзакция завершилась ошибкой в блокчейне.",
      transaction_not_final: "Транзакция ещё не достигла нужной финальности.",
      transaction_decode_failed: "Не удалось безопасно разобрать транзакцию.",
      signature_mismatch: "Подпись транзакции не совпадает.",
      signature_already_used: "Эта подпись уже использована для другого платежа.",
      issuance_mismatch: "Транзакция не соответствует выданному запросу.",
      wrong_program: "Транзакция использует другую программу.",
      wrong_mint: "В транзакции указан другой mint.",
      wrong_recipient: "Транзакция отправлена другому получателю.",
      wrong_amount: "Переведённое количество base units не совпадает.",
      wrong_decimals: "Decimals mint не совпадают со snapshot актива.",
      wrong_decimals_in_instruction: "В инструкции перевода указаны другие decimals.",
      invalid_balance_delta: "Изменение баланса получателя не совпадает.",
      multiple_payment_instructions: "В транзакции несколько платёжных инструкций.",
      reference_missing: "В транзакции нет reference платежа.",
      unsupported_instruction: "Инструкция транзакции не поддерживается.",
      unsupported_cpi_or_swap: "Платежи через программы и swap не принимаются.",
      late_payment: "Платёж пришёл вне допустимого временного окна.",
      block_time_unavailable: "Время транзакции пока нельзя проверить.",
      wallet_rejected: "Кошелёк не подтвердил транзакцию.",
      wallet_not_found: "Совместимый кошелёк Solana не найден.",
      wallet_network_mismatch: "Кошелёк подключён к другой сети.",
      wallet_unsupported_transaction_version:
        "Кошелёк не поддерживает требуемую версию транзакции.",
      verification_pending: "Проверка ещё выполняется.",
    },
  },
};

const SolanaCommerceI18nContext = createContext<SolanaCommerceMessages>(
  solanaCommerceMessages.en,
);

export function SolanaCommerceI18nProvider({
  locale,
  overrides,
  children,
}: {
  readonly locale: SolanaCommerceLocale;
  readonly overrides?: SolanaCommerceMessageOverrides;
  readonly children: ReactNode;
}) {
  const messages = useMemo(
    () => mergeMessages(solanaCommerceMessages[locale], overrides),
    [locale, overrides],
  );
  return (
    <SolanaCommerceI18nContext.Provider value={messages}>
      {children}
    </SolanaCommerceI18nContext.Provider>
  );
}

export function useSolanaCommerceMessages(): SolanaCommerceMessages {
  return useContext(SolanaCommerceI18nContext);
}

function mergeMessages(
  base: SolanaCommerceMessages,
  overrides?: SolanaCommerceMessageOverrides,
): SolanaCommerceMessages {
  if (!overrides) return base;
  return Object.fromEntries(
    Object.entries(base).map(([key, value]) => [
      key,
      { ...value, ...(overrides[key as keyof SolanaCommerceMessages] ?? {}) },
    ]),
  ) as SolanaCommerceMessages;
}
