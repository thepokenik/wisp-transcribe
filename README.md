# Wisp Transcribe

Aplicação independente de transcrição de áudio/vídeo, com interface web, API HTTP, worker CPU e uma CLI feita para agentes. O modelo Whisper continua centralizado no servidor; a CLI apenas envia o arquivo, acompanha a tarefa e baixa resultados estruturados.

Repositório: [thepokenik/wisp-transcribe](https://github.com/thepokenik/wisp-transcribe). O serviço é independente e não requer o workspace de origem nem arquivos de mídia pré-existentes. Mantém faster-whisper small, int8, VAD, `beam_size=1` e `batch_size=8` do script original. Arquivo, idioma e contexto são entradas do usuário; caminhos e termos de reuniões não estão embutidos.

Conteúdo principal:

| Caminho | Papel |
| --- | --- |
| `service.py` | Servidor HTTP e worker de transcrição |
| `index.html` | Interface web em `/` |
| `cli/` | Pacote Python da CLI `transcribe`, sem dependências externas |
| `AGENTS.md` | Contexto do projeto e orientação para agentes de qualquer provedor |
| `skills/wisp-transcribe/` | Skill em inglês para transcrever e resumir |

## Docker (recomendado)

Requisitos: Docker Engine com Compose (ou Docker Desktop em modo Linux), internet no build e na primeira transcrição para baixar o modelo. Reserve inicialmente 4 GB de RAM; ajuste conforme duração dos arquivos e concorrência de uploads. Há uma transcrição por vez.

Clone o repositório e entre na pasta:

```powershell
git clone https://github.com/thepokenik/wisp-transcribe.git
cd wisp-transcribe
```

No PowerShell, dentro desta pasta:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Edite `.env`, substituindo `TRANSCRIPTION_API_KEY` pela chave gerada. Não compartilhe esse arquivo junto do código. Depois:

```powershell
docker compose up -d --build
docker compose logs -f
```

Abra http://localhost:8000, informe a chave, selecione áudio/vídeo e clique em Transcrever. Guarde o ID para consultar depois. A primeira tarefa demora mais pelo download e carregamento do modelo. O andamento mostra segundos de áudio processados, não uma previsão de conclusão.

Para parar, preservando resultados/modelos: `docker compose down`. Para atualizar o código: `docker compose up -d --build`.

## Compartilhar o serviço

Em uma máquina acessível por rede privada/VPN, altere `BIND_ADDRESS=0.0.0.0` no `.env` e recrie o container com `docker compose up -d`. Os usuários acessam `http://IP-DA-MAQUINA:8000` e usam a chave. Libere a porta 8000 apenas para a rede pretendida no firewall.

Para acesso fora de uma rede confiável, coloque um proxy HTTPS autenticado na frente (limite de upload e timeout compatíveis); mantenha a porta interna restrita ao proxy. O servidor HTTP embutido foi destinado a uso interno, não à exposição direta à internet. A chave concede acesso a todos os jobs: não existe isolamento por usuário.

## Execução local sem Docker

Use Python **3.12** em ambiente separado para o servidor (o Python 3.14 do workspace não foi o alvo das dependências fixadas):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TRANSCRIPTION_API_KEY = 'SUA-CHAVE-ALEATORIA-DE-32-CARACTERES'
.\.venv\Scripts\python.exe service.py
```

A execução local lê variáveis do processo; `.env` é usado pelo Compose, não carregado pelo Python. Por padrão escuta somente em 127.0.0.1:8000 e grava em `./data`. Para rede privada: `$env:HOST = '0.0.0.0'` antes de iniciar.

## CLI `transcribe`

Requisitos: Python 3.10 ou superior. A CLI usa apenas a biblioteca padrão. Instale de forma isolada:

```powershell
python -m venv .venv-cli
.\.venv-cli\Scripts\python.exe -m pip install .\cli
$env:TRANSCRIPTION_URL = 'http://localhost:8000'
$env:TRANSCRIPTION_API_KEY = 'SUA-CHAVE'
.\.venv-cli\Scripts\transcribe.exe doctor --json
```

Depois de ativar o ambiente, o comando fica disponível como `transcribe`. Para desenvolvimento, `pip install -e .\cli` evita reinstalar a cada mudança.

### Comandos

| Comando | Uso |
| --- | --- |
| `transcribe run ARQUIVO --language pt --output ./resultado --json` | Envia, aguarda e baixa TXT, JSONL e manifesto |
| `transcribe submit ARQUIVO --json` | Envia e retorna o ID sem aguardar |
| `transcribe status JOB_ID --json` | Consulta o estado |
| `transcribe wait JOB_ID --timeout 1800 --json` | Aguarda a conclusão |
| `transcribe fetch JOB_ID --output ./resultado --json` | Baixa uma tarefa concluída |
| `transcribe doctor --json` | Verifica configuração, autenticação e contrato |

`run` e `fetch` aceitam `--block-seconds N` para dividir a saída em blocos (0 desativa). `wait` e `run` aceitam `--timeout` e `--poll-interval`. `submit` deriva uma chave de idempotência do conteúdo do arquivo e das opções; `--idempotency-key` permite informar uma chave explícita e `--no-idempotency` desativa o comportamento.

### Saída para agentes

Com `--json`, o stdout contém **somente** um objeto JSON estável; progresso e mensagens operacionais vão para o stderr. Exemplo de `run` concluído:

```json
{
  "schema_version": 1,
  "ok": true,
  "job_id": "uuid",
  "state": "completed",
  "files": {
    "text": "C:\\caminho\\resultado\\transcript.txt",
    "segments": "C:\\caminho\\resultado\\transcript.jsonl",
    "manifest": "C:\\caminho\\resultado\\manifest.json"
  },
  "blocks": [],
  "metadata": {
    "language": "pt",
    "duration_seconds": 742.31,
    "model": "small",
    "segment_count": 183,
    "options": {"beam_size": 1, "batch_size": 8, "vad_filter": true}
  }
}
```

Erros usam `{"schema_version": 1, "ok": false, "error": {"code": "...", "message": "..."}}`. Em timeout, o payload preserva `job_id`, `state` e um comando `resume`; a tarefa continua no servidor.

| Código de saída | Significado |
| --- | --- |
| 0 | Sucesso |
| 1 | Erro inesperado do cliente |
| 2 | Configuração, argumentos ou arquivo inválido |
| 3 | Autenticação recusada |
| 4 | Serviço indisponível, fila cheia, tarefa ainda não pronta ou contrato incompatível |
| 5 | Tempo de espera esgotado; use `wait`/`fetch` com o `job_id` |
| 6 | Falha de transcrição, mídia inválida ou tarefa interrompida por reinício |
| 7 | Tarefa não encontrada |
| 8 | Conflito de idempotência (outra requisição com a mesma chave) |

### Variáveis da CLI

| Variável | Padrão | Uso |
| --- | --- | --- |
| `TRANSCRIPTION_URL` | `http://127.0.0.1:8000` | URL do serviço |
| `TRANSCRIPTION_API_KEY` | — | Chave Bearer; nunca passe por argumento |
| `TRANSCRIPTION_HTTP_TIMEOUT` | 60 | Timeout de consultas/downloads em segundos |
| `TRANSCRIPTION_UPLOAD_TIMEOUT` | 3600 | Timeout do upload em segundos |
| `TRANSCRIPTION_WAIT_TIMEOUT` | 1800 | Espera padrão de `run`/`wait` |
| `TRANSCRIPTION_POLL_INTERVAL` | 3 | Intervalo de consulta em segundos |
| `TRANSCRIPTION_BLOCK_SECONDS` | 0 | Blocos padrão em segundos |

### Resultados e blocos

Cada `fetch` grava:

- `transcript.txt`: texto corrido.
- `transcript.jsonl`: um segmento por linha com `start`, `end` e `text`.
- `manifest.json`: metadados do servidor, caminhos absolutos locais, origem e lista de blocos.
- `blocks/block-NNNN.txt` e `blocks/block-NNNN.jsonl`: blocos com timestamps preservados, quando `--block-seconds` é maior que zero. O texto do bloco usa `[HH:MM:SS]` em cada linha.

O resumo é responsabilidade do agente. Isso permite usar o mesmo serviço com diferentes modelos e formatos de resumo.

## Skill de agente

A skill [`skills/wisp-transcribe/SKILL.md`](skills/wisp-transcribe/SKILL.md) descreve o objetivo, contrato essencial e retomada em inglês. Substitui a antiga pasta `transcrever-e-resumir`; atualize referências ou instalações locais. O comando `transcribe` permanece igual.

Use [`AGENTS.md`](AGENTS.md) como ponto de entrada. Se o agente não reconhecer AGENTS.md ou skills, peça explicitamente que leia os dois arquivos como Markdown. Basta ter terminal, acesso a arquivos e conexão HTTP com o serviço; não é necessário SDK ou integração com um provedor de LLM. Ao copiar a skill separadamente, mantenha acesso ao README e REVIEW indicados nas referências.

O [roteiro de sessão isolada](docs/agent-smoke-test.md) descreve preparação, prompt e critérios observáveis para testar com qualquer agente compatível.

## API

Todas as rotas de jobs e `/capabilities` exigem `Authorization: Bearer CHAVE`. Upload é o corpo binário do arquivo, **não multipart**. Exemplo PowerShell usando curl.exe:

```powershell
$env:TRANSCRIPTION_API_KEY = 'SUA-CHAVE'
curl.exe -H "Authorization: Bearer $env:TRANSCRIPTION_API_KEY" -H "Content-Type: application/octet-stream" --data-binary "@C:\audios\reuniao.mp4" "http://localhost:8000/jobs?language=pt"
curl.exe -H "Authorization: Bearer $env:TRANSCRIPTION_API_KEY" "http://localhost:8000/jobs/ID-RETORNADO"
curl.exe -H "Authorization: Bearer $env:TRANSCRIPTION_API_KEY" "http://localhost:8000/jobs/ID-RETORNADO/txt" -o transcricao.txt
curl.exe -H "Authorization: Bearer $env:TRANSCRIPTION_API_KEY" "http://localhost:8000/jobs/ID-RETORNADO/jsonl" -o transcricao.jsonl
curl.exe -H "Authorization: Bearer $env:TRANSCRIPTION_API_KEY" "http://localhost:8000/jobs/ID-RETORNADO/manifest" -o manifest.json
```

Rotas:

| Rota | Descrição |
| --- | --- |
| `GET /health` | Público; verifica o HTTP, não o modelo |
| `GET /capabilities` | Versão do contrato, modelo, formatos, limites, estados, códigos de erro e capacidades |
| `POST /jobs` | Envia arquivo; retorna 202 com ID ou 200 quando repete uma chave de idempotência |
| `GET /jobs/ID` | Estado da tarefa |
| `GET /jobs/ID/txt` | Texto; 409 enquanto não concluída |
| `GET /jobs/ID/jsonl` | Segmentos; 409 enquanto não concluída |
| `GET /jobs/ID/manifest` | Metadados da conclusão; gerado sob demanda se ausente |

`POST /jobs` aceita `language` (padrão pt; vazio para detectar) e `prompt` (contexto até 2000 caracteres) na query string. O cabeçalho opcional `Idempotency-Key` (1 a 200 caracteres ASCII imprimíveis) evita duplicar tarefas: uma repetição da mesma chave retorna a tarefa `queued`, `running` ou `completed` existente. Chaves de tarefas falhas ou interrompidas são liberadas para nova tentativa.

Estados: `uploading`, `queued`, `running`, `completed`, `failed`. Durante a execução, `processed_seconds` é o último timestamp processado; ao concluir, corresponde à duração detectada. A conclusão inclui `result` com idioma, probabilidade, durações, modelo, contagem de segmentos e opções.

Erros retornam `{"schema_version": 1, "code": "CODIGO", "error": "mensagem", "message": "mensagem"}`. O campo `error` continua sendo texto para compatibilidade com a interface web. Códigos estáveis: `UNAUTHORIZED`, `NOT_FOUND`, `JOB_NOT_FOUND`, `JOB_NOT_READY`, `JOB_INTERRUPTED`, `INVALID_REQUEST`, `INVALID_MEDIA`, `PAYLOAD_TOO_LARGE`, `QUEUE_FULL`, `UPLOAD_INTERRUPTED`, `IDEMPOTENCY_CONFLICT`, `TRANSCRIPTION_FAILED` e `INTERNAL_ERROR`. A CLI traduz esses códigos em códigos de saída.

O `manifest.json` do servidor inclui `schema_version`, `job_id`, `state`, `created_at`, `completed_at`, `language`, `requested_language`, `duration_seconds`, `model`, `options`, `segment_count`, `text_characters`, `files` e `sizes_bytes`. O `fetch` acrescenta `fetched_at`, `source` e caminhos absolutos locais.

## Configuração e operação

| Variável | Padrão | Uso |
| --- | --- | --- |
| TRANSCRIPTION_API_KEY | obrigatória, mínimo 24 caracteres | Chave ASCII aleatória |
| WHISPER_MODEL | small | Nome do modelo ou caminho de modelo local |
| CPU_THREADS | 8 | Threads de CPU |
| BATCH_SIZE | 8 | Reduza se faltar memória |
| MAX_UPLOAD_MB | 500 | Limite por arquivo |
| MAX_QUEUE | 10 | Tarefas aguardando, além da que está executando |
| CONNECTION_TIMEOUT | 300 | Timeout de socket por requisição, em segundos |
| DATA_DIR | ./data | Dados/modelos; no Docker /data |
| HOST / PORT | 127.0.0.1 / 8000 | Bind local; Docker usa 0.0.0.0 / 8000 |

O volume `transcription-data` guarda modelo, status, índice de idempotência e resultados. O arquivo original é removido ao terminar, inclusive em falhas. Transcrições e contexto ficam retidos até limpeza manual pelo administrador, com o serviço parado; não há política automática de expiração. Não execute duas instâncias sobre o mesmo diretório de dados. A fila é em memória: jobs pendentes ou em execução são marcados como falha (`JOB_INTERRUPTED`) após reinício e precisam de novo upload. Resultados concluídos continuam disponíveis, e o índice de idempotência de tarefas concluídas é preservado. Não há diarização de falantes, cancelamento, GPU ou garantia de precisão dos termos transcritos.

Dependências do servidor: faster-whisper 1.2.1 e PyAV 16.0.1 fixados para evitar o patch global de `av.open` presente no script antigo. O [decoder da versão original](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/audio.py) utiliza `metadata_errors`; por isso não se reutiliza o PyAV 19 do runtime solto. PyAV inclui bibliotecas FFmpeg, dispensando um executável FFmpeg separado.

## Revisão e validação

Na revisão de 2026-11-09 passaram: compilação Python, configuração Compose, build Docker, build e instalação isolada do pacote `wisp-transcribe-cli`, e integração CLI/API em localhost com transcritor simulado. Foram exercitados os seis comandos, detecção automática por idioma vazio, manifesto, blocos, repetição idempotente, erros JSON de argumentos, recusa de timeout NaN, mídia inválida e preservação do ID após falha de conexão. As verificações foram temporárias, sem adicionar testes versionados.

Ainda é necessário validar uma gravação falada representativa pelo pacote instalado; a integração simulada não avalia precisão ou desempenho do Whisper. Consulte [REVIEW.md](REVIEW.md) para a comparação com o plano, correções, pendências e compatibilidade. Nenhum serviço permanente foi iniciado ou publicado.
