package com.aegis.service;

import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/incidents")
public class IncidentController {

    private final IncidentRepository incidentRepository;

    public IncidentController(IncidentRepository incidentRepository) {
        this.incidentRepository = incidentRepository;
    }

    @PostMapping
    public Incident receiveIncident(@RequestBody Incident incident) {

        System.out.println(
            "Java API received incident: " +
            incident.getService() +
            " | " +
            incident.getSeverity() +
            " | " +
            incident.getMessage()
        );

        return incidentRepository.save(incident);
    }

    @GetMapping
    public List<Incident> getIncidents() {
        return incidentRepository.findAll();
    }
}
