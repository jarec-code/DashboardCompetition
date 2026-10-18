# Competition Checker

A small local web app that pulls product names and prices from competitor websites
and compares them with your own prices.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://localhost:5050 (set `PORT` to use another port; avoid 5000 on macOS, where AirPlay Receiver uses it).

## Deploy on Railway

`railway.json` makes Railway start the app with gunicorn; `gunicorn.conf.py` binds it to `0.0.0.0:$PORT`.
The deploy log prints `Competition Checker listening on 0.0.0.0:<port>`. The public domain's target port
(*Settings → Networking*) must be that same port, otherwise Railway shows "Application failed to respond".

1. Push the repo and deploy it as a Railway service.
2. **Add a volume** to the service (any mount path, e.g. `/data`). The app stores its SQLite database there
   automatically (via `RAILWAY_VOLUME_MOUNT_PATH`). Without a volume, all data is lost on every redeploy.
3. Optional: set the `SECRET_KEY` variable to a random string.
4. Under *Settings → Networking*, click *Generate Domain* to get a public URL.

The app has no login, so anyone with the URL can see and change the data.

## How to use

1. **Our products**: add products by hand, or import a CSV with the columns `name`, `price` and optionally `sku` and `currency`.
2. **Competitors**: add a competitor with one or more page URLs (category/listing pages or single product pages). The app scrapes them straight away.
   - It first reads the schema.org product data (JSON-LD or microdata) or Open Graph price tags that most e-shops publish.
   - If it finds nothing, open the competitor's *Settings* and give CSS selectors for the product card, the name and the price.
3. **Comparison**: each of your products is matched by name to the most similar competitor product and shown with its price difference in %. Red means the competitor is cheaper. If a match is wrong, fix it on the competitor's page (pick a product or *Ignore*). Manual matches are kept when you re-scrape.
4. Use **Refresh all competitors** to scrape again and **Export CSV** to download the table.

## Notes

- Data is stored in `competition.db` (SQLite). Set `COMPETITION_DB` to use a different path.
- The app follows `robots.txt` and fetches one page per URL. Pages that build their product list with JavaScript only may need the CSS-selector option, or may not work at all.
- Tests: `.venv/bin/python -m unittest discover -s tests`
