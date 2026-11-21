# MPComm - Multiparty Communication

## Execução local com Docker
Requer Docker e Docker Compose v2. Sobe 1 GroupMngr, 1 comparisonServer e N peers
(`N_PEERS`, padrão 6) numa rede bridge local. Os logs dos peers aparecem em `./logs`.

Modo automático (teste rápido; `AUTO_ROUNDS` aceita uma lista, ex.: `20,50`):
```bash
rm -rf logs && mkdir logs
AUTO_ROUNDS=20 timeout 180 docker compose up --build --exit-code-from comparison
echo "código de saída: $?"
for f in logs/logfile*.log; do python3 -c "import ast,sys; print(sys.argv[1], len(ast.literal_eval(open(sys.argv[1]).read())))" "$f"; done
docker compose down
```

Modo interativo (como no original):
```bash
docker compose up --build -d groupmngr peer
docker compose run --rm --use-aliases comparison
# digite 20; depois 0 para encerrar
docker compose down
```

Para outro número de peers: `N_PEERS=8 AUTO_ROUNDS=20 docker compose up --build --exit-code-from comparison`.
Em rede local o número de "unordered message rounds" costuma ser 0 ou baixo; isso é esperado.

## Sockets e XML-RPC

As interações cliente-servidor (GroupMngr, sinal de início e envio do log) usam XML-RPC da biblioteca padrão. O multicast entre peers continua em UDP. A comparação abaixo é com a tag `sockets-baseline`.

O `peerCommunicatorUDP.py` inteiro passou de 201 para 395 linhas. A maior parte desse aumento é a entrega confiável e o FIFO, que não entram nesta comparação. Os números de linhas abaixo cobrem só o cliente-servidor: o `GroupMngr.py` inteiro, o `comparisonServer.py` sem as 65 linhas de `check_fifo_and_completeness`, e no peer o trecho que registra, lista, recebe o início da rodada e devolve o log.

| | `sockets-baseline` | XML-RPC |
|---|---:|---:|
| Linhas, GroupMngr.py | 39 | 104 |
| Linhas, comparisonServer.py sem a checagem FIFO | 142 | 170 |
| Linhas, trecho cliente-servidor do peer | 57 | 81 |
| `register` | 64 B | 210 B |
| resposta de `list` com 6 IPs | 94 B | 396 B |
| `start(0, 20)` | 18 B | 190 B |
| log de 120 mensagens (6×20) | 736 B | 12 415 B |
| log de 3000 mensagens (6×500) | 20 764 B | 311 055 B |
| serializar o log de 3000, nesta máquina | 0,22 ms | 3,26 ms |
| ida e volta local do log de 3000 | 0,16 ms | 21,8 ms |

Os tamanhos do lado socket são o `pickle` da mensagem. Os do XML-RPC são o XML do pedido ou da resposta, com o nome do método. A ida e volta do socket leu o `pickle` inteiro; a do XML-RPC foi uma chamada `submitLog` de verdade, com as 3000 mensagens entregues.

**Vantagens do XML-RPC neste código**

- O corpo HTTP traz o tamanho. Some o `recv` de tamanho fixo (`2048` no GroupMngr, `512` na confirmação, `1024` no início, `32768` no log). Um `recv(32768)` também só funciona se o TCP entregar a mensagem inteira numa leitura. O `pickle` de 3000 mensagens tem 20 764 B e ainda cabe nesse buffer; o estouro medido fica por volta de 4800 mensagens no log (cerca de `nMsgs=800` com 6 peers). A rodada Docker com `nMsgs=500` entregou 3000 mensagens em cada peer.
- `register` devolve confirmação e é idempotente por IP. Reiniciar um peer não cria outra entrada: o GroupMngr registrou os 6, o reinício do mesmo contêiner gerou `Peer already registered` e `list()` continuou com 6 IPs distintos.
- `unregister` existe e também é idempotente. No baseline era `pass`.
- Falha de método vira `xmlrpc.client.Fault` ou `ProtocolError`, além de `OSError` quando a conexão não abre. O peer repete o `register` nesses dois casos.

**Desvantagens**

- A mensagem fica maior: cerca de 3 a 4 vezes no `register` e no `list`, 10 vezes no `start`, 15 a 17 vezes no log. O log de 3000 mensagens sai de 20 KB para 311 KB.
- A chamada é mais lenta. Na mesma máquina, a ida e volta desse log passou de 0,16 ms para 21,8 ms. No experimento isso fica pequeno perto da espera aleatória de 10 a 100 ms entre mensagens UDP.
- XML-RPC não tem tupla. O log chega como lista de listas e `restore_log` reconstitui `(remetente, sequência)` antes da comparação.
- O servidor atende uma chamada por vez. O comparison precisa de fila (`max(N, 16)`) para os logs que chegam juntos.
- Os dois lados continuam acoplados pelo nome dos métodos e pela porta em `constMP.py`. O que saiu foi o acordo implícito sobre as chaves do dicionário `op` e sobre o tamanho de cada `recv`.

Very simple demo of multicast communication without coordination. The intent is to demonstrate how things can go wrong as we scale a distributed system (in this case, scaling in the geographical dimension) without taking appropriate measures to coordinate the behavior of its components. Later, this example will serve as the basis to implement coordination protocols, notably for message ordering, as part of course assignments.

## Overall structure
A set of **peer processes** is established and each process multicasts a sequence of messages to all the others at random intervals. Messages are stamped with the ID of the sending process and a local sequence number defined by the sending process. This is a simple attempt to demonstrate the problem of message ordering (or, indeed, the lack of message ordering).

The peer processes run the *peerCommunicatorUDP.py* program, which has two separate threads, one for sending and the other for receiving messages. After starting, a peer processes waits for a signal (from a process that coordinates the experiment) in order to actually start multicasting the sequence of messages. Each peer collects all the received messages (in the order that they were received) and sends the list of messages to the coordinator process, which will compare the order of messages as received by all the peers (as described next).

A separate process runs *comparisonServer.py*, which, besides coordinating the start of the peer processes (by sending the start signal mentioned above), waits for the list of received messages from each peer and compares their order. It reports a metric that indicates the occurrence of out-of-order messages (actually, the number of message exchange rounds in which at least one process received a different message form the others). Importantly, we assume that no messages are lost, and, in an ideal scenario, all processes should receive all the same messages in the same order. Note, however, that the current version does not use a reliable message communication protocol to ensure that messages are not lost (this can be an improvement to be implemented as an assignment).

A second server process, running *GroupMngr.py*, is used for peers to register themselves (notably their IP addresses) as well as to discover the other peers.

In order to actually see the problem of message ordering, it is necessary to introduce variability on message communication latency, such as by running the peer processes on different networks, e.g., running some of the peers in one region of the cloud, whereas the others are run on another region. This illustrates the issue of geographic scalability, when the system works fine when all its components are in the same network, but starts to behave erroneously when its components are spread across the Internet.

## Setup
- Create one instance (virtual machine in the cloud) to run the two servers, and a number of instances (e.g., 6 in one cloud region, 2 on another) to run the peers.
- Edit *constMP.py* with the correct IP address of the instance where the two servers will run. Tip: allocate a fixed IP address (elastic IP in AWS jargon) to the server instance. Also edit the file to configure the number of peers processes in the experiment.
- On the server instance, run *GroupMngr.py*
- On some of the peer instances: run *peerCommunicator.py*
- Back on the server instance: run *comparisonServer.py*
