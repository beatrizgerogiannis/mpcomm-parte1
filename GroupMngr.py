from xmlrpc.server import SimpleXMLRPCServer
from constMP import *

# Group membership only. Peers still exchange messages over UDP.


class ReusableSimpleXMLRPCServer(SimpleXMLRPCServer):
  # The port can be bound again right after a restart, as with the old socket.
  allow_reuse_address = True


def register_peer(membership, ipaddr, port):
  """Register a peer once. Identity is the IP address.

  A second call with the same IP does not append another entry, so a
  restarted peer does not show up twice in list(). The same IP and port
  leaves the list unchanged. The same IP with a new port replaces the port.
  Returns 'registered', 'unchanged', or 'updated'.
  """
  port = int(port)
  for i, (ip, existing) in enumerate(membership):
    if ip == ipaddr and existing == port:
      return 'unchanged'
    if ip == ipaddr:
      membership[i] = (ipaddr, port)
      return 'updated'
  membership.append((ipaddr, port))
  return 'registered'


def unregister_peer(membership, ipaddr, port):
  """Remove the entry (ipaddr, port). A missing entry is left as it is.

  The port is part of the key, so a late unregister from an old process
  does not remove a peer that has already registered again with another port.
  Returns True when an entry was removed.
  """
  port = int(port)
  entry = (ipaddr, port)
  if entry in membership:
    membership.remove(entry)
    return True
  return False


def list_peer_ips(membership):
  return [ip for (ip, port) in membership]


class GroupManagerService:
  def __init__(self):
    self._membership = []
    self._stopped = False

  def register(self, ipaddr, port):
    result = register_peer(self._membership, ipaddr, port)
    described = {'ipaddr': ipaddr, 'port': int(port)}
    if result == 'registered':
      print('Registered peer: ', described)
    elif result == 'updated':
      print('Updated peer registration: ', described)
    else:
      print('Peer already registered: ', described)
    return True

  def unregister(self, ipaddr, port):
    described = {'ipaddr': ipaddr, 'port': int(port)}
    if unregister_peer(self._membership, ipaddr, port):
      print('Unregistered peer: ', described)
    else:
      print('Peer was not registered: ', described)
    return True

  def list(self):
    peers = list_peer_ips(self._membership)
    print('List of peers sent to server: ', peers)
    return peers

  def stop(self):
    print('Stopping.')
    self._stopped = True
    return True


def serve(server, service):
  server.register_instance(service)
  # One request at a time, same as the previous accept loop.
  # stop() only flips the flag; the response is sent before the next check.
  while not service._stopped:
    server.handle_request()
  server.server_close()


def serverLoop():
  server = ReusableSimpleXMLRPCServer(
    ('0.0.0.0', GROUPMNGR_TCP_PORT),
    allow_none=True,
    logRequests=False,
  )
  serve(server, GroupManagerService())


if __name__ == '__main__':
  serverLoop()
