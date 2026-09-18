#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <iostream>
#include <string>

#include "telemetry.hpp"

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

// Value of a request header (case-insensitive name), or "" if absent.
std::string headerValue(const std::string& request, std::string name) {
    std::string lowerReq = request;
    std::transform(lowerReq.begin(), lowerReq.end(), lowerReq.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    std::transform(name.begin(), name.end(), name.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });

    const std::string needle = "\r\n" + name + ":";
    const auto pos = lowerReq.find(needle);
    if (pos == std::string::npos) {
        return "";
    }
    const auto start = pos + needle.size();
    const auto end = request.find("\r\n", start);
    std::string value = request.substr(start, end == std::string::npos ? std::string::npos : end - start);
    const auto first = value.find_first_not_of(" \t");
    const auto last = value.find_last_not_of(" \t");
    return first == std::string::npos ? "" : value.substr(first, last - first + 1);
}

std::string requestMethod(const std::string& request) {
    const auto sp = request.find(' ');
    return sp == std::string::npos ? "UNKNOWN" : request.substr(0, sp);
}

std::atomic<std::uint64_t> requestCount{0};

} // namespace

int main() {
    std::cout << std::unitbuf;  // logs must appear immediately in containers
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

    telemetry::Emitter emitter;

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

        const auto startedAt = std::chrono::steady_clock::now();
        std::string request(buffer);
        std::string body;
        std::string route;
        requestCount.fetch_add(1);

        if (request.find("GET /health") != std::string::npos) {

            route = "/health";

            body =
                "{"
                "\"service\":\"cpp-service\","
                "\"status\":\"UP\""
                "}";

        } else if (request.find("GET /metrics") != std::string::npos) {

            route = "/metrics";
            emitter.poll();
            body =
                "{"
                "\"service\":\"cpp-service\","
                "\"requests\":" + std::to_string(requestCount.load()) + ","
                "\"telemetry_dropped\":" + std::to_string(telemetry::Emitter::dropped().load()) + ","
                "\"status\":\"UP\""
                "}";

        } else {

            route = "*";
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

        telemetry::Event event;
        event.service = emitter.serviceName();
        event.request_id = headerValue(request, "X-Request-ID");
        if (event.request_id.empty()) {
            event.request_id = telemetry::newUuid();
        }
        event.trace_id = headerValue(request, "X-Trace-Id");
        if (event.trace_id.empty()) {
            event.trace_id = event.request_id;
        }
        event.latency_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - startedAt).count();
        event.status_code = 200;  // this service answers every request with 200
        event.method = requestMethod(request);
        event.path = route;
        emitter.emit(event);

        closeSocket(clientSocket);
    }

    closeSocket(serverSocket);
#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}
