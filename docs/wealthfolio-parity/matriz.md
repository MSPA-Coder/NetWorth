# Fase 0 — matriz de paridade Wealthfolio 3.8.0

Este documento é o contrato de QA para comparar o shell do NetWorth com a referência Wealthfolio `3.8.0` fixada no projeto `WealthfolioTeste`. A Fase 0 separa contratos estruturais de comparação visual: os testes Python não validam pixels, e o diff visual não deve esconder divergências de dados.

## Rotas, estados e viewports

| Tela | Rota | Estados mínimos | Viewports |
| --- | --- | --- | --- |
| Dashboard — Investments | `/dashboard/?tab=investments` | completo, parcial, vazio, privacidade | 1440, 1024, 768, 390 |
| Dashboard — Net Worth | `/dashboard/?tab=net-worth` | histórico, uma foto, sem FX, zero, parcial | 1440, 1024, 768, 390 |
| Dashboard — Spending | `/dashboard/?tab=spending` | entradas/saídas, sem fluxo, negativo, sem budget | 1440, 1024, 768, 390 |
| Insights — Summary | `/insights/?tab=summary` | dimensões, busca, filtro, sem classificação | 1440, 1024, 768, 390 |
| Insights — Performance | `/insights/?tab=performance` | série completa, insuficiente, retorno negativo | 1440, 1024, 768, 390 |
| Insights — Income | `/insights/?tab=income` | renda, vazio, múltiplas moedas | 1440, 1024, 768, 390 |
| Holdings/Accounts | `/holdings/`, `/accounts/` | lista, detalhe, sem histórico | 1440, 768, 390 |
| Spending derivado | `/spending/insights/`, `/spending/budget/` | cada etapa, sem categorias, sem plano | 1440, 768, 390 |
| Goals | `/goals/`, `/goals/new/` | sem meta, meta criada, valor zero | 1440, 768, 390 |

## Fixtures determinísticas

Usar o mesmo snapshot lógico nos dois sistemas: conta bancária BRL; posição de renda variável BRL; posição USD com taxa válida; fluxo positivo e negativo; renda/provento; histórico de três datas; fonte parcial/fora do ar; ausência deliberada de taxa FX; ausência deliberada de setor/região.

Os números devem ser comparados com os endpoints, nunca inferidos da imagem. Moedas sem taxa válida continuam separadas. “Não classificado” não pode ser preenchido pelo NetWorth a partir de ticker, instituição ou mercado.

## Checklist de interação

- Tabs preservam `tab` na URL e sobrevivem a reload/back.
- Período, data, busca, dimensão, direção e conta não desaparecem ao navegar.
- Ações “Ver tudo” levam à rota derivada correta.
- Dashboard não oferece gravação de dados financeiros.
- Metas, budget e alocação alvo informam que são preferências locais do NetWorth.
- Fonte parcial aparece antes dos números.
- Privacidade cobre cards, tabelas, tooltips e acessibilidade.
- Teclado alcança tabs, selects, botões, links e tabelas.
- Foco é visível e não há overflow horizontal acidental em 390 px.

## Screenshot diff

1. Construir a imagem Docker e executar `migrate`/`collectstatic`.
2. Popular os dois sistemas com os fixtures acima.
3. Capturar as mesmas rotas em 1440, 1024, 768 e 390 px, com mesma escala e data.
4. Mascarar somente timestamp, cursor e elementos deliberadamente dinâmicos.
5. Comparar com `pixelmatch` ou equivalente e baseline versionado.
6. Aceitar no máximo 1,5% de diferença global e 0,5% por região crítica. Divergência numérica ou de estado reprova mesmo que o diff visual passe.

## Gates de aceite

```text
G0 docker compose config --quiet
G1 suíte quality em PostgreSQL efêmero
G2 rotas autenticadas e 302 sem sessão
G3 contratos dashboard/insights JSON
G4 estados completo/parcial/vazio/sem FX
G5 screenshot diff nos viewports definidos
G6 console sem erro, foco/teclado e ausência de overflow
G7 confirmação de que CB/CRV não recebem escrita
```

O merge só é aceito quando G0–G4 passam automaticamente e G5–G7 têm relatório anexado à revisão. Qualquer falha de G2 ou G3 bloqueia a comparação visual.

## Coordenação e rollback

- Coordenador: fixtures, contratos, Docker e integração final.
- Agente de dados: adapters/serializers e testes de contrato.
- Agente Dashboard: Dashboard e seus assets.
- Agente Insights: Insights e telas derivadas.
- Agente QA: somente `tests/test_wealthfolio_parity*.py`, este diretório e `tests/visual/**`.

No workspace compartilhado, cada agente edita apenas sua propriedade. Rebuild Docker, collectstatic e baselines são executados somente pelo coordenador após checkpoint de integração. Em caso de regressão, reverter o checkpoint do agente responsável, sem `git reset --hard` e sem remover dados locais dos demais agentes.
