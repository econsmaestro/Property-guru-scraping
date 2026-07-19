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
import json
import random
import re
import sys
import time
from dataclasses import dataclass, asdict, fields
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

try:
    from playwright._impl._errors import TargetClosedError
except ImportError:  # not exposed in some playwright versions
    TargetClosedError = PlaywrightError

BASE_URL = "https://www.propertyguru.com.sg"


def build_search_url(page_no: int, districts: list[str]) -> str:
    # property_type=N is PropertyGuru's code for Condo/Apartment.
    url = f"{BASE_URL}/property-for-sale/{page_no}?property_type=N&listing_type=sale"
    for district in districts:
        url += f"&district_code[]={district}"
    return url


def normalize_districts(spec: str) -> list[str]:
    """Turn 'D9, 15, d21' into ['D09', 'D15', 'D21'] (validated D01-D28)."""
    districts = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"[Dd]?(\d{1,2})", part)
        if not m or not 1 <= int(m.group(1)) <= 28:
            raise SystemExit(
                f"Invalid district {part!r}: use D01-D28, e.g. --districts D09,D15"
            )
        districts.append(f"D{int(m.group(1)):02d}")
    return districts

# Browser profile kept between runs so a solved bot check is remembered.
PROFILE_DIR = Path(__file__).resolve().parent / ".pw-profile"

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
BEDS_RE = re.compile(r"(\d+)\s*(?:Beds?|BR)\b", re.IGNORECASE)
BATHS_RE = re.compile(r"(\d+)\s*(?:Baths?|BA)\b", re.IGNORECASE)
STUDIO_RE = re.compile(r"\bStudio\b", re.IGNORECASE)
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

    if not listing.bedrooms and STUDIO_RE.search(text):
        listing.bedrooms = "Studio"

    mrt = MRT_RE.search(text)
    if mrt:
        mins, metres, station = mrt.groups()
        listing.mrt_proximity = f"{mins} min ({metres} m) to {station.strip()}"


# ---------------------------------------------------------------- scraping

CARD_SELECTORS = [
    "div.listing-card-v2",       # current site layout (2025+)
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


def wait_for_cards(page, timeout_s: float = 20.0) -> list:
    """Poll for listing cards — pages keep rendering after domcontentloaded,
    and after a solved bot check the real page arrives via a redirect."""
    deadline = time.time() + timeout_s
    while True:
        cards = extract_cards(page)
        if cards or time.time() > deadline:
            return cards
        page.wait_for_timeout(1_000)


def _da_text(card, suffix: str) -> str:
    """Text of the element PropertyGuru tags with da-id="listing-card-v2-…"."""
    el = card.query_selector(f'[da-id="listing-card-v2-{suffix}"]')
    return (el.inner_text() or "").strip() if el else ""


def scrape_card(card) -> Listing | None:
    listing = Listing()

    text = card.inner_text()
    if not text or "S$" not in text:
        return None
    parse_card_text(text, listing)  # regex baseline for any layout

    # The current site tags every field with a da-id marker — when those
    # are present they are authoritative and override the regex guesses.
    if title := _da_text(card, "title"):
        listing.title = title
    if price := _first(PRICE_RE, _da_text(card, "price")):
        listing.asking_price_sgd = price
    if area := _first(AREA_RE, _da_text(card, "area")):
        listing.area_sqft = area
    if psf := _first(PSF_RE, _da_text(card, "psf")):
        listing.price_psf = psf
    if tenure := _da_text(card, "tenure"):
        listing.tenure = tenure
    if beds := _da_text(card, "bedrooms"):
        listing.bedrooms = beds
    if baths := _da_text(card, "bathrooms"):
        listing.bathrooms = baths
    if mrt := _da_text(card, "mrt"):
        listing.mrt_proximity = mrt

    if not listing.title:
        listing.title = _query_text(card, TITLE_SELECTORS)
    address = _query_text(card, ADDRESS_SELECTORS)
    listing.location = ", ".join(p for p in [listing.title, address] if p) or address

    # Older layouts show bed/bath counts as bare numbers beside icons —
    # recover them from dedicated elements if still missing.
    if not listing.bedrooms:
        listing.bedrooms = re.sub(r"\D", "", _query_text(card, BED_SELECTORS))
    if not listing.bathrooms:
        listing.bathrooms = re.sub(r"\D", "", _query_text(card, BATH_SELECTORS))

    link = card.query_selector("a[href*='/listing/']") or card.query_selector("a[href]")
    if link:
        href = link.get_attribute("href") or ""
        listing.url = href if href.startswith("http") else BASE_URL + href

    return listing


# --------------------------------------------- embedded-JSON fallback data
#
# PropertyGuru renders its cards from JSON embedded in <script> tags. Cards
# often show bed/bath counts only as bare numbers beside icons (and omit
# MRT info for some layouts), so we also mine that JSON and use it to fill
# any fields the visible text didn't yield.

BED_KEYS = {"bedrooms", "beds", "bedroom", "bedroomscount"}
BATH_KEYS = {"bathrooms", "baths", "bathroom", "bathroomscount"}
ID_KEYS = {"id", "listingid"}


def _collect_listing_dicts(node, out) -> None:
    if isinstance(node, dict):
        lowered = {k.lower() for k in node}
        if lowered & BED_KEYS and lowered & BATH_KEYS:
            out.append(node)
        for value in node.values():
            _collect_listing_dicts(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_listing_dicts(value, out)


def _flatten_strings(node, out, depth=0) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for value in node.values():
            _flatten_strings(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _flatten_strings(value, out, depth + 1)
    elif isinstance(node, str):
        out.append(node)


def page_json_details(page) -> dict:
    """Map listing id -> {bedrooms, bathrooms, mrt} mined from page JSON."""
    details: dict[str, dict] = {}
    for script in page.query_selector_all("script"):
        text = script.text_content() or ""
        if len(text) < 200 or "bathroom" not in text.lower():
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        records: list[dict] = []
        _collect_listing_dicts(data, records)
        for record in records:
            entry: dict[str, str] = {}
            lid = ""
            for key, value in record.items():
                kl = key.lower()
                if kl in ID_KEYS and isinstance(value, (str, int)):
                    lid = str(value)
                elif kl in BED_KEYS:
                    if value == 0 or value == "0":
                        entry["bedrooms"] = "Studio"
                    elif value not in (None, ""):
                        entry["bedrooms"] = str(value)
                elif kl in BATH_KEYS and value not in (None, "", 0):
                    entry["bathrooms"] = str(value)
                elif "mrt" in kl and value:
                    strings: list[str] = []
                    _flatten_strings(value, strings)
                    joined = " | ".join(strings)
                    m = MRT_RE.search(joined)
                    if m:
                        mins, metres, station = m.groups()
                        entry["mrt"] = f"{mins} min ({metres} m) to {station.strip()}"
                    else:
                        for s in strings:
                            if "mrt" in s.lower():
                                entry["mrt"] = s.strip()
                                break
            if lid and entry:
                details.setdefault(lid, {}).update(entry)
    return details


def fill_from_json(listing: Listing, card, json_details: dict) -> None:
    lid = card.get_attribute("data-listing-id") or ""
    if not lid and listing.url:
        m = re.search(r"(\d{6,})", listing.url)
        lid = m.group(1) if m else ""
    info = json_details.get(lid)
    if not info:
        return
    if not listing.bedrooms:
        listing.bedrooms = info.get("bedrooms", "")
    if not listing.bathrooms:
        listing.bathrooms = info.get("bathrooms", "")
    if not listing.mrt_proximity:
        listing.mrt_proximity = info.get("mrt", "")


def looks_blocked(page) -> bool:
    title = (page.title() or "").lower()
    body = ""
    try:
        body = page.inner_text("body")[:2000].lower()
    except Exception:
        pass
    needles = ("just a moment", "verify you are human", "access denied", "captcha")
    return any(n in title or n in body for n in needles)


def open_browser(p, headful: bool):
    """Open a browser that looks as much like a normal one as possible.

    Prefers the user's installed Google Chrome over Playwright's bundled
    Chromium (real Chrome passes Cloudflare checks far more often), hides
    the navigator.webdriver automation flag, and keeps a persistent
    profile so a solved bot check is remembered for future runs.
    """
    common = dict(
        headless=not headful,
        chromium_sandbox=True,  # Playwright disables it by default, which
        # makes Chrome show a "--no-sandbox" warning banner
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1366, "height": 900},
        locale="en-SG",
    )
    if not headful:
        # headless Chromium advertises "HeadlessChrome" — mask it
        common["user_agent"] = USER_AGENT
    try:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", **common
        )
        print("Using installed Google Chrome")
    except PlaywrightError:
        context = p.chromium.launch_persistent_context(str(PROFILE_DIR), **common)
        print("Google Chrome not found — using Playwright's Chromium")
    return context


def wait_out_bot_check(page, headful: bool) -> bool:
    """Return True once the bot check is passed, False if still blocked."""
    if not looks_blocked(page):
        return True
    if headful:
        print("  ! bot check detected — click the checkbox in the browser window.")
        print("    DO NOT close the window; the scrape continues automatically.")
        print("    Waiting up to 10 minutes...")
        for _ in range(120):
            page.wait_for_timeout(5_000)
            if not looks_blocked(page):
                print("  check passed, continuing")
                return True
    return not looks_blocked(page)


def scrape(max_pages: int, output: str, headful: bool, delay: float,
           districts: list[str] | None = None, debug: bool = False) -> int:
    listings: list[Listing] = []
    districts = districts or []
    if districts:
        print(f"Filtering to districts: {', '.join(districts)}")

    with sync_playwright() as p:
        context = open_browser(p, headful)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            for page_no in range(1, max_pages + 1):
                url = build_search_url(page_no, districts)
                print(f"[page {page_no}/{max_pages}] {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(3_000)  # let the listing cards render
                except PlaywrightTimeoutError:
                    print("  ! page load timed out, skipping")
                    continue

                if not wait_out_bot_check(page, headful):
                    print("  ! blocked by bot protection. Re-run with --headful")
                    print("    from a residential connection and solve the check once.")
                    break

                if debug and page_no == 1:
                    debug_file = "debug_page1.html"
                    with open(debug_file, "w", encoding="utf-8") as fh:
                        fh.write(page.content())
                    print(f"  (debug: saved page HTML to {debug_file})")

                cards = wait_for_cards(page)
                if not cards:
                    # retry once with a fresh load — right after a solved bot
                    # check the first response is often not the real page yet
                    print("  ... no cards yet, reloading page")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    except PlaywrightTimeoutError:
                        pass
                    if not wait_out_bot_check(page, headful):
                        print("  ! blocked by bot protection on reload.")
                        break
                    cards = wait_for_cards(page)
                if not cards:
                    debug_file = f"debug_page{page_no}.html"
                    with open(debug_file, "w", encoding="utf-8") as fh:
                        fh.write(page.content())
                    print("  ! no listing cards found — page layout may have changed,")
                    print("    or this was the last page of results.")
                    print(f"    Saved what the browser saw to {debug_file} —")
                    print("    send that file to Claude to get the selectors fixed.")
                    break

                json_details = page_json_details(page)

                found = 0
                for card in cards:
                    try:
                        listing = scrape_card(card)
                        if listing:
                            fill_from_json(listing, card, json_details)
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
        except TargetClosedError:
            print("\n! The browser window was closed — stopping early.")
            print("  (Leave the window open next time; it closes itself when done.)")
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass

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
    # Windows consoles/pipes may default to a legacy encoding (cp1252)
    # that can't represent all characters — never let printing crash us.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Scrape PropertyGuru condo listings")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="number of search result pages to scrape (default: 3)")
    parser.add_argument("--output", default="listings.csv",
                        help="output CSV file (default: listings.csv)")
    parser.add_argument("--headful", action="store_true",
                        help="show the browser window (needed to solve bot checks)")
    parser.add_argument("--delay", type=float, default=8.0,
                        help="base delay in seconds between pages (default: 8)")
    parser.add_argument("--districts", default="",
                        help="only these postal districts, comma-separated "
                             "(e.g. D09,D15,D19)")
    parser.add_argument("--debug", action="store_true",
                        help="save the first results page as debug_page1.html "
                             "to help diagnose missing fields")
    args = parser.parse_args()
    return scrape(args.max_pages, args.output, args.headful, args.delay,
                  normalize_districts(args.districts), args.debug)


if __name__ == "__main__":
    sys.exit(main())
