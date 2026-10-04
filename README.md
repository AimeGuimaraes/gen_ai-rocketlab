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

## Roadmap

- Definir a stack tecnológica
- Implementar as ferramentas de esquema e execução
- Configurar os guardrails de segurança (somente leitura)
- Adicionar exemplos de perguntas e testes
- Configurar instruções de execução e instalação

## Testes

*Em desenvolvimento.*

---

