# Trabalho de Sistemas Distribuídos (UFG) — Parte 1

Repositório baseado no MPComm do prof. Fábio (https://github.com/professorfabio/MPComm).
Multicast em Python 3 que demonstra a falta de ordenação de mensagens quando o sistema escala.

## Escopo da Parte 1 (entrega: 22/11/2026, 23:59)
- 1a) Trocar sockets TCP por RPC nas interações cliente-servidor:
  peer→GroupMngr (register, list), comparisonServer→GroupMngr (list, stop),
  comparisonServer→peer (sinal de início), peer→comparisonServer (envio do log).
- 1b) No multicast entre peers (continua em UDP): tratar perda de mensagens
  (números de sequência, detecção de lacunas, retransmissão) e garantir ordem FIFO
  por remetente (fila de espera / hold-back queue). O handshake também deve ser confiável.
- FORA DO ESCOPO: ordem total, ordem causal, aplicação replicada (são das Partes 2 a 4).
  Não implemente nada disso.

## Arquivos
- constMP.py — constantes (portas, N, endereços dos servidores)
- GroupMngr.py — gerenciador de grupo (registro e listagem de peers)
- comparisonServer.py — coordena o experimento e compara os logs dos peers
- peerCommunicatorUDP.py — o peer (envio e recepção multicast via UDP)

## Regras de trabalho
- Leia os arquivos relevantes antes de editar. Não reescreva o que não foi pedido.
- Uma tarefa por vez; cada tarefa termina com algo testável localmente.
- Tudo deve rodar LOCALMENTE (vários peers na mesma máquina). Não dependa de nuvem nem de IP público.
- Na 1b, perda e atraso devem ser simuláveis por configuração (taxa de descarte e atraso artificial).
- Não faça commit nem push. Sugira a mensagem de commit e eu commito.
- Não adicione dependências sem me avisar e justificar.
- Toda correção de bug do código original deve ser registrada em CHANGES.md
  (o que era, por que era problema, o que mudou), porque vai para o README.
- Mantenha nomes e mensagens de log em português ou inglês de forma consistente com o arquivo editado.

## Observações conhecidas
- Serialização atual com pickle (inseguro em rede; manter, mas documentar).
- A métrica antiga do comparisonServer mede ORDEM TOTAL; com FIFO ela continua acusando
  rodadas fora de ordem, o que é esperado. A verificação nova deve checar FIFO e ausência de perdas.

## Estado atual
- [X] Passo 1: execução local com vários peers
- [X] Passo 2: correções do código-base
- [ ] Passo 3: RPC (1a)
- [ ] Passo 4: comparação sockets × RPC
- [X] Passo 5: perda de mensagens (1b)
- [X] Passo 6: FIFO (1b)
- [ ] Passo 7: verificação no comparisonServer
- [ ] Passo 9: README e vídeo