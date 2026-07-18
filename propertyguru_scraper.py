#!/usr/bin/env python3
"""Scrape PropertyGuru Singapore condo listings.

Collects, for each listing on the condo search results pages:
  1. tenure            (Freehold / 99-year Leasehold / ...)
  2. square foot area  (built-up size in sqft)
  3. asking price      (S$)
  4. number of bedrooms
  5. number of bathrooms
  6. location          (project name + address)
  7. proximity to MRT  (walk time, distance, and station name)

Results are written to a CSV file (default: listings.csv).

PropertyGuru is protected by Cloudflare, so this scraper drives a real
Chromium browser via Playwright instead of using plain HTTP requests.
Run it from a normal home/residential connection for best results; if a
"verify you are human" page appears, run with --headful and solve the
check once, then the scrape continues.

Usage:
    pip install -r requirements.txt
    playwright install chromium
    python propertyguru_scraper.py --max-pages 5 --output listings.csv

Please scrape gently (the default delay between pages is deliberately
slow) and use the data for personal house-hunting only.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import time
from dataclasses import dataclass, asdict, fields

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.propertyguru.com.sg"
# property_type=N is PropertyGuru's code for Condo/Apartment.
SEARCH_PATH = "/property-for-sale/{page}?property_type=N&listing_type=sale"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------- data model


@dataclass
class Listing:
    title: str = ""
    tenure: str = ""
    area_sqft: str = ""
    asking_price_sgd: str = ""
    bedrooms: str = ""
    bathrooms: str = ""
    location: str = ""
    mrt_proximity: str = ""
    price_psf: str = ""
    url: str = ""


# ------------------------------------------------------------- text parsing

PRICE_RE = re.compile(r"S\$\s*([\d,]+)")
AREA_RE = re.compile(r"([\d,]+)\s*sq\s*ft", re.IGNORECASE)
PSF_RE = re.compile(r"S\$\s*([\d,]+(?:\.\d+)?)\s*psf", re.IGNORECASE)
TENURE_RE = re.compile(r"(Freehold|\d{2,4}[- ]?(?:yr|year)s?\s*Leasehold|Leasehold)", re.IGNORECASE)
BEDS_RE = re.compile(r"(\d+)\s*Beds?\b", re.IGNORECASE)
BATHS_RE = re.compile(r"(\d+)\s*Baths?\b", re.IGNORECASE)
MRT_RE = re.compile(
    r"(\d+)\s*mins?\s*\(([\d,]+)\s*m\)\s*(?:from|to)\s*([^\n|]+)", re.IGNORECASE
)


def _first(regex: re.Pattern, text: str) -> str:
    m = regex.search(text)
    return m.group(1).strip() if m else ""


def parse_card_text(text: str, listing: Listing) -> None:
    """Fill listing fields from the visible text of one result card."""
    listing.asking_price_sgd = _first(PRICE_RE, text)
    listing.area_sqft = _first(AREA_RE, text)
    listing.price_psf = _first(PSF_RE, text)
    listing.tenure = _first(TENURE_RE, text).title()
    listing.bedrooms = _first(BEDS_RE, text)
    listing.bathrooms = _first(BATHS_RE, text)

    mrt = MRT_RE.search(text)
    if mrt:
        mins, metres, station = mrt.groups()
        listing.mrt_proximity = f"{mins} min ({metres} m) to {station.strip()}"


# ---------------------------------------------------------------- scraping

CARD_SELECTORS = [
    "div[data-listing-id]",
    "div.listing-card",
    "div[class*='listing-card']",
]

TITLE_SELECTORS = ["h3", "a.nav-link", "[class*='listing-title']", "h2"]
ADDRESS_SELECTORS = [
    "[class*='listing-address']",
    "[class*='listing-location']",
    "[itemprop='streetAddress']",
    "p[class*='address']",
]
BED_SELECTORS = ["span.bed", "[class*='bed'] span", "li[class*='bed']"]
BATH_SELECTORS = ["span.bath", "[class*='bath'] span", "li[class*='bath']"]


def _query_text(card, selectors) -> str:
    for sel in selectors:
        try:
            el = card.query_selector(sel)
        except Exception:
            continue
        if el:
            text = (el.inner_text() or "").strip()
            if text:
                return text
    return ""


def extract_cards(page) -> list:
    for sel in CARD_SELECTORS:
        cards = page.query_selector_all(sel)
        if cards:
            return cards
    return []


def scrape_card(card) -> Listing | None:
    listing = Listing()

    text = card.inner_text()
    if not text or "S$" not in text:
        return None
    parse_card_text(text, listing)

    listing.title = _query_text(card, TITLE_SELECTORS)
    address = _query_text(card, ADDRESS_SELECTORS)
    listing.location = ", ".join(p for p in [listing.title, address] if p) or address

    # Cards often show bed/bath counts as bare numbers beside icons, which
    # the regexes above can't label — recover them from dedicated elements.
    if not listing.bedrooms:
        listing.bedrooms = re.sub(r"\D", "", _query_text(card, BED_SELECTORS))
    if not listing.bathrooms:
        listing.bathrooms = re.sub(r"\D", "", _query_text(card, BATH_SELECTORS))

    link = card.query_selector("a[href*='/listing/']") or card.query_selector("a[href]")
    if link:
        href = link.get_attribute("href") or ""
        listing.url = href if href.startswith("http") else BASE_URL + href

    return listing


def looks_blocked(page) -> bool:
    title = (page.title() or "").lower()
    body = ""
    try:
        body = page.inner_text("body")[:2000].lower()
    except Exception:
        pass
    needles = ("just a moment", "verify you are human", "access denied", "captcha")
    return any(n in title or n in body for n in needles)


def scrape(max_pages: int, output: str, headful: bool, delay: float) -> int:
    listings: list[Listing] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-SG",
        )
        page = context.new_page()

        for page_no in range(1, max_pages + 1):
            url = BASE_URL + SEARCH_PATH.format(page=page_no)
            print(f"[page {page_no}/{max_pages}] {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3_000)  # let the listing cards render
            except PlaywrightTimeoutError:
                print("  ! page load timed out, skipping")
                continue

            if looks_blocked(page):
                if headful:
                    print("  ! bot check detected — solve it in the browser window;")
                    print("    waiting up to 2 minutes...")
                    for _ in range(24):
                        page.wait_for_timeout(5_000)
                        if not looks_blocked(page):
                            break
                if looks_blocked(page):
                    print("  ! blocked by bot protection. Re-run with --headful")
                    print("    from a residential connection and solve the check once.")
                    break

            cards = extract_cards(page)
            if not cards:
                print("  ! no listing cards found — page layout may have changed,")
                print("    or this was the last page of results.")
                break

            found = 0
            for card in cards:
                try:
                    listing = scrape_card(card)
                except Exception as exc:  # one bad card shouldn't kill the run
                    print(f"  ! failed to parse a card: {exc}")
                    continue
                if listing and listing.asking_price_sgd:
                    listings.append(listing)
                    found += 1
            print(f"  -> {found} listings")

            if page_no < max_pages:
                pause = delay + random.uniform(0, delay / 2)
                time.sleep(pause)

        browser.close()

    if not listings:
        print("No listings scraped.")
        return 1

    fieldnames = [f.name for f in fields(Listing)]
    with open(output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(l) for l in listings)

    print(f"\nSaved {len(listings)} listings to {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape PropertyGuru condo listings")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="number of search result pages to scrape (default: 3)")
    parser.add_argument("--output", default="listings.csv",
                        help="output CSV file (default: listings.csv)")
    parser.add_argument("--headful", action="store_true",
                        help="show the browser window (needed to solve bot checks)")
    parser.add_argument("--delay", type=float, default=8.0,
                        help="base delay in seconds between pages (default: 8)")
    args = parser.parse_args()
    return scrape(args.max_pages, args.output, args.headful, args.delay)


if __name__ == "__main__":
    sys.exit(main())
