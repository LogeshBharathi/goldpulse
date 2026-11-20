# GoldPulse India – Data source & licensing assessment

Status: researched 2026-11-19. **Not legal advice.** Verify every point with the vendor/exchange before production or any commercial use.

## Findings

| Need | Initial reference in the brief | Assessment | What this project does |
|---|---|---|---|
| COMEX gold futures (GC) | cnbc.com/quotes/@GC.1 | CNBC's quote pages are a consumer website, not an API. Scraping them is fragile and is likely to breach site terms. COMEX data originates from CME Group, which requires a license for real-time **and delayed** data, and only T+1 data is generally license-free. CME's own website data is labelled "reference only". | **Not scraped.** A configurable REST provider (`RestQuoteProvider`) points at a licensed distributor (CME lists 300+) or a vendor API you hold a contract for. |
| MCX gold futures | mcxindia.com get-quote page | The page is an interactive web page, not a supported API. MCX data redistribution is handled via MCX-authorised vendors. | **Not scraped.** Same configurable provider pointed at a licensed vendor. End-of-day closes can also be loaded from a CSV you export from a licensed source. |
| XAU/USD spot | – | Available from several vendors (e.g. Twelve Data preset included). Free tiers typically forbid redistribution/commercial use. | `PRESET_TWELVEDATA` mapping included, API key via env. **Check the plan's commercial-use terms.** |
| USD/INR | – | Vendor quote for intraday. For an official daily benchmark, the RBI reference rate is now computed and published by **FBIL** (since 10 Jul 2018), not RBI. | Vendor quote for live use; FBIL daily rate can be loaded as a historical/EOD cross-check. |
| Historical data for training | – | Needs a licensed historical dataset (CME historical data is a separately-licensed product). | `CsvHistoricalProvider` loads your licensed export. A **synthetic** generator is included for pipeline testing only. |

## Hard rules baked into the code
1. No webpage scraping providers exist in the codebase.
2. Every stored quote carries `provider`, `is_delayed`, `delay_minutes`, UTC / market / IST timestamps, and a quality status.
3. Delayed data is flagged; the prediction run records `data_quality`.
4. Stale or missing inputs → the API returns HTTP 422 and **no prediction row is written**.
5. Credentials are placeholders in `.env.example`.

## Open items only you can resolve
- Choose and contract a COMEX source and an MCX source, then fill `COMEX_*` / `MCX_*` in `.env`.
- Confirm whether a public Telegram channel counts as "redistribution" under your data licenses (it very likely does for exchange data; publishing *your derived forecast* may be treated differently by different vendors).
- Fill the MCX holiday file (`data/holidays/in_mcx_*.json`) from the official MCX circular.
- Confirm the current Indian import-duty rate used in `PricingAssumptions` (default is a **placeholder**).
