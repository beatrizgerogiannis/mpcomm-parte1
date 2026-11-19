# Registro de mudanças

## Passo 1 — Execução local com Docker Compose

Objetivo: rodar o sistema localmente (2 servidores + N peers), sem alterar a lógica de
comunicação. Sem variáveis de ambiente, o código se comporta exatamente como o original.

### constMP.py
- **O que mudou:** `SERVER_ADDR`, `GROUPMNGR_ADDR` e `N` passam a ser lidos das variáveis
  de ambiente `SERVER_ADDR`, `GROUPMNGR_ADDR` e `N_PEERS` (esta convertida para `int`).
  Os valores originais (`'34.203.80.210'` e `6`) continuam como padrão. As portas seguem fixas.
- **Por quê:** os endereços estavam fixos no código, então rodar em outro ambiente exigia
  editar o arquivo. No Compose, os servidores são acessados pelo nome do serviço
  (`groupmngr`, `comparison`).

### peerCommunicatorUDP.py
- **`get_public_ip()`:** com `PEER_IP_MODE=local`, usa `socket.gethostbyname(socket.gethostname())`
  (IP do contêiner). Com o padrão (`public`), continua consultando o api.ipify.org.
  **Por quê:** em rede local o IP público é o mesmo para todos os peers (NAT) e não é
  alcançável pelos outros contêineres; cada peer precisa registrar o IP que tem na rede do Compose.
- **`registerWithGroupManager()`:** até 20 tentativas de conexão ao GroupMngr, com 0,5 s entre
  elas; depois disso o erro original é propagado.
  **Por quê:** no Compose o peer pode subir antes do GroupMngr estar escutando; sem a nova
  tentativa, ele morre com `ConnectionRefusedError`.

### comparisonServer.py
- **O que mudou:** modo automático controlado por `AUTO_ROUNDS` (ex.: `"20"` ou `"20,50"`).
  Se a variável estiver definida e não vazia, `promptUser()` não chama `input()`: usa os valores
  em sequência. Antes de cada rodada, pede a lista ao GroupMngr (op `list`) a cada 0,5 s até
  haver N peers registrados (timeout de 60 s, com mensagem de erro e código de saída 1). Depois
  da última rodada, o processo termina com código 0, **sem** enviar `nMsgs=0` aos peers e **sem**
  enviar `stop` ao GroupMngr. Valores não positivos em `AUTO_ROUNDS` são rejeitados na partida
  (um `0` acionaria o caminho de encerramento do original).
- **Por quê:** permite rodar o experimento sem interação (`docker compose up --exit-code-from comparison`)
  e evita começar uma rodada antes de todos os peers estarem registrados.
- **Detalhe:** o Compose repassa `AUTO_ROUNDS=${AUTO_ROUNDS:-}`, ou seja, a variável existe mas
  fica vazia quando não é definida no host. Por isso "vazia" é tratada como modo interativo.

### Arquivos novos
- **requirements.txt:** só `requests` (já era usado pelo peer), com versão fixada.
- **Dockerfile:** imagem única `python:3.12-slim`, com `iproute2` (para `tc netem` nas etapas
  de perda/atraso), `PYTHONUNBUFFERED=1` (para os `print` aparecerem na hora no log do
  Compose) e código em `/app`.
- **docker-compose.yml:** rede bridge e três serviços: `groupmngr`, `comparison` (com
  `stdin_open`/`tty` para o modo interativo) e `peer` (`replicas: ${N_PEERS:-6}`,
  `PEER_IP_MODE=local`, `cap_add: NET_ADMIN` para o `tc`, diretório de trabalho `/app/logs`
  montado em `./logs` para os `logfile<ID>.log` aparecerem no host sem mudar o código).
  Também foi adicionado `depends_on: groupmngr` a `comparison` e `peer`, que só controla a ordem
  de criação dos contêineres. As novas tentativas continuam necessárias porque o processo pode
  ainda não estar escutando.
- **.gitignore:** `logs/`, `__pycache__/`, `*.pyc`, `.venv/`, `*.log`.

### Não alterado (de propósito, fica para o Passo 2)
Lógica de handshake, envio, recepção e comparação; bugs conhecidos (espera ocupada,
índices da comparação, `recv` de tamanho fixo etc.).

## Passo 2 — Correções do código-base

Correções de bugs do código original que atrapalham a Parte 1. O protocolo de comunicação
(formato das mensagens, portas, handshake, envio e recepção UDP) não mudou.

### peerCommunicatorUDP.py — espera ocupada pelos handshakes
- **O que era:** a thread principal esperava os handshakes com `while (handShakeCount < N): pass`.
- **Por que era problema:** espera ocupada consome 100% de um núcleo enquanto espera e,
  por causa do GIL, disputa CPU com a própria thread `MsgHandler` que precisa receber os
  handshakes. Com vários peers na mesma máquina, isso atrasa todos.
- **O que mudou:** novo `threading.Event` global `allHandshakesReceived`. A thread
  `MsgHandler` chama `.set()` quando `handShakeCount` chega a N; a thread principal chama
  `.wait()`. O Event é recriado no início de cada rodada (antes de criar o `MsgHandler`),
  porque o peer roda várias rodadas no mesmo processo e o Event da rodada anterior já está
  sinalizado. O contador `handShakeCount` continua existindo para os prints (virou
  atributo do `MsgHandler`; ver a correção seguinte).

### peerCommunicatorUDP.py — corrida no contador global `handShakeCount`
- **O que era:** `handShakeCount` era uma variável global, incrementada pela thread
  `MsgHandler` e zerada só no fim de `run()`, depois de enviar o log ao comparisonServer.
- **Por que era problema:** corrida entre rodadas. Assim que o comparisonServer recebe todos
  os logs, ele pode iniciar a rodada seguinte; a thread da rodada nova começa a incrementar o
  contador enquanto a thread da rodada anterior ainda pode executar o `handShakeCount = 0`.
  Se o zero vier depois de algum incremento, handshakes recebidos são perdidos na contagem e
  o peer espera para sempre por um contador que nunca chega a N.
- **O que mudou:** o contador virou atributo da instância (`self.handShakeCount`, iniciado em
  0 no construtor), então cada rodada tem o seu. O `handShakeCount = 0` do fim de `run()` e a
  variável global foram removidos. O print da thread principal usa `msgHandler.handShakeCount`.

### peerCommunicatorUDP.py — `myself` global usado dentro da thread
- **O que era:** `MsgHandler.run()` nomeava o log com `'logfile'+str(myself)+'.log'`, lendo a
  variável global `myself` da thread principal.
- **Por que era problema:** dependência implícita de estado global compartilhado entre
  threads; o valor lido é o que a thread principal tiver no momento da escrita, não
  necessariamente o da rodada em que o handler foi criado.
- **O que mudou:** o ID é passado no construtor (`MsgHandler(sock, myself)`), guardado em
  `self.myself` e usado para nomear o logfile.

### comparisonServer.py — comparação com índices errados
- **O que era:** `for j in range(0, N_MSGS-1)` e `for i in range(1, N-1)`.
- **Por que era problema:** `range` já exclui o limite superior, então o laço ignorava a
  última posição e o último peer. Além disso, cada log tem `N * N_MSGS` mensagens (N_MSGS
  de cada peer), mas só as primeiras `N_MSGS - 1` posições eram comparadas. Logs mais curtos
  (mensagens perdidas) causariam `IndexError` ou passariam despercebidos.
- **O que mudou:**
  - Lógica extraída para a função pura `count_unordered_rounds(logs)` (lista de logs → int).
  - Compara todas as posições (0 até o tamanho do maior log - 1) e todos os peers
    (1 até `len(logs) - 1`) contra o peer 0. Uma posição conta como fora de ordem se algum
    peer tiver ali uma mensagem diferente da do peer 0 ou não tiver mensagem nessa posição.
  - `waitForLogsAndCompare` imprime o tamanho de cada log e o esperado (`N * nMsgs`), com
    aviso se algum for diferente. A mensagem final continua "Found X unordered message rounds".
  - A criação do socket do servidor e a chamada `mainLoop()` foram para dentro de
    `if __name__ == "__main__":`, para os testes importarem a função sem abrir a porta.
    Rodar o script continua igual.
- **Testes:** `tests/test_compare.py` (unittest): `python3 -m unittest discover -s tests -v`.

## Passo 5 — Entrega confiável entre peers (1b: perda de mensagens)

Objetivo: entrega sem perdas e sem duplicatas no multicast UDP entre peers. A ordem
**não** muda: o log continua na ordem de chegada (FIFO é o próximo passo). O log enviado ao
comparisonServer continua sendo uma lista de tuplas `(remetente, seq)`.

### O que era / por que era problema
- **Handshake não confiável:** se um `('READY', id)` se perdesse, quem o esperava ficava
  preso para sempre no laço de handshake.
- **Fim não confiável:** se um `(-1, -1)` se perdesse, o receptor nunca saía do laço de
  recepção (não gravava o log nem o enviava ao servidor).
- **Mensagens de dados perdidas:** sumiam do log sem nenhum aviso.
- **Mensagens de dados durante o handshake:** um peer que terminava o handshake antes já
  enviava dados, e quem ainda estava no laço de handshake descartava tudo que não fosse
  `READY`.
- **Uma thread receptora por rodada:** a thread da rodada anterior podia ainda estar viva
  e as duas disputavam o mesmo socket.

### Protocolo novo (tuplas com `pickle`, como antes)
```
('READY', rodada, remetente)
('DATA',  rodada, remetente, seq)
('END',   rodada, remetente, total)          # total = quantas DATA o remetente enviou
('ACK',   rodada, quem_confirma, tipo_confirmado, remetente, seq)   # seq = -1 p/ READY e END
```
- **ACK positivo + retransmissão por timeout:** todo `READY`, `DATA` e `END` é enviado a
  cada peer e fica pendente até chegar o ACK correspondente. Uma thread de retransmissão
  verifica a cada 50 ms e reenvia o que está sem ACK há mais de `RETRANS_TIMEOUT_MS`
  (padrão 200). Depois de `MAX_RETRIES` (padrão 100) retransmissões de um mesmo item, o
  peer imprime `ERROR: giving up on ...` e desiste daquele item.
- **Duplicatas:** o receptor responde ACK a **toda** cópia recebida (o ACK anterior pode ter
  se perdido), mas só a primeira entra no log; as outras são descartadas e contadas.
- **Fim da rodada:** o peer só termina a rodada quando (a) recebeu `END` de todos os N e,
  para cada remetente, todas as mensagens `0..total-1`, e (b) recebeu ACK de tudo o que
  enviou (para garantir que os outros receberam tudo dele). Só então grava
  `logfile<ID>.log` e envia o log.
- **Por que ACK positivo e não NACK:** com NACK o receptor só percebe uma perda quando
  chega uma mensagem posterior (lacuna). A perda da **última** mensagem de um remetente, ou
  do próprio `END`, não deixa lacuna nenhuma, então nunca seria pedida de novo sem um
  mecanismo extra (timeout no receptor, heartbeat etc.). Com ACK positivo quem enviou sabe
  exatamente o que falta e retransmite.
- **Detecção de lacunas:** só para evidência (o reparo é feito pela retransmissão). Quando
  chega `seq` maior que o próximo esperado daquele remetente, o peer imprime
  `Gap: from process 2 expected 5, got 7`; quando a mensagem atrasada chega, imprime
  `Late message 5 from process 2 fills a gap`.
- **Resumo por rodada:** `Round X finished: 120 messages delivered, R retransmissions,
  D duplicates discarded, G gaps detected`.

### Número da rodada
- Contador local de cada peer, incrementado a cada sinal de início. Como todos os peers
  recebem a mesma sequência de sinais, os contadores coincidem. Toda mensagem leva a rodada.
- **Rodada passada:** responde ACK (para o remetente parar de retransmitir) e descarta.
  Acontece quando o ACK de uma mensagem da rodada anterior se perdeu.
- **Rodada futura:** descarta **sem** ACK. **Desvio da especificação** ("rodada diferente:
  ACK e descarta"): o comparisonServer inicia os peers um por um, então o peer 0 já está na
  rodada r+1 e envia `READY` enquanto o peer 5 ainda está na rodada r esperando o sinal de
  início. Se o peer 5 desse ACK e descartasse, o peer 0 pararia de retransmitir e o peer 5
  nunca receberia aquele `READY` (deadlock). Sem ACK, o peer 0 retransmite até o peer 5
  entrar na rodada r+1. Por isso podem aparecer algumas retransmissões mesmo sem perda.

### Threads (peerCommunicatorUDP.py)
- **Uma thread receptora de vida longa** (`MsgHandler`, daemon, iniciada uma vez): lê o
  socket UDP para sempre, trata ACKs (remove a pendência), responde ACK a `READY`/`DATA`/`END`
  e atualiza o estado da rodada atual. Continua respondendo ACK depois que a rodada termina,
  senão os outros peers retransmitiriam para sempre.
- **Mensagens de dados antes de completar o handshake** são processadas normalmente (não
  há mais um laço separado só de handshake).
- **Thread de retransmissão** (`Retransmitter`, daemon, iniciada uma vez).
- **Thread principal, por rodada:** incrementa a rodada e cria o estado (sob `roundLock`),
  envia `READY` a todos, espera `allReady`, envia as `nMsgs` `DATA` (mesmo intervalo
  aleatório de antes), envia `END` a todos, espera `complete` e as pendências zerarem
  (`threading.Condition`), grava o log e chama `sendLog(logList)`.
- Substitui o `allHandshakesReceived` global e o `self.handShakeCount` do Passo 2: o estado
  agora fica num objeto `Round` por rodada (com os Events `allReady` e `complete`). O
  `MsgHandler` não recebe mais o `myself` no construtor; o ID fica no `Round`.
- **`sendLog(logList)`:** o envio do log ao servidor foi movido para essa função **sem
  alterar o corpo** (vai ser trocado por xmlrpc no Passo 3).

### Arquivos novos
- **reliability.py:** lógica pura, sem sockets: `RoundState` (handshakes, mensagens por
  remetente, totais dos `END`, log, `gaps()`, `is_complete()`) e `PendingSends` (envios
  aguardando ACK, `due()` para os vencidos).
- **tests/test_reliability.py:** testes de unidade dessas classes.
- **tests/check_logs.py:** `python3 tests/check_logs.py N nMsgs [dir]` verifica que cada
  `logs/logfile*.log` tem exatamente `N*nMsgs` entradas, sem duplicatas, com todos os pares
  `(s, k)`. Sai com 0 (OK) ou 1. Não verifica ordem.

### docker-compose.yml — simulação de perda sem alterar o código
- O serviço `peer` roda `sh -c 'if [ -n "$NETEM" ]; then tc qdisc add dev eth0 root netem
  $NETEM; fi; exec python ...'` (no YAML escrito `$$NETEM`, senão o Compose substituiria a
  variável pelo valor do host na leitura do arquivo). Ex.: `NETEM="loss 10%"`. O netem só
  age no tráfego que **sai** pela `eth0`; as mensagens de um peer para ele mesmo não passam
  por ela.
- Novas variáveis repassadas: `NETEM`, `RETRANS_TIMEOUT_MS` e `MAX_RETRIES`.

### Resultados (Docker Compose, 6 peers, com as correções do Passo 5.1)
Netem só no UDP entre peers (porta 6789). Cada cenário com perda rodou 3 vezes seguidas.
Os números da versão anterior desta tabela foram medidos com o netem na interface inteira
(ver Passo 5.1) e foram substituídos.

| NETEM | AUTO_ROUNDS | Execuções | Código de saída | check_logs | Retransmissões por peer/rodada | Linhas `ERROR` |
|---|---|---|---|---|---|---|
| (nenhum)¹ | 20,20 | 1 | 0 | OK | 0 | — |
| `loss 10%` | 20,20 | 3 | 0, 0, 0 | OK ×3 | 16–38 | 0 |
| `loss 30%` | 20,20 | 3 | 0, 0, 0 | OK ×3 | 75–139 | 0 |

¹ Medido antes do Passo 5.1 e não repetido; sem `NETEM` nenhum comando `tc` é executado.

Com `NETEM="isso-e-invalido"` o contêiner `peer` sai com código 1 e mostra a mensagem de uso
do `tc` (antes rodava sem perda).

## Passo 5.1 — Correções da revisão

Correções encontradas na revisão técnica do Passo 5, antes do FIFO. O protocolo (formatos
das mensagens, ACK, regra de rodadas) não mudou; só foram extraídas funções.

### docker-compose.yml — netem aplicado à interface inteira
- **O que era:** `tc qdisc add dev eth0 root netem $NETEM` colocava o netem na raiz da `eth0`,
  então **todo** o tráfego que sai do peer sofria perda e atraso: ARP, TCP com o GroupMngr
  (register/list), TCP com o comparisonServer (resposta ao sinal de início e envio do log).
- **Cenário em que falhava:** com `loss 30%`, uma resposta ARP perdida fazia o `connect` falhar
  com `No route to host`, matando a thread principal do peer; segmentos TCP perdidos atrasavam
  muito o envio do log e o comparisonServer ficava esperando. O experimento travava ou terminava
  com erro por motivos que não têm nada a ver com o multicast. Além disso, um `NETEM` inválido
  só imprimia o erro do `tc` e o peer seguia rodando **sem** perda, dando um resultado "OK"
  falso.
- **O que mudou:** qdisc raiz `prio` (3 bandas, `priomap` mandando todo o tráfego não filtrado
  para a banda 1:2), `netem $NETEM` só na banda 1:3, e um filtro `u32` que manda para 1:3
  apenas UDP (`ip protocol 17`) com porta de destino 6789 (`PEER_UDP_PORT`). Todo o tráfego
  entre peers vai para essa porta, inclusive ACKs e retransmissões (o ACK é enviado para
  `(addr[0], PEER_UDP_PORT)`), então só ele sofre perda. O comando começa com `set -e`: se
  qualquer `tc` falhar, o contêiner sai com erro. Quando `NETEM` está definido, o peer imprime
  `tc qdisc show dev eth0` e `tc filter show dev eth0` ao subir, como evidência.
  O `priomap` explícito evita que o mapeamento padrão do `prio` (baseado no TOS) mande para a
  banda 1:3 algum tráfego que não seja do multicast.

### peerCommunicatorUDP.py — esperas sem timeout
- **O que era:** `rnd.allReady.wait()` e `rnd.complete.wait()` sem timeout.
- **Cenário em que falhava:** se um peer desistisse de um item (`MAX_RETRIES`) ou morresse,
  quem esperava por ele ficava bloqueado para sempre; o comparisonServer também, esperando
  o log. Nada era reportado.
- **O que mudou:** as duas esperas usam `ROUND_TIMEOUT_S` (padrão 120 s, repassada pelo
  Compose). No timeout, o peer imprime `ERROR: round R timed out after T s: missing READY from
  [...]; gaps per sender {...}; missing END from [...]` (lacunas: seqs que faltam abaixo do
  `total` do `END`, ou abaixo do maior seq recebido quando o `END` não chegou), grava o log
  parcial e chama `sendLog` normalmente, para a verificação acusar a perda em vez de travar.
  Se o timeout for no handshake, o peer não envia `DATA`/`END` naquela rodada.

### peerCommunicatorUDP.py — exceção derrubava as threads
- **O que era:** `MsgHandler.run` e `Retransmitter.run` não tratavam exceções (só a de
  `pickle.loads`).
- **Cenário em que falhava:** qualquer exceção inesperada (ex.: uma tupla mal formada que passa
  pelo `pickle` mas tem campos a menos, gerando `IndexError`/`ValueError`) matava a thread em
  silêncio. Sem a receptora, o peer para de responder ACK e de receber; sem a de retransmissão,
  nada mais é reenviado. Em ambos os casos o experimento trava.
- **O que mudou:** cada iteração dos dois laços está num `try/except Exception` que imprime
  `ERROR: exception in ... thread (continuing):` e o traceback completo
  (`traceback.print_exc()`), e segue para a próxima iteração. O corpo de cada iteração virou
  um método (`handleOne`, `checkOnce`).

### reliability.py — lógica do protocolo duplicada entre o peer e os testes
- **O que era:** a montagem do ACK, a chave extraída do ACK, a classificação da rodada e a
  regra de desistência estavam escritas inline no peer; o teste
  `test_ack_key_matches_ack_message` reconstruía o ACK por conta própria.
- **Cenário em que falhava:** uma mudança no peer (ex.: ordem dos campos do ACK) não quebraria
  nenhum teste, porque o teste verificava uma cópia da lógica e não a usada de verdade.
- **O que mudou:** novas funções puras em `reliability.py`, usadas pelo peer e pelos testes:
  `make_ack(msg, acker)`, `key_from_ack(ack, dest)` (mesmo formato de `pending_key`),
  `classify_round(rnd, current)` → `"current"`/`"past"`/`"future"` (`current=None`, antes da
  primeira rodada, dá `"future"`) e `should_give_up(sends, max_retries)` (`sends > max_retries`,
  mesma regra de antes). Novos testes para `classify_round` (inclusive `None`) e
  `should_give_up`; `test_ack_key_matches_ack_message` usa `make_ack`/`key_from_ack`.

## Problemas conhecidos ainda não corrigidos
- ~~**UDP sem confirmação/retransmissão e handshake não confiável:** um `READY` ou uma
  mensagem perdida trava o peer ou deixa o log incompleto.~~ → **Resolvido no Passo 5.**
- ~~**Mensagens de dados recebidas durante o laço de handshake são descartadas
  silenciosamente:** um peer que termina o handshake antes começa a enviar dados, e quem
  ainda está no laço de handshake ignora tudo que não é `READY`.~~ → **Resolvido no Passo 5.**
- **Sem ordem FIFO:** com perdas e retransmissões, mensagens do mesmo remetente chegam (e
  entram no log) fora de ordem. → Passo 6 (fila de espera).
- **`recv` de tamanho fixo no TCP** (`recv(2048)`, `recv(32768)` etc.): mensagens maiores
  (ex.: logs grandes) chegam truncadas e o `pickle.loads` falha. → Passo 3 (resolvido pelo RPC).
- **Registro duplicado e ausência de `unregister` no GroupMngr:** um peer que reinicia é
  registrado de novo e peers que saem nunca são removidos. → Passo 3.
- **`pickle` em rede é inseguro:** desserializar dados recebidos pela rede permite execução
  de código arbitrário. Mantido por compatibilidade; apenas documentado.
