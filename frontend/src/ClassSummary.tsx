/** @file
 * @brief 교수자가 저장된 막힘 세션의 선수 개념 빈틈·과목 간 연결·보충 결과를 읽는 집계 화면
 */
import { useEffect, useState } from "react";
import { loadClassSummary, type ClassSummaryData, type ConceptCount, type SummaryConcept } from "./api";
import "./class-summary.css";

/** @brief 개념 이름 아래 과목을 함께 표시해 과목을 넘는 빈틈을 구분 */
function ConceptLabel({ concept }: { concept: SummaryConcept }) {
  return <span className="class-concept">{concept.title}<small>{concept.subjectTitle}</small></span>;
}

/** @brief 한 세션당 한 번 센 개념 빈도를 전체 세션 수와 같은 척도의 가로 막대로 표시 */
function Frequency({ title, id, rows, total, empty }: { title: string; id: string; rows: ConceptCount[]; total: number; empty: string }) {
  return <section className="class-section" aria-labelledby={id}>
    <h2 id={id}>{title}</h2>
    <p className="class-caption">세션별 1회 집계 · 막대 전체 길이 {total.toLocaleString()}개</p>
    {rows.length ? <ul className="class-frequencies">{rows.map(({ concept, count }) =>
      <li key={concept.id}>
        <div className="class-frequency-label"><ConceptLabel concept={concept} /><span className="class-number">{count.toLocaleString()}회</span></div>
        <div className="class-track" aria-hidden="true"><span style={{ width: `${total ? count / total * 100 : 0}%` }} /></div>
      </li>)}</ul> : <p className="class-empty">{empty}</p>}
  </section>;
}

/** @brief 기록된 Unix 초를 한국 표준시로 표시; 날짜가 없는 옛 기록은 추정하지 않음 */
function formatTime(at: number | null) {
  if (at === null) return "—";
  const date = new Date(at * 1000);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** @brief 집계 조회·새로고침·실패 재시도를 관리하고 학생 수와 세션 수의 차이를 명시 */
export function ClassSummary() {
  const [data, setData] = useState<ClassSummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [request, setRequest] = useState(0);
  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError("");
    loadClassSummary().then(next => { if (!ignore) setData(next); }, (failure: Error) => {
      if (!ignore) setError(failure.message);
    }).finally(() => { if (!ignore) setLoading(false); });
    // 경로 이동·StrictMode 재실행 뒤 늦은 응답이 새 화면에 반영되지 않게 함
    return () => { ignore = true; };
  }, [request]);
  // 10세션 미만 안내는 소표본 해석을 돕는 표시 기준; 통계적 유의성이나 운영 제한이 아님
  const smallSample = data !== null && data.totalSessions < 10;
  return <main className="class-summary" aria-label="반 전체 막힘 통계">
    <div className="class-toolbar">
      <p>교수자용 · 이 서버에 저장된 전체 막힘 기록</p>
      <button className="button secondary" disabled={loading} onClick={() => setRequest(value => value + 1)}>새로고침</button>
    </div>
    {loading && <p role="status">{data ? "집계를 새로 불러오는 중입니다." : "막힘 기록을 집계하고 있습니다."}</p>}
    {error && <div className="class-error" role="alert"><p>{error}</p><button className="button secondary" onClick={() => setRequest(value => value + 1)}>다시 시도</button></div>}
    {data && <>
      <div className="class-sample">
        <p className="class-sample-count">막힘 세션 {data.totalSessions.toLocaleString()}개 기준</p>
        <p>진단 진입 {data.diagnosedSessions.toLocaleString()}개 · 동일 학습자의 여러 세션이 포함될 수 있어 학생 수와 다릅니다.</p>
        <p>기록 기간 (한국 표준시): {formatTime(data.earliestAt)} ~ {formatTime(data.latestAt)}</p>
      </div>
      {data.totalSessions === 0 ? <p className="class-notice">아직 저장된 막힘 세션이 없습니다. 학습 화면에서 [막혔어요]로 진행한 기록이 쌓이면 집계됩니다.</p>
        : smallSample && <p className="class-notice">표본이 적습니다(10개 미만). 현재 기록의 빈도로만 해석하고 반 전체의 이해도로 일반화하지 마세요.</p>}
      <div className="class-grid">
        <Frequency title="많이 모르는 선수 개념" id="class-gaps" rows={data.gaps} total={data.totalSessions} empty="진단 빈틈 기록이 없습니다." />
        <Frequency title="막힌 개념" id="class-targets" rows={data.targets} total={data.totalSessions} empty="막힌 개념 기록이 없습니다." />
        <section className="class-section" aria-labelledby="class-pairs">
          <h2 id="class-pairs">막힌 개념 → 원인 개념</h2>
          <p className="class-caption">빈도 상위 10쌍 · 과목을 넘는 쌍 {data.crossSubjectPairs.toLocaleString()}회 (전체 쌍 기준)</p>
          <p className="class-caption">진단에서 함께 관찰된 연결이며, 인과관계의 확정을 뜻하지 않습니다.</p>
          {data.pairs.length ? <div className="class-table-wrap"><table>
            <thead><tr><th scope="col">막힌 개념</th><th scope="col">원인 개념</th><th scope="col" className="class-number">횟수</th></tr></thead>
            <tbody>{data.pairs.map(pair => <tr key={`${pair.target.id}:${pair.gap.id}`}>
              <td><ConceptLabel concept={pair.target} /></td><td><ConceptLabel concept={pair.gap} />{pair.crossSubject && <small className="class-cross">다른 과목</small>}</td><td className="class-number">{pair.count.toLocaleString()}</td>
            </tr>)}</tbody>
          </table></div> : <p className="class-empty">막힌 개념과 진단 빈틈의 연결 기록이 없습니다.</p>}
        </section>
        <section className="class-section" aria-labelledby="class-outcomes">
          <h2 id="class-outcomes">보충 결과</h2>
          <p className="class-caption">개념별 최종 확인 결과 · 진행 중·미확인은 제외</p>
          {data.outcomes.length ? <div className="class-table-wrap"><table>
            <thead><tr><th scope="col">보충 개념</th><th scope="col" className="class-number">확인 통과</th><th scope="col" className="class-number">미해결</th></tr></thead>
            <tbody>{data.outcomes.map(outcome => <tr key={outcome.concept.id}>
              <td><ConceptLabel concept={outcome.concept} /></td><td className="class-number">{outcome.passed.toLocaleString()}</td><td className="class-number">{outcome.unresolved.toLocaleString()}</td>
            </tr>)}</tbody>
          </table></div> : <p className="class-empty">확인까지 마친 보충 기록이 없습니다.</p>}
        </section>
      </div>
      <p className="class-footnote">세션 ID·학습자 식별 정보·답 원문은 집계 응답에 포함하지 않습니다. 진단하지 않은 개념은 안다고 판정하지 않습니다.</p>
    </>}
  </main>;
}
