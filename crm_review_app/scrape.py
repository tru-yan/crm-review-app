"""Scrape every Bellhaven community: all directory pages plus community links found elsewhere on the site."""
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import SITE_BASE
from .models import Location

EXTRA_LINK_PAGES = ("/", "/about")
_CITY_LINE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


class ScrapeError(RuntimeError):
    pass


def parse_listing(html: str) -> tuple[list[str], str | None, int | None]:
    """Return (community slugs, next page path, the 'N communities listed' count)."""
    soup = BeautifulSoup(html, "html.parser")
    slugs = [a["href"] for a in soup.select(".card h3 a[href^='/communities/']")]
    next_path = next((a["href"] for a in soup.select(".pager a") if "Next" in a.get_text()), None)
    m = re.search(r"(\d+)\s+communities listed", soup.get_text(" "))
    return slugs, next_path, int(m.group(1)) if m else None


def parse_community_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [a["href"] for a in soup.select("a[href^='/communities/']") if "?" not in a["href"]]


def parse_detail(html: str, slug: str, base: str, in_directory: bool) -> Location:
    soup = BeautifulSoup(html, "html.parser")
    fields = {dt.get_text(strip=True).lower(): dt.find_next_sibling("dd") for dt in soup.select("dl.detail dt")}
    lines = list(fields["address"].stripped_strings)
    m = _CITY_LINE.match(lines[-1])
    if not m:
        raise ScrapeError(f"{slug}: cannot parse city/state/zip from {lines[-1]!r}")
    offerings = fields.get("care offerings")
    return Location(
        slug=slug,
        url=urljoin(base, slug),
        name=soup.select_one(".wrap h1").get_text(strip=True),
        street=", ".join(lines[:-1]),
        city=m["city"],
        state=m["state"],
        zip=m["zip"],
        phone=fields["phone"].get_text(strip=True) if "phone" in fields else "",
        administrator=fields["administrator"].get_text(strip=True) if "administrator" in fields else "",
        care_offerings=tuple(b.get_text(strip=True) for b in offerings.select(".badge")) if offerings else (),
        in_directory=in_directory,
    )


def scrape(session=None, base: str = SITE_BASE, delay: float = 0.3) -> list[Location]:
    s = session or requests.Session()

    def get(path: str) -> str:
        r = s.get(urljoin(base, path), timeout=30)
        r.raise_for_status()
        time.sleep(delay)
        return r.text

    directory, path, claimed, seen = [], "/communities", None, set()
    while path and path not in seen:
        seen.add(path)
        slugs, path, count = parse_listing(get(path))
        claimed = claimed if claimed is not None else count
        directory += [x for x in slugs if x not in directory]
    if claimed is None:
        raise ScrapeError("directory page no longer says 'N communities listed'; the site layout may have changed")
    if not directory or len(directory) != claimed:
        raise ScrapeError(f"directory says {claimed} communities but {len(directory)} were found")

    extra = []
    for page in EXTRA_LINK_PAGES:
        extra += [x for x in parse_community_links(get(page)) if x not in directory and x not in extra]

    locations = []
    for slug in directory + extra:
        try:
            locations.append(parse_detail(get(slug), slug, base, slug in directory))
        except (KeyError, AttributeError, IndexError) as e:
            raise ScrapeError(f"{slug}: unexpected page layout ({e!r})") from e
    return locations
