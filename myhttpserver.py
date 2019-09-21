import socket
import argparse
import selectors
from utils import ClientInformation, handle_exceptions, log_debug_info, SocketType, settings_parser
from typing import Dict, Tuple, Union, Any, List, Callable
import logging
from handlers import ManageHandlers,HttpBaseHandler

logging.basicConfig(filename='server.log',
                            filemode='a',
                            datefmt='%H:%M:%S',
                            level=logging.DEBUG)
parser = argparse.ArgumentParser()
parser.add_argument('port')
args = parser.parse_args()

HOST = '0.0.0.0'  
PORT = int(args.port)

client_manager = selectors.DefaultSelector()
list_of_sockets = []
    
def accept_new_client(master_socket) -> None:
    new_client_socket, addr = master_socket.accept()
    new_client_socket.setblocking(False)
    client_manager.register(new_client_socket, selectors.EVENT_READ | selectors.EVENT_WRITE, data = ClientInformation(addr,SocketType.CLIENT_SOCKET))
    list_of_sockets.append(new_client_socket)

def handle_client_request(socket_wrapper, events, handlers: List[HttpBaseHandler]) -> None:
    recv_data = None 
    client_socket = socket_wrapper.fileobj
    if events & selectors.EVENT_READ:
        try:
            recv_data = client_socket.recv(1024)
            print(recv_data)
        except (ConnectionResetError, TimeoutError) as e: 
            handle_exceptions(e, socket_wrapper)
            
        if not recv_data:
            close_client_connection(socket_wrapper)
        else:
            print("in sending messages part")
            for handler in handlers:
                response = handler.handle_request(recv_data)
                send_all(client_socket,response)

def send_all(client_socket, response: bytes):
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

def close_client_connection(socket_wrapper) -> None:
    log_debug_info('closing connection', socket_wrapper.data.addr,stdout_print=True)
    client_socket = socket_wrapper.fileobj
    client_manager.unregister(client_socket)
    client_socket.close()
    list_of_sockets.remove(client_socket)         

def init_master_socket() -> None:
    master_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    master_socket.bind((HOST, PORT))
    master_socket.listen()
    master_socket.setblocking(False)
    client_manager.register(master_socket, selectors.EVENT_READ, data=ClientInformation(None,SocketType.MASTER_SOCKET))

# currently gets an http request and passes it to all the relevant handlers. Relevant handlers are the ones that could
# be relevant given the tasks in the settings file. But, the problem is that just because it is a relevant handler
# doesn't mean it can handle that particular request. For example, lets say you have two tasks, serving static and acting as a reverse
# proxy. The reverse proxy handler and static handler are relevant handlers, so every request is passed to both of them. But, these tasks 
# have matching criteria like only serve static if the requested url begins with /images/ or if the request is for the host "gooby.com". Thus, by 
# passing the request to every handler, you are doing the same work over and over, which is making the handler decide
# whether the request matches its criteria. Its not horrible, but i wonder if having something outside make the decision of what handler to send the request
# to is better. Maybe this thing could have access to some mapping of criteria to handlers and only pass the request to the handler if the criteria matches. 
 
def server_loop(handlers: List[HttpBaseHandler]) -> None:
    print("server loop")
    while True:
        ready_sockets = client_manager.select()
        for socket_wrapper, events in ready_sockets:
            if socket_wrapper.data.socket_type == SocketType.MASTER_SOCKET:
                accept_new_client(socket_wrapper.fileobj)
            elif socket_wrapper.data.socket_type == SocketType.CLIENT_SOCKET:
                handle_client_request(socket_wrapper, events, handlers) 

def main() -> None:
    settings = settings_parser()
    task_handlers = ManageHandlers(settings['tasks']).pick_handlers()
    init_master_socket()
    server_loop(task_handlers)

if __name__ == "__main__":
    main()



"""
because there are cases where one handler is picked over the other i feel like its wasteful to pass the request to each of the handlers and make them
parse it and then figure out what they should do and this doesn't work if several handlers reject the request for whatever reasons (am i gonna return multiple
error responses?).I think a better architecture would be to have some sort of mapping that maps patterns of requests to handler so that i dont have to pass
the request to each handler and have them decide whether or not they are responsible for processing the request.

what about conflicts like load balance when you see x, and serve static when u see x?
"""    