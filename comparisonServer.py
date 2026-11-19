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

	# Check log sizes: each peer should have received N_MSGS messages from each of the N peers
	expected = N * N_MSGS
	for i in range(len(msgs)):
		print('Log ' + str(i) + ': ' + str(len(msgs[i])) + ' messages (expected ' + str(expected) + ')')
		if len(msgs[i]) != expected:
			print('WARNING: log ' + str(i) + ' has ' + str(len(msgs[i])) + ' messages, expected ' + str(expected))

	# --- Metric 1: FIFO and absence of loss per sender (Issue #6) ---
	fifo_results = check_fifo_and_completeness(msgs, N_MSGS, N)
	print('\n=== Metric 1: FIFO and Completeness Check (Per-Sender Order) ===')
	all_ok = True
	for i, peer_result in enumerate(fifo_results):
		status = 'OK' if peer_result['ok'] else 'FAIL'
		print('  Peer log ' + str(i) + ': ' + status)
		for problem in peer_result['problems']:
			print('    - ' + problem)
		if not peer_result['ok']:
			all_ok = False
	if all_ok:
		print('  All peers delivered every message (0..' + str(N_MSGS-1) + ') without loss, duplicates, or FIFO violations.')
	else:
		print('  WARNING: Some peers failed FIFO or completeness check!')

	# --- Metric 2: Total order (Original metric, kept for comparison) ---
	unordered = count_unordered_rounds(msgs)
	print('\n=== Metric 2: Total Order Check (Global Interleaving) ===')
	print('  Found ' + str(unordered) + ' unordered message rounds')
	print('  (Demonstration: FIFO order != Total order. Messages from different senders')
	print('   may be delivered in different orders across peers without violating FIFO.)\n')

def check_fifo_and_completeness(logs, n_msgs, n_peers=None):
	"""
	For every peer log, verify that for each sender:
	  - Delivered messages are exactly 0..n_msgs-1
	  - Without gaps / losses (all expected seq numbers present)
	  - Without duplicates
	  - In FIFO order per sender (strictly ascending order of sequence numbers)

	Returns a list (one entry per log) of dicts:
	  {'ok': bool, 'problems': [str]}
	"""
	if n_peers is None:
		n_peers = N

	results = []
	for log_idx, log in enumerate(logs):
		problems = []
		# Group messages by sender
		messages_by_sender = {s: [] for s in range(n_peers)}
		for entry in log:
			sender, seq = entry[0], entry[1]
			if sender not in messages_by_sender:
				messages_by_sender[sender] = []
			messages_by_sender[sender].append(seq)

		for sender in range(n_peers):
			seqs = messages_by_sender.get(sender, [])

			# Check duplicates
			seen = set()
			duplicates = []
			for seq in seqs:
				if seq in seen and seq not in duplicates:
					duplicates.append(seq)
				seen.add(seq)
			if duplicates:
				problems.append(
					'duplicate messages from sender ' + str(sender) + ': seqs ' + str(duplicates))

			# Check FIFO order (strictly ascending per sender)
			last_seq = -1
			order_violations = []
			for seq in seqs:
				if last_seq != -1 and seq <= last_seq:
					order_violations.append((last_seq, seq))
				last_seq = seq
			if order_violations:
				problems.append(
					'FIFO violations from sender ' + str(sender) + ' (earlier, later): ' + str(order_violations))

			# Check missing / gaps
			expected_seqs = set(range(n_msgs))
			missing = sorted(expected_seqs - seen)
			if missing:
				problems.append(
					'missing seqs from sender ' + str(sender) + ': ' + str(missing))

			extra = sorted(seen - expected_seqs)
			if extra:
				problems.append(
					'unexpected seqs from sender ' + str(sender) + ': ' + str(extra))

		results.append({'ok': len(problems) == 0, 'problems': problems})
	return results

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
	serverSock = socket(AF_INET, SOCK_STREAM)
	# Allow immediate reuse of the port after closing
	serverSock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
	serverSock.bind(('0.0.0.0', SERVER_PORT))
	serverSock.listen(6)

	# Initiate server:
	mainLoop()
