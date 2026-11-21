from socket  import *
from constMP import * #-
import threading
import random
import time
import pickle
import os
import xmlrpc.client
from xmlrpc.server import SimpleXMLRPCServer
from requests import get

#handShakes = [] # not used; only if we need to check whose handshake is missing

# Set by the handler thread when its handShakeCount reaches N (recreated every round)
allHandshakesReceived = threading.Event()

PEERS = []
myIpAddr = None

# UDP sockets to send and receive data messages:
# Create send socket
sendSocket = socket(AF_INET, SOCK_DGRAM)
#Create and bind receive socket
recvSocket = socket(AF_INET, SOCK_DGRAM)
# Allow immediate reuse of the port after closing
recvSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
recvSocket.bind(('0.0.0.0', PEER_UDP_PORT))


def get_public_ip():
  # PEER_IP_MODE=local: use the container/host IP instead of the public one (local runs)
  if os.environ.get('PEER_IP_MODE', 'public') == 'local':
    ipAddr = gethostbyname(gethostname())
    print('My local IP address is: {}'.format(ipAddr))
    return ipAddr
  ipAddr = get('https://api.ipify.org').content.decode('utf8')
  print('My public IP address is: {}'.format(ipAddr))
  return ipAddr

class ReusableSimpleXMLRPCServer(SimpleXMLRPCServer):
  # The port can be bound again right after a restart, as with the old socket.
  allow_reuse_address = True

class PeerService:
  def __init__(self):
    self.myself = None
    self.nMsgs = None

  def start(self, peerNumber, nMsgs):
    self.myself = int(peerNumber)
    self.nMsgs = int(nMsgs)
    # Leave the group before acknowledging termination. startPeers waits for
    # this reply, and only then does the comparison server stop the group manager.
    if self.nMsgs == 0:
      unregisterFromGroupManager()
    return 'Peer process ' + str(self.myself) + ' started.'

# XML-RPC server for the comparison server's start signal. Peer messages stay on UDP.
peerServer = ReusableSimpleXMLRPCServer(
  ('0.0.0.0', PEER_TCP_PORT),
  allow_none=True,
  logRequests=False,
)
peerService = PeerService()
peerServer.register_instance(peerService)

def groupManagerProxy():
  # XML-RPC to the group manager. Messages between peers stay on UDP.
  return xmlrpc.client.ServerProxy(
    'http://%s:%s' % (GROUPMNGR_ADDR, GROUPMNGR_TCP_PORT),
    allow_none=True)

def comparisonProxy():
  return xmlrpc.client.ServerProxy(
    'http://%s:%s' % (SERVER_ADDR, SERVER_PORT),
    allow_none=True)

# Register this peer with the group manager.
def registerWithGroupManager():
  global myIpAddr
  print ('Connecting to group manager: ', (GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
  myIpAddr = get_public_ip()
  req = {'ipaddr': myIpAddr, 'port': PEER_UDP_PORT}
  print ('Registering with group manager: ', req)
  # Retry: the group manager may not be up yet (e.g., when started by Docker Compose).
  # A repeated register is safe: the server keeps a single entry per IP.
  for attempt in range(1, 21):
    try:
      groupManagerProxy().register(myIpAddr, PEER_UDP_PORT)
      break
    except (OSError, xmlrpc.client.Error) as e:
      print('Group manager not available (attempt ' + str(attempt) + '/20): ', e)
      if attempt == 20:
        raise
      time.sleep(0.5)

def unregisterFromGroupManager():
  if myIpAddr is None:
    return
  req = {'ipaddr': myIpAddr, 'port': PEER_UDP_PORT}
  print ('Unregistering from group manager: ', req)
  try:
    groupManagerProxy().unregister(myIpAddr, PEER_UDP_PORT)
  except (OSError, xmlrpc.client.Error) as e:
    print('Could not unregister from group manager: ', e)

def getListOfPeers():
  print ('Connecting to group manager: ', (GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
  print ('Getting list of peers from group manager')
  PEERS = groupManagerProxy().list()
  print ('Got list of peers: ', PEERS)
  return PEERS

class MsgHandler(threading.Thread):
  def __init__(self, sock, myself):
    threading.Thread.__init__(self)
    self.sock = sock
    self.myself = myself
    # Counter to make sure we have received handshakes from all other processes
    self.handShakeCount = 0

  def run(self):
    print('Handler is ready. Waiting for the handshakes...')
    
    #global handShakes
    
    logList = []
    
    # Wait until handshakes are received from all other processes
    # (to make sure that all processes are synchronized before they start exchanging messages)
    while self.handShakeCount < N:
      msgPack = self.sock.recv(1024)
      msg = pickle.loads(msgPack)
      #print ('########## unpickled msgPack: ', msg)
      if msg[0] == 'READY':

        # To do: send reply of handshake and wait for confirmation

        self.handShakeCount = self.handShakeCount + 1
        #handShakes[msg[1]] = 1
        print('--- Handshake received: ', msg[1])
        if self.handShakeCount == N:
          allHandshakesReceived.set()

    print('Secondary Thread: Received all handshakes. Entering the loop to receive messages.')

    stopCount=0 
    while True:                
      msgPack = self.sock.recv(1024)   # receive data from client
      msg = pickle.loads(msgPack)
      if msg[0] == -1:   # count the 'stop' messages from the other processes
        stopCount = stopCount + 1
        if stopCount == N:
          break  # stop loop when all other processes have finished
      else:
        print('Message ' + str(msg[1]) + ' from process ' + str(msg[0]))
        logList.append(msg)
        
    # Write log file
    logFile = open('logfile'+str(self.myself)+'.log', 'w')
    logFile.writelines(str(logList))
    logFile.close()
    
    # Send the list of messages to the comparison server.
    print('Sending the list of messages to the server for comparison...')
    comparisonProxy().submitLog(logList)

    exit(0)

# Function to wait for the start signal from the comparison server:
def waitToStart():
  # One start() call. The XML-RPC response is sent before this returns.
  peerServer.handle_request()
  return (peerService.myself, peerService.nMsgs)

# From here, code is executed when program starts:
registerWithGroupManager()
while 1:
  print('Waiting for signal to start...')
  (myself, nMsgs) = waitToStart()
  print('I am up, and my ID is: ', str(myself))

  if nMsgs == 0:
    print('Terminating.')
    exit(0)

  # New event for this round (the previous one is already set)
  allHandshakesReceived = threading.Event()

  # Create receiving message handler
  msgHandler = MsgHandler(recvSocket, myself)
  msgHandler.start()
  print('Handler started')

  PEERS = getListOfPeers()
  
  # Send handshakes
  # To do: Must continue sending until it gets a reply from each process
  #        Send confirmation of reply
  for addrToSend in PEERS:
    print('Sending handshake to ', addrToSend)
    msg = ('READY', myself)
    msgPack = pickle.dumps(msg)
    sendSocket.sendto(msgPack, (addrToSend,PEER_UDP_PORT))
    #data = recvSocket.recvfrom(128) # Handshadke confirmations have not yet been implemented

  print('Main Thread: Sent all handshakes. handShakeCount=', str(msgHandler.handShakeCount))

  # Block until the handler thread has received all handshakes
  allHandshakesReceived.wait()

  # Send a sequence of data messages to all other processes 
  for msgNumber in range(0, nMsgs):
    # Wait some random time between successive messages
    time.sleep(random.randrange(10,100)/1000)
    msg = (myself, msgNumber)
    msgPack = pickle.dumps(msg)
    for addrToSend in PEERS:
      sendSocket.sendto(msgPack, (addrToSend,PEER_UDP_PORT))
      print('Sent message ' + str(msgNumber))

  # Tell all processes that I have no more messages to send
  for addrToSend in PEERS:
    msg = (-1,-1)
    msgPack = pickle.dumps(msg)
    sendSocket.sendto(msgPack, (addrToSend,PEER_UDP_PORT))
