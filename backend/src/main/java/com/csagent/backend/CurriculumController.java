package com.csagent.backend;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/curriculum")
public class CurriculumController {
    private final String coreUrl;
    private final HttpClient client = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5)).build();

    public CurriculumController(@Value("${ai-core.base-url:http://127.0.0.1:8001}") String coreUrl) {
        this.coreUrl = coreUrl;
    }

    public record DraftRequest(String topic, String goal, String level) {}

    @PostMapping("/draft")
    public ResponseEntity<?> draft(@RequestBody DraftRequest body) {
        if (!valid(body.topic()) || !valid(body.goal()) || !valid(body.level()))
            return error(422, "주제·학습 목적·현재 수준을 각각 1~300자로 입력해 주세요.");
        try {
            var request = HttpRequest.newBuilder(URI.create(coreUrl + "/curriculum/draft"))
                    .timeout(Duration.ofSeconds(130)).header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(ConversationStore.JSON.writeValueAsString(body))).build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 503)
                return error(503, "다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.");
            if (response.statusCode() != 200)
                return error(502, "초안을 생성하지 못했습니다. 다시 시도해 주세요.");
            return ResponseEntity.ok().header("Cache-Control", "no-store")
                    .body(ConversationStore.JSON.readTree(response.body()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return error(503, "요청이 중단됐습니다. 다시 시도해 주세요.");
        } catch (IOException | tools.jackson.core.JacksonException e) {
            return error(502, "AI 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
        }
    }

    private static boolean valid(String value) {
        return value != null && !value.isBlank() && value.length() <= 300;
    }

    private static ResponseEntity<?> error(int status, String message) {
        return ResponseEntity.status(status).header("Cache-Control", "no-store").body(Map.of("message", message));
    }
}
