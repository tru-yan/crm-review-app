from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    """One community as listed on the Bellhaven website."""
    slug: str
    url: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    phone: str
    administrator: str
    care_offerings: tuple[str, ...]
    in_directory: bool
