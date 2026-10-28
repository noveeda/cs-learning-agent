/**
 * @file
 * @brief 막힘 세션 체크포인트 저장소; 새로고침·재접속 후 같은 단계에서 재개(M4)하기 위한 로컬 JSON 보관
 */
package com.csagent.backend;

import java.io.IOException;
import java.nio.file.*;
import java.util.Set;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.node.ObjectNode;

/** Stuck-session checkpoints {id, version, state}. Single backend process only. */
@Component
public class StuckSessionStore {
    private final Path directory;
    private final Set<String> active = ConcurrentHashMap.newKeySet();
    /** @brief 세션 파일을 저장할 디렉터리 보관; 기본값은 Git에서 제외되는 data 폴더 */
    public StuckSessionStore(@Value("${stuck.storage-dir:./data/stuck-sessions}") String directory) {
        this.directory = Path.of(directory);
    }
    /** @brief 32자리 소문자 16진수만 세션 ID로 인정; "../" 같은 경로 조작으로 저장 폴더 밖 파일에 닿지 않게 함 */
    public static boolean validId(String id) { return id != null && id.matches("[0-9a-f]{32}"); }
    /** @brief 같은 세션의 진행 요청을 하나만 통과시킴; 이미 처리 중이면 false(호출한 쪽이 409로 응답) */
    public boolean tryAcquire(String id) { return validId(id) && active.add(id); }
    /** @brief 진행 요청 잠금 해제; 다른 프로세스 사이의 잠금은 제공하지 않음 */
    public void release(String id) { active.remove(id); }
    /** @brief 저장된 세션 조회; 형식이 틀리거나 없는 ID는 null(새 세션을 만들지 않음) */
    public ObjectNode load(String id) throws IOException {
        if (!validId(id)) return null;
        Path path = directory.resolve(id + ".json");
        if (!Files.exists(path)) return null;
        return (ObjectNode) ConversationStore.JSON.readTree(Files.readString(path));
    }
    /**
     * @brief 확정된 세션 JSON만 읽은 스냅샷 반환; 집계에서는 저장·잠금·파일 수정을 하지 않음
     * - 임시 파일·심볼릭 링크는 제외; 손상 파일을 조용히 누락하면 분모가 틀려지므로 조회 전체를 실패시킴
     */
    public List<ObjectNode> loadAll() throws IOException {
        var sessions = new ArrayList<ObjectNode>();
        if (Files.notExists(directory)) return sessions;
        try (var paths = Files.newDirectoryStream(directory, "*.json")) {
            for (var path : paths) {
                String name = path.getFileName().toString();
                if (!validId(name.substring(0, name.length() - 5)) || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) continue;
                var session = ConversationStore.JSON.readTree(Files.readString(path));
                if (session == null || !session.isObject() || !session.path("state").isObject())
                    throw new IOException("Invalid stuck session checkpoint");
                sessions.add((ObjectNode) session);
            }
        }
        return sessions;
    }
    /** @brief 임시 파일에 쓴 뒤 원자적으로 교체; 쓰는 도중 실패해도 이전 체크포인트가 깨지지 않음 */
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
