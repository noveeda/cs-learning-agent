/**
 * @file
 * @brief 실제 학생 실험 자료의 과목 확장·평행 문항·계산 정답과 제외 설정을 검증
 * - 가짜 자료를 쓰는 API 테스트와 별개로 배포되는 materials.json을 직접 읽음
 * - 세션은 @TempDir에만 저장해 실제 학생 실험 기록을 만들지 않음
 */
package com.csagent.backend;

import java.nio.file.Path;
import java.time.Clock;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import static org.junit.jupiter.api.Assertions.*;

class ExperimentMaterialsTests {
    private static final List<String> DSA = List.of("hash-table", "bst", "heap", "quick-sort",
            "merge-sort", "bfs", "topological-sort", "dynamic-programming");
    private static final List<String> ADDED = List.of("sjf", "priority-scheduling", "btree-index", "routing", "cache-locality");
    private final ExperimentMaterials materials = new ExperimentMaterials("experiment/materials.json");
    @TempDir Path directory;

    /** @brief 기존 자료구조 순서와 새 5개 개념을 보존하고, 기록에서 자료 개정판을 구분하는지 확인 */
    @Test
    void productionMaterialsIncludeThirteenConcepts() {
        assertEquals(13, materials.conceptIds().size());
        assertEquals(DSA, materials.conceptIds().subList(0, 8));
        assertEquals(ADDED, materials.conceptIds().subList(8, 13));
        assertEquals("2", materials.version());
    }

    /** @brief 읽기 분량·A/B형 구성·보기와 루브릭을 검사해 추가 자료가 실험 중 누락되거나 채점 불가능해지는 상황 방지 */
    @Test
    void readingsAndParallelFormsFollowMaterialContract() {
        Set<String> ids = new HashSet<>();
        for (String id : materials.conceptIds()) {
            var concept = materials.concept(id);
            assertTrue(concept.part1().length() >= 400 && concept.part1().length() <= 600, id + " 1부");
            assertTrue(concept.part2().length() >= 200 && concept.part2().length() <= 400, id + " 2부");
            for (String form : List.of("A", "B")) {
                var items = concept.forms().get(form);
                assertEquals(List.of("short_exact", "multiple_choice", "explanation"),
                        items.stream().map(item -> item.path("format").asString()).toList(), id + form);
                assertEquals(2, concept.autoItems(form).size());
                for (int i = 0; i < items.size(); i++) {
                    var item = items.get(i);
                    String itemId = "exp-" + id + "-" + form + (i + 1);
                    assertEquals(itemId, item.path("id").asString());
                    assertTrue(ids.add(itemId), itemId);
                    assertFalse(item.path("question").asString().isBlank(), itemId);
                    assertFalse(item.path("rationale").asString().isBlank(), itemId);
                    if (i == 1) {
                        assertEquals(4, item.path("options").size(), itemId);
                        assertTrue(item.path("options").has(item.path("answer").asString()), itemId);
                    } else if (i == 2) {
                        assertEquals(3, item.path("rubric").size(), itemId);
                        item.path("rubric").forEach(point -> assertFalse(point.asString().isBlank(), itemId));
                    }
                    var view = materials.publicItem(id, item);
                    assertFalse(view.has("answer") || view.has("rationale") || view.has("rubric"), itemId);
                }
            }
        }
        assertEquals(78, ids.size());
    }

    /**
     * @brief 새 단답 10개를 손으로 전개한 간트 구간·경로·페이지 집합·배열 블록 수와 대조
     * - SJF A: P1 0~5, P2 5~7, P4 7~8, P3 8~10 / B: P1 0~4, P2 4~7, P4 7~9, P3 9~12
     * - 우선순위 A: P1 0~4, P2 4~7, P3 7~9, P4 9~10 / B: P1 0~3, P2 3~4, P3 4~6, P4 6~10
     * - 동점은 도착 시각 다음 이름 순; 입력 목록에서 P3가 앞서도 P2부터 실행
     */
    @Test
    void newShortAnswersMatchIndependentWorkedExamples() {
        assertAnswer("sjf", "A", "8," + (0 + 4 + 7 + 1));
        assertAnswer("sjf", "B", "9," + (0 + 3 + 8 + 2));
        assertAnswer("priority-scheduling", "A", "7," + (0 + 3 + 6 + 7));
        assertAnswer("priority-scheduling", "B", "4," + (0 + 2 + 3 + 4));
        assertAnswer("btree-index", "A", "2," + Set.of("I1", "L1", "L2", "I2", "L3").size());
        assertAnswer("btree-index", "B", "2," + Set.of("J1", "M1", "J2", "M2", "M3").size());
        int routeA = Math.min(3 + 1 + 3, 2 + 2 + 3);
        int routeB = Math.min(2 + 2 + 5, 1 + 2 + 5);
        assertAnswer("routing", "A", routeA + "," + (routeA - 6));
        assertAnswer("routing", "B", routeB + "," + (routeB - 4));
        int blocksA = (12 * 4 + 15) / 16, blocksB = (16 * 2 + 7) / 8;
        assertAnswer("cache-locality", "A", blocksA + "," + (2 * 12 - blocksA));
        assertAnswer("cache-locality", "B", blocksB + "," + (2 * 16 - blocksB));
    }

    /** @brief 새 자료로 26문항을 제공하고 미수강 4과목을 제외하면 기존 16문항만 제공하는지 실제 API로 검증 */
    @Test
    void expandedPretestRespectsUntakenSubjectExclusions() throws Exception {
        var controller = new ExperimentController(materials, new ExperimentStore(directory.toString()), Clock.systemUTC());
        var all = controller.create(new ExperimentController.CreateRequest("S1", 3, "taken", List.of()));
        assertEquals(200, all.getStatusCode().value());
        var full = ConversationStore.JSON.valueToTree(all.getBody());
        assertEquals(26, full.path("pretest").size());
        assertFalse(full.toString().contains("\"rationale\""));
        assertFalse(full.toString().contains("\"rubric\""));
        var excluded = controller.create(new ExperimentController.CreateRequest("S2", 3, "taken", ADDED));
        assertEquals(200, excluded.getStatusCode().value());
        var pretest = ConversationStore.JSON.valueToTree(excluded.getBody()).path("pretest");
        assertEquals(16, pretest.size());
        for (var item : pretest) assertTrue(DSA.contains(item.path("concept").asString()));
    }

    /** @brief 개념과 형에서 단답 문항을 찾아 재계산 값으로 실제 채점이 성공하는지 확인 */
    private void assertAnswer(String concept, String form, String expected) {
        JsonNode item = materials.concept(concept).autoItems(form).getFirst();
        assertEquals(expected, item.path("answer").asString(), item.path("id").asString());
        assertTrue(materials.correct(item.path("id").asString(), expected));
    }
}
