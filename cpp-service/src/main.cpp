#include <algorithm>
#include <atomic>
#include <cctype>
#include <csignal>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <random>
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

// Read one HTTP request: headers, then Content-Length bytes of body. A client may send them in
// separate packets, so a single recv() is not enough. The receive timeout keeps idle or
// half-open connections from blocking this single-threaded server.
std::string readRequest(SOCKET client) {
#ifdef _WIN32
    DWORD timeoutMs = 2000;
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeoutMs), sizeof(timeoutMs));
#else
    timeval tv{};
    tv.tv_sec = 2;
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
#endif
    constexpr std::size_t kMaxRequest = 16384;
    std::string data;
    char chunk[4096];
    while (data.size() < kMaxRequest) {
        const int n = static_cast<int>(recv(client, chunk, sizeof(chunk), 0));
        if (n <= 0) {
            break;
        }
        data.append(chunk, static_cast<std::size_t>(n));
        const auto headerEnd = data.find("\r\n\r\n");
        if (headerEnd == std::string::npos) {
            continue;
        }
        std::size_t want = 0;
        const std::string length = headerValue(data, "Content-Length");
        if (!length.empty() && std::all_of(length.begin(), length.end(), [](unsigned char c) { return std::isdigit(c) != 0; })) {
            want = std::min<std::size_t>(std::stoul(length), kMaxRequest);
        }
        if (data.size() >= headerEnd + 4 + want) {
            break;
        }
    }
    return data;
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

std::atomic<bool> stopRequested{false};

// ---- HTTP-error fault hook (driven by the fault agent; self-expiring) ---------------------------
std::atomic<double> hookRate{0.0};
std::atomic<int> hookStatus{500};
std::atomic<long long> hookExpiryMs{0};  // steady-clock milliseconds

long long steadyMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

bool hookActive() {
    return hookRate.load() > 0.0 && steadyMs() < hookExpiryMs.load();
}

// Status to inject for this request, or 0 to let it through.
int hookShouldFail() {
    static thread_local std::mt19937 rng{std::random_device{}()};
    if (!hookActive()) {
        return 0;
    }
    return std::uniform_real_distribution<double>(0.0, 1.0)(rng) < hookRate.load() ? hookStatus.load() : 0;
}

std::string hookStateJson() {
    const bool active = hookActive();
    char rate[32];
    std::snprintf(rate, sizeof(rate), "%.3f", active ? hookRate.load() : 0.0);
    const long long remaining = active ? (hookExpiryMs.load() - steadyMs()) / 1000 : 0;
    return std::string("{\"active\":") + (active ? "true" : "false") + ",\"rate\":" + rate +
           ",\"status\":" + std::to_string(hookStatus.load()) + ",\"expires_in_s\":" + std::to_string(remaining) + "}";
}

// Numeric value of `"key": <number>` in a flat JSON body; false if absent.
bool jsonNumber(const std::string& body, const std::string& key, double& out) {
    const auto pos = body.find("\"" + key + "\"");
    if (pos == std::string::npos) {
        return false;
    }
    const auto colon = body.find(':', pos);
    if (colon == std::string::npos) {
        return false;
    }
    char* end = nullptr;
    out = std::strtod(body.c_str() + colon + 1, &end);
    return end != body.c_str() + colon + 1;
}

extern "C" void onStopSignal(int) {
    stopRequested.store(true);
}

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

    // Graceful shutdown: no SA_RESTART, so accept() returns EINTR and the loop can exit;
    // leaving main() then runs the emitter destructor, which flushes pending telemetry.
#ifdef _WIN32
    std::signal(SIGINT, onStopSignal);
#else
    struct sigaction sa {};
    sa.sa_handler = onStopSignal;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGINT, &sa, nullptr);
#endif

    while (!stopRequested.load()) {
        SOCKET clientSocket = accept(serverSocket, nullptr, nullptr);

        if (clientSocket == INVALID_SOCKET) {
            continue;  // EINTR on a stop signal: the loop condition decides
        }

        std::string request = readRequest(clientSocket);
        const auto startedAt = std::chrono::steady_clock::now();
        std::string body;
        std::string route;
        int status = 200;
        const char* reason = "OK";
        requestCount.fetch_add(1);

        const bool hookRoute = request.rfind("POST /_faults/http-error", 0) == 0 ||
                               request.rfind("DELETE /_faults/http-error", 0) == 0 ||
                               request.rfind("GET /_faults/http-error", 0) == 0;
        int injected = 0;

        if (hookRoute) {

            route = "/_faults/http-error";
            const char* configured = std::getenv("FAULT_HOOK_TOKEN");
            const std::string token = configured ? configured : "";
            if (token.empty()) {
                status = 503;
                reason = "Service Unavailable";
                body = "{\"error\":\"fault hook disabled: FAULT_HOOK_TOKEN not set\"}";
            } else if (headerValue(request, "X-Fault-Token") != token) {
                status = 401;
                reason = "Unauthorized";
                body = "{\"error\":\"invalid fault hook token\"}";
            } else if (request.rfind("DELETE", 0) == 0) {
                hookRate.store(0.0);
                hookExpiryMs.store(0);
                body = hookStateJson();
            } else if (request.rfind("POST", 0) == 0) {
                const auto bodyStart = request.find("\r\n\r\n");
                const std::string payload = bodyStart == std::string::npos ? "" : request.substr(bodyStart + 4);
                double rate = 0, statusCode = 500, ttl = 60;
                jsonNumber(payload, "status", statusCode);
                jsonNumber(payload, "ttl_s", ttl);
                if (!jsonNumber(payload, "rate", rate) || rate <= 0.0 || rate > 1.0 ||
                    statusCode < 500 || statusCode > 599 || ttl < 1 || ttl > 900) {
                    status = 422;
                    reason = "Unprocessable Entity";
                    body = "{\"error\":\"rate in (0,1], status 500-599, ttl_s 1-900\"}";
                } else {
                    hookStatus.store(static_cast<int>(statusCode));
                    hookExpiryMs.store(steadyMs() + static_cast<long long>(ttl) * 1000);
                    hookRate.store(rate);
                    body = hookStateJson();
                }
            } else {
                body = hookStateJson();
            }

        } else if (request.find("GET /compute") != std::string::npos) {

            route = "/compute";
            std::uint64_t n = kDefaultComputeN;
            const QueryResult parsed = queryUint(request, "n", n);
            injected = hookShouldFail();

            if (injected != 0) {
                status = injected;
                reason = "Injected Fault";
                body = "{\"service\":\"cpp-service\",\"error\":\"injected_fault\"}";
            } else if (parsed == QueryResult::Invalid || n < 1 || n > kMaxComputeN) {
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
        if (injected != 0) {
            event.error_type = "injected_fault";
        }
        event.method = requestMethod(request);
        event.path = route;
        emitter.emit(event);

        closeSocket(clientSocket);
    }

    std::cout << "Aegis C++ Service stopping\n";
    closeSocket(serverSocket);
#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}
