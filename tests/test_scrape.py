import pytest

from bellhaven.scrape import ScrapeError, parse_detail, parse_listing, scrape

BASE = "https://site.test"


def listing(slugs, next_page=None, total=3):
    cards = "".join(f'<div class="card"><h3><a href="{s}">{s}</a></h3></div>' for s in slugs)
    nxt = f'<a href="{next_page}">Next &rarr;</a>' if next_page else ""
    return (f'<div class="wrap"><p>Page 1 of 2 · {total} communities listed</p>'
            f'<div class="cardgrid">{cards}</div>'
            f'<div class="pager"><a href="/communities?page=1">&larr; Prev</a>{nxt}</div></div>')


def detail(name, street="100 Main Street", city_line="Testville, OH 44000", offerings=("Assisted Living",)):
    badges = "".join(f'<span class="badge">{o}</span>' for o in offerings)
    return (f'<div class="wrap"><h1>{name}</h1><dl class="detail">'
            f'<dt>Address</dt><dd>{street}<br>{city_line}</dd>'
            f'<dt>Care Offerings</dt><dd>{badges}</dd>'
            f'<dt>Administrator</dt><dd>Pat Admin</dd>'
            f'<dt>Phone</dt><dd>(614) 555-0100</dd></dl></div>')


class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout):
        path = url.removeprefix(BASE)
        return FakeResponse(self.pages[path]) if path in self.pages else FakeResponse("", 404)


def test_parse_listing():
    slugs, nxt, count = parse_listing(listing(["/communities/a"], "/communities?page=2", total=34))
    assert slugs == ["/communities/a"]
    assert nxt == "/communities?page=2"
    assert count == 34


def test_parse_detail():
    loc = parse_detail(detail("Bellhaven Shores of Erie", "4930 W Lake Rd", "Erie, PA 16505",
                              ("Assisted Living", "Memory Support")), "/communities/erie", BASE, True)
    assert (loc.name, loc.street, loc.city, loc.state, loc.zip) == ("Bellhaven Shores of Erie", "4930 W Lake Rd", "Erie", "PA", "16505")
    assert loc.care_offerings == ("Assisted Living", "Memory Support")
    assert loc.administrator == "Pat Admin" and loc.phone == "(614) 555-0100"
    assert loc.url == "https://site.test/communities/erie"


def pages(total=3):
    return {
        "/communities": listing(["/communities/a", "/communities/b"], "/communities?page=2", total),
        "/communities?page=2": listing(["/communities/c"], None, total),
        "/": '<a href="/communities/d">New!</a> <a href="/communities/a">A</a> <a href="/communities">All</a>',
        "/about": "<p>About us</p>",
        "/communities/a": detail("A"), "/communities/b": detail("B"),
        "/communities/c": detail("C"), "/communities/d": detail("D"),
    }


def test_scrape_follows_pager_and_finds_homepage_only_communities():
    locs = scrape(FakeSession(pages()), base=BASE, delay=0)
    assert [l.name for l in locs] == ["A", "B", "C", "D"]
    assert [l.in_directory for l in locs] == [True, True, True, False]


def test_scrape_fails_when_directory_count_is_wrong():
    with pytest.raises(ScrapeError, match="says 5"):
        scrape(FakeSession(pages(total=5)), base=BASE, delay=0)
