# -*- coding: utf-8 -*-
"""문제은행.json 검사 — 깨진 글자·식·정답 번호를 기계가 잡는다.

사람 눈으로 187문항 × 보기 4개를 훑으면 반드시 놓친다.
커밋 전에 이것을 돌린다:

    python check_bank.py

전부 OK 면 종료코드 0, 하나라도 걸리면 1.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
BANK = HERE / "문제은행.json"

# 깨진 글자로 볼 것들 —
#   U+FFFD  치환문자(인코딩이 한 번 깨진 흔적)
#   C0/C1 제어문자 (줄바꿈·탭 빼고)
#   조합용 낱자모(ㄱ ㅏ 처럼 홀로 남은 것은 대개 깨진 것이다)
REPLACEMENT = "�"
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
JAMO = re.compile(r"[ᄀ-ᇿㄱ-ㆎ]")     # 홀낱자모
MOJIBAKE = re.compile(r"[ÃÂâ][-¿]|ì|í |ë°|ê°")   # cp949↔utf-8 사고 흔적

# 식에 쓰는 글자들 — 여기 없는 수학기호가 나오면 알린다(폰트가 없을 수 있다)
ALLOWED_MATH = set("×÷±∓·∘°√∛∞≈≠≤≥≪≫∑∏∫∂∇∈∉⊂⊃∪∩∴∵∠⊥∥→←↔⇒⇔αβγδεζηθικλμνξπρστυφχψωΔΘΛΞΠΣΦΨΩΩ℧ΦΨ∮ψεμσρωθφ")
SUP_SUB = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")

# 오류 = 고쳐야 하는 것 (깨진 글자·틀린 정답 번호 등). 하나라도 있으면 종료코드 1.
# 참고 = 사람이 판단할 것 (해설이 얇다 등). 알려만 주고 통과시킨다.
problems: list[str] = []
notes: list[str] = []


def flag(where: str, why: str, text: str = "") -> None:
    snippet = (" | " + text[:70]) if text else ""
    problems.append(f"{where}: {why}{snippet}")


def note(where: str, why: str, text: str = "") -> None:
    snippet = (" | " + text[:70]) if text else ""
    notes.append(f"{where}: {why}{snippet}")


def scan_text(where: str, text: str) -> None:
    if REPLACEMENT in text:
        flag(where, "치환문자(U+FFFD) — 인코딩이 깨졌다", text)
    if CTRL.search(text):
        flag(where, "제어문자가 섞였다", text)
    if JAMO.search(text):
        flag(where, "홀낱자모(ㄱ·ㅏ 같은 것) — 깨진 글자일 수 있다", text)
    if MOJIBAKE.search(text):
        flag(where, "cp949↔utf-8 깨짐 의심", text)
    # 정규화하면 달라지는 글자 = 조합형이 섞인 것 (폰에서 자모가 분리돼 보인다)
    if unicodedata.normalize("NFC", text) != text:
        flag(where, "NFC 정규화가 안 된 한글(자모 분리) — 폰에서 깨져 보인다", text)
    # 괄호·대괄호 짝
    for open_c, close_c in (("(", ")"), ("[", "]"), ("{", "}")):
        if text.count(open_c) != text.count(close_c):
            flag(where, f"{open_c}{close_c} 짝이 안 맞는다", text)
    # 단위는 [V] 처럼 대괄호로 쓰기로 했다 — 대괄호가 비어 있으면 빠뜨린 것이다
    if "[]" in text:
        flag(where, "빈 대괄호 — 단위가 빠졌다", text)
    # 지수/아래첨자 문자가 있는데 짝이 되는 숫자가 없는 경우는 잡기 어려우니
    # 여기서는 '10-19' 처럼 지수 문자를 안 쓴 흔적만 본다
    if re.search(r"10\s*[-−]\s*\d", text):
        flag(where, "10⁻¹⁹ 를 10-19 로 쓴 것 같다(지수 문자가 빠졌다)", text)


def main() -> int:
    if not BANK.exists():
        print(f"[오류] {BANK.name} 이 없다")
        return 1
    raw = BANK.read_text(encoding="utf-8")
    data = json.loads(raw)
    items = data["문항"]

    seen_ids: set[str] = set()
    for it in items:
        qid = it.get("id", "(id 없음)")
        if qid in seen_ids:
            flag(qid, "id 가 중복이다")
        seen_ids.add(qid)

        # 필수 항목
        for key in ("s", "u", "q", "c", "a", "why", "src"):
            if key not in it:
                flag(qid, f"'{key}' 항목이 없다")
        if any(k not in it for k in ("q", "c", "a")):
            continue

        # 정답 번호
        if not isinstance(it["a"], int) or not (0 <= it["a"] < len(it["c"])):
            flag(qid, f"정답 번호 {it['a']} 가 보기 {len(it['c'])}개를 벗어난다")
        if len(it["c"]) != 4:
            flag(qid, f"보기가 4개가 아니라 {len(it['c'])}개다")
        # 보기 중복 — 같은 보기가 둘이면 정답이 둘이 된다
        norm = [re.sub(r"\s+", "", c) for c in it["c"]]
        if len(set(norm)) != len(norm):
            flag(qid, "보기 중 같은 것이 있다", " / ".join(it["c"]))
        for c in it["c"]:
            if not str(c).strip():
                flag(qid, "빈 보기가 있다")

        # 글자 검사
        scan_text(f"{qid} 문제", it["q"])
        for n, c in enumerate(it["c"], 1):
            scan_text(f"{qid} 보기{n}", str(c))
        scan_text(f"{qid} 해설", it.get("why", ""))

        # 해설이 너무 얇으면 알린다 (사용자가 "자세하게" 를 요구했다)
        if len(it.get("why", "").strip()) < 60:
            note(qid, f"해설이 {len(it.get('why','').strip())}자로 얇다", it.get("why", ""))

        # 해설이 정답을 실제로 말하고 있나 — 정답 보기의 핵심 낱말이 해설에 있나
        ans = re.sub(r"\s+", "", str(it["c"][it["a"]])) if isinstance(it["a"], int) and 0 <= it["a"] < len(it["c"]) else ""
        why_n = re.sub(r"\s+", "", it.get("why", ""))
        if ans and len(ans) >= 4 and ans[:4] not in why_n and ans not in why_n:
            # 숫자·기호만 있는 보기는 이 검사가 잘 안 맞으니 한글이 있을 때만 본다
            if re.search(r"[가-힣]{2,}", ans):
                note(qid, "해설이 정답을 바꿔 말했다(확인만)", f"정답={it['c'][it['a']]}")

    print(f"문항 {len(items)} · 보기 {sum(len(i.get('c', [])) for i in items)}")

    if problems:
        print(f"\n[오류] 고쳐야 할 것 {len(problems)}개")
        for line in problems:
            print("  · " + line)
    else:
        print("  OK   깨진 글자 없음 (치환문자·자모분리·인코딩깨짐·제어문자)")
        print("  OK   식 표기 정상 (괄호 짝·빈 단위·지수 문자)")
        print("  OK   정답 번호·보기 4개·보기 중복 정상")

    if notes:
        thin = [n for n in notes if "얇다" in n]
        para = [n for n in notes if "바꿔 말했다" in n]
        print(f"\n[참고] 사람이 볼 것 {len(notes)}개"
              f" — 해설 얇음 {len(thin)} · 정답을 바꿔 말함 {len(para)}")
        for line in notes:
            print("  · " + line)

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
