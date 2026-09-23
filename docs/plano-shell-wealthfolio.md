# Plano de implementação — Wealthfolio como shell do NetWorth

## 0. Revisão de rumo — 23/09/2026

Esta seção prevalece sobre o restante do documento onde houver conflito. As
seções seguintes continuam valendo para a camada de dados, os contratos e os
limites de escrita; o que muda é **o que conta como pronto** na interface.

### O que mudou

**O Wealthfolio passa de gabarito a referência de design.** O NetWorth não é
uma instalação do Wealthfolio: é uma reimplementação em Django do visual de
um app React. Perseguir paridade pixel a pixel com ele é caro, não termina, e
depende de uma instância externa -- que se perdeu uma vez e parou o trabalho.

Continua valendo do Wealthfolio: a organização (sidebar, abas Investimentos /
Patrimônio líquido / Gastos, Holdings, Insights, Accounts, Activities), a
linguagem de cartões e gráficos, a ocultação de valores, e os conceitos de
tela. Deixa de valer: diferença máxima de 2 px, diff visual abaixo de 1% e a
proibição de acrescentar cards ou telas que a referência não tem.

O critério passa a ser: **consistente, legível, correto sobre cobertura e
moeda, e melhor que a referência onde os dados das fontes permitirem**. A
instância de referência serve para consultar e comparar ideias, não como
portão de aceite.

**O diferencial entra no plano.** O Controle Bancário tem lançamentos futuros,
recorrência e fluxo de caixa, que o Wealthfolio não tem. A tela de patrimônio
projetado (saldo de hoje, mais lançamentos futuros do CB, mais proventos
anunciados do CRV) passa a ser prioridade, à frente das rotas que só existem
por paridade.

**Metas (Goals) e Assistente continuam no horizonte**, fora do escopo
obrigatório desta etapa. Eles não são cortados: as rotas continuam existindo
com estado indisponível, e a arquitetura já comporta os dois:

- Metas seriam o primeiro dado que **pertence ao próprio NetWorth** (valor
  alvo, prazo, contas ou posições associadas), o que a seção 1 já prevê;
  o progresso sai das mesmas leituras somente-leitura das fontes;
- o Assistente seria uma conversa com um modelo de linguagem que consulta os
  mesmos view-models, sem acesso de escrita às fontes.

Antes de desenhar qualquer um dos dois, o usuário vai experimentar como eles
funcionam no Wealthfolio de referência.

### Direção de longo prazo

1. Agora: **experiência única com dados federados** -- login único,
   navegação comum entre os três aplicativos e visual base no SharedAuth. CB e
   CRV continuam donos dos dados e das telas de lançamento; o NetWorth é a
   visão sintética e leva de volta ao analítico por deep links.
2. Talvez depois: **um aplicativo e um banco**. CB e NetWorth já são Django, e
   seriam os primeiros a conviver no mesmo projeto; o CRV (Flask) seria o
   último. Antes dessa fusão é preciso decidir a licença: código combinado com
   o que deriva do Wealthfolio fica sob AGPL-3.0.

Uso atual: pessoal. No futuro pode incluir a família, o que pesa a favor do
login único e das permissões por titular que o CB já tem.

### Ordem de trabalho a partir daqui

| Fase | Entrega |
| --- | --- |
| 0. Referência | Instância 3.8.0 restaurada em `referencia-wealthfolio/`; NOTICE aponta ao código-fonte público. **Feito em 23/09/2026.** |
| 1. Meta revisada | Esta seção. **Feito em 23/09/2026.** |
| 2. Limpeza | Um só caminho de dados: `leitor.consolidar_v2` → `Consolidado` → `consolidado/contexto.py` → view-models → templates. O caminho paralelo (`adapters`/`normalize`/`contracts`/`SnapshotDTO`), que só os testes usavam, foi retirado; `views.py` e os templates `patrimonio.html`/`historico.html`, sem rota, também. **Feito em 23/09/2026.** |
| 3. Diferencial | Tela de fluxo de caixa e patrimônio projetado, sobre os lançamentos futuros do CB (v3) e os proventos do CRV. Desenho aprovado pelo usuário antes do código. |
| 4. Experiência única | Login único, barra comum para trocar de aplicativo e visual base no SharedAuth; os três atualizados para a mesma versão do SharedAuth. |
| 5. Horizonte | Metas e Assistente; lançamento rápido pelo NetWorth gravando pela API da fonte; fusão CB + NetWorth. Cada um por decisão própria. |

Os lotes B, C, D e F da seção 7 continuam como lista de telas a construir,
mas o aceite de cada uma segue o critério desta seção, não o da seção 10.

## 1. Objetivo

O NetWorth deve oferecer a mesma interface, navegação e experiência de consulta
do Wealthfolio, usando como fontes de verdade:

- o Controle Bancário para contas, saldos, receitas, despesas, categorias e
  demais fluxos bancários;
- o Controle de Renda Variável para carteiras, posições, cotações, proventos,
  ganhos realizados e performance;
- o próprio NetWorth apenas para dados que realmente lhe pertençam, como taxas
  de câmbio, fotografias consolidadas e funcionalidades que forem
  deliberadamente migradas no futuro.

O Wealthfolio é o **shell visual e comportamental**. O NetWorth substitui a
camada de persistência e consulta por projeções somente leitura sobre os dois
sistemas de origem.

```text
Shell visual/comportamental Wealthfolio 3.8.0
                         |
Camada de compatibilidade do NetWorth
                         |
DTOs/view-models somente leitura e contratos versionados
                  /                 \
     Controle Bancário       Controle Renda Variável
```

## 2. Baseline obrigatório

A referência fica congelada em:

- Wealthfolio `3.8.0`;
- revisão `8f6f9898d30e84d7215e01d3d06cd65e02c9ab1b`;
- imagem local com digest
  `sha256:3c6f117828949204029c2b4a391f039e62987b4e091139e11b04e6764b5f6866`;
- instância de referência local em `http://127.0.0.1:8088`, definida em
  `referencia-wealthfolio/compose.yaml` (volume externo `wealthfolio-data`).

Não usar a tag móvel `latest` como baseline durante a implementação.

O usuário decidiu explicitamente adotar o Wealthfolio sob a licença
AGPL-3.0. A reutilização ou modificação de código-fonte e assets do baseline é
permitida dentro das obrigações da licença; o código-fonte correspondente e a
atribuição devem permanecer disponíveis conforme a AGPL-3.0.

O produto continua se chamando **NetWorth** e é um projeto distinto do
Wealthfolio. O NetWorth não deve usar o nome, logo, ícones de marca ou outros
assets de branding do Wealthfolio como identidade própria, nem sugerir
afiliação, endosso ou distribuição oficial. Componentes reutilizados devem ser
identificados na documentação e em `NOTICE.md`.

## 3. Definição de fidelidade

> **Substituída pela seção 0 (23/09/2026).** O texto abaixo registra o
> critério original e não é mais portão de aceite.

O resultado não será uma interface "inspirada" no Wealthfolio. Para cada rota,
deve preservar:

- hierarquia e posição dos elementos;
- navegação lateral, abas, controles e estados ativos;
- tipografia, espaçamentos, cores, bordas, ícones e densidade;
- ordem e composição de cards, gráficos, listas e tabelas;
- comportamento de períodos, filtros, busca, ordenação e drill-down;
- loading, vazio, parcial, indisponível, erro e retry;
- ocultação de valores, navegação por teclado, foco e responsividade;
- query parameters, histórico do navegador e recarregamento da página.

Valores e disponibilidade de dados podem diferir. A estrutura da tela não.
Quando uma informação não for publicada pelas fontes, o shell deve mostrar o
estado vazio ou indisponível equivalente ao Wealthfolio; não deve inventar um
card, uma métrica ou uma composição alternativa.

## 4. Limites de dados e significado de "views"

Não serão criadas no NetWorth tabelas que dupliquem contas, posições,
lançamentos, categorias, cotações ou carteiras dos sistemas de origem.

A camada equivalente às tabelas internas do Wealthfolio será formada por:

1. contratos HTTP autenticados e somente leitura, publicados pelas fontes;
2. validação e normalização em DTOs imutáveis no NetWorth;
3. view-models específicos para cada tela;
4. fotografias agregadas somente quando necessárias para histórico.

Os contratos `patrimonio/v2` já existentes serão a base inicial. Views SQL
podem ser criadas **dentro do sistema proprietário** para estabilizar ou
acelerar a publicação, mas o NetWorth não deve acessar diretamente tabelas,
schemas ou bancos externos. Isso mantém migrations, autorização e regras de
negócio sob responsabilidade da fonte.

Não usar neste estágio:

- banco compartilhado;
- `postgres_fdw` ou `dblink`;
- Django models `managed=False` apontando para bancos externos;
- importação periódica de registros operacionais;
- chamadas de escrita do NetWorth para as fontes.

Se futuramente o HTTP se mostrar insuficiente, uma réplica read-only e views
SQL versionadas poderão ser avaliadas por uma decisão arquitetural separada.

## 5. Contrato canônico do NetWorth

Templates não poderão consumir dicionários crus de `Consolidado` nem formatos
específicos de CB ou CRV. A compatibilidade deve ficar em módulos próprios, por
exemplo:

```text
consolidado/wealthfolio_compat/
    contracts.py
    transport.py
    controle_bancario.py
    renda_variavel.py
    domain.py
    capabilities.py
    view_models/
```

DTOs mínimos:

- `SourceSnapshot`;
- `Coverage`;
- `Money(amount: Decimal, currency: str)`;
- `Account`;
- `Holding`;
- `Activity`;
- `Income`;
- `RealizedGain`;
- `PerformancePoint`;
- `ChartSeries`;
- `DeepLink`;
- `CapabilitySet`.

Cada item deve carregar ID opaco e prefixado pela fonte, origem, titular,
instituição, moeda, data de referência, qualidade e link profundo publicado
pela própria fonte. IDs numéricos de sistemas diferentes nunca serão tratados
como globais.

Invariantes:

- dinheiro trafega como texto no JSON e vira `Decimal` no adapter;
- toda quantia possui moeda;
- moedas não são somadas sem taxa válida para a data da fotografia;
- `as_of`, início/fim do período, data do preço, geração e captura são datas
  distintas;
- ausência de informação não vira zero;
- fonte ausente ou defasada é informada antes dos totais;
- uma fotografia incompleta nunca é persistida como completa;
- tokens permanecem no backend, em arquivos de segredo, e não aparecem em URL,
  HTML, JavaScript ou logs.

## 6. Lacunas conhecidas das fontes

O Controle Bancário v2 já publica foto e fluxos agregados por dia, moeda e
natureza, mas ainda não publica todos os lançamentos individuais, descrições e
categorias necessários à paridade integral da tela de Gastos.

O Controle de Renda Variável v2 já publica posições, carteiras, custos,
resultado não realizado, qualidade de cotação, performance, ganhos realizados
e renda. Posições simuladas, opções e casos sem cotação possuem omissões
explícitas que devem continuar visíveis.

As lacunas serão fechadas por contratos de leitura adicionais e versionados,
com paginação e ordenação determinística. Não serão preenchidas por inferência
ou por dados fictícios no NetWorth. Candidatos:

```text
GET /patrimonio/v3/activities
GET /patrimonio/v3/categories
GET /patrimonio/v3/metadata
```

## 7. Fases de implementação

### Fase 0 — Congelamento e inventário

Responsável: coordenador.

Entregas:

- matriz rota × estado × viewport;
- screenshots da referência em desktop e mobile;
- inventário de controles, ações, modais e query parameters;
- mapa de cada componente para sua fonte de dados;
- fixtures determinísticas equivalentes no WealthfolioTeste e nas fontes;
- inventário das alterações atuais do protótipo, separando o que pode ser
  reaproveitado do que deve ser removido ou isolado;
- decisão de licenciamento registrada.

Saída: nenhum agente precisa interpretar livremente o que "parece" o
Wealthfolio.

### Fase 1 — Camada de dados read-only

Responsável: Luna Dados.

Entregas:

- separar transporte HTTP, validação, adapters, domínio e consolidação;
- consumir `patrimonio/v2` de ambas as fontes;
- converter os dois contratos para os DTOs canônicos;
- publicar `coverage`, `quality`, `capabilities` e deep links;
- criar fixtures de contrato para sucesso, parcial, vazio, stale, erro,
  múltiplas moedas e ausência de câmbio;
- documentar exatamente quais campos ainda faltam para cada tela.

Saída: frontend e templates só recebem view-models estáveis.

### Fase 2 — Shell global

Responsável: Luna Shell.

Entregas:

- sidebar, marca, ícones, navegação e estados ativos fiéis;
- comportamento responsivo e recolhimento no mobile;
- tema, tipografia, tokens visuais, foco e ARIA;
- cabeçalhos, menus, dropdowns e ocultação de valores;
- remoção, nas rotas canônicas, do cabeçalho e navegação próprios do NetWorth;
- os endereços legados `/patrimonio/` e `/patrimonio/historico/` redirecionam
  para o shell canônico e não renderizam uma interface paralela.

Rotas mínimas do shell:

```text
/dashboard/
/insights/
/holdings/
/accounts/
/activities/
/goals/
/spending/insights/
/spending/budget/
/assistant/
/settings/
```

Saída: screenshots do shell diferem apenas em marca, idioma e dados aprovados.

### Fase 3 — Dashboard

Responsáveis: Luna Shell com contrato congelado pela Luna Dados.

Ordem obrigatória:

1. Investments;
2. Net Worth;
3. Spending.

Para cada aba:

- reproduzir a composição real antes de conectar dados;
- conectar valor principal, variação, gráfico, períodos, listas e cards aos
  view-models;
- implementar tooltips, cliques, filtros, links "ver tudo" e retry;
- preservar a aba e o período na URL e no histórico do navegador;
- validar completo, parcial, vazio, loading e erro;
- não acrescentar cards ou ações inexistentes na referência.

Spending somente será considerado completo quando a fonte publicar os detalhes
necessários. Até lá, deve usar o estado indisponível fiel ao Wealthfolio nas
áreas sem dados.

### Fase 4 — Insights

Responsáveis: Luna Shell com contrato congelado pela Luna Dados.

Ordem obrigatória:

1. Summary;
2. Performance;
3. Income.

Entregas:

- abas, seletores, busca, ordenação e filtros;
- gráficos, legendas, tooltips e tabelas;
- agrupamentos e drill-down;
- performance, renda e ganhos conectados ao CRV;
- estados de histórico insuficiente e classificação ausente;
- cobertura e moeda coerentes com o Dashboard.

Métricas ausentes permanecem ausentes. O NetWorth não inferirá setor, região,
categoria, performance ou renda que a fonte não tenha publicado.

### Fase 5 — Rotas derivadas

Responsável: Luna Shell, uma rota por lote.

Ordem:

1. Holdings e detalhe de holding;
2. Accounts e detalhe de conta;
3. Activities, busca e filtros;
4. Spending Insights;
5. Budget;
6. Goals e detalhe;
7. Assistant;
8. Settings.

A página genérica atual não será usada como substituta de telas distintas. Cada
rota terá a composição do Wealthfolio e reutilizará apenas componentes que a
referência também compartilha.

Ações de criação, edição, importação e categorização permanecem desabilitadas ou
em estado fiel de indisponibilidade até que a funcionalidade seja
deliberadamente transferida para o NetWorth.

### Fase 6 — Contratos faltantes nas fontes

Responsável: Luna Dados, em lotes independentes por repositório.

Entregas iniciais:

- atividades detalhadas e paginadas do Controle Bancário;
- categorias com IDs opacos e natureza explícita;
- metadados necessários a Accounts e Spending;
- atividades e eventos de investimento quando exigidos pelas telas derivadas;
- testes de compatibilidade e segurança nos três projetos.

Cada alteração de fonte será aditiva. Os contratos v1 e v2 continuarão
funcionando.

### Fase 7 — Validação final e documentação

Responsáveis: Luna QA e coordenador.

Entregas:

- relatório de paridade por rota e estado;
- screenshots lado a lado;
- lista explícita das funcionalidades ainda indisponíveis;
- confirmação de ausência de escritas nas fontes;
- documentação da arquitetura e dos contratos;
- comandos Docker reproduzíveis para build, testes e smoke.

### Estado auditado e ordem de retomada — 2026-09-21

O destino deste plano continua sendo **todo o sistema Wealthfolio como a única
interface do NetWorth**. As fases abaixo não são alternativas nem escopo
opcional: elas descrevem os lotes necessários para chegar a esse destino sem
voltar a criar uma segunda interface de patrimônio.

Estado do repositório na revisão `8768aa1`:

| Área | Estado atual | O que falta para aceite |
| --- | --- | --- |
| Interface pública | A raiz abre `/dashboard/`; `/patrimonio/` e `/patrimonio/historico/` redirecionam para o shell | Manter esse redirecionamento; nenhuma rota pode voltar a renderizar os templates retirados. |
| Shell global | Sidebar, topbar, privacidade e navegação inicial existem | Comparação visual desktop/mobile, foco por teclado, menus, tooltips e todos os estados da referência. |
| Camada de compatibilidade | `models.py`, `normalize.py` e `view_models.py` validam DTOs e produzem view-models | Tornar DTOs/adapters/transport o caminho de produção; hoje as views ainda partem de `Consolidado` diretamente. |
| Dashboard | As três abas, períodos, gráficos, cards e endpoints JSON iniciais existem | Fidelidade visual/comportamental, tooltips, retry/loading/erro, links e todos os estados da matriz. |
| Insights | Summary, Performance e Income existem em estrutura inicial | Filtros, busca, ordenação, drill-down, séries completas e fidelidade visual por aba. |
| Holdings e Accounts | Lista e detalhe iniciais existem | IDs opacos de fonte, paginação, filtros, detalhes e composição específica da referência. |
| Activities e Spending | Fluxos agregados são exibidos; atividades individuais são declaradas indisponíveis | Contratos v3 para lançamentos/categorias e telas específicas de atividades, categorias e análise de gastos. |
| Goals, Budget, Assistant e Settings | Rotas e estados indisponíveis existem | Páginas próprias equivalentes à referência; operações só serão ativadas após migração deliberada. |
| QA visual | Testes de modelo, rota e contrato existem | Fixtures iguais nas fontes, screenshots lado a lado, diff, geometria, mobile e acessibilidade. |

#### Lotes obrigatórios a partir do estado atual

> **Redefinido em 23/09/2026 (seção 0, fase 2).** O caminho que ficou é o
> que a produção já usava, o `Consolidado` do `leitor`, e não o `SnapshotDTO`;
> o `leitor` já tinha a validação e os testes das regras de cobertura e moeda.

**Lote A — consolidar o caminho de dados.** Separar o uso interno de
`Consolidado` da superfície Wealthfolio: adapters HTTP normalizam cada fonte em
`SnapshotDTO`; uma composição read-only produz `WealthfolioVM`; templates e
endpoints JSON consomem exclusivamente esse view-model. Cobrir sucesso,
parcial, vazio, defasado, erro e câmbio ausente. Nenhum template recebe payload
da fonte ou dicionário cru.

**Lote B — fechar Dashboard.** Validar Investments, Net Worth e Spending contra
a referência congelada nos viewports da matriz. Cada aba precisa de estrutura,
ações, estados e navegação equivalentes. Spending usa os agregados v2 onde eles
forem suficientes e usa a forma visual de indisponibilidade onde ainda não
forem.

**Lote C — fechar Insights.** Entregar Summary, Performance e Income usando os
mesmos contratos e cobertura do Dashboard. Busca, filtros, ordenação,
dimensão, drill-down, gráficos e URL pertencem ao lote; métricas não publicadas
continuam explicitamente indisponíveis.

**Lote D — substituir a página genérica pelas rotas do sistema.** Cada rota
abaixo terá template, view-model e estados próprios, seguindo a referência e
sem reutilizar uma página genérica como substituta:

1. `/holdings/` e `/holdings/<id>/`;
2. `/accounts/` e `/accounts/<id>/`;
3. `/activities/`;
4. `/spending/insights/` e `/spending/budget/`;
5. `/goals/` e criação/detalhe de meta;
6. `/assistant/`;
7. `/settings/`.

Cada rota nasce completa visualmente, mesmo quando sua fonte ainda não publica
os dados ou autoriza a ação correspondente. Nesse caso, mostra o estado de
indisponibilidade equivalente ao Wealthfolio, nunca uma tabela alternativa do
NetWorth.

**Lote E — estender os contratos nas fontes.** Implementar aditivamente os
contratos v3 de CB e CRV na ordem exigida pelos lotes B–D: atividades
detalhadas/paginadas, categorias, metadados de contas, eventos de investimento,
séries de renda e performance. O NetWorth só consome HTTP autenticado,
somente leitura, com IDs prefixados pela fonte e deep links publicados por ela.

**Lote F — aceite de paridade.** Construir fixtures determinísticas equivalentes
nos três sistemas, executar os gates G0–G7 e registrar screenshot diff e
comparação de geometria para cada rota/estado/viewport. Nenhuma rota é
considerada concluída só por renderizar ou por ter teste de backend.

#### Regras de execução contínua

- Não reintroduzir a antiga interface de patrimônio, templates ou navegação
  como fallback visual.
- Não tratar a página genérica atual como entrega final de uma rota derivada.
- Não criar dados operacionais locais para preencher lacunas das fontes.
- Não ativar escrita em CB ou CRV a partir do NetWorth sem uma migração
  deliberada e um contrato próprio.
- Cada lote encerra com a suíte Docker completa, smoke autenticado, revisão de
  cobertura/moeda/data e checkpoint visual antes do lote seguinte.

#### Progresso do lote E — contratos de atividades (2026-09-21)

Os dois publicadores agora têm alterações aditivas preparadas em PRs, sem
alterar v1/v2 nem aceitar escritas:

- Controle Bancário: `/patrimonio/v3/activities`, `/categories` e `/metadata`,
  com filtros de período/conta/categoria/status/natureza, paginação estável,
  IDs opacos e deep links.
- Controle de Renda Variável: os mesmos recursos, publicando transações
  encerradas e proventos de carteiras reais, com IDs opacos sem colisão e
  deep links locais.

O NetWorth ainda precisa consumir esses endpoints na rota `/activities/` e
manter o estado parcial quando apenas uma fonte responder. Até esse consumo
ser integrado e validado, a tela continua explicitamente indisponível para
lançamentos individuais; nenhum dado é copiado para tabelas locais.

### Progresso do lote E — analytics v3 e consumo no shell (2026-09-21)

O contrato foi estendido sem alterar v1/v2:

- o Controle Bancário publica no metadata as capacidades efetivamente
  existentes (`fluxos`, `atividades`, `categorias`, `escrita=false`) e declara
  que não possui renda, performance ou eventos de carteira;
- o Controle de Renda Variável publica `GET /patrimonio/v3/income`,
  `/performance` e `/events`, sempre autenticados, paginados quando aplicável,
  com escopo do proprietário, IDs opacos, deep links e `Cache-Control:
  no-store`;
- o NetWorth normaliza esses envelopes em DTOs read-only, usa renda e TWR
  publicados na aba Insights e mantém o estado parcial quando uma origem não
  oferece o recurso. A composição não converte moedas sem taxa publicada nem
  cria lançamentos locais.

As suítes dos publicadores passaram em Docker (CB: 534; CRV: 458) e a suíte
integrada do NetWorth passou com 213 testes. A validação autenticada pública
também foi executada com o usuário de teste autorizado; o detalhe de contas
resolve grupos, instituições e o filtro da conta publicada.

### Progresso do lote F — fechamento estrutural do Dashboard (2026-09-21)

O shell recebeu os blocos que faltavam na composição das abas do Dashboard,
sem criar dados locais nem habilitar escrita:

- Investments agora mantém a coluna de Posições e exibe o bloco de Metas no
  mesmo arranjo lateral da referência; a ação permanece somente leitura;
- Spending agora exibe Aprofundar, Orçamento mensal e Eventos, com ações
  indisponíveis explicitamente identificadas quando as fontes não publicam a
  capacidade;
- links novos e existentes preservam período, data e filtros na URL.

O lote passou a suíte Docker integrada (213 testes), `git diff --check` e foi
publicado no VPS na revisão `de14533`. A comparação pixel a pixel, console,
foco, teclado e tooltips continuam gates de aceite separados; eles não são
declarados concluídos por este lote estrutural.

### Correção do drill-down de contas (2026-09-21)

O detalhe autenticado deixou de aceitar somente o nome da instituição. URLs
derivadas de grupos/titulares (como `Esposita`) agora resolvem a árvore
publicada, e `?account=Conta%2001` filtra as linhas da conta escolhida (como
Mercado Pago — Conta 01). O comportamento é somente leitura e tem regressão
automatizada na suíte de views.


## 8. Divisão entre agentes Luna

No máximo três agentes trabalharão simultaneamente, além do coordenador.
Um quarto agente poderá ser usado depois que um dos três encerrar.

| Papel | Propriedade exclusiva | Proibido alterar |
| --- | --- | --- |
| Luna Dados | `consolidado/wealthfolio_compat/`, adapters, DTOs e testes de contrato; contratos publicados nas fontes em lotes separados | templates e CSS |
| Luna Shell | componentes e estilos do shell, Dashboard, Insights e uma rota derivada por lote | adapters, models, migrations e contratos das fontes |
| Luna QA | fixtures visuais, automação do navegador, baselines, testes de interação e relatório | código de produção |
| Coordenador | arquivos compartilhados, URLs, settings, base template, Compose, integração e correções transversais | — |

Regras contra corrida:

1. um arquivo tem exatamente um escritor durante cada lote;
2. o coordenador publica antes de cada lote a lista exata de arquivos permitidos;
3. agentes não criam branches, não fazem reset/checkout e não sobrescrevem
   mudanças alheias no workspace compartilhado;
4. agentes não editam `base.html`, URLs, settings, Compose ou navegação global;
5. contratos/view-models são congelados antes do frontend consumir o lote;
6. rebuild completo, migrations, `collectstatic` e testes visuais integrados são
   executados somente pelo coordenador;
7. conflito ou necessidade fora da lista de arquivos interrompe o lote e volta
   ao coordenador;
8. integração ocorre em checkpoints pequenos: dados, shell, uma aba ou uma rota
   por vez;
9. uma falha reverte somente o checkpoint do lote, nunca o workspace inteiro;
10. nenhum agente usa comandos destrutivos ou formatação global.

Cada entrega de agente deve informar:

- arquivos alterados;
- comportamento implementado;
- testes executados;
- limitações conhecidas;
- confirmação de que não escreveu nas fontes.

## 9. Matriz mínima de validação

| Tela | Estados | Viewports |
| --- | --- | --- |
| Dashboard / Investments | completo, parcial, vazio, moedas múltiplas, privacidade | 1440, 1024, 768, 390 |
| Dashboard / Net Worth | histórico, uma foto, sem FX, fonte ausente, zero | 1440, 1024, 768, 390 |
| Dashboard / Spending | normal, vazio, sem detalhe, sem orçamento, valores negativos | 1440, 1024, 768, 390 |
| Insights / Summary | dimensões, busca, ordenação, filtro, não classificado | 1440, 1024, 768, 390 |
| Insights / Performance | completo, insuficiente, negativo, drawdown | 1440, 1024, 768, 390 |
| Insights / Income | normal, vazio, moedas múltiplas, fontes agrupadas | 1440, 1024, 768, 390 |
| Rotas derivadas | normal, vazio, parcial, erro e loading | 1440, 768, 390 |

Verificações obrigatórias:

- sem overflow horizontal não intencional;
- sem card ou ação cortada;
- tabelas usam scroll interno quando necessário;
- foco visível e navegação por teclado;
- valores ocultos não vazam em tooltip, DOM acessível ou texto auxiliar;
- voltar, avançar e recarregar preservam aba, período e filtros;
- nenhum erro JavaScript no console;
- nenhum request duplicado desnecessário às fontes;
- deep links chegam ao sistema proprietário correto;
- nenhuma ação do NetWorth altera CB ou CRV.

## 10. Comparação visual

> **Não é mais portão de aceite (seção 0, 23/09/2026).** A comparação com a
> referência continua útil para consulta; os limites numéricos abaixo não se
> aplicam.

As capturas devem usar a mesma versão, fixture, data, viewport, escala, DPR,
locale, tema, aba e filtros.

Usar duas verificações complementares:

1. screenshot diff, mascarando apenas textos e gráficos cujos dados
   legitimamente diferem;
2. comparação de geometria DOM para os principais âncoras da interface.

Critérios:

- diferença estrutural máxima de 2 px nos principais alinhamentos;
- diferença visual global inferior a 1% depois das máscaras aprovadas;
- nenhuma diferença não aprovada na presença, ordem ou posição de controles;
- gráficos devem ter o mesmo contêiner, eixos, legenda e interação, ainda que os
  pontos sejam diferentes;
- todas as diferenças restantes são registradas no relatório de paridade.

Baselines não serão atualizados automaticamente. Uma mudança de baseline exige
aprovação do coordenador após comparação com o Wealthfolio de referência.

## 11. Gates do coordenador

Um lote só avança quando:

1. o diff respeita a propriedade de arquivos;
2. contratos anteriores continuam compatíveis;
3. lint e testes do lote passam em Docker;
4. a suíte completa do NetWorth passa;
5. build, migrations e `collectstatic --clear` passam;
6. rotas autenticadas respondem 200 e rotas anônimas redirecionam para login;
7. smoke dos endpoints JSON passa;
8. a rota foi revisada em desktop e mobile (foco, teclado, overflow), sem
   exigir paridade com a referência;
9. cobertura parcial, moeda e datas permanecem corretas;
10. não houve escrita nem duplicação de dados das fontes.

Comando oficial de qualidade:

```powershell
docker compose --env-file .env.docker -f compose.yaml `
  --profile quality run --build --rm quality
```

O coordenador também validará os projetos de origem, pelos respectivos serviços
Docker, sempre que um contrato publicado for alterado.

## 12. Critério final de sucesso

O projeto estará concluído quando:

1. Dashboard, Insights e rotas derivadas seguirem a organização do
   Wealthfolio como referência de design, com liberdade para melhorá-la
   (seção 0);
2. a visão de fluxo de caixa e patrimônio projetado, que o Wealthfolio não
   tem, estiver disponível;
3. os dados exibidos vierem dos dois sistemas proprietários por contratos
   read-only e view-models;
4. NetWorth não duplicar contas, posições, lançamentos ou categorias;
5. dados ausentes, parciais, antigos ou sem câmbio nunca parecerem completos;
6. funcionalidades não migradas permanecerem visualmente coerentes e
   explicitamente indisponíveis;
7. novas funcionalidades puderem ser transferidas gradualmente para o
   NetWorth sem refazer o shell.

## 13. Atribuição, identidade e código-fonte correspondente

O shell reutilizado tem como baseline o Wealthfolio 3.8.0, revisão
`8f6f9898d30e84d7215e01d3d06cd65e02c9ab1b`, distribuído sob AGPL-3.0. A
atribuição curta e o inventário geral das alterações ficam em `NOTICE.md`.

O código-fonte correspondente à versão reutilizada/modificada deve ser
mantido junto da distribuição ou disponibilizado por uma oferta válida de
fonte, conforme a AGPL-3.0. O código-fonte correspondente é o repositório
público `https://github.com/wealthfolio/wealthfolio`, tag `v3.8.0`, na revisão
acima (a antiga cópia local em `CodexTemp/` se perdeu). O código do NetWorth é
público em `https://github.com/MSPA-Coder/NetWorth`.

Nenhum logo ou asset de marca do Wealthfolio deve ser apresentado como marca
do NetWorth. O NetWorth deve manter nome, identidade visual própria e um aviso
claro de que não é afiliado, patrocinado ou endossado pelo Wealthfolio.
