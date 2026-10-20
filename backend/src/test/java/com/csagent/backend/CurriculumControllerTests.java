package com.csagent.backend;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class CurriculumControllerTests {
    @Test
    void validatesInputBeforeCallingCore() {
        var controller = new CurriculumController("http://127.0.0.1:1");
        for (String topic : new String[]{null, " ", "x".repeat(301)})
            assertEquals(422, controller.draft(new CurriculumController.DraftRequest(topic, "목적", "수준"))
                    .getStatusCode().value());
    }

    @Test
    void forwardsInputAndHandlesFailureThenRetry() throws Exception {
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var status = new AtomicInteger(503);
        var received = new AtomicReference<String>();
        var upgrade = new AtomicReference<String>();
        upstream.createContext("/curriculum/draft", exchange -> {
            upgrade.set(exchange.getRequestHeaders().getFirst("Upgrade"));
            received.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] body = (status.get() == 200 ? "{\"title\":\"페이징\",\"lessons\":[]}" : "private details")
                    .getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status.get(), body.length);
            try (var output = exchange.getResponseBody()) { output.write(body); }
        });
        upstream.start();
        try {
            var controller = new CurriculumController("http://127.0.0.1:" + upstream.getAddress().getPort());
            var input = new CurriculumController.DraftRequest("페이징", "취업 준비", "기초");
            var failed = controller.draft(input);
            assertEquals(503, failed.getStatusCode().value());
            assertFalse(failed.getBody().toString().contains("private details"));
            status.set(200);
            var result = controller.draft(input);
            assertEquals(200, result.getStatusCode().value());
            assertEquals("no-store", result.getHeaders().getCacheControl());
            assertNull(upgrade.get(), "Uvicorn does not support h2c upgrade");
            assertEquals("페이징", ConversationStore.JSON.readTree(received.get()).path("topic").asText());
            assertTrue(result.getBody().toString().contains("페이징"));
            status.set(500);
            assertEquals(502, controller.draft(input).getStatusCode().value());
        } finally { upstream.stop(0); }
    }
}
