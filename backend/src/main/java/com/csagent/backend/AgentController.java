/**
 * @file
 * @brief 브라우저 채팅 요청을 AI core로 중계하며 문답과 도식 기록을 서버에 저장
 */
package com.csagent.backend;

import java.io.IOException;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import jakarta.servlet.http.HttpServletResponse;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import tools.jackson.databind.node.ObjectNode;
import tools.jackson.databind.node.ArrayNode;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

@RestController
@RequestMapping("/api")
public class AgentController {
    private final HttpClient client = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5)).build();
    private final String coreUrl;
    private final ConversationStore store;

    /** @brief AI core 주소와 대화 저장소 주입 */
    public AgentController(@Value("${ai-core.base-url:http://127.0.0.1:8001}") String coreUrl,
                           ConversationStore store) {
        this.coreUrl = coreUrl;
        this.store = store;
    }

    public record ChatRequest(String conversation_id, String question, String artifact_id) {}

    @GetMapping("/conversations/{id}")
    /** @brief 대화 ID로 저장된 문답 조회 */
    public ObjectNode conversation(@PathVariable String id) throws IOException {
        return store.load(id);
    }

    @PostMapping(value = "/chat", produces = "application/x-ndjson")
    /** @brief 완료 이력을 모델에 전달하고 이벤트를 즉시 중계; 성공·실패 결과를 저장 */
    public ResponseEntity<StreamingResponseBody> chat(@RequestBody ChatRequest body, HttpServletResponse servletResponse) {
        if (body.question() == null || body.question().isBlank() || body.question().length() > 4000) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "Invalid question");
        }
        store.acquire(body.conversation_id());
        boolean handedToStream = false;
        try {
            var conversation = store.load(body.conversation_id());
            var messages = (ArrayNode) conversation.path("messages");
            var payload = ConversationStore.JSON.createObjectNode().put("question", body.question());
            // 서버에 저장된 complete 문답만 사용해 실패·미완료 출력을 다음 질문의 사실로 재사용하지 않음
            var history = payload.putArray("history");
            for (var message : messages) {
                if (message.path("status").asText().equals("complete")) {
                    var turn = history.addObject();
                    turn.put("role", message.path("role").asText());
                    turn.put("content", message.path("content").asText());
                    if (message.has("artifacts")) turn.set("artifacts", message.path("artifacts"));
                }
                for (var artifact : message.path("artifacts")) {
                    if (artifact.path("artifact_id").asText().equals(body.artifact_id()))
                        payload.set("active_artifact", artifact);
                }
            }
            if (body.artifact_id() != null && !payload.has("active_artifact"))
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Unknown conversation artifact");
            var user = messages.addObject().put("role", "user")
                    .put("content", body.question()).put("status", "pending");
            store.save(conversation);
            var request = HttpRequest.newBuilder(URI.create(coreUrl + "/chat"))
                    .timeout(Duration.ofSeconds(550))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(ConversationStore.JSON.writeValueAsString(payload))).build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofInputStream());
            var assistant = messages.addObject().put("role", "assistant").put("content", "").put("status", "failed");
            var artifacts = assistant.putArray("artifacts");
            // 스트림에 제어를 넘긴 뒤에는 스트림의 finally가 저장·잠금 해제를 책임짐
            StreamingResponseBody stream = output -> {
                boolean completed = false;
                boolean answered = false;
                try (var input = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = input.readLine()) != null) {
                        if (response.statusCode() == 200 && !line.isBlank()) {
                            var event = ConversationStore.JSON.readTree(line);
                            switch (event.path("type").asText()) {
                                case "text_start", "tool_started" -> assistant.put("content", "");
                                case "text_delta" -> assistant.put("content",
                                        assistant.path("content").asText() + event.path("text").asText());
                                case "answer" -> {
                                    assistant.put("content", event.path("text").asText());
                                    answered = true;
                                }
                                case "artifact" -> artifacts.add(event);
                                case "done" -> {
                                    if (!answered) throw new IOException("Missing final answer");
                                    user.put("status", "complete");
                                    assistant.put("status", "complete");
                                    store.save(conversation);
                                    completed = true;
                                }
                                default -> { }
                            }
                        }
                        output.write((line + "\n").getBytes(StandardCharsets.UTF_8));
                        // 이벤트마다 버퍼를 비워 답변 전체가 끝나기 전에 브라우저에 전달
                        output.flush();
                        servletResponse.flushBuffer();
                    }
                } finally {
                    try {
                        if (!completed) {
                            user.put("status", "failed");
                            assistant.put("status", "failed");
                            store.save(conversation);
                        }
                    } finally {
                        store.release(body.conversation_id());
                    }
                }
            };
            handedToStream = true;
            return ResponseEntity.status(response.statusCode())
                    .header("Content-Type", response.statusCode() == 200 ? "application/x-ndjson" : "application/json")
                    .header("Cache-Control", "no-store").body(stream);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Request interrupted");
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "AI core unavailable");
        } finally {
            if (!handedToStream) store.release(body.conversation_id());
        }
    }

    @GetMapping("/artifacts/{id}")
    /**
     * @brief 그림 ID를 검증하고 도식 SVG 또는 교재 그림 PNG를 브라우저 보안 헤더와 함께 중계
     * - Content-Type은 AI core가 준 값 중 image/png·image/svg+xml만 그대로 씀. 그 밖의 형식은 404
     *   - 같은 출처(/api)로 HTML 같은 문서가 열리지 않게 하려는 허용 목록
     */
    public ResponseEntity<byte[]> artifact(@PathVariable String id) {
        if (!id.matches("[0-9a-f]{32}")) throw new ResponseStatusException(HttpStatus.NOT_FOUND);
        try {
            var request = HttpRequest.newBuilder(URI.create(coreUrl + "/artifacts/" + id))
                    .timeout(Duration.ofSeconds(10)).GET().build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
            // "image/svg+xml; charset=utf-8"처럼 매개변수가 붙어도 형식 이름만 비교
            var type = response.headers().firstValue("Content-Type").orElse("").split(";")[0].trim();
            if (response.statusCode() == 200 && !type.equals("image/png") && !type.equals("image/svg+xml"))
                throw new ResponseStatusException(HttpStatus.NOT_FOUND);
            return ResponseEntity.status(response.statusCode())
                    .header("Content-Type", response.statusCode() == 200 ? type : "application/json")
                    .header("Content-Security-Policy", type.equals("image/png") ? "default-src 'none'; sandbox"
                            : "default-src 'none'; style-src 'unsafe-inline'; font-src data:; sandbox")
                    .header("X-Content-Type-Options", "nosniff").body(response.body());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE);
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY);
        }
    }
}
