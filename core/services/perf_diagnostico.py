import logging
import time
from contextlib import ContextDecorator

from django.db import connection
from django.test.utils import CaptureQueriesContext

logger = logging.getLogger('perf.diagnostico')

_SQL_MAX_LEN = 240


def _formatar_sql(sql: str | None) -> str:
    if not sql:
        return '-'
    texto = ' '.join(str(sql).split())
    if len(texto) <= _SQL_MAX_LEN:
        return texto
    return f"{texto[:_SQL_MAX_LEN]}..."


def _query_mais_lenta(captured_queries: list[dict]) -> tuple[float, str]:
    if not captured_queries:
        return 0.0, '-'

    tempo_max = 0.0
    sql_max = '-'
    for item in captured_queries:
        try:
            tempo_atual = float(item.get('time') or 0.0)
        except (TypeError, ValueError):
            tempo_atual = 0.0
        if tempo_atual >= tempo_max:
            tempo_max = tempo_atual
            sql_max = _formatar_sql(item.get('sql'))
    return tempo_max, sql_max


class medir_etapa(ContextDecorator):
    """Medição temporária de tempo e SQL por etapa sem alterar comportamento."""

    def __init__(self, etapa: str):
        self.etapa = etapa
        self._inicio = 0.0
        self._captura = None
        self._force_debug_anterior = None

    def __enter__(self):
        self._inicio = time.perf_counter()
        self._force_debug_anterior = connection.force_debug_cursor
        connection.force_debug_cursor = True
        self._captura = CaptureQueriesContext(connection)
        self._captura.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        fim = time.perf_counter()
        captura = self._captura
        if captura is not None:
            captura.__exit__(exc_type, exc, tb)
        if self._force_debug_anterior is not None:
            connection.force_debug_cursor = self._force_debug_anterior

        queries = list(getattr(captura, 'captured_queries', [])) if captura else []
        tempo_sql_lenta, sql_lenta = _query_mais_lenta(queries)
        logger.info(
            'PERF_ETAPA etapa=%s tempo=%.4fs queries=%s query_lenta=%.4fs sql_lenta="%s"',
            self.etapa,
            fim - self._inicio,
            len(queries),
            tempo_sql_lenta,
            sql_lenta,
        )
        return False


def log_resumo_view(nome_view: str, captured_queries: list[dict], duracao_segundos: float) -> None:
    tempo_sql_lenta, sql_lenta = _query_mais_lenta(captured_queries)
    logger.info(
        'PERF_VIEW view=%s tempo=%.4fs queries=%s query_lenta=%.4fs sql_lenta="%s"',
        nome_view,
        duracao_segundos,
        len(captured_queries),
        tempo_sql_lenta,
        sql_lenta,
    )
