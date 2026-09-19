package com.aegis.service.faults;

import com.aegis.service.telemetry.TelemetryFilter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.annotation.Order;
import org.springframework.lang.NonNull;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Fails a fraction of business requests while the HTTP-error fault is active. Runs INSIDE
 * TelemetryFilter (higher order number), so the injected failure is recorded like any other.
 */
@Component
@Order(20)
public class HttpErrorFaultFilter extends OncePerRequestFilter {

    private final HttpErrorFault fault;

    public HttpErrorFaultFilter(HttpErrorFault fault) {
        this.fault = fault;
    }

    @Override
    protected boolean shouldNotFilter(@NonNull HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/health") || path.startsWith("/actuator") || path.startsWith("/_faults");
    }

    @Override
    protected void doFilterInternal(
            @NonNull HttpServletRequest request,
            @NonNull HttpServletResponse response,
            @NonNull FilterChain chain) throws ServletException, IOException {

        int status = fault.shouldFail();
        if (status < 0) {
            chain.doFilter(request, response);
            return;
        }
        request.setAttribute(TelemetryFilter.ATTR_ERROR_TYPE, "injected_fault");
        response.setStatus(status);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"injected_fault\"}");
    }
}
