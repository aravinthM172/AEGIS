package com.aegis.service.faultscope;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/faultscope")
public class FaultScopeController {

    private final FaultScopeClient client;

    public FaultScopeController(FaultScopeClient client) {
        this.client = client;
    }

    @PostMapping("/analyze")
    public FaultScopeResponse analyze(
            @RequestBody FaultScopeRequest request) {

        return client.analyze(request.getIncident());
    }
}
