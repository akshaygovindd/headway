import httpx

from headway.ingest.fetch import fetch_feed

URL = "https://example.invalid/feed.pb"


def client_for(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_success_sends_gzip_header() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"abc")

    sleeps: list[float] = []
    r = fetch_feed(URL, client_for(httpx.MockTransport(handler)), sleeps.append)
    assert (r.status, r.http_status, r.body, r.error) == ("ok", 200, b"abc", None)
    assert r.fetched_at.tzinfo is not None
    assert seen[0].headers["accept-encoding"] == "gzip"
    assert sleeps == []


def test_500_then_success() -> None:
    codes = iter([500, 200])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes), content=b"ok")

    sleeps: list[float] = []
    r = fetch_feed(URL, client_for(httpx.MockTransport(handler)), sleeps.append)
    assert r.status == "ok"
    assert sleeps == [1.0]


def test_timeout_exhaustion() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=req)

    sleeps: list[float] = []
    r = fetch_feed(URL, client_for(httpx.MockTransport(handler)), sleeps.append)
    assert r.status == "error"
    assert r.body is None and r.http_status is None
    assert r.error is not None and "ReadTimeout" in r.error
    assert calls == 3
    assert sleeps == [1.0, 3.0]


def test_persistent_500_keeps_status() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    r = fetch_feed(URL, client_for(httpx.MockTransport(handler)), lambda _: None)
    assert (r.status, r.http_status, r.error) == ("error", 503, "HTTP 503")


def test_404_not_retried() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    sleeps: list[float] = []
    r = fetch_feed(URL, client_for(httpx.MockTransport(handler)), sleeps.append)
    assert r.status == "error" and calls == 1 and sleeps == []
