-- =====================================================================
-- 02_views.sql: views analiticas e base de features para o modelo
-- =====================================================================

-- 1) Base de ML: um registro por ALERTA (acao = 'warn'), com features
--    historicas calculadas por funcoes de janela usando SOMENTE eventos
--    anteriores ao alerta (sem olhar o futuro = sem vazamento temporal).
CREATE OR REPLACE VIEW dw.vw_base_alertas AS
WITH eventos AS (
    SELECT f.*,
           ROW_NUMBER() OVER (PARTITION BY f.usuario_sk ORDER BY f.ts_local, f.evento_id) AS seq
    FROM dw.fato_evento_acesso f
),
hist AS (
    SELECT e.*,
        -- alertas recebidos antes, no mesmo dia (fadiga de alerta)
        COUNT(*) FILTER (WHERE e.acao = 'warn') OVER w_dia - (e.acao = 'warn')::int    AS alertas_previos_dia,
        -- historico do usuario ate o evento anterior
        COUNT(*) FILTER (WHERE e.acao = 'warn')  OVER w_hist AS n_alertas_hist,
        SUM(CASE WHEN e.prosseguiu THEN 1 ELSE 0 END) OVER w_hist AS n_ignorados_hist,
        COUNT(*) FILTER (WHERE e.acao = 'block') OVER w_hist AS n_bloqueios_hist,
        COUNT(*) OVER w_hist                                  AS n_eventos_hist
    FROM eventos e
    WINDOW
        w_dia  AS (PARTITION BY e.usuario_sk, e.data_id ORDER BY e.seq
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
        w_hist AS (PARTITION BY e.usuario_sk ORDER BY e.seq
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
)
SELECT
    h.evento_id, h.ts_local, d.data, h.hora, d.dia_semana, d.tipo_periodo,
    d.semana_prova, d.entrega_trabalhos, d.feriado, d.semana_letiva,
    u.perfil, u.curso, u.area_ti, u.semestre, u.turno,
    (u.data_treinamento_ia IS NOT NULL AND u.data_treinamento_ia <= d.data) AS treinado_ia,
    es.local, s.dominio, s.categoria, s.catalogado_interno, s.no_catalogo_externo,
    h.exposicao, h.navegador,
    h.alertas_previos_dia,
    COALESCE(h.n_alertas_hist, 0)   AS n_alertas_hist,
    COALESCE(h.n_ignorados_hist, 0) AS n_ignorados_hist,
    COALESCE(h.n_bloqueios_hist, 0) AS n_bloqueios_hist,
    COALESCE(h.n_eventos_hist, 0)   AS n_eventos_hist,
    h.tempo_decisao_ms,                    -- mantido so para demonstrar vazamento
    h.prosseguiu::int AS alvo_prosseguiu
FROM hist h
JOIN dw.dim_data    d  ON d.data_id    = h.data_id
JOIN dw.dim_usuario u  ON u.usuario_sk = h.usuario_sk
JOIN dw.dim_estacao es ON es.estacao_sk = h.estacao_sk
JOIN dw.dim_servico s  ON s.servico_sk = h.servico_sk
WHERE h.acao = 'warn';

-- 2) Painel diario de governanca (volume e taxa de alerta ignorado)
CREATE OR REPLACE VIEW dw.vw_painel_diario AS
SELECT d.data, d.tipo_periodo, d.feriado,
       COUNT(*)                                            AS eventos,
       COUNT(*) FILTER (WHERE f.acao = 'warn')             AS alertas,
       COUNT(*) FILTER (WHERE f.acao = 'block')            AS bloqueios,
       ROUND(AVG(f.prosseguiu::int)::numeric, 3)           AS taxa_ignorou_alerta,
       COUNT(DISTINCT f.usuario_sk)                        AS usuarios_ativos
FROM dw.fato_evento_acesso f
JOIN dw.dim_data d ON d.data_id = f.data_id
GROUP BY d.data, d.tipo_periodo, d.feriado;

-- 3) Ranking de servicos de IA (Shadow AI catalogada x descoberta)
CREATE OR REPLACE VIEW dw.vw_ranking_servicos AS
SELECT s.servico, s.dominio, s.categoria, s.politica, s.catalogado_interno,
       COUNT(*)                                  AS acessos,
       COUNT(DISTINCT f.usuario_sk)              AS usuarios,
       ROUND(AVG(f.prosseguiu::int)::numeric, 3) AS taxa_ignorou_alerta
FROM dw.fato_evento_acesso f
JOIN dw.dim_servico s ON s.servico_sk = f.servico_sk
GROUP BY s.servico, s.dominio, s.categoria, s.politica, s.catalogado_interno;
