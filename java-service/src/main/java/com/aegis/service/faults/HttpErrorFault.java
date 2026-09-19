package com.aegis.service.faults;

import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Service-side HTTP-error fault state, controlled by the fault agent. Self-expiring, so a
 * dead agent cannot leave the service failing requests.
 */
@Component
public class HttpErrorFault {

    private volatile double rate = 0.0;
    private volatile int status = 500;
    private volatile long expiresAtNanos = 0L;

    public Map<String, Object> set(double rate, int status, int ttlSeconds) {
        this.rate = rate;
        this.status = status;
        this.expiresAtNanos = System.nanoTime() + ttlSeconds * 1_000_000_000L;
        return state();
    }

    public Map<String, Object> clear() {
        this.rate = 0.0;
        this.expiresAtNanos = 0L;
        return state();
    }

    public Map<String, Object> state() {
        long remaining = expiresAtNanos - System.nanoTime();
        boolean active = rate > 0 && remaining > 0;
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("active", active);
        state.put("rate", active ? rate : 0.0);
        state.put("status", status);
        state.put("expires_in_s", active ? Math.round(remaining / 1e8) / 10.0 : 0);
        return state;
    }

    /** The status to return for this request, or -1 to let it through. */
    public int shouldFail() {
        if (rate <= 0 || System.nanoTime() >= expiresAtNanos) {
            return -1;
        }
        return ThreadLocalRandom.current().nextDouble() < rate ? status : -1;
    }
}
