// 파일 역할: 개념 지도 조회·대화 복구·질문 전송과 NDJSON 스트림 해석; 화면 컴포넌트와 통신 처리 분리
import type { Concept } from './roadmap';
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

// 응답에서 화면이 쓰는 필드만 꺼내기 위한 최소 형태; 그래프 규칙 검증은 AI core가 담당
type ConceptMapResponse = {
  title: string;
  concepts: Omit<Concept, 'domain' | 'stuckSupported'>[];
  stuck_supported?: string[];
};

// 역할: 개념 지도 응답의 stuck_supported(확인·서술 문항이 모두 있는 개념)로 [막혔어요] 지원 여부 판단
// - 목록은 AI core가 문항 은행에서 계산하므로 화면과 엔진의 판단이 어긋나지 않고, 문항이 추가되면 자동으로 따라옴
// - 필드가 없는 옛 응답이면 엔진이 이해 확인을 할 수 있는지 모르므로 지원하지 않는 것으로 봄
export function isStuckSupported(map: { stuck_supported?: string[] }, conceptId: string): boolean {
  return Array.isArray(map.stuck_supported) && map.stuck_supported.includes(conceptId);
}

// 역할: /api/concept-map을 받아 로드맵용 개념 목록으로 변환; 실패 시 화면에 보여 줄 한국어 안내를 담아 예외
export async function loadConceptMap(): Promise<Concept[]> {
  let response: Response;
  try {
    response = await fetch('/api/concept-map', { cache: 'no-store' });
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.');
  }
  // 프록시 오류처럼 JSON이 아닌 응답도 올 수 있어 해석 실패는 null로 두고 아래에서 기본 안내 사용
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof body?.message === 'string'
      ? body.message
      : '개념 지도를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
  }
  if (!Array.isArray(body?.concepts)) throw new Error('개념 지도 형식이 올바르지 않습니다.');
  const map = body as ConceptMapResponse;
  return map.concepts.map(({ id, title, description, objectives, prerequisites }) => ({
    id, title, description, objectives, prerequisites, domain: map.title,
    stuckSupported: isStuckSupported(map, id),
  }));
}

// 역할: 브라우저에 새로운 대화 ID 저장; 서버 기록은 실제 질문 전송 때 생성
export function newConversation() {
  const id = crypto.randomUUID().replaceAll('-', '');
  localStorage.setItem('cs-conversation', id);
  return id;
}

// 역할: 서버 문답을 화면 메시지로 변환하고 실패 여부·마지막 도식 복원
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
// 역할: 완료 이벤트까지 텍스트·도식 이벤트를 전달하고 최종 답변 반환
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

  // 역할: 완성된 JSON 한 줄을 해석해 결과를 누적하고 화면에 진행 이벤트 전달
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
        // 네트워크 조각은 한글 바이트나 JSON 줄 중간에서 끊길 수 있으므로 디코더와 미완성 줄을 유지
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
    // 연결 종료만으로 성공 처리하지 않고 done과 최종 답변을 함께 확인
    if (!finished || !answer) throw new Error('응답이 중단됐습니다. 다시 질문해 주세요.');
    return { answer, artifact };
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
