import socket
from typing import Dict
import selectors
from handlers.handler_manager import ManageHandlers
from .base_server import BaseServer
from handlers.http_handlers import HttpBaseHandler
from utils.general_utils import ClientInformation, HttpResponse, handle_exceptions, HttpRequest, SocketType, execute_in_new_thread, read_all, send_all
from utils.custom_exceptions import ClientClosingConnection, NotValidHttpFormat
from queue import Queue
import threading

class ThreadPerRequest(BaseServer):
    """
    This implementation of the server uses a threadpool to respond to requests in a queue, but the clients
    themselves are not given their own threads.
    """
    def __init__(self, settings: Dict, host: str = '0.0.0.0', port: int = 9999):
        super().__init__(settings, host, port)
        self.client_manager = selectors.KqueueSelector()
        # The reason for this set is that every request is handled in its own thread but before
        # the socket is able to be read, control is yeilded to the main thread which picks which sockets are to be read.
        # Since the sub thread hasn't yet exhausted that client socket, the main thread thinks that socket needs to be serviced. 
        # So this set prevents that by only servicing client sockets not currently in the set.
        self.clients_currently_being_serviced = set() 
        self.clients_to_be_serviced = Queue()
    
    def get_type(self) -> str:
        return 'threadperrequest'

    def init_master_socket(self) -> None:
        super().init_master_socket()
        self.client_manager.register(self.master_socket, selectors.EVENT_READ, data=ClientInformation(SocketType.MASTER_SOCKET))
    
    def start_threads(self):
        for _ in range(50):
            threading.Thread(target=self.handle_client).start()

    def loop_forever(self) -> None:
        self.start_threads()
        while True:
            ready_sockets = self.client_manager.select()
            for socket_wrapper, events in ready_sockets:
                if socket_wrapper.data.socket_type == SocketType.MASTER_SOCKET:
                    #wonder if i should put this in the queue too
                    master_socket = socket_wrapper.fileobj
                    new_client_socket, addr = master_socket.accept()
                    self.accept_new_client(new_client_socket)
                elif socket_wrapper.data.socket_type == SocketType.CLIENT_SOCKET:
                    client_socket = socket_wrapper.fileobj
                    if client_socket not in self.clients_currently_being_serviced and not client_socket._closed:
                        self.clients_currently_being_serviced.add(client_socket)
                        self.clients_to_be_serviced.put(client_socket)
        
    def accept_new_client(self, new_client) -> None:
        self.client_manager.register(new_client, selectors.EVENT_READ, data = ClientInformation(socket_type=SocketType.CLIENT_SOCKET))
    
    def handle_client(self):
        while True:
            client_socket = self.clients_to_be_serviced.get()
            try:
                raw_client_request = read_all(client_socket)
                http_request = HttpRequest.from_bytes(raw_client_request)
                http_response = self.handle_client_request(http_request)
                send_all(client_socket, http_response.dump())
            except (ClientClosingConnection, socket.timeout, ConnectionResetError, TimeoutError, BrokenPipeError):
                self.close_client_connection(client_socket)
    
            self.clients_currently_being_serviced.remove(client_socket)

    def close_client_connection(self, client_socket) -> None:
        self.client_manager.unregister(client_socket)
        client_socket.close()      