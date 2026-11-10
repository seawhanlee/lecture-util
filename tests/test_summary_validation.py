import pytest

from lecture_util.summary_validation import validate_summary


@pytest.mark.parametrize("content", [
    "# 제목\n\n## 소제목\n\n일반 본문.",
    "> [!abstract]+ 요약\n> 설명\n>\n> > [!custom]- 접기\n> > ==강조==",
    "[[#한글 제목|별칭]] [[다른 노트#^block]] ![[그림.png|300]] [[아직 없는 노트]]",
    "[[#$E = mc^2$|에너지]] [[#`기호`|코드 제목]] [[수식 $단위]]",
    "| 개념 | 링크 |\n| --- | :---: |\n| 질량 | [[#질량\\|보기]] |",
    "개념 | 링크\n--- | ---\n질량 | ![[그림.png\\|300]]",
    "> | 개념 | 링크 |\n> | --- | --- |\n> | 질량 | [[#질량\\|보기]] |",
    "$x_i = 1$\n\n$$\nA = [[1, 2], [3, 4]]\n$$",
    "$$A = [[1, 2], [3, 4]]$$",
    "인라인 `$x [[` 와 `` ` [[ `` 예제",
    "여러 줄 `코드\n[[닫지 않은 예제\n` 끝",
    "```markdown\n[[닫지 않은 예제\n\n$$\n```",
    "~~~~markdown\n```\n[[예제\n~~~~",
    "> [!example]\n> ```markdown\n> [[예제\n>\n> ```",
    "    [[들여쓴 코드 예제\n\n\t$$",
    "- 예제\n\n    ```markdown\n    [[예제\n    ```",
    "%% [[숨긴 주석\n$$\n```\n%%\n본문",
    "<!-- [[숨긴 HTML 주석\n$$\n```\n-->\n본문",
    '---\ntitle: "[[예제"\n---\n# 제목',
    "---\n일반 본문",
    r"\[[문자 그대로 \$$",
    "가격 $10, $20 또는 단독 $ 기호",
    "`짝 없는 백틱은 일반 문자",
])
def test_valid_ofm_and_literal_examples(content):
    assert validate_summary(content) == []


@pytest.mark.parametrize(("content", "expected"), [
    (" \n\t", [("empty_summary", 1)]),
    ("본문\n```python\npass", [("unclosed_fence", 2)]),
    ("본문\n~~~\n\n예제", [("unclosed_fence", 2)]),
    ("본문\n$$\nx", [("unclosed_math", 2)]),
    ("본문\n[[대상", [("unclosed_wikilink", 2)]),
    ("[[첫 링크 [[두 번째]]", [("unclosed_wikilink", 1)]),
    ("[[|별칭]] [[]] [[#]] [[#^]]", [("empty_link_target", 1)] * 4),
    ("| 링크 | 값 |\n| --- | --- |\n| [[#제목|별칭]] | 1 |",
     [("unescaped_table_alias", 3)]),
    ("링크 | 값\n--- | ---\n[[노트|별칭]] | 1",
     [("unescaped_table_alias", 3)]),
    ("| [[노트|별칭]] | 값 |\n| --- | --- |", [("unescaped_table_alias", 1)]),
    ("> [!warning]\n> $$\n> x", [("unclosed_math", 2)]),
    ("%% 숨김 %%\n[[대상\n\n~~~\n\n코드", [("unclosed_wikilink", 2), ("unclosed_fence", 4)]),
])
def test_reports_issue_codes_and_original_line_numbers(content, expected):
    issues = validate_summary(content)
    assert [(issue.code, issue.line) for issue in issues] == expected
    assert all(issue.message for issue in issues)


def test_alias_outside_table_and_examples_inside_table_are_allowed():
    assert validate_summary(
        "[[노트|별칭]]\n\n| 예제 |\n| --- |\n| `[[닫지 않은 링크` |\n"
        "\n표 밖의 [[다른 노트|별칭]]"
    ) == []


def test_checks_resume_after_each_literal_region():
    issues = validate_summary(
        "```markdown\n[[예제\n```\n[[실제 오류\n"
        "$$\n[[수식 예제\n$$\n[[다음 오류\n"
        "%% [[주석 %%\n[[마지막 오류"
    )
    assert [(issue.code, issue.line) for issue in issues] == [
        ("unclosed_wikilink", 4), ("unclosed_wikilink", 8), ("unclosed_wikilink", 10),
    ]
