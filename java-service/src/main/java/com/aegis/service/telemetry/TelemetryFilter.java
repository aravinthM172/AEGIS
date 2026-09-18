package com.aegis.service.telemetry;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;

import java.io.IOException;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Emits one FaultScope TelemetryEvent per HTTP request to the Kafka
 * telemetry topic. Field names match app/telemetry.py (TelemetryEvent).
 */
@Component
public class TelemetryFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(TelemetryFilter.class);

    private final KafkaTemplate<String, String> kafka;
    private final ObjectMapper mapper;
    private final String topic;
    private final String serviceName;

    public TelemetryFilter(
            KafkaTemplate<String, String> kafka,
            ObjectMapper mapper,
            @Value("${faultscope.telemetry.topic:telemetry}") String topic,
            @Value("${faultscope.service-name:java-service}") String serviceName) {
        this.kafka = kafka;
        this.mapper = mapper;
        this.topic = topic;
        this.serviceName = serviceName;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {

        String requestId = headerOrRandom(request, "X-Request-ID");
        String traceId = request.getHeader("X-Trace-Id");
        if (traceId == null || traceId.isBlank()) {
            traceId = requestId;
        }

        long startedNanos = System.nanoTime();
        int status = 500;
        String errorType = null;

        try {
            chain.doFilter(request, response);
            status = response.getStatus();
            if (status >= 400) {
                errorType = "http_" + (status / 100) + "xx";
            }
        } catch (ServletException | IOException | RuntimeException ex) {
            errorType = ex.getClass().getSimpleName();
            throw ex;
        } finally {
            double latencyMs = Math.round((System.nanoTime() - startedNanos) / 1_000.0) / 1_000.0;
            emit(request, requestId, traceId, latencyMs, status, errorType);
        }
    }

    private void emit(HttpServletRequest request, String requestId, String traceId,
                      double latencyMs, int status, String errorType) {
        try {
            Object pattern = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);

            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("method", request.getMethod());
            metadata.put("path", pattern != null ? pattern.toString() : request.getRequestURI());

            Map<String, Object> event = new LinkedHashMap<>();
            event.put("event_id", UUID.randomUUID().toString());
            event.put("timestamp", Instant.now().toString());
            event.put("service", serviceName);
            event.put("event_type", "http_request");
            event.put("severity", status >= 500 ? "ERROR" : status >= 400 ? "WARN" : "INFO");
            event.put("request_id", requestId);
            event.put("trace_id", traceId);
            event.put("latency_ms", latencyMs);
            event.put("status_code", status);
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

    private static String headerOrRandom(HttpServletRequest request, String name) {
        String value = request.getHeader(name);
        return (value == null || value.isBlank()) ? UUID.randomUUID().toString() : value;
    }
}
