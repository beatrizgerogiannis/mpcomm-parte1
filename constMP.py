import os

PEER_UDP_PORT = 6789
PEER_TCP_PORT = 5679
N = int(os.environ.get('N_PEERS', 6))   # Number of peers
SERVER_ADDR = os.environ.get('SERVER_ADDR', '34.203.80.210')
SERVER_PORT = 5678
GROUPMNGR_ADDR = os.environ.get('GROUPMNGR_ADDR', '34.203.80.210')
GROUPMNGR_TCP_PORT = 5680
