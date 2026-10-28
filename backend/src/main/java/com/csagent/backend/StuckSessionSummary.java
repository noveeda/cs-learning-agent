/** @file
 * @brief 막힘 세션을 개인 원문 없이 반 단위 통계로 변환
 */
package com.csagent.backend;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Comparator;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

@Component
public class StuckSessionSummary {
    /** @brief 지도에서 검증한 공개 개념·과목 정보 */
    public record Concept(String id, String title, String subject, String subjectTitle) {}
    /** @brief 한 개념이 관찰된 세션 수 */
    public record Count(Concept concept, int count) {}
    /** @brief 막힌 개념과 진단 빈틈의 세션별 동시 관찰 빈도 */
    public record Pair(Concept target, Concept gap, int count, boolean crossSubject) {}
    /** @brief 개념별 최종 보충 판정; 진행 중·미확인은 제외 */
    public record Outcome(Concept concept, int passed, int unresolved) {}
    /** @brief 교수자 화면에 반환할 집계 전용 응답; 날짜는 Unix 초 */
    public record Summary(int totalSessions, int diagnosedSessions, Double earliestAt, Double latestAt,
            List<Count> targets, List<Count> gaps, List<Pair> pairs, int crossSubjectPairs, List<Outcome> outcomes) {}
    // 상위 10쌍은 교수자가 한 번에 비교할 표시 분량; 저장·진단 횟수를 제한하는 운영 보호 장치는 아님
    private static final int PAIR_LIMIT = 10;
    private static final Concept UNKNOWN = new Concept("unknown", "지도에 없는 개념", "unknown", "과목 미상");
    private static final Set<String> DIAGNOSED_STAGES = Set.of("diagnose", "remediate", "check", "explain", "decide", "return", "complete");
    private final StuckSessionStore store;
    /** @brief 기존 체크포인트 저장소를 읽기 전용으로 사용 */
    public StuckSessionSummary(StuckSessionStore store) { this.store = store; }
    /** @brief 현재 지도와 저장된 세션으로 집계 응답 계산 */
    public Summary summarize(JsonNode map) throws IOException {
        var concepts = concepts(map);
        var targets = new HashMap<Concept, Integer>();
        var gaps = new HashMap<Concept, Integer>();
        var pairs = new HashMap<PairKey, Integer>();
        var outcomes = new HashMap<Concept, int[]>();
        int total = 0, diagnosed = 0, crossSubject = 0;
        Double earliest = null, latest = null;
        for (var session : store.loadAll()) {
            total++;
            var state = session.path("state");
            boolean reachedDiagnosis = DIAGNOSED_STAGES.contains(state.path("stage").asText());
            for (var event : state.path("log")) {
                if (event.path("kind").asText().equals("diagnosis_started")) reachedDiagnosis = true;
                var at = event.path("at");
                // 시각이 없는 옛 기록도 분모에 포함하되 기간은 추정하지 않음
                if (at.isNumber() && Double.isFinite(at.asDouble()) && at.asDouble() >= 0) {
                    double time = at.asDouble();
                    earliest = earliest == null ? time : Math.min(earliest, time);
                    latest = latest == null ? time : Math.max(latest, time);
                }
            }
            if (reachedDiagnosis) diagnosed++;
            Concept target = state.path("target").isString()
                    ? concepts.getOrDefault(state.path("target").asString(), UNKNOWN) : null;
            if (target != null) targets.merge(target, 1, Integer::sum);
            var sessionGaps = new HashSet<Concept>();
            for (var probe : state.path("probes"))
                if (probe.path("result").asText().equals("gap"))
                    sessionGaps.add(concepts.getOrDefault(probe.path("concept").asText(), UNKNOWN));
            // 한 세션에서 같은 개념을 여러 번 검사해도 빈틈·쌍 빈도는 한 번만 증가
            for (var gap : sessionGaps) {
                gaps.merge(gap, 1, Integer::sum);
                if (target == null) continue;
                pairs.merge(new PairKey(target, gap), 1, Integer::sum);
                if (crosses(target, gap)) crossSubject++;
            }
            // 이벤트 재시도 횟수 대신 체크포인트에 남은 개념별 최종 판정만 합산
            for (var fix : state.path("fixes")) {
                String result = fix.path("outcome").asText();
                if (!result.equals("passed") && !result.equals("unresolved")) continue;
                var concept = concepts.getOrDefault(fix.path("concept").asText(), UNKNOWN);
                int[] counts = outcomes.computeIfAbsent(concept, ignored -> new int[2]);
                counts[result.equals("passed") ? 0 : 1]++;
            }
        }
        var rankedPairs = pairs.entrySet().stream()
                .map(entry -> new Pair(entry.getKey().target(), entry.getKey().gap(), entry.getValue(), crosses(entry.getKey().target(), entry.getKey().gap())))
                .sorted(Comparator.comparingInt(Pair::count).reversed().thenComparing(pair -> pair.target().id()).thenComparing(pair -> pair.gap().id()))
                .limit(PAIR_LIMIT).toList();
        var rankedOutcomes = outcomes.entrySet().stream()
                .map(entry -> new Outcome(entry.getKey(), entry.getValue()[0], entry.getValue()[1]))
                .sorted(Comparator.<Outcome>comparingInt(result -> result.passed() + result.unresolved()).reversed().thenComparing(result -> result.concept().id()))
                .toList();
        return new Summary(total, diagnosed, earliest, latest, ranked(targets), ranked(gaps), rankedPairs, crossSubject, rankedOutcomes);
    }

    /** @brief 제목·과목은 검증된 지도에서만 가져와 저장본의 임의 문자열이 공개되지 않게 함 */
    private static Map<String, Concept> concepts(JsonNode map) {
        var subjects = new HashMap<String, String>();
        for (var subject : map.path("subjects")) subjects.put(subject.path("id").asText(), subject.path("title").asText());
        var concepts = new HashMap<String, Concept>();
        for (var concept : map.path("concepts")) {
            String subject = concept.path("subject").asText("dsa");
            String subjectTitle = subjects.getOrDefault(subject, subjects.isEmpty() ? map.path("title").asText("과목 미상") : "과목 미상");
            String id = concept.path("id").asText();
            concepts.put(id, new Concept(id, concept.path("title").asText(), subject, subjectTitle));
        }
        return concepts;
    }
    /** @brief 과목이 모두 알려진 쌍만 과목 경계 집계에 포함 */
    private static boolean crosses(Concept target, Concept gap) {
        return !target.subject().equals("unknown") && !gap.subject().equals("unknown") && !target.subject().equals(gap.subject());
    }
    /** @brief 세션 수 내림차순·개념 ID 오름차순으로 동률 정렬을 고정 */
    private static List<Count> ranked(Map<Concept, Integer> counts) {
        return counts.entrySet().stream().map(entry -> new Count(entry.getKey(), entry.getValue()))
                .sorted(Comparator.comparingInt(Count::count).reversed().thenComparing(count -> count.concept().id())).toList();
    }
    /** @brief 진단에서 함께 관찰된 목표·빈틈의 집계 키; 통계적 인과 추론을 뜻하지 않음 */
    private record PairKey(Concept target, Concept gap) {}
}
