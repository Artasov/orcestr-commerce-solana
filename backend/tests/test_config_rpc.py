from __future__ import annotations

import json
import base64
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from orcestr_commerce_solana.config import SolanaCommerceConfig, SolanaRpcConfig
from orcestr_commerce_solana.constants import DEVNET_GENESIS_HASH, MAINNET_GENESIS_HASH
from orcestr_commerce_solana.errors import SolanaErrorCode, SolanaRpcResponseError, SolanaRpcUnavailableError
from orcestr_commerce_solana.rpc.http import HttpSolanaRpc
from orcestr_commerce_solana.schemas.assets import SolanaCluster
from orcestr_commerce_solana.schemas.assets import SolanaCommitment
from orcestr_commerce_solana.schemas.transactions import SolanaPayGetResponse
from solders.hash import Hash
from solders.signature import Signature


class TestSolanaConfig:
    """Covers trusted origins, named clusters, and hard feature policy."""

    def test_allows_http_only_for_loopback_local_development(self) -> None:
        config = SolanaCommerceConfig(
            public_base_url="http://127.0.0.1:8000",
            merchant_label="Local merchant",
            rpc=SolanaRpcConfig(
                genesis_hash=MAINNET_GENESIS_HASH,
                endpoints=("http://localhost:8899",),
            ),
            enable_token_2022=True,
        )

        assert config.public_base_url == "http://127.0.0.1:8000"
        assert config.rpc.endpoints == ("http://localhost:8899",)

    @pytest.mark.parametrize(
        "url",
        ["http://example.com", "http://192.168.1.10:8000", "https://user:secret@example.com"],
    )
    def test_rejects_insecure_or_credential_bearing_public_origins(self, url: str) -> None:
        with pytest.raises(ValidationError):
            SolanaCommerceConfig(public_base_url=url, merchant_label="Merchant")

    def test_cluster_name_must_match_exact_genesis_hash(self) -> None:
        with pytest.raises(ValidationError):
            SolanaCommerceConfig(
                cluster=SolanaCluster.DEVNET,
                public_base_url="https://pay.example.com",
                merchant_label="Merchant",
                rpc=SolanaRpcConfig(genesis_hash=MAINNET_GENESIS_HASH),
            )

        config = SolanaCommerceConfig(
            cluster=SolanaCluster.DEVNET,
            public_base_url="https://pay.example.com",
            merchant_label="Merchant",
            rpc=SolanaRpcConfig(
                genesis_hash=DEVNET_GENESIS_HASH,
                endpoints=("https://api.devnet.solana.com",),
            ),
        )
        assert config.cluster == SolanaCluster.DEVNET

    @pytest.mark.parametrize(
        "changes",
        (
            {"max_issuances_per_intent": 0},
            {"max_issuances_per_intent": 101},
            {"terminal_reconciliation_grace": timedelta(seconds=59)},
            {"terminal_reconciliation_grace": timedelta(hours=1, seconds=1)},
        ),
    )
    def test_reconciliation_resource_bounds_are_fail_closed(self, changes) -> None:
        with pytest.raises(ValidationError):
            SolanaCommerceConfig(
                public_base_url="https://pay.example.com",
                merchant_label="Merchant",
                **changes,
            )

    def test_solana_pay_icon_must_be_absolute_http_image_url(self) -> None:
        assert SolanaPayGetResponse(label="Merchant", icon="https://cdn.example.com/icon.png").icon
        with pytest.raises(ValidationError):
            SolanaPayGetResponse(label="Merchant", icon="javascript:alert(1)")

class TestHttpSolanaRpc:
    """Verifies free standard JSON-RPC error and cluster semantics."""

    @pytest.mark.asyncio
    async def test_checks_exact_genesis_hash(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["method"] == "getGenesisHash"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": DEVNET_GENESIS_HASH})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc = HttpSolanaRpc(
            SolanaRpcConfig(genesis_hash=MAINNET_GENESIS_HASH, max_attempts_per_endpoint=1),
            client=client,
        )

        with pytest.raises(SolanaRpcResponseError) as error:
            await rpc.check_cluster()

        assert error.value.code == SolanaErrorCode.WRONG_CLUSTER
        await client.aclose()

    @pytest.mark.asyncio
    async def test_rate_limit_is_retryable_without_paid_fallback(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            _ = request
            return httpx.Response(429, json={"error": "rate limited"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc = HttpSolanaRpc(
            SolanaRpcConfig(
                genesis_hash=MAINNET_GENESIS_HASH,
                max_attempts_per_endpoint=1,
                circuit_break_seconds=0,
            ),
            client=client,
        )

        with pytest.raises(SolanaRpcUnavailableError) as error:
            await rpc.check_cluster()

        assert error.value.code == SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE
        await client.aclose()

    @pytest.mark.asyncio
    async def test_wrong_cluster_endpoint_is_skipped_for_a_valid_fallback(self) -> None:
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host or "")
            payload = json.loads(request.content)
            genesis = DEVNET_GENESIS_HASH if request.url.host == "wrong.example" else MAINNET_GENESIS_HASH
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": genesis})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc = HttpSolanaRpc(
            SolanaRpcConfig(
                genesis_hash=MAINNET_GENESIS_HASH,
                endpoints=("https://wrong.example", "https://right.example"),
                max_attempts_per_endpoint=1,
            ),
            client=client,
        )

        assert await rpc.check_cluster() == MAINNET_GENESIS_HASH
        assert calls == ["wrong.example", "right.example", "right.example"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_latest_blockhash_preserves_rpc_context_slot(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            result = (
                MAINNET_GENESIS_HASH
                if payload["method"] == "getGenesisHash"
                else {
                    "context": {"slot": 987654},
                    "value": {
                        "blockhash": str(Hash.default()),
                        "lastValidBlockHeight": 1234,
                    },
                }
            )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc = HttpSolanaRpc(SolanaRpcConfig(genesis_hash=MAINNET_GENESIS_HASH), client=client)

        latest = await rpc.get_latest_blockhash(SolanaCommitment.FINALIZED)

        assert latest.context_slot == 987654
        assert latest.last_valid_block_height == 1234
        await client.aclose()

    @pytest.mark.asyncio
    async def test_get_transaction_rejects_oversized_wire_payload_before_parsing(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            result = (
                MAINNET_GENESIS_HASH
                if payload["method"] == "getGenesisHash"
                else {
                    "slot": 1,
                    "blockTime": None,
                    "transaction": [base64.b64encode(b"x" * 1233).decode("ascii"), "base64"],
                    "meta": {},
                }
            )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc = HttpSolanaRpc(SolanaRpcConfig(genesis_hash=MAINNET_GENESIS_HASH), client=client)

        with pytest.raises(SolanaRpcResponseError) as error:
            await rpc.get_transaction(str(Signature.new_unique()), SolanaCommitment.FINALIZED)

        assert error.value.code == SolanaErrorCode.RPC_INVALID_RESPONSE
        await client.aclose()
