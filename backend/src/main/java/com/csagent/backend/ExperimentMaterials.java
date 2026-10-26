// 파일 역할: 비교 실험 자료(읽기 글·사전·사후 문항·정답 키)와 설문 문항을 읽어 두고, 화면용 문항과 채점을 분리해 제공
// - 정답 키·근거·루브릭은 이 클래스 밖으로 나가지 않음; 화면용 문항은 허용 필드만 골라 새로 만듦
package com.csagent.backend;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

@Component
public class ExperimentMaterials {
    private static final Set<String> AUTO_FORMATS = Set.of("multiple_choice", "short_exact");
    // 역할: 후보 개념 하나의 읽기 글과 A/B형 문항; forms는 "A"·"B" → [자동 채점 문항…, 서술 문항]
    public record Concept(String id, String title, String part1, String part2, Map<String, List<JsonNode>> forms) {
        // 역할: 해당 형의 자동 채점 문항만 반환(사전 시험·사전 점수 계산용)
        public List<JsonNode> autoItems(String form) {
            return forms.get(form).stream().filter(item -> AUTO_FORMATS.contains(item.path("format").asString())).toList();
        }
    }

    private final String version;
    private final Map<String, Concept> concepts = new LinkedHashMap<>();
    private final Map<String, JsonNode> items = new LinkedHashMap<>();
    private final ArrayNode survey;

    // 역할: 클래스패스의 실험 자료와 설문 파일을 읽음
    // - 실제 자료(materials.json)는 사용자 검수를 거친 데이터 PR로 들어오므로, 아직 없으면 빈 상태로 두고 API가 503으로 안내
    // - 형식이 틀린 자료는 서버 시작 시 바로 실패시켜 실험 도중에 잘못된 문항이 나가지 않게 함
    public ExperimentMaterials(@Value("${experiment.materials:experiment/materials.json}") String resource) {
        var root = read(resource);
        version = root == null ? null : root.path("version").asString();
        if (root != null) for (var concept : root.path("concepts")) addConcept(concept);
        var surveyRoot = read("experiment/survey.json");
        survey = surveyRoot == null ? ConversationStore.JSON.createArrayNode() : (ArrayNode) surveyRoot;
    }

    // 역할: 클래스패스 JSON 읽기; 파일이 없으면 null
    private static JsonNode read(String resource) {
        try (InputStream in = ExperimentMaterials.class.getClassLoader().getResourceAsStream(resource)) {
            return in == null ? null : ConversationStore.JSON.readTree(in);
        } catch (IOException e) { throw new IllegalStateException("실험 자료를 읽을 수 없습니다: " + resource, e); }
    }

    // 역할: 개념 하나를 검사해 등록; 각 형은 자동 채점 문항 1개 이상과 루브릭이 있는 서술 문항 1개를 가져야 함
    private void addConcept(JsonNode node) {
        String id = node.path("id").asString();
        if (id.isBlank() || concepts.containsKey(id) || !node.path("passage").path("part1").isString() || !node.path("passage").path("part2").isString())
            throw new IllegalStateException("실험 개념 형식이 올바르지 않습니다: " + id);
        Map<String, List<JsonNode>> forms = new LinkedHashMap<>();
        for (String form : new String[]{"A", "B"}) {
            List<JsonNode> list = new ArrayList<>();
            int auto = 0, explanation = 0;
            for (var item : node.path("pre_post").path(form)) {
                String itemId = item.path("id").asString(), format = item.path("format").asString();
                if (!itemId.startsWith("exp-" + id + "-" + form) || items.containsKey(itemId) || !item.path("question").isString())
                    throw new IllegalStateException("실험 문항 ID·질문이 올바르지 않습니다: " + itemId);
                if (AUTO_FORMATS.contains(format) && item.path("answer").isString()) auto++;
                else if (format.equals("explanation") && item.path("rubric").isArray()) explanation++;
                else throw new IllegalStateException("실험 문항 형식이 올바르지 않습니다: " + itemId);
                items.put(itemId, item);
                list.add(item);
            }
            if (auto == 0 || explanation != 1) throw new IllegalStateException("실험 문항 구성이 올바르지 않습니다: " + id + " " + form);
            forms.put(form, List.copyOf(list));
        }
        concepts.put(id, new Concept(id, node.path("title").asString(), node.path("passage").path("part1").asString(),
                node.path("passage").path("part2").asString(), forms));
    }

    // 역할: 실험 자료가 들어와 있는지; 없으면 세션을 만들지 않음
    public boolean available() { return concepts.size() >= 2; }
    public String version() { return version; }
    public Concept concept(String id) { return concepts.get(id); }
    // 역할: 자료 파일 순서대로 개념 ID 반환; 사전 점수 동점일 때의 우선순위로 씀
    public List<String> conceptIds() { return List.copyOf(concepts.keySet()); }
    public Set<String> surveyIds() {
        Set<String> ids = new HashSet<>();
        survey.forEach(item -> ids.add(item.path("id").asString()));
        return ids;
    }
    public ArrayNode survey() { return survey.deepCopy(); }

    // 역할: 화면용 문항 생성; id·개념·형식·질문·선택지만 복사해 정답 키·근거·루브릭이 응답에 섞일 수 없게 함
    public ObjectNode publicItem(String conceptId, JsonNode item) {
        var view = ConversationStore.JSON.createObjectNode();
        view.put("id", item.path("id").asString()).put("concept", conceptId)
                .put("format", item.path("format").asString()).put("question", item.path("question").asString());
        if (item.path("options").isObject()) view.set("options", item.path("options").deepCopy());
        return view;
    }

    // 역할: 자동 채점 문항 하나를 결정적으로 채점; 규칙은 AI core serving/grading.py와 같음
    // - 선택형: 앞뒤 공백·대소문자 무시 / 단답형: 모든 공백 제거, 소문자, 전각 쉼표를 반각으로
    public boolean correct(String itemId, String answer) {
        var item = items.get(itemId);
        if (item == null || answer == null) return false;
        String key = item.path("answer").asString();
        return switch (item.path("format").asString()) {
            case "multiple_choice" -> answer.strip().toUpperCase().equals(key);
            case "short_exact" -> answer.replaceAll("(?U)\\s+", "").toLowerCase().replace('，', ',').equals(key);
            default -> false;
        };
    }
}
