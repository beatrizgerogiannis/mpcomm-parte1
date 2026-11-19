# Integração do RPC (Passo 3 / item 1a) com a Parte 1b

Para quem vai trocar os sockets TCP por `xmlrpc` (biblioteca padrão do Python:
`xmlrpc.server.SimpleXMLRPCServer` e `xmlrpc.client.ServerProxy`, sem dependência nova).

A Parte 1b (entrega confiável + FIFO) já está na `main` e **não depende** do transporte das
interações cliente-servidor: ela só usa o UDP entre peers (porta 6789). O RPC troca apenas
as quatro interações TCP:

| Quem chama | Quem atende | Hoje (TCP + pickle) | Com RPC |
|---|---|---|---|
| peer | GroupMngr | `register`, `list` | `register(ip, porta)`, `list()` |
| comparisonServer | GroupMngr | `list`, `stop` | `list()`, `stop()` |
| comparisonServer | peer | sinal de início `(id, nMsgs)` | `start(myself, nMsgs)` |
| peer | comparisonServer | envio do log | `sendLog(myself, rodada, log)` |

As linhas citadas abaixo são as da `main` depois do Passo 6.1.

---

## (a) Tuplas viram listas

O XML-RPC não tem tupla: uma tupla enviada chega como **lista**. O log do peer é uma lista de
tuplas `(remetente, seq)`; pelo RPC ele chega como lista de listas `[[0, 0], [1, 0], ...]`.

Listas não são hasháveis, então `set`, `Counter` e comparação com tuplas quebram ou dão
resultado errado (`[0, 1] != (0, 1)`). **Ao receber, converta:**

```python
def sendLog(myself, rodada, log):
    log = [tuple(e) for e in log]
    ...
```

O mesmo vale para qualquer outra tupla que passe pelo RPC (ex.: se o `list` do GroupMngr
devolver tuplas `(ip, porta)`; hoje ele devolve só os IPs, `GroupMngr.py:20-23`).

## (b) `None` exige `allow_none=True`

Uma função Python sem `return` devolve `None`, e o XML-RPC padrão não sabe serializar `None`
(`TypeError: cannot marshal None unless allow_none is enabled`). Duas opções (use as duas):

- criar servidor **e** cliente com `allow_none=True`:
  `SimpleXMLRPCServer(..., allow_none=True)` e `ServerProxy(url, allow_none=True)`;
- fazer todo handler devolver algo explícito (`True` ou uma string).

## (c) `start(myself, nMsgs)` no peer só enfileira e retorna

Hoje a thread principal do peer bloqueia em `serverSock.accept()` dentro de `waitToStart()`
(`peerCommunicatorUDP.py:276-284`), e o socket é criado no topo do arquivo (linhas 33-38).

Com RPC, o handler **não pode executar a rodada**. O comparisonServer chama `start` nos peers
**um por um** (`startPeers`, `comparisonServer.py:80-92`) e espera cada chamada retornar. Se o
handler do peer 0 rodasse a rodada, ele ficaria esperando o handshake do peer 1, que nunca
recebeu o `start` porque o comparisonServer está preso esperando o peer 0: **deadlock**.

Então:

```python
import queue
startQueue = queue.Queue()

def start(myself, nMsgs):           # roda na thread do servidor RPC
    startQueue.put((myself, nMsgs))
    return 'Peer process ' + str(myself) + ' started.'

def waitToStart():                  # roda na thread principal, no lugar do accept()
    return startQueue.get()
```

O laço principal (`while 1:`, linha 294) continua igual: `(myself, nMsgs) = waitToStart()`
(linha 296). Toda a lógica da rodada (número da rodada, `Round`, `roundLock`) fica na thread
principal, como hoje.

## (d) `sendLog` continua na thread principal; o servidor do comparisonServer sobe antes

- `sendLog(logList)` (definição na linha 246, chamada na linha 368) continua sendo chamado
  pela **thread principal** do peer, no fim da rodada. Não chame de dentro da thread receptora
  (`MsgHandler`) nem da `Retransmitter`: elas precisam continuar respondendo ACK e
  retransmitindo enquanto o log é enviado.
- No comparisonServer, o servidor RPC que recebe `sendLog` precisa estar **escutando antes de
  `startPeers`**. Um peer pode terminar a rodada e chamar `sendLog` logo depois do `start`; se
  o servidor ainda não estiver no ar, dá `ConnectionRefusedError` e o log se perde. Hoje isso
  já funciona porque o socket é criado no `__main__` antes de `mainLoop()`
  (`comparisonServer.py:134-142`).
- Como o `mainLoop` do comparisonServer é sequencial (chama `startPeers` e depois espera os
  logs), o servidor RPC dele tem que rodar `serve_forever()` **numa thread separada**
  (daemon), iniciada antes de `mainLoop()`. O handler `sendLog` só coloca o log numa
  `queue.Queue`; `waitForLogsAndCompare` lê dessa fila.

## (e) Assinatura sugerida e timeout esperando os N logs

```python
def sendLog(myself, rodada, log):   # no comparisonServer
    logQueue.put((myself, rodada, [tuple(e) for e in log]))
    return True
```

- `myself` identifica quem mandou (hoje os logs chegam sem identificação, na ordem de chegada,
  `comparisonServer.py:100-106`). Guardar por ID permite ordenar os logs por peer e dizer
  **quem** não mandou.
- `rodada` permite descartar um log atrasado de uma rodada anterior. O peer tem o número em
  `rnd.number` (contador local incrementado a cada `start`); o comparisonServer pode manter o
  próprio contador, também incrementado a cada rodada que inicia.
- **Timeout:** `waitForLogsAndCompare` deve usar `logQueue.get(timeout=...)` e, se estourar,
  imprimir `ERROR: ...` com os IDs que faltam, em vez de ficar parado para sempre. O peer espera
  até `ROUND_TIMEOUT_S` (padrão 120 s) em cada uma das três esperas da rodada, então um valor
  como `3 * ROUND_TIMEOUT_S + 30` cobre o pior caso.

## (f) Encerramento com `nMsgs = 0`

No modo interativo, digitar `0` faz o comparisonServer mandar `start(id, 0)` a todos e depois
`stop` ao GroupMngr (`comparisonServer.py:33-44`).

- **Peer:** a thread principal lê `nMsgs == 0` e chama `exit(0)` (linhas 299-301). A thread do
  servidor RPC precisa ser **daemon** (`threading.Thread(target=server.serve_forever,
  daemon=True)`), senão o processo não termina. As threads `MsgHandler` e `Retransmitter` já
  são daemon.
- **GroupMngr:** **não** chame `server.shutdown()` dentro do handler `stop`. O `shutdown()`
  espera o `serve_forever()` sair, e o `serve_forever()` está parado executando o próprio
  handler: trava para sempre. Faça o handler só sinalizar e retornar, e encerre fora dele:

```python
stopEvent = threading.Event()

def stop():
    stopEvent.set()
    return True

threading.Thread(target=server.serve_forever, daemon=True).start()
stopEvent.wait()
server.shutdown()
server.server_close()
```

## (g) Ordem da lista e as 20 tentativas de registro

- **Ordem:** o comparisonServer dá a cada peer o ID igual à **posição dele na lista** do
  GroupMngr (`startPeers`, `peerNumber`). A lista tem que sair na ordem de registro, como hoje
  (`GroupMngr.py:20-23`, uma `list` montada de `membership`). Não troque por `set` ou `dict`
  sem ordem, e não ordene por IP.
- **20 tentativas:** `registerWithGroupManager` (linhas 52-71) tenta conectar 20 vezes, com
  0,5 s entre elas, porque no Compose o peer pode subir antes do GroupMngr estar escutando.
  Com `ServerProxy`, criar o proxy **não** conecta; o erro só aparece **na chamada**
  `proxy.register(...)`. Então o laço de tentativas deve envolver a chamada e capturar
  `OSError` (inclui `ConnectionRefusedError`) **e também** `xmlrpc.client.Fault` (exceção
  levantada no handler do servidor) e, por segurança, `xmlrpc.client.ProtocolError`. Na 20ª
  falha, propague o erro, como hoje.
- `getListOfPeers` (linhas 73-85) e o `waitForPeers` do comparisonServer
  (`comparisonServer.py:59-78`, que já trata `OSError`) passam a chamar `list()`.

## (h) Manter as portas TCP 5678, 5679 e 5680

O XML-RPC é HTTP sobre TCP, então ele usa as mesmas portas de hoje (`constMP.py`):

| Servidor RPC | Porta | Constante |
|---|---|---|
| comparisonServer | 5678 | `SERVER_PORT` |
| peer | 5679 | `PEER_TCP_PORT` |
| GroupMngr | 5680 | `GROUPMNGR_TCP_PORT` |

Não mude as portas nem use a 6789: o filtro do netem no `docker-compose.yml` aplica perda e
atraso **só** ao UDP com destino 6789. Se o RPC fosse para lá (ou para UDP), ele passaria a
sofrer as perdas simuladas. Escute em `'0.0.0.0'` (não `'localhost'`), senão os outros
contêineres não alcançam o servidor.

## (i) Servidor do comparisonServer dentro de `if __name__ == "__main__":`

Os testes (`tests/test_compare.py`) fazem `import comparisonServer` para testar
`count_unordered_rounds`. Se o `SimpleXMLRPCServer` for criado no nível do módulo, o import
abre a porta 5678 (e falha se ela estiver em uso). Crie o servidor, registre as funções e
inicie a thread **dentro** do bloco `if __name__ == "__main__":` (linhas 134-142), como o
socket é criado hoje.

## (j) Onde estão os conflitos esperados

Se o seu branch saiu de antes do Passo 5, espere conflito de merge em:

- **`peerCommunicatorUDP.py`, topo (linhas 1-38):** a 1b acrescentou imports (`traceback`,
  `from reliability import ...`) e as constantes `RETRANS_TIMEOUT`, `MAX_RETRIES`,
  `ROUND_TIMEOUT`. O RPC remove `serverSock` (linhas 33-38) e acrescenta os imports do xmlrpc.
  Fique com os dois conjuntos de imports.
- **`peerCommunicatorUDP.py`, laço principal (linhas 287-368):** a 1b reescreveu quase todo o
  laço (rodada, `sendReliable`, três esperas com timeout). Do lado do RPC só mudam a criação e
  o início da thread do servidor RPC (antes de `while 1:`), o corpo de `waitToStart()` e o de
  `sendLog()`. Resolva mantendo o laço da 1b e trocando só essas funções.
- **`sendLog`:** na 1b ele foi movido para uma função com o corpo original (linhas 246-253),
  justamente para o RPC trocar só o corpo. Se o seu branch ainda tem o envio dentro do
  `MsgHandler.run()`, descarte essa versão: a thread receptora da 1b não envia log.
- **`CHANGES.md`:** os dois lados acrescentam seções; mantenha as duas, na ordem dos passos.
- **Não deve haver conflito** em `comparisonServer.py`, `GroupMngr.py`, `constMP.py` e
  `reliability.py`: a 1b não mexeu nos três primeiros depois do Passo 2, e o RPC não precisa
  mexer no último.

## (k) `logRequests=False`

Por padrão o `SimpleXMLRPCServer` imprime uma linha por requisição
(`"POST /RPC2 HTTP/1.1" 200 -`). O `waitForPeers` chama `list` a cada 0,5 s, e cada rodada
gera `start` e `sendLog` para cada peer: a saída fica poluída e atrapalha os `grep` do
`scripts/run_scenario.sh`. Crie os três servidores com `logRequests=False`.

---

### Esqueleto de referência (peer)

```python
from xmlrpc.server import SimpleXMLRPCServer
import xmlrpc.client, queue, threading

startQueue = queue.Queue()

def start(myself, nMsgs):
    startQueue.put((myself, nMsgs))
    return 'Peer process ' + str(myself) + ' started.'

rpcServer = SimpleXMLRPCServer(('0.0.0.0', PEER_TCP_PORT), allow_none=True, logRequests=False)
rpcServer.register_function(start, 'start')
threading.Thread(target=rpcServer.serve_forever, daemon=True).start()
```

A serialização com `pickle` continua só no UDP entre peers (inseguro em rede; documentado no
`CHANGES.md`). O RPC elimina o `pickle` nas interações cliente-servidor e também o
`recv` de tamanho fixo (`recv(2048)`, `recv(32768)`), que truncaria logs grandes.
