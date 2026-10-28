/**
 * @file
 * @brief 가짜 세션 파일로 반 요약의 세션별 빈도·과목 경계·최종 보충 결과·개인정보 제외 검증
 */
package com.csagent.backend;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.ResponseEntity;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class StuckSummaryTests {
    @TempDir Path directory;
    private StuckSessionStore store;
    private StuckSessionSummary summary;
    private MockMvc mvc;
    private final JsonNode map = ConversationStore.JSON.readTree("""
        {"title":"CS 전공","subjects":[{"id":"dsa","title":"자료구조"},{"id":"os","title":"운영체제"}],
         "concepts":[{"id":"queue","title":"큐","subject":"dsa"},
                     {"id":"priority","title":"우선순위","subject":"os"},
                     {"id":"cpu","title":"CPU 스케줄링","subject":"os"}]}
        """);

    /** @brief 실제 저장소와 집계를 연결하고 외부 지도 조회만 가짜 응답으로 대체 */
    @BeforeEach
    void setUp() {
        store = new StuckSessionStore(directory.toString());
        summary = new StuckSessionSummary(store);
        var maps = new ConceptMapController("http://127.0.0.1:1") {
            @Override public ResponseEntity<?> conceptMap() { return ResponseEntity.ok(map); }
        };
        mvc = MockMvcBuilders.standaloneSetup(new StuckSummaryController(summary, maps)).build();
    }

    /** @brief 학생 답·붙여넣은 문장·중복 진단·단계별 로그를 가진 저장본 생성 */
    private void save(int number, String stage, String probes, String fixes, int start, int end) throws Exception {
        String id = "%032x".formatted(number);
        var session = ConversationStore.JSON.createObjectNode().put("id", id).put("version", 7);
        var state = session.putObject("state").put("stage", stage).put("target", "cpu");
        state.putObject("entry").put("kind", "excerpt").put("excerpt", "private student text").put("return_to", "private-route");
        state.set("probes", ConversationStore.JSON.readTree(probes));
        state.set("fixes", ConversationStore.JSON.readTree(fixes));
        state.set("log", ConversationStore.JSON.readTree("""
            [{"at":%d,"kind":"diagnosis_started","detail":{"answer":"private answer"}},
             {"at":%d,"kind":"fix_concluded","detail":{"participant":"private person"}}]
            """.formatted(start, end)));
        store.save(session);
    }

    /** @brief 같은 세션의 중복 빈틈은 한 번만 세고 과목을 넘는 쌍과 최종 결과를 합산 */
    @Test
    void countsSessionsGapsPairsAndFinalOutcomes() throws Exception {
        save(1, "complete", """
            [{"concept":"queue","result":"gap","answer":"private answer"},
             {"concept":"queue","result":"gap"},{"concept":"priority","result":"known"}]
            """, """
            [{"concept":"queue","outcome":"passed"},{"concept":"cpu","outcome":"unresolved"}]
            """, 100, 180);
        save(2, "check", """
            [{"concept":"queue","result":"gap"},{"concept":"priority","result":"gap"},
             {"concept":"cpu","result":"self_known"}]
            """, """
            [{"concept":"queue","outcome":"passed"},{"concept":"priority","outcome":"pending"}]
            """, 200, 300);
        var identifying = ConversationStore.JSON.createObjectNode().put("id", "%032x".formatted(3));
        identifying.putObject("state").put("stage", "identify").putArray("log");
        store.save(identifying);
        var result = ConversationStore.JSON.valueToTree(summary.summarize(map));
        assertEquals(3, result.path("totalSessions").asInt());
        assertEquals(2, result.path("diagnosedSessions").asInt());
        assertEquals(100, result.path("earliestAt").asDouble());
        assertEquals(300, result.path("latestAt").asDouble());
        assertEquals(2, result.path("targets").get(0).path("count").asInt());
        assertEquals("queue", result.path("gaps").get(0).path("concept").path("id").asText());
        assertEquals("자료구조", result.path("gaps").get(0).path("concept").path("subjectTitle").asText());
        assertEquals(2, result.path("gaps").get(0).path("count").asInt());
        assertEquals(2, result.path("crossSubjectPairs").asInt());
        assertEquals(2, result.path("pairs").get(0).path("count").asInt());
        assertTrue(result.path("pairs").get(0).path("crossSubject").asBoolean());
        assertEquals("queue", result.path("outcomes").get(0).path("concept").path("id").asText());
        assertEquals(2, result.path("outcomes").get(0).path("passed").asInt());
        assertEquals(1, result.path("outcomes").get(1).path("unresolved").asInt());
        String json = result.toString();
        assertFalse(json.contains("private"));
        assertFalse(json.contains("%032x".formatted(1)));
        assertFalse(json.contains("answer"));
        assertFalse(json.contains("entry"));
    }

    /** @brief 신규 설치에서 폴더가 없거나 세션이 없어도 0과 빈 배열을 반환하고 폴더를 만들지 않음 */
    @Test
    void emptyDirectoryHasNoDatesOrSideEffects() throws Exception {
        Path missing = directory.resolve("not-created");
        var result = ConversationStore.JSON.valueToTree(new StuckSessionSummary(new StuckSessionStore(missing.toString())).summarize(map));
        assertEquals(0, result.path("totalSessions").asInt());
        assertEquals(0, result.path("diagnosedSessions").asInt());
        assertTrue(result.path("earliestAt").isNull());
        assertTrue(result.path("latestAt").isNull());
        assertTrue(result.path("gaps").isEmpty());
        assertTrue(result.path("pairs").isEmpty());
        assertTrue(result.path("outcomes").isEmpty());
        assertFalse(Files.exists(missing));
    }

    /** @brief 확정된 ID 형식의 JSON만 읽고 임시 파일·다른 이름·심볼릭 링크는 집계에서 제외 */
    @Test
    void ignoresTemporaryAndUnrelatedFiles() throws Exception {
        Files.writeString(directory.resolve("private.json"), "private contents");
        Files.writeString(directory.resolve("a".repeat(32) + ".tmp"), "unfinished");
        Path outside = directory.resolve("elsewhere.txt");
        Files.writeString(outside, "private contents");
        Files.createSymbolicLink(directory.resolve("b".repeat(32) + ".json"), outside);
        assertEquals(0, summary.summarize(map).totalSessions());
    }

    /** @brief 저장본의 임의 개념 문자열을 응답에 복사하지 않고 과목 미상으로 처리 */
    @Test
    void unknownConceptsCannotEchoPrivateText() throws Exception {
        save(1, "remediate", "[{\"concept\":\"private student name\",\"result\":\"gap\"}]", "[]", 100, 200);
        var result = ConversationStore.JSON.valueToTree(summary.summarize(map));
        assertFalse(result.toString().contains("private"));
        assertEquals("unknown", result.path("gaps").get(0).path("concept").path("subject").asText());
        assertEquals(0, result.path("crossSubjectPairs").asInt());
    }

    /** @brief 프록시로 접속해도 집계만 반환하고 캐시를 남기지 않는 공개 계약 확인 */
    @Test
    void exposesOnlyReadOnlyAggregatesThroughProxy() throws Exception {
        mvc.perform(get("/api/stuck/summary").header("X-Forwarded-For", "192.0.2.1"))
            .andExpect(status().isOk()).andExpect(header().string("Cache-Control", "no-store"))
            .andExpect(jsonPath("$.totalSessions").value(0));
    }

    /** @brief 손상된 저장본을 정상 집계처럼 숨기거나 오류에 저장 경로를 노출하지 않음 */
    @Test
    void corruptSessionReturnsSanitizedError() throws Exception {
        Files.writeString(directory.resolve("a".repeat(32) + ".json"), "private invalid json");
        var response = mvc.perform(get("/api/stuck/summary")).andExpect(status().isServiceUnavailable()).andReturn().getResponse();
        assertFalse(response.getContentAsString().contains("private"));
        assertFalse(response.getContentAsString().contains(directory.toString()));
    }
    /** @brief 상위 10쌍을 잘라도 과목 경계 합계는 전체 관찰 빈도를 보존하고 동률 순서를 고정 */
    @Test
    void limitsRankedPairsWithoutTruncatingCrossSubjectTotal() throws Exception {
        var customMap = (ObjectNode) map.deepCopy();
        var concepts = customMap.withArray("concepts");
        for (int i = 0; i < 12; i++) {
            String id = "gap-%02d".formatted(i);
            concepts.addObject().put("id", id).put("title", "선수 개념 " + i).put("subject", "dsa");
            save(i + 1, "diagnose", "[{\"concept\":\"" + id + "\",\"result\":\"gap\"}]", "[]", 100, 200);
        }
        var result = summary.summarize(customMap);
        assertEquals(12, result.totalSessions());
        assertEquals(12, result.crossSubjectPairs());
        assertEquals(10, result.pairs().size());
        assertEquals("gap-00", result.pairs().getFirst().gap().id());
        assertEquals("gap-09", result.pairs().getLast().gap().id());
    }

    /** @brief 진단에 진입했지만 아직 답하지 않은 세션은 빈틈·보충 결과를 지어내지 않음 */
    @Test
    void includesDiagnosisEntryBeforeAnyProbeAnswer() throws Exception {
        save(1, "diagnose", "[]", "[]", 100, 200);
        var before = Files.readString(directory.resolve("%032x.json".formatted(1)));
        var result = summary.summarize(map);
        assertEquals(1, result.diagnosedSessions());
        assertTrue(result.gaps().isEmpty());
        assertTrue(result.outcomes().isEmpty());
        assertEquals(before, Files.readString(directory.resolve("%032x.json".formatted(1))));
    }

    /** @brief 공개 제목·과목을 제공하는 지도 장애도 원문·서버 경로가 없는 오류로 안내 */
    @Test
    void unavailableMapReturnsSanitizedError() throws Exception {
        var unavailable = new ConceptMapController("http://127.0.0.1:1") {
            @Override public ResponseEntity<?> conceptMap() { return ResponseEntity.status(502).body("private upstream path"); }
        };
        var local = MockMvcBuilders.standaloneSetup(new StuckSummaryController(summary, unavailable)).build();
        var response = local.perform(get("/api/stuck/summary")).andExpect(status().isBadGateway()).andReturn().getResponse();
        assertFalse(response.getContentAsString().contains("private"));
        assertEquals("no-store", response.getHeader("Cache-Control"));
    }
}
