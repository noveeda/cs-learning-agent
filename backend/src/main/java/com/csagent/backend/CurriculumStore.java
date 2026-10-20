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
    public CurriculumStore(@Value("${curriculum.storage-dir:./data/curricula}") String directory) {
        this.directory = Path.of(directory);
    }
    public void acquire(String id) {
        ConversationStore.validateId(id);
        if (!active.add(id)) throw new ResponseStatusException(HttpStatus.CONFLICT, "Generation is busy");
    }
    public void release(String id) { active.remove(id); }
    public ObjectNode load(String id) throws IOException {
        ConversationStore.validateId(id);
        Path path = directory.resolve(id + ".json");
        if (!Files.exists(path)) throw new ResponseStatusException(HttpStatus.NOT_FOUND);
        return (ObjectNode) ConversationStore.JSON.readTree(Files.readString(path));
    }
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
