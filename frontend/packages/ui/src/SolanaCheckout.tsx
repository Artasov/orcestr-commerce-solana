"use client";

import {
  createSolanaPayHref,
  isSolanaCheckoutActionActive,
  shortenSolanaAddress,
  type SolanaCluster,
  type SolanaPaymentIntent,
  type SolanaPaymentStatus,
} from "@orcestr/commerce-solana-core";
import {
  Alert,
  Badge,
  Button,
  Card,
  CopyButton,
  Dialog,
  Stack,
  Text,
} from "@orcestr/ui";
import { useEffect, useState, type ReactNode } from "react";

import { useSolanaCommerceMessages } from "./i18n.js";
import { SolanaPaymentState } from "./SolanaPaymentState.js";
import { SolanaQrCode } from "./SolanaQrCode.js";

const ACTIONABLE_STATUSES = new Set<SolanaPaymentStatus>([
  "preparing",
  "waiting",
]);
const RETRY_STATUSES = new Set<SolanaPaymentStatus>([
  "expired",
  "cancelled",
  "failed",
]);

export type SolanaCheckoutProps = {
  readonly intent: SolanaPaymentIntent;
  readonly walletConnected?: boolean;
  readonly connectingWallet?: boolean;
  readonly submittingWalletPayment?: boolean;
  readonly requestingAction?: boolean;
  readonly showQr?: boolean;
  readonly showHeader?: boolean;
  readonly onConnectWallet?: () => void;
  readonly onPayWithWallet?: () => void;
  readonly onCancel?: () => void;
  readonly onRetry?: () => void;
  readonly onRequestAction?: () => void;
  readonly formatExpiresAt?: (isoTimestamp: string) => ReactNode;
  readonly className?: string;
};

export function SolanaCheckout({
  intent,
  walletConnected = false,
  connectingWallet = false,
  submittingWalletPayment = false,
  requestingAction = false,
  showQr = true,
  showHeader = true,
  onConnectWallet,
  onPayWithWallet,
  onCancel,
  onRetry,
  onRequestAction,
  formatExpiresAt = defaultFormatExpiresAt,
  className,
}: SolanaCheckoutProps) {
  const messages = useSolanaCommerceMessages();
  const action = intent.action;
  const checkedAction = useCheckedActiveAction(action);
  const actionChecked = checkedAction.source === action;
  const activeAction = actionChecked ? checkedAction.active : null;
  const actionable =
    ACTIONABLE_STATUSES.has(intent.state) && activeAction !== null;
  const walletSubmissionLocked = submittingWalletPayment;
  const activeWithoutAction =
    ACTIONABLE_STATUSES.has(intent.state) &&
    actionChecked &&
    activeAction === null;
  const { settlement } = intent;
  const mint = settlement.kind === "token" ? settlement.mint : null;

  return (
    <Card
      className={["ocs-checkout", className].filter(Boolean).join(" ")}
      v="surface"
      size={3}
    >
      <Stack g={4}>
        {showHeader ? (
          <header className="ocs-checkout-header">
            <div>
              <Text as="h2" fw={800} fs="20px">
                {messages.checkout.title}
              </Text>
              <Text as="p" tone="muted" fs="14px">
                {messages.checkout.description}
              </Text>
            </div>
            <Badge
              tone={settlement.cluster === "mainnet-beta" ? "warning" : "info"}
            >
              {clusterLabel(settlement.cluster)}
            </Badge>
          </header>
        ) : null}

        <Alert
          tone={settlement.cluster === "mainnet-beta" ? "warning" : "info"}
        >
          {settlement.cluster === "mainnet-beta"
            ? messages.checkout.mainnetNotice
            : messages.checkout.testNetworkNotice}
        </Alert>

        <SolanaPaymentState
          status={intent.state}
          reasonCode={intent.reasonCode}
          {...(RETRY_STATUSES.has(intent.state) && onRetry ? { onRetry } : {})}
        />

        <div className="ocs-checkout-grid">
          {actionable && showQr && !walletSubmissionLocked ? (
            <div className="ocs-checkout-qr">
              <SolanaQrCode uri={activeAction.uri} />
            </div>
          ) : null}
          <dl className="ocs-checkout-details">
            <Detail
              label={messages.checkout.amount}
              value={`${settlement.displayAmount} ${settlement.assetSymbol}`}
              prominent
            />
            <Detail
              label={messages.checkout.network}
              value={clusterLabel(settlement.cluster)}
            />
            <Detail
              label={messages.checkout.mint}
              value={
                mint
                  ? shortenSolanaAddress(mint, 6)
                  : messages.checkout.nativeAsset
              }
              action={
                mint ? (
                  <CopyButton
                    size={1}
                    v="ghost"
                    text={mint}
                    label={messages.actions.copy}
                    copiedLabel={messages.actions.copied}
                    successMessage=""
                  />
                ) : null
              }
            />
            <Detail
              label={messages.checkout.recipient}
              value={shortenSolanaAddress(settlement.recipientWallet, 6)}
              action={
                <CopyButton
                  size={1}
                  v="ghost"
                  text={settlement.recipientWallet}
                  label={messages.actions.copy}
                  copiedLabel={messages.actions.copied}
                  successMessage=""
                />
              }
            />
            <Detail
              label={messages.checkout.expires}
              value={formatExpiresAt(
                activeAction?.expiresAt ?? intent.expiresAt,
              )}
            />
          </dl>
        </div>

        {actionable ? (
          <div className="ocs-checkout-actions">
            {walletConnected && onPayWithWallet ? (
              <Button
                fullWidth
                type="button"
                loading={submittingWalletPayment}
                onClick={onPayWithWallet}
              >
                {submittingWalletPayment
                  ? messages.actions.paying
                  : messages.actions.payWithWallet}
              </Button>
            ) : onConnectWallet ? (
              <Button
                fullWidth
                type="button"
                loading={connectingWallet}
                onClick={onConnectWallet}
              >
                {connectingWallet
                  ? messages.actions.connectingWallet
                  : messages.actions.connectWallet}
              </Button>
            ) : null}
            {!walletSubmissionLocked ? (
              <>
                <Button fullWidth v="surface" asChild>
                  <a href={createSolanaPayHref(activeAction)} rel="noreferrer">
                    {messages.actions.openWallet}
                  </a>
                </Button>
                <CopyButton
                  fullWidth
                  v="ghost"
                  text={activeAction.uri}
                  label={messages.actions.copy}
                  copiedLabel={messages.actions.copied}
                  successMessage=""
                />
              </>
            ) : null}
          </div>
        ) : null}

        {activeWithoutAction ? (
          <Alert tone="info">{messages.checkout.actionUnavailable}</Alert>
        ) : null}

        {activeWithoutAction && onRequestAction ? (
          <Button
            fullWidth
            type="button"
            loading={requestingAction}
            onClick={onRequestAction}
          >
            {requestingAction
              ? messages.actions.requestingAction
              : messages.actions.requestAction}
          </Button>
        ) : null}

        {onCancel && ACTIONABLE_STATUSES.has(intent.state) ? (
          <Button type="button" v="ghost" tone="danger" onClick={onCancel}>
            {messages.actions.cancel}
          </Button>
        ) : null}
      </Stack>
    </Card>
  );
}

export type SolanaCheckoutDialogProps = SolanaCheckoutProps & {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
};

export function SolanaCheckoutDialog({
  open,
  onOpenChange,
  ...checkoutProps
}: SolanaCheckoutDialogProps) {
  const messages = useSolanaCommerceMessages();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content className="ocs-checkout-dialog">
        <Dialog.Title>{messages.checkout.title}</Dialog.Title>
        <Dialog.Description>{messages.checkout.description}</Dialog.Description>
        <SolanaCheckout {...checkoutProps} showHeader={false} />
        <Dialog.Close>
          <Button fullWidth v="ghost" type="button">
            {messages.actions.close}
          </Button>
        </Dialog.Close>
      </Dialog.Content>
    </Dialog.Root>
  );
}

type CheckedAction = {
  readonly source: SolanaPaymentIntent["action"];
  readonly active: SolanaPaymentIntent["action"];
};

function useCheckedActiveAction(
  action: SolanaPaymentIntent["action"],
): CheckedAction {
  const [checked, setChecked] = useState<CheckedAction>({
    source: null,
    active: null,
  });

  useEffect(() => {
    if (!action) {
      setChecked({ source: null, active: null });
      return;
    }
    const now = Date.now();
    if (!isSolanaCheckoutActionActive(action, now)) {
      setChecked({ source: action, active: null });
      return;
    }
    setChecked({ source: action, active: action });
    const timer = window.setTimeout(
      () => setChecked({ source: action, active: null }),
      Math.min(Date.parse(action.expiresAt) - now + 25, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [action]);

  return checked;
}

function Detail({
  label,
  value,
  action,
  prominent = false,
}: {
  readonly label: string;
  readonly value: ReactNode;
  readonly action?: ReactNode;
  readonly prominent?: boolean;
}) {
  return (
    <div className="ocs-detail" data-prominent={prominent ? "true" : undefined}>
      <dt>{label}</dt>
      <dd>
        <span>{value}</span>
        {action}
      </dd>
    </div>
  );
}

function clusterLabel(cluster: SolanaCluster): string {
  return cluster === "mainnet-beta" ? "mainnet" : cluster;
}

function defaultFormatExpiresAt(isoTimestamp: string): string {
  return new Date(isoTimestamp).toISOString();
}
