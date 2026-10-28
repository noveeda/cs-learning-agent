/**
 * @file
 * @brief 모의 AI 서버로 즉시 스트림 전달과 실패 문답의 재사용 제외, 그림 Content-Type 중계를 검증
 */
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
    /** @brief 상류 응답 종료 전에 브라우저로 이벤트가 전달되는지 검증 */
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
    /** @brief 실패한 부분 답변은 기록하되 다음 모델 문맥에서는 제외되는지 검증 */
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

    /** @brief /artifacts/{id}만 응답하는 모의 AI 서버를 띄움; 주어진 Content-Type과 본문을 그대로 돌려줌 */
    private HttpServer artifactUpstream(String contentType, byte[] body) throws Exception {
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        upstream.createContext("/artifacts/", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", contentType);
            exchange.sendResponseHeaders(200, body.length);
            try (var output = exchange.getResponseBody()) {
                output.write(body);
            }
        });
        upstream.start();
        return upstream;
    }

    @Test
    /** @brief 하네스 교재 그림(PNG)이 SVG로 잘못 표시되지 않고 image/png와 바이트 그대로 전달되는지 검증 */
    void artifactPassesPngContentType() throws Exception {
        var png = new byte[] {(byte) 0x89, 'P', 'N', 'G', 0, 1, 2};
        var upstream = artifactUpstream("image/png", png);
        try {
            var controller = new AgentController("http://127.0.0.1:" + upstream.getAddress().getPort(),
                    new ConversationStore(directory.toString()));
            var response = controller.artifact("d".repeat(32));
            assertEquals(200, response.getStatusCode().value());
            assertEquals("image/png", response.getHeaders().getFirst("Content-Type"));
            assertEquals("nosniff", response.getHeaders().getFirst("X-Content-Type-Options"));
            assertArrayEquals(png, response.getBody());
            // 기존 도식 SVG 중계도 그대로 유지
            upstream.stop(0);
            upstream = artifactUpstream("image/svg+xml", "<svg/>".getBytes(StandardCharsets.UTF_8));
            controller = new AgentController("http://127.0.0.1:" + upstream.getAddress().getPort(),
                    new ConversationStore(directory.toString()));
            assertEquals("image/svg+xml", controller.artifact("d".repeat(32)).getHeaders().getFirst("Content-Type"));
        } finally {
            upstream.stop(0);
        }
    }

    @Test
    /** @brief 그림이 아닌 형식(HTML 등)은 같은 출처 문서로 열리지 않도록 404로 막는지 검증 */
    void artifactRejectsOtherContentTypes() throws Exception {
        var upstream = artifactUpstream("text/html", "<script>alert(1)</script>".getBytes(StandardCharsets.UTF_8));
        try {
            var controller = new AgentController("http://127.0.0.1:" + upstream.getAddress().getPort(),
                    new ConversationStore(directory.toString()));
            var error = assertThrows(org.springframework.web.server.ResponseStatusException.class,
                    () -> controller.artifact("d".repeat(32)));
            assertEquals(404, error.getStatusCode().value());
        } finally {
            upstream.stop(0);
        }
    }
}
