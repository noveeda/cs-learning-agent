/**
 * @file
 * @brief 비교 실험 세션 API; 사전 시험 채점·개념과 조건 배정·학습 마감·사건 기록·내보내기를 서버 시각 기준으로 처리
 * - 실험 절차와 배정 규칙은 docs/superpowers/plans/2026-10-26-mvp3-experiment-mode.md 1절을 따름
 * - 응답에는 화면에 필요한 필드만 담고 정답 키·채점 결과·사건 기록은 서버에만 둠(내보내기 제외)
 */
package com.csagent.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.net.InetAddress;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/experiments")
public class ExperimentController {
    // 학습 단계 시간 상한(실험 절차 ②·③): 두 조건이 같은 시간 안에서 비교되도록 하는 실험 조건이며 운영 보호 장치가 아님
    static final Duration LEARN_LIMIT = Duration.ofMinutes(15);
    // 답 하나의 최대 길이: 서술 답이 기록 파일을 비정상적으로 키우지 않게 하는 저장 보호 장치(서술 3문항 분량으로 충분)
    static final int MAX_ANSWER_LENGTH = 2000;
    // 기록 시각 형식: 밀리초까지 고정 자릿수 UTC; Instant.toString은 밀리초가 0이면 생략해 문자열 정렬 순서가 같은 초 안에서 어긋남
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC);
    // 프록시 경유 표시 헤더: 같은 컴퓨터의 프록시가 중계하면 원격 주소가 127.0.0.1로 보여 주소만으로는 외부 요청을 가려낼 수 없음
    // - 이 헤더를 붙이는 프록시(nginx, xfwd를 켠 vite 등)만 걸러짐; 헤더를 붙이지 않는 중계(현재 vite 설정, SSH 터널)는 막지 못함
    private static final String[] FORWARD_HEADERS = {"X-Forwarded-For", "Forwarded", "X-Real-IP"};
    private static final Set<String> EVENT_TYPES = Set.of("help_open", "stuck_session", "chat_conversation", "continue_reading", "timeout");

    private final ExperimentMaterials materials;
    private final ExperimentStore store;
    private final Clock clock;

    /** @brief 운영용 생성자; 서버 시계(UTC)를 씀 */
    @Autowired
    public ExperimentController(ExperimentMaterials materials, ExperimentStore store) {
        this(materials, store, Clock.systemUTC());
    }
    /** @brief 시계 주입 생성자; 테스트가 15분 마감 전후를 실제로 기다리지 않고 재현할 때 사용 */
    ExperimentController(ExperimentMaterials materials, ExperimentStore store, Clock clock) {
        this.materials = materials;
        this.store = store;
        this.clock = clock;
    }

    /** @brief 세션 시작 요청; 참가자 코드(S1~S99)·학년(1~4)·자료구조 수강 여부·제외할 개념만 받음 */
    public record CreateRequest(String participant, Integer grade, String dsa, List<String> exclude) {}
    /** @brief 사전·사후 시험과 설문 제출; 문항 ID → 답 */
    public record AnswersRequest(Map<String, String> answers) {}
    /** @brief 학습 단계 사건; ref는 막힘 세션·대화 ID처럼 사건이 가리키는 대상 */
    public record EventRequest(String type, String ref) {}

    // 동시 요청 처리: 쓰기 요청은 synchronized로 한 번에 하나씩 처리
    // - 막힘 세션과 달리 LLM 호출 없이 파일만 쓰므로 순서대로 기다리게 해도 빠름
    // - 409로 돌려보내면 화면이 거의 동시에 보내는 help_open·chat_conversation 중 하나가 유실될 수 있어 대기 방식을 씀
    // - 참가자 중복 확인과 저장 사이에 다른 생성 요청이 끼어들지 못하게 하는 역할도 함

    @PostMapping
    /** @brief 새 세션을 만들고 사전 시험 화면을 반환; 같은 참가자 코드가 있으면 덮어쓰지 않고 409와 기존 id 반환 */
    public synchronized ResponseEntity<?> create(@RequestBody CreateRequest body) throws IOException {
        if (!materials.available()) return error(503, "실험 자료가 아직 준비되지 않았습니다.");
        if (body == null || body.participant() == null || !body.participant().matches("S[1-9][0-9]?"))
            return error(422, "참가자 코드는 S1~S99 중 하나여야 합니다.");
        if (body.grade() == null || body.grade() < 1 || body.grade() > 4) return error(422, "학년은 1~4 중 하나여야 합니다.");
        if (!"taken".equals(body.dsa()) && !"taking".equals(body.dsa())) return error(422, "자료구조 수강 여부는 taken 또는 taking이어야 합니다.");
        var exclude = new LinkedHashSet<>(body.exclude() == null ? List.of() : body.exclude());
        if (!materials.conceptIds().containsAll(exclude)) return error(422, "제외할 개념 중 실험 자료에 없는 개념이 있습니다.");
        var candidates = materials.conceptIds().stream().filter(id -> !exclude.contains(id)).toList();
        if (candidates.size() < 2) return error(422, "제외하고 남은 후보 개념이 2개 이상이어야 합니다.");
        var existing = store.findByParticipant(body.participant());
        if (existing != null) return ResponseEntity.status(409).header("Cache-Control", "no-store").body(Map.of(
                "message", "이미 같은 참가자 코드의 세션이 있습니다. 기존 세션을 이어서 진행해 주세요.", "id", existing.path("id").asString()));

        // 사전 형 상쇄: 번호를 2로 나눈 몫이 짝수면 사전 A형·사후 B형, 홀수면 반대(S1 A, S2 B, S3 B, S4 A)
        int number = participantNumber(body.participant());
        String pretestForm = (number / 2) % 2 == 0 ? "A" : "B";
        var session = ConversationStore.JSON.createObjectNode();
        session.put("id", UUID.randomUUID().toString().replace("-", "")).put("participant", body.participant())
                .put("grade", body.grade()).put("dsa", body.dsa());
        exclude.forEach(session.putArray("exclude")::add);
        session.put("created_at", iso(now())).put("stage", "pretest")
                .put("pretest_form", pretestForm).put("posttest_form", pretestForm.equals("A") ? "B" : "A");
        candidates.forEach(session.putArray("candidates")::add);
        store.save(session);
        return ok(session);
    }

    /**
     * @brief 참가자 코드에서 번호를 꺼냄(S7 → 7, S12 → 12); 조건 순서·사전 형 배정 규칙의 입력
     * - 참가자가 9명을 넘을 수 있어 첫 글자 하나가 아니라 S 뒤 숫자 전체를 읽음
     */
    private static int participantNumber(String participant) {
        return Integer.parseInt(participant.substring(1));
    }

    @GetMapping("/{id}")
    /** @brief 현재 단계와 화면 데이터 반환; 새로고침·브라우저 재시작 뒤 같은 단계와 남은 시간으로 복구할 때 사용 */
    public ResponseEntity<?> get(@PathVariable String id) throws IOException {
        var session = store.load(id);
        return session == null ? notFound() : ok(session);
    }

    @PostMapping("/{id}/pretest")
    /**
     * @brief 사전 시험을 키로 채점하고 개념 1·2와 조건을 배정한 뒤 개념 1 학습을 시작
     * - 점수가 낮은 개념부터 고르고 동점이면 자료 파일 순서(안정 정렬)를 따름
     * - 고른 개념 중 만점이 있으면(모르는 개념이 2개 미만) pretest_high로 표시해 보고서에서 따로 봄
     */
    public synchronized ResponseEntity<?> pretest(@PathVariable String id, @RequestBody AnswersRequest body) throws IOException {
        var session = store.load(id);
        if (session == null) return notFound();
        if (!session.path("stage").asString().equals("pretest")) return error(409, "사전 시험은 이미 제출했습니다.");
        String form = session.path("pretest_form").asString();
        List<String> candidates = session.path("candidates").valueStream().map(JsonNode::asString).toList();
        Set<String> allowed = new LinkedHashSet<>();
        candidates.forEach(c -> materials.concept(c).autoItems(form).forEach(item -> allowed.add(item.path("id").asString())));
        var answers = answers(body, allowed);
        if (answers == null) return error(422, "답 형식이 올바르지 않습니다.");

        var scores = ConversationStore.JSON.createObjectNode();
        Map<String, Integer> byConcept = new HashMap<>();
        for (String concept : candidates) {
            int score = score(concept, form, answers);
            scores.put(concept, score);
            byConcept.put(concept, score);
        }
        var chosen = new ArrayList<>(candidates);
        chosen.sort(Comparator.comparingInt(byConcept::get));
        boolean high = chosen.subList(0, 2).stream().anyMatch(c -> byConcept.get(c) == materials.concept(c).autoItems(form).size());
        // 조건 상쇄: 참가자 번호가 홀수면 개념 1을 제안 시스템, 짝수면 채팅으로 학습
        boolean systemFirst = participantNumber(session.path("participant").asString()) % 2 == 1;

        var pretest = session.putObject("pretest");
        pretest.put("submitted_at", iso(now()));
        pretest.set("answers", answers);
        pretest.set("scores", scores);
        session.put("pretest_high", high);
        var learn = session.putArray("learn");
        // slot: 학습 순서(1 = 개념 1, 2 = 개념 2); 결과 계산 도구가 배열 위치 대신 이 값으로 순서를 읽음
        var first = learn.addObject().put("slot", 1).put("concept", chosen.get(0)).put("condition", systemFirst ? "system" : "chat");
        learn.addObject().put("slot", 2).put("concept", chosen.get(1)).put("condition", systemFirst ? "chat" : "system");
        startLearning(first);
        session.put("stage", "learn1");
        store.save(session);
        return ok(session);
    }

    @PostMapping("/{id}/events")
    /**
     * @brief 학습 단계 사건을 서버 시각으로 기록; continue_reading이면 2부 글을 열어 줌
     * - 마감 뒤 사건은 409로 거부해 늦게 도착한 요청이 O2(시간 안 복귀)·O3(걸린 시간)를 바꾸지 못하게 함
     * - timeout만 예외: 화면이 마감 순간에 보내는 기록이라 마감 뒤에만 받고 한 번만 남김
     */
    public synchronized ResponseEntity<?> event(@PathVariable String id, @RequestBody EventRequest body) throws IOException {
        if (body == null || !EVENT_TYPES.contains(body.type())) return error(422, "알 수 없는 사건 종류입니다.");
        if (body.ref() != null && body.ref().length() > 64) return error(422, "사건 대상 ID가 너무 깁니다.");
        var session = store.load(id);
        if (session == null) return notFound();
        var learn = currentLearn(session);
        if (learn == null) return error(409, "학습 단계가 아닙니다.");
        Instant now = now();
        boolean expired = !now.isBefore(Instant.parse(learn.path("deadline").asString()));
        var events = learn.withArray("events");
        if (body.type().equals("timeout")) {
            if (!expired) return error(409, "아직 학습 시간이 남아 있습니다.");
            if (events.valueStream().anyMatch(e -> e.path("type").asString().equals("timeout"))) return ok(session);
        } else if (expired) return error(409, "학습 시간이 끝났습니다. 다음 단계로 넘어가 주세요.");
        // 계속 읽기는 처음 누른 시각만 의미가 있어(O3 끝 시각) 재요청은 기록 없이 같은 화면을 돌려줌
        if (body.type().equals("continue_reading") && learn.has("continued_at")) return ok(session);
        var event = events.addObject().put("type", body.type());
        if (body.ref() != null) event.put("ref", body.ref());
        event.put("at", iso(now));
        if (body.type().equals("continue_reading")) learn.put("continued_at", iso(now));
        store.save(session);
        return ok(session);
    }

    @PostMapping("/{id}/next")
    /**
     * @brief learn1 → learn2 → posttest로 넘김; 2부를 읽었거나 마감이 지났을 때만 허용
     * - posttest·survey는 답 제출 요청이 다음 단계로 넘기므로 여기서는 409
     */
    public synchronized ResponseEntity<?> next(@PathVariable String id) throws IOException {
        var session = store.load(id);
        if (session == null) return notFound();
        var learn = currentLearn(session);
        if (learn == null) return error(409, "이 단계는 답을 제출하면 다음 단계로 넘어갑니다.");
        Instant now = now();
        if (!learn.has("continued_at") && now.isBefore(Instant.parse(learn.path("deadline").asString())))
            return error(409, "글의 2부를 읽거나 학습 시간이 끝나야 다음 단계로 넘어갈 수 있습니다.");
        learn.put("ended_at", iso(now));
        if (session.path("stage").asString().equals("learn1")) {
            startLearning((ObjectNode) session.path("learn").get(1));
            session.put("stage", "learn2");
        } else session.put("stage", "posttest");
        store.save(session);
        return ok(session);
    }

    @PostMapping("/{id}/posttest")
    /** @brief 사후 시험 답을 저장하고 자동 채점 문항 점수를 계산한 뒤 설문 단계로 넘김; 서술 답은 눈가림 채점용으로 원문 보관 */
    public synchronized ResponseEntity<?> posttest(@PathVariable String id, @RequestBody AnswersRequest body) throws IOException {
        var session = store.load(id);
        if (session == null) return notFound();
        if (!session.path("stage").asString().equals("posttest")) return error(409, "지금은 사후 시험을 제출할 수 없습니다.");
        String form = session.path("posttest_form").asString();
        Set<String> allowed = new LinkedHashSet<>();
        for (var learn : session.path("learn"))
            materials.concept(learn.path("concept").asString()).forms().get(form).forEach(item -> allowed.add(item.path("id").asString()));
        var answers = answers(body, allowed);
        if (answers == null) return error(422, "답 형식이 올바르지 않습니다.");
        var scores = ConversationStore.JSON.createObjectNode();
        for (var learn : session.path("learn")) {
            String concept = learn.path("concept").asString();
            scores.put(concept, score(concept, form, answers));
        }
        var posttest = session.putObject("posttest");
        posttest.put("submitted_at", iso(now()));
        posttest.set("answers", answers);
        posttest.set("scores", scores);
        session.put("stage", "survey");
        store.save(session);
        return ok(session);
    }

    @PostMapping("/{id}/survey")
    /** @brief 설문 답을 저장하고 실험을 끝냄 */
    public synchronized ResponseEntity<?> survey(@PathVariable String id, @RequestBody AnswersRequest body) throws IOException {
        var session = store.load(id);
        if (session == null) return notFound();
        if (!session.path("stage").asString().equals("survey")) return error(409, "지금은 설문을 제출할 수 없습니다.");
        var answers = answers(body, materials.surveyIds());
        if (answers == null) return error(422, "답 형식이 올바르지 않습니다.");
        var survey = session.putObject("survey");
        survey.put("submitted_at", iso(now()));
        survey.set("answers", answers);
        session.put("stage", "done").put("completed_at", iso(now()));
        store.save(session);
        return ok(session);
    }

    @GetMapping("/export")
    /** @brief 모든 세션 기록을 결과 계산용 JSON으로 반환; 답 원문이 있어 이 서버 컴퓨터(loopback)에서 온 요청만 허용 */
    public ResponseEntity<?> export(HttpServletRequest request) throws IOException {
        if (!loopback(request.getRemoteAddr()) || forwarded(request)) return error(403, "실험 기록 내보내기는 서버 컴퓨터에서만 할 수 있습니다.");
        var body = ConversationStore.JSON.createObjectNode();
        body.put("exported_at", iso(now())).put("materials_version", materials.version());
        store.all().forEach(body.putArray("sessions")::add);
        return ResponseEntity.ok().header("Cache-Control", "no-store").body(body);
    }

    /** @brief 학습 단계 시작 시각과 마감 시각을 서버 시계로 기록; 화면은 이 마감에서 남은 시간을 받아 새로고침해도 늘어나지 않음 */
    private void startLearning(ObjectNode learn) {
        Instant now = now();
        learn.put("started_at", iso(now)).put("deadline", iso(now.plus(LEARN_LIMIT)));
        learn.putArray("events");
    }

    /** @brief 한 개념의 해당 형 자동 채점 문항 중 맞힌 수; 답하지 않은 문항은 오답 */
    private int score(String concept, String form, ObjectNode answers) {
        return (int) materials.concept(concept).autoItems(form).stream().filter(item -> {
            var answer = answers.path(item.path("id").asString());
            return answer.isString() && materials.correct(item.path("id").asString(), answer.asString());
        }).count();
    }

    /** @brief 현재 학습 단계 기록 반환; 학습 단계가 아니면 null */
    private static ObjectNode currentLearn(ObjectNode session) {
        return switch (session.path("stage").asString()) {
            case "learn1" -> (ObjectNode) session.path("learn").get(0);
            case "learn2" -> (ObjectNode) session.path("learn").get(1);
            default -> null;
        };
    }

    /** @brief 제출된 답에서 이 단계 문항 ID만 남김; 다른 단계 문항이나 알 수 없는 ID는 버리고, 너무 긴 답은 null(422) */
    private static ObjectNode answers(AnswersRequest body, Set<String> allowed) {
        var answers = ConversationStore.JSON.createObjectNode();
        if (body == null || body.answers() == null) return answers;
        for (var entry : body.answers().entrySet()) {
            if (!allowed.contains(entry.getKey()) || entry.getValue() == null) continue;
            if (entry.getValue().length() > MAX_ANSWER_LENGTH) return null;
            answers.put(entry.getKey(), entry.getValue());
        }
        return answers;
    }

    /**
     * @brief 저장본에서 현재 단계 화면에 필요한 필드만 골라 응답 생성
     * - pretest·posttest: 화면용 문항(키 없음) / learn: 개념·조건·글·마감·남은 시간 / survey: 설문 문항
     */
    private ResponseEntity<?> ok(ObjectNode session) {
        var body = ConversationStore.JSON.createObjectNode();
        String stage = session.path("stage").asString();
        body.put("id", session.path("id").asString()).put("participant", session.path("participant").asString()).put("stage", stage);
        switch (stage) {
            case "pretest" -> {
                var items = body.putArray("pretest");
                for (var concept : session.path("candidates"))
                    materials.concept(concept.asString()).autoItems(session.path("pretest_form").asString())
                            .forEach(item -> items.add(materials.publicItem(concept.asString(), item)));
            }
            case "learn1", "learn2" -> {
                var learn = currentLearn(session);
                var concept = materials.concept(learn.path("concept").asString());
                body.putObject("concept").put("id", concept.id()).put("title", concept.title());
                body.put("condition", learn.path("condition").asString()).put("passage_part1", concept.part1());
                if (learn.has("continued_at")) body.put("passage_part2", concept.part2());
                else body.putNull("passage_part2");
                Instant deadline = Instant.parse(learn.path("deadline").asString());
                body.put("deadline", iso(deadline))
                        .put("remaining_seconds", Math.max(0, Duration.between(now(), deadline).getSeconds()));
            }
            case "posttest" -> {
                var items = body.putArray("posttest");
                for (var learn : session.path("learn")) {
                    String concept = learn.path("concept").asString();
                    materials.concept(concept).forms().get(session.path("posttest_form").asString())
                            .forEach(item -> items.add(materials.publicItem(concept, item)));
                }
            }
            case "survey" -> body.set("survey", materials.survey());
            default -> {}
        }
        return ResponseEntity.ok().header("Cache-Control", "no-store").body(body);
    }

    /** @brief 시각을 기록 형식(예: 2026-10-27T03:04:05.000Z) 문자열로 바꿈; Instant.parse로 다시 읽을 수 있음 */
    private static String iso(Instant instant) { return TIME.format(instant); }

    /** @brief 기록용 서버 시각; 밀리초까지만 남겨 내보낸 JSON을 읽기 쉽게 함 */
    private Instant now() { return clock.instant().truncatedTo(ChronoUnit.MILLIS); }

    /** @brief 요청 주소가 이 컴퓨터(127.0.0.1·::1)인지 확인; 서블릿이 주는 원격 주소는 IP 문자열이라 DNS 조회 없이 해석됨 */
    private static boolean loopback(String address) {
        try { return address != null && InetAddress.getByName(address).isLoopbackAddress(); }
        catch (java.net.UnknownHostException e) { return false; }
    }
    /** @brief 프록시·터널을 거친 요청인지 확인; 이런 헤더가 하나라도 있으면 원래 요청자가 이 컴퓨터라고 보장할 수 없음 */
    private static boolean forwarded(HttpServletRequest request) {
        for (String header : FORWARD_HEADERS) if (request.getHeader(header) != null) return true;
        return false;
    }
    /** @brief 없는 세션과 형식이 틀린 ID를 같은 404로 응답해 저장 경로 정보를 드러내지 않음 */
    private static ResponseEntity<?> notFound() { return error(404, "실험 세션을 찾을 수 없습니다. 진행자 화면에서 다시 시작해 주세요."); }
    /** @brief 상태 코드와 한국어 오류 안내를 통일된 응답 형태로 반환 */
    private static ResponseEntity<?> error(int status, String message) {
        return ResponseEntity.status(status).header("Cache-Control", "no-store").body(Map.of("message", message));
    }
}
