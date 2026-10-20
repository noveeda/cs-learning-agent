export type DraftInput = { topic: string; goal: string; level: string };
export type Lesson = { title: string; objectives: string[] };
export type Unit = {
  id: string; plan: { title: string; scope: string; topics: string[] };
  lessons: Lesson[]; reviewed: boolean; feedback: string[];
};
export type CurriculumJob = {
  id: string; version: number; state: {
    request: DraftInput; stage: 'outline' | 'generate' | 'review' | 'final' | 'repair' | 'complete' | 'blocked';
    units: Unit[]; message?: string; retries?: number; compactions?: number;
  };
};

async function request(path: string, body?: unknown): Promise<CurriculumJob> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 330000);
  try {
    const response = await fetch(`/api/curriculum/draft${path}`, {
      method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        503: '다른 요청을 처리 중입니다. 잠시 후 이어서 생성해 주세요.',
        409: '다른 요청이 진행 중입니다. 잠시 후 이어서 생성해 주세요.',
        404: '저장된 생성 작업을 찾을 수 없습니다. 새 초안을 시작해 주세요.',
        422: '주제·학습 목적·현재 수준을 각각 1~300자로 입력해 주세요.',
      };
      throw new Error(messages[response.status] || '단계 생성에 실패했습니다. 저장된 결과에서 이어서 재시도해 주세요.');
    }
    const job = await response.json();
    const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim();
    const validUnit = (unit: Unit) => text(unit?.id) && text(unit.plan?.title) && text(unit.plan?.scope)
      && Array.isArray(unit.plan.topics) && unit.plan.topics.length > 0 && unit.plan.topics.every(text)
      && Array.isArray(unit.lessons) && unit.lessons.length <= unit.plan.topics.length
      && unit.lessons.every(lesson => text(lesson?.title) && Array.isArray(lesson.objectives)
        && lesson.objectives.length > 0 && lesson.objectives.every(text))
      && Array.isArray(unit.feedback) && unit.feedback.every(text);
    if (typeof job?.id !== 'string' || !/^[0-9a-f]{32}$/.test(job.id) || !Number.isInteger(job.version)
      || !job.state?.request || !Array.isArray(job.state.units) || !job.state.units.every(validUnit)
      || !['outline', 'generate', 'review', 'final', 'repair', 'complete', 'blocked'].includes(job.state.stage))
      throw new Error('생성 상태 형식이 올바르지 않습니다. 다시 불러와 주세요.');
    return job;
  } catch (error) {
    if (controller.signal.aborted) throw new Error('단계 대기 시간이 초과됐습니다. 이어서 생성하면 마지막 저장 상태부터 진행합니다.');
    if (error instanceof TypeError) throw new Error('서버에 연결하지 못했습니다. 잠시 후 이어서 생성해 주세요.');
    throw error;
  } finally { clearTimeout(timer); }
}
export const createCurriculum = (input: DraftInput) => request('', input);
export const loadCurriculum = (id: string) => request(`/${id}`);
export const stepCurriculum = (job: CurriculumJob) => request(`/${job.id}/step`, { version: job.version });
