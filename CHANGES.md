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
