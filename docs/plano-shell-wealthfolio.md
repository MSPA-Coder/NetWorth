# NetWorth — rumo, limites e referência

Rumo decidido em 23/09/2026. Este arquivo guarda só o que continua valendo;
o plano original de paridade (critério de 2 px, diff visual, lotes, gates e
divisão entre agentes) e os relatórios de progresso de 21/09 estão no
histórico do Git, na versão anterior a 24/09/2026.

## Rumo

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
  alvo, prazo, contas ou posições associadas), o que os limites de dados
  abaixo já preveem;
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
| 3. Diferencial | Patrimônio projetado em `/projecao/` e cartão na aba Patrimônio líquido. O CB publica `GET /patrimonio/v3/projection` (PR sistema-financeiro#76, em produção desde 23/09/2026); o NetWorth soma os investimentos de hoje, trata aporte como neutro, converte pela taxa de hoje e lista as premissas na tela. Proventos anunciados ficam para quando o CRV os publicar. |
| 4. Experiência única | Login único, barra comum para trocar de aplicativo e visual base no SharedAuth; os três atualizados para a mesma versão do SharedAuth. |
| 5. Horizonte | Metas e Assistente; lançamento rápido pelo NetWorth gravando pela API da fonte; fusão CB + NetWorth. Cada um por decisão própria. |

### Telas que faltam

Cada rota abaixo tem template e view-model próprios e mostra estado
indisponível, sem tabela alternativa, quando a fonte não publica o dado. O
aceite segue o critério desta seção (consistente, legível, correto sobre
cobertura e moeda), não a comparação com a referência.

- `/holdings/` e `/holdings/<id>/`;
- `/accounts/` e `/accounts/<id>/`;
- `/activities/`;
- `/spending/insights/` e `/spending/budget/`;
- `/goals/`, `/assistant/` e `/settings/` (fase 5).

## Referência

- Wealthfolio `3.8.0`, revisão `8f6f9898d30e84d7215e01d3d06cd65e02c9ab1b`;
- imagem local com digest
  `sha256:3c6f117828949204029c2b4a391f039e62987b4e091139e11b04e6764b5f6866`,
  definida em `referencia-wealthfolio/compose.yaml` (volume externo
  `wealthfolio-data`), em `http://127.0.0.1:8088`.

Serve para consultar e comparar ideias, não como portão de aceite.

## Limites de dados

O NetWorth não cria tabelas que dupliquem contas, posições, lançamentos,
categorias, cotações ou carteiras das fontes. O que ele guarda é só o que lhe
pertence: taxas de câmbio e fotografias consolidadas (e, na fase 5, metas).

As fontes publicam contratos HTTP autenticados e somente leitura
(`patrimonio/v1` para as fotografias diárias, `v2` e `v3` para as telas). Views
SQL podem existir **dentro** da fonte para estabilizar a publicação, mas o
NetWorth não acessa tabela, schema ou banco externo. Não usar: banco
compartilhado, `postgres_fdw`/`dblink`, models `managed=False` apontando para
fora, importação periódica de registros operacionais, escrita nas fontes. Se
o HTTP deixar de bastar, uma réplica somente leitura é decisão arquitetural
separada.

Invariantes do caminho de dados:

- dinheiro trafega como texto no JSON e vira `Decimal` na leitura;
- toda quantia tem moeda, e moedas não se somam sem taxa válida para a data;
- `as_of`, início e fim do período, data do preço e captura são datas
  distintas;
- ausência de informação não vira zero; fonte ausente ou defasada aparece
  antes dos totais;
- fotografia incompleta nunca é gravada como completa;
- IDs de fontes diferentes são opacos e prefixados pela fonte, nunca globais;
- tokens ficam no backend, em arquivo de segredo, e não aparecem em URL,
  HTML, JavaScript ou log.

Lacuna que falta fechar nas fontes: os proventos anunciados do CRV (fase 3).
Lacuna se fecha com contrato de leitura novo e versionado na fonte, nunca com
inferência ou dado fictício no NetWorth.

## Atribuição, identidade e código-fonte correspondente

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
