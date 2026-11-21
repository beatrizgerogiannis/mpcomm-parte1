from xmlrpc.server import SimpleXMLRPCServer
import xmlrpc.client
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

class ReusableSimpleXMLRPCServer(SimpleXMLRPCServer):
	# The port can be bound again right after a restart, as with the old socket.
	allow_reuse_address = True

def groupManagerProxy():
	return xmlrpc.client.ServerProxy(
		'http://%s:%s' % (GROUPMNGR_ADDR, GROUPMNGR_TCP_PORT),
		allow_none=True)

def peerProxy(peer):
	return xmlrpc.client.ServerProxy(
		'http://%s:%s' % (peer, PEER_TCP_PORT),
		allow_none=True)

def restore_log(log):
	# XML-RPC has no tuples: each (sender, seq) arrives as a list.
	return [tuple(msg) for msg in log]

class ComparisonService:
	def __init__(self):
		self.logs = []

	def submitLog(self, log):
		self.logs.append(restore_log(log))
		print('Received log from peer')
		return True

def mainLoop():
	cont = 1
	while 1:
		nMsgs = promptUser()
		peerList = groupManagerProxy().list()
		print("List of Peers: ", peerList)
		startPeers(peerList,nMsgs)
		if nMsgs != 0:
			print('Now, wait for the message logs from the communicating peers...')
			waitForLogsAndCompare(nMsgs)
		else:
			print('Stopping.')
			server.server_close()

			# Tell group manager to stop
			try:
				groupManagerProxy().stop()
			except (OSError, xmlrpc.client.Error) as e:
				print('Group manager stop: ', e)
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
		try:
			peerList = groupManagerProxy().list()
		except (OSError, xmlrpc.client.Error) as e:
			print('Group manager not available: ', e)
		if len(peerList) >= N:
			return
		if time.time() >= deadline:
			sys.exit('ERROR: timeout (' + str(timeout) + ' s) waiting for ' + str(N) +
				' peers to register; group manager has ' + str(len(peerList)))
		time.sleep(0.5)

def startPeers(peerList,nMsgs):
	# Tell each peer its id and how many messages to send.
	peerNumber = 0
	for peer in peerList:
		print(peerProxy(peer).start(peerNumber, nMsgs))
		peerNumber = peerNumber + 1

def waitForLogsAndCompare(N_MSGS):
	# One submitLog call per peer. The listening socket is already open, so a
	# peer that finishes during startPeers queues until handle_request runs.
	comparisonService.logs = []
	while len(comparisonService.logs) < N:
		server.handle_request()
	msgs = comparisonService.logs

	# Check log sizes: each peer should have received N_MSGS messages from each of the N peers
	expected = N * N_MSGS
	for i in range(len(msgs)):
		print('Log ' + str(i) + ': ' + str(len(msgs[i])) + ' messages (expected ' + str(expected) + ')')
		if len(msgs[i]) != expected:
			print('WARNING: log ' + str(i) + ' has ' + str(len(msgs[i])) + ' messages, expected ' + str(expected))

	unordered = count_unordered_rounds(msgs)
	print ('Found ' + str(unordered) + ' unordered message rounds')

def count_unordered_rounds(logs):
	# Compare every position of every log against logs[0]. A position is unordered if
	# some peer has a different message there or has no message there (shorter log).
	if not logs:
		return 0
	maxLen = max(len(log) for log in logs)
	unordered = 0
	for j in range(0, maxLen):
		firstMsg = logs[0][j] if j < len(logs[0]) else None
		for i in range(1, len(logs)):
			if j >= len(logs[0]) or j >= len(logs[i]) or firstMsg != logs[i][j]:
				unordered = unordered + 1
				break
	return unordered


if __name__ == "__main__":
	server = ReusableSimpleXMLRPCServer(
		('0.0.0.0', SERVER_PORT),
		allow_none=True,
		logRequests=False,
		bind_and_activate=False,
	)
	# Peers submit logs at nearly the same time. The queue has to hold every
	# connection until this process calls handle_request.
	server.request_queue_size = max(N, 16)
	server.server_bind()
	server.server_activate()
	comparisonService = ComparisonService()
	server.register_instance(comparisonService)

	# Initiate server:
	mainLoop()
