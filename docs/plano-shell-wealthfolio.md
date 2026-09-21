# Plano de implementação — Wealthfolio como shell do NetWorth

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
- instância de referência local em `http://127.0.0.1:8088`.

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
8. comparação visual da rota passa em desktop e mobile;
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

1. o usuário reconhecer a interface como o Wealthfolio, e não como uma
   interpretação do seu design;
2. Dashboard, Insights e rotas derivadas tiverem a mesma estrutura e
   comportamento da versão fixada;
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
fonte, conforme a AGPL-3.0. Para o desenvolvimento local, a cópia auditada é
`CodexTemp/wealthfolio-src-3.8.0`, na revisão acima; a URL/repositório público
correspondente deve ser registrada no artefato de release quando houver
distribuição externa.

Nenhum logo ou asset de marca do Wealthfolio deve ser apresentado como marca
do NetWorth. O NetWorth deve manter nome, identidade visual própria e um aviso
claro de que não é afiliado, patrocinado ou endossado pelo Wealthfolio.
