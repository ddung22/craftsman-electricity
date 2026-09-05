"""요약집 마크다운 3개 + 학습계획을 한 페이지 HTML 로 묶는다.

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
import re
from pathlib import Path

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
    "propto": "∝", "iff": "⟺", "implies": "⟹", "Rightarrow": "⇒",
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
            elif name == "text":
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
                out.append(f"<h3>{inline(text)}</h3>")
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
  --s1:#8A5533; --s2:#4F6572; --s3:#1F5FA8; --danger:#B84B2A;
  --shadow:0 1px 2px rgba(27,30,36,.05), 0 8px 24px -16px rgba(27,30,36,.28);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#14171C; --surface:#1B1F26; --ink:#E7E4DE; --muted:#9AA3AF;
    --line:#2C323B; --line-soft:#232830; --code:#232830;
    --accent:#7FAEE8; --pe:#A9C46C; --pe-bg:#1F2519;
    --s1:#D0996B; --s2:#9DB6C4; --s3:#7FAEE8; --danger:#E38564;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"]{
  --bg:#14171C; --surface:#1B1F26; --ink:#E7E4DE; --muted:#9AA3AF;
  --line:#2C323B; --line-soft:#232830; --code:#232830;
  --accent:#7FAEE8; --pe:#A9C46C; --pe-bg:#1F2519;
  --s1:#D0996B; --s2:#9DB6C4; --s3:#7FAEE8; --danger:#E38564;
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
  position:sticky; top:3.55rem; z-index:20; background:var(--bg);
  display:flex; gap:.35rem; overflow-x:auto; padding:.55rem 0;
  border-bottom:1px solid var(--line-soft); scrollbar-width:none;
}
.chips::-webkit-scrollbar{display:none;}
.chip{
  flex:0 0 auto; text-decoration:none; white-space:nowrap;
  font-size:.78rem; font-weight:600; color:var(--muted);
  border:1px solid var(--line); border-radius:999px; padding:.28rem .7rem;
  background:var(--surface);
}
.chip:hover{color:var(--accent); border-color:var(--accent);}

/* --- 본문 --- */
.panel{padding-top:1.25rem;}
.panel > p{margin:1.1rem 0 1.35rem; color:var(--muted); font-size:.92rem;}
.panel[hidden]{display:none;}
.card{
  background:var(--surface); border:1px solid var(--line); border-radius:.75rem;
  padding:1.1rem 1.1rem 1.25rem; margin:0 0 1rem; box-shadow:var(--shadow);
  scroll-margin-top:7rem;
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
</div>
"""

PLAN_JS = """
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


def build() -> str:
    tabs, panels = [], []
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

    legend = "".join(
        f'<span><i style="background:var(--s{n})"></i>{name}</span>'
        for n, (_, name, _, _, _) in enumerate(SUBJECTS, start=1)
    )
    legend += '<span><i style="background:var(--pe)"></i>학습계획</span>'

    head = f"<title>전기기능사 필기 요약집</title>\n<style>{CSS}</style>"
    body = f"""<div class="wrap">
  <header class="masthead">
    <p class="eyebrow">Haengdo Brain · 전기기능사 학습실</p>
    <h1>전기기능사 필기 요약집</h1>
    <p>시험 직전에 폰으로 훑어보는 판. 탭을 눌러 옮겨 다니고, 칩을 눌러 단원으로 바로 간다.
       각 과목 맨 끝에 <strong>시험 직전 30초 체크리스트</strong>가 있다.</p>
    <p class="legend">{legend}
      <span>탭 색은 KEC 전선 식별 색상(갈·회·청·녹)에서 가져왔다</span>
    </p>
  </header>
  <div class="tabs" role="tablist" aria-label="과목">{''.join(tabs)}</div>
  {''.join(panels)}
  <p class="foot">원본은 저장소의 마크다운 4개다. 이 페이지는 <code>build_summaries.py</code> 가 만든다 —
     내용을 고칠 때는 마크다운을 고치고 다시 돌린다.</p>
</div>
<button class="totop" type="button">맨 위로</button>
<script>{JS}{PLAN_JS}</script>
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
