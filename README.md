# Property-guru-scraping
my family is moving house and is looking for help to scrape for listing.
we want you to scrape property guru for condos.
the information to scrape for should be 1. tenure 2. square foot area 3. asking price 4. number of bedrooms 5. number of bathrooms 6. location and 7. proximity to MRT

## How to run the scraper

The scraper (`propertyguru_scraper.py`) drives a real Chromium browser with
[Playwright](https://playwright.dev/python/), because PropertyGuru blocks
plain HTTP scrapers with Cloudflare bot protection.

### Setup (one time)

```bash
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Scrape

```bash
# scrape the first 5 pages of condo listings into listings.csv
python propertyguru_scraper.py --max-pages 5 --output listings.csv
```

Options:

| flag | default | meaning |
|------|---------|---------|
| `--max-pages` | 3 | how many search result pages to scrape (~20 listings per page) |
| `--output` | `listings.csv` | where to save the CSV |
| `--headful` | off | show the browser window (use this if a "verify you are human" check appears — solve it once and the scrape continues) |
| `--delay` | 8 | base seconds to wait between pages, to scrape politely |

### Output columns

`title, tenure, area_sqft, asking_price_sgd, bedrooms, bathrooms, location,
mrt_proximity, price_psf, url` — covering all seven fields requested above,
plus price-per-sqft and the listing URL as bonuses. Open the CSV in Excel or
Google Sheets to sort and filter.

### Notes

- Run it from a normal home internet connection. Cloud servers and VPNs are
  usually blocked by PropertyGuru's bot protection.
- Scrape gently and only what you need — this is for personal house-hunting.
- If the site redesigns its listing cards the selectors/regexes at the top of
  `propertyguru_scraper.py` may need small updates.
