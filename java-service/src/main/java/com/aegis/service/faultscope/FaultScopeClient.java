package com.aegis.service.faultscope;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

@Service
public class FaultScopeClient {

    private final RestClient restClient;

    public FaultScopeClient() {

        this.restClient = RestClient.builder()
                .baseUrl("http://127.0.0.1:8003")
                .build();
    }

    public FaultScopeResponse analyze(String incident) {

        FaultScopeRequest request =
                new FaultScopeRequest(incident);

        return restClient.post()
                .uri("/api/v1/analyze")
                .body(request)
                .retrieve()
                .body(FaultScopeResponse.class);
    }
}
