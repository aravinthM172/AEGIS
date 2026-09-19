package com.aegis.service.jobs;

import com.aegis.service.telemetry.TelemetryFilter;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * POST /jobs?n=... asks cpp-service to count primes up to n, persists the result in
 * Postgres and returns it. Depends on cpp-service (HTTP) and postgres (JPA).
 */
@RestController
@RequestMapping("/jobs")
public class JobController {

    static final long MAX_N = 5_000_000;

    private final CppClient cpp;
    private final JobRepository jobs;

    public JobController(CppClient cpp, JobRepository jobs) {
        this.cpp = cpp;
        this.jobs = jobs;
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> create(
            @RequestParam(defaultValue = "100000") long n,
            HttpServletRequest request) {

        if (n < 1 || n > MAX_N) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "n must be between 1 and " + MAX_N));
        }

        String requestId = (String) request.getAttribute(TelemetryFilter.ATTR_REQUEST_ID);
        String traceId = (String) request.getAttribute(TelemetryFilter.ATTR_TRACE_ID);

        ComputeResult result = cpp.compute(n, requestId, traceId);
        Job job = jobs.save(new Job(result.n(), result.primes(), result.computeMs(), traceId));

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", job.getId());
        body.put("n", job.getN());
        body.put("primes", job.getPrimes());
        body.put("compute_ms", job.getComputeMs());
        body.put("trace_id", traceId);
        return ResponseEntity.ok(body);
    }

    /** cpp-service failed: mark the request so telemetry records which dependency broke. */
    @ExceptionHandler(DependencyException.class)
    public ResponseEntity<Map<String, Object>> onDependencyFailure(DependencyException ex, HttpServletRequest request) {
        request.setAttribute(TelemetryFilter.ATTR_ERROR_TYPE, "dependency_" + ex.getErrorType());
        request.setAttribute(TelemetryFilter.ATTR_DEPENDENCY, ex.getDependency());

        HttpStatus status = switch (ex.getErrorType()) {
            case "timeout" -> HttpStatus.GATEWAY_TIMEOUT;
            case "connection_error" -> HttpStatus.SERVICE_UNAVAILABLE;
            default -> HttpStatus.BAD_GATEWAY;
        };

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", "dependency_failure");
        body.put("dependency", ex.getDependency());
        body.put("error_type", ex.getErrorType());
        body.put("detail", ex.getMessage());
        return ResponseEntity.status(status).body(body);
    }
}
