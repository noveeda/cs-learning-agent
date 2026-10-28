/**
 * @file
 * @brief 개념 지도 조회·대화 복구·질문 전송과 NDJSON 스트림 해석; 화면 컴포넌트와 통신 처리 분리
 */
import type { AnimationData } from './animation';
import type { Concept } from './roadmap';
export type ChatMessage = { role: 'user' | 'assistant'; content: string };
/** @brief 도식 artifact; AI core가 그린 SVG를 url로 받아 DiagramPanel에 표시 */
export type Artifact = { artifact_id: string; kind?: 'diagram'; url: string; diagram: { title: string } };
/** @brief 애니메이션 artifact; AI core create_animation 결과로 SVG 경로 없이 프레임(animation)을 직접 담음 */
export type AnimationArtifact = { artifact_id: string; kind: 'animation'; animation: AnimationData };
// 채팅 답변에 붙는 artifact는 둘 중 하나; kind로 구분하고 도식 쪽은 kind가 없던 기존 저장값도 그대로 받음
export type ChatArtifact = Artifact | AnimationArtifact;
export type Message = ChatMessage & { artifact?: ChatArtifact; failed?: boolean };
export type AgentEvent =
  | { type: 'started' | 'done' | 'text_start' }
  | { type: 'tool_started'; name: string }
  | { type: 'tool_finished'; name: string; ok: boolean }
  | { type: 'tool_error'; name?: string; error: string }
  | ({ type: 'artifact' } & ChatArtifact)
  | { type: 'answer' | 'text_delta'; text: string }
  | { type: 'context'; input_tokens: number; dropped_messages: number; context_limit: number }
  | { type: 'error'; message: string };

/** @brief 화면이 도식 패널 대신 애니메이션 재생기를 보여 줘야 하는 artifact인지 판단 */
export function isAnimationArtifact(artifact: ChatArtifact): artifact is AnimationArtifact {
  return artifact.kind === 'animation';
}

/** @brief 대화 속 artifact 버튼·패널 제목; 종류마다 제목이 담긴 위치가 달라 한곳에서 꺼냄 */
export function artifactTitle(artifact: ChatArtifact): string {
  return isAnimationArtifact(artifact) ? artifact.animation.title : artifact.diagram.title;
}

/** @brief 패널 열기 버튼·안내 문구에 쓰는 artifact 종류 이름 */
export function artifactNoun(artifact: ChatArtifact): '도식' | '애니메이션' {
  return isAnimationArtifact(artifact) ? '애니메이션' : '도식';
}

/**
 * @brief 도구 호출 진행 이벤트를 사용자 안내 문구로 바꿈
 * - 결과 artifact가 오기 전이라 종류를 모르므로 이벤트의 도구 이름(name)으로 도식·애니메이션을 구분
 * - failed: 도구 입력이 검증에 실패해 모델이 고쳐 다시 부르는 중(tool_error)
 */
export function toolProgress(name: string | undefined, failed: boolean): string {
  if (name === 'show_animation')
    return failed ? '애니메이션 단계를 다시 확인하고 있어요' : '연산 과정을 애니메이션으로 만들고 있어요';
  return failed ? '도식의 연결을 다시 확인하고 있어요' : '개념을 도식으로 정리하고 있어요';
}

// 응답에서 화면이 쓰는 필드만 꺼내기 위한 최소 형태; 그래프 규칙 검증은 AI core가 담당
// - subjects·subject는 여러 과목 지도(MVP 4)부터 오므로 선택 필드로 둠
type ConceptMapResponse = {
  title: string;
  subjects?: { id: string; title: string }[];
  concepts: (Omit<Concept, 'subject' | 'domain' | 'stuckSupported'> & { subject?: string })[];
  stuck_supported?: string[];
};

/**
 * @brief 개념 지도 응답의 stuck_supported(확인·서술 문항이 모두 있는 개념)로 [막혔어요] 지원 여부 판단
 * - 목록은 AI core가 문항 은행에서 계산하므로 화면과 엔진의 판단이 어긋나지 않고, 문항이 추가되면 자동으로 따라옴
 * - 필드가 없는 옛 응답이면 엔진이 이해 확인을 할 수 있는지 모르므로 지원하지 않는 것으로 봄
 */
export function isStuckSupported(map: { stuck_supported?: string[] }, conceptId: string): boolean {
  return Array.isArray(map.stuck_supported) && map.stuck_supported.includes(conceptId);
}

/**
 * @brief /api/concept-map을 받아 로드맵용 개념 목록으로 변환; 실패 시 화면에 보여 줄 한국어 안내를 담아 예외
 * - domain은 개념이 속한 과목 이름; 합친 지도의 제목(CS 전공)이 아니라 과목 이름이 학습 화면에 보이게 함
 * - subject·subjects가 없는 옛 응답은 자료구조 지도 하나뿐이었으므로 dsa 과목, 이름은 지도 제목으로 봄
 */
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
  const subjects = Array.isArray(map.subjects) ? map.subjects : [{ id: 'dsa', title: map.title }];
  return map.concepts.map(({ id, title, description, objectives, prerequisites, subject = 'dsa' }) => ({
    id, title, description, objectives, prerequisites, subject,
    domain: subjects.find((item) => item.id === subject)?.title ?? subject,
    stuckSupported: isStuckSupported(map, id),
  }));
}

/** @brief 브라우저에 새로운 대화 ID 저장; 서버 기록은 실제 질문 전송 때 생성 */
export function newConversation() {
  const id = crypto.randomUUID().replaceAll('-', '');
  localStorage.setItem('cs-conversation', id);
  return id;
}

/** @brief 서버 문답을 화면 메시지로 변환하고 실패 여부·마지막 artifact(도식·애니메이션) 복원 */
export async function loadConversation(id: string): Promise<Message[]> {
  const response = await fetch(`/api/conversations/${id}`, { cache: 'no-store' });
  if (!response.ok) throw new Error('대화를 불러오지 못했습니다. 새로고침해 주세요.');
  const conversation = await response.json();
  return conversation.messages.map((m: ChatMessage & { status: string; artifacts?: ChatArtifact[] }) => ({
    role: m.role, content: m.content, failed: m.status !== 'complete',
    artifact: m.artifacts?.at(-1),
  }));
}

// Kept independent of React so the temporary UI can be replaced.
/** @brief 완료 이벤트까지 텍스트·artifact 이벤트를 전달하고 최종 답변 반환 */
export async function requestChat(
  question: string,
  conversationId: string,
  onEvent: (event: AgentEvent) => void,
  artifactId?: string,
): Promise<{ answer: string; artifact?: ChatArtifact }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 540000);
  let answer = '';
  let artifact: ChatArtifact | undefined;
  let finished = false;

  /** @brief 완성된 JSON 한 줄을 해석해 결과를 누적하고 화면에 진행 이벤트 전달 */
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

/** @brief 세션 집계에 쓰는 공개 개념·과목 정보; 학습자 식별 필드는 없음 */
export type SummaryConcept = { id: string; title: string; subject: string; subjectTitle: string };
/** @brief 한 개념이 관찰된 세션 수 */
export type ConceptCount = { concept: SummaryConcept; count: number };
/** @brief 교수자 반 요약 계약; 날짜는 Unix 초, 빈 기간은 null */
export type ClassSummaryData = {
  totalSessions: number; diagnosedSessions: number; earliestAt: number | null; latestAt: number | null;
  targets: ConceptCount[]; gaps: ConceptCount[];
  pairs: { target: SummaryConcept; gap: SummaryConcept; count: number; crossSubject: boolean }[];
  crossSubjectPairs: number;
  outcomes: { concept: SummaryConcept; passed: number; unresolved: number }[];
};

/** @brief 읽기 전용 집계를 조회하고 분모·필수 배열을 확인; 실패를 빈 통계로 바꾸지 않음 */
export async function loadClassSummary(): Promise<ClassSummaryData> {
  let response: Response;
  try {
    response = await fetch('/api/stuck/summary', { cache: 'no-store' });
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.message === 'string' ? body.message : '반 요약을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
  if (!body || !Number.isInteger(body.totalSessions) || body.totalSessions < 0 ||
      !Number.isInteger(body.diagnosedSessions) || body.diagnosedSessions < 0 || body.diagnosedSessions > body.totalSessions ||
      !Number.isInteger(body.crossSubjectPairs) || body.crossSubjectPairs < 0 ||
      !['targets', 'gaps', 'pairs', 'outcomes'].every(key => Array.isArray(body[key])) ||
      !['earliestAt', 'latestAt'].every(key => body[key] === null || (typeof body[key] === 'number' && Number.isFinite(body[key]))))
    throw new Error('집계 응답 형식이 올바르지 않습니다. 잠시 후 다시 시도해 주세요.');
  return body as ClassSummaryData;
}
