# Notas da avaliação

Registro das rodadas de `python eval/run_eval.py` e dos ajustes feitos entre elas. O relatório
completo da última rodada fica em [report.md](report.md).

## Rodada 1 — 04/10/2026, ~21h20 (q01–q20)

- Resultados esperados de q01, q04, q06, q07, q08, q16 e q17 regerados antes da rodada (o
  `date('now')` do SQLite usa UTC e já era 05/10): **nenhum resultado mudou**.
- **Acerto: 16/19 (84,2%)**, sem a q17 (informativa). Média de 1,9 requisição por pergunta (2,1
  entre as que chamaram o modelo). Todas respondidas por `nvidia/nemotron-3.5-lightning:free`.
- As 3 erradas trouxeram as linhas certas, mas faltou uma coluna exigida pelo comparador:

| id | Faltou | Diagnóstico |
|---|---|---|
| q01 | `receita_brl` (o valor da receita) | Erro do agente: a métrica da pergunta deveria aparecer. |
| q02 | `qtd_filmes` (contagem por gênero) | Erro do agente: a regra P2 pede a contagem. |
| q04 | `qtd_tmdb` | Critério rígido demais: a coluna só é usada como filtro (`qtd_tmdb >= 100`). |

## Ajuste 1 — comparador: colunas usadas só como filtro ficam opcionais

**Critério ajustado depois da 1ª rodada.** No `resultado_exato`, as colunas numéricas de métrica
eram todas obrigatórias. Mas algumas colunas do `sql_esperado` só aparecem para conferência de um
filtro (ex.: `qtd_tmdb` na q04, que pergunta "os 5 filmes mais populares"); exigir que o agente as
mostre reprova uma resposta correta.

Regra geral (função `colunas_so_filtro` em `run_eval.py`, aplicada a todas as perguntas): uma
coluna do SELECT esperado é opcional quando é uma coluna direta da base que aparece no
WHERE/HAVING, mas **não** aparece no ORDER BY nem dentro de outra coluna calculada do SELECT.
Colunas agregadas ou calculadas (AVG, SUM, margem) e as de ordenação continuam obrigatórias. Se o
agente trouxer a coluna, ela é conferida normalmente.

No golden set atual, a regra só afeta a q04 (`qtd_tmdb`). A q09 também é apontada (`ator`), mas
essa coluna é a chave e não passa pela checagem numérica.

- A q04 foi reavaliada com a resposta guardada no cache (mesmo prompt, **0 requisições**): passou
  a acertar.

## Ajuste 2 — prompt: incluir a métrica e a contagem no SELECT

Instrução acrescentada às regras do prompt de sistema (`src/cinedata_agent/prompts.py`):

> No SELECT, inclua sempre a coluna da métrica usada para ordenar ou agregar (ex.: receita, lucro
> médio) e, ao agrupar, a contagem de filmes de cada grupo (COUNT(*) AS qtd_filmes).

Como o prompt mudou, o cache das respostas antigas deixou de valer.

## Rodada 2 — 04/10/2026, ~21h45 (`--refazer --ids q01,q02,q10,q12,q16`)

Cota antes: 13 requisições restantes.

| id | Antes (prompt antigo) | Depois (prompt novo) | Requisições |
|---|---|---|---:|
| q01 | ❌ sem `receita_brl` | ✅ traz `receita_brl` | 2 |
| q02 | ❌ sem `qtd_filmes` | ✅ traz `qtd_filmes` | 3 |
| q10 | ✅ | ✅ (sem piora) | 2 |
| q12 | ✅ | **não concluída**: erro 502 do provedor (Nvidia) | ~6 |
| q16 | ✅ | não rodada (a rodada parou na q12) | 0 |

- Na q12, o agente com o prompt novo executou 5 consultas (a 1ª já trazia a resposta certa, com
  `LIMIT 1`; depois repetiu com `LIMIT 10` e passou a conferir só o gênero Action) até o provedor
  devolver 502. **Sinal de atenção:** o comportamento foi pior que na rodada 1 (3 requisições).
  Ainda não dá para dizer se é efeito do prompt ou instabilidade do modelo; é preciso refazer a
  q12 e a q16.
- O 502 derrubou o script, que não tratava esse erro. Correção: `run_eval.py` agora para de forma
  limpa em qualquer erro de API do modelo (`ModelAPIError`), sem salvar a pergunta, e gera o relatório.
- A q01–q10 gastaram 2–3 requisições cada; a q12 gastou 6 (5 consultas + a falha 502).

## Ajuste 3 — agente: erro 5xx do provedor troca de modelo

O 502 da q12 ("Provider returned error", com `provider_name`) não acionava o fallback: só 429 com
provedor e 404 trocavam de modelo, e a pergunta falhava sem tentar o modelo reserva. Agora
`classificar_erro` (em `src/cinedata_agent/agent.py`) trata **5xx com provedor** como "trocar de
modelo". 5xx sem provedor continua como erro comum. Coberto por testes em `tests/test_agent.py`.

## Tentativa de rodada 3 (q12 e q16) — não executada

O `check_quota` mostrou **1 requisição restante** (49 de 50 usadas). O contador do OpenRouter
estava atrasado na rodada 2 (mostrava 9). Com menos de 3 requisições o script não começa uma
pergunta, então a rodada não foi feita e nada foi gasto. A cota só zera às 21h de 05/10, depois do
prazo de entrega (18h): **q12 e q16 ficam sem reavaliação com o prompt novo**.

## Comportamento observado com o prompt novo (q12)

Na única execução da q12 com o prompt novo, o agente **fez consultas extras depois de já ter a
resposta**: a 1ª consulta (`ORDER BY margem DESC LIMIT 1`) já trazia o gênero certo, mas ele
repetiu com `LIMIT 10`, consultou linhas soltas da tabela fato, recalculou só o gênero Action e
conferiu as somas, até o provedor falhar com 502 na 6ª requisição. Com o prompt antigo, a mesma
pergunta usou 3 requisições. Como foi uma única execução, não dá para separar o efeito do prompt
("inclua a métrica e a contagem") da variação normal do modelo; fica como ponto a observar.

## Versão do prompt usada em cada pergunta

| Versão do prompt | Perguntas | Rodada |
|---|---|---|
| Antigo (sem a regra de métrica e contagem) | q03–q09, q11–q20 | Rodada 1 (q04 reavaliada pelo cache, mesma resposta) |
| Novo (com a regra de métrica e contagem) | q01, q02, q10 | Rodada 2 |
| Novo, sem resultado | q12 (502), q16 (não rodou) | Rodada 2; seguem valendo os resultados da rodada 1 |

q19 e q20 são recusadas pelos guardrails antes do modelo, então não dependem do prompt.

## Resultado atual: 16/19 → 19/19

| Passo | Acerto | O que mudou |
|---|---|---|
| Rodada 1 | 16/19 (84,2%) | — |
| + Ajuste 1 (comparador) | 17/19 (89,5%) | q04 passa (coluna só de filtro opcional). |
| + Ajuste 2 (prompt) e rodada 2 | **19/19 (100%)** | q01 e q02 passam com o prompt novo. |

**Ressalva:** o 19/19 mistura versões do prompt (ver a tabela acima). Só q01, q02 e q10 foram
avaliadas com o prompt novo. Uma rodada completa com o prompt novo fica para quando houver cota.
