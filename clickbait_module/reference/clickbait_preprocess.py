# clickbait_preprocess.py
# 원본 학습 JSON(Part1/Part2)을 단일 기사 판별기에 넣기 위한 평탄 JSON으로 변환하는 모듈

import os
import json
from typing import Dict, Any

# ----- 카테고리 정규화 ------

CATEGORY_MAP = {
    # 한글 → 코드
    "경제": "EC",
    "연예": "ET",
    "세계": "GB",
    "IT&과학": "IS",
    "생활&문화": "LC",
    "정치": "PO",
    "사회": "SO",
    # 이미 코드로 들어오는 경우
    "EC": "EC",
    "ET": "ET",
    "GB": "GB",
    "IS": "IS",
    "LC": "LC",
    "PO": "PO",
    "SO": "SO",
}

def normalize_category(raw_cat: Any) -> str:
    """newsCategory를 EC/ET/GB/IS/LC/PO/SO/ETC 중 하나로 정규화."""
    if raw_cat is None:
        return "ETC"
    s = str(raw_cat).strip()
    if s in CATEGORY_MAP:
        return CATEGORY_MAP[s]
    # 부분 일치 방어적 처리 (예: "IT/과학, 헬스 > 모바일")
    for key, code in CATEGORY_MAP.items():
        if key in s:
            return code
    return "ETC"


# ----- 단일 기사 전처리 ------

def preprocess_article(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    원본 JSON 1개를 받아서 판별기 입력용 평탄 JSON으로 변환.

    출력 필드:
      - newsID, newsCategory, newsTitle, newsContent
      - partNum, useType, processType, clickbaitClass
    """
    src = raw.get("sourceDataInfo", {})
    lab = raw.get("labeledDataInfo", {})

    part = src.get("partNum", "P1")
    use_type = src.get("useType")
    process_type = src.get("processType")
    clickbait_class = lab.get("clickbaitClass")

    # 1) 카테고리 정규화
    category = normalize_category(src.get("newsCategory"))

    # 2) 제목 결정
    new_title = lab.get("newTitle")
    src_title = src.get("newsTitle", "")

    # 기본 정책:
    #  - Part1: newTitle이 있으면 그게 최종 제목
    #  - Part2: newTitle이 특별히 들어오는 경우가 없다고 가정하지만,
    #            혹시 있으면 newTitle 우선, 없으면 원본 제목 사용
    if isinstance(new_title, str) and new_title.strip():
        final_title = new_title.strip()
    else:
        final_title = src_title.strip()

    # 3) 본문 결정
    src_content = src.get("newsContent", "")

    process_sents = lab.get("processSentenceInfo")
    if part == "P2" and isinstance(process_sents, list) and len(process_sents) > 0:
        # sentenceNo 기준 정렬 후 문장 내용만 이어 붙임
        sorted_sents = sorted(
            process_sents,
            key=lambda x: x.get("sentenceNo", 0)
        )
        final_content = "\n".join(
            (s.get("sentenceContent") or "").rstrip()
            for s in sorted_sents
        )
    else:
        # Part1 또는 processSentenceInfo가 없는 경우: 원문 본문 그대로 사용
        final_content = src_content

    # 4) 최종 평탄 JSON 구성
    flat = {
        "newsID": src.get("newsID"),
        "newsCategory": category,
        "newsTitle": final_title,
        "newsContent": final_content,
        "partNum": part,
        "useType": use_type,
        "processType": process_type,
        "clickbaitClass": clickbait_class,
    }
    return flat


# ----- 파일/디렉터리 단위 헬퍼 ------

def preprocess_file(src_path: str, dst_path: str) -> None:
    """
    원본 JSON 파일 하나를 읽어 전처리한 뒤 dst_path에 저장.
    """
    with open(src_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    flat = preprocess_article(raw)

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(flat, f, ensure_ascii=False, indent=2)


def preprocess_tree(src_root: str, dst_root: str) -> None:
    """
    src_root 아래의 모든 .json 파일을 재귀적으로 찾은 뒤,
    같은 상대 경로 구조로 dst_root 아래에 전처리 결과를 저장.

    예:
      src_root/GB_M08_510482_L.json
        → dst_root/GB_M08_510482_L.json
    """
    for cur, _dirs, files in os.walk(src_root):
        for name in files:
            if not name.lower().endswith(".json"):
                continue
            src_path = os.path.join(cur, name)
            rel = os.path.relpath(src_path, src_root)
            dst_path = os.path.join(dst_root, rel)

            preprocess_file(src_path, dst_path)


if __name__ == "__main__":
    # 간단한 CLI 예시:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="원본 JSON 파일 또는 디렉터리 경로")
    ap.add_argument("dst", help="전처리 결과를 저장할 파일 또는 디렉터리 경로")
    args = ap.parse_args()

    if os.path.isfile(args.src):
        # src가 파일이면 dst도 파일로 취급
        dst = args.dst
        # dst가 디렉터리면 같은 파일 이름으로 저장
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(args.src))
        preprocess_file(args.src, dst)
    else:
        # src가 디렉터리면 트리 전체 처리
        preprocess_tree(args.src, args.dst)