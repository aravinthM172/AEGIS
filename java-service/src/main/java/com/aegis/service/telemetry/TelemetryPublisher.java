package com.aegis.service.telemetry;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Builds FaultScope TelemetryEvents (schema: app/telemetry.py) and sends them to
 * the Kafka telemetry topic. Never throws into the caller's request path.
 */
@Component
public class TelemetryPublisher {

    private static final Logger log = LoggerFactory.getLogger(TelemetryPublisher.class);

    private final KafkaTemplate<String, String> kafka;
    private final ObjectMapper mapper;
    private final String topic;
    private final String serviceName;

    public TelemetryPublisher(
            KafkaTemplate<String, String> kafka,
            ObjectMapper mapper,
            @Value("${faultscope.telemetry.topic:telemetry}") String topic,
            @Value("${faultscope.service-name:java-service}") String serviceName) {
        this.kafka = kafka;
        this.mapper = mapper;
        this.topic = topic;
        this.serviceName = serviceName;
    }

    public static String severityFor(Integer statusCode) {
        if (statusCode == null) {
            return "ERROR";
        }
        return statusCode >= 500 ? "ERROR" : statusCode >= 400 ? "WARN" : "INFO";
    }

    public void publish(String eventType, String severity, String requestId, String traceId,
                        double latencyMs, Integer statusCode, String errorType,
                        Map<String, Object> metadata) {
        try {
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("event_id", UUID.randomUUID().toString());
            event.put("timestamp", Instant.now().toString());
            event.put("service", serviceName);
            event.put("event_type", eventType);
            event.put("severity", severity);
            event.put("request_id", requestId);
            event.put("trace_id", traceId);
            event.put("latency_ms", latencyMs);
            event.put("status_code", statusCode);
            event.put("error_type", errorType);
            event.put("metadata", metadata);

            kafka.send(topic, serviceName, mapper.writeValueAsString(event))
                 .whenComplete((result, ex) -> {
                     if (ex != null) {
                         log.warn("telemetry send failed: {}", ex.toString());
                     }
                 });
        } catch (Exception ex) {
            // telemetry must never break the request path
            log.warn("telemetry emit failed", ex);
        }
    }
}
