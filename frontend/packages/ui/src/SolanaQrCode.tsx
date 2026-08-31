"use client";

import { parseSolanaPayUri } from "@orcestr/commerce-solana-core";
import { Alert, Spinner } from "@orcestr/ui";
import { toDataURL } from "qrcode";
import { useEffect, useState } from "react";

import { useSolanaCommerceMessages } from "./i18n.js";

export type SolanaQrCodeProps = {
  readonly uri: string;
  readonly size?: number;
  readonly className?: string;
};

export function SolanaQrCode({
  uri,
  size = 240,
  className,
}: SolanaQrCodeProps) {
  const messages = useSolanaCommerceMessages();
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setDataUrl(null);
    setFailed(false);
    try {
      const canonicalUri = parseSolanaPayUri(uri);
      void toDataURL(canonicalUri, {
        errorCorrectionLevel: "M",
        margin: 2,
        width: size,
        color: { dark: "#111827", light: "#ffffff" },
      })
        .then((nextDataUrl) => {
          if (active) setDataUrl(nextDataUrl);
        })
        .catch(() => {
          if (active) setFailed(true);
        });
    } catch {
      setFailed(true);
    }
    return () => {
      active = false;
    };
  }, [size, uri]);

  if (failed) {
    return <Alert tone="danger">{messages.checkout.qrUnavailable}</Alert>;
  }
  if (!dataUrl) {
    return (
      <span
        className="ocs-qr-loading"
        role="status"
        aria-live="polite"
        aria-label={messages.checkout.qrLoading}
      >
        <Spinner size={3} />
      </span>
    );
  }
  return (
    <img
      className={["ocs-qr", className].filter(Boolean).join(" ")}
      src={dataUrl}
      width={size}
      height={size}
      alt={messages.checkout.qrAlt}
      draggable={false}
    />
  );
}
