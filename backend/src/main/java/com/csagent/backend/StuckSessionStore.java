// 파일 역할: 막힘 세션 체크포인트 저장소; 새로고침·재접속 후 같은 단계에서 재개(M4)하기 위한 로컬 JSON 보관
package com.csagent.backend;

import java.io.IOException;
import java.nio.file.*;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.node.ObjectNode;

/** Stuck-session checkpoints {id, version, state}. Single backend process only. */
@Component
public class StuckSessionStore {
    private final Path directory;
    private final Set<String> active = ConcurrentHashMap.newKeySet();
    // 역할: 세션 파일을 저장할 디렉터리 보관; 기본값은 Git에서 제외되는 data 폴더
    public StuckSessionStore(@Value("${stuck.storage-dir:./data/stuck-sessions}") String directory) {
        this.directory = Path.of(directory);
    }
    // 역할: 32자리 소문자 16진수만 세션 ID로 인정; "../" 같은 경로 조작으로 저장 폴더 밖 파일에 닿지 않게 함
    public static boolean validId(String id) { return id != null && id.matches("[0-9a-f]{32}"); }
    // 역할: 같은 세션의 진행 요청을 하나만 통과시킴; 이미 처리 중이면 false(호출한 쪽이 409로 응답)
    public boolean tryAcquire(String id) { return validId(id) && active.add(id); }
    // 역할: 진행 요청 잠금 해제; 다른 프로세스 사이의 잠금은 제공하지 않음
    public void release(String id) { active.remove(id); }
    // 역할: 저장된 세션 조회; 형식이 틀리거나 없는 ID는 null(새 세션을 만들지 않음)
    public ObjectNode load(String id) throws IOException {
        if (!validId(id)) return null;
        Path path = directory.resolve(id + ".json");
        if (!Files.exists(path)) return null;
        return (ObjectNode) ConversationStore.JSON.readTree(Files.readString(path));
    }
    // 역할: 임시 파일에 쓴 뒤 원자적으로 교체; 쓰는 도중 실패해도 이전 체크포인트가 깨지지 않음
    public void save(ObjectNode session) throws IOException {
        String id = session.path("id").asText();
        if (!validId(id)) throw new IllegalArgumentException("Invalid stuck session ID");
        Files.createDirectories(directory);
        Path temp = Files.createTempFile(directory, id, ".tmp");
        try {
            Files.writeString(temp, ConversationStore.JSON.writeValueAsString(session));
            Files.move(temp, directory.resolve(id + ".json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { Files.deleteIfExists(temp); }
    }
}
