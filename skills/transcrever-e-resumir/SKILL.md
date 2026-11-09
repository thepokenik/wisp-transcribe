---
name: transcrever-e-resumir
description: Transcrever áudio ou vídeo pela CLI `transcribe`, aguardar/retomar a tarefa e resumir os arquivos resultantes com timestamps, sem interação manual.
---

# Transcrever e resumir

Use esta skill quando precisar transcrever uma gravação e produzir um resumo confiável para outra pessoa ou para outro agente. O modelo Whisper fica no servidor; a CLI apenas envia o arquivo, acompanha a tarefa e baixa os resultados.

## Pré-requisitos

- `transcribe` instalado e disponível no `PATH`.
- `TRANSCRIPTION_API_KEY` definida no ambiente. Nunca passe a chave por argumento, log ou arquivo de resultado.
- `TRANSCRIPTION_URL` aponta para o serviço (padrão `http://127.0.0.1:8000`).

Confirme antes de começar:

```powershell
transcribe doctor --json
```

Só prossiga se o processo terminar com código `0` e `ok` for `true`. Se `doctor` falhar, informe o código e a mensagem; não tente adivinhar a URL ou a chave.

## Fluxo principal

1. Escolha um diretório de saída exclusivo para a gravação, por exemplo `./resultado-reuniao-2026-05-10`.
2. Execute e interprete o stdout como JSON. O progresso aparece no stderr e pode ser exibido ao usuário.

```powershell
transcribe run "C:\videos\reuniao.mp4" --language pt --output ".\resultado-reuniao" --json
```

3. Em caso de sucesso (`exit 0`, `state: completed`), leia `manifest.json`, depois os blocos (`blocks/block-*.txt` ou `.jsonl`) e, por último, `transcript.txt`. Use os blocos para resumos incrementais de gravações longas.
4. Produza o resumo com referências de tempo no formato `[HH:MM:SS]`, extraídas do JSONL ou do texto dos blocos. Não invente falas, decisões, nomes ou números.

Para dividir gravações longas em trechos de 10 minutos:

```powershell
transcribe run "C:\videos\reuniao.mp4" --language pt --output ".\resultado-reuniao" --block-seconds 600 --json
```

## Retomada e falhas

`run` pode terminar sem concluir; o servidor continua trabalhando. Use o `job_id` retornado:

```powershell
transcribe wait JOB_ID --timeout 1800 --json
transcribe fetch JOB_ID --output ".\resultado-reuniao" --json
```

| Código | Significado | Ação do agente |
| --- | --- | --- |
| `0` | Sucesso | Ler os arquivos e resumir |
| `2` | Configuração, argumento ou arquivo inválido | Corrigir caminho/opções; não reenviar igual |
| `3` | Autenticação recusada | Pedir chave/configuração ao responsável |
| `4` | Serviço indisponível, fila cheia ou contrato incompatível | Aguardar e tentar depois; preservar o `job_id` se houver |
| `5` | Timeout de espera | Não reenviar: usar `wait`/`fetch` com o `job_id` |
| `6` | Falha de transcrição ou mídia inválida | Informar `error.code`; verificar o arquivo; não resumir |
| `7` | Tarefa não encontrada | Conferir o ID ou reenviar |
| `8` | Conflito de idempotência | Aguardar alguns segundos e repetir a mesma operação |

O JSON de erro segue o formato `{"ok": false, "error": {"code": "...", "message": "..."}}`. Preserve `job_id` e `state` quando aparecerem; eles permitem retomar.

## Envio e espera separados

Para gravações grandes ou quando houver outras tarefas em paralelo:

```powershell
transcribe submit "C:\videos\reuniao.mp4" --language pt --json
transcribe status JOB_ID --json
transcribe wait JOB_ID --timeout 1800 --json
transcribe fetch JOB_ID --output ".\resultado-reuniao" --json
```

`submit` deriva uma chave de idempotência do conteúdo do arquivo e das opções. Repetir o mesmo envio devolve a tarefa existente (`replayed: true`) em vez de duplicá-la. Use `--idempotency-key` para identificar um envio entre processos e `--no-idempotency` somente quando duplicar for intencional.

## Leitura dos resultados

O `manifest.json` local contém `language`, `requested_language`, `duration_seconds`, `model`, `options`, `segment_count`, `text_characters`, `files` (caminhos absolutos) e `blocks`. Cada bloco informa `start`, `end`, `segment_count`, `text` e `segments`.

- `transcript.txt`: texto corrido, bom para leitura e busca.
- `transcript.jsonl`: uma linha por segmento com `start`, `end` e `text`; use para citar tempos.
- `blocks/`: blocos com timestamps no texto e JSONL próprio; use para resumo em etapas.
- `manifest.json`: metadados e caminhos para auditoria.

Se não houver blocos (`blocks: []`), resuma o texto completo. Não crie uma segunda transcrição só para dividir o resultado.

## Como resumir

Entregue, nesta ordem, apenas o que a transcrição sustentar:

1. **Resumo executivo** em até cinco linhas.
2. **Contexto** (assunto, participantes citados, duração).
3. **Decisões** com timestamp e quem decidiu, quando explicitado.
4. **Pendências e próximos passos** com responsável e prazo somente se citados.
5. **Dúvidas em aberto** e informações ausentes ou ambíguas.
6. **Trechos de referência** com `[HH:MM:SS]`.

Diferencie claramente o que foi dito do que é inferência. Se a transcrição estiver incompleta, ambígua ou sem uma informação, escreva “não consta na transcrição” em vez de completar. Não atribua falas a pessoas que não foram identificadas no texto.

## Boas práticas

- Use um diretório de saída por gravação para não sobrescrever resultados anteriores.
- Não apague arquivos do servidor nem tente limpar `DATA_DIR` por conta própria.
- Não inclua a chave de API em comandos, resumos ou logs compartilhados.
- Em caso de vídeo sem áudio, o serviço retorna `INVALID_MEDIA`; peça outro arquivo.
- Antes de entregar, confira que `manifest.json` e `transcript.jsonl` existem e que o resumo cita apenas trechos presentes neles.
