## @file
# @brief 공개 교재 원본을 내려받고 절로 분해해 sections.jsonl과 PNG 그림을 만드는 명령(python -m rag.fetch [--book ID])
# - 저장 위치는 RAG_DATA_DIR(없으면 ai-core/data/rag). 모두 Git 밖이라 교재 원문·그림이 저장소에 들어가지 않음
#   - raw/<book>/: 받은 원본, figures/<book>/<file>.png: 변환한 그림, sections.jsonl: 절 결과
# - fetch=None인 책(Watt·OpenStax)은 사이트가 자동 요청을 막거나 약관상 사람이 받아야 하므로 놓을 위치만 안내함
# - 이미 받은 원본·변환한 그림은 다시 받지 않음. 새로 받으려면 해당 raw/<book>/ 폴더를 지우고 실행
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path

from rag.parse import parse_book
from rag.sources import BOOKS

DATA_DIR = Path(os.environ.get('RAG_DATA_DIR') or Path(__file__).resolve().parents[1] / 'data' / 'rag')
# 원본 서버 한 번의 응답 대기 한도(초). 넘으면 한 번만 다시 시도하고, 그래도 실패하면 그 책만 건너뜀
TIMEOUT = 30
USER_AGENT = 'cs-learning-agent-rag/0.1 (https://github.com/noveeda/cs-learning-agent)'
# GitHub 저장소에서 풀어 둘 파일: 본문(.tex·.rst)과 그림(.pdf·.png), 라이선스 안내. 슬라이드·EPS 원본 등은 받지 않음
KEEP_SUFFIXES = {'.tex', '.rst', '.png', '.pdf', '.md'}
KEEP_NAMES = {'README', 'LICENSE', 'COPYING'}


## @brief 주소 하나를 열어 응답을 파일 객체로 넘김. 시간 초과·연결 오류면 1회 재시도
def _download(url, sink):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                sink.seek(0)
                sink.truncate()
                shutil.copyfileobj(response, sink)
                return
        except OSError:
            if attempt == 2:
                raise


## @brief GitHub 저장소의 기본 브랜치 묶음(tar.gz)을 받아 필요한 파일만 dest에 풂
# - 맨 앞 폴더 이름(<repo>-<커밋>)은 떼어 raw/<book>/ 바로 아래가 저장소 최상위가 되게 함
# - filter='data'로 절대 경로·링크 같은 위험한 항목은 풀지 않음
def fetch_github(repo, dest):
    with tempfile.TemporaryFile() as tmp:
        _download(f'https://codeload.github.com/{repo}/tar.gz/HEAD', tmp)
        tmp.seek(0)
        with tarfile.open(fileobj=tmp, mode='r:gz') as tar:
            for member in tar.getmembers():
                parts = member.name.split('/', 1)
                name = Path(member.name).name
                if len(parts) < 2 or not member.isfile():
                    continue
                if Path(name).suffix.lower() not in KEEP_SUFFIXES and name not in KEEP_NAMES:
                    continue
                member.name = parts[1]
                tar.extract(member, dest, filter='data')


## @brief 파일 하나(PDF 등)를 받아 dest/<주소의 파일 이름>으로 저장
def fetch_file(url, dest):
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / Path(urllib.parse.urlparse(url).path).name
    with open(target, 'wb') as sink:
        _download(url, sink)


## @brief MediaWiki API로 JSON 응답 하나를 받음
def _wiki_api(params):
    query = urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': 2})
    with tempfile.TemporaryFile() as tmp:
        _download(f'https://en.wikibooks.org/w/api.php?{query}', tmp)
        tmp.seek(0)
        return json.load(tmp)


## @brief Wikibooks 책의 하위 쪽(<책>/<쪽>) 위키 문법 원문을 dest/<쪽>.wiki로 저장
# - 넘겨주기(redirect) 쪽은 내용이 없어 받지 않음. 쪽 이름의 '/'는 파일 이름에 쓸 수 없어 ' - '로 바꿈
# - 한 번에 50쪽씩 묶어 요청해 위키 서버 호출 수를 줄임
def fetch_wikibooks(name, dest):
    pages = _wiki_api({'action': 'query', 'list': 'allpages', 'apprefix': f'{name}/', 'apnamespace': 0,
                       'aplimit': 500, 'apfilterredir': 'nonredirects'})['query']['allpages']
    titles = [p['title'] for p in pages]
    dest.mkdir(parents=True, exist_ok=True)
    for k in range(0, len(titles), 50):
        batch = _wiki_api({'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main',
                           'titles': '|'.join(titles[k:k + 50])})
        for page in batch['query']['pages']:
            content = page.get('revisions', [{}])[0].get('slots', {}).get('main', {}).get('content')
            if content:
                stem = page['title'].removeprefix(f'{name}/').replace('/', ' - ')
                (dest / f'{stem}.wiki').write_text(content)


## @brief 책의 fetch 값에 맞는 방식으로 원본을 raw/<book>/에 받음
def fetch_book(book, raw):
    if book.fetch.startswith('github:'):
        fetch_github(book.fetch.removeprefix('github:'), raw)
    elif book.fetch.startswith('wikibooks:'):
        fetch_wikibooks(book.fetch.removeprefix('wikibooks:'), raw)
    else:
        fetch_file(book.fetch, raw)


## @brief 절에 남은(라이선스 확인된) 그림을 figures/<book>/<file>.png로 변환하고 (변환 수, 건너뛴 수) 반환
# - LaTeX는 확장자 없이 그림을 가리키므로 .pdf·.png를 붙여 원본을 찾음
# - PDF 그림은 Ghostscript로 첫 쪽만 200dpi PNG로, PNG는 그대로 복사
# - 변환할 수 없는 형식(ODS의 Ipe 원본 등)이나 원본이 없는 그림은 건너뛰고 건수만 셈
def convert_figures(sections, raw, out):
    converted = skipped = 0
    for file in dict.fromkeys(f.file for s in sections for f in s.figures):
        source = next((p for p in (raw / file, raw / f'{file}.pdf', raw / f'{file}.png')
                       if p.is_file() and p.suffix.lower() in ('.pdf', '.png')), None)
        target = out / Path(file).with_suffix('.png')
        if source is None:
            skipped += 1
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() == '.png':
                shutil.copyfile(source, target)
            else:
                subprocess.run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-dSAFER', '-sDEVICE=png16m', '-r200',
                                '-dFirstPage=1', '-dLastPage=1', f'-sOutputFile={target}', str(source)],
                               check=False, capture_output=True, timeout=120)
        if target.exists():
            converted += 1
        else:
            skipped += 1
    return converted, skipped


## @brief 수집 명령 진입점: 책별로 받기 → 절 분해 → 그림 변환 → sections.jsonl 저장, 책별 건수 표 출력
# - 이번에 처리한 책의 줄만 바꾸고, --book으로 고르지 않았거나 받기에 실패한 책의 기존 줄은 그대로 둠
# - 받기에 실패한 책이 있으면 나머지는 계속 처리하고 종료 코드 1을 반환
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m rag.fetch', description='공개 교재를 받아 절로 분해합니다.')
    parser.add_argument('--book', choices=sorted(BOOKS), help='이 책만 처리')
    args = parser.parse_args(argv)
    books = [BOOKS[args.book]] if args.book else list(BOOKS.values())
    status, rows, records = 0, [], {}
    for book in books:
        raw = DATA_DIR / 'raw' / book.id
        has_raw = raw.is_dir() and any(raw.iterdir())
        if book.fetch is None and not has_raw:
            print(f'[{book.id}] 사람이 직접 내려받는 책입니다: {book.home} 에서 받아 {raw}/ 에 두세요.')
        elif not has_raw:
            try:
                print(f'[{book.id}] 받는 중: {book.fetch}', flush=True)
                fetch_book(book, raw)
            except OSError as error:
                print(f'[{book.id}] 받기 실패: {error}', file=sys.stderr)
                status = 1
                continue
        sections = parse_book(book, DATA_DIR / 'raw')
        converted, skipped = convert_figures(sections, raw, DATA_DIR / 'figures' / book.id)
        records[book.id] = [asdict(s) for s in sections]
        rows.append((book.id, len(sections), sum(len(s.figures) for s in sections), converted, skipped))
    out = DATA_DIR / 'sections.jsonl'
    kept = []
    if out.exists():
        kept = [line for line in out.read_text().splitlines() if json.loads(line)['book'] not in records]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(''.join(f'{line}\n' for line in kept)
                   + ''.join(json.dumps(r, ensure_ascii=False) + '\n' for rs in records.values() for r in rs))
    print('| 책 | 절 | 그림 참조 | PNG 변환 | 건너뜀 |\n|---|---:|---:|---:|---:|')
    for row in rows:
        print('| {} | {} | {} | {} | {} |'.format(*row))
    return status


if __name__ == '__main__':
    sys.exit(main())
