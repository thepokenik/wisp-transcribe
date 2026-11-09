# Revisão do Wisp Transcribe — 2026-11-09

O plano de uso agêntico está implementado para um serviço interno com um worker CPU. O resumo é produzido pelo agente consumidor; não há chamada a LLM no servidor.

| Etapa do plano | Situação |
| --- | --- |
| CLI run, submit, status, wait, fetch, doctor | Implementada |
| JSON versionado, progresso no stderr e códigos de saída | Implementados; argumentos inválidos também retornam JSON com --json |
| Retomada por ID, timeout sem cancelar tarefa | Implementada; falhas HTTP na espera e download de run preservam ID |
| API com capabilities, erros estáveis e idempotência | Implementada; índice persistido e reserva durante upload |
| TXT, JSONL, manifesto e blocos com timestamps | Implementados |
| Pacote Python e skill para resumo pelo agente | Implementados |
| Validação de fala representativa | Pendente |

## Correções desta revisão

- Nome público, identidade de capabilities, título web, pacote CLI e documentação alinhados a Wisp Transcribe. O comando `transcribe` e as variáveis `TRANSCRIPTION_*` foram preservados.
- `language=` agora preserva o valor vazio, permitindo detecção automática; o manifesto registra idioma solicitado como nulo nessa situação.
- Erros de argumentos retornam JSON quando solicitado; tempos negativos, NaN e infinito são recusados antes do upload.
- Erros HTTP após o envio em run/wait preservam o ID e instrução de retomada.
- Idempotência é consultada novamente após reservar a chave, fechando uma janela de duplicação entre uploads concorrentes.
- Limpeza de blocos restrita aos arquivos TXT/JSONL com nomes gerados, em vez de qualquer `block-*`.

## Pendências

1. **Validação de fala real:** executar uma gravação representativa pelo pacote instalado e conferir idioma, termos técnicos, timestamps e resumo. Os testes de integração desta revisão simulam o transcritor; não medem precisão ou desempenho do Whisper.
2. **Robustez de retomada:** Ctrl+C ainda retorna 130 com orientação no stderr, sem JSON com ID em todos os caminhos. Falha local ao gravar arquivos também pode retornar erro genérico sem ID. O limite de espera pode ser excedido pela duração de uma consulta HTTP em andamento.
3. **Idempotência:** uma chave explícita reutilizada com outro arquivo retorna a tarefa anterior; não há validação de fingerprint no servidor. A chave automática da CLI inclui arquivo, idioma e contexto, mas não o modelo configurado no servidor. O índice não tem expiração.
4. **Blocos:** divisão por duração não garante limite de tokens; um segmento longo não é cortado. O diretório de saída deve ser exclusivo por gravação; fetch pode sobrescrever resultados existentes.
5. **Operação:** retenção/limpeza automática, limites de uploads simultâneos, fila persistente, cancelamento e recuperação após reinício não foram implementados. Tarefas interrompidas exigem reenvio.
6. **Compartilhamento externo:** autenticação por usuário, isolamento de tarefas, rate limit e proxy HTTPS não fazem parte do serviço. A chave atual dá acesso a todos os resultados; usar rede privada/VPN ou proxy configurado pelo administrador.
7. **Entrega contínua:** não há CI, publicação em PyPI, imagem em registry ou testes automatizados versionados. O repositório contém o código e setup para build local. Dependências transitivas e imagem base ainda não têm lock/digest fixo.

## Compatibilidade e implantação

Servidor e CLI usam schema_version=1. Rotas TXT/JSONL e chave Bearer existentes são preservadas. Instale o servidor atualizado antes da CLI que exige capabilities/manifest. O nome do serviço Compose e volume lógico `transcription-data` foram preservados, mas o Compose deriva o projeto da pasta: ao mover uma instalação de transcription-service para wisp-transcribe, use `docker compose -p transcription-service ...` para continuar usando o volume antigo, ou migre os dados explicitamente com o serviço parado. Nenhum volume foi migrado nesta revisão.

Publicar o código no GitHub não inicia nem hospeda a API. A operação continua seguindo o README.
