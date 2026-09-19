package com.aegis.service.jobs;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "jobs")
public class Job {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private long n;
    private long primes;

    @Column(name = "compute_ms")
    private double computeMs;

    @Column(name = "trace_id")
    private String traceId;

    @Column(name = "created_at")
    private Instant createdAt;

    public Job() {
    }

    public Job(long n, long primes, double computeMs, String traceId) {
        this.n = n;
        this.primes = primes;
        this.computeMs = computeMs;
        this.traceId = traceId;
    }

    @PrePersist
    void onCreate() {
        createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public long getN() { return n; }
    public long getPrimes() { return primes; }
    public double getComputeMs() { return computeMs; }
    public String getTraceId() { return traceId; }
    public Instant getCreatedAt() { return createdAt; }
}
