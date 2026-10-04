-- =====================================================================
-- 03_consultas_analiticas.sql: consultas de apoio a governanca
-- psql -U postgres -d sentinela_dw -f sql/03_consultas_analiticas.sql
-- =====================================================================

-- Q1. Volume por tipo de periodo letivo (efeito do calendario academico)
SELECT d.tipo_periodo,
       COUNT(DISTINCT d.data)                                   AS dias,
       COUNT(*)                                                 AS eventos,
       ROUND(COUNT(*)::numeric / COUNT(DISTINCT d.data), 1)     AS eventos_por_dia,
       ROUND(AVG(f.prosseguiu::int)::numeric, 3)                AS taxa_ignorou_alerta
FROM dw.fato_evento_acesso f
JOIN dw.dim_data d USING (data_id)
GROUP BY d.tipo_periodo
ORDER BY eventos_por_dia DESC;

-- Q2. Top 10 servicos de IA mais acessados (catalogados x Shadow AI descoberta)
SELECT servico, categoria, politica, catalogado_interno, acessos, usuarios, taxa_ignorou_alerta
FROM dw.vw_ranking_servicos
ORDER BY acessos DESC
LIMIT 10;

-- Q3. Taxa de alerta ignorado por perfil e treinamento em IA
SELECT perfil, treinado_ia, COUNT(*) AS alertas,
       ROUND(AVG(alvo_prosseguiu)::numeric, 3) AS taxa_ignorou
FROM dw.vw_base_alertas
GROUP BY perfil, treinado_ia
ORDER BY perfil, treinado_ia;

-- Q4. Fadiga de alerta: quanto mais alertas no dia, maior a chance de ignorar?
SELECT LEAST(alertas_previos_dia, 5) AS alertas_antes_no_dia,
       COUNT(*) AS alertas,
       ROUND(AVG(alvo_prosseguiu)::numeric, 3) AS taxa_ignorou
FROM dw.vw_base_alertas
GROUP BY 1 ORDER BY 1;

-- Q5. Feriados x dias letivos (fonte BrasilAPI)
SELECT d.feriado, COUNT(DISTINCT d.data) AS dias, COUNT(f.evento_id) AS eventos
FROM dw.dim_data d
LEFT JOIN dw.fato_evento_acesso f USING (data_id)
WHERE d.fim_de_semana = FALSE
GROUP BY d.feriado;
