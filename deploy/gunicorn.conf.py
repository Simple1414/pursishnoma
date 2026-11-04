"""Gunicorn configuration for an Ubuntu VPS behind nginx."""
import multiprocessing
import os

bind = os.getenv("GUNICORN_BIND", "unix:/run/researchsurvey/gunicorn.sock")
workers = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
timeout = 120  # AI assistant requests can take up to a minute
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "127.0.0.1"
