import httpx

from trackfactory.resolver.commons import search_commons


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class DummyClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, _url, params=None):
        self.calls.append(params)
        if params.get("list") == "search":
            return DummyResponse(
                {
                    "query": {
                        "search": [
                            {"title": "File:Example Circuit.svg"},
                            {"title": "File:Other.png"},
                        ]
                    }
                }
            )
        return DummyResponse(
            {
                "query": {
                    "pages": {
                        "1": {
                            "title": "File:Example Circuit.svg",
                            "imageinfo": [{"url": "https://example.com/track.svg", "mime": "image/svg+xml"}],
                        },
                        "2": {
                            "title": "File:Other.png",
                            "imageinfo": [{"url": "https://example.com/other.png", "mime": "image/png"}],
                        },
                    }
                }
            }
        )


def test_commons_search_parsing(monkeypatch):
    monkeypatch.setattr(httpx, "Client", DummyClient)
    results = search_commons("Example")
    assert len(results) == 1
    assert results[0].url.endswith(".svg")
