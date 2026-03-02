from applypilot.scoring.pdf import build_letter_html


def test_build_letter_html_preserves_paragraphs_and_line_breaks() -> None:
    text = "Hello Hiring Team,\nLine 2\n\nSecond paragraph"
    html = build_letter_html(text)

    assert "<p>Hello Hiring Team,<br>Line 2</p>" in html
    assert "<p>Second paragraph</p>" in html


def test_build_letter_html_escapes_html_content() -> None:
    text = "Use <script>alert('x')</script> safely"
    html = build_letter_html(text)

    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in html
    assert "<script>" not in html
