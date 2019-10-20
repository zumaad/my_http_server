
from typing import Dict
import socket
from handlers.http_handlers import HttpBaseHandler
from handlers.handler_manager import ManageHandlers
from utils.general_utils import HttpResponse, handle_exceptions,parse_http_request
from utils.custom_exceptions import ClientClosingConnection
from abc import ABC, abstractmethod


class BaseServer(ABC):

    def __init__(self, settings: Dict, host: str = '0.0.0.0', port: int = 9999):
        self.host = host
        self.port = port
        self.request_handlers = ManageHandlers(settings, self.update_statistics).prepare_handlers()
        self.statistics = {'bytes_sent':0, 'bytes_recv':0, 'requests_recv':0, 'responses_sent':0}
        print(f'listening on port {self.port}')
    
    def init_master_socket(self):
        master_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        master_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        master_socket.bind((self.host, self.port))
        master_socket.listen()
        self.master_socket = master_socket
        
    def send_all(self, client_socket, response: bytes) -> None:
        """ 
        I can't just use the sendall method on the socket object because it throws an error when it can't send
        all the bytes for whatever reason (typically other socket isn't ready for reading i guess) and you can't just catch
        the error and try again because you have no clue how many bytes were actually written. However, using the send
        method gives you much finer control as it returns how many bytes were written, so if all the bytes couldn't be written
        you can truncate your message accordingly and repeat.  
        """
        BUFFER_SIZE = 1024 * 16
        while response:
            try:
                bytes_sent = client_socket.send(response[:BUFFER_SIZE])
                if bytes_sent < BUFFER_SIZE:
                    response = response[bytes_sent:]
                else:
                    response = response[BUFFER_SIZE:]
            except BlockingIOError: 
                continue

    def handle_client_request(self, client_socket) -> None:
        raw_request = None 
        raw_request = client_socket.recv(1024)
        #clients (such as browsers) will send an empty message when they are closing
        #their side of the connection.
        if not raw_request: 
           raise ClientClosingConnection("client is closing its side of the connection, clean up connection")
        else:
            self.on_received_data(client_socket, raw_request)

    def on_received_data(self, client_socket, raw_data):
        http_request = parse_http_request(raw_data)
        self.update_statistics(responses_sent=1, requests_recv=1)
        for handler in self.request_handlers:
            if handler.should_handle(http_request):
                handler.raw_http_request = raw_data
                http_response = handler.handle_request()
                self.send_all(client_socket, http_response)
                break
        else:
            http_error_response = HttpResponse(400, 'No handler could handle your request, check the matching criteria in settings.py').dump()
            self.send_all(client_socket, http_error_response)
    
    def start_loop(self) -> None:
        self.init_master_socket()
        self.loop_forever()
    
    def stop_loop(self) -> None:
        self.master_socket.close()
        print(self.statistics)
    
    def update_statistics(self, **statistics) -> None:
        for statistic_name, statistic_value in statistics.items():
            if statistic_name in self.statistics:
                self.statistics[statistic_name] += statistic_value
            else:
                self.statistics[statistic_name] = statistic_value

    @abstractmethod
    def close_client_connection(self, client_socket) -> None:
        pass
    
    @abstractmethod
    def loop_forever(self) -> None:
        pass

    @abstractmethod
    def handle_client(self, client) -> None:
        pass

    @abstractmethod
    def accept_new_client(self, new_client) -> None:
        pass