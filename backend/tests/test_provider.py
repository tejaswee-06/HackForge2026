import logging

import httpx
import pytest

from app.config import Settings
from app.services.ingestion.providers import EtherscanProvider, ProviderError, WalletQuery

from .conftest import SECRET, WALLET, settings

Q = WalletQuery(chain_id=1, address=WALLET, max_transfers=10)
ROW = {"hash": "0x" + "a" * 64, "blockNumber": "1", "timeStamp": "1790000000", "from": WALLET,
       "to": "0x" + "0" * 36 + "beef", "value": "5", "isError": "0"}


def prov(handler, **kw):
    sleeps = []
    p = EtherscanProvider(settings(etherscan_api_key=SECRET, **kw), transport=httpx.MockTransport(handler),
                          sleep=sleeps.append)
    p.sleeps = sleeps
    return p


def ok(rows):
    return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})


def test_successful_response():
    p = prov(lambda r: ok([ROW]))
    res = p.fetch_wallet_transfers(Q)
    assert res.native_rows == [ROW] and res.erc20_rows == [ROW] and not res.from_cache and p.request_count == 2


def test_no_transactions_is_empty_not_error():
    p = prov(lambda r: httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []}))
    assert p.fetch_wallet_transfers(Q).native_rows == []


@pytest.mark.parametrize("resp", [
    httpx.Response(200, content=b"<html>oops</html>"),
    httpx.Response(200, json=["not", "a", "dict"]),
    httpx.Response(200, json={"status": "1", "message": "OK"}),
    httpx.Response(200, json={"status": "1", "message": "OK", "result": "string"}),
    httpx.Response(200, json={"status": "1", "message": "OK", "result": [1, 2]}),
    httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "weird failure"}),
    httpx.Response(404),
])
def test_malformed_response_is_non_retryable_bad_response(resp):
    calls = []
    p = prov(lambda r: (calls.append(1), resp)[1])
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert e.value.code == "PROVIDER_BAD_RESPONSE" and not e.value.retryable and len(calls) == 1


def test_timeout_retries_then_exhausts():
    calls = []

    def h(r):
        calls.append(1)
        raise httpx.ReadTimeout("slow")
    p = prov(h, provider_max_retries=2, provider_backoff_s=0.5)
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert e.value.code == "PROVIDER_RETRY_EXHAUSTED" and e.value.retryable
    assert len(calls) == 3  # 1 + 2 retries: bounded
    assert p.sleeps == [0.5, 1.0]  # exponential backoff
    assert p.health()["last_error_code"] == "PROVIDER_RETRY_EXHAUSTED"


def test_rate_limit_http_429_retries_then_succeeds_honouring_retry_after():
    state = {"n": 0}

    def h(r):
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return ok([ROW])
    p = prov(h, provider_backoff_s=0.1)
    assert p.fetch_wallet_transfers(Q).native_rows == [ROW]
    assert p.sleeps[0] == 3.0 and p.sleeps[1] == 3.0


def test_rate_limit_in_body_is_retryable():
    state = {"n": 0}

    def h(r):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Max rate limit reached"})
        return ok([ROW])
    assert prov(h).fetch_wallet_transfers(Q).native_rows == [ROW]


def test_5xx_retry_exhaustion():
    p = prov(lambda r: httpx.Response(503), provider_max_retries=1)
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert e.value.code == "PROVIDER_RETRY_EXHAUSTED" and p.request_count == 2


def test_auth_error_not_retried():
    p = prov(lambda r: httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Invalid API Key"}))
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert e.value.code == "PROVIDER_AUTH" and not e.value.retryable and p.request_count == 1


def test_missing_configuration():
    p = EtherscanProvider(Settings(etherscan_api_key=None))
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert e.value.code == "PROVIDER_NOT_CONFIGURED" and p.health()["status"] == "not_configured"


def test_unsupported_chain():
    p = prov(lambda r: ok([]))
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(WalletQuery(chain_id=137, address=WALLET))
    assert e.value.code == "UNSUPPORTED_CHAIN" and p.request_count == 0


def test_cache_marks_second_result_cached_and_avoids_http():
    p = prov(lambda r: ok([ROW]))
    first = p.fetch_wallet_transfers(Q)
    n = p.request_count
    second = p.fetch_wallet_transfers(Q)
    assert (first.from_cache, second.from_cache) == (False, True) and p.request_count == n
    assert second.retrieved_at == first.retrieved_at  # original retrieval time preserved


def test_cache_disabled_when_ttl_zero():
    p = prov(lambda r: ok([ROW]), cache_ttl_s=0)
    p.fetch_wallet_transfers(Q)
    p.fetch_wallet_transfers(Q)
    assert p.request_count == 4


def test_failures_are_not_cached():
    state = {"fail": True}
    p = prov(lambda r: httpx.Response(500) if state["fail"] else ok([ROW]), provider_max_retries=0)
    with pytest.raises(ProviderError):
        p.fetch_wallet_transfers(Q)
    state["fail"] = False
    assert p.fetch_wallet_transfers(Q).native_rows == [ROW]


def test_api_key_sent_but_never_leaked(caplog):
    seen = []
    caplog.set_level(logging.DEBUG)

    def h(r):
        seen.append(dict(r.url.params))
        raise httpx.ConnectError(f"boom {r.url}")  # exception text contains the URL (and key)
    p = prov(h, provider_max_retries=1)
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert seen[0]["apikey"] == SECRET  # actually used server-side...
    blob = str(e.value) + e.value.message + repr(p.health()) + caplog.text + repr(p.s)
    assert SECRET not in blob  # ...but never in errors, logs, health or repr(settings)


def test_bad_response_message_redacts_key():
    p = prov(lambda r: httpx.Response(200, json={"status": "0", "message": f"NOTOK {SECRET}", "result": "x"}))
    with pytest.raises(ProviderError) as e:
        p.fetch_wallet_transfers(Q)
    assert SECRET not in e.value.message
