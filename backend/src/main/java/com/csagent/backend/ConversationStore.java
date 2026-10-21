// 파일 역할: 단일 백엔드 프로세스의 대화 JSON 저장·조회와 같은 대화의 동시 수정 방지
package com.csagent.backend;

import java.io.IOException;
import java.nio.file.*;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.node.ObjectNode;
import tools.jackson.databind.json.JsonMapper;

/** Local single-process storage. Keep conversation files outside source control. */
@Component
public class ConversationStore {
    static final JsonMapper JSON = JsonMapper.builder().build();
    private final Path directory;
    private final Set<String> active = ConcurrentHashMap.newKeySet();

    // 역할: 설정된 대화 저장 디렉터리 보관
    public ConversationStore(@Value("${chat.storage-dir:./data/conversations}") String directory) {
        this.directory = Path.of(directory);
    }

    // 역할: 32자리 16진수 ID만 허용해 임의 파일 경로 접근 방지
    static void validateId(String id) {
        if (id == null || !id.matches("[0-9a-f]{32}"))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid conversation ID");
    }

    // 역할: 처리 중 ID를 집합에 등록; 같은 대화의 중복 요청은 충돌로 응답
    public void acquire(String id) {
        validateId(id);
        if (!active.add(id))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Conversation is busy");
    }

    // 역할: 완료·실패한 요청의 대화 잠금 해제
    public void release(String id) { active.remove(id); }

    // 역할: 저장 기록 조회; 아직 없는 ID는 빈 대화로 초기화
    public ObjectNode load(String id) throws IOException {
        validateId(id);
        var file = directory.resolve(id + ".json");
        if (Files.exists(file)) return (ObjectNode) JSON.readTree(Files.readString(file));
        var conversation = JSON.createObjectNode();
        conversation.put("conversation_id", id);
        conversation.putArray("messages");
        return conversation;
    }

    // 역할: 임시 파일 작성 후 원자적 교체로 불완전한 JSON이 원본을 덮어쓰는 상황 방지
    public void save(ObjectNode conversation) throws IOException {
        String id = conversation.path("conversation_id").asText();
        validateId(id);
        Files.createDirectories(directory);
        var temporary = Files.createTempFile(directory, id, ".tmp");
        try {
            Files.writeString(temporary, JSON.writeValueAsString(conversation));
            Files.move(temporary, directory.resolve(id + ".json"),
                    StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temporary);
        }
    }
}
