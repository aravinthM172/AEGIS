#include <iostream>
#include <string>

#ifdef _WIN32
    #include <winsock2.h>
    #pragma comment(lib, "ws2_32.lib")
#else
    #include <arpa/inet.h>
    #include <sys/socket.h>
    #include <unistd.h>
    using SOCKET = int;
    constexpr int INVALID_SOCKET = -1;
    constexpr int SOCKET_ERROR = -1;
#endif

namespace {

void closeSocket(SOCKET s) {
#ifdef _WIN32
    closesocket(s);
#else
    close(s);
#endif
}

} // namespace

int main() {
#ifdef _WIN32
    WSADATA wsaData;

    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        std::cerr << "WSAStartup failed\n";
        return 1;
    }
#endif

    SOCKET serverSocket = socket(AF_INET, SOCK_STREAM, 0);

    if (serverSocket == INVALID_SOCKET) {
        std::cerr << "Socket creation failed\n";
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

#ifndef _WIN32
    int reuse = 1;
    setsockopt(serverSocket, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
#endif

    sockaddr_in serverAddress{};
    serverAddress.sin_family = AF_INET;
    serverAddress.sin_addr.s_addr = INADDR_ANY;
    serverAddress.sin_port = htons(8082);

    if (bind(
            serverSocket,
            reinterpret_cast<sockaddr*>(&serverAddress),
            sizeof(serverAddress)) == SOCKET_ERROR) {

        std::cerr << "Bind failed\n";
        closeSocket(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    if (listen(serverSocket, 5) == SOCKET_ERROR) {
        std::cerr << "Listen failed\n";
        closeSocket(serverSocket);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    std::cout << "Aegis C++ Service started\n";
    std::cout << "Service: cpp-service\n";
    std::cout << "Listening on port: 8082\n";

    while (true) {
        SOCKET clientSocket = accept(serverSocket, nullptr, nullptr);

        if (clientSocket == INVALID_SOCKET) {
            continue;
        }

        char buffer[4096] = {};

        recv(
            clientSocket,
            buffer,
            sizeof(buffer) - 1,
            0
        );

        std::string request(buffer);
        std::string body;

        if (request.find("GET /health") != std::string::npos) {

            body =
                "{"
                "\"service\":\"cpp-service\","
                "\"status\":\"UP\""
                "}";

        } else if (request.find("GET /metrics") != std::string::npos) {

            body =
                "{"
                "\"service\":\"cpp-service\","
                "\"requests\":1,"
                "\"status\":\"UP\""
                "}";

        } else {

            body =
                "{"
                "\"service\":\"cpp-service\","
                "\"status\":\"OK\""
                "}";
        }

        std::string response =
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: " +
            std::to_string(body.size()) +
            "\r\n"
            "Connection: close\r\n"
            "\r\n" +
            body;

        send(
            clientSocket,
            response.c_str(),
            static_cast<int>(response.size()),
            0
        );

        closeSocket(clientSocket);
    }

    closeSocket(serverSocket);
#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}
