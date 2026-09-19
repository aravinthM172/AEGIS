package com.aegis.service.jobs;

/** A downstream dependency call failed. errorType is one of: timeout, connection_error, http_4xx, http_5xx. */
public class DependencyException extends RuntimeException {

    private final String dependency;
    private final String errorType;

    public DependencyException(String dependency, String errorType, String message, Throwable cause) {
        super(message, cause);
        this.dependency = dependency;
        this.errorType = errorType;
    }

    public String getDependency() {
        return dependency;
    }

    public String getErrorType() {
        return errorType;
    }
}
