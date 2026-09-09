"""핵심 + 요약집 마크다운 3개 + 학습계획 + 공식 사용처를 한 페이지 HTML 로 묶는다.

원본은 항상 마크다운이다. HTML 을 손으로 고치지 말 것 — 여기서 다시 만든다.

    python build_summaries.py                 # summaries.html 생성 (그냥 열면 되는 완결 문서)
    python build_summaries.py --artifact 경로  # 아티팩트 업로드용 (doctype/head/body 없이 본문만)

마크다운은 이 폴더의 요약집이 쓰는 만큼만 읽는다:
제목(#/##/###), 표, 목록(중첩 포함), 인용, 구분선, 굵게/코드/링크, 그리고 $수식$ · $$수식$$.

4번째 탭(학습계획)만 예외로, 렌더링한 마크다운 위에 오늘 날짜 기준 D-day·진행률
위젯(PLAN_WIDGET/PLAN_JS)을 붙인다 — 날짜 계산은 손으로 맞지 않으니 스크립트가 한다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

# 이 스크립트의 경고문에는 ⚠·— 같은 글자가 있는데, 윈도우 기본 콘솔(cp949)
# 에서는 그걸 찍다가 UnicodeEncodeError 로 죽는다. 경고가 안 보이면 요약집이
# 조용히 깨진 채로 배포된다 — 경고보다 더 나쁜 일이라 출력을 UTF-8 로 고정한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent

# (파일, 탭 이름, 눈표, 강조색 light, 강조색 dark)
# 탭 색은 KEC 전선 식별 색상에서 가져왔다 — L1 갈 · L3 회 · N 청.
SUBJECTS = [
    ("Theory_Summary.md", "전기이론", "제1과목", "#8A5533", "#D0996B"),
    ("Machinery_Summary.md", "전기기기", "제2과목", "#4F6572", "#9DB6C4"),
    ("Facility_Summary.md", "전기설비", "제3과목", "#1F5FA8", "#7FAEE8"),
]

# ---------------------------------------------------------------- 수식(TeX)

SYMBOLS = {
    "times": "×", "cdot": "·", "approx": "≈", "neq": "≠", "le": "≤", "ge": "≥",
    "propto": "∝", "sim": "~", "iff": "⟺", "implies": "⟹", "Rightarrow": "⇒",
    "leftrightarrow": "↔", "sum": "Σ", "pi": "π", "mu": "μ", "eta": "η",
    "theta": "θ", "rho": "ρ", "varepsilon": "ε", "Phi": "Φ", "Omega": "Ω",
    "ell": "ℓ", "alpha": "α", "beta": "β", "lambda": "λ", "omega": "ω",
    "Delta": "Δ", "infty": "∞", "pm": "±", "div": "÷", "circ": "°",
    "sigma": "σ", "Sigma": "Σ", "gamma": "γ", "delta": "δ", "phi": "φ",
    "varphi": "φ", "psi": "ψ", "tau": "τ", "epsilon": "ε", "kappa": "κ",
    "oint": "∮", "int": "∫", "partial": "∂", "nabla": "∇",
    "Leftrightarrow": "⟺", "rightarrow": "→", "leftarrow": "←", "to": "→",
    "cdots": "⋯", "ldots": "…", "angle": "∠", "perp": "⊥", "parallel": "∥",
}
UPRIGHT_WORDS = {"sin", "cos", "tan", "log", "ln", "max", "min"}
SPACES = {"quad": "sp-q", "qquad": "sp-qq", ",": "sp-t", ";": "sp-t", ":": "sp-t", "!": ""}


def _read_group(s: str, i: int) -> tuple[str, int]:
    """s[i:] 에서 인자 하나를 떼어낸다. {..} 면 짝 맞는 데까지, 아니면 한 글자."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 1, i + 1
        while j < len(s) and depth:
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
            j += 1
        return s[i + 1: j - 1], j
    if s[i] == "\\":
        j = i + 1
        while j < len(s) and s[j].isalpha():
            j += 1
        return s[i:j], j
    return s[i], i + 1


# 모르는 TeX 명령을 여기 모은다. 조용히 버리면 공식에서 변수가 통째로 사라진다.
# (실제로 \sigma 를 모르는 바람에 E = σ/2ε₀ 가 'E = 2ε₀' 로 나온 적이 있다.
#  틀린 공식보다 나쁘다 — 보는 사람이 이상한 줄도 모른다.)
UNKNOWN: set[str] = set()
# 화면에 그대로 새어나온 굵게 표시(**). 인용문·목록은 줄마다 따로 처리하므로
# 굵게가 줄을 넘으면 닫히지 않고 별표가 그대로 보인다. 원본에서 한 줄 안에 닫아야 한다.
STRAY_BOLD: list[str] = []
DUP_IDS: list[str] = []
BAD_ANSWER: list[str] = []

# 일부러 버리는 명령. 괄호 크기 조절용이라 괄호 자체는 뒤에 따로 나온다.
IGNORED = {"left", "right", "displaystyle", "limits"}


def tex(s: str) -> str:
    """쓰는 만큼의 TeX 를 HTML 로 바꾼다. 모르는 명령은 UNKNOWN 에 모아 빌드 끝에 알린다."""
    out: list[str] = []
    letters: list[str] = []

    def flush() -> None:
        if letters:
            out.append(f"<i>{html.escape(''.join(letters))}</i>")
            letters.clear()

    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            j = i + 1
            while j < len(s) and s[j].isalpha():
                j += 1
            name = s[i + 1: j] if j > i + 1 else s[i + 1: i + 2]
            if not name:
                i += 1
                continue
            j = j if j > i + 1 else i + 2
            flush()
            if name in ("frac", "dfrac", "tfrac"):
                num, j = _read_group(s, j)
                den, j = _read_group(s, j)
                out.append(
                    f'<span class="frac"><span class="num">{tex(num)}</span>'
                    f'<span class="den">{tex(den)}</span></span>'
                )
            elif name == "sqrt":
                arg, j = _read_group(s, j)
                out.append(f'<span class="sqrt">{tex(arg)}</span>')
            elif name in ("text", "mathrm"):
                arg, j = _read_group(s, j)
                out.append(f'<span class="txt">{html.escape(arg)}</span>')
            elif name == "mathbf":
                arg, j = _read_group(s, j)
                out.append(f"<b>{tex(arg)}</b>")
            elif name in UPRIGHT_WORDS:
                out.append(f'<span class="txt">{name}</span>')
            elif name in SYMBOLS:
                out.append(f'<span class="op">{SYMBOLS[name]}</span>')
            elif name in SPACES:
                cls = SPACES[name]
                out.append(f'<span class="{cls}"></span>' if cls else "")
            elif name.isalpha() and name not in IGNORED:
                # 한 글자 기호(\, \{ 등)가 아니라 이름이 있는 명령인데 모른다.
                UNKNOWN.add(name)
            i = j
            continue
        if c in "^_":
            flush()
            arg, j = _read_group(s, i + 1)
            tag = "sup" if c == "^" else "sub"
            out.append(f"<{tag}>{tex(arg)}</{tag}>")
            i = j
            continue
        if c.isalpha():
            letters.append(c)
            i += 1
            continue
        flush()
        if c in "+-=<>":
            out.append(f'<span class="op">{html.escape(c)}</span>')
        elif c not in "{}":
            out.append(html.escape(c))
        i += 1
    flush()
    return "".join(out)


# ------------------------------------------------------------ 인라인 마크다운

MATH_SLOT = "\x00M{}\x00"


def inline(text: str) -> str:
    stash: list[str] = []

    def keep(m: re.Match[str]) -> str:
        body = m.group(1) or m.group(2) or ""
        block = m.group(1) is not None
        cls = "math math-inline-block" if block else "math"
        stash.append(f'<span class="{cls}">{tex(body)}</span>')
        return MATH_SLOT.format(len(stash) - 1)

    text = re.sub(r"\$\$(.+?)\$\$|\$([^$]+?)\$", keep, text, flags=re.S)
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)
    if "**" in text:
        STRAY_BOLD.append(text.strip()[:70])
    text = re.sub(
        r"\[(.+?)\]\((.+?)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )
    text = text.replace("★", '<span class="star">★</span>')
    text = re.sub(r"\x00M(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text


# ------------------------------------------------------------- 블록 마크다운

def slug(prefix: str, n: int) -> str:
    return f"{prefix}-s{n}"


def render(md: str, prefix: str) -> tuple[str, list[tuple[str, str]], str]:
    """마크다운 → (본문 HTML, [(앵커, 섹션명)], 문서 제목)"""
    lines = md.split("\n")
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    title = ""
    liststack: list[str] = []     # 'ul' | 'ol'
    indents: list[int] = []
    in_section = False
    in_quote = False
    sec_no = 0
    i = 0

    def close_lists(to: int = 0) -> None:
        while len(liststack) > to:
            out.append(f"</li></{liststack.pop()}>")
            indents.pop()

    def close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    def close_section() -> None:
        nonlocal in_section
        close_lists()
        close_quote()
        if in_section:
            out.append("</section>")
            in_section = False

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            close_lists()
            close_quote()
            i += 1
            continue

        # 그림 —  :::: 설명글  <svg>...</svg>  ::::
        # 본문 마크다운은 html.escape 를 거치므로 SVG 를 그냥 쓰면 글자로 나온다.
        # 처음 공부하는 사람은 "자속·기자력" 같은 말이 글로만은 안 그려진다고 해서,
        # 그림을 넣을 통로를 하나 텄다. 안쪽은 손으로 쓴 SVG 를 그대로 통과시킨다.
        # ⚠ ::: 보다 먼저 검사한다 — 3콜론 규칙이 4콜론을 먼저 먹으면 안 된다.
        if line.startswith("::::"):
            close_lists()
            close_quote()
            caption = line[4:].strip()
            i += 1
            raw_svg: list[str] = []
            while i < len(lines) and lines[i].strip() != "::::":
                raw_svg.append(lines[i])
                i += 1
            i += 1
            cap = f'<figcaption>{inline(caption)}</figcaption>' if caption else ""
            out.append(f'<figure class="fig">{"".join(raw_svg)}{cap}</figure>')
            continue

        # 접이식 상세 설명 —  ::: 제목  ...  :::
        # 공식만 있고 "왜 그런지"가 없으면 외워도 문제 앞에서 못 꺼낸다.
        # 그렇다고 본문에 길게 풀어 쓰면 시험 직전에 훑기 나빠진다 —
        # 그래서 접어 둔다(기본은 닫힘, 궁금할 때만 편다).
        if line.startswith(":::"):
            close_lists()
            close_quote()
            summary = line[3:].strip() or "왜 이 식인가"
            i += 1
            buf: list[str] = []
            while i < len(lines) and lines[i].strip() != ":::":
                buf.append(lines[i])
                i += 1
            i += 1  # 닫는 ::: 를 건너뛴다
            # 접이식 안쪽도 본문과 똑같은 규칙으로 그린다 (표·인용·목록·수식 전부).
            # 전에는 문단과 목록만 처리해서, 안에 넣은 표가 화면에
            # "| 글자 | 뜻 |" 파이프 글자 그대로 보였다 — 표인 줄도 모른다.
            # 안쪽에는 ## 를 쓰지 않는다(목차 앵커가 바깥과 겹친다).
            inner_html, _, _ = render("\n".join(buf), prefix)
            out.append(
                f'<details class="why"><summary>{inline(summary)}</summary>'
                f'<div class="why-body">{inner_html}</div></details>'
            )
            continue

        # 표 — 목록 안에 들여쓴 것도 표로 본다
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            close_lists()
            close_quote()
            head = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            cells = "".join(f"<th>{inline(c)}</th>" for c in head)
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(f'<div class="scroller"><table><thead><tr>{cells}</tr></thead><tbody>{body}</tbody></table></div>')
            continue

        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            text = line[level:].strip()
            if level == 1:
                title = text
                i += 1
                continue
            if level == 2:
                close_section()
                sec_no += 1
                anchor = slug(prefix, sec_no)
                chip = re.sub(r"\s*\([^)]*\)\s*$", "", text)
                chip = re.sub(r"^[0-9]+\.\s*", "", chip)
                toc.append((anchor, chip.strip()))
                check = " card-check" if "✅" in text else ""
                out.append(f'<section class="card{check}" id="{anchor}"><h2>{inline(text)}</h2>')
                in_section = True
            else:
                close_lists()
                close_quote()
                low = " low" if "▽" in text else ""
                out.append(f'<h3 class="h3{low}">{inline(text)}</h3>')
            i += 1
            continue

        if line.startswith("---"):
            close_lists()
            close_quote()
            i += 1
            continue

        if line.startswith(">"):
            close_lists()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{inline(line.lstrip('> ').strip())}</p>")
            i += 1
            continue

        m = re.match(r"^([*\-]|\d+\.)\s+(.*)$", line)
        if m:
            close_quote()
            kind = "ol" if m.group(1)[0].isdigit() else "ul"
            indent = len(raw) - len(raw.lstrip(" "))
            if not liststack:
                out.append(f"<{kind}>")
                liststack.append(kind)
                indents.append(indent)
                out.append("<li>")
            elif indent > indents[-1]:
                out.append(f"<{kind}>")
                liststack.append(kind)
                indents.append(indent)
                out.append("<li>")
            else:
                while len(liststack) > 1 and indent < indents[-1]:
                    out.append(f"</li></{liststack.pop()}>")
                    indents.pop()
                out.append("</li><li>")
            out.append(inline(m.group(2)))
            i += 1
            continue

        # 그냥 문단 (목록 안이면 이어 붙인다)
        if liststack:
            out.append("<br>" + inline(line))
        else:
            close_quote()
            body = inline(line)
            cls = ' class="display"' if body.count('math-inline-block') == 1 and body.strip().startswith('<span class="math math-inline-block"') else ""
            out.append(f"<p{cls}>{body}</p>")
        i += 1

    close_section()
    return "\n".join(out), toc, title


# ------------------------------------------------------------------- 페이지

CSS = """
:root{
  --bg:#FAF9F7; --surface:#FFFFFF; --ink:#1B1E24; --muted:#5F6570;
  --line:#E3DFD8; --line-soft:#EFEBE4; --code:#F2EFE9;
  --accent:#1F5FA8; --pe:#5F7F33; --pe-bg:#F0F3E4;
  --s1:#8A5533; --s2:#4F6572; --s3:#1F5FA8; --fx:#2B2B2B; --core:#B4553A; --danger:#B84B2A;
  --shadow:0 1px 2px rgba(27,30,36,.05), 0 8px 24px -16px rgba(27,30,36,.28);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#14171C; --surface:#1B1F26; --ink:#E7E4DE; --muted:#9AA3AF;
    --line:#2C323B; --line-soft:#232830; --code:#232830;
    --accent:#7FAEE8; --pe:#A9C46C; --pe-bg:#1F2519;
    --s1:#D0996B; --s2:#9DB6C4; --s3:#7FAEE8; --fx:#D5D5D5; --core:#E89478; --danger:#E38564;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"]{
  --bg:#14171C; --surface:#1B1F26; --ink:#E7E4DE; --muted:#9AA3AF;
  --line:#2C323B; --line-soft:#232830; --code:#232830;
  --accent:#7FAEE8; --pe:#A9C46C; --pe-bg:#1F2519;
  --s1:#D0996B; --s2:#9DB6C4; --s3:#7FAEE8; --fx:#D5D5D5; --core:#E89478; --danger:#E38564;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.8);
}

*{box-sizing:border-box;}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Pretendard","Pretendard Variable","Apple SD Gothic Neo","Noto Sans KR",
    system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:16px; line-height:1.75; -webkit-text-size-adjust:100%;
  word-break:keep-all; overflow-wrap:anywhere;   /* 한글은 어절 단위로 끊는다 */
}
.wrap{max-width:44rem; margin:0 auto; padding:0 1rem 5rem;}

/* --- 머리 --- */
.masthead{padding:2.25rem 0 1.25rem;}
.eyebrow{
  font-size:.72rem; letter-spacing:.16em; text-transform:uppercase;
  color:var(--muted); font-weight:700; margin:0 0 .5rem;
}
.masthead h1{
  margin:0; font-size:1.75rem; line-height:1.3; letter-spacing:-.02em;
  font-weight:800; text-wrap:balance;
}
.masthead p{margin:.6rem 0 0; color:var(--muted); font-size:.92rem;}
.legend{
  display:flex; flex-wrap:wrap; gap:.5rem .9rem; margin:.9rem 0 0;
  font-size:.78rem; color:var(--muted);
}
.legend span{display:inline-flex; align-items:center; gap:.35rem;}
.legend i{width:.7rem; height:.7rem; border-radius:2px; display:inline-block;}

/* --- 탭 --- */
.tabs{
  position:sticky; top:0; z-index:30; background:var(--bg);
  display:flex; gap:.4rem; padding:.55rem 0; border-bottom:1px solid var(--line);
}
.tab{
  flex:1; appearance:none; border:1px solid var(--line); background:var(--surface);
  color:var(--muted); font:inherit; font-size:.88rem; font-weight:700;
  padding:.5rem .4rem; border-radius:.5rem; cursor:pointer; line-height:1.35;
  display:flex; flex-direction:column; align-items:center; gap:.1rem;
  transition:color .15s, border-color .15s, background .15s;
}
.tab small{font-size:.66rem; font-weight:600; letter-spacing:.06em; opacity:.85;}
.tab:hover{color:var(--ink);}
.tab[aria-selected="true"]{
  color:var(--surface); background:var(--tab); border-color:var(--tab);
}
.tab:focus-visible, .chip:focus-visible, .totop:focus-visible{
  outline:2px solid var(--accent); outline-offset:2px;
}

/* --- 섹션 칩 --- */
.chips{
  /* ⚠ 예전엔 top:3.55rem 고정이었다. 탭이 4개일 땐 맞았지만 5번째 탭
     ("공식 사용처")이 두 줄로 접히면서 탭 바가 90px 로 높아지자, 칩 바가
     탭 바 뒤에 숨어버렸다(z-index 도 탭이 위라 반쯤 잘려 보였다).
     이제 JS 가 실제 높이를 재서 --tabs-h 에 넣는다. 아래 값은 JS 가 아직
     안 돌았을 때의 대비책일 뿐이다. */
  position:sticky; top:var(--tabs-h, 3.55rem); z-index:20; background:var(--bg);
  display:flex; gap:.35rem; overflow-x:auto; padding:.55rem 0;
  border-bottom:1px solid var(--line-soft); scrollbar-width:none;
}
.chips::-webkit-scrollbar{display:none;}
.chip{
  /* 폰에서 누를 것이라 손가락 크기를 먼저 맞춘다 — 예전엔 높이가 33px 라
     애플 권장 최소 터치 영역(44px)에 못 미쳐 누르기 불편했다.
     min-height 로 44px 를 보장하고, 세로 가운데 정렬로 글자를 맞춘다. */
  flex:0 0 auto; text-decoration:none; white-space:nowrap;
  display:inline-flex; align-items:center; min-height:44px;
  font-size:.82rem; font-weight:600; color:var(--muted);
  border:1px solid var(--line); border-radius:999px; padding:0 .85rem;
  background:var(--surface);
  -webkit-tap-highlight-color:transparent;
}
.chip:active{background:var(--line-soft); color:var(--ink);}
.chip:hover{color:var(--accent); border-color:var(--accent);}

/* --- 본문 --- */
.panel{padding-top:1.25rem;}
.panel > p{margin:1.1rem 0 1.35rem; color:var(--muted); font-size:.92rem;}
.panel[hidden]{display:none;}
.card{
  background:var(--surface); border:1px solid var(--line); border-radius:.75rem;
  padding:1.1rem 1.1rem 1.25rem; margin:0 0 1rem; box-shadow:var(--shadow);
  /* 스티키 두 줄(탭+칩) 높이만큼 띄운다. 7rem 고정이던 것을 실측으로 바꿨다 —
     탭이 두 줄로 접히면 7rem 으로는 모자라 제목이 가려졌다. */
  scroll-margin-top:calc(var(--tabs-h, 3.55rem) + var(--chips-h, 3.2rem) + .6rem);
}
.card h2{
  margin:0 0 .85rem; font-size:1.12rem; font-weight:800; letter-spacing:-.01em;
  line-height:1.45; padding-bottom:.6rem; border-bottom:2px solid var(--accent);
}
.card h3{margin:1.35rem 0 .5rem; font-size:.98rem; font-weight:700; color:var(--accent);}
.card > h3:first-of-type{margin-top:.25rem;}
.card p{margin:.55rem 0;}
.card ul,.card ol{margin:.5rem 0; padding-left:1.15rem;}
.card li{margin:.3rem 0;}
.card li > ul, .card li > ol{margin:.25rem 0;}
strong{font-weight:700;}
code{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:.86em; background:var(--code); padding:.1em .35em; border-radius:.25rem;
}
a{color:var(--accent);}
blockquote{
  margin:.8rem 0; padding:.65rem .9rem; border-left:3px solid var(--accent);
  background:var(--code); border-radius:0 .4rem .4rem 0; font-size:.92rem;
}
blockquote p{margin:.2rem 0;}
.star{color:var(--pe); font-weight:700;}

/* --- ▽ 후순위 — 시간이 없으면 여기부터 버린다 --- */
h3.low{opacity:.55;}
h3.low::after{
  content:"후순위"; margin-left:.45rem; padding:.1rem .4rem;
  border:1px solid var(--line); border-radius:.35rem;
  font-size:.68rem; font-weight:700; color:var(--muted); vertical-align:middle;
}

/* --- 접이식 상세 설명(::: 블록) --- */
.fig{
  margin:1rem 0 1.25rem; padding:1rem .75rem .6rem;
  border:1px solid var(--line); border-radius:.7rem;
  background:var(--surface); text-align:center;
}
.fig svg{max-width:100%; height:auto; overflow:visible;}
.fig svg text{fill:var(--ink); font-family:inherit;}
.fig svg .lbl{font-size:11px; font-weight:700;}
.fig svg .sub{font-size:9.5px; fill:var(--muted);}
.fig svg .ln{stroke:var(--ink); stroke-width:2; fill:none; stroke-linecap:round;}
.fig svg .ln2{stroke:var(--muted); stroke-width:1.4; fill:none; stroke-dasharray:4 3;}
.fig svg .acc{stroke:var(--accent); stroke-width:2.6; fill:none; stroke-linecap:round;}
.fig svg .fill{fill:var(--accent); opacity:.16;}
.fig figcaption{
  margin-top:.6rem; font-size:.86rem; color:var(--muted); line-height:1.6;
  text-align:left; word-break:keep-all;
}
.why{
  margin:.5rem 0 1rem; border:1px solid var(--line);
  border-radius:.6rem; background:var(--bg); overflow:hidden;
}
.why > summary{
  cursor:pointer; list-style:none; padding:.55rem .8rem;
  font-size:.86rem; font-weight:700; color:var(--muted);
  display:flex; align-items:center; gap:.4rem; min-height:44px;
  -webkit-tap-highlight-color:transparent;
}
.why > summary::-webkit-details-marker{display:none;}
.why > summary::before{content:"＋"; font-weight:800; color:var(--accent);}
.why[open] > summary::before{content:"－";}
.why[open] > summary{border-bottom:1px solid var(--line); color:var(--ink);}
.why > summary:active{background:var(--line-soft);}
.why-body{padding:.75rem .9rem .9rem; font-size:.92rem; line-height:1.75;}
.why-body p{margin:0 0 .6rem;}
.why-body p:last-child, .why-body ul:last-child{margin-bottom:0;}
.why-body ul{margin:0 0 .6rem; padding-left:1.15rem;}
.why-body li{margin:.2rem 0;}
/* 접이식 안의 표·인용도 본문과 같게 보이되, 마지막 여백만 없앤다 */
.why-body .scroller{margin:.6rem 0;}
.why-body blockquote{margin:.6rem 0;}
.why-body > :last-child{margin-bottom:0;}

/* 체크리스트 카드 */
.card-check{border-color:var(--pe); background:var(--pe-bg);}
.card-check h2{border-bottom-color:var(--pe);}
.card-check ol{counter-reset:ck; list-style:none; padding-left:0;}
.card-check li{
  counter-increment:ck; position:relative; padding-left:2rem; margin:.55rem 0;
}
.card-check li::before{
  content:counter(ck); position:absolute; left:0; top:.28rem;
  width:1.4rem; height:1.4rem; border-radius:50%; background:var(--pe);
  color:var(--bg); font-size:.75rem; font-weight:700; line-height:1.4rem;
  text-align:center;
}

/* --- 학습계획 위젯 (4번째 탭 전용) --- */
.plan-widget{
  background:var(--code); border:1px solid var(--line); border-radius:.6rem;
  padding:.9rem 1rem 1rem; margin:0 0 1.1rem;
}
.plan-top{display:flex; flex-direction:column; gap:.15rem;}
.plan-label{font-size:.76rem; color:var(--muted); font-weight:600;}
.plan-dday{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:1.5rem;
  font-weight:800; color:var(--danger); font-variant-numeric:tabular-nums;
}
.plan-rail{display:flex; gap:3px; margin:.7rem 0 0;}
.plan-rail .pip{flex:1; height:.5rem; border-radius:2px; min-width:2px;}
.plan-rail .pip.today{outline:2px solid var(--ink); outline-offset:1px;}
.plan-rail .pip.past{opacity:.35;}
.plan-today{margin:.75rem 0 0; font-size:.92rem;}
.plan-link{margin:.5rem 0 0; font-size:.8rem; color:var(--muted);}
.plan-link a{color:var(--accent);}

/* --- 표 --- */
.scroller{overflow-x:auto; margin:.8rem 0; border:1px solid var(--line); border-radius:.5rem;}
/* 폰에서 칸이 눌려 글자 단위로 쪼개지지 않게: 내용 너비를 쓰고, 넘치면 표만 가로로 민다 */
table{border-collapse:collapse; width:max-content; min-width:100%;
      font-size:.86rem; font-variant-numeric:tabular-nums;}
th,td{max-width:15rem;}
th,td{padding:.5rem .65rem; text-align:left; vertical-align:top; border-bottom:1px solid var(--line-soft);}
th{background:var(--code); font-weight:700; white-space:nowrap;}
tbody tr:last-child td{border-bottom:0;}
td:first-child{white-space:nowrap;}

/* --- 수식 --- */
.math{
  font-family:"Cambria Math","STIX Two Math",Cambria,"Times New Roman",serif;
  font-variant-numeric:tabular-nums;
}
/* 본문 속 긴 식은 줄바꿈을 허용하고, 그래도 넘치면 그 식만 가로로 민다 */
.math-inline-block{display:inline-block; max-width:100%; overflow-x:auto; vertical-align:middle;}
.math i{font-style:italic;}
.math .txt{font-style:normal; font-family:inherit;}
.math .op{padding:0 .18em; font-style:normal;}
.math sub,.math sup{font-size:.68em;}
.sp-t{display:inline-block; width:.2em;}
.sp-q{display:inline-block; width:.9em;}
.sp-qq{display:inline-block; width:1.8em;}
.frac{display:inline-flex; flex-direction:column; vertical-align:middle; text-align:center;
      margin:0 .2em; font-size:.95em; line-height:1.3;}
.frac .num{border-bottom:1px solid currentColor; padding:0 .3em;}
.frac .den{padding:0 .3em;}
.sqrt{border-top:1px solid currentColor; padding:0 .15em;}
.sqrt::before{content:"√"; margin-left:-.15em; border-top:0;}
p.display{
  margin:.9rem 0; padding:.75rem .9rem; background:var(--code);
  border-left:3px solid var(--accent); border-radius:0 .4rem .4rem 0;
  overflow-x:auto; font-size:1.05rem; text-align:center;
}
p.display .math{white-space:nowrap;}
/* 넘쳐서 가로로 밀어야 하는 것은 오른쪽을 흐려 '더 있다'고 알린다 */
.is-scroll{
  -webkit-mask-image:linear-gradient(to right,#000 84%,transparent 100%);
  mask-image:linear-gradient(to right,#000 84%,transparent 100%);
}
p.display.is-scroll{text-align:left;}
@media (max-width:400px){ p.display{font-size:.98rem;} }

/* --- 맨 위로 --- */
.totop{
  position:fixed; right:1rem; bottom:1rem; z-index:40;
  border:1px solid var(--line); background:var(--surface); color:var(--muted);
  border-radius:999px; padding:.5rem .85rem; font:inherit; font-size:.8rem;
  font-weight:600; cursor:pointer; box-shadow:var(--shadow);
  opacity:0; pointer-events:none; transition:opacity .2s;
}
.totop.show{opacity:1; pointer-events:auto;}
.foot{margin:1.5rem 0 0; color:var(--muted); font-size:.78rem; text-align:center;}

@media (prefers-reduced-motion: reduce){
  *{transition:none !important; scroll-behavior:auto !important;}
}
html{scroll-behavior:smooth;}
"""

PLAN_WIDGET = """<div class="plan-widget">
  <div class="plan-top">
    <span class="plan-label">시험일 09/21까지</span>
    <span class="plan-dday" id="planDday">D-?</span>
  </div>
  <div class="plan-rail" id="planRail"></div>
  <p class="plan-today" id="planToday">불러오는 중…</p>
  <!-- PUBLIC-MIRROR:SKIP-START -->
  <p class="plan-link">날짜별 체크박스로 진행 상황을 기록하려면 —
    <a href="https://claude.ai/code/artifact/482687a7-2aee-46d5-bfc5-7b531237985f" target="_blank" rel="noopener">학습 로드맵 페이지</a>를 쓴다.</p>
  <!-- PUBLIC-MIRROR:SKIP-END -->
</div>
"""

PLAN_JS = """
/* 스티키 바(탭·칩)의 실제 높이를 재서 CSS 변수로 넣는다.
   고정값으로 두면 글꼴·화면폭·탭 개수가 바뀔 때마다 어긋난다 — 실제로
   탭을 5개로 늘리자 탭 라벨이 두 줄로 접히며 어긋났다. */
(function(){
  var root = document.documentElement;
  function measure(){
    var tabs = document.querySelector('.tabs');
    if(tabs) root.style.setProperty('--tabs-h', tabs.getBoundingClientRect().height + 'px');
    /* 칩 바는 패널마다 따로 있으니 지금 보이는 것을 잰다. */
    var chips = document.querySelector('.panel:not([hidden]) .chips');
    if(chips) root.style.setProperty('--chips-h', chips.getBoundingClientRect().height + 'px');
  }
  window.__measureSticky = measure;
  measure();
  window.addEventListener('resize', measure);
  window.addEventListener('orientationchange', measure);
  /* 웹폰트가 늦게 오면 높이가 달라진다 — 폰트 로딩 후 한 번 더 잰다. */
  if(document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
})();
(function(){
  var railEl = document.getElementById('planRail');
  if(!railEl) return;
  var PHASES = [
    {start:'2026-09-05', end:'2026-09-07', color:'--s1', title:'1단계 · 핵심 요약 강의 1회독'},
    {start:'2026-09-08', end:'2026-09-12', color:'--s2', title:'2단계 · 과년도 기출 5~7개년 1회독'},
    {start:'2026-09-13', end:'2026-09-17', color:'--s3', title:'3단계 · 기출 2회독 + 오답 집중 정리'},
    {start:'2026-09-18', end:'2026-09-20', color:'--pe', title:'4단계 · 실전 CBT 모의고사 반복'}
  ];
  var EXAM = '2026-09-21';
  function parseD(s){ var p = s.split('-').map(Number); return new Date(p[0], p[1]-1, p[2]); }
  var today = new Date(); today.setHours(0,0,0,0);
  var examDate = parseD(EXAM);

  var dday = Math.round((examDate - today) / 86400000);
  document.getElementById('planDday').textContent = dday > 0 ? ('D-' + dday) : (dday === 0 ? 'D-DAY' : '종료');

  PHASES.forEach(function(ph){
    var d = parseD(ph.start), end = parseD(ph.end);
    while(d <= end){
      var pip = document.createElement('div');
      pip.className = 'pip';
      pip.style.background = 'var(' + ph.color + ')';
      if(d.getTime() === today.getTime()) pip.classList.add('today');
      else if(d.getTime() < today.getTime()) pip.classList.add('past');
      railEl.appendChild(pip);
      d = new Date(d); d.setDate(d.getDate() + 1);
    }
  });
  var examPip = document.createElement('div');
  examPip.className = 'pip';
  examPip.style.background = 'repeating-linear-gradient(135deg, var(--pe) 0 3px, var(--danger) 3px 6px)';
  if(today.getTime() === examDate.getTime()) examPip.classList.add('today');
  else if(today.getTime() > examDate.getTime()) examPip.classList.add('past');
  railEl.appendChild(examPip);

  var currentPhase = null;
  for(var i=0; i<PHASES.length; i++){
    if(today >= parseD(PHASES[i].start) && today <= parseD(PHASES[i].end)){ currentPhase = PHASES[i]; break; }
  }
  var todayEl = document.getElementById('planToday');
  if(currentPhase){
    todayEl.innerHTML = '<strong>지금 — ' + currentPhase.title + '</strong>';
  } else if(today < parseD(PHASES[0].start)){
    todayEl.textContent = '1단계 시작까지 ' + Math.round((parseD(PHASES[0].start) - today) / 86400000) + '일 남음';
  } else if(today.getTime() === examDate.getTime()){
    todayEl.innerHTML = '<strong>오늘은 시험일이다.</strong>';
  } else if(today > examDate){
    todayEl.textContent = '시험이 끝났다. 수고했다.';
  }
})();
"""

QUIZ_CSS = """
/* ⚠ 이 한 줄이 없으면 hidden 이 duds 가 된다.
   .qask·.qcard 에 display:flex 를 주면 **작성자 스타일이 UA 의 [hidden]{display:none}
   을 이겨서**, hidden 을 걸어도 그대로 보인다. 실제로 문제풀이 탭에서
   "맞았다 — 찍었나?" 블록과 결과 카드가 항상 떠 있었다(2026-09-10 고침).
   본문 CSS 의 .panel[hidden] 도 같은 이유로 따로 적어 둔 것이다. */
.quiz [hidden]{display:none !important;}
/* --- 문제풀이 탭 --- */
.quiz{display:flex; flex-direction:column; gap:1rem; margin-top:.4rem;}
.quiz-note{margin:0; font-size:.86rem; color:var(--muted);}
.quiz-pick{display:flex; flex-wrap:wrap; gap:.4rem;}
.qpill{
  appearance:none; cursor:pointer; font:inherit; font-size:.86rem;
  padding:.4rem .8rem; border-radius:999px; border:1px solid var(--line);
  background:var(--surface); color:var(--muted);
}
.qpill[aria-pressed="true"]{border-color:var(--accent); color:var(--accent); font-weight:600;}
.qbar{height:4px; background:var(--line-soft); border-radius:999px; overflow:hidden;}
.qbar i{display:block; height:100%; width:0; background:var(--accent); transition:width .25s ease;}
.qmeta{display:flex; justify-content:space-between; font-size:.8rem; color:var(--muted);
  font-variant-numeric:tabular-nums; margin-top:.35rem;}
.qcard{
  background:var(--surface); border:1px solid var(--line); border-radius:12px;
  padding:1.05rem 1rem; box-shadow:var(--shadow); display:flex; flex-direction:column; gap:.85rem;
}
.qtag{font-size:.74rem; letter-spacing:.05em; color:var(--muted);}
.qtag b{color:var(--accent); font-weight:600;}
.qtext{margin:0; font-size:1.04rem; line-height:1.55; font-weight:600; text-wrap:balance;}
.qchoices{display:flex; flex-direction:column; gap:.45rem;}
.qchoice{
  display:flex; gap:.6rem; align-items:flex-start; text-align:left;
  appearance:none; cursor:pointer; font:inherit; width:100%; line-height:1.5;
  padding:.62rem .75rem; border-radius:9px; border:1px solid var(--line);
  background:var(--bg); color:var(--ink);
}
.qchoice:hover:not(:disabled){border-color:var(--accent);}
.qchoice:disabled{cursor:default;}
.qchoice .qn{
  flex:0 0 1.35rem; height:1.35rem; border-radius:50%; border:1px solid var(--line);
  display:grid; place-items:center; font-size:.76rem; color:var(--muted);
  font-variant-numeric:tabular-nums;
}
.qchoice.ok{border-color:var(--pe); background:var(--pe-bg);}
.qchoice.ok .qn{border-color:var(--pe); color:var(--pe); font-weight:700;}
.qchoice.bad{border-color:var(--danger);}
.qchoice.bad .qn{border-color:var(--danger); color:var(--danger); font-weight:700;}
/* 색만으로 알리지 않는다 — 색약이거나 흑백으로 인쇄해도 읽혀야 한다.
   글자 배지를 같이 붙인다. */
.qchoice .qflag{
  margin-left:auto; align-self:center; flex:0 0 auto;
  font-size:.7rem; font-weight:700; letter-spacing:.02em; white-space:nowrap;
  padding:.12rem .42rem; border-radius:.32rem;
}
.qchoice.ok .qflag{background:var(--pe); color:var(--bg);}
.qchoice.bad .qflag{background:var(--danger); color:var(--bg);}
/* 틀렸을 때 정답을 문장으로 한 번 더 못박는다 */
.qans{
  margin:0 0 .55rem; padding:.55rem .7rem; border-radius:.45rem;
  background:var(--pe-bg); border:1px solid var(--pe);
  font-size:.94rem; line-height:1.6;
}
.qans b{color:var(--pe);}
.qwhy-label{
  display:block; margin:.7rem 0 .25rem; font-size:.72rem; font-weight:700;
  letter-spacing:.09em; color:var(--muted);
}
/* 찍었는지 묻는 자리 — 4지선다는 25 % 가 운이라, 이걸 안 물으면 오답 목록이 거짓말을 한다 */
.qask{
  display:flex; flex-direction:column; gap:.55rem;
  border:1px dashed var(--accent); border-radius:10px; padding:.8rem .85rem;
}
.qask p{margin:0; font-size:.92rem; font-weight:600;}
.qask .qrow{display:flex; gap:.5rem; flex-wrap:wrap;}
.qverdict{display:flex; flex-direction:column; gap:.45rem;
  border-top:1px solid var(--line-soft); padding-top:.8rem;}
.qhead{font-weight:700; font-size:.95rem;}
.qhead.ok{color:var(--pe);} .qhead.bad{color:var(--danger);} .qhead.luck{color:var(--core);}
.qwhy{margin:0; font-size:.92rem;}
.qsrc{margin:0; font-size:.78rem; color:var(--muted);}
.qbtn{
  appearance:none; cursor:pointer; font:inherit; font-weight:600; font-size:.92rem;
  padding:.48rem 1rem; border-radius:8px;
  border:1px solid var(--accent); background:var(--accent); color:var(--surface);
}
.qbtn.ghost{background:var(--surface); color:var(--ink); border-color:var(--line);}
.qbtn:focus-visible, .qchoice:focus-visible, .qpill:focus-visible{
  outline:2px solid var(--accent); outline-offset:2px;
}
.qend{display:flex; gap:1.4rem; flex-wrap:wrap; align-items:baseline;}
.qend .big{font-size:1.9rem; font-weight:700; line-height:1; font-variant-numeric:tabular-nums;}
.qend .sub{font-size:.82rem; color:var(--muted);}
.qlist{display:flex; flex-direction:column; gap:.7rem; margin:0; padding:0; list-style:none;}
.qlist li{border-left:3px solid var(--danger); padding-left:.75rem;}
.qlist li.luck{border-left-color:var(--core);}
.qlist .lq{font-weight:600; font-size:.94rem;}
.qlist .la{font-size:.86rem; color:var(--muted);}
.qlist .la s{color:var(--danger);} .qlist .la b{color:var(--pe);}
.qrank{width:100%; border-collapse:collapse; font-size:.86rem;}
.qrank th,.qrank td{border-bottom:1px solid var(--line-soft); padding:.45rem .5rem; text-align:left;}
.qrank td.num{text-align:right; font-variant-numeric:tabular-nums;}
.qrank .star{color:var(--danger); font-weight:700;}
.qpaste{
  margin:0; background:var(--code); border:1px solid var(--line); border-radius:9px;
  padding:.8rem; overflow-x:auto; font-size:.8rem; line-height:1.65; white-space:pre-wrap;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
.qsync{font-size:.78rem; color:var(--muted);}
.qsync.ok{color:var(--pe);} .qsync.fail{color:var(--danger);}
"""

QUIZ_PANEL = """<div class="quiz">
  <p class="quiz-note">요약집에서 뽑은 <strong>기출 유형</strong>이다 — 실제 회차 문제를 그대로 옮긴 것이 아니다.
    <strong>30초 넘게 고민하지 말고</strong> 고르고 해설을 본다. 문제는 매번 섞여 나오고,
    <strong>틀렸거나 찍은 문제가 더 자주</strong> 나온다.</p>
  <div class="quiz-pick" id="qPick" role="group" aria-label="과목 고르기"></div>
  <div>
    <div class="qbar"><i id="qFill"></i></div>
    <div class="qmeta"><span id="qPos">—</span><span id="qTally">—</span></div>
  </div>
  <section class="qcard" id="qCard">
    <div class="qtag" id="qTag"></div>
    <p class="qtext" id="qQ"></p>
    <div class="qchoices" id="qChoices"></div>

    <div class="qask" id="qAsk" hidden>
      <p>맞았다. <strong>확실히 알고 골랐나, 찍었나?</strong></p>
      <div class="qrow">
        <button class="qbtn" type="button" id="qKnew">알고 맞혔다</button>
        <button class="qbtn ghost" type="button" id="qLuck">찍어서 맞혔다</button>
      </div>
      <p class="qsrc">찍어서 맞힌 것은 <strong>모르는 것</strong>으로 친다 — 4지선다는 25 % 가 운이라,
        이걸 걸러내지 않으면 오답 목록이 거짓말을 한다.</p>
    </div>

    <div class="qverdict" id="qVerdict" hidden>
      <div class="qhead" id="qHead"></div>
      <p class="qans" id="qAns" hidden></p>
      <span class="qwhy-label" id="qWhyLabel">해설</span>
      <p class="qwhy" id="qWhy"></p>
      <p class="qsrc" id="qSrc"></p>
      <button class="qbtn" type="button" id="qNext">다음 문제</button>
    </div>
  </section>

  <section class="qcard" id="qEnd" hidden>
    <div class="qend">
      <div><div class="big" id="qSureN">—</div><div class="sub">알고 맞힘</div></div>
      <div><div class="big" id="qLuckN">—</div><div class="sub">찍어서 맞힘</div></div>
      <div><div class="big" id="qWrongN">—</div><div class="sub">틀림</div></div>
    </div>
    <div id="qEndBody"></div>
    <div class="qrow" style="display:flex; gap:.5rem; flex-wrap:wrap;">
      <button class="qbtn ghost" type="button" id="qCopy">오답 복사</button>
      <button class="qbtn ghost" type="button" id="qRetry">모르는 것만 다시</button>
      <button class="qbtn ghost" type="button" id="qReset">처음부터</button>
    </div>
    <div class="qsync" id="qSync"></div>
  </section>

  <section class="qcard">
    <div class="qtag"><b>누적</b> · 자주 틀리는 것 (이 기기에 쌓인 기록)</div>
    <div id="qRankBody"><p class="quiz-note">아직 푼 기록이 없다.</p></div>
    <div class="qrow" style="display:flex; gap:.5rem; flex-wrap:wrap;">
      <button class="qbtn ghost" type="button" id="qRankCopy">누적 오답 복사</button>
      <button class="qbtn ghost" type="button" id="qRankClear">기록 지우기</button>
    </div>
  </section>
</div>
"""

JS = """
(function(){
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.panel'));
  function select(idx, focus){
    tabs.forEach(function(t,i){
      t.setAttribute('aria-selected', i===idx ? 'true':'false');
      t.tabIndex = i===idx ? 0 : -1;
    });
    panels.forEach(function(p,i){ p.hidden = i!==idx; });
    document.documentElement.style.setProperty('--accent', tabs[idx].dataset.accent);
    if(window.__measureSticky) window.__measureSticky();
    if(focus) tabs[idx].focus();
    try{ localStorage.setItem('haengdo.craft.tab', String(idx)); }catch(e){}
  }
  tabs.forEach(function(t,i){
    t.addEventListener('click', function(){ select(i); window.scrollTo({top:0}); });
    t.addEventListener('keydown', function(e){
      if(e.key==='ArrowRight'||e.key==='ArrowLeft'){
        e.preventDefault();
        select((i + (e.key==='ArrowRight'?1:tabs.length-1)) % tabs.length, true);
      }
    });
  });
  var saved = 0;
  try{ saved = parseInt(localStorage.getItem('haengdo.craft.tab')||'0',10) || 0; }catch(e){}
  select(saved >= 0 && saved < tabs.length ? saved : 0);

  function markScroll(){
    document.querySelectorAll('p.display, .scroller').forEach(function(el){
      el.classList.toggle('is-scroll', el.scrollWidth - el.clientWidth > 4 && el.scrollLeft < 4);
    });
  }
  markScroll();
  window.addEventListener('resize', markScroll, {passive:true});
  document.addEventListener('scroll', function(e){
    var el = e.target;
    if(el.classList && (el.classList.contains('display') || el.classList.contains('scroller'))){
      el.classList.toggle('is-scroll', el.scrollWidth - el.clientWidth - el.scrollLeft > 4);
    }
  }, true);
  tabs.forEach(function(t){ t.addEventListener('click', function(){ setTimeout(markScroll, 0); }); });

  var top = document.querySelector('.totop');
  window.addEventListener('scroll', function(){
    top.classList.toggle('show', window.scrollY > 600);
  }, {passive:true});
  top.addEventListener('click', function(){ window.scrollTo({top:0, behavior:'smooth'}); });
})();
"""


QUIZ_JS = """
(function(){
  var BANK = __BANK__;
  if(!BANK.length) return;

  var LS = 'haengdo.craft.quiz.v1';   /* 이 기기 */
  var DOC = 'craftsman/wrong-answers'; /* 브라우저 바깥(아티팩트 db) */

  /* stats[id] = {seen, wrong, luck} — 틀린 횟수와 '찍어서 맞힌' 횟수를 따로 센다.
     찍어서 맞힌 것을 정답으로 치면 누적 기록이 거짓말을 하기 때문이다. */
  var stats = {};
  try{ stats = JSON.parse(localStorage.getItem(LS) || '{}') || {}; }catch(e){ stats = {}; }

  var byId = {};
  BANK.forEach(function(q){ byId[q.id] = q; });

  var S = {subj:'전체', order:[], at:0, sure:0, luck:0, wrong:[], picked:-1, phase:'ask'};
  function $(id){ return document.getElementById(id); }

  /* --- 출제 순서. 틀렸거나 찍은 문제에 가중치를 줘 더 자주 나오게 한다. --- */
  function weightOf(q){
    var st = stats[q.id] || {};
    return 1 + (st.wrong || 0) * 2 + (st.luck || 0);
  }
  function drawOrder(pool){
    var bag = [];
    pool.forEach(function(q){
      var w = Math.min(weightOf(q), 6);
      for(var i=0;i<w;i++) bag.push(q);
    });
    var out = [], seen = {};
    while(bag.length && out.length < pool.length){
      var i = Math.floor(Math.random()*bag.length);
      var q = bag[i];
      bag.splice(i,1);
      if(seen[q.id]) continue;
      seen[q.id] = 1; out.push(q);
    }
    pool.forEach(function(q){ if(!seen[q.id]) out.push(q); });
    return out;
  }

  /* --- 과목 고르기 --- */
  ['전체','이론','기기','설비'].forEach(function(name){
    var n = name === '전체' ? BANK.length : BANK.filter(function(x){return x.s===name;}).length;
    var b = document.createElement('button');
    b.type='button'; b.className='qpill'; b.dataset.subj=name;
    b.setAttribute('aria-pressed', name==='전체' ? 'true':'false');
    b.textContent = name + ' ' + n;
    b.addEventListener('click', function(){ start(name); });
    $('qPick').appendChild(b);
  });

  function start(subj, only){
    S.subj = subj;
    var pool = only || BANK.filter(function(x){ return subj==='전체' || x.s===subj; });
    S.order = drawOrder(pool);
    S.at=0; S.sure=0; S.luck=0; S.wrong=[]; S.picked=-1;
    Array.prototype.forEach.call($('qPick').children, function(b){
      b.setAttribute('aria-pressed', String(b.dataset.subj===subj));
    });
    $('qEnd').hidden = true; $('qCard').hidden = false;
    render();
  }

  function render(){
    if(S.at >= S.order.length){ finish(); return; }
    var it = S.order[S.at];
    S.picked = -1;
    $('qTag').innerHTML = '<b>' + it.s + '</b> · ' + it.u;
    $('qQ').textContent = it.q;
    $('qAsk').hidden = true;
    $('qVerdict').hidden = true;

    var box = $('qChoices'); box.textContent='';
    it.c.forEach(function(text, i){
      var b = document.createElement('button');
      b.type='button'; b.className='qchoice';
      var n = document.createElement('span'); n.className='qn'; n.textContent=String(i+1);
      var t = document.createElement('span'); t.textContent=text;
      b.appendChild(n); b.appendChild(t);
      b.addEventListener('click', function(){ pick(i); });
      box.appendChild(b);
    });
    $('qPos').textContent = (S.at+1) + ' / ' + S.order.length + ' 문항';
    tally();
    $('qFill').style.width = (S.at / S.order.length * 100) + '%';
  }

  function tally(){
    $('qTally').textContent = '알고 ' + S.sure + ' · 찍음 ' + S.luck + ' · 틀림 ' + S.wrong.length;
  }

  function bump(id, key){
    var st = stats[id] || {seen:0, wrong:0, luck:0};
    st.seen = (st.seen||0) + 1;
    if(key) st[key] = (st[key]||0) + 1;
    stats[id] = st;
    try{ localStorage.setItem(LS, JSON.stringify(stats)); }catch(e){}
  }

  function pick(i){
    if(S.picked >= 0) return;
    S.picked = i;
    var it = S.order[S.at];
    Array.prototype.forEach.call($('qChoices').children, function(b, idx){
      b.disabled = true;
      var flag = null;
      if(idx === it.a){ b.classList.add('ok'); flag = (idx === i) ? '정답 · 내 답' : '정답'; }
      else if(idx === i){ b.classList.add('bad'); flag = '내 답'; }
      if(flag){
        var f = document.createElement('span');
        f.className = 'qflag'; f.textContent = flag;
        b.appendChild(f);
      }
    });
    if(i === it.a){
      /* 맞았어도 바로 넘어가지 않는다 — 찍었는지 먼저 묻는다. */
      $('qAsk').hidden = false;
      $('qKnew').focus();
    } else {
      bump(it.id, 'wrong');
      S.wrong.push({id:it.id, kind:'wrong', q:it.q, picked:it.c[i], answer:it.c[it.a], src:it.src});
      reveal('bad', '틀렸다');
    }
  }

  $('qKnew').addEventListener('click', function(){
    if($('qAsk').hidden) return;   /* 숨어 있는 버튼이 눌려 두 번 세는 것을 막는다 */
    var it = S.order[S.at];
    bump(it.id, null);
    S.sure++;
    $('qAsk').hidden = true;
    reveal('ok', '맞았다 — 알고 맞힌 것으로 기록했다');
  });

  $('qLuck').addEventListener('click', function(){
    if($('qAsk').hidden) return;   /* 위와 같은 이유 */
    var it = S.order[S.at];
    bump(it.id, 'luck');
    S.luck++;
    S.wrong.push({id:it.id, kind:'luck', q:it.q, picked:'(찍어서 맞힘)', answer:it.c[it.a], src:it.src});
    $('qAsk').hidden = true;
    reveal('luck', '찍어서 맞혔다 — 모르는 것으로 쌓았다');
  });

  /* src("Machinery_Summary.md §3 등가회로 시험") → 그 단원 카드로 가는 앵커.
     과목마다 탭이 다르고 카드 id 가 k<탭>-s<절> 이라 기계적으로 만들 수 있다. */
  var SUBJ_TAB = {'이론':1, '기기':2, '설비':3};
  function srcLink(it){
    var m = /§ *([0-9]+)/.exec(it.src || '');
    var t = SUBJ_TAB[it.s];
    if(!m || !t) return null;
    return {tab:t, id:'k' + t + '-s' + m[1]};
  }

  function reveal(kind, head){
    var it = S.order[S.at];
    $('qHead').className = 'qhead ' + kind;
    $('qHead').textContent = head;

    /* 틀렸거나 찍었으면 정답을 문장으로 한 번 더 말해준다.
       보기 색만으로는 무엇이 답인지 확실히 안 남는다. */
    var ans = $('qAns');
    if(kind === 'bad' || kind === 'luck'){
      ans.hidden = false;
      ans.textContent = '';
      var lead = document.createElement('b');
      lead.textContent = '정답 ' + '①②③④'.charAt(it.a) + '  ';
      ans.appendChild(lead);
      ans.appendChild(document.createTextNode(it.c[it.a]));
    } else {
      ans.hidden = true;
    }

    $('qWhy').textContent = it.why;

    /* '다시 볼 곳' 을 눌러서 갈 수 있게 만든다 — 틀린 자리를 바로 펴 보는 것이
       오답 학습의 전부다. 앵커를 못 만들면 예전처럼 글씨로만 둔다. */
    var link = srcLink(it);
    if(link){
      $('qSrc').textContent = '다시 볼 곳 — ';
      var a = document.createElement('a');
      a.href = '#' + link.id;
      a.textContent = it.src;
      a.addEventListener('click', function(ev){
        ev.preventDefault();
        var tabBtn = document.getElementById('tab' + link.tab);
        if(tabBtn) tabBtn.click();
        var target = document.getElementById(link.id);
        /* 탭 버튼 클릭은 window.scrollTo({top:0}) 를 부른다(위 tabs 처리).
           그래서 한 번만 스크롤하면 맨 위로 되돌려진 자리에서 끝난다 —
           레이아웃이 잡힌 뒤 한 번 더 맞춘다. 카드의 scroll-margin-top 이
           스티키 탭·칩 높이만큼 띄워 준다. */
        var jump = function(){
          var el = document.getElementById(link.id);
          if(el) el.scrollIntoView({block:'start'});
        };
        setTimeout(function(){
          jump();
          requestAnimationFrame(function(){ requestAnimationFrame(jump); });
        }, 120);
      });
      $('qSrc').appendChild(a);
    } else {
      $('qSrc').innerHTML = '다시 볼 곳 — <code>' + it.src + '</code>';
    }
    $('qVerdict').hidden = false;
    $('qNext').textContent = (S.at+1 >= S.order.length) ? '결과 보기' : '다음 문제';
    $('qNext').focus();
    tally();
    drawRank();
  }

  $('qNext').addEventListener('click', function(){ S.at++; render(); });

  function pasteOf(list){
    return list.map(function(w,i){
      return (i+1) + '. ' + w.q +
        '\\n   → 내가 쓴 답: ' + w.picked +
        '\\n   → 정답: ' + w.answer + '  (' + w.src + ')';
    }).join('\\n');
  }

  function finish(){
    $('qCard').hidden = true; $('qEnd').hidden = false;
    $('qFill').style.width = '100%';
    $('qPos').textContent = S.order.length + ' 문항 끝';
    $('qSureN').textContent = S.sure;
    $('qLuckN').textContent = S.luck;
    $('qWrongN').textContent = S.wrong.filter(function(w){return w.kind==='wrong';}).length;

    var body = $('qEndBody'); body.textContent='';
    if(!S.wrong.length){
      var p = document.createElement('p'); p.className='quiz-note';
      p.textContent = '모르는 것이 없다. 다른 과목으로 넘어가거나 전체로 돌린다.';
      body.appendChild(p);
    } else {
      var ul = document.createElement('ul'); ul.className='qlist';
      S.wrong.forEach(function(w){
        var li = document.createElement('li');
        if(w.kind==='luck') li.className='luck';
        var q = document.createElement('div'); q.className='lq'; q.textContent=w.q;
        var a = document.createElement('div'); a.className='la';
        a.innerHTML = '내가 쓴 답 <s></s> → 정답 <b></b>';
        a.querySelector('s').textContent = w.picked;
        a.querySelector('b').textContent = w.answer;
        var s = document.createElement('div'); s.className='qsrc'; s.textContent=w.src;
        li.appendChild(q); li.appendChild(a); li.appendChild(s);
        ul.appendChild(li);
      });
      body.appendChild(ul);
      var pre = document.createElement('pre'); pre.className='qpaste';
      pre.textContent = pasteOf(S.wrong);
      body.appendChild(pre);
    }
    drawRank();
    push();
  }

  /* --- 누적: 자주 틀리는 것 --- */
  function ranked(){
    return Object.keys(stats).map(function(id){
      var st = stats[id]; var q = byId[id];
      if(!q) return null;
      return {q:q, miss:(st.wrong||0) + (st.luck||0), wrong:st.wrong||0, luck:st.luck||0, seen:st.seen||0};
    }).filter(function(r){ return r && r.miss > 0; })
      .sort(function(a,b){ return b.miss - a.miss || b.wrong - a.wrong; });
  }

  function drawRank(){
    var rows = ranked();
    var box = $('qRankBody'); box.textContent='';
    if(!rows.length){
      var p = document.createElement('p'); p.className='quiz-note';
      p.textContent = '아직 틀리거나 찍은 문제가 없다.';
      box.appendChild(p);
      return;
    }
    var wrap = document.createElement('div'); wrap.className='scroller';
    var t = document.createElement('table'); t.className='qrank';
    t.innerHTML = '<thead><tr><th>문제</th><th>과목</th><th>틀림</th><th>찍음</th></tr></thead>';
    var tb = document.createElement('tbody');
    rows.slice(0, 30).forEach(function(r){
      var tr = document.createElement('tr');
      var star = r.miss >= 3 ? '★ ' : '';
      var c1 = document.createElement('td');
      c1.innerHTML = '<span class="star"></span>';
      c1.querySelector('.star').textContent = star;
      c1.appendChild(document.createTextNode(r.q.q));
      var c2 = document.createElement('td'); c2.textContent = r.q.s + ' ' + r.q.u;
      var c3 = document.createElement('td'); c3.className='num'; c3.textContent=r.wrong;
      var c4 = document.createElement('td'); c4.className='num'; c4.textContent=r.luck;
      tr.appendChild(c1); tr.appendChild(c2); tr.appendChild(c3); tr.appendChild(c4);
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
    var n = document.createElement('p'); n.className='quiz-note';
    n.textContent = '★ 는 세 번 이상 틀렸거나 찍은 것이다. 시험 직전에는 이 줄들만 본다.';
    box.appendChild(n);
  }

  $('qRankCopy').addEventListener('click', function(){
    var rows = ranked();
    if(!rows.length) return;
    var text = rows.map(function(r,i){
      return (i+1) + '. ' + (r.miss>=3 ? '★ ' : '') + r.q.q +
        '\\n   → 정답: ' + r.q.c[r.q.a] +
        '\\n   → 틀림 ' + r.wrong + '회 · 찍음 ' + r.luck + '회  (' + r.q.src + ')';
    }).join('\\n');
    copy(text, $('qRankCopy'));
  });

  $('qRankClear').addEventListener('click', function(){
    if(!window.confirm('이 기기에 쌓인 문제풀이 기록을 지운다. 되돌릴 수 없다.')) return;
    stats = {};
    try{ localStorage.removeItem(LS); }catch(e){}
    drawRank(); push();
  });

  $('qCopy').addEventListener('click', function(){ copy(pasteOf(S.wrong), $('qCopy')); });

  function copy(text, btn){
    if(!text) return;
    var was = btn.textContent;
    if(navigator.clipboard){
      navigator.clipboard.writeText(text).then(
        function(){ btn.textContent='복사했다'; setTimeout(function(){btn.textContent=was;},1800); },
        function(){ btn.textContent='복사 실패 — 화면의 글을 직접 선택'; });
    } else { btn.textContent='화면의 글을 직접 선택'; }
  }

  /* --- 브라우저 바깥에도 남긴다(아티팩트에서 열었을 때만) --- */
  var cloud = null;
  function mark(kind){
    var n = $('qSync');
    var say = {off:'이 기기에만 저장됩니다', wait:'저장하는 중…',
               ok:'저장했습니다 — 다른 기기에서도 이어집니다',
               fail:'바깥에 못 올렸습니다 — 이 기기에는 남아 있습니다'};
    n.className = 'qsync ' + kind;
    n.textContent = say[kind] || '';
  }
  function push(){
    if(!cloud) return;
    mark('wait');
    cloud.doc(DOC).set({
      stats: stats,
      last: {subj:S.subj, sure:S.sure, luck:S.luck, wrong:S.wrong},
      updatedAt: new Date().toISOString()
    }).then(function(){ mark('ok'); }, function(){ mark('fail'); });
  }
  if(window.claude && window.claude.use){
    window.claude.use('db').then(function(db){
      if(!db){ mark('off'); return; }
      cloud = db; mark('');
    }, function(){ mark('off'); });
  } else { mark('off'); }

  start('전체');
  drawRank();
})();
"""


def build() -> str:
    tabs, panels = [], []

    # 0번째 탭 — 핵심. 처음 공부하는 사람이 열자마자 보는 자리라 맨 앞에 둔다.
    # (요약집 3과목이 A4 50쪽이 넘어 "어디부터"가 첫 관문이다.)
    core_md = (HERE / "핵심.md").read_text(encoding="utf-8")
    core_body, core_toc, _ = render(core_md, "k0")
    core_chips = "".join(f'<a class="chip" href="#{a}">{html.escape(t)}</a>' for a, t in core_toc)
    tabs.append(
        '<button class="tab" role="tab" id="tab0" aria-controls="panel0" '
        'aria-selected="false" tabindex="-1" style="--tab:var(--core)" '
        'data-accent="var(--core)"><small>먼저</small>핵심</button>'
    )
    panels.append(
        '<div class="panel" id="panel0" role="tabpanel" aria-labelledby="tab0" hidden>'
        f'<nav class="chips" aria-label="핵심 섹션">{core_chips}</nav>{core_body}</div>'
    )
    for n, (fname, name, no, light, dark) in enumerate(SUBJECTS, start=1):
        md = (HERE / fname).read_text(encoding="utf-8")
        body, toc, _ = render(md, f"k{n}")
        var = f"--s{n}"
        tabs.append(
            f'<button class="tab" role="tab" id="tab{n}" aria-controls="panel{n}" '
            f'aria-selected="false" tabindex="-1" style="--tab:var({var})" '
            f'data-accent="var({var})"><small>{no}</small>{name}</button>'
        )
        chips = "".join(f'<a class="chip" href="#{a}">{html.escape(t)}</a>' for a, t in toc)
        panels.append(
            f'<div class="panel" id="panel{n}" role="tabpanel" aria-labelledby="tab{n}" hidden>'
            f'<nav class="chips" aria-label="{name} 섹션">{chips}</nav>{body}</div>'
        )

    # 4번째 탭 — 학습계획 (마크다운 그대로 렌더링 + 오늘 날짜 기준 D-day 위젯)
    plan_md = (HERE / "학습계획.md").read_text(encoding="utf-8")
    plan_body, plan_toc, _ = render(plan_md, "k4")
    plan_chips = "".join(f'<a class="chip" href="#{a}">{html.escape(t)}</a>' for a, t in plan_toc)
    tabs.append(
        '<button class="tab" role="tab" id="tab4" aria-controls="panel4" '
        'aria-selected="false" tabindex="-1" style="--tab:var(--pe)" '
        'data-accent="var(--pe)"><small>D-16</small>학습계획</button>'
    )
    panels.append(
        '<div class="panel" id="panel4" role="tabpanel" aria-labelledby="tab4" hidden>'
        f'<nav class="chips" aria-label="학습계획 섹션">{plan_chips}</nav>'
        f'{PLAN_WIDGET}{plan_body}</div>'
    )

    # 5번째 탭 — 공식 사용처 (과목을 가로지르는 색인. 위젯 없이 마크다운 그대로)
    fx_md = (HERE / "공식_사용처.md").read_text(encoding="utf-8")
    fx_body, fx_toc, _ = render(fx_md, "k5")
    fx_chips = "".join(f'<a class="chip" href="#{a}">{html.escape(t)}</a>' for a, t in fx_toc)
    tabs.append(
        '<button class="tab" role="tab" id="tab5" aria-controls="panel5" '
        'aria-selected="false" tabindex="-1" style="--tab:var(--fx)" '
        'data-accent="var(--fx)"><small>색인</small>공식 사용처</button>'
    )
    panels.append(
        '<div class="panel" id="panel5" role="tabpanel" aria-labelledby="tab5" hidden>'
        f'<nav class="chips" aria-label="공식 사용처 섹션">{fx_chips}</nav>{fx_body}</div>'
    )

    # 6번째 탭 — 문제풀이. 원본은 문제은행.json 이고, 여기서 화면을 만든다.
    # 요약집과 같은 파일에서 나오므로 둘이 갈라질 수 없다(따로 두었다가 갈라진 적이 있다).
    bank = json.loads((HERE / "문제은행.json").read_text(encoding="utf-8"))["문항"]
    _seen_ids: set[str] = set()
    for item in bank:
        if item["id"] in _seen_ids:
            DUP_IDS.append(item["id"])
        _seen_ids.add(item["id"])
        if not (0 <= item["a"] < len(item["c"])):
            BAD_ANSWER.append(f'{item["id"]} — 정답 번호 {item["a"]} 가 보기 {len(item["c"])}개를 벗어난다')
        if len(item["c"]) != 4:
            BAD_ANSWER.append(f'{item["id"]} — 보기가 4개가 아니라 {len(item["c"])}개다')
    tabs.append(
        '<button class="tab" role="tab" id="tab6" aria-controls="panel6" '
        'aria-selected="false" tabindex="-1" style="--tab:var(--danger)" '
        f'data-accent="var(--danger)"><small>{len(bank)}문항</small>문제풀이</button>'
    )
    panels.append(
        '<div class="panel" id="panel6" role="tabpanel" aria-labelledby="tab6" hidden>'
        f'{QUIZ_PANEL}</div>'
    )

    legend = '<span><i style="background:var(--core)"></i>핵심</span>' + "".join(
        f'<span><i style="background:var(--s{n})"></i>{name}</span>'
        for n, (_, name, _, _, _) in enumerate(SUBJECTS, start=1)
    )
    legend += '<span><i style="background:var(--pe)"></i>학습계획</span>'
    legend += '<span><i style="background:var(--fx)"></i>공식 사용처</span>'
    legend += '<span><i style="background:var(--danger)"></i>문제풀이</span>'

    quiz_js = QUIZ_JS.replace(
        "__BANK__",
        json.dumps(bank, ensure_ascii=False, separators=(",", ":")),
    )
    # ⚠ QUIZ_CSS 를 빠뜨려 문제풀이 탭이 **스타일 없는 맨 버튼**으로 나왔다.
    #   정답에 .ok 클래스는 붙는데 CSS 가 없어 초록 표시가 안 보였다 —
    #   "정답을 안 알려준다" 로 보였던 것의 진짜 원인이다. (2026-09-10 고침)
    head = f"<title>전기기능사 필기 요약집</title>\n<style>{CSS}{QUIZ_CSS}</style>"
    body = f"""<div class="wrap">
  <header class="masthead">
    <p class="eyebrow">Haengdo Brain · 전기기능사 학습실</p>
    <h1>전기기능사 필기 요약집</h1>
    <p>시험 직전에 폰으로 훑어보는 판. 탭을 눌러 옮겨 다니고, 칩을 눌러 단원으로 바로 간다.
       각 과목 맨 끝에 <strong>시험 직전 30초 체크리스트</strong>가 있다.</p>
    <p class="legend">{legend}
      <span>탭 색은 KEC 전선 식별 색상(갈·회·청·녹·흑)에서 가져왔다</span>
    </p>
  </header>
  <div class="tabs" role="tablist" aria-label="과목">{''.join(tabs)}</div>
  {''.join(panels)}
  <p class="foot">원본은 저장소의 마크다운 6개다. 이 페이지는 <code>build_summaries.py</code> 가 만든다 —
     내용을 고칠 때는 마크다운을 고치고 다시 돌린다.</p>
</div>
<button class="totop" type="button">맨 위로</button>
<script>{JS}{PLAN_JS}{quiz_js}</script>
"""
    return head, body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", metavar="경로", help="아티팩트용 본문만 이 경로에 쓴다")
    args = ap.parse_args()

    head, body = build()
    if args.artifact:
        # 아티팩트는 doctype/head/body 를 알아서 씌운다 — 본문만 넘긴다.
        out = Path(args.artifact)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{head}\n{body}", encoding="utf-8")
    else:
        out = HERE / "summaries.html"
        out.write_text(
            '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n',
            encoding="utf-8",
        )
    print(f"만듦: {out} ({out.stat().st_size:,} 바이트)")

    if DUP_IDS:
        print()
        print(f"⚠ 문제은행에 id 가 겹치는 문항 {len(DUP_IDS)}개 — 누적 기록이 엉킨다.")
        for i in DUP_IDS:
            print(f"    {i}")

    if BAD_ANSWER:
        print()
        print(f"⚠ 문제은행 오류 {len(BAD_ANSWER)}건 — 정답을 못 고르거나 보기 수가 안 맞는다.")
        for m in BAD_ANSWER:
            print(f"    {m}")

    if STRAY_BOLD:
        print(f"\n⚠ 굵게(**)가 닫히지 않은 곳 {len(STRAY_BOLD)}군데 — 화면에 별표가 그대로 보인다.")
        for line in STRAY_BOLD:
            print(f"    {line}")
        print("  인용문·목록은 줄마다 따로 처리한다. **굵게**를 한 줄 안에서 닫을 것.")

    if UNKNOWN:
        names = ", ".join("\\" + n for n in sorted(UNKNOWN))
        print(
            f"\n⚠ 모르는 수식 명령 {len(UNKNOWN)}개: {names}\n"
            f"  이 기호들은 화면에서 사라진다. 공식에서 변수가 빠지면 틀린 공식보다 나쁘다.\n"
            f"  build_summaries.py 의 SYMBOLS 에 추가하고 다시 돌릴 것."
        )


if __name__ == "__main__":
    main()
