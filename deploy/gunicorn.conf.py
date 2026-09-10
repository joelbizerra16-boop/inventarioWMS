"""Gunicorn — produção InventarioWMS (6 pockets + operadores web)."""
import multiprocessing
import os

bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8000')

# Workers: (2 x CPU) + 1, limitado para VPS pequenas com 6 pockets.
_cpu = multiprocessing.cpu_count() or 1
_default_workers = min(max(2 * _cpu + 1, 3), 6)
workers = int(os.environ.get('GUNICORN_WORKERS', str(_default_workers)))

threads = int(os.environ.get('GUNICORN_THREADS', '2'))
worker_class = 'gthread'
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
capture_output = True
preload_app = False
