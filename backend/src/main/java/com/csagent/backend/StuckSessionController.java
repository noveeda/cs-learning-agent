// 파일 역할: 막힘 세션 API; AI core의 /stuck/step을 중계하고 버전 체크포인트로 저장·재시도·재개를 처리
// - 단계 판단(진단·보충·이해 확인)은 AI core가 하고, 이 파일은 저장과 브라우저 공개 범위만 책임짐
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
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/stuck/sessions")
public class StuckSessionController {
    private final String coreUrl;
    private final StuckSessionStore store;
    // Uvicorn 중계에서 HTTP/2 업그레이드 문제가 발생해 HTTP/1.1로 고정(커리큘럼 API와 같은 이유)
    private final HttpClient client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5)).build();
    // 역할: AI core 연결 주소와 막힘 세션 저장소 주입
    public StuckSessionController(@Value("${ai-core.base-url:http://127.0.0.1:8001}") String coreUrl, StuckSessionStore store) {
        this.coreUrl = coreUrl;
        this.store = store;
    }
    // 역할: 세션 시작 요청; entry 세부 검사(kind·concept·return_to 길이)는 AI core가 맡음
    public record CreateRequest(JsonNode entry) {}
    // 역할: 진행 요청; version은 브라우저가 마지막으로 받은 체크포인트 번호
    public record StepRequest(Integer version, JsonNode input) {}
    // 역할: AI core 오류를 브라우저용 상태 코드·한국어 안내로 바꿔 전달하는 내부 신호
    private static class CoreError extends Exception {
        final int status;
        CoreError(int status, String message) { super(message); this.status = status; }
    }

    @PostMapping
    // 역할: AI core에 start 입력을 보내 첫 상태를 받고, 성공했을 때만 버전 1로 저장
    // - 실패하면 파일을 만들지 않아 빈 세션이 남지 않음
    public ResponseEntity<?> create(@RequestBody CreateRequest body) throws IOException {
        if (body == null || body.entry() == null || !body.entry().isObject())
            return error(422, "막힘 세션을 시작할 위치 정보가 필요합니다.");
        var payload = ConversationStore.JSON.createObjectNode();
        payload.putObject("input").put("type", "start").set("entry", body.entry());
        try {
            var session = ConversationStore.JSON.createObjectNode();
            session.put("id", UUID.randomUUID().toString().replace("-", "")).put("version", 1);
            session.set("state", callCore(payload));
            store.save(session);
            return ok(session);
        } catch (CoreError e) { return error(e.status, e.getMessage()); }
    }

    @GetMapping("/{id}")
    // 역할: 저장된 체크포인트를 브라우저 공개 형태로 반환; 새로고침·재접속 후 재개에 사용
    public ResponseEntity<?> get(@PathVariable String id) throws IOException {
        var session = store.load(id);
        return session == null ? notFound() : ok(session);
    }

    @PostMapping("/{id}/step")
    // 역할: 버전을 확인하고 AI core의 다음 상태를 저장한 뒤 반환
    // - 낮은 버전은 응답 유실 후 재시도로 보고 저장본 반환(같은 단계를 두 번 진행하지 않음)
    // - 같은 세션 동시 요청은 잠금으로 409; 예외가 나도 finally에서 잠금 해제
    public ResponseEntity<?> step(@PathVariable String id, @RequestBody StepRequest body) throws IOException {
        if (!StuckSessionStore.validId(id)) return notFound();
        if (body == null || body.version() == null || body.input() == null || !body.input().path("type").isString())
            return error(422, "진행 버전과 입력 종류가 필요합니다.");
        if (!store.tryAcquire(id)) return error(409, "같은 세션의 이전 요청을 처리 중입니다. 잠시 후 다시 불러와 주세요.");
        try {
            var session = store.load(id);
            if (session == null) return notFound();
            int version = session.path("version").asInt();
            if (body.version() < version) return ok(session);
            if (body.version() != version) return error(409, "세션 상태가 변경됐습니다. 다시 불러와 주세요.");
            var state = session.path("state");
            String stage = state.path("stage").asText();
            if (stage.equals("complete") || stage.equals("out_of_scope")) return ok(session);
            // 브라우저 입력은 계약 필드(type·item·answer)만 옮겨 AI core에 예상 밖 값이 가지 않게 함
            var payload = ConversationStore.JSON.createObjectNode();
            payload.set("state", state);
            var input = payload.putObject("input").put("type", body.input().path("type").asString());
            for (String field : new String[]{"item", "answer"})
                if (body.input().path(field).isString()) input.put(field, body.input().path(field).asString());
            session.set("state", callCore(payload));
            session.put("version", version + 1);
            // 응답 전에 저장해 네트워크 응답이 유실돼도 다음 조회에서 완료한 단계를 복구
            store.save(session);
            return ok(session);
        } catch (CoreError e) {
            return error(e.status, e.getMessage());
        } finally { store.release(id); }
    }

    // 역할: AI core /stuck/step 호출 후 새 상태 반환; 오류는 계약의 상태 코드로 바꿔 CoreError로 던짐
    // - 320초 제한: LLM 보충·채점 단계가 오래 걸려도 Spring 요청 스레드가 무기한 묶이지 않게 하는 운영 보호 장치
    // - 409의 detail은 AI core가 학습자용 한국어 안내로 만든 문장이라 그대로 전달, 나머지 본문은 내부 정보일 수 있어 숨김
    private ObjectNode callCore(ObjectNode payload) throws CoreError {
        try {
            var request = HttpRequest.newBuilder(URI.create(coreUrl + "/stuck/step"))
                    .timeout(Duration.ofSeconds(320)).header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(ConversationStore.JSON.writeValueAsString(payload))).build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            switch (response.statusCode()) {
                case 200 -> {}
                case 409 -> {
                    var detail = ConversationStore.JSON.readTree(response.body()).path("detail");
                    throw new CoreError(409, detail.isString() ? detail.asString() : "세션 상태가 변경됐습니다. 다시 불러와 주세요.");
                }
                case 503 -> throw new CoreError(503, "지금은 이 단계를 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.");
                case 422 -> throw new CoreError(422, "입력 형식이 올바르지 않습니다.");
                default -> throw new CoreError(502, "막힘 세션 처리에 실패했습니다. 저장된 단계에서 다시 시도해 주세요.");
            }
            var next = ConversationStore.JSON.readTree(response.body());
            // 저장 전에 최소 형태를 확인해 깨진 상태가 체크포인트를 덮어쓰지 않게 함
            if (!next.isObject() || !next.path("stage").isString() || !next.path("view").isObject() || !next.path("entry").isObject())
                throw new CoreError(502, "잘못된 세션 상태를 받았습니다. 기존 단계는 보존했습니다.");
            return (ObjectNode) next;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CoreError(503, "요청이 중단됐습니다. 저장된 단계에서 다시 시도해 주세요.");
        } catch (java.net.http.HttpTimeoutException e) {
            throw new CoreError(504, "처리 시간이 초과됐습니다. 저장된 단계에서 다시 시도해 주세요.");
        } catch (IOException | tools.jackson.core.JacksonException e) {
            throw new CoreError(502, "막힘 세션 처리 중 오류가 발생했습니다. 저장된 단계에서 다시 시도해 주세요.");
        }
    }
    // 역할: 저장본에서 브라우저에 필요한 필드만 골라 반환; log·probes·frontier 등 내부 진단 기록은 서버에만 둠
    private static ResponseEntity<?> ok(ObjectNode session) {
        var state = session.path("state");
        var body = ConversationStore.JSON.createObjectNode();
        body.put("id", session.path("id").asString()).put("version", session.path("version").asInt());
        body.set("stage", state.path("stage"));
        body.set("view", state.path("view"));
        body.set("entry", state.path("entry"));
        return ResponseEntity.ok().header("Cache-Control", "no-store").body(body);
    }
    // 역할: 없는 세션과 형식이 틀린 ID를 같은 404로 응답해 저장 경로 정보를 드러내지 않음
    private static ResponseEntity<?> notFound() { return error(404, "막힘 세션을 찾을 수 없습니다. 처음부터 다시 시작해 주세요."); }
    // 역할: 상태 코드와 한국어 오류 안내를 통일된 응답 형태로 반환
    private static ResponseEntity<?> error(int status, String message) {
        return ResponseEntity.status(status).header("Cache-Control", "no-store").body(Map.of("message", message));
    }
}
