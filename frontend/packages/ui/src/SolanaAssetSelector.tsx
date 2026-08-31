"use client";

import {
  shortenSolanaAddress,
  type SolanaPaymentOption,
} from "@orcestr/commerce-solana-core";
import { Badge, Text } from "@orcestr/ui";
import { useId } from "react";

import { useSolanaCommerceMessages } from "./i18n.js";

export type SolanaAssetSelectorProps = {
  readonly options: readonly SolanaPaymentOption[];
  readonly selectedOptionId: string | null;
  readonly onChange: (paymentOptionId: string) => void;
  readonly disabled?: boolean;
  readonly className?: string;
};

export function SolanaAssetSelector({
  options,
  selectedOptionId,
  onChange,
  disabled = false,
  className,
}: SolanaAssetSelectorProps) {
  const messages = useSolanaCommerceMessages();
  const groupName = `solana-payment-option-${useId()}`;
  return (
    <fieldset
      className={["ocs-asset-selector", className].filter(Boolean).join(" ")}
      disabled={disabled}
    >
      <legend>{messages.assetSelector.legend}</legend>
      <div className="ocs-asset-options">
        {options.map((option) => (
          <label
            className="ocs-asset-option"
            data-selected={option.id === selectedOptionId ? "true" : undefined}
            key={option.id}
          >
            <input
              type="radio"
              name={groupName}
              value={option.id}
              checked={option.id === selectedOptionId}
              onChange={() => onChange(option.id)}
            />
            <span className="ocs-asset-option-main">
              <span className="ocs-asset-option-title">
                <Text fw={700}>{option.symbol}</Text>
                <Badge size={1}>{option.requiredCommitment}</Badge>
              </span>
              <Text as="span" tone="muted" fs="12px">
                {option.mint
                  ? shortenSolanaAddress(option.mint)
                  : messages.checkout.nativeAsset}
              </Text>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
