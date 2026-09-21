# Relatório de paridade — shell Wealthfolio do NetWorth

Data da execução: **21/09/2026**
Escopo: contratos determinísticos da superfície autenticada, sem rede e sem
alterar dados do Controle Bancário ou do Controle de Renda Variável.

## Como a matriz funciona

`tests/test_wealthfolio_parity_matrix.py` monta envelopes normalizados em
memória para três estados observáveis:

- completo: as duas fontes respondem e publicam uma conta/posição;
- parcial: o Controle Bancário responde e a Renda Variável falha, portanto o
  shell não pode apresentar o patrimônio como completo;
- vazio: nenhuma linha publicada, com estado vazio/indisponível explícito.

Os cenários não dependem de dados reais, de uma API externa ou de uma tabela
operacional local de cópia. A fixture representa o contrato que o leitor já
validou; a responsabilidade da matriz é verificar a projeção para a interface.

## Gates executáveis

Os comandos devem ser executados na raiz do repositório:

```powershell
docker compose --env-file .env.docker -f compose.yaml config --quiet
docker compose --progress quiet --env-file .env.docker -f compose.yaml --profile quality run --build --rm quality pytest tests/test_wealthfolio_parity_matrix.py -q
docker compose --env-file .env.docker -f compose.yaml --profile quality run --build --rm quality
```

Resultado desta execução:

| Gate | Evidência | Estado |
| --- | --- | --- |
| G0 configuração Compose | `config --quiet` | **passou** |
| G1 matriz de paridade | `19 passed` no PostgreSQL efêmero | **passou** |
| G2 autenticação e redirects legados | `tests/test_tela.py` e matriz de views | coberto; confirmar na suíte completa |
| G3 contratos JSON | Dashboard e Insights na matriz | **passou** |
| G4 completo/parcial/vazio/read-only | testes parametrizados da matriz | **passou** |
| G5 screenshot diff | ainda sem baseline versionada e sem captura automatizada | **pendente** |
| G6 console, foco, teclado e overflow | não substituído por teste de status HTTP | **pendente** |
| G7 ausência de escrita em CB/CRV | GET/405 e não-mutação do snapshot em memória | coberto no shell; confirmar com teste de transporte no lote E |

A execução integrada da suíte completa (`quality` sem selecionar um arquivo)
passou após a integração dos adapters, transport, atividades v3 e templates:
**207 testes passaram**. O comando também executa Ruff, verificações de dependências e
`collectstatic` dentro da imagem de qualidade.

## O que a matriz garante

- cada aba do Dashboard tem uma única aba ativa, conserva período/data na URL e
  publica a cobertura das fontes;
- Summary, Performance e Income recebem o mesmo contrato de filtros
  somente leitura, incluindo dimensão, busca, ordenação e direção;
- Holdings, Accounts, Activities, gastos, metas, Assistente e Configurações
  expõem o tipo próprio da rota e rejeitam `POST` com `405 Allow: GET`;
- uma fonte parcial continua marcada como parcial em Dashboard, Insights,
  rotas derivadas e JSON;
- o estado vazio não cria números nem tabelas alternativas;
- os endpoints JSON serializam valores monetários como texto e mantêm a moeda;
- GETs de consulta não mutam o snapshot publicado recebido pelo shell.

## Lacunas que continuam reais

Passar essa matriz não significa que a interface já seja pixel a pixel igual ao
Wealthfolio. Ainda precisam de implementação/validação coordenada:

1. screenshot diff nos viewports de `docs/wealthfolio-parity/matriz.md`, com
   máscara somente para dados dinâmicos;
2. validação de foco, teclado, tooltip, carregamento/erro e console no navegador;
3. eventos, séries de renda e performance ainda não publicados pelas fontes;
4. IDs opacos/deep links publicados pelas fontes em todas as rotas derivadas;
5. substituição das telas derivadas provisórias por componentes próprios quando
   a referência exigir interação ainda indisponível;
6. validação de produção dos filtros e deep links v3 com dados reais, sem
   transformar a consulta em uma escrita ou em uma cópia local.

Uma rota só deve ser marcada como concluída depois dos gates automáticos e da
evidência visual/interativa correspondente. `HTTP 200` sozinho não é critério
de paridade.
