/**
 * @file
 * @brief 막힘 세션 중계 API의 버전 체크포인트·오류 매핑·내부 상태 비공개 규칙 검증
 */
package com.csagent.backend;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.ResponseEntity;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import static org.junit.jupiter.api.Assertions.*;

class StuckSessionControllerTests {
    @TempDir Path directory;
    private HttpServer upstream;
    // 가짜 AI core가 돌려줄 상태 코드와 본문; 테스트마다 바꿔 오류 매핑을 확인
    private final AtomicInteger status = new AtomicInteger(200);
    private final AtomicReference<String> errorBody = new AtomicReference<>("{\"detail\":\"private details\"}");
    private final AtomicReference<CountDownLatch> gate = new AtomicReference<>();
    private final CopyOnWriteArrayList<JsonNode> requests = new CopyOnWriteArrayList<>();
    private StuckSessionStore store;
    private StuckSessionController controller;

    @BeforeEach
    /** @brief /stuck/step을 흉내 내는 AI core를 띄움; 성공 시 진단 문항 화면과 내부 기록(log·probes·frontier)을 함께 돌려줌 */
    void startUpstream() throws Exception {
        upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        upstream.setExecutor(Executors.newCachedThreadPool());
        upstream.createContext("/stuck/step", exchange -> {
            var request = ConversationStore.JSON.readTree(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            requests.add(request);
            var latch = gate.get();
            if (latch != null) try { latch.await(5, TimeUnit.SECONDS); } catch (InterruptedException ignored) {}
            String body;
            if (status.get() == 200) {
                var state = request.path("state").isObject() ? (ObjectNode) request.path("state").deepCopy()
                        : newState(request.path("input").path("entry"));
                state.withArray("log").addObject().put("kind", request.path("input").path("type").asText());
                state.withObject("view").put("step", state.path("log").size());
                body = state.toString();
            } else body = errorBody.get();
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status.get(), bytes.length);
            try (var out = exchange.getResponseBody()) { out.write(bytes); }
        });
        upstream.start();
        store = new StuckSessionStore(directory.toString());
        controller = new StuckSessionController("http://127.0.0.1:" + upstream.getAddress().getPort(), store);
    }

    @AfterEach
    void stopUpstream() { upstream.stop(0); }

    /** @brief AI core가 시작 입력으로 만드는 상태 흉내; 브라우저에 보내면 안 되는 내부 필드 포함 */
    private static ObjectNode newState(JsonNode entry) {
        var state = ConversationStore.JSON.createObjectNode();
        state.set("entry", entry);
        state.put("stage", "diagnose");
        state.putArray("probes").addObject().put("concept", "array").put("result", "gap");
        state.putArray("frontier").addArray().add("array").add(1);
        state.putArray("log");
        state.putObject("view").put("kind", "question").put("purpose", "diagnosis");
        return state;
    }

    private static ObjectNode entry() {
        var entry = ConversationStore.JSON.createObjectNode();
        return entry.put("kind", "roadmap").put("concept", "stack").put("return_to", "/#/roadmap?concept=stack");
    }

    private static ObjectNode input(String type) {
        return ConversationStore.JSON.createObjectNode().put("type", type).put("item", "stack-d1").put("answer", "B");
    }

    private ObjectNode start() throws Exception {
        var response = controller.create(new StuckSessionController.CreateRequest(entry()));
        assertEquals(200, response.getStatusCode().value());
        return (ObjectNode) response.getBody();
    }

    private ResponseEntity<?> step(String id, Integer version, String type) throws Exception {
        return controller.step(id, new StuckSessionController.StepRequest(version, input(type)));
    }

    private static String message(ResponseEntity<?> response) { return ((Map<?, ?>) response.getBody()).get("message").toString(); }

    @Test
    /** @brief 시작·진행·조회가 같은 저장본을 쓰고, AI core에 계약대로 요청하는지 검증 */
    void startsStepsAndResumesFromSavedSession() throws Exception {
        var created = start();
        var id = created.path("id").asText();
        assertEquals(1, created.path("version").asInt());
        assertEquals("diagnose", created.path("stage").asText());
        var first = requests.get(0);
        assertFalse(first.has("state"));
        assertEquals("start", first.path("input").path("type").asText());
        assertEquals(entry(), first.path("input").path("entry"));

        var stepped = step(id, 1, "answer");
        assertEquals(200, stepped.getStatusCode().value());
        assertEquals("no-store", stepped.getHeaders().getCacheControl());
        assertEquals(2, ((JsonNode) stepped.getBody()).path("version").asInt());
        // 진행 요청에는 저장된 전체 상태와 허용된 입력 필드만 전달
        var second = requests.get(1);
        assertEquals(1, second.path("state").path("log").size());
        assertEquals(input("answer"), second.path("input"));

        // 저장소를 새로 만들어도(서버 재시작) 같은 단계에서 재개
        var resumed = new StuckSessionController("http://127.0.0.1:1", new StuckSessionStore(directory.toString())).get(id);
        assertEquals(stepped.getBody(), resumed.getBody());
        assertEquals(2, ((JsonNode) resumed.getBody()).path("view").path("step").asInt());
    }

    @Test
    /** @brief 세 API 모두 {id, version, stage, view, entry}만 내보내고 log·probes·frontier는 숨기는지 검증 */
    void neverExposesInternalState() throws Exception {
        var created = start();
        var id = created.path("id").asText();
        var stepped = (JsonNode) step(id, 1, "answer").getBody();
        var loaded = (JsonNode) controller.get(id).getBody();
        for (JsonNode body : new JsonNode[]{created, stepped, loaded}) {
            assertEquals(Set.of("id", "version", "stage", "view", "entry"), Set.copyOf(body.propertyNames()));
            assertFalse(body.toString().contains("probes"));
        }
        // 저장본에는 다음 단계 진행에 필요한 전체 상태가 남아 있어야 함
        assertTrue(store.load(id).path("state").has("probes"));
    }

    @Test
    /** @brief 응답 유실 후 낮은 버전 재시도는 재실행 없이 저장본 반환, 맞지 않는 버전은 409, 종료 세션은 저장본 반환 */
    void handlesRetriesConflictsAndFinishedSessions() throws Exception {
        var id = start().path("id").asText();
        step(id, 1, "answer");
        var retried = step(id, 1, "answer");
        assertEquals(200, retried.getStatusCode().value());
        assertEquals(2, ((JsonNode) retried.getBody()).path("version").asInt());
        assertEquals(2, requests.size());
        assertEquals(409, step(id, 7, "answer").getStatusCode().value());

        var saved = store.load(id);
        ((ObjectNode) saved.path("state")).put("stage", "complete");
        store.save(saved);
        var finished = step(id, 2, "continue");
        assertEquals(200, finished.getStatusCode().value());
        assertEquals("complete", ((JsonNode) finished.getBody()).path("stage").asText());
        assertEquals(2, requests.size());
    }

    @Test
    /** @brief AI core 오류별 상태 코드 매핑과, 어떤 오류에서도 저장본이 바뀌지 않는지 검증 */
    void mapsUpstreamErrorsWithoutChangingSavedSession() throws Exception {
        var id = start().path("id").asText();
        var before = store.load(id);

        status.set(409);
        errorBody.set("{\"detail\":\"현재 문항에 대한 답이 아닙니다. 화면을 새로고침해 주세요.\"}");
        var conflict = step(id, 1, "answer");
        assertEquals(409, conflict.getStatusCode().value());
        assertEquals("현재 문항에 대한 답이 아닙니다. 화면을 새로고침해 주세요.", message(conflict));

        // 409 외의 상류 오류 본문은 내부 정보일 수 있어 그대로 전달하지 않음
        errorBody.set("{\"detail\":\"private details\"}");
        for (int[] pair : new int[][]{{503, 503}, {422, 422}, {500, 502}}) {
            status.set(pair[0]);
            var failed = step(id, 1, "answer");
            assertEquals(pair[1], failed.getStatusCode().value());
            assertEquals("no-store", failed.getHeaders().getCacheControl());
            assertFalse(message(failed).contains("private details"));
        }
        // 200이어도 상태 형태가 아니면 저장하지 않음
        status.set(200);
        upstream.removeContext("/stuck/step");
        upstream.createContext("/stuck/step", exchange -> {
            byte[] bytes = "{\"stage\":1}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            try (var out = exchange.getResponseBody()) { out.write(bytes); }
        });
        assertEquals(502, step(id, 1, "answer").getStatusCode().value());
        assertEquals(before, new StuckSessionStore(directory.toString()).load(id));
    }

    @Test
    /** @brief 시작이 실패하면 세션 파일을 만들지 않고, 진입 정보가 없으면 AI core를 부르지 않는지 검증 */
    void doesNotCreateSessionWhenStartFails() throws Exception {
        status.set(503);
        assertEquals(503, controller.create(new StuckSessionController.CreateRequest(entry())).getStatusCode().value());
        assertEquals(422, controller.create(new StuckSessionController.CreateRequest(null)).getStatusCode().value());
        assertEquals(1, requests.size());
        try (var files = Files.list(directory)) { assertEquals(0, files.count()); }
    }

    @Test
    /** @brief 같은 세션의 진행 요청이 동시에 오면 두 번째를 409로 거부해 같은 단계가 두 번 진행되지 않는지 검증 */
    void rejectsConcurrentStepsOnSameSession() throws Exception {
        var id = start().path("id").asText();
        var latch = new CountDownLatch(1);
        gate.set(latch);
        requests.clear();
        var executor = Executors.newSingleThreadExecutor();
        try {
            var first = executor.submit(() -> step(id, 1, "answer"));
            // 첫 요청이 AI core에 도착해 잠금을 쥔 상태에서 두 번째 요청을 보냄
            while (requests.isEmpty()) Thread.sleep(10);
            assertEquals(409, step(id, 1, "answer").getStatusCode().value());
            latch.countDown();
            assertEquals(200, first.get(5, TimeUnit.SECONDS).getStatusCode().value());
            assertEquals(1, requests.size());
            // 잠금이 풀린 뒤에는 다음 버전 진행 가능
            gate.set(null);
            assertEquals(200, step(id, 2, "answer").getStatusCode().value());
        } finally { latch.countDown(); executor.shutdownNow(); }
    }

    @Test
    /** @brief 경로 조작·형식 위반 id와 없는 id를 404로 막고 저장 폴더 밖 파일을 읽지 않는지 검증 */
    void rejectsInvalidAndUnknownIds() throws Exception {
        for (String id : new String[]{"../secret", "A".repeat(32), "a".repeat(31), "a".repeat(32)}) {
            assertEquals(404, controller.get(id).getStatusCode().value());
            assertEquals(404, step(id, 1, "answer").getStatusCode().value());
        }
        assertTrue(requests.isEmpty());
        var id = start().path("id").asText();
        assertEquals(422, controller.step(id, new StuckSessionController.StepRequest(null, input("answer"))).getStatusCode().value());
        assertEquals(422, controller.step(id, new StuckSessionController.StepRequest(1, null)).getStatusCode().value());
        assertEquals(1, requests.size());
    }
}
