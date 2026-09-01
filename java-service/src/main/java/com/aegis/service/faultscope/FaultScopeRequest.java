package com.aegis.service.faultscope;

public class FaultScopeRequest {

    private String incident;

    public FaultScopeRequest() {
    }

    public FaultScopeRequest(String incident) {
        this.incident = incident;
    }

    public String getIncident() {
        return incident;
    }

    public void setIncident(String incident) {
        this.incident = incident;
    }
}
