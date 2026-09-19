#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <iostream>
#include <string>
#include <vector>

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

// Number of primes <= n (sieve of Eratosthenes). Real CPU work, deterministic:
// pi(100000) = 9592, pi(1000000) = 78498.
std::uint64_t countPrimes(std::uint64_t n) {
    if (n < 2) {
        return 0;
    }
    std::vector<bool> composite(n + 1, false);
    std::uint64_t count = 0;
    for (std::uint64_t i = 2; i <= n; ++i) {
        if (!composite[i]) {
            ++count;
            for (std::uint64_t j = i * i; j <= n; j += i) {
                composite[j] = true;
            }
        }
    }
    return count;
}

enum class QueryResult { Missing, Valid, Invalid };

// Parse an unsigned integer query parameter from the request line ("GET /x?n=5 HTTP/1.1").
QueryResult queryUint(const std::string& request, const std::string& key, std::uint64_t& out) {
    const std::string line = request.substr(0, request.find("\r\n"));
    const auto qmark = line.find('?');
    if (qmark == std::string::npos) {
        return QueryResult::Missing;
    }
    const auto end = line.find(' ', qmark);
    const std::string query = line.substr(qmark + 1, end == std::string::npos ? std::string::npos : end - qmark - 1);

    const std::string needle = key + "=";
    std::size_t pos = 0;
    while (pos <= query.size()) {
        const auto amp = query.find('&', pos);
        const std::string pair = query.substr(pos, amp == std::string::npos ? std::string::npos : amp - pos);
        if (pair.compare(0, needle.size(), needle) == 0) {
            const std::string value = pair.substr(needle.size());
            if (value.empty() || value.size() > 12 ||
                !std::all_of(value.begin(), value.end(), [](unsigned char c) { return std::isdigit(c) != 0; })) {
                return QueryResult::Invalid;
            }
            out = std::stoull(value);
            return QueryResult::Valid;
        }
        if (amp == std::string::npos) {
            break;
        }
        pos = amp + 1;
    }
    return QueryResult::Missing;
}

constexpr std::uint64_t kMaxComputeN = 5000000;
constexpr std::uint64_t kDefaultComputeN = 100000;

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
        int status = 200;
        const char* reason = "OK";
        requestCount.fetch_add(1);

        if (request.find("GET /compute") != std::string::npos) {

            route = "/compute";
            std::uint64_t n = kDefaultComputeN;
            const QueryResult parsed = queryUint(request, "n", n);

            if (parsed == QueryResult::Invalid || n < 1 || n > kMaxComputeN) {
                status = 400;
                reason = "Bad Request";
                body =
                    "{"
                    "\"service\":\"cpp-service\","
                    "\"error\":\"n must be an integer between 1 and " + std::to_string(kMaxComputeN) + "\""
                    "}";
            } else {
                const auto computeStart = std::chrono::steady_clock::now();
                const std::uint64_t primes = countPrimes(n);
                const double computeMs = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - computeStart).count();
                char ms[32];
                std::snprintf(ms, sizeof(ms), "%.3f", computeMs);
                body =
                    "{"
                    "\"service\":\"cpp-service\","
                    "\"n\":" + std::to_string(n) + ","
                    "\"primes\":" + std::to_string(primes) + ","
                    "\"compute_ms\":" + ms +
                    "}";
            }

        } else if (request.find("GET /health") != std::string::npos) {

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
            "HTTP/1.1 " + std::to_string(status) + " " + reason +
            "\r\n"
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
        event.status_code = status;
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
