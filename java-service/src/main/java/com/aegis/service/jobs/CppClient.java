package com.aegis.service.jobs;

import com.aegis.service.telemetry.TelemetryPublisher;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.net.SocketTimeoutException;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Calls cpp-service. Correlation headers are forwarded, and every call emits a
 * dependency_call telemetry event (the caller-side view of the edge: target, latency, outcome).
 */
@Component
public class CppClient {

    static final String TARGET = "cpp-service";

    private final RestClient rest;
    private final TelemetryPublisher telemetry;

    public CppClient(
            @Value("${faultscope.cpp-service.url:http://cpp-service:8082}") String baseUrl,
            @Value("${faultscope.cpp-service.connect-timeout-ms:1000}") int connectTimeoutMs,
            @Value("${faultscope.cpp-service.read-timeout-ms:2000}") int readTimeoutMs,
            TelemetryPublisher telemetry) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeoutMs);
        factory.setReadTimeout(readTimeoutMs);
        this.rest = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
        this.telemetry = telemetry;
    }

    public ComputeResult compute(long n, String requestId, String traceId) {
        long startedNanos = System.nanoTime();
        Integer status = null;
        String errorType = null;

        try {
            ComputeResult result = rest.get()
                    .uri("/compute?n={n}", n)
                    .header("X-Request-ID", requestId)
                    .header("X-Trace-Id", traceId)
                    .retrieve()
                    .body(ComputeResult.class);
            status = 200;
            if (result == null) {
                errorType = "http_5xx";
                throw new DependencyException(TARGET, errorType, "empty response from " + TARGET, null);
            }
            return result;
        } catch (RestClientResponseException ex) {
            status = ex.getStatusCode().value();
            errorType = "http_" + (status / 100) + "xx";
            throw new DependencyException(TARGET, errorType, TARGET + " returned " + status, ex);
        } catch (ResourceAccessException ex) {
            errorType = ex.getCause() instanceof SocketTimeoutException ? "timeout" : "connection_error";
            throw new DependencyException(TARGET, errorType, TARGET + " unreachable: " + errorType, ex);
        } finally {
            double latencyMs = Math.round((System.nanoTime() - startedNanos) / 1_000.0) / 1_000.0;
            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("target", TARGET);
            metadata.put("method", "GET");
            metadata.put("path", "/compute");
            telemetry.publish("dependency_call",
                    errorType == null ? "INFO" : "ERROR",
                    requestId, traceId, latencyMs, status, errorType, metadata);
        }
    }
}
