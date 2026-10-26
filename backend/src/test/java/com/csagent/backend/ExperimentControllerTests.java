// 파일 역할: 비교 실험 API의 정답 키 비공개·상쇄 배정·서버 시각 마감·중복 참가자·내보내기 규칙 검증
// - 실제 실험 자료(Task 1) 대신 src/test/resources/experiment/test-materials.json(가짜 개념 3개)을 씀
package com.csagent.backend;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import tools.jackson.databind.JsonNode;
import static org.junit.jupiter.api.Assertions.*;

class ExperimentControllerTests {
    private static final String MATERIALS = "experiment/test-materials.json";
    @TempDir Path directory;
    private final MutableClock clock = new MutableClock(Instant.parse("2026-11-20T01:00:00Z"));
    private ExperimentMaterials materials;
    private ExperimentStore store;
    private ExperimentController controller;

    // 역할: 테스트가 직접 움직이는 서버 시계; 실제 15분을 기다리지 않고 마감 전후를 재현
    static class MutableClock extends Clock {
        private Instant now;
        MutableClock(Instant now) { this.now = now; }
        void advance(Duration duration) { now = now.plus(duration); }
        @Override public Instant instant() { return now; }
        @Override public ZoneId getZone() { return ZoneOffset.UTC; }
        @Override public Clock withZone(ZoneId zone) { return this; }
    }

    @BeforeEach
    void setUp() {
        materials = new ExperimentMaterials(MATERIALS);
        store = new ExperimentStore(directory.toString());
        controller = new ExperimentController(materials, store, clock);
    }

    private static JsonNode body(ResponseEntity<?> response) { return ConversationStore.JSON.valueToTree(response.getBody()); }

    private JsonNode create(String participant, List<String> exclude) throws Exception {
        var response = controller.create(new ExperimentController.CreateRequest(participant, 3, "taken", exclude));
        assertEquals(200, response.getStatusCode().value(), String.valueOf(response.getBody()));
        assertEquals("no-store", response.getHeaders().getCacheControl());
        return body(response);
    }

    // 역할: 테스트 자료에서 정답 키를 읽어, 고른 개념은 맞히고 나머지는 틀린 사전 시험 답을 만듦
    private Map<String, String> pretestAnswers(String form, Set<String> correctConcepts) throws Exception {
        var raw = ConversationStore.JSON.readTree(getClass().getClassLoader().getResourceAsStream(MATERIALS));
        Map<String, String> answers = new HashMap<>();
        for (var concept : raw.path("concepts"))
            for (var item : concept.path("pre_post").path(form))
                if (!item.path("format").asString().equals("explanation"))
                    answers.put(item.path("id").asString(),
                            correctConcepts.contains(concept.path("id").asString()) ? item.path("answer").asString() : "틀린 답");
        return answers;
    }

    private JsonNode submitPretest(String id, Map<String, String> answers) throws Exception {
        var response = controller.pretest(id, new ExperimentController.AnswersRequest(answers));
        assertEquals(200, response.getStatusCode().value(), String.valueOf(response.getBody()));
        return body(response);
    }

    private ResponseEntity<?> event(String id, String type) throws Exception {
        return controller.event(id, new ExperimentController.EventRequest(type, null));
    }

    private JsonNode get(String id) throws Exception { return body(controller.get(id)); }

    private static String message(ResponseEntity<?> response) { return ((Map<?, ?>) response.getBody()).get("message").toString(); }

    @Test
    // 역할: 사전·사후 시험 화면 데이터에 정답 키·근거·루브릭이 섞이지 않는지 검증(Global Constraints 정답 키)
    // - 서버 저장본에는 채점 결과가 있으므로 응답을 고르는 단계에서 빠지면 학생이 정답을 볼 수 있음
    void pretestHidesKeys() throws Exception {
        var created = create("S1", List.of());
        assertEquals("pretest", created.path("stage").asString());
        assertEquals(32, created.path("id").asString().length());
        // 개념 3개 × 자동 채점 2문항; 서술 문항은 사전 시험에 없음
        assertEquals(6, created.path("pretest").size());
        for (var item : created.path("pretest")) {
            assertNotEquals("explanation", item.path("format").asString());
            for (String field : new String[]{"answer", "rationale", "rubric"}) assertFalse(item.has(field), field);
        }
        assertTrue(created.path("pretest").get(0).path("options").isObject());
        var id = created.path("id").asString();
        var views = new java.util.ArrayList<JsonNode>(List.of(created, get(id), submitPretest(id, Map.of())));
        event(id, "continue_reading");
        controller.next(id);
        event(id, "continue_reading");
        views.add(body(controller.next(id)));
        // 사후 시험은 두 개념 각각 자동 2문항 + 서술 1문항
        assertEquals(6, views.getLast().path("posttest").size());
        for (var view : views) {
            var text = view.toString();
            assertFalse(text.contains("KEY-"), text);
            assertFalse(text.contains("\"answer\""), text);
            assertFalse(text.contains("1,2,3"), text);
        }
    }

    @Test
    // 역할: 참가자 번호로 조건 순서와 사전·사후 형이 1절 상쇄 규칙대로 배정되는지 검증
    // - 홀수: 개념 1 제안 시스템 / 번호÷2 몫이 짝수: 사전 A형·사후 B형
    void assignmentFollowsParity() throws Exception {
        var expected = Map.of("S1", List.of("system", "A"), "S2", List.of("chat", "B"),
                "S3", List.of("system", "B"), "S4", List.of("chat", "A"));
        for (var entry : expected.entrySet()) {
            var created = create(entry.getKey(), List.of());
            var form = entry.getValue().get(1);
            assertTrue(created.path("pretest").get(0).path("id").asString().endsWith("-" + form + "1"), entry.getKey());
            var id = created.path("id").asString();
            // 모두 틀리면 점수가 같아 자료 파일 순서(hash-table, bst)대로 고름
            var learn1 = submitPretest(id, Map.of());
            assertEquals("learn1", learn1.path("stage").asString());
            assertEquals(entry.getValue().get(0), learn1.path("condition").asString(), entry.getKey());
            assertEquals("hash-table", learn1.path("concept").path("id").asString());
            assertEquals("해시 테이블 1부 글", learn1.path("passage_part1").asString());
            assertTrue(learn1.path("passage_part2").isNull());
            event(id, "continue_reading");
            var learn2 = body(controller.next(id));
            assertEquals("bst", learn2.path("concept").path("id").asString());
            assertNotEquals(learn1.path("condition").asString(), learn2.path("condition").asString());
            event(id, "continue_reading");
            var post = body(controller.next(id));
            var postForm = form.equals("A") ? "B" : "A";
            assertTrue(post.path("posttest").get(0).path("id").asString().endsWith("-" + postForm + "1"), entry.getKey());
        }
    }

    @Test
    // 역할: 사전 점수가 낮은 개념부터 고르고, 모르는 개념이 2개 미만이면 사전 점수 높음을 기록하는지 검증(Review Focus 3)
    void fewUnknownConceptsFlagged() throws Exception {
        // hash-table만 만점 → bst, heap을 고르고 둘 다 모르는 개념이라 표시 없음
        var low = create("S1", List.of());
        var learn = submitPretest(low.path("id").asString(), pretestAnswers("A", Set.of("hash-table")));
        assertEquals("bst", learn.path("concept").path("id").asString());
        assertFalse(store.load(low.path("id").asString()).path("pretest_high").asBoolean());
        // 두 개념 만점 → 남은 heap(0점)과 만점 중 파일 순서가 앞선 hash-table을 고르고 표시
        var high = create("S2", List.of());
        var id = high.path("id").asString();
        learn = submitPretest(id, pretestAnswers("B", Set.of("hash-table", "bst")));
        assertEquals("heap", learn.path("concept").path("id").asString());
        var saved = store.load(id);
        assertTrue(saved.path("pretest_high").asBoolean());
        assertEquals("hash-table", saved.path("learn").get(1).path("concept").asString());
        assertEquals(2, saved.path("pretest").path("scores").path("bst").asInt());
    }

    @Test
    // 역할: 남은 시간이 서버 시각 기준이라 새로고침·서버 재시작으로 늘어나지 않는지 검증(Review Focus 1)
    void deadlineFromServerClock() throws Exception {
        var id = create("S1", List.of()).path("id").asString();
        var learn = submitPretest(id, Map.of());
        assertEquals(900, learn.path("remaining_seconds").asInt());
        assertEquals("2026-11-20T01:15:00.000Z", learn.path("deadline").asString());
        clock.advance(Duration.ofMinutes(5));
        assertEquals(600, get(id).path("remaining_seconds").asInt());
        // 같은 저장 폴더로 컨트롤러를 새로 만들어도(서버 재시작) 같은 남은 시간
        var restarted = new ExperimentController(new ExperimentMaterials(MATERIALS), new ExperimentStore(directory.toString()), clock);
        assertEquals(600, body(restarted.get(id)).path("remaining_seconds").asInt());
        // 마감 전에는 2부를 읽지 않으면 다음 단계로 못 감
        assertEquals(409, controller.next(id).getStatusCode().value());
        // 2단계 마감은 2단계를 시작한 시각부터 15분
        event(id, "continue_reading");
        clock.advance(Duration.ofMinutes(2));
        var learn2 = body(controller.next(id));
        assertEquals(900, learn2.path("remaining_seconds").asInt());
        assertEquals("2026-11-20T01:22:00.000Z", learn2.path("deadline").asString());
    }

    @Test
    // 역할: 마감 뒤 도움 요청·계속 읽기를 거부하고, 마감 기록(timeout)만 받는지 검증(Review Focus 2)
    // - 마감 직전 화면이 늦게 보낸 이벤트가 O2(복귀)를 바꾸지 못하게 함
    void eventsAfterDeadlineRejected() throws Exception {
        var id = create("S1", List.of()).path("id").asString();
        submitPretest(id, Map.of());
        assertEquals(200, event(id, "help_open").getStatusCode().value());
        assertEquals(409, event(id, "timeout").getStatusCode().value());
        clock.advance(Duration.ofMinutes(15));
        assertEquals(0, get(id).path("remaining_seconds").asInt());
        var late = event(id, "continue_reading");
        assertEquals(409, late.getStatusCode().value());
        assertFalse(message(late).isBlank());
        assertEquals(409, event(id, "help_open").getStatusCode().value());
        assertEquals(200, event(id, "timeout").getStatusCode().value());
        var learn1 = store.load(id).path("learn").get(0);
        assertEquals(List.of("help_open", "timeout"), learn1.path("events").valueStream().map(e -> e.path("type").asString()).toList());
        assertFalse(learn1.has("continued_at"));
        assertEquals("2026-11-20T01:00:00.000Z", learn1.path("events").get(0).path("at").asString());
        // 마감 뒤에는 2부를 읽지 않았어도 다음 단계로 넘어감
        assertEquals("learn2", body(controller.next(id)).path("stage").asString());
        assertEquals(422, event(id, "unknown").getStatusCode().value());
    }

    @Test
    // 역할: 계속 읽기로 연 2부가 새로고침(GET)·서버 재시작 뒤에도 그대로 보이는지 검증(Review Focus 1)
    // - 복구 화면에서 2부가 사라지면 학생이 계속 읽기를 다시 눌러야 하고 O3 끝 시각이 흐려짐
    void continueReadingSurvivesReload() throws Exception {
        var id = create("S1", List.of()).path("id").asString();
        submitPretest(id, Map.of());
        assertTrue(get(id).path("passage_part2").isNull());
        event(id, "continue_reading");
        assertEquals("해시 테이블 2부 글", get(id).path("passage_part2").asString());
        var restarted = new ExperimentController(new ExperimentMaterials(MATERIALS), new ExperimentStore(directory.toString()), clock);
        assertEquals("해시 테이블 2부 글", body(restarted.get(id)).path("passage_part2").asString());
    }

    @Test
    // 역할: 단답 채점이 서버 기본 로캘과 무관한지 검증; 터키어 로캘에서는 "I".toLowerCase()가 점 없는 ı가 되어 정답을 틀리게 셀 수 있음
    void gradingIgnoresDefaultLocale() {
        var original = Locale.getDefault();
        try {
            Locale.setDefault(Locale.forLanguageTag("tr-TR"));
            assertTrue(materials.correct("exp-heap-A2", "FIFO"));
            assertTrue(materials.correct("exp-heap-A1", "c"));
        } finally { Locale.setDefault(original); }
    }

    @Test
    // 역할: 진행자가 같은 참가자 코드로 다시 만들면 기존 세션을 덮어쓰지 않고 409와 기존 id를 주는지 검증(Review Focus 5)
    void duplicateParticipantConflict() throws Exception {
        var id = create("S3", List.of()).path("id").asString();
        submitPretest(id, Map.of());
        var before = Files.readString(directory.resolve(id + ".json"));
        var again = controller.create(new ExperimentController.CreateRequest("S3", 1, "taking", List.of()));
        assertEquals(409, again.getStatusCode().value());
        assertEquals(id, body(again).path("id").asString());
        assertFalse(message(again).isBlank());
        assertEquals(before, Files.readString(directory.resolve(id + ".json")));
        try (var files = Files.list(directory)) { assertEquals(1, files.count()); }
    }

    @Test
    // 역할: 참가자 코드·학년·수강 여부·제외 개념 형식이 틀리면 저장 없이 422로 거부되는지 검증
    // - 이름 같은 개인정보를 코드 자리에 넣는 실수를 막음
    void invalidCreateRejected() throws Exception {
        for (var request : List.of(
                new ExperimentController.CreateRequest("홍길동", 3, "taken", List.of()),
                new ExperimentController.CreateRequest("S10", 3, "taken", List.of()),
                new ExperimentController.CreateRequest("S1", 5, "taken", List.of()),
                new ExperimentController.CreateRequest("S1", 3, "none", List.of()),
                new ExperimentController.CreateRequest("S1", 3, "taken", List.of("unknown")),
                new ExperimentController.CreateRequest("S1", 3, "taken", List.of("hash-table", "bst"))))
            assertEquals(422, controller.create(request).getStatusCode().value(), request.toString());
        assertFalse(Files.exists(directory) && Files.list(directory).findAny().isPresent());
        // 제외한 개념은 사전 시험에서도 빠짐
        var created = create("S1", List.of("bst"));
        assertEquals(4, created.path("pretest").size());
        assertFalse(created.toString().contains("exp-bst"));
        assertEquals(404, controller.get("../../etc/passwd").getStatusCode().value());
    }

    @Test
    // 역할: 사후 시험·설문 제출이 단계를 넘기고, 제출 뒤 수정·단계 건너뛰기를 막는지 검증
    void posttestAndSurveyFinishSession() throws Exception {
        var id = create("S2", List.of()).path("id").asString();
        submitPretest(id, Map.of());
        // 사전 시험은 한 번만 제출
        assertEquals(409, controller.pretest(id, new ExperimentController.AnswersRequest(Map.of())).getStatusCode().value());
        event(id, "continue_reading");
        controller.next(id);
        event(id, "continue_reading");
        var post = body(controller.next(id));
        assertEquals("posttest", post.path("stage").asString());
        assertFalse(post.has("remaining_seconds"));
        // posttest·survey 단계의 /next는 답 제출로만 넘어가므로 409
        assertEquals(409, controller.next(id).getStatusCode().value());
        var survey = body(controller.posttest(id, new ExperimentController.AnswersRequest(Map.of("exp-hash-table-A1", " c ", "exp-hash-table-A3", "설명"))));
        assertEquals("survey", survey.path("stage").asString());
        assertEquals(5, survey.path("survey").size());
        assertEquals(409, controller.posttest(id, new ExperimentController.AnswersRequest(Map.of())).getStatusCode().value());
        var done = body(controller.survey(id, new ExperimentController.AnswersRequest(Map.of("survey-1", "4"))));
        assertEquals("done", done.path("stage").asString());
        var saved = store.load(id);
        // 사후 A형 선택 문항은 대소문자·공백을 무시하고 채점, 서술 답은 눈가림 채점용으로 원문 보관
        assertEquals(1, saved.path("posttest").path("scores").path("hash-table").asInt());
        assertEquals("설명", saved.path("posttest").path("answers").path("exp-hash-table-A3").asString());
        assertEquals(409, event(id, "help_open").getStatusCode().value());
    }

    @Test
    // 역할: 내보내기가 로컬 요청에만 열리고, 기록에 이름 같은 개인정보 필드가 없는지 검증
    void exportHasNoNames() throws Exception {
        var id = create("S1", List.of()).path("id").asString();
        submitPretest(id, Map.of());
        var remote = new MockHttpServletRequest();
        remote.setRemoteAddr("10.0.0.5");
        assertEquals(403, controller.export(remote).getStatusCode().value());
        var local = new MockHttpServletRequest();
        local.setRemoteAddr("127.0.0.1");
        // 로컬 주소라도 프록시·터널 경유 표시 헤더가 있으면 원격 요청으로 보고 거부(vite 프록시는 127.0.0.1에서 중계함)
        for (String header : new String[]{"X-Forwarded-For", "Forwarded", "X-Real-IP"}) {
            var proxied = new MockHttpServletRequest();
            proxied.setRemoteAddr("127.0.0.1");
            proxied.addHeader(header, "for=10.0.0.5");
            assertEquals(403, controller.export(proxied).getStatusCode().value(), header);
        }
        var response = controller.export(local);
        assertEquals(200, response.getStatusCode().value());
        assertEquals("no-store", response.getHeaders().getCacheControl());
        var exported = body(response);
        assertEquals("test-1", exported.path("materials_version").asString());
        var session = exported.path("sessions").get(0);
        assertEquals("S1", session.path("participant").asString());
        Set<String> keys = new HashSet<>(session.propertyNames());
        assertEquals(Set.of("id", "participant", "grade", "dsa", "exclude", "created_at", "stage", "pretest_form",
                "posttest_form", "candidates", "pretest", "pretest_high", "learn"), keys);
        // 결과 계산 도구가 학습 순서를 slot으로 읽고, 시각을 문자열로 정렬해도 순서가 맞도록 밀리초 고정 자릿수 사용
        assertEquals(List.of(1, 2), session.path("learn").valueStream().map(l -> l.path("slot").asInt()).toList());
        assertEquals("2026-11-20T01:00:00.000Z", session.path("created_at").asString());
        assertEquals("2026-11-20T01:00:00.000Z", exported.path("exported_at").asString());
        var text = exported.toString().toLowerCase();
        for (String field : new String[]{"\"name\"", "\"email\"", "\"phone\"", "\"student_id\""}) assertFalse(text.contains(field), field);
    }
}
