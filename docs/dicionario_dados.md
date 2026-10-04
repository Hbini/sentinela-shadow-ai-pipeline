# Dicionário de dados

## Esquema `dw` (modelo estrela)

### `dw.fato_evento_acesso` (grão: 1 evento de acesso a serviço de IA)
| Coluna | Tipo | Descrição |
|--------|------|-----------|
| evento_id | UUID (PK) | identificador do evento gerado pela extensão |
| data_id | INTEGER (FK) | data no formato AAAAMMDD (dim_data) |
| usuario_sk | INTEGER (FK) | chave substituta do usuário |
| estacao_sk | INTEGER (FK) | chave substituta da estação |
| servico_sk | INTEGER (FK) | chave substituta do serviço de IA |
| ts_local | TIMESTAMP | data e hora no fuso America/Sao_Paulo (convertido de UTC no ETL) |
| hora | SMALLINT | hora local (0 a 23) |
| acao | VARCHAR | ação do Sentinela: allow, warn, block |
| exposicao | VARCHAR | potencial de exposição de contexto: nenhuma, contexto_baixo, contexto_alto, nao_informado |
| decisao | VARCHAR | proceeded ou left (só quando acao = warn) |
| prosseguiu | BOOLEAN | **alvo**: usuário ignorou o alerta e prosseguiu |
| tempo_decisao_ms | INTEGER | tempo até a decisão; **não usar como feature (vazamento)** |
| versao_extensao, navegador | VARCHAR | metadados técnicos |

### `dw.dim_data`
data_id, data, ano, mes, dia, dia_semana (0 = segunda), nome_dia, fim_de_semana, feriado e nome_feriado (BrasilAPI),
tipo_periodo, semana_prova, entrega_trabalhos (calendário acadêmico), semana_letiva.

### `dw.dim_usuario`
usuario_sk, user_hash (pseudônimo), perfil (aluno, docente, administrativo), curso, area_ti, semestre (alunos),
turno, data_treinamento_ia (data em que concluiu o letramento em IA; nulo se não fez).

### `dw.dim_estacao`
estacao_sk, station_id (pseudônimo), local (laboratorio, biblioteca, administrativo, sala_docentes), bloco.

### `dw.dim_servico`
servico_sk, dominio, servico, categoria (chat_llm, escrita_parafrase, resolucao_exercicios, codigo, imagem_video,
audio, apresentacao, pesquisa_academica), politica (allow, warn, block), catalogado_interno (está na política do
Sentinela) e no_catalogo_externo (aparece no catálogo público do GitHub).

### `dw.quarentena_eventos`
Eventos rejeitados pelo ETL (chave sem correspondência nas dimensões), com o registro original em JSONB.

## Features do modelo (`src/features/build_features.py`)
| Feature | Origem | Descrição |
|---------|--------|-----------|
| hora_sin, hora_cos | fato | hora codificada em círculo (23h fica perto de 0h) |
| noturno | fato | 1 se hora maior ou igual a 19 |
| dia_semana, semana_letiva | dim_data | dia da semana e semana do semestre |
| semana_prova, entrega_trabalhos | dim_data (calendário) | período acadêmico crítico |
| feriado | dim_data (BrasilAPI) | dia de feriado nacional |
| perfil, curso, area_ti, turno | dim_usuario | perfil acadêmico |
| semestre_num, calouro | dim_usuario | semestre (0 para não alunos) e 1o/2o semestre |
| treinado_ia | dim_usuario + dim_data | já tinha feito o letramento em IA na data do evento |
| local | dim_estacao | tipo de ambiente da estação |
| exposicao, navegador | fato | contexto do acesso |
| categoria, catalogado_interno, no_catalogo_externo | dim_servico (GitHub) | tipo e catalogação do serviço |
| alertas_previos_dia, log_alertas_previos_dia | SQL janela | alertas recebidos antes, no mesmo dia (fadiga) |
| n_alertas_hist, log_eventos_hist | SQL janela | volume de histórico do usuário até o evento anterior |
| taxa_ignorar_hist | SQL janela | (ignorados + 1) / (alertas + 2), suavização de Laplace |
| taxa_bloqueio_hist | SQL janela | proporção de eventos bloqueados no histórico |
| sem_historico | SQL janela | 1 se é o primeiro alerta do usuário |
