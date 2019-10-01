import socket
import argparse
import selectors
from utils import ClientInformation, handle_exceptions, log_debug_info, SocketType, settings_parser, parse_http_request, HttpResponse
from typing import Dict, Tuple, Union, Any, List, Callable
import logging
from handlers import ManageHandlers,HttpBaseHandler
from settings import settings_map


logging.basicConfig(filename='server.log',
                            filemode='a',
                            datefmt='%H:%M:%S',
                            level=logging.DEBUG)
parser = argparse.ArgumentParser()
parser.add_argument('--port','-p',type=int)
parser.add_argument('--settings','-s',type=int)
args = parser.parse_args()  

class Server:
    def __init__(self, handlers:List[HttpBaseHandler], host = '0.0.0.0', port=9999):
        self.request_handlers = handlers
        self.client_manager = selectors.DefaultSelector()
        self.host = host
        self.port = port
        self.statistics = {'bytes_sent':0, 'bytes_recv':0, 'requests_recv':0, 'responses_sent':0}
        
    def accept_new_client(self, master_socket) -> None:
        new_client_socket, addr = master_socket.accept()
        new_client_socket.setblocking(False)
        self.client_manager.register(new_client_socket, selectors.EVENT_READ | selectors.EVENT_WRITE, data = ClientInformation(addr,SocketType.CLIENT_SOCKET))

    def handle_client_request(self, socket_wrapper, events) -> None:
        recv_data = None 
        client_socket = socket_wrapper.fileobj
        if events & selectors.EVENT_READ:
            try:
                recv_data = client_socket.recv(1024)
            except (ConnectionResetError, TimeoutError) as e: 
                handle_exceptions(e, socket_wrapper)
                
            if not recv_data:
                self.close_client_connection(socket_wrapper)
            else:
                http_request = parse_http_request(recv_data)
                log_debug_info("received raw data ->",recv_data)
                log_debug_info("parsed raw data into ->", repr(http_request))
                self.update_statistics(responses_sent=1, requests_recv=1)
                
                for handler in self.request_handlers:
                    if handler.should_handle(http_request):
                        handler.raw_http_request = recv_data
                        http_response = handler.handle_request()
                        self.update_statistics(bytes_recv=len(recv_data), bytes_sent=len(http_response))
                        self.send_all(client_socket,http_response)
                        return

                http_error_response = HttpResponse(400, 'No handler could handle your request, check the matching criteria in settings.py').dump()
                self.update_statistics(bytes_sent=len(http_error_response))
                self.send_all(client_socket, http_error_response)

    def update_statistics(self, **statistics) -> None:
        for statistic_name, statistic_value in statistics.items():
            self.statistics[statistic_name] += statistic_value

    def send_all(self, client_socket, response: bytes) -> None:
        """ I can't just use the sendall method on the socket object because it throws an error when it can't send
            all the bytes for whatever reason (typically other socket isn't ready for reading i guess) and you can't just catch
            the error and try again because you have no clue how many bytes were actually written. However, using the send
            method gives you much finer control as it returns how many bytes were written, so if all the bytes couldn't be written
            you can truncate your message accordingly and repeat.  """
        BUFFER_SIZE = 1024 #is this optimal, i have no clue :), should research what a good buffer size is.
        while response:
            bytes_sent = client_socket.send(response[:BUFFER_SIZE])
            if bytes_sent < BUFFER_SIZE:
                response = response[bytes_sent:]
            else:
                response = response[BUFFER_SIZE:]

    def close_client_connection(self, socket_wrapper) -> None:
        log_debug_info('closing connection', socket_wrapper.data.addr,stdout_print=True)
        client_socket = socket_wrapper.fileobj
        self.client_manager.unregister(client_socket)
        client_socket.close()      

    def init_master_socket(self) -> None:
        master_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        master_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        master_socket.bind((self.host, self.port))
        master_socket.listen()
        master_socket.setblocking(False)
        self.master_socket = master_socket
        self.client_manager.register(master_socket, selectors.EVENT_READ, data=ClientInformation(None,SocketType.MASTER_SOCKET))
    
    def loop_forever(self) -> None:
        while True:
            ready_sockets = self.client_manager.select()
            for socket_wrapper, events in ready_sockets:
                if socket_wrapper.data.socket_type == SocketType.MASTER_SOCKET:
                    self.accept_new_client(socket_wrapper.fileobj)
                elif socket_wrapper.data.socket_type == SocketType.CLIENT_SOCKET:
                    self.handle_client_request(socket_wrapper, events)

    def start_loop(self) -> None:
        self.init_master_socket()
        self.loop_forever()
    
    def stop_loop(self) -> None:
        self.master_socket.close()
        print(self.statistics)
    
def main() -> None:
    relevant_task_handlers = ManageHandlers(settings_map[args.settings]).pick_handlers()
    server = Server(relevant_task_handlers, port=args.port)
    try:
        server.start_loop()
    except KeyboardInterrupt:
        print("Stopping server :(")
        server.stop_loop()

if __name__ == "__main__":
    main()