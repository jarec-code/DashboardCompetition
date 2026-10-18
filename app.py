import csv
import io
import os
from datetime import datetime

from flask import Flask, Response, flash, redirect, render_template, request, url_for

from db import close_db, get_db, init_db
from matcher import assign_matches
from scraper import parse_price, scrape_competitor

app = Flask(__name__)
app.secret_key = "competition-checker-local"
app.teardown_appcontext(close_db)
init_db()


# ---------- helpers ----------

def load_links(db):
    return {r["competitor_product_id"]: r["our_product_id"] for r in db.execute("SELECT * FROM links")}


def build_comparison(db):
    """Return (our_products, competitors, rows) where each row has per-competitor match info."""
    ours = db.execute("SELECT * FROM our_products ORDER BY name").fetchall()
    competitors = db.execute("SELECT * FROM competitors ORDER BY name").fetchall()
    cps = db.execute("SELECT * FROM competitor_products").fetchall()
    matches = assign_matches(ours, cps, load_links(db))
    cp_by_id = {cp["id"]: cp for cp in cps}

    # (our_id, competitor_id) -> best match; manual links win over automatic ones
    cells = {}
    for cp_id, (our_id, score, manual) in matches.items():
        cp = cp_by_id[cp_id]
        key = (our_id, cp["competitor_id"])
        current = cells.get(key)
        if current is None or (manual, score) > (current["manual"], current["score"]):
            cells[key] = {"cp": cp, "score": score, "manual": manual}

    rows = []
    for op in ours:
        row = {"product": op, "cells": {}, "min_price": op["price"], "cheapest": "us"}
        for comp in competitors:
            cell = cells.get((op["id"], comp["id"]))
            if cell and cell["cp"]["price"] is not None:
                price = cell["cp"]["price"]
                cell["diff"] = price - op["price"]
                cell["diff_pct"] = (price - op["price"]) / op["price"] * 100 if op["price"] else None
                cell["currency_mismatch"] = bool(cell["cp"]["currency"]) and cell["cp"]["currency"] != op["currency"]
                if price < row["min_price"]:
                    row["min_price"], row["cheapest"] = price, comp["id"]
            row["cells"][comp["id"]] = cell
        rows.append(row)
    return ours, competitors, rows


def run_scrape(db, comp):
    urls = [u.strip() for u in comp["urls"].splitlines() if u.strip()]
    products, status = scrape_competitor(urls, comp["item_selector"], comp["name_selector"], comp["price_selector"])
    now = datetime.now().isoformat(timespec="seconds")
    if products:
        # Keep manual links working across re-scrapes by re-attaching them by name.
        old = db.execute(
            "SELECT cp.name, l.our_product_id FROM links l JOIN competitor_products cp "
            "ON cp.id = l.competitor_product_id WHERE cp.competitor_id = ?", (comp["id"],)).fetchall()
        old_links = {r["name"]: r["our_product_id"] for r in old}
        db.execute("DELETE FROM competitor_products WHERE competitor_id = ?", (comp["id"],))
        for p in products:
            cur = db.execute(
                "INSERT INTO competitor_products (competitor_id, name, price, currency, url, scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (comp["id"], p["name"], p["price"], p["currency"], p["url"], now))
            if p["name"] in old_links:
                db.execute("INSERT OR REPLACE INTO links VALUES (?, ?)", (cur.lastrowid, old_links[p["name"]]))
    db.execute("UPDATE competitors SET last_scraped = ?, last_status = ? WHERE id = ?", (now, status, comp["id"]))
    db.commit()
    return len(products), status


# ---------- comparison ----------

@app.route("/")
def index():
    db = get_db()
    ours, competitors, rows = build_comparison(db)
    summary = {
        "total": len(rows),
        "we_cheapest": sum(1 for r in rows if r["cheapest"] == "us" and any(r["cells"].values())),
        "undercut": sum(1 for r in rows if r["cheapest"] != "us"),
        "unmatched": sum(1 for r in rows if not any(r["cells"].values())),
    }
    return render_template("index.html", competitors=competitors, rows=rows, summary=summary)


@app.route("/export.csv")
def export_csv():
    _, competitors, rows = build_comparison(get_db())
    out = io.StringIO()
    writer = csv.writer(out)
    header = ["Our product", "SKU", "Our price"]
    for c in competitors:
        header += [f"{c['name']} product", f"{c['name']} price", f"{c['name']} diff %"]
    writer.writerow(header)
    for r in rows:
        p = r["product"]
        line = [p["name"], p["sku"] or "", p["price"]]
        for c in competitors:
            cell = r["cells"].get(c["id"])
            if cell:
                pct = cell.get("diff_pct")
                line += [cell["cp"]["name"], cell["cp"]["price"], f"{pct:.1f}" if pct is not None else ""]
            else:
                line += ["", "", ""]
        writer.writerow(line)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=price-comparison.csv"})


# ---------- our products ----------

@app.route("/products", methods=["GET", "POST"])
def products():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = parse_price(request.form.get("price"))
        if not name or price is None:
            flash("Name and a valid price are required.", "error")
        else:
            db.execute("INSERT INTO our_products (name, sku, price, currency) VALUES (?, ?, ?, ?)",
                       (name, request.form.get("sku", "").strip() or None, price,
                        request.form.get("currency", "EUR").strip().upper() or "EUR"))
            db.commit()
            flash(f"Added {name}.", "ok")
        return redirect(url_for("products"))
    items = db.execute("SELECT * FROM our_products ORDER BY name").fetchall()
    return render_template("products.html", products=items)


@app.route("/products/<int:pid>/update", methods=["POST"])
def update_product(pid):
    price = parse_price(request.form.get("price"))
    name = request.form.get("name", "").strip()
    if not name or price is None:
        flash("Name and a valid price are required.", "error")
    else:
        db = get_db()
        db.execute("UPDATE our_products SET name = ?, sku = ?, price = ? WHERE id = ?",
                   (name, request.form.get("sku", "").strip() or None, price, pid))
        db.commit()
    return redirect(url_for("products"))


@app.route("/products/<int:pid>/delete", methods=["POST"])
def delete_product(pid):
    db = get_db()
    db.execute("DELETE FROM our_products WHERE id = ?", (pid,))
    db.commit()
    return redirect(url_for("products"))


@app.route("/products/import", methods=["POST"])
def import_products():
    file = request.files.get("file")
    if not file:
        flash("Choose a CSV file.", "error")
        return redirect(url_for("products"))
    text = file.read().decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t") if text else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    db = get_db()
    added = skipped = 0
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        price = parse_price(row.get("price"))
        if not row.get("name") or price is None:
            skipped += 1
            continue
        db.execute("INSERT INTO our_products (name, sku, price, currency) VALUES (?, ?, ?, ?)",
                   (row["name"], row.get("sku") or None, price, (row.get("currency") or "EUR").upper()))
        added += 1
    db.commit()
    flash(f"Imported {added} products" + (f", skipped {skipped} invalid rows." if skipped else "."), "ok")
    return redirect(url_for("products"))


# ---------- competitors ----------

@app.route("/competitors", methods=["GET", "POST"])
def competitors():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        urls = "\n".join(u.strip() for u in request.form.get("urls", "").splitlines() if u.strip())
        if not name or not urls:
            flash("Name and at least one URL are required.", "error")
        elif any(not u.startswith(("http://", "https://")) for u in urls.splitlines()):
            flash("URLs must start with http:// or https://", "error")
        else:
            cur = db.execute(
                "INSERT INTO competitors (name, urls, item_selector, name_selector, price_selector) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, urls, *(request.form.get(k, "").strip() or None
                               for k in ("item_selector", "name_selector", "price_selector"))))
            db.commit()
            comp = db.execute("SELECT * FROM competitors WHERE id = ?", (cur.lastrowid,)).fetchone()
            count, status = run_scrape(db, comp)
            flash(f"Added {name}: {status}", "ok" if count else "error")
        return redirect(url_for("competitors"))
    items = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM competitor_products cp WHERE cp.competitor_id = c.id) AS product_count "
        "FROM competitors c ORDER BY c.name").fetchall()
    return render_template("competitors.html", competitors=items)


@app.route("/competitors/<int:cid>")
def competitor_detail(cid):
    db = get_db()
    comp = db.execute("SELECT * FROM competitors WHERE id = ?", (cid,)).fetchone()
    if comp is None:
        return redirect(url_for("competitors"))
    ours = db.execute("SELECT * FROM our_products ORDER BY name").fetchall()
    cps = db.execute("SELECT * FROM competitor_products WHERE competitor_id = ? ORDER BY name", (cid,)).fetchall()
    links = load_links(db)
    matches = assign_matches(ours, cps, links)
    ours_by_id = {o["id"]: o for o in ours}
    return render_template("competitor.html", comp=comp, products=cps, ours=ours, ours_by_id=ours_by_id,
                           matches=matches, links=links)


@app.route("/competitors/<int:cid>/update", methods=["POST"])
def update_competitor(cid):
    db = get_db()
    urls = "\n".join(u.strip() for u in request.form.get("urls", "").splitlines() if u.strip())
    db.execute("UPDATE competitors SET name = ?, urls = ?, item_selector = ?, name_selector = ?, price_selector = ? "
               "WHERE id = ?",
               (request.form.get("name", "").strip(), urls,
                *(request.form.get(k, "").strip() or None for k in ("item_selector", "name_selector", "price_selector")),
                cid))
    db.commit()
    flash("Saved.", "ok")
    return redirect(url_for("competitor_detail", cid=cid))


@app.route("/competitors/<int:cid>/scrape", methods=["POST"])
def scrape(cid):
    db = get_db()
    comp = db.execute("SELECT * FROM competitors WHERE id = ?", (cid,)).fetchone()
    if comp:
        count, status = run_scrape(db, comp)
        flash(f"{comp['name']}: {status}", "ok" if count else "error")
    return redirect(request.referrer or url_for("competitors"))


@app.route("/scrape-all", methods=["POST"])
def scrape_all():
    db = get_db()
    for comp in db.execute("SELECT * FROM competitors").fetchall():
        count, status = run_scrape(db, comp)
        flash(f"{comp['name']}: {status}", "ok" if count else "error")
    return redirect(request.referrer or url_for("index"))


@app.route("/competitors/<int:cid>/delete", methods=["POST"])
def delete_competitor(cid):
    db = get_db()
    db.execute("DELETE FROM competitors WHERE id = ?", (cid,))
    db.commit()
    return redirect(url_for("competitors"))


@app.route("/link", methods=["POST"])
def link():
    """Set a manual match: value 'auto' (remove override), 'none' (ignore) or an our_product id."""
    db = get_db()
    cp_id = int(request.form["cp_id"])
    value = request.form.get("our_id", "auto")
    if value == "auto":
        db.execute("DELETE FROM links WHERE competitor_product_id = ?", (cp_id,))
    else:
        db.execute("INSERT OR REPLACE INTO links VALUES (?, ?)", (cp_id, None if value == "none" else int(value)))
    db.commit()
    return redirect(request.referrer or url_for("index"))


@app.template_filter("money")
def money(value, currency=None):
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ") + (f" {currency}" if currency else "")


if __name__ == "__main__":
    # Not 5000: on macOS that port is taken by the AirPlay Receiver.
    app.run(debug=True, port=int(os.environ.get("PORT", 5050)))
