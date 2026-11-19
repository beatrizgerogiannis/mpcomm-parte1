from socket  import *
from constMP import * #-
import threading
import random
import time
import pickle
import os
from requests import get

from reliability import RoundState, PendingSends, pending_key, ack_seq

# Reliable delivery (positive ACK + retransmission by timeout; see reliability.py)
RETRANS_TIMEOUT = int(os.environ.get('RETRANS_TIMEOUT_MS', 200)) / 1000  # seconds
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 100))  # retransmissions per item
RETRANS_CHECK_INTERVAL = 0.05  # seconds

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
      msgPack, addr = self.sock.recvfrom(1024)
      try:
        msg = pickle.loads(msgPack)
      except Exception as e:
        print('Discarded invalid packet from ', addr, ': ', e)
        continue
      if msg[0] == 'ACK':
        self.handleAck(msg, addr)
      elif msg[0] in ('READY', 'DATA', 'END'):
        self.handleMessage(msg, addr)
      else:
        print('Discarded unknown message from ', addr, ': ', msg)

  def handleAck(self, msg, addr):
    # ('ACK', round, acker, ackedKind, sender, seq): the acker is the destination of the original
    (_, rnd, acker, ackedKind, sender, seq) = msg
    with pendingCond:
      if pending.ack((addr[0], ackedKind, rnd, sender, seq)) and pending.empty():
        pendingCond.notify_all()

  def sendAck(self, msg, addr, myself):
    ack = ('ACK', msg[1], myself, msg[0], msg[2], ack_seq(msg))
    # ACKs go to the receive port of the sender (handled by its receiver thread)
    sendSocket.sendto(pickle.dumps(ack), (addr[0], PEER_UDP_PORT))

  def handleMessage(self, msg, addr):
    kind = msg[0]
    rnd = msg[1]
    sender = msg[2]
    with roundLock:
      r = currentRound
      if r is None or rnd > r.number:
        # Future round (the sender got its start signal before us): no ACK, so the sender
        # keeps retransmitting until we enter that round
        return
      self.sendAck(msg, addr, r.myself)
      if rnd < r.number:
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
        expected = st.next_expected(sender)
        if st.on_data(sender, seq):
          if seq > expected:
            r.gapsDetected += 1
            print('Gap: from process ' + str(sender) + ' expected ' + str(expected) + ', got ' + str(seq))
          elif seq < expected:
            print('Late message ' + str(seq) + ' from process ' + str(sender) + ' fills a gap')
          print('Message ' + str(seq) + ' from process ' + str(sender))
        else:
          print('Duplicate message ' + str(seq) + ' from process ' + str(sender) + ' discarded')
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
      now = time.monotonic()
      toResend = []
      with pendingCond:
        for (key, msgPack, sends) in pending.due(now, RETRANS_TIMEOUT):
          if sends > MAX_RETRIES:
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

  # Block until the receiver thread has received all handshakes
  rnd.allReady.wait()
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

  # Wait until I received everything, and everyone acknowledged everything I sent
  rnd.complete.wait()
  with pendingCond:
    pendingCond.wait_for(pending.empty)

  with roundLock:
    logList = list(rnd.state.log)
    duplicates = rnd.state.duplicates
    retransmissions = rnd.retransmissions
    gapsDetected = rnd.gapsDetected
  print('Round ' + str(rnd.number) + ' finished: ' + str(len(logList)) + ' messages delivered, ' +
        str(retransmissions) + ' retransmissions, ' + str(duplicates) + ' duplicates discarded, ' +
        str(gapsDetected) + ' gaps detected')

  # Write log file
  logFile = open('logfile'+str(myself)+'.log', 'w')
  logFile.writelines(str(logList))
  logFile.close()

  sendLog(logList)
