// FaultScope telemetry for the C++ service.
// Emits TelemetryEvent JSON (same fields as app/telemetry.py) to Kafka via
// librdkafka. Built without Kafka (FAULTSCOPE_HAVE_KAFKA undefined) the
// emitter is a no-op that counts every event as dropped.
#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <iostream>
#include <random>
#include <string>

#ifdef FAULTSCOPE_HAVE_KAFKA
#include <librdkafka/rdkafka.h>
#endif

namespace telemetry {

struct Event {
    std::string service;
    std::string request_id;
    std::string trace_id;
    double latency_ms = 0.0;
    int status_code = 0;
    std::string error_type;  // empty -> JSON null
    std::string method;
    std::string path;
};

inline std::string envOr(const char* name, const std::string& fallback) {
    const char* value = std::getenv(name);
    return (value && *value) ? std::string(value) : fallback;
}

inline std::string newUuid() {
    static thread_local std::mt19937_64 rng{std::random_device{}()};
    std::uint64_t a = rng(), b = rng();
    a = (a & 0xFFFFFFFFFFFF0FFFULL) | 0x0000000000004000ULL;  // version 4
    b = (b & 0x3FFFFFFFFFFFFFFFULL) | 0x8000000000000000ULL;  // variant 10
    char buf[37];
    std::snprintf(buf, sizeof(buf), "%08x-%04x-%04x-%04x-%012llx",
                  static_cast<unsigned>(a >> 32),
                  static_cast<unsigned>((a >> 16) & 0xFFFF),
                  static_cast<unsigned>(a & 0xFFFF),
                  static_cast<unsigned>(b >> 48),
                  static_cast<unsigned long long>(b & 0xFFFFFFFFFFFFULL));
    return buf;
}

inline std::string nowIso8601() {
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto ms = duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000;
    const std::time_t secs = system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &secs);
#else
    gmtime_r(&secs, &tm);
#endif
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                  tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                  tm.tm_hour, tm.tm_min, tm.tm_sec, static_cast<int>(ms));
    return buf;
}

inline std::string jsonEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char esc[8];
                    std::snprintf(esc, sizeof(esc), "\\u%04x", c);
                    out += esc;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    return out;
}

inline std::string toJson(const Event& e) {
    const char* severity = e.status_code >= 500 ? "ERROR" : e.status_code >= 400 ? "WARN" : "INFO";
    char latency[32];
    std::snprintf(latency, sizeof(latency), "%.3f", e.latency_ms);

    std::string j = "{";
    j += "\"event_id\":\"" + newUuid() + "\",";
    j += "\"timestamp\":\"" + nowIso8601() + "\",";
    j += "\"service\":\"" + jsonEscape(e.service) + "\",";
    j += "\"event_type\":\"http_request\",";
    j += std::string("\"severity\":\"") + severity + "\",";
    j += "\"request_id\":\"" + jsonEscape(e.request_id) + "\",";
    j += "\"trace_id\":\"" + jsonEscape(e.trace_id) + "\",";
    j += std::string("\"latency_ms\":") + latency + ",";
    j += "\"status_code\":" + std::to_string(e.status_code) + ",";
    j += e.error_type.empty() ? "\"error_type\":null," : "\"error_type\":\"" + jsonEscape(e.error_type) + "\",";
    j += "\"metadata\":{\"method\":\"" + jsonEscape(e.method) + "\",\"path\":\"" + jsonEscape(e.path) + "\"}";
    j += "}";
    return j;
}

class Emitter {
public:
    Emitter()
        : topic_(envOr("TELEMETRY_TOPIC", "telemetry")),
          service_(envOr("SERVICE_NAME", "cpp-service")) {
#ifdef FAULTSCOPE_HAVE_KAFKA
        const std::string brokers = envOr("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092");
        char errstr[512];
        rd_kafka_conf_t* conf = rd_kafka_conf_new();

        auto set = [&](const char* key, const std::string& value) {
            if (rd_kafka_conf_set(conf, key, value.c_str(), errstr, sizeof(errstr)) != RD_KAFKA_CONF_OK) {
                std::cerr << "telemetry: kafka config " << key << ": " << errstr << "\n";
            }
        };
        set("bootstrap.servers", brokers);
        set("client.id", service_);
        set("linger.ms", "20");
        set("message.timeout.ms", "5000");  // give up on undeliverable events instead of buffering forever
        rd_kafka_conf_set_dr_msg_cb(conf, &Emitter::onDelivery);

        rk_ = rd_kafka_new(RD_KAFKA_PRODUCER, conf, errstr, sizeof(errstr));
        if (!rk_) {
            std::cerr << "telemetry: kafka producer init failed: " << errstr << "\n";
            rd_kafka_conf_destroy(conf);  // ownership stays with us on failure
        } else {
            std::cout << "telemetry: emitting to " << brokers << " topic=" << topic_ << "\n";
        }
#else
        std::cerr << "telemetry: built without Kafka support; events are dropped\n";
#endif
    }

    ~Emitter() {
#ifdef FAULTSCOPE_HAVE_KAFKA
        if (rk_) {
            rd_kafka_flush(rk_, 2000);
            rd_kafka_destroy(rk_);
        }
#endif
    }

    Emitter(const Emitter&) = delete;
    Emitter& operator=(const Emitter&) = delete;

    const std::string& serviceName() const { return service_; }

    // Never blocks the request path and never throws.
    void emit(const Event& event) {
#ifdef FAULTSCOPE_HAVE_KAFKA
        if (rk_) {
            const std::string json = toJson(event);
            const rd_kafka_resp_err_t err = rd_kafka_producev(
                rk_,
                RD_KAFKA_V_TOPIC(topic_.c_str()),
                RD_KAFKA_V_KEY(service_.data(), service_.size()),
                RD_KAFKA_V_VALUE(const_cast<char*>(json.data()), json.size()),
                RD_KAFKA_V_MSGFLAGS(RD_KAFKA_MSG_F_COPY),
                RD_KAFKA_V_END);
            rd_kafka_poll(rk_, 0);  // serve delivery callbacks
            if (err == RD_KAFKA_RESP_ERR_NO_ERROR) {
                return;
            }
        }
#endif
        dropped().fetch_add(1);
    }

    // Collect pending delivery reports so dropped() is current (call before reading it).
    void poll() {
#ifdef FAULTSCOPE_HAVE_KAFKA
        if (rk_) {
            rd_kafka_poll(rk_, 0);
        }
#endif
    }

    // Events that were not delivered (produce failure, delivery timeout, or no Kafka).
    static std::atomic<std::uint64_t>& dropped() {
        static std::atomic<std::uint64_t> count{0};
        return count;
    }

private:
#ifdef FAULTSCOPE_HAVE_KAFKA
    static void onDelivery(rd_kafka_t*, const rd_kafka_message_t* msg, void*) {
        if (msg->err) {
            dropped().fetch_add(1);
            std::cerr << "telemetry: delivery failed: " << rd_kafka_err2str(msg->err) << "\n";
        }
    }
    rd_kafka_t* rk_ = nullptr;
#endif
    std::string topic_;
    std::string service_;
};

}  // namespace telemetry
