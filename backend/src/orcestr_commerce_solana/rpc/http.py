from __future__ import annotations

import asyncio
import base64
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from itertools import count
from typing import Any

import httpx

from orcestr_commerce_solana.config import SolanaRpcConfig
from orcestr_commerce_solana.constants import MAX_SOLANA_TRANSACTION_BYTES
from orcestr_commerce_solana.errors import (
    SolanaErrorCode,
    SolanaRpcResponseError,
    SolanaRpcUnavailableError,
)
from orcestr_commerce_solana.rpc.types import (
    RpcAccountInfo,
    RpcLatestBlockhash,
    RpcSignatureInfo,
    RpcSignatureStatus,
    RpcTransaction,
)
from orcestr_commerce_solana.schemas.assets import SolanaCommitment


class HttpSolanaRpc:
    """Uses ordinary Solana HTTP JSON-RPC with cluster pinning and failover."""

    retryable_rpc_codes = {-32005, -32004, -32002, -32603}

    def __init__(
        self,
        config: SolanaRpcConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._owns_client = client is None
        self._sleeper = sleeper
        self._random_value = random_value
        self._request_ids = count(1)
        self._validated_endpoints: set[str] = set()
        self._circuit_until: dict[str, float] = {}

    async def close(self) -> None:
        """Closes the internally owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpSolanaRpc:
        """Returns this pool for an async context manager."""
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Closes the internally owned HTTP client after context exit."""
        _ = exc_type
        _ = exc
        _ = traceback
        await self.close()

    async def check_cluster(self) -> str:
        """Accepts the pool only when at least one endpoint has the exact genesis hash."""
        result = await self._call("getGenesisHash", [])
        genesis_hash = self._read_string(result, "getGenesisHash")
        if genesis_hash != self.config.genesis_hash:
            raise SolanaRpcResponseError(
                SolanaErrorCode.WRONG_CLUSTER,
                f"RPC returned genesis hash {genesis_hash}, expected {self.config.genesis_hash}.",
            )
        return genesis_hash

    async def get_account_info(self, address: str, commitment: SolanaCommitment) -> RpcAccountInfo | None:
        """Reads an account using base64 so domain code decodes Token-2022 itself."""
        result = await self._call(
            "getAccountInfo",
            [address, {"encoding": "base64", "commitment": commitment.value}],
        )
        value = self._read_context_value(result, "getAccountInfo")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise self._invalid_response("getAccountInfo value must be an object or null.")
        data = value.get("data")
        if not isinstance(data, list) or len(data) != 2 or data[1] != "base64" or not isinstance(data[0], str):
            raise self._invalid_response("getAccountInfo did not return base64 account data.")
        try:
            raw_data = base64.b64decode(data[0], validate=True)
        except ValueError as exc:
            raise self._invalid_response("getAccountInfo returned invalid base64 data.") from exc
        try:
            return RpcAccountInfo(
                owner=value["owner"],
                lamports=value["lamports"],
                executable=value["executable"],
                rent_epoch=value["rentEpoch"],
                data=raw_data,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_response("getAccountInfo fields are invalid.") from exc

    async def get_latest_blockhash(self, commitment: SolanaCommitment) -> RpcLatestBlockhash:
        """Reads the blockhash used by a persisted transaction issuance."""
        result = await self._call("getLatestBlockhash", [{"commitment": commitment.value}])
        value = self._read_context_value(result, "getLatestBlockhash")
        context_slot = self._read_context_slot(result, "getLatestBlockhash")
        if not isinstance(value, dict):
            raise self._invalid_response("getLatestBlockhash value must be an object.")
        try:
            return RpcLatestBlockhash(
                blockhash=value["blockhash"],
                last_valid_block_height=value["lastValidBlockHeight"],
                context_slot=context_slot,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_response("getLatestBlockhash fields are invalid.") from exc

    async def get_block_height(self, commitment: SolanaCommitment) -> int:
        """Reads current block height for safe issuance cutoffs."""
        result = await self._call("getBlockHeight", [{"commitment": commitment.value}])
        if isinstance(result, bool) or not isinstance(result, int) or result < 0:
            raise self._invalid_response("getBlockHeight must return a non-negative integer.")
        return result

    async def get_signature_status(self, signature: str) -> RpcSignatureStatus | None:
        """Reads one status and explicitly searches historical transactions."""
        result = await self._call(
            "getSignatureStatuses",
            [[signature], {"searchTransactionHistory": True}],
        )
        value = self._read_context_value(result, "getSignatureStatuses")
        if not isinstance(value, list) or len(value) != 1:
            raise self._invalid_response("getSignatureStatuses must return exactly one requested value.")
        status = value[0]
        if status is None:
            return None
        if not isinstance(status, dict):
            raise self._invalid_response("Signature status must be an object or null.")
        try:
            return RpcSignatureStatus(
                slot=status["slot"],
                confirmations=status.get("confirmations"),
                confirmation_status=status.get("confirmationStatus"),
                error=status.get("err"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_response("Signature status fields are invalid.") from exc

    async def get_transaction(self, signature: str, commitment: SolanaCommitment) -> RpcTransaction | None:
        """Reads the raw transaction; jsonParsed is never used for a financial verdict."""
        result = await self._call(
            "getTransaction",
            [signature, {"encoding": "base64", "commitment": commitment.value, "maxSupportedTransactionVersion": 0}],
        )
        if result is None:
            return None
        if not isinstance(result, dict):
            raise self._invalid_response("getTransaction result must be an object or null.")
        encoded = result.get("transaction")
        meta = result.get("meta")
        if not isinstance(encoded, list) or len(encoded) != 2 or encoded[1] != "base64" or not isinstance(encoded[0], str):
            raise self._invalid_response("getTransaction did not return a base64 transaction.")
        if not isinstance(meta, dict):
            raise self._invalid_response("getTransaction did not return transaction metadata.")
        max_base64_length = 4 * ((MAX_SOLANA_TRANSACTION_BYTES + 2) // 3)
        if not encoded[0] or len(encoded[0]) > max_base64_length:
            raise self._invalid_response("getTransaction transaction exceeds the Solana wire-size limit.")
        try:
            raw_transaction = base64.b64decode(encoded[0], validate=True)
        except ValueError as exc:
            raise self._invalid_response("getTransaction returned invalid base64 data.") from exc
        if not raw_transaction or len(raw_transaction) > MAX_SOLANA_TRANSACTION_BYTES:
            raise self._invalid_response("getTransaction transaction exceeds the Solana wire-size limit.")
        block_time = self._read_block_time(result.get("blockTime"))
        try:
            return RpcTransaction(
                slot=result["slot"],
                block_time=block_time,
                raw_transaction=raw_transaction,
                meta=meta,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_response("getTransaction fields are invalid.") from exc

    async def get_signatures_for_address(
        self,
        address: str,
        *,
        before: str | None = None,
        limit: int = 100,
        commitment: SolanaCommitment = SolanaCommitment.CONFIRMED,
    ) -> tuple[RpcSignatureInfo, ...]:
        """Finds candidate signatures by paginating a unique reference account."""
        if limit < 1 or limit > 1000:
            raise ValueError("Signature page limit must be between 1 and 1000.")
        config: dict[str, Any] = {"limit": limit, "commitment": commitment.value}
        if before is not None:
            config["before"] = before
        result = await self._call("getSignaturesForAddress", [address, config])
        if not isinstance(result, list):
            raise self._invalid_response("getSignaturesForAddress must return a list.")
        items: list[RpcSignatureInfo] = []
        for raw in result:
            if not isinstance(raw, dict):
                raise self._invalid_response("Signature history entry must be an object.")
            try:
                items.append(
                    RpcSignatureInfo(
                        signature=raw["signature"],
                        slot=raw["slot"],
                        block_time=self._read_block_time(raw.get("blockTime")),
                        error=raw.get("err"),
                        memo=raw.get("memo"),
                    ),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise self._invalid_response("Signature history fields are invalid.") from exc
        return tuple(items)

    async def _call(self, method: str, params: list[Any], *, check_cluster: bool = True) -> Any:
        """Tries configured endpoints in order and preserves retryable outage semantics."""
        failures: list[str] = []
        wrong_cluster: SolanaRpcResponseError | None = None
        for endpoint in self.config.endpoints:
            if self._circuit_until.get(endpoint, 0) > time.monotonic():
                continue
            for attempt in range(self.config.max_attempts_per_endpoint):
                try:
                    if check_cluster and endpoint not in self._validated_endpoints:
                        await self._validate_endpoint(endpoint)
                    return await self._raw_call(endpoint, method, params)
                except SolanaRpcUnavailableError as exc:
                    failures.append(str(exc))
                    if attempt + 1 < self.config.max_attempts_per_endpoint:
                        backoff = self.config.base_backoff_seconds * 2**attempt
                        await self._sleeper(backoff * (0.5 + self._random_value()))
                except SolanaRpcResponseError as exc:
                    if exc.code != SolanaErrorCode.WRONG_CLUSTER:
                        raise
                    wrong_cluster = exc
                    failures.append(str(exc))
                    break
            self._circuit_until[endpoint] = time.monotonic() + self.config.circuit_break_seconds
        if wrong_cluster is not None:
            raise wrong_cluster
        detail = "; ".join(failures[-3:]) or "All JSON-RPC endpoints have an open circuit."
        raise SolanaRpcUnavailableError(SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE, detail)

    async def _validate_endpoint(self, endpoint: str) -> None:
        """Pins each fallback endpoint to the expected cluster before financial reads."""
        result = await self._raw_call(endpoint, "getGenesisHash", [])
        genesis_hash = self._read_string(result, "getGenesisHash")
        if genesis_hash != self.config.genesis_hash:
            raise SolanaRpcResponseError(
                SolanaErrorCode.WRONG_CLUSTER,
                f"RPC endpoint returned genesis hash {genesis_hash}, expected {self.config.genesis_hash}.",
            )
        self._validated_endpoints.add(endpoint)

    async def _raw_call(self, endpoint: str, method: str, params: list[Any]) -> Any:
        """Executes one JSON-RPC request without domain-level parsing."""
        payload = {"jsonrpc": "2.0", "id": next(self._request_ids), "method": method, "params": params}
        try:
            response = await self._client.post(endpoint, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SolanaRpcUnavailableError(
                SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
                f"JSON-RPC transport unavailable for {method}.",
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise SolanaRpcUnavailableError(
                SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
                f"JSON-RPC returned retryable HTTP status {response.status_code} for {method}.",
            )
        if response.status_code >= 400:
            raise self._invalid_response(f"JSON-RPC returned HTTP status {response.status_code} for {method}.")
        try:
            body = response.json()
        except ValueError as exc:
            raise self._invalid_response(f"JSON-RPC returned invalid JSON for {method}.") from exc
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            raise self._invalid_response(f"JSON-RPC envelope is invalid for {method}.")
        error = body.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            if code in self.retryable_rpc_codes:
                raise SolanaRpcUnavailableError(
                    SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
                    f"JSON-RPC returned retryable error code {code} for {method}.",
                )
            raise self._invalid_response(f"JSON-RPC returned error code {code} for {method}.")
        if "result" not in body:
            raise self._invalid_response(f"JSON-RPC response has no result for {method}.")
        return body["result"]

    @staticmethod
    def _read_context_value(result: Any, method: str) -> Any:
        """Extracts the value from a standard context response."""
        if not isinstance(result, dict) or "value" not in result:
            raise SolanaRpcResponseError(
                SolanaErrorCode.RPC_INVALID_RESPONSE,
                f"{method} response has no context value.",
            )
        return result["value"]

    @staticmethod
    def _read_context_slot(result: Any, method: str) -> int:
        """Reads the exact slot attached to a context response."""
        if not isinstance(result, dict) or not isinstance(result.get("context"), dict):
            raise SolanaRpcResponseError(
                SolanaErrorCode.RPC_INVALID_RESPONSE,
                f"{method} response has no context object.",
            )
        slot = result["context"].get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
            raise SolanaRpcResponseError(
                SolanaErrorCode.RPC_INVALID_RESPONSE,
                f"{method} context slot must be a non-negative integer.",
            )
        return slot

    @staticmethod
    def _read_string(result: Any, method: str) -> str:
        """Reads a scalar string result."""
        if not isinstance(result, str) or not result:
            raise SolanaRpcResponseError(
                SolanaErrorCode.RPC_INVALID_RESPONSE,
                f"{method} must return a non-empty string.",
            )
        return result

    @staticmethod
    def _read_block_time(value: Any) -> datetime | None:
        """Converts an optional Unix timestamp to aware UTC."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise SolanaRpcResponseError(
                SolanaErrorCode.RPC_INVALID_RESPONSE,
                "Block time must be an integer or null.",
            )
        return datetime.fromtimestamp(value, UTC)

    @staticmethod
    def _invalid_response(message: str) -> SolanaRpcResponseError:
        """Creates a consistent non-retryable response error."""
        return SolanaRpcResponseError(SolanaErrorCode.RPC_INVALID_RESPONSE, message)
