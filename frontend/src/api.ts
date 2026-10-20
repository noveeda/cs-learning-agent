export type ChatMessage = { role: 'user' | 'assistant'; content: string };
export type Artifact = { artifact_id: string; url: string; diagram: { title: string } };
export type Message = ChatMessage & { artifact?: Artifact; failed?: boolean };
export type AgentEvent =
  | { type: 'started' | 'done' | 'text_start' }
  | { type: 'tool_started'; name: string }
  | { type: 'tool_finished'; name: string; ok: boolean }
  | { type: 'tool_error'; error: string }
  | ({ type: 'artifact' } & Artifact)
  | { type: 'answer' | 'text_delta'; text: string }
  | { type: 'context'; input_tokens: number; dropped_messages: number; context_limit: number }
  | { type: 'error'; message: string };

export function newConversation() {
  const id = crypto.randomUUID().replaceAll('-', '');
  localStorage.setItem('cs-conversation', id);
  return id;
}

export async function loadConversation(id: string): Promise<Message[]> {
  const response = await fetch(`/api/conversations/${id}`, { cache: 'no-store' });
  if (!response.ok) throw new Error('대화를 불러오지 못했습니다. 새로고침해 주세요.');
  const conversation = await response.json();
  return conversation.messages.map((m: ChatMessage & { status: string; artifacts?: Artifact[] }) => ({
    role: m.role, content: m.content, failed: m.status !== 'complete',
    artifact: m.artifacts?.at(-1),
  }));
}

// Kept independent of React so the temporary UI can be replaced.
export async function requestChat(
  question: string,
  conversationId: string,
  onEvent: (event: AgentEvent) => void,
  artifactId?: string,
): Promise<{ answer: string; artifact?: Artifact }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 540000);
  let answer = '';
  let artifact: Artifact | undefined;
  let finished = false;

  function accept(line: string) {
    if (!line.trim()) return;
    const event: AgentEvent = JSON.parse(line);
    if (event.type === 'error') throw new Error(event.message);
    if (event.type === 'answer') answer = event.text;
    if (event.type === 'artifact') artifact = event;
    if (event.type === 'done') finished = true;
    onEvent(event);
  }

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, conversation_id: conversationId, artifact_id: artifactId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(response.status === 422
        ? '질문이나 대화가 너무 깁니다. 질문을 줄이거나 새 학습을 시작해 주세요.'
        : '서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.');
    }
    if (!response.body) throw new Error('응답을 읽을 수 없습니다.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    try {
      while (true) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value, { stream: !done });
        const lines = pending.split('\n');
        pending = lines.pop() || '';
        lines.forEach(accept);
        if (done) {
          accept(pending);
          break;
        }
      }
    } finally {
      reader.releaseLock();
    }
    if (!finished || !answer) throw new Error('응답이 중단됐습니다. 다시 질문해 주세요.');
    return { answer, artifact };
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
