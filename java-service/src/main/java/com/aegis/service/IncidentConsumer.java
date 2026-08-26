package com.aegis.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class IncidentConsumer {

    private final IncidentRepository incidentRepository;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public IncidentConsumer(IncidentRepository incidentRepository) {
        this.incidentRepository = incidentRepository;
    }

    @KafkaListener(
        topics = "aegis-incidents",
        groupId = "aegis-java-consumer"
    )
    public void consume(String message) throws Exception {
        System.out.println("Kafka Java consumer received: " + message);

        JsonNode node = objectMapper.readTree(message);

        Incident incident = new Incident(
            node.path("service").asText(null),
            node.path("severity").asText(null),
            node.path("message").asText(null)
        );

        if (node.hasNonNull("status")) {
            incident.setStatus(node.get("status").asText());
        }

        incidentRepository.save(incident);
    }
}
