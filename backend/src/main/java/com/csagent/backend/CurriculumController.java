package com.csagent.backend;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/curriculum/draft")
public class CurriculumController {
    private final String coreUrl;
    private final CurriculumStore store;
    private final HttpClient client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5)).build();
    public CurriculumController(@Value("${ai-core.base-url:http://127.0.0.1:8001}") String coreUrl, CurriculumStore store) {
        this.coreUrl = coreUrl;
        this.store = store;
    }
    public record DraftRequest(String topic, String goal, String level) {}
    public record StepRequest(int version) {}

    @PostMapping
    public ResponseEntity<?> create(@RequestBody DraftRequest body) throws IOException {
        if (!valid(body.topic()) || !valid(body.goal()) || !valid(body.level()))
            return error(422, "주제·학습 목적·현재 수준을 각각 1~300자로 입력해 주세요.");
        var job = ConversationStore.JSON.createObjectNode();
        job.put("id", UUID.randomUUID().toString().replace("-", "")).put("version", 0);
        var state = job.putObject("state");
        state.set("request", ConversationStore.JSON.valueToTree(body));
        state.put("stage", "outline");
        state.putArray("units");
        store.save(job);
        return ok(job);
    }

    @GetMapping("/{id}")
    public ResponseEntity<?> get(@PathVariable String id) throws IOException { return ok(store.load(id)); }

    @PostMapping("/{id}/step")
    public ResponseEntity<?> step(@PathVariable String id, @RequestBody StepRequest body) throws IOException {
        store.acquire(id);
        try {
            var job = store.load(id);
            int version = job.path("version").asInt();
            // A retry after a lost response returns the saved checkpoint instead of advancing twice.
            if (body.version() < version) return ok(job);
            if (body.version() != version) return error(409, "작업 상태가 변경됐습니다. 다시 불러와 주세요.");
            var state = job.path("state");
            String stage = state.path("stage").asText();
            if (stage.equals("complete") || stage.equals("blocked")) return ok(job);
            var request = HttpRequest.newBuilder(URI.create(coreUrl + "/curriculum/step"))
                    .timeout(Duration.ofSeconds(320)).header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(ConversationStore.JSON.writeValueAsString(state))).build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 503) return error(503, "다른 요청을 처리 중입니다. 잠시 후 이어서 생성해 주세요.");
            if (response.statusCode() != 200) return error(502, "단계 생성에 실패했습니다. 저장된 결과에서 이어서 재시도해 주세요.");
            var next = ConversationStore.JSON.readTree(response.body());
            if (!next.isObject() || !next.path("units").isArray() || !next.has("stage")
                    || !next.path("request").isObject())
                return error(502, "잘못된 생성 상태를 받았습니다. 기존 결과는 보존했습니다.");
            // The original user requirements are authoritative, even if the model-side validator normalizes whitespace.
            ((ObjectNode) next).set("request", state.path("request"));
            job.set("state", next);
            job.put("version", version + 1);
            store.save(job);
            return ok(job);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return error(503, "요청이 중단됐습니다. 저장된 단계에서 이어서 생성해 주세요.");
        } catch (java.net.http.HttpTimeoutException e) {
            return error(504, "단계 생성 시간이 초과됐습니다. 이어서 생성해 주세요.");
        } catch (IOException | tools.jackson.core.JacksonException e) {
            return error(502, "처리 중 오류가 발생했습니다. 마지막 저장 상태를 다시 불러와 주세요.");
        } finally { store.release(id); }
    }
    private static boolean valid(String value) { return value != null && !value.isBlank() && value.length() <= 300; }
    private static ResponseEntity<?> ok(ObjectNode job) {
        return ResponseEntity.ok().header("Cache-Control", "no-store").body(job);
    }
    private static ResponseEntity<?> error(int status, String message) {
        return ResponseEntity.status(status).header("Cache-Control", "no-store").body(Map.of("message", message));
    }
}
