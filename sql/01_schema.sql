-- =====================================================================
-- Projeto Integrador BDED 2026.1: Sentinela Shadow AI
-- 01_schema.sql: camadas staging (dados brutos) e dw (modelo estrela)
-- Executado automaticamente pelo ETL (src/etl/pipeline_etl.py) ou via:
--   psql -U postgres -d sentinela_dw -f sql/01_schema.sql
-- =====================================================================

DROP SCHEMA IF EXISTS dw CASCADE;
DROP SCHEMA IF EXISTS staging CASCADE;
CREATE SCHEMA staging;
CREATE SCHEMA dw;

-- ---------------------------------------------------------------------
-- STAGING: copia fiel das fontes (texto, sem regras), para auditoria
-- ---------------------------------------------------------------------
CREATE TABLE staging.stg_eventos (
    event_id            TEXT,
    ts                  TEXT,
    station_id          TEXT,
    user_hash           TEXT,
    domain              TEXT,
    action              TEXT,
    exposure            TEXT,
    ext_version         TEXT,
    browser             TEXT,
    decision            TEXT,
    time_to_decision_ms DOUBLE PRECISION,
    carregado_em        TIMESTAMP DEFAULT now()
);
CREATE TABLE staging.stg_usuarios (
    user_hash TEXT, perfil TEXT, curso TEXT, semestre DOUBLE PRECISION,
    turno TEXT, data_treinamento_ia TEXT
);
CREATE TABLE staging.stg_estacoes (station_id TEXT, local TEXT, bloco TEXT);
CREATE TABLE staging.stg_politica (dominio TEXT, servico TEXT, categoria TEXT, politica TEXT);
CREATE TABLE staging.stg_revisao_catalogo (dominio TEXT, categoria TEXT);
CREATE TABLE staging.stg_catalogo_externo (dominio TEXT, origem TEXT);
CREATE TABLE staging.stg_feriados (data DATE, nome TEXT, tipo TEXT, origem TEXT);
CREATE TABLE staging.stg_calendario (data_inicio DATE, data_fim DATE, evento TEXT, tipo TEXT);

-- ---------------------------------------------------------------------
-- DW: modelo dimensional (estrela) com 1 fato e 4 dimensoes
-- ---------------------------------------------------------------------
CREATE TABLE dw.dim_data (
    data_id           INTEGER PRIMARY KEY,           -- AAAAMMDD
    data              DATE NOT NULL UNIQUE,
    ano               SMALLINT NOT NULL,
    mes               SMALLINT NOT NULL CHECK (mes BETWEEN 1 AND 12),
    dia               SMALLINT NOT NULL,
    dia_semana        SMALLINT NOT NULL CHECK (dia_semana BETWEEN 0 AND 6), -- 0 = segunda
    nome_dia          VARCHAR(10) NOT NULL,
    fim_de_semana     BOOLEAN NOT NULL,
    feriado           BOOLEAN NOT NULL,
    nome_feriado      VARCHAR(80),
    tipo_periodo      VARCHAR(30) NOT NULL,           -- aulas, semana_prova, recesso...
    semana_prova      BOOLEAN NOT NULL,
    entrega_trabalhos BOOLEAN NOT NULL,
    semana_letiva     SMALLINT NOT NULL
);

CREATE TABLE dw.dim_usuario (
    usuario_sk          SERIAL PRIMARY KEY,
    user_hash           VARCHAR(16) NOT NULL UNIQUE,  -- pseudonimo (LGPD)
    perfil              VARCHAR(20) NOT NULL CHECK (perfil IN ('aluno','docente','administrativo')),
    curso               VARCHAR(40),
    area_ti             BOOLEAN NOT NULL DEFAULT FALSE,
    semestre            SMALLINT CHECK (semestre BETWEEN 1 AND 10),
    turno               VARCHAR(10) NOT NULL CHECK (turno IN ('manha','noite')),
    data_treinamento_ia DATE
);

CREATE TABLE dw.dim_estacao (
    estacao_sk SERIAL PRIMARY KEY,
    station_id VARCHAR(16) NOT NULL UNIQUE,
    local      VARCHAR(20) NOT NULL CHECK (local IN ('laboratorio','biblioteca','administrativo','sala_docentes')),
    bloco      CHAR(1) NOT NULL
);

CREATE TABLE dw.dim_servico (
    servico_sk          SERIAL PRIMARY KEY,
    dominio             VARCHAR(80) NOT NULL UNIQUE,
    servico             VARCHAR(60) NOT NULL,
    categoria           VARCHAR(30) NOT NULL,
    politica            VARCHAR(10) NOT NULL CHECK (politica IN ('allow','warn','block')),
    catalogado_interno  BOOLEAN NOT NULL,   -- esta na politica do Sentinela?
    no_catalogo_externo BOOLEAN NOT NULL    -- aparece na lista publica do GitHub?
);

CREATE TABLE dw.fato_evento_acesso (
    evento_id         UUID PRIMARY KEY,
    data_id           INTEGER  NOT NULL REFERENCES dw.dim_data(data_id),
    usuario_sk        INTEGER  NOT NULL REFERENCES dw.dim_usuario(usuario_sk),
    estacao_sk        INTEGER  NOT NULL REFERENCES dw.dim_estacao(estacao_sk),
    servico_sk        INTEGER  NOT NULL REFERENCES dw.dim_servico(servico_sk),
    ts_local          TIMESTAMP NOT NULL,                 -- America/Sao_Paulo
    hora              SMALLINT NOT NULL CHECK (hora BETWEEN 0 AND 23),
    acao              VARCHAR(10) NOT NULL CHECK (acao IN ('allow','warn','block')),
    exposicao         VARCHAR(20) NOT NULL,
    decisao           VARCHAR(10) CHECK (decisao IN ('proceeded','left')),
    prosseguiu        BOOLEAN,                            -- ALVO (so para acao = warn)
    tempo_decisao_ms  INTEGER,                            -- NAO usar como feature (vazamento)
    versao_extensao   VARCHAR(10),
    navegador         VARCHAR(10),
    CONSTRAINT ck_decisao_warn CHECK ((acao = 'warn') = (decisao IS NOT NULL))
);

CREATE INDEX ix_fato_data     ON dw.fato_evento_acesso(data_id);
CREATE INDEX ix_fato_usuario  ON dw.fato_evento_acesso(usuario_sk, ts_local);
CREATE INDEX ix_fato_servico  ON dw.fato_evento_acesso(servico_sk);
CREATE INDEX ix_fato_acao     ON dw.fato_evento_acesso(acao);

-- Quarentena: registros rejeitados pelo ETL (rastreabilidade)
CREATE TABLE dw.quarentena_eventos (
    event_id TEXT, motivo TEXT, registro JSONB, rejeitado_em TIMESTAMP DEFAULT now()
);
