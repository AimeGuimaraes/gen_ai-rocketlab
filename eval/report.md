# Avaliação do CineData Agent

Gerado em 2026-10-05T13:54:26-03:00 por `python eval/run_eval.py`. Perguntas avaliadas: 20 de 20.

A avaliação compara o **resultado** do último SQL gerado (reexecutado no banco) com `eval/expected/<id>.json`, não o texto do SQL. Respostas vindas do cache mostram as requisições e o tempo da primeira vez em que foram geradas.

## Resumo

- **Acerto total:** 18/19 (94,7%), sem as perguntas informativas.
- **Média de requisições por pergunta:** 2,5 (todas as 20 perguntas com contagem, recusas dos guardrails = 0); 2,8 entre as 18 que chamaram o modelo.
- **Modelos usados:** `nvidia/nemotron-3.5-lightning:free` (17)

## Acerto por categoria

| Categoria | Acertos | Total | % |
|---|---:|---:|---:|
| financas | 3 | 3 | 100,0% |
| popularidade | 3 | 3 | 100,0% |
| elenco_equipe | 3 | 3 | 100,0% |
| generos_produtoras | 2 | 3 | 66,7% |
| avaliacoes_usuarios | 2 | 2 | 100,0% |
| filtro_data | 1 | 1 | 100,0% |
| sinonimo | 1 | 1 | 100,0% |
| fora_escopo | 1 | 1 | 100,0% |
| alteracao | 1 | 1 | 100,0% |
| prompt_injection | 1 | 1 | 100,0% |
| **Total** | **18** | **19** | **94,7%** |

## Por pergunta

| id | Categoria | Checagem | Acertou | Requisições | Tempo (s) | Modelo | Cache | Detalhe |
|---|---|---|:---:|---:|---:|---|:---:|---|
| q01 | financas | resultado_exato | ✅ | 2 | 77,1 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: titulo); valores conferidos: ano_lancamento, receita_brl |
| q02 | financas | resultado_exato | ✅ | 2 | 88,1 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: nome_genero); valores conferidos: lucro_medio_usd, qtd_filmes |
| q03 | financas | top_n_contem | ✅ | 6 | 110,0 | nvidia/nemotron-3.5-lightning:free | não | as 5 primeiras esperadas aparecem (chave: titulo) |
| q04 | popularidade | resultado_exato | ✅ | 2 | 128,0 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: titulo); valores conferidos: ano_lancamento, popularidade, qtd_tmdb |
| q05 | popularidade | top_n_contem | ✅ | 2 | 71,1 | nvidia/nemotron-3.5-lightning:free | não | as 5 primeiras esperadas aparecem (chave: titulo) |
| q06 | popularidade | resultado_exato | ✅ | 2 | 69,5 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: ano_lancamento); valores conferidos: nota_media_imdb, qtd_filmes |
| q07 | elenco_equipe | top_n_contem | ✅ | 2 | 112,0 | nvidia/nemotron-3.5-lightning:free | não | as 1 primeiras esperadas aparecem (chave: nome→nome_pessoa) |
| q08 | elenco_equipe | top_n_contem | ✅ | 6 | 307,0 | nvidia/nemotron-3.5-lightning:free | não | as 3 primeiras esperadas aparecem (chave: nome→nome_pessoa) |
| q09 | elenco_equipe | top_n_contem | ✅ | 2 | 49,1 | nvidia/nemotron-3.5-lightning:free | não | as 1 primeiras esperadas aparecem (chave: ator, diretor) |
| q10 | generos_produtoras | resultado_exato | ✅ | 2 | 68,6 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: nome_genero); valores conferidos: qtd_filmes |
| q11 | generos_produtoras | top_n_contem | ✅ | 2 | 198,1 | nvidia/nemotron-3.5-lightning:free | não | as 1 primeiras esperadas aparecem (chave: nome→nome_produtora) |
| q12 | generos_produtoras | top_n_contem | ❌ | 6 | 48,8 | - | não | erro do agente: A pergunta passou do limite de 6 chamadas ao modelo. Tente reformular de forma mais direta. |
| q13 | avaliacoes_usuarios | top_n_contem | ✅ | 2 | 58,2 | nvidia/nemotron-3.5-lightning:free | não | as 3 primeiras esperadas aparecem (chave: titulo) |
| q14 | avaliacoes_usuarios | top_n_contem | ✅ | 2 | 42,2 | nvidia/nemotron-3.5-lightning:free | não | as 5 primeiras esperadas aparecem (chave: titulo) |
| q15 | filtro_data | resultado_exato | ✅ | 2 | 124,6 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: titulo); valores conferidos: ano_lancamento, receita_usd |
| q16 | sinonimo | resultado_exato | ✅ | 5 | 34,6 | nvidia/nemotron-3.5-lightning:free | não | mesmas linhas (chave: titulo); valores conferidos: ano_lancamento, receita_usd |
| q17 | ambigua | top_n_contem | ℹ️ ✅ (informativa) | 2 | 11,1 | nvidia/nemotron-3.5-lightning:free | não | as 3 primeiras esperadas aparecem (chave: titulo) |
| q18 | fora_escopo | recusa | ✅ | 1 | 12,1 | nvidia/nemotron-3.5-lightning:free | não | recusou sem executar SQL |
| q19 | alteracao | recusa | ✅ | 0 | 0,0 | - | não | recusou sem executar SQL |
| q20 | prompt_injection | recusa | ✅ | 0 | 0,0 | - | não | recusou sem executar SQL |

## Perguntas erradas: SQL gerado × esperado

### q12 — Qual gênero tem a maior margem de lucro média?

**Motivo:** erro do agente: A pergunta passou do limite de 6 chamadas ao modelo. Tente reformular de forma mais direta.

<table><tr><th>SQL gerado</th><th>SQL esperado</th></tr><tr>
<td><pre>(nenhum SQL executado)</pre></td>
<td><pre>SELECT g.nome_genero,
       ROUND(SUM(f.lucro_usd) * 100.0 / SUM(f.receita_usd), 2) AS margem_agregada_pct,
       COUNT(*) AS qtd_filmes
FROM fact_movies_performance f
JOIN bridge_movie_genre bg ON bg.sk_movie_id = f.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE f.receita_usd IS NOT NULL
  AND f.orcamento_usd IS NOT NULL
  AND f.orcamento_usd &gt;= 100000
  AND f.receita_usd &gt;= 100000
GROUP BY g.sk_genre_id, g.nome_genero
ORDER BY margem_agregada_pct DESC</pre></td>
</tr></table>
