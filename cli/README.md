# Wisp Transcribe CLI

Cliente Python para o serviço [Wisp Transcribe](https://github.com/thepokenik/wisp-transcribe).

Instale com `python -m pip install ./cli` a partir da raiz do repositório. Configure `TRANSCRIPTION_URL` e `TRANSCRIPTION_API_KEY`, depois execute `transcribe doctor --json`.

Exemplo: `transcribe run video.mp4 --output ./resultado --block-seconds 600 --json`.

Consulte o README principal para comandos, códigos de saída e retomada. O resumo é produzido pelo agente a partir dos resultados.
