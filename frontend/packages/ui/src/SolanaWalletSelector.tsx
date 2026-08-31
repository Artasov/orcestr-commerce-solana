"use client";

import { Button, Card, Stack, Text } from "@orcestr/ui";

import { useSolanaCommerceMessages } from "./i18n.js";

export type SolanaWalletChoice = {
  readonly id: string;
  readonly name: string;
  readonly iconDataUrl?: string | null;
};

export type SolanaWalletSelectorProps = {
  readonly wallets: readonly SolanaWalletChoice[];
  readonly selectedWalletId?: string | null;
  readonly connectingWalletId?: string | null;
  readonly onConnect: (walletId: string) => void;
  readonly className?: string;
};

export function SolanaWalletSelector({
  wallets,
  selectedWalletId = null,
  connectingWalletId = null,
  onConnect,
  className,
}: SolanaWalletSelectorProps) {
  const messages = useSolanaCommerceMessages();
  return (
    <Stack
      className={["ocs-wallet-selector", className].filter(Boolean).join(" ")}
      role="list"
    >
      {wallets.map((wallet) => (
        <Card
          className="ocs-wallet-choice"
          data-selected={wallet.id === selectedWalletId ? "true" : undefined}
          key={wallet.id}
          role="listitem"
          v="surface"
        >
          <span className="ocs-wallet-identity">
            {isSafeWalletIconDataUrl(wallet.iconDataUrl) ? (
              <img
                src={wallet.iconDataUrl}
                width={28}
                height={28}
                alt=""
                draggable={false}
              />
            ) : null}
            <Text fw={700}>{wallet.name}</Text>
          </span>
          <Button
            size={2}
            v="surface"
            type="button"
            loading={connectingWalletId === wallet.id}
            disabled={
              wallet.id === selectedWalletId ||
              (connectingWalletId !== null && connectingWalletId !== wallet.id)
            }
            aria-label={`${messages.actions.connectWallet}: ${wallet.name}`}
            onClick={() => onConnect(wallet.id)}
          >
            {connectingWalletId === wallet.id
              ? messages.actions.connectingWallet
              : messages.actions.connectWallet}
          </Button>
        </Card>
      ))}
    </Stack>
  );
}

export function isSafeWalletIconDataUrl(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 131_072 &&
    /^data:image\/[a-z0-9.+-]+(?:;[a-z0-9=.+-]+)*,/iu.test(value)
  );
}
