# Perguntas do enunciado

As 14 perguntas que o agente deve responder, por categoria.
Sinônimos: "receita" = "faturamento" = "bilheteria".

As regras de cálculo de cada pergunta (P1–P14) estão em `docs/schema_docs.yaml`, seção `regras_de_negocio`.

## Bilheteria e finanças
1. Top 10 filmes com maior receita em R$
2. Lucro médio por gênero, considerando apenas filmes com receita informada
3. Filmes com maior margem de lucro, entre os que possuem receita e orçamento informados

## Popularidade e engajamento
4. Os 5 filmes mais populares
5. Filmes com maior divergência entre a nota TMDB e a nota IMDb
6. Nota média IMDb por ano de lançamento

## Elenco e equipe
7. Ator com mais participações em filmes lançados nos últimos 5 anos
8. Diretores com maior nota média (mínimo de 5 filmes)
9. Dupla ator-diretor que mais trabalhou junta

## Gêneros e produtoras
10. Quantidade de filmes por gênero
11. Produtora com maior lucro total
12. Gênero com maior margem de lucro média

## Avaliações dos usuários
13. Filmes mais avaliados pelos usuários
14. Filmes em que a nota média dos usuários mais diverge da nota IMDb
