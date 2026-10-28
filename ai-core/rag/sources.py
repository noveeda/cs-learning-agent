## @file
# @brief 근거로 쓰는 공개 교재 7권의 출처·라이선스·원본 형식·그림 사용 범위를 한곳에 정의
# - 라이선스·출처 문구는 2026-10-28 조사(1차 출처 확인) 결과를 따름. 답변의 출처 표기(TASL)와 수집(fetch)이 이 표를 씀
# - 그림은 라이선스를 확인한 것만 쓰고, 확인되지 않은 책은 figures='none'으로 시작함(설계 6절)
from dataclasses import dataclass
from typing import Literal


## @brief 교재 한 권의 출처와 수집·그림 정책
# - fetch: 'github:<owner>/<repo>', PDF 주소, 'wikibooks:<책 이름>' 중 하나. None이면 사람이 직접 내려받는 책
# - figures: 'all'은 figure_deny만 빼고 사용, 'allowlist'는 figure_allow에 있는 파일만 사용, 'none'은 그림 미사용
# - figure_allow·figure_deny 항목은 원본 폴더(raw/<id>/) 기준 그림 경로로 FigureRef.file과 같은 형식
@dataclass(frozen=True)
class Book:
    id: str
    title: str
    authors: str
    license: str
    license_url: str
    home: str
    kind: Literal['latex', 'rst', 'pdf', 'epub', 'html', 'wikibooks']
    fetch: str | None
    figures: Literal['all', 'none', 'allowlist']
    figure_allow: tuple[str, ...] = ()
    figure_deny: tuple[str, ...] = ()


# Peterson & Davie 그림 중 원 출판사(Elsevier 5판) ISBN 9780123850591이 파일 이름에 없는 것
# - 6판 공개본에서 저자가 새로 만든 슬라이드 그림(같은 저장소에 pptx 원본이 있음)과 방화벽 그림만 둠
# - 제외: browser-screenshot.png(다른 사이트 화면 캡처), f04-26-22092018.png(출판사 그림 번호 체계를 그대로 써서 출처 불명)
_PD_FIGURES = (
    'congestion/figures/tcp/Slide1.png',
    'direct/figures/access/Slide1.png', 'direct/figures/access/Slide2.png',
    'direct/figures/access/Slide3.png', 'direct/figures/access/Slide4.png',
    'e2e/figures/rpc/Slide1.png', 'e2e/figures/rpc/Slide2.png',
    'e2e/figures/rpc/Slide3.png', 'e2e/figures/rpc/Slide4.png',
    'internetworking/figures/impl/Slide1.png', 'internetworking/figures/impl/Slide2.png',
    'internetworking/figures/impl/Slide3.png', 'internetworking/figures/impl/Slide4.png',
    'internetworking/figures/impl/Slide5.png', 'internetworking/figures/impl/Slide6.png',
    'internetworking/figures/impl/Slide7.png', 'internetworking/figures/impl/Slide8.png',
    'applications/figures/apps/Slide1.png',
    'scaling/figures/structure/Slide1.png',
    'security/figures/firewall-std.png',
)

BOOKS: dict[str, Book] = {b.id: b for b in (
    # 웹·GitHub판만 CC BY. 인쇄판(AU Press 2013)은 BY-NC-ND라 쓰지 않음
    # - 그림은 저자 작성 Ipe 원본이라 라이선스는 문제없지만, 서버에 Ipe 변환 도구가 없어 수집 때 건너뜀
    Book('ods', 'Open Data Structures', 'Pat Morin', 'CC BY 2.5 CA',
         'https://creativecommons.org/licenses/by/2.5/ca/', 'https://opendatastructures.org/',
         'latex', 'github:patmorin/ods', 'all'),
    # PDF만 공개되어 그림 파일을 따로 뽑을 수 없으므로 본문만 씀
    Book('erickson', 'Algorithms', 'Jeff Erickson', 'CC BY 4.0',
         'https://creativecommons.org/licenses/by/4.0/', 'https://jeffe.cs.illinois.edu/teaching/algorithms/',
         'pdf', 'https://jeffe.cs.illinois.edu/teaching/algorithms/book/Algorithms-JeffE.pdf', 'none'),
    Book('peterson-davie', 'Computer Networks: A Systems Approach', 'Larry Peterson, Bruce Davie', 'CC BY 4.0',
         'https://creativecommons.org/licenses/by/4.0/', 'https://book.systemsapproach.org/',
         'rst', 'github:SystemsApproach/book', 'allowlist', _PD_FIGURES),
    # 그림 저작권은 저자에게 돌아와 같은 CC BY-SA로 공개됨(저장소 README)
    # - 그림 8.1(hail_f0801)은 Seagate 사진이라 책 안에서만 허용되고 따로 떼어 쓸 수 없으므로 제외
    Book('hailperin', 'Operating Systems and Middleware: Supporting Controlled Interaction (Rev. 1.3.1)',
         'Max Hailperin', 'CC BY-SA 3.0', 'https://creativecommons.org/licenses/by-sa/3.0/',
         'https://gustavus.edu/+max/os-book/', 'latex',
         'github:Max-Hailperin/Operating-Systems-and-Middleware--Supporting-Controlled-Interaction',
         'all', figure_deny=('hail_f0801',)),
    # 사람이 내려받음: RAG_DATA_DIR/raw/openstax-cs/*.pdf. 그림마다 출처 표기가 달라 확인 전에는 쓰지 않음
    Book('openstax-cs', 'Introduction to Computer Science', 'OpenStax', 'CC BY-NC-SA 4.0',
         'https://creativecommons.org/licenses/by-nc-sa/4.0/',
         'https://openstax.org/details/books/introduction-computer-science', 'pdf', None, 'none'),
    # 사람이 내려받음: RAG_DATA_DIR/raw/watt-db/*.epub. 사이트가 자동 요청을 403으로 막으므로 우회하지 않음
    # - 그림별 출처 표기를 아직 확인하지 못해 'none'으로 시작
    Book('watt-db', 'Database Design – 2nd Edition', 'Adrienne Watt, Nelson Eng', 'CC BY 4.0',
         'https://creativecommons.org/licenses/by/4.0/', 'https://opentextbc.ca/dbdesign01/',
         'epub', None, 'none'),
    # 그림은 파일마다 Wikimedia Commons 라이선스가 달라 확인 전에는 쓰지 않음
    Book('wikibooks-mpd', 'Microprocessor Design', 'Wikibooks contributors', 'CC BY-SA 4.0',
         'https://creativecommons.org/licenses/by-sa/4.0/', 'https://en.wikibooks.org/wiki/Microprocessor_Design',
         'wikibooks', 'wikibooks:Microprocessor Design', 'none'),
)}
