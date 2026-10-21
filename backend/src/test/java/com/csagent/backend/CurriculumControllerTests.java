// 파일 역할: 저장소 재생성과 단계 재시도에서 원본·버전이 보존되는지 검증
package com.csagent.backend;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.node.ObjectNode;
import static org.junit.jupiter.api.Assertions.*;

class CurriculumControllerTests {
    @TempDir Path directory;
    @Test
    // 역할: 입력 경계와 저장소 객체 재생성 후 체크포인트 복구 검증
    void validatesInputAndRestoresAfterStoreRestart() throws Exception {
        var controller = new CurriculumController("http://127.0.0.1:1", new CurriculumStore(directory.toString()));
        for (String topic : new String[]{null, " ", "x".repeat(301)})
            assertEquals(422, controller.create(new CurriculumController.DraftRequest(topic, "목적", "수준")).getStatusCode().value());
        var job = (ObjectNode) controller.create(new CurriculumController.DraftRequest("페이징", "취업", "기초")).getBody();
        var restarted = new CurriculumStore(directory.toString());
        assertEquals(job, restarted.load(job.path("id").asText()));
        assertThrows(ResponseStatusException.class, () -> restarted.load("../invalid"));
        assertThrows(ResponseStatusException.class, () -> restarted.load("a".repeat(32)));
    }
    @Test
    // 역할: 상류 실패 시 기존 상태 보존과 이미 저장한 버전의 중복 실행 차단 검증
    void preservesCheckpointOnFailureAndDoesNotReplaySavedSteps() throws Exception {
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var status = new AtomicInteger(503);
        var calls = new AtomicInteger();
        var upgrade = new AtomicReference<String>();
        upstream.createContext("/curriculum/step", exchange -> {
            calls.incrementAndGet();
            upgrade.set(exchange.getRequestHeaders().getFirst("Upgrade"));
            var state = (ObjectNode) ConversationStore.JSON.readTree(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            state.put("stage", "generate");
            state.withArray("units").addObject().put("id", "u1");
            byte[] bytes = (status.get() == 200 ? state.toString() : "private details").getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status.get(), bytes.length);
            try (var out = exchange.getResponseBody()) { out.write(bytes); }
        });
        upstream.start();
        try {
            var store = new CurriculumStore(directory.toString());
            var controller = new CurriculumController("http://127.0.0.1:" + upstream.getAddress().getPort(), store);
            var job = (ObjectNode) controller.create(new CurriculumController.DraftRequest("페이징", "취업", "기초")).getBody();
            var id = job.path("id").asText();
            var failed = controller.step(id, new CurriculumController.StepRequest(0));
            assertEquals(503, failed.getStatusCode().value());
            assertFalse(failed.getBody().toString().contains("private details"));
            assertEquals(job, store.load(id));
            status.set(200);
            var result = controller.step(id, new CurriculumController.StepRequest(0));
            assertEquals(200, result.getStatusCode().value());
            assertNull(upgrade.get());
            assertEquals(1, store.load(id).path("version").asInt());
            assertEquals(1, store.load(id).path("state").path("units").size());
            controller.step(id, new CurriculumController.StepRequest(0));
            assertEquals(2, calls.get());
            assertEquals(409, controller.step(id, new CurriculumController.StepRequest(9)).getStatusCode().value());
            store.acquire(id);
            try { assertThrows(ResponseStatusException.class, () -> controller.step(id, new CurriculumController.StepRequest(1))); }
            finally { store.release(id); }
            status.set(500);
            assertEquals(502, controller.step(id, new CurriculumController.StepRequest(1)).getStatusCode().value());
            assertEquals(1, new CurriculumStore(directory.toString()).load(id).path("version").asInt());
        } finally { upstream.stop(0); }
    }
}
