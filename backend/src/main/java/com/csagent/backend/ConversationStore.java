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

    public ConversationStore(@Value("${chat.storage-dir:./data/conversations}") String directory) {
        this.directory = Path.of(directory);
    }

    static void validateId(String id) {
        if (id == null || !id.matches("[0-9a-f]{32}"))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid conversation ID");
    }

    public void acquire(String id) {
        validateId(id);
        if (!active.add(id))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Conversation is busy");
    }

    public void release(String id) { active.remove(id); }

    public ObjectNode load(String id) throws IOException {
        validateId(id);
        var file = directory.resolve(id + ".json");
        if (Files.exists(file)) return (ObjectNode) JSON.readTree(Files.readString(file));
        var conversation = JSON.createObjectNode();
        conversation.put("conversation_id", id);
        conversation.putArray("messages");
        return conversation;
    }

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
