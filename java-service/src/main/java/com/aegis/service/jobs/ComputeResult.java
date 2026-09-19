package com.aegis.service.jobs;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public record ComputeResult(
        long n,
        long primes,
        @JsonProperty("compute_ms") double computeMs) {
}
