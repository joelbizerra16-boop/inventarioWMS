-- Desbloqueio seguro de locks órfãos/expirados do Pocket Cíclico (PostgreSQL)
-- Recomendado: executar dentro de transação manual e revisar o RETURNING.
--
-- BEGIN;
--   <script>
-- ROLLBACK; -- use COMMIT apenas após validação.

WITH candidatos AS (
    SELECT l.id
    FROM inventario_inventariolock l
    LEFT JOIN django_session s
        ON s.session_key = l.session_key
       AND s.expire_date > NOW()
    WHERE l.ativo = TRUE
      AND l.tipo_inventario = 'CICLICO'
      AND (
            l.expira_em <= NOW()
         OR (
                l.session_key IS NOT NULL
            AND l.session_key <> ''
            AND s.session_key IS NULL
         )
      )
),
locks_liberados AS (
    UPDATE inventario_inventariolock l
       SET ativo = FALSE,
           liberado_em = NOW(),
           motivo_liberacao = CASE
               WHEN l.expira_em <= NOW() THEN 'TIMEOUT'
               ELSE 'SESSAO'
           END
     WHERE l.id IN (SELECT id FROM candidatos)
    RETURNING l.id, l.tarefa_id, l.usuario_id, l.posicao_id, l.ciclo_id, l.motivo_liberacao
),
tarefas_reabertas AS (
    UPDATE inventario_inventariotarefa t
       SET status = 'PENDENTE'
     WHERE t.id IN (
         SELECT tarefa_id
         FROM locks_liberados
         WHERE tarefa_id IS NOT NULL
     )
       AND t.status = 'EM_CONTAGEM'
    RETURNING t.id, t.status
)
SELECT
    ll.id AS lock_id,
    ll.usuario_id,
    ll.posicao_id,
    ll.ciclo_id,
    ll.tarefa_id,
    ll.motivo_liberacao
FROM locks_liberados ll
ORDER BY ll.id;
