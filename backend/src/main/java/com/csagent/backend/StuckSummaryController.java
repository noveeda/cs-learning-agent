/** @file
 * @brief 교수자용 막힘 집계 API; 세션 ID·학습자 답·로그 원문을 응답에 포함하지 않음
 */
package com.csagent.backend;

import java.io.IOException;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

@RestController
public class StuckSummaryController {
    private final StuckSessionSummary summary;
    private final ConceptMapController maps;
    /** @brief 세션 집계와 검증된 지도 조회 의존성 주입 */
    public StuckSummaryController(StuckSessionSummary summary, ConceptMapController maps) {
        this.summary = summary;
        this.maps = maps;
    }
    /** @brief 공개 개념 정보와 집계 수치만 반환; 프록시 경유 허용, 브라우저 캐시 금지 */
    @GetMapping("/api/stuck/summary")
    public ResponseEntity<?> get() {
        var map = maps.conceptMap();
        if (!map.getStatusCode().is2xxSuccessful() || !(map.getBody() instanceof JsonNode body) || !body.path("concepts").isArray())
            return ResponseEntity.status(502).header("Cache-Control", "no-store").body(Map.of("message", "개념 지도를 불러오지 못해 집계할 수 없습니다. 잠시 후 다시 시도해 주세요."));
        try {
            return ResponseEntity.ok().header("Cache-Control", "no-store").body(summary.summarize(body));
        } catch (IOException | tools.jackson.core.JacksonException e) {
            return ResponseEntity.status(503).header("Cache-Control", "no-store").body(Map.of("message", "막힘 기록을 읽지 못했습니다. 저장소 상태를 확인한 뒤 다시 시도해 주세요."));
        }
    }
}
