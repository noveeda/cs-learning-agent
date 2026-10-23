// 파일 역할: 개념 지도 중계가 정상 지도는 그대로 전달하고 상류 오류·깨진 응답은 내용 없이 막는지 검증
package com.csagent.backend;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import static org.junit.jupiter.api.Assertions.*;

class ConceptMapControllerTests {
    @Test
    // 역할: 정상 지도 전달, 상류 오류·깨진 형식의 내용 비노출 검증
    void relaysValidMapAndHidesUpstreamFailures() throws Exception {
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var status = new AtomicInteger(200);
        var body = new AtomicReference<>("{\"id\":\"dsa\",\"concepts\":[{\"id\":\"array\"}]}");
        upstream.createContext("/concept-map", exchange -> {
            byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status.get(), bytes.length);
            try (var out = exchange.getResponseBody()) { out.write(bytes); }
        });
        upstream.start();
        try {
            var controller = new ConceptMapController("http://127.0.0.1:" + upstream.getAddress().getPort());
            var ok = controller.conceptMap();
            assertEquals(200, ok.getStatusCode().value());
            assertEquals("array", ((JsonNode) ok.getBody()).path("concepts").get(0).path("id").asText());

            status.set(500);
            body.set("private details");
            var failed = controller.conceptMap();
            assertEquals(502, failed.getStatusCode().value());
            assertFalse(failed.getBody().toString().contains("private details"));

            status.set(200);
            for (String broken : new String[]{"not json", "[]", "{\"id\":\"dsa\"}"}) {
                body.set(broken);
                assertEquals(502, controller.conceptMap().getStatusCode().value());
            }
        } finally { upstream.stop(0); }
    }

    @Test
    // 역할: AI core가 꺼져 있을 때 연결 오류를 502 안내로 바꾸는지 검증
    void returnsBadGatewayWhenCoreIsDown() {
        assertEquals(502, new ConceptMapController("http://127.0.0.1:1").conceptMap().getStatusCode().value());
    }
}
