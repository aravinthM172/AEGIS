package com.aegis.service.faults;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** Fault-agent control surface for HttpErrorFault. Requires the shared FAULT_HOOK_TOKEN. */
@RestController
@RequestMapping("/_faults/http-error")
public class HttpErrorFaultController {

    private final HttpErrorFault fault;
    private final String token;

    public HttpErrorFaultController(HttpErrorFault fault, @Value("${faultscope.hook-token:}") String token) {
        this.fault = fault;
        this.token = token;
    }

    private ResponseEntity<Map<String, Object>> denied(String supplied) {
        if (token.isEmpty()) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "fault hook disabled: FAULT_HOOK_TOKEN not set"));
        }
        if (!token.equals(supplied)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "invalid fault hook token"));
        }
        return null;
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> activate(
            @RequestHeader(value = "X-Fault-Token", defaultValue = "") String supplied,
            @RequestBody Map<String, Object> body) {
        ResponseEntity<Map<String, Object>> refusal = denied(supplied);
        if (refusal != null) {
            return refusal;
        }
        double rate = ((Number) body.get("rate")).doubleValue();
        int status = body.containsKey("status") ? ((Number) body.get("status")).intValue() : 500;
        int ttl = body.containsKey("ttl_s") ? ((Number) body.get("ttl_s")).intValue() : 60;
        if (!(rate > 0 && rate <= 1) || status < 500 || status > 599 || ttl < 1 || ttl > 900) {
            return ResponseEntity.unprocessableEntity()
                    .body(Map.of("error", "rate in (0,1], status 500-599, ttl_s 1-900"));
        }
        return ResponseEntity.ok(fault.set(rate, status, ttl));
    }

    @DeleteMapping
    public ResponseEntity<Map<String, Object>> deactivate(
            @RequestHeader(value = "X-Fault-Token", defaultValue = "") String supplied) {
        ResponseEntity<Map<String, Object>> refusal = denied(supplied);
        return refusal != null ? refusal : ResponseEntity.ok(fault.clear());
    }

    @GetMapping
    public ResponseEntity<Map<String, Object>> current(
            @RequestHeader(value = "X-Fault-Token", defaultValue = "") String supplied) {
        ResponseEntity<Map<String, Object>> refusal = denied(supplied);
        return refusal != null ? refusal : ResponseEntity.ok(fault.state());
    }
}
