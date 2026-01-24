from trackfactory.store.tracks import slugify


def test_slugify():
    assert slugify("Nürburgring Nordschleife") == "nurburgring-nordschleife"
