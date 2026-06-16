-- Diagnóstico de locks órfãos no Pocket Cíclico (PostgreSQL)
-- Uso: executar em READ ONLY para identificar falsos bloqueios.

WITH locks_ativos AS (
    SELECT
        l.id,
        l.tipo_inventario,
        l.ciclo_id,
        l.ciclo_item_id,
        l.tarefa_id,
        l.posicao_id,
        l.usuario_id,
        l.session_key,
        l.adquirido_em,
        l.renovado_em,
        l.expira_em,
        l.ativo,
        CASE
            WHEN l.expira_em <= NOW() THEN 'EXPIRADO_TIMEOUT'
            WHEN l.session_key IS NOT NULL
                 AND l.session_key <> ''
                 AND s.session_key IS NULL THEN 'SESSAO_ORFA'
            ELSE 'ATIVO_VALIDO'
        END AS diagnostico_lock
    FROM inventario_inventariolock l
    LEFT JOIN django_session s
        ON s.session_key = l.session_key
       AND s.expire_date > NOW()
    WHERE l.ativo = TRUE
      AND l.tipo_inventario = 'CICLICO'
)
SELECT
    la.id AS lock_id,
    la.diagnostico_lock,
    la.usuario_id AS operador_lock_id,
    u.username AS operador_lock_login,
    la.session_key,
    la.adquirido_em,
    la.renovado_em,
    la.expira_em,
    la.ciclo_id,
    ci.codigo_posicao,
    ci.status_contagem AS status_item_ciclico,
    t.id AS tarefa_id,
    t.status AS status_tarefa,
    t.operador_id AS operador_tarefa_id
FROM locks_ativos la
LEFT JOIN auth_user u
    ON u.id = la.usuario_id
LEFT JOIN inventario_cicloinventarioitem ci
    ON ci.id = la.ciclo_item_id
LEFT JOIN inventario_inventariotarefa t
    ON t.id = la.tarefa_id
ORDER BY
    CASE la.diagnostico_lock
        WHEN 'SESSAO_ORFA' THEN 1
        WHEN 'EXPIRADO_TIMEOUT' THEN 2
        ELSE 3
    END,
    la.adquirido_em ASC;
