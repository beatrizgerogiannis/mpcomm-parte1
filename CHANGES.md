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

## Problemas conhecidos ainda não corrigidos
- **UDP sem confirmação/retransmissão e handshake não confiável:** um `READY` ou uma
  mensagem perdida trava o peer ou deixa o log incompleto. → Passo 5.
- **Mensagens de dados recebidas durante o laço de handshake são descartadas
  silenciosamente:** um peer que termina o handshake antes começa a enviar dados, e quem
  ainda está no laço de handshake ignora tudo que não é `READY`. → Passo 5.
- **`recv` de tamanho fixo no TCP** (`recv(2048)`, `recv(32768)` etc.): mensagens maiores
  (ex.: logs grandes) chegam truncadas e o `pickle.loads` falha. → Passo 3 (resolvido pelo RPC).
- **Registro duplicado e ausência de `unregister` no GroupMngr:** um peer que reinicia é
  registrado de novo e peers que saem nunca são removidos. → Passo 3.
- **`pickle` em rede é inseguro:** desserializar dados recebidos pela rede permite execução
  de código arbitrário. Mantido por compatibilidade; apenas documentado.
