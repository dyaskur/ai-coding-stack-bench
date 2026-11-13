def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_cache_reachable(client):
    assert client.get("/cache").json() == {"cache": "PONG"}
