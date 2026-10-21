// 파일 역할: 커리큘럼 생성 체크포인트 저장소; 학습자의 진도 저장과는 별개
package com.csagent.backend;

import java.io.IOException;
import java.nio.file.*;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.node.ObjectNode;

/** Generation checkpoints, separate from learner progress. Single backend process only. */
@Component
public class CurriculumStore {
    private final Path directory;
    private final Set<String> active = ConcurrentHashMap.newKeySet();
    // 역할: 생성 결과를 저장할 디렉터리 보관
    public CurriculumStore(@Value("${curriculum.storage-dir:./data/curricula}") String directory) {
        this.directory = Path.of(directory);
    }
    // 역할: 작업 ID별 중복 실행을 막는 단일 프로세스 잠금 획득
    public void acquire(String id) {
        ConversationStore.validateId(id);
        if (!active.add(id)) throw new ResponseStatusException(HttpStatus.CONFLICT, "Generation is busy");
    }
    // 역할: 작업 잠금 해제; 다른 프로세스 사이의 잠금은 제공하지 않음
    public void release(String id) { active.remove(id); }
    // 역할: 검증된 ID의 체크포인트 조회; 없는 ID를 새 작업으로 만들지 않음
    public ObjectNode load(String id) throws IOException {
        ConversationStore.validateId(id);
        Path path = directory.resolve(id + ".json");
        if (!Files.exists(path)) throw new ResponseStatusException(HttpStatus.NOT_FOUND);
        return (ObjectNode) ConversationStore.JSON.readTree(Files.readString(path));
    }
    // 역할: 체크포인트를 임시 파일에 쓴 뒤 원자적으로 교체하고 임시 파일 정리
    public void save(ObjectNode job) throws IOException {
        String id = job.path("id").asText();
        ConversationStore.validateId(id);
        Files.createDirectories(directory);
        Path temp = Files.createTempFile(directory, id, ".tmp");
        try {
            Files.writeString(temp, ConversationStore.JSON.writeValueAsString(job));
            Files.move(temp, directory.resolve(id + ".json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { Files.deleteIfExists(temp); }
    }
}
