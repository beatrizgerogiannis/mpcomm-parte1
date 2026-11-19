from socket  import *
from constMP import * #-
import threading
import random
import time
import pickle
import os
import traceback
from requests import get

from reliability import (RoundState, PendingSends, pending_key, make_ack, key_from_ack,
                         classify_round, should_give_up)

# Reliable delivery (positive ACK + retransmission by timeout; see reliability.py)
RETRANS_TIMEOUT = int(os.environ.get('RETRANS_TIMEOUT_MS', 200)) / 1000  # seconds
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 100))  # retransmissions per item
RETRANS_CHECK_INTERVAL = 0.05  # seconds
# Max wait (seconds) for the handshakes and for the end of the round; on timeout the partial
# log is written and sent anyway, so the comparison server reports the loss instead of hanging
ROUND_TIMEOUT = float(os.environ.get('ROUND_TIMEOUT_S', 120))

PEERS = []

# UDP sockets to send and receive data messages:
# Create send socket
sendSocket = socket(AF_INET, SOCK_DGRAM)
#Create and bind receive socket
recvSocket = socket(AF_INET, SOCK_DGRAM)
# Allow immediate reuse of the port after closing
recvSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
recvSocket.bind(('0.0.0.0', PEER_UDP_PORT))

# TCP socket to receive start signal from the comparison server:
serverSock = socket(AF_INET, SOCK_STREAM)
# Allow immediate reuse of the port after closing
serverSock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
serverSock.bind(('0.0.0.0', PEER_TCP_PORT))
serverSock.listen(1)


def get_public_ip():
  # PEER_IP_MODE=local: use the container/host IP instead of the public one (local runs)
  if os.environ.get('PEER_IP_MODE', 'public') == 'local':
    ipAddr = gethostbyname(gethostname())
    print('My local IP address is: {}'.format(ipAddr))
    return ipAddr
  ipAddr = get('https://api.ipify.org').content.decode('utf8')
  print('My public IP address is: {}'.format(ipAddr))
  return ipAddr

# Function to register this peer with the group manager
def registerWithGroupManager():
  print ('Connecting to group manager: ', (GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
  # Retry: the group manager may not be up yet (e.g., when started by Docker Compose)
  for attempt in range(1, 21):
    clientSock = socket(AF_INET, SOCK_STREAM)
    try:
      clientSock.connect((GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
      break
    except OSError as e:
      clientSock.close()
      print('Group manager not available (attempt ' + str(attempt) + '/20): ', e)
      if attempt == 20:
        raise
      time.sleep(0.5)
  ipAddr = get_public_ip()
  req = {"op":"register", "ipaddr":ipAddr, "port":PEER_UDP_PORT}
  msg = pickle.dumps(req)
  print ('Registering with group manager: ', req)
  clientSock.send(msg)
  clientSock.close()

def getListOfPeers():
  clientSock = socket(AF_INET, SOCK_STREAM)
  print ('Connecting to group manager: ', (GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
  clientSock.connect((GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
  req = {"op":"list"}
  msg = pickle.dumps(req)
  print ('Getting list of peers from group manager: ', req)
  clientSock.send(msg)
  msg = clientSock.recv(2048)
  PEERS = pickle.loads(msg)
  print ('Got list of peers: ', PEERS)
  clientSock.close()
  return PEERS

# State of one round, shared by the main thread and the receiver thread (guarded by roundLock)
class Round:
  def __init__(self, number, myself):
    self.number = number      # local round counter (incremented at every start signal)
    self.myself = myself      # my ID in this round
    self.state = RoundState(N)
    self.allReady = threading.Event()   # set when handshakes from all N peers were received
    self.complete = threading.Event()   # set when all messages and ENDs were received
    self.retransmissions = 0
    self.gapsDetected = 0

roundLock = threading.Lock()
currentRound = None   # Round being executed (None before the first start signal)
roundNumber = 0

# Messages sent and not yet acknowledged (guarded by pendingCond, notified when it empties)
pending = PendingSends()
pendingCond = threading.Condition()

# Send msg to addr and keep it pending until the ACK arrives (retransmitted by Retransmitter)
def sendReliable(addr, msg):
  msgPack = pickle.dumps(msg)
  with pendingCond:
    # Registered before sending, so that a fast ACK always finds it
    pending.add(pending_key(addr, msg), msgPack, time.monotonic())
  sendSocket.sendto(msgPack, (addr, PEER_UDP_PORT))

# Long-lived receiver thread (started once): reads the UDP socket forever, ACKs every
# READY/DATA/END (including duplicates) and updates the state of the current round
class MsgHandler(threading.Thread):
  def __init__(self, sock):
    threading.Thread.__init__(self, daemon=True)
    self.sock = sock

  def run(self):
    print('Handler is ready. Waiting for messages...')
    while True:
      # An exception must not kill the thread (the peer would stop receiving and ACKing)
      try:
        self.handleOne()
      except Exception:
        print('ERROR: exception in receiver thread (continuing):')
        traceback.print_exc()

  def handleOne(self):
    msgPack, addr = self.sock.recvfrom(1024)
    try:
      msg = pickle.loads(msgPack)
    except Exception as e:
      print('Discarded invalid packet from ', addr, ': ', e)
      return
    if msg[0] == 'ACK':
      self.handleAck(msg, addr)
    elif msg[0] in ('READY', 'DATA', 'END'):
      self.handleMessage(msg, addr)
    else:
      print('Discarded unknown message from ', addr, ': ', msg)

  def handleAck(self, msg, addr):
    # ('ACK', round, acker, ackedKind, sender, seq): the acker is the destination of the original
    with pendingCond:
      if pending.ack(key_from_ack(msg, addr[0])) and pending.empty():
        pendingCond.notify_all()

  def sendAck(self, msg, addr, myself):
    ack = make_ack(msg, myself)
    # ACKs go to the receive port of the sender (handled by its receiver thread)
    sendSocket.sendto(pickle.dumps(ack), (addr[0], PEER_UDP_PORT))

  def handleMessage(self, msg, addr):
    kind = msg[0]
    rnd = msg[1]
    sender = msg[2]
    with roundLock:
      r = currentRound
      which = classify_round(rnd, r.number if r is not None else None)
      if which == 'future':
        # Future round (the sender got its start signal before us): no ACK, so the sender
        # keeps retransmitting until we enter that round
        return
      self.sendAck(msg, addr, r.myself)
      if which == 'past':
        # Retransmission of an old round whose ACK was lost: the ACK above stops it
        print('Discarded ' + kind + ' from process ' + str(sender) + ' of old round ' + str(rnd))
        return
      st = r.state
      if kind == 'READY':
        if st.on_ready(sender):
          print('--- Handshake received: ', sender)
        else:
          print('Duplicate handshake from process ' + str(sender) + ' discarded')
        if st.all_ready():
          r.allReady.set()
      elif kind == 'DATA':
        seq = msg[3]
        if st.seen(sender, seq):
          st.on_data(sender, seq)   # counts the duplicate
          print('Duplicate message ' + str(seq) + ' from process ' + str(sender) + ' discarded')
        else:
          expected = st.highest(sender) + 1
          if seq > expected:
            r.gapsDetected += 1
            print('Gap: from process ' + str(sender) + ' expected ' + str(expected) + ', got ' + str(seq))
          elif seq < expected:
            print('Late message ' + str(seq) + ' from process ' + str(sender) + ' fills a gap')
          delivered = st.on_data(sender, seq)
          if not delivered:
            print('Message ' + str(seq) + ' from process ' + str(sender) + ' put in holdback (waiting for ' +
                  str(st.next_deliver.get(sender, 0)) + ')')
          for (s, k) in delivered:
            print('Message ' + str(k) + ' from process ' + str(s))
      elif kind == 'END':
        if not st.on_end(sender, msg[3]):
          print('Duplicate END from process ' + str(sender) + ' discarded')
      if st.is_complete():
        r.complete.set()

# Retransmission thread (started once): every 50 ms, resends what was not acknowledged
# within RETRANS_TIMEOUT; gives up on an item after MAX_RETRIES retransmissions
class Retransmitter(threading.Thread):
  def __init__(self):
    threading.Thread.__init__(self, daemon=True)

  def run(self):
    while True:
      time.sleep(RETRANS_CHECK_INTERVAL)
      # An exception must not kill the thread (nothing would be retransmitted anymore)
      try:
        self.checkOnce()
      except Exception:
        print('ERROR: exception in retransmission thread (continuing):')
        traceback.print_exc()

  def checkOnce(self):
    now = time.monotonic()
    toResend = []
    with pendingCond:
      for (key, msgPack, sends) in pending.due(now, RETRANS_TIMEOUT):
        if should_give_up(sends, MAX_RETRIES):
          pending.drop(key)
          print('ERROR: giving up on ' + str(key) + ' after ' + str(MAX_RETRIES) +
                ' retransmissions without ACK (destination, kind, round, sender, seq)')
        else:
          pending.mark_resent(key, now)
          toResend.append((key[0], msgPack))
      if pending.empty():
        pendingCond.notify_all()
    for (addr, msgPack) in toResend:
      sendSocket.sendto(msgPack, (addr, PEER_UDP_PORT))
    if toResend:
      with roundLock:
        if currentRound is not None:
          currentRound.retransmissions += len(toResend)

# Body moved unchanged from the end of the old MsgHandler.run()
def sendLog(logList):
  # Send the list of messages to the server (using a TCP socket) for comparison
  print('Sending the list of messages to the server for comparison...')
  clientSock = socket(AF_INET, SOCK_STREAM)
  clientSock.connect((SERVER_ADDR, SERVER_PORT))
  msgPack = pickle.dumps(logList)
  clientSock.send(msgPack)
  clientSock.close()

# Describes what is missing in round r (called on timeout): READY, gaps per sender, END, and the
# messages stuck in the holdback (received but not delivered because a lower seq is missing)
def describeMissing(r):
  with roundLock:
    st = r.state
    missingReady = sorted(set(range(N)) - st.ready)
    missingEnd = sorted(set(range(N)) - set(st.totals))
    heldBack = {s: st.held(s) for s in range(N) if st.held(s)}
    gaps = {}
    for sender in range(N):
      seqs = st.received.get(sender, set())
      if sender in st.totals:
        missing = [k for k in range(st.totals[sender]) if k not in seqs]
      else:
        missing = st.gaps(sender)   # total unknown: only the holes below the highest seq
      if missing:
        gaps[sender] = missing
  return ('missing READY from ' + str(missingReady) + '; gaps per sender ' + str(gaps) +
          '; missing END from ' + str(missingEnd) + '; held back per sender ' + str(heldBack))

# Function to wait for start signal from comparison server:
def waitToStart():
  (conn, addr) = serverSock.accept()
  msgPack = conn.recv(1024)
  msg = pickle.loads(msgPack)
  myself = msg[0]
  nMsgs = msg[1]
  conn.send(pickle.dumps('Peer process '+str(myself)+' started.'))
  conn.close()
  return (myself,nMsgs)

# From here, code is executed when program starts:
registerWithGroupManager()

# Receiver and retransmission threads live for the whole process (not one per round)
MsgHandler(recvSocket).start()
Retransmitter().start()
print('Handler started')

while 1:
  print('Waiting for signal to start...')
  (myself, nMsgs) = waitToStart()
  print('I am up, and my ID is: ', str(myself))

  if nMsgs == 0:
    print('Terminating.')
    exit(0)

  # New round: messages of other rounds are no longer accepted
  with roundLock:
    roundNumber = roundNumber + 1
    currentRound = Round(roundNumber, myself)
    rnd = currentRound
  print('Starting round ' + str(rnd.number))

  PEERS = getListOfPeers()

  # Send handshakes (reliably: retransmitted until each peer acknowledges)
  for addrToSend in PEERS:
    print('Sending handshake to ', addrToSend)
    sendReliable(addrToSend, ('READY', rnd.number, myself))

  print('Main Thread: Sent all handshakes. handShakeCount=', str(len(rnd.state.ready)))

  # Block until the receiver thread has received all handshakes (or timeout)
  timedOut = not rnd.allReady.wait(ROUND_TIMEOUT)
  if not timedOut:
    print('Main Thread: Received all handshakes. Sending data messages.')

    # Send a sequence of data messages to all other processes
    for msgNumber in range(0, nMsgs):
      # Wait some random time between successive messages
      time.sleep(random.randrange(10,100)/1000)
      for addrToSend in PEERS:
        sendReliable(addrToSend, ('DATA', rnd.number, myself, msgNumber))
        print('Sent message ' + str(msgNumber))

    # Tell all processes that I have no more messages to send (and how many I sent)
    for addrToSend in PEERS:
      sendReliable(addrToSend, ('END', rnd.number, myself, nMsgs))

    # Wait until I received everything (or timeout), and everyone acknowledged everything I
    # sent (bounded: items are dropped after MAX_RETRIES retransmissions)
    timedOut = not rnd.complete.wait(ROUND_TIMEOUT)
    if not timedOut:
      with pendingCond:
        pendingCond.wait_for(pending.empty)

  if timedOut:
    # Do not hang: send the partial log so that the verification reports the loss
    print('ERROR: round ' + str(rnd.number) + ' timed out after ' + str(ROUND_TIMEOUT) + ' s: ' +
          describeMissing(rnd))

  with roundLock:
    logList = list(rnd.state.log)
    duplicates = rnd.state.duplicates
    retransmissions = rnd.retransmissions
    gapsDetected = rnd.gapsDetected
    heldBack = rnd.state.held_back
  print('Round ' + str(rnd.number) + ' finished: ' + str(len(logList)) + ' messages delivered, ' +
        str(retransmissions) + ' retransmissions, ' + str(duplicates) + ' duplicates discarded, ' +
        str(gapsDetected) + ' gaps detected, ' + str(heldBack) + ' held back')

  # Write log file
  logFile = open('logfile'+str(myself)+'.log', 'w')
  logFile.writelines(str(logList))
  logFile.close()

  sendLog(logList)
