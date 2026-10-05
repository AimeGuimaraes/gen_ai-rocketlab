# Exemplo: memória da conversa pela API

Três perguntas seguidas para `POST /ask`, todas com o mesmo `session_id`. A 2ª e a 3ª não citam os filmes: o agente só sabe quais são porque a API repassa o histórico da 1ª.

Teste real feito em 05/10/2026 com o modelo `nvidia/nemotron-3.5-lightning:free`. Ao todo foram 4 requisições: a 1ª pergunta veio do cache e as outras duas gastaram 2 cada.

```json
{"pergunta": "Quais os 5 filmes de terror com maior faturamento?", "session_id": "teste-memoria"}
```

## 1. "Quais os 5 filmes de terror com maior faturamento?"

**Resposta (resumida):** os 5 filmes de Terror (Horror) com maior receita, só entre os já lançados e com receita informada.

| Título (ano) | Receita (US$) |
|---|---|
| It (2017) | 701.800.000 |
| The Meg (2018) | 530.517.320 |
| It Chapter Two (2019) | 473.122.525 |
| The Mummy (2017) | 409.231.607 |
| Meg 2: The Trench (2023) | 384.056.482 |

**SQL:** o agente se corrigiu sozinho. A primeira consulta filtrou `'Terror'`, que não existe na base. Ele listou os gêneros e refez a consulta com `'Horror'`, a grafia da base. Abaixo, a consulta final:

```sql
SELECT m.titulo, m.ano_lancamento, f.receita_usd
FROM dim_movies m
JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
JOIN bridge_movie_genre bg ON bg.sk_movie_id = m.sk_movie_id
JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
WHERE g.nome_genero = 'Horror'
  AND f.receita_usd IS NOT NULL
  AND m.data_lancamento <= date('now')
ORDER BY f.receita_usd DESC
LIMIT 5
```

Requisições: 0 (resposta do cache).

## 2. "E qual deles teve o maior orçamento?"

**Resposta (resumida):** entre os cinco, **The Meg (2018)** teve o maior orçamento, de US$ 150 milhões.

| Título (ano) | Orçamento (US$) |
|---|---|
| The Meg (2018) | 150.000.000 |
| Meg 2: The Trench (2023) | 129.000.000 |
| The Mummy (2017) | 125.000.000 |
| It Chapter Two (2019) | 79.000.000 |
| It (2017) | 40.000.000 |

**SQL:** "deles" virou a lista dos 5 títulos da resposta anterior.

```sql
SELECT m.titulo, f.orcamento_usd
FROM dim_movies m
JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
WHERE m.titulo IN ('It','The Meg','It Chapter Two','The Mummy','Meg 2: The Trench')
AND f.orcamento_usd IS NOT NULL
ORDER BY f.orcamento_usd DESC
LIMIT 5
```

Requisições: 2.

## 3. "Mostre esses mesmos valores em reais."

**Resposta (resumida):** os mesmos orçamentos em R$, pela coluna `orcamento_brl` (câmbio histórico do lançamento, regra G4).

| Título (ano) | Orçamento (R$) |
|---|---|
| Meg 2: The Trench (2023) | 620.193.300 |
| The Meg (2018) | 570.360.000 |
| The Mummy (2017) | 410.137.500 |
| It Chapter Two (2019) | 325.819.700 |
| It (2017) | 124.508.000 |

Em reais a ordem muda: Meg 2 (2023) passa The Meg (2018), porque o dólar estava mais caro em 2023.

**SQL:** "esses mesmos valores" virou os orçamentos dos mesmos 5 filmes, agora em R$.

```sql
SELECT m.titulo, f.orcamento_brl
FROM dim_movies m
JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
WHERE m.titulo IN ('The Meg','Meg 2: The Trench','The Mummy','It Chapter Two','It')
AND f.orcamento_brl IS NOT NULL
ORDER BY f.orcamento_brl DESC
LIMIT 100
```

Requisições: 2.

## Observações

- A 2ª e a 3ª consultas filtram só pelo título, sem o ano. Neste caso o resultado está certo, porque nenhum dos 5 títulos tem homônimo na base (conferido direto no banco). Com títulos repetidos, porém, filtrar também pelo ano ou pelo `id_filme` seria mais seguro (regra G8).
- Mesmo vindo do cache, a 1ª resposta traz o histórico completo da conversa. Por isso a memória funciona também a partir de uma resposta guardada.
- Perguntas com histórico nunca usam o cache, porque a resposta depende da conversa anterior.
