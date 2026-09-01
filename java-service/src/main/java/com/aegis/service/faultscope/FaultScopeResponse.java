package com.aegis.service.faultscope;

import java.util.List;

public class FaultScopeResponse {

    private String incident;
    private Analysis analysis;

    public String getIncident() {
        return incident;
    }

    public void setIncident(String incident) {
        this.incident = incident;
    }

    public Analysis getAnalysis() {
        return analysis;
    }

    public void setAnalysis(Analysis analysis) {
        this.analysis = analysis;
    }

    public static class Analysis {

        private String summary;
        private String root_cause;
        private List<String> evidence;
        private List<String> affected_services;
        private List<String> investigation_steps;
        private List<String> remediation;
        private double confidence;

        public String getSummary() {
            return summary;
        }

        public void setSummary(String summary) {
            this.summary = summary;
        }

        public String getRoot_cause() {
            return root_cause;
        }

        public void setRoot_cause(String root_cause) {
            this.root_cause = root_cause;
        }

        public List<String> getEvidence() {
            return evidence;
        }

        public void setEvidence(List<String> evidence) {
            this.evidence = evidence;
        }

        public List<String> getAffected_services() {
            return affected_services;
        }

        public void setAffected_services(List<String> affected_services) {
            this.affected_services = affected_services;
        }

        public List<String> getInvestigation_steps() {
            return investigation_steps;
        }

        public void setInvestigation_steps(List<String> investigation_steps) {
            this.investigation_steps = investigation_steps;
        }

        public List<String> getRemediation() {
            return remediation;
        }

        public void setRemediation(List<String> remediation) {
            this.remediation = remediation;
        }

        public double getConfidence() {
            return confidence;
        }

        public void setConfidence(double confidence) {
            this.confidence = confidence;
        }
    }
}
