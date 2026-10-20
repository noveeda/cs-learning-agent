package com.csagent.backend;

import com.sun.net.httpserver.HttpServer;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicReference;

import java.io.ByteArrayOutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AgentControllerTests {
    @TempDir Path directory;

    @Test
    void flushesServletBufferBeforeUpstreamCompletes() throws Exception {
        var firstFlush = new CountDownLatch(1);
        var flushedBeforeCompletion = new AtomicBoolean();
        var received = new AtomicReference<String>();
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        upstream.createContext("/chat", exchange -> {
            received.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            exchange.sendResponseHeaders(200, 0);
            try (var output = exchange.getResponseBody()) {
                output.write("{\"type\":\"started\"}\n".getBytes(StandardCharsets.UTF_8));
                output.flush();
                try {
                    flushedBeforeCompletion.set(firstFlush.await(3, TimeUnit.SECONDS));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                output.write(("{\"type\":\"artifact\",\"artifact_id\":\"" + "b".repeat(32)
                        + "\",\"diagram\":{\"title\":\"페이징\",\"nodes\":[],\"edges\":[]}}\n"
                        + "{\"type\":\"answer\",\"text\":\"완료\"}\n{\"type\":\"done\"}\n").getBytes(StandardCharsets.UTF_8));
            }
        });
        upstream.start();
        try {
            var servlet = mock(HttpServletResponse.class);
            doAnswer(invocation -> { firstFlush.countDown(); return null; })
                    .when(servlet).flushBuffer();
            var store = new ConversationStore(directory.toString());
            var controller = new AgentController("http://127.0.0.1:" + upstream.getAddress().getPort(), store);
            var response = controller.chat(new AgentController.ChatRequest("a".repeat(32), "그려줘", null), servlet);
            var output = new ByteArrayOutputStream();
            assertNotNull(response.getBody());
            response.getBody().writeTo(output);
            assertTrue(flushedBeforeCompletion.get(), "First event must flush before upstream finishes");
            assertTrue(output.toString(StandardCharsets.UTF_8).endsWith("{\"type\":\"done\"}\n"));
            var restored = new ConversationStore(directory.toString()).load("a".repeat(32));
            assertEquals(2, restored.path("messages").size());
            assertEquals("complete", restored.path("messages").get(0).path("status").asText());
            assertEquals("페이징", restored.path("messages").get(1).path("artifacts").get(0).path("diagram").path("title").asText());

            var second = controller.chat(new AgentController.ChatRequest("a".repeat(32), "수정해줘", "b".repeat(32)), servlet);
            second.getBody().writeTo(new ByteArrayOutputStream());
            var payload = ConversationStore.JSON.readTree(received.get());
            assertEquals(2, payload.path("history").size());
            assertEquals("그려줘", payload.path("history").get(0).path("content").asText());
            assertEquals("b".repeat(32), payload.path("active_artifact").path("artifact_id").asText());
            assertEquals(0, store.load("c".repeat(32)).path("messages").size());
        } finally {
            upstream.stop(0);
        }
    }

    @Test
    void interruptedReplyIsStoredButNotReused() throws Exception {
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var received = new AtomicReference<String>();
        upstream.createContext("/chat", exchange -> {
            received.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            exchange.sendResponseHeaders(200, 0);
            try (var output = exchange.getResponseBody()) {
                output.write("{\"type\":\"text_delta\",\"text\":\"일부\"}\n".getBytes(StandardCharsets.UTF_8));
            }
        });
        upstream.start();
        try {
            var store = new ConversationStore(directory.toString());
            var controller = new AgentController("http://127.0.0.1:" + upstream.getAddress().getPort(), store);
            var body = new AgentController.ChatRequest("a".repeat(32), "질문", null);
            controller.chat(body, mock(HttpServletResponse.class)).getBody().writeTo(new ByteArrayOutputStream());
            var messages = store.load(body.conversation_id()).path("messages");
            assertEquals("failed", messages.get(0).path("status").asText());
            assertEquals("일부", messages.get(1).path("content").asText());
            controller.chat(body, mock(HttpServletResponse.class)).getBody().writeTo(new ByteArrayOutputStream());
            assertEquals(0, ConversationStore.JSON.readTree(received.get()).path("history").size());
            assertThrows(org.springframework.web.server.ResponseStatusException.class,
                    () -> store.load("../escape"));
        } finally {
            upstream.stop(0);
        }
    }
}
