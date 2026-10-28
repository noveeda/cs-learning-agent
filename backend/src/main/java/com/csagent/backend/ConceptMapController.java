/**
 * @file
 * @brief 개념 지도 조회 API; AI core가 검증한 지도를 브라우저에 그대로 중계
 */
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
@RequestMapping("/api")
public class ConceptMapController {
    private final String coreUrl;
    // Uvicorn 중계에서 HTTP/2 업그레이드 문제가 발생해 HTTP/1.1로 고정
    private final HttpClient client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5)).build();

    /** @brief AI core 연결 주소 주입 */
    public ConceptMapController(@Value("${ai-core.base-url:http://127.0.0.1:8001}") String coreUrl) {
        this.coreUrl = coreUrl;
    }

    @GetMapping("/concept-map")
    /** @brief AI core의 검증된 지도를 받아 중계; 상류 오류 내용은 숨기고 502로 안내 */
    public ResponseEntity<?> conceptMap() {
        try {
            var request = HttpRequest.newBuilder(URI.create(coreUrl + "/concept-map"))
                    .timeout(Duration.ofSeconds(10)).GET().build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) return error(502, "개념 지도를 불러오지 못했습니다. AI core 상태를 확인해 주세요.");
            var map = ConversationStore.JSON.readTree(response.body());
            // 최소 형태만 확인: 그래프 규칙 검증은 AI core가 담당하고, 여기서는 깨진 응답이 화면까지 가지 않게 막음
            if (!map.isObject() || !map.path("concepts").isArray())
                return error(502, "개념 지도 형식이 올바르지 않습니다.");
            return ResponseEntity.ok().header("Cache-Control", "no-store").body(map);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return error(503, "요청이 중단됐습니다. 다시 시도해 주세요.");
        } catch (IOException | tools.jackson.core.JacksonException e) {
            return error(502, "개념 지도를 불러오지 못했습니다. AI core 상태를 확인해 주세요.");
        }
    }

    /** @brief 브라우저에 보낼 안내 메시지만 담은 오류 응답 생성 */
    private static ResponseEntity<?> error(int status, String message) {
        return ResponseEntity.status(status).header("Cache-Control", "no-store").body(Map.of("message", message));
    }
}
