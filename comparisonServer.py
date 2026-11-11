from socket import *
import pickle
from constMP import *
import time
import sys
import os

# Automatic mode (e.g., Docker Compose): AUTO_ROUNDS="20" or "20,50" replaces input().
# Unset or empty means interactive mode, as in the original.
autoRounds = None
if os.environ.get('AUTO_ROUNDS', '').strip():
	autoRounds = [int(x) for x in os.environ['AUTO_ROUNDS'].split(',')]
	if any(n <= 0 for n in autoRounds):
		sys.exit('ERROR: AUTO_ROUNDS must contain only positive integers (e.g., "20" or "20,50")')

serverSock = socket(AF_INET, SOCK_STREAM)
# Allow immediate reuse of the port after closing
serverSock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
serverSock.bind(('0.0.0.0', SERVER_PORT))
serverSock.listen(6)

def mainLoop():
	cont = 1
	while 1:
		nMsgs = promptUser()
		clientSock = socket(AF_INET, SOCK_STREAM)
		clientSock.connect((GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
		req = {"op":"list"}
		msg = pickle.dumps(req)
		clientSock.send(msg)
		msg = clientSock.recv(2048)
		clientSock.close()
		peerList = pickle.loads(msg)
		print("List of Peers: ", peerList)
		startPeers(peerList,nMsgs)
		if nMsgs != 0:
			print('Now, wait for the message logs from the communicating peers...')
			waitForLogsAndCompare(nMsgs)
		else:
			print('Stopping.')
			serverSock.close()
			
			# Tell group manager to stop
			cSock = socket(AF_INET, SOCK_STREAM)
			cSock.connect((GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
			req = {"op":"stop"}
			msg = pickle.dumps(req)
			cSock.send(msg)
			cSock.close()
			break

def promptUser():
	if autoRounds is None:
		nMsgs = int(input('Enter the number of messages for each peer to send (0 to terminate)=> '))
		return nMsgs
	if not autoRounds:
		# Last round done: exit without telling peers/group manager to stop
		print('AUTO_ROUNDS: all rounds done. Exiting.')
		sys.exit(0)
	nMsgs = autoRounds.pop(0)
	print('AUTO_ROUNDS: next round with ' + str(nMsgs) + ' messages per peer')
	waitForPeers()
	return nMsgs

def waitForPeers(timeout=60):
	# Poll the group manager until N peers are registered
	deadline = time.time() + timeout
	peerList = []
	while True:
		clientSock = socket(AF_INET, SOCK_STREAM)
		try:
			clientSock.connect((GROUPMNGR_ADDR,GROUPMNGR_TCP_PORT))
			clientSock.send(pickle.dumps({"op":"list"}))
			peerList = pickle.loads(clientSock.recv(2048))
		except (OSError, EOFError) as e:
			print('Group manager not available: ', e)
		finally:
			clientSock.close()
		if len(peerList) >= N:
			return
		if time.time() >= deadline:
			sys.exit('ERROR: timeout (' + str(timeout) + ' s) waiting for ' + str(N) +
				' peers to register; group manager has ' + str(len(peerList)))
		time.sleep(0.5)

def startPeers(peerList,nMsgs):
	# Connect to each of the peers and send the 'initiate' signal:
	peerNumber = 0
	for peer in peerList:
		clientSock = socket(AF_INET, SOCK_STREAM)
		clientSock.connect((peer, PEER_TCP_PORT))
		msg = (peerNumber,nMsgs)
		msgPack = pickle.dumps(msg)
		clientSock.send(msgPack)
		msgPack = clientSock.recv(512)
		print(pickle.loads(msgPack))
		clientSock.close()
		peerNumber = peerNumber + 1

def waitForLogsAndCompare(N_MSGS):
	# Loop to wait for the message logs for comparison:
	numPeers = 0
	msgs = [] # each msg is a list of tuples (with the original messages received by the peer processes)

	# Receive the logs of messages from the peer processes
	while numPeers < N:
		(conn, addr) = serverSock.accept()
		msgPack = conn.recv(32768)
		print ('Received log from peer')
		conn.close()
		msgs.append(pickle.loads(msgPack))
		numPeers = numPeers + 1

	unordered = 0

	# Compare the lists of messages
	for j in range(0,N_MSGS-1):
		firstMsg = msgs[0][j]
		for i in range(1,N-1):
			if firstMsg != msgs[i][j]:
				unordered = unordered + 1
				break
	
	print ('Found ' + str(unordered) + ' unordered message rounds')


# Initiate server:
mainLoop()
