from pathlib import Path

TARGET_URL = "https://www.bigkinds.or.kr/regional/curation.do"
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
WORK_DIR = ROOT_DIR / "work"
SCREENSHOT_DIR = ROOT_DIR / "artifacts" / "screenshots"
TRACE_DIR = ROOT_DIR / "artifacts" / "traces"

RESULT_COLUMNS = [
    "순번", "조회요청일", "화면표시일", "지역명", "이슈순번", "이슈제목", "이슈분류",
    "출처수", "출처구분", "언론사명", "기사일자", "기사제목", "원본URL", "최종URL",
    "HTTP상태", "브라우저표시결과", "링크작동여부_YN", "최종판정", "응답시간_초",
    "오류내용", "점검일시",
]

DEBUG_COLUMNS = [
    "로그시각", "실행단계", "조회요청일", "화면표시일", "지역명", "이슈순번", "이슈제목",
    "href속성원문", "href프로퍼티값", "클릭대상URL원문", "URL처리전", "원본URL", "URL처리방식",
    "클릭직전URL", "클릭직후URL", "새페이지최초URL", "새탭생성여부_YN", "현재탭이동여부_YN",
    "추정정상URL", "URL구조이상여부", "URL구조이상내용", "DOM또는Locator",
    "이벤트", "예외유형", "상세내용", "HTTP상태", "최종URL", "스크린샷경로", "재시도횟수",
    "접근제한판정근거코드", "감지문구", "감지문구Locator", "감지문구DOM영역",
    "감지문구Visible_YN", "document.title", "visible h1", "article존재여부_YN",
    "주요콘텐츠텍스트길이", "기사제목일치여부_YN", "기사렌더링근거여부_YN",
]

VERDICTS = ["정상", "접근제한", "링크오류", "서버오류", "타임아웃", "클릭오류", "빈화면", "확인필요"]
ISSUE_CATEGORIES = {"정치", "경제", "사회", "문화", "국제", "IT과학"}
