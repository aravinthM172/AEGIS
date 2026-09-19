package com.aegis.service.telemetry;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.annotation.Order;
import org.springframework.lang.NonNull;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Emits one http_request TelemetryEvent per request. Handlers can enrich the event by
 * setting request attributes ATTR_ERROR_TYPE / ATTR_DEPENDENCY (e.g. when a downstream
 * dependency failed), and read the correlation ids from ATTR_REQUEST_ID / ATTR_TRACE_ID.
 */
@Component
@Order(10)
public class TelemetryFilter extends OncePerRequestFilter {

    public static final String ATTR_REQUEST_ID = "faultscope.request_id";
    public static final String ATTR_TRACE_ID = "faultscope.trace_id";
    public static final String ATTR_ERROR_TYPE = "faultscope.error_type";
    public static final String ATTR_DEPENDENCY = "faultscope.dependency";

    private final TelemetryPublisher publisher;

    public TelemetryFilter(TelemetryPublisher publisher) {
        this.publisher = publisher;
    }

    @Override
    protected void doFilterInternal(
            @NonNull HttpServletRequest request,
            @NonNull HttpServletResponse response,
            @NonNull FilterChain chain) throws ServletException, IOException {

        String requestId = headerOrRandom(request, "X-Request-ID");
        String traceId = request.getHeader("X-Trace-Id");
        if (traceId == null || traceId.isBlank()) {
            traceId = requestId;
        }
        request.setAttribute(ATTR_REQUEST_ID, requestId);
        request.setAttribute(ATTR_TRACE_ID, traceId);
        response.setHeader("X-Request-ID", requestId);

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

            Object handlerError = request.getAttribute(ATTR_ERROR_TYPE);
            if (handlerError != null) {
                errorType = handlerError.toString();
            }

            Object pattern = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);
            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("method", request.getMethod());
            metadata.put("path", pattern != null ? pattern.toString() : request.getRequestURI());
            Object dependency = request.getAttribute(ATTR_DEPENDENCY);
            if (dependency != null) {
                metadata.put("dependency", dependency.toString());
            }

            publisher.publish("http_request", TelemetryPublisher.severityFor(status),
                    requestId, traceId, latencyMs, status, errorType, metadata);
        }
    }

    private static String headerOrRandom(HttpServletRequest request, String name) {
        String value = request.getHeader(name);
        return (value == null || value.isBlank()) ? UUID.randomUUID().toString() : value;
    }
}
