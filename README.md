# CineData Agent — Text-to-SQL com GenAI

> **Projeto em desenvolvimento** — Atividade GenAI do Rocket Lab 2026 (Visagio). 

Agente de IA que permite a usuários **não técnicos** fazer perguntas em linguagem natural sobre o catálogo de filmes da **CineData Analytics**. O agente traduz a pergunta em SQL, consulta a camada **Gold** do Data Lakehouse (SQLite) em tempo real, **somente para leitura**, e responde em português com a tabela de resultado e o SQL usado.

---

## Arquitetura

Fluxo planejado: pergunta -> guardrails -> agente -> ferramentas (esquema, amostras, execução de SQL) -> SQLite somente leitura -> resposta com tabela e SQL.

---

## Instalação

Requer Python 3.11+. No Windows (PowerShell), na raiz do projeto:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # dependências com as versões testadas
pip install -e .                  # instala o próprio pacote cinedata_agent (modo editável)
copy .env.example .env            # depois preencha LLM_API_KEY com sua chave do OpenRouter
```

Teste a instalação (não chama o modelo):

```powershell
python -m cinedata_agent --help
```

---

## API (FastAPI)

Suba a API na raiz do projeto, com o ambiente virtual ativo:

```powershell
uvicorn api.main:app --reload
```

Ao subir, a API carrega o banco em memória (cerca de 3 s). Espere aparecer `Application startup complete` e abra **http://127.0.0.1:8000/docs** no navegador. Para testar um endpoint, clique nele, depois em **Try it out** e em **Execute**.

| Endpoint | O que faz | Gasta cota? |
|---|---|---|
| `POST /ask` | Responde uma pergunta: `{"pergunta": "...", "session_id": "conversa-1", "usar_cache": true}` | Sim, exceto respostas do cache e recusas |
| `GET /health` | Confere se o banco responde e se a `LLM_API_KEY` está configurada | Não |
| `GET /schema` | Tabelas, colunas e descrições do dicionário de dados | Não |
| `GET /quota` | Cota restante do dia no OpenRouter | Não |

A saída do `/ask` traz `resposta`, `sql`, `colunas`, `linhas`, `modelo`, `requisicoes`, `tempo_ms`, `cache` e `recusada`. Com o mesmo `session_id`, a API lembra as perguntas anteriores, como em "E o segundo?". Esse histórico fica na memória e some quando a API reinicia. Há um exemplo real com três perguntas encadeadas em [docs/exemplo_memoria.md](docs/exemplo_memoria.md).

Códigos de resposta do `/ask`:

- **200**: resposta normal. Uma pergunta recusada pelos guardrails (ex.: "Apague a tabela dim_movies") também volta com 200 e `recusada: true`.
- **422**: pergunta vazia ou com mais de 500 caracteres.
- **429**: a cota diária do OpenRouter acabou (zera às 21h, horário de Brasília).
- **503**: o agente não conseguiu responder (modelos fora do ar ou limite de tentativas).
- **500**: erro interno, sem detalhes na resposta (eles ficam no log do servidor).

> **Cota:** a conta gratuita permite 50 requisições por dia, e cada pergunta nova gasta cerca de 2. Para ver, sem chamar o modelo, quais perguntas do golden set já estão no cache: `python eval/run_eval.py --dry-run --refazer --ids q01,q10,q13` (coluna `origem` = `cache`).

Também dá para testar pelo PowerShell:

```powershell
$corpo = @{ pergunta = "Qual é a quantidade de filmes por gênero?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ask -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($corpo))
```

O Windows PowerShell 5.1 mostra os acentos da resposta trocados (ex.: `NÃ£o`), porque interpreta o JSON como Latin-1. A resposta da API está certa: pelo `/docs` ou no PowerShell 7 os acentos aparecem normalmente.

---

## Roadmap

- Definir a stack tecnológica
- Implementar as ferramentas de esquema e execução
- Configurar os guardrails de segurança (somente leitura)
- Adicionar exemplos de perguntas e testes
- Configurar instruções de execução e instalação

## Testes

*Em desenvolvimento.*

---

