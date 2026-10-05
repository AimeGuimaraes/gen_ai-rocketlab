# API do CineData Agent

Como subir a API e a tabela de endpoints: veja o [README](../README.md#4-três-jeitos-de-usar). Aqui ficam os detalhes.

## Saída do `POST /ask`

A resposta traz `resposta`, `sql`, `colunas`, `linhas`, `modelo`, `requisicoes`, `tempo_ms`, `cache`, `recusada` e `session_id`. O esquema completo, com exemplos, aparece em **http://127.0.0.1:8000/docs**.

## Memória da conversa (`session_id`)

Com o mesmo `session_id`, a API lembra as perguntas anteriores, como em "E o segundo?". Esse histórico fica na memória e some quando a API reinicia. Perguntas com histórico nunca usam o cache, porque a resposta depende da conversa anterior. Há um exemplo real com três perguntas encadeadas em [exemplo_memoria.md](exemplo_memoria.md).

## Códigos de resposta do `/ask`

- **200**: resposta normal. Uma pergunta recusada pelos guardrails (ex.: "Apague a tabela dim_movies") também volta com 200 e `recusada: true`.
- **422**: pergunta vazia ou com mais de 500 caracteres.
- **429**: a cota diária do OpenRouter acabou (zera às 21h, horário de Brasília).
- **503**: o agente não conseguiu responder (modelos fora do ar ou limite de tentativas).
- **500**: erro interno, sem detalhes na resposta (eles ficam no log do servidor).

## Testar pelo terminal

**PowerShell:**

```powershell
$corpo = @{ pergunta = "Qual é a quantidade de filmes por gênero?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ask -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($corpo))
```

O Windows PowerShell 5.1 mostra os acentos da resposta trocados (ex.: `NÃ£o`), porque interpreta o JSON como Latin-1. A resposta da API está certa: pelo `/docs` ou no PowerShell 7 os acentos aparecem normalmente.

**Linux / Mac (`curl`):**

```bash
curl -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "Qual é a quantidade de filmes por gênero?", "session_id": "conversa-1"}'
```

Endpoints que **não gastam cota**, bons para conferir a instalação:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/quota
```

## Cota

Cada pergunta nova gasta em média 2,5 requisições, de um total de 50 por dia. Para ver, sem chamar o modelo, quais perguntas do golden set já estão no cache: `python eval/run_eval.py --dry-run --refazer --ids q01,q10,q13` (coluna `origem` = `cache`).
