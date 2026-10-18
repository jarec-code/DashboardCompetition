# Loaded automatically by gunicorn from the working directory.
# Reads PORT in Python, so it works even when the start command isn't run through a shell.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 1
threads = 4
timeout = 180  # scraping several competitor pages can be slow
accesslog = "-"
errorlog = "-"


def when_ready(server):
    server.log.info("Competition Checker listening on %s (Railway domain target port must match)", bind)
