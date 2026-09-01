"""
finn.no real estate scraper
----------------------------
Scrapes listings from a finn.no realestate search URL and, for each listing,
visits the ad's detail page to pull area breakdown fields (TBA / BRA-i / BRA-e).

Fields collected:
  - address
  - total_price      (Totalpris)
  - asking_price      (Prisantydning, shown as the big price on the card)
  - m2                (area shown on the search card)
  - rooms             (Rom - from the detail page "Nøkkelinfo" box)
  - bedrooms          (Soverom - from the search card)
  - apartment_type    (Boligtype: Leilighet / Enebolig / Rekkehus / Tomannsbolig ...)
  - TBA               (Bruksareal - total usable area, m²)
  - BRA_i             (Internt bruksareal - internal usable area, m²)
  - BRA_e             (Eksternt bruksareal - external usable area, m²)
  - byggeår           (Byggeår - year built, from the detail page "Nøkkelinfo" box)

Requirements:
    pip install requests beautifulsoup4

Usage:
    python finn_scraper.py
"""

import re
import csv
import time
import random
import requests
from bs4 import BeautifulSoup

BASE_SEARCH_URL = "https://www.finn.no/realestate/homes/search.html"
SEARCH_PARAMS = {
    "q": "furuset",
    "location": "0.20061",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
}

REQUEST_DELAY_RANGE = (1.5, 3.0)  # be polite - random delay between requests


def get_soup(url, params=None):
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_number(text):
    """Extract an integer from strings like '3 300 000 kr' or '73 m²'."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def get_ad_links(soup):
    """Find all unique ad detail-page links on a search results page."""
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/realestate/" in href and "finnkode=" in href:
            if href.startswith("/"):
                href = "https://www.finn.no" + href
            links.add(href)

    # Normalize to canonical ad URL with finnkode only
    normalized = set()
    for href in links:
        m = re.search(r"finnkode=(\d+)", href)
        if m:
            code = m.group(1)
            base = href.split("?")[0]
            normalized.add(f"{base}?finnkode={code}")
    return sorted(normalized)


def parse_search_card(card_text):
    """
    Given the visible text block for one listing card (as it reads top to bottom),
    pull out address, m2, asking price, total price, bedrooms, ownership + type.

    Finn card text typically looks like:
        Ulsholtveien 4C, Oslo
        119 m²6 100 000 kr
        Totalpris: 6 272 257 kr ∙ Fellesutg.: 6 902 kr ∙ Andel ∙ Rekkehus ∙ 3 soverom
    """
    data = {}

    # Address: a line "Streetname 1A, Oslo" (ends in ", Oslo" or another place name)
    addr_match = re.search(r"([A-ZÆØÅ][^\n,]{2,60}\d[^\n,]*,\s*[A-ZÆØÅ][a-zæøå]+)", card_text)
    data["address"] = addr_match.group(1).strip() if addr_match else None

    # m2 + asking price, glued together e.g. "119 m²6 100 000 kr"
    m2_price = re.search(r"(\d[\d\s]*)\s*m²\s*([\d\s]+)\s*kr", card_text)
    if m2_price:
        data["m2"] = parse_number(m2_price.group(1))
        data["asking_price"] = parse_number(m2_price.group(2))
    else:
        data["m2"] = None
        data["asking_price"] = None

    # Total price
    tot_match = re.search(r"Totalpris:\s*([\d\s]+)\s*kr", card_text)
    data["total_price"] = parse_number(tot_match.group(1)) if tot_match else None

    # Bedrooms (soverom)
    bed_match = re.search(r"(\d+)\s*soverom", card_text)
    data["bedrooms"] = int(bed_match.group(1)) if bed_match else None

    # Apartment type - one of the known Norwegian dwelling types
    type_match = re.search(
        r"\b(Leilighet|Enebolig|Rekkehus|Tomannsbolig|Andelsleilighet|Gårdsbruk/Småbruk)\b",
        card_text,
    )
    data["apartment_type"] = type_match.group(1) if type_match else None

    return data


def scrape_search_pages(max_pages=20):
    """Scrape all search-result pages, returning list of (ad_url, card_data)."""
    results = []
    page = 1
    while page <= max_pages:
        params = dict(SEARCH_PARAMS)
        if page > 1:
            params["page"] = page

        print(f"Fetching search results page {page} ...")
        soup = get_soup(BASE_SEARCH_URL, params=params)

        ad_links = get_ad_links(soup)
        if not ad_links:
            print("No more listings found, stopping pagination.")
            break

        # Each ad's card content sits inside an <article> (or similar container).
        # We match each link back to its enclosing block's text.
        articles = soup.find_all("article") or soup.find_all("div", recursive=True)

        seen_on_page = set()
        for link in ad_links:
            code = re.search(r"finnkode=(\d+)", link).group(1)
            if code in seen_on_page:
                continue
            seen_on_page.add(code)

            # find an ancestor block that contains this finnkode, to grab its text
            anchor = soup.find("a", href=re.compile(code))
            block = anchor
            card_text = ""
            # walk up a few parent levels to capture the full card block
            for _ in range(6):
                if block is None:
                    break
                block = block.parent
                text = block.get_text(separator="\n", strip=True)
                if "kr" in text and "m²" in text:
                    card_text = text
                    break

            card_data = parse_search_card(card_text)
            results.append((link, card_data))

        page += 1
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    return results


def scrape_ad_detail(ad_url):
    """Visit a single ad page and extract Rom, TBA (Bruksareal), BRA-i, BRA-e."""
    soup = get_soup(ad_url)
    text = soup.get_text(separator="\n", strip=True)

    detail = {"rooms": None, "TBA": None, "BRA_i": None, "BRA_e": None, "byggeår": None}

    rom_match = re.search(r"Rom\n(\d+)", text)
    if rom_match:
        detail["rooms"] = int(rom_match.group(1))

    # "Byggeår\n1973" - only present on the detail page's Nøkkelinfo box
    year_match = re.search(r"Byggeår\n(\d{4})", text)
    if year_match:
        detail["byggeår"] = int(year_match.group(1))

    # "Internt bruksareal\n73 m² (BRA-i)"
    bra_i_match = re.search(r"Internt bruksareal\n(\d+)\s*m", text)
    if bra_i_match:
        detail["BRA_i"] = int(bra_i_match.group(1))

    # "Eksternt bruksareal\n9 m² (BRA-e)"
    bra_e_match = re.search(r"Eksternt bruksareal\n(\d+)\s*m", text)
    if bra_e_match:
        detail["BRA_e"] = int(bra_e_match.group(1))

    # "Bruksareal\n91 m²"  (total usable area == TBA)
    tba_match = re.search(r"(?<!Internt )(?<!Eksternt )Bruksareal\n(\d+)\s*m", text)
    if tba_match:
        detail["TBA"] = int(tba_match.group(1))

    return detail


def main():
    search_results = scrape_search_pages()
    print(f"Found {len(search_results)} listings. Fetching details for each...")

    rows = []
    for i, (ad_url, card_data) in enumerate(search_results, start=1):
        print(f"[{i}/{len(search_results)}] {ad_url}")
        try:
            detail_data = scrape_ad_detail(ad_url)
        except Exception as e:
            print(f"  -> failed to fetch detail page: {e}")
            detail_data = {"rooms": None, "TBA": None, "BRA_i": None, "BRA_e": None, "byggeår": None}

        row = {
            "url": ad_url,
            "address": card_data.get("address"),
            "total_price": card_data.get("total_price"),
            "asking_price": card_data.get("asking_price"),
            "m2": card_data.get("m2"),
            "rooms": detail_data.get("rooms"),
            "bedrooms": card_data.get("bedrooms"),
            "apartment_type": card_data.get("apartment_type"),
            "TBA": detail_data.get("TBA"),
            "BRA_i": detail_data.get("BRA_i"),
            "BRA_e": detail_data.get("BRA_e"),
            "byggeår": detail_data.get("byggeår"),
        }
        rows.append(row)
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    out_file = "finn_raw.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Saved {len(rows)} listings to {out_file}")


if __name__ == "__main__":
    main()