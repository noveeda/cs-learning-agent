// 파일 역할: 비교 실험 세션 기록 저장소; 새로고침·브라우저 재시작 뒤 같은 단계로 복구하고 실험 뒤 내보내기 위한 로컬 JSON 보관
// - 기록에는 참가자 코드·학년·수강 여부·답·서버 시각만 두고 이름 등 개인정보는 받지 않음
package com.csagent.backend;

import java.io.IOException;
import java.nio.file.*;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.node.ObjectNode;

/** Experiment session records {id, participant, stage, ...}. Single backend process only. */
@Component
public class ExperimentStore {
    private final Path directory;
    // 역할: 세션 파일을 저장할 디렉터리 보관; 기본값은 Git에서 제외되는 data 폴더(실험 보고서 작성 뒤 원문 삭제)
    public ExperimentStore(@Value("${experiment.storage-dir:./data/experiments}") String directory) {
        this.directory = Path.of(directory);
    }
    // 역할: 32자리 소문자 16진수만 세션 ID로 인정; 경로 조작으로 저장 폴더 밖 파일에 닿지 않게 함
    public static boolean validId(String id) { return id != null && id.matches("[0-9a-f]{32}"); }
    // 역할: 저장된 세션 조회; 형식이 틀리거나 없는 ID는 null
    public ObjectNode load(String id) throws IOException {
        if (!validId(id)) return null;
        Path path = directory.resolve(id + ".json");
        if (!Files.exists(path)) return null;
        return (ObjectNode) ConversationStore.JSON.readTree(Files.readString(path));
    }
    // 역할: 저장된 모든 세션 반환(내보내기·참가자 중복 확인용); 실험 인원이 4~5명이라 매번 폴더를 읽어도 충분함
    public List<ObjectNode> all() throws IOException {
        List<ObjectNode> sessions = new ArrayList<>();
        if (!Files.isDirectory(directory)) return sessions;
        try (Stream<Path> files = Files.list(directory)) {
            for (Path path : files.filter(p -> p.getFileName().toString().matches("[0-9a-f]{32}\\.json")).sorted().toList())
                sessions.add((ObjectNode) ConversationStore.JSON.readTree(Files.readString(path)));
        }
        return sessions;
    }
    // 역할: 같은 참가자 코드의 기존 세션 조회; 없으면 null
    public ObjectNode findByParticipant(String participant) throws IOException {
        for (var session : all()) if (session.path("participant").asString().equals(participant)) return session;
        return null;
    }
    // 역할: 임시 파일에 쓴 뒤 원자적으로 교체; 쓰는 도중 실패해도 이전 기록이 깨지지 않음
    public void save(ObjectNode session) throws IOException {
        String id = session.path("id").asString();
        if (!validId(id)) throw new IllegalArgumentException("Invalid experiment session ID");
        Files.createDirectories(directory);
        Path temp = Files.createTempFile(directory, id, ".tmp");
        try {
            Files.writeString(temp, ConversationStore.JSON.writeValueAsString(session));
            Files.move(temp, directory.resolve(id + ".json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { Files.deleteIfExists(temp); }
    }
}
