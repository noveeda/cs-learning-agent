export type DraftInput = { topic: string; goal: string; level: string };
export type CurriculumDraft = { title: string; lessons: { title: string; objectives: string[] }[] };

export async function requestCurriculum(input: DraftInput): Promise<CurriculumDraft> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 140000);
  try {
    const response = await fetch('/api/curriculum/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input), signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(response.status === 503
        ? '다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.'
        : response.status === 422
          ? '주제·학습 목적·현재 수준을 각각 1~300자로 입력해 주세요.'
          : '초안을 생성하지 못했습니다. 다시 시도해 주세요.');
    }
    const draft = await response.json();
    const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim() && value.length <= 300;
    if (!text(draft?.title) || !Array.isArray(draft.lessons) || draft.lessons.length < 2 || draft.lessons.length > 6
      || !draft.lessons.every((lesson: CurriculumDraft['lessons'][number]) => text(lesson?.title)
        && Array.isArray(lesson.objectives) && lesson.objectives.length >= 1 && lesson.objectives.length <= 3
        && lesson.objectives.every(text))) {
      throw new Error('초안 형식이 올바르지 않습니다. 다시 시도해 주세요.');
    }
    return draft;
  } catch (error) {
    if (controller.signal.aborted) throw new Error('생성 시간이 초과됐습니다. 다시 시도해 주세요.');
    if (error instanceof TypeError) throw new Error('서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
