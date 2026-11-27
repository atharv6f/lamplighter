"""HTML to text: what is dropped, where lines break, and how a link reads."""

from lamplighter.adapters.builtin_tools.page_text import page_text

PAGE = """<!doctype html>
<html><head><title>Example Domain</title>
<style>body { color: red }</style>
<script>alert("hidden")</script></head>
<body>
<div><h1>Example Domain</h1>
<p>This domain is for use in   illustrative
examples.</p>



<p><a href="https://www.iana.org/domains/example">More information&hellip;</a></p>
<script>var hidden = 1;</script>
<noscript>Turn on scripts.</noscript><template><p>Not shown</p></template>
<ul><li>one</li><li>two<br>three</li></ul>
</div></body></html>
"""


def test_a_known_page_reads_as_its_text() -> None:
    assert page_text(PAGE) == (
        "Example Domain\n"
        "\n"
        "This domain is for use in illustrative examples.\n"
        "\n"
        "More information… (https://www.iana.org/domains/example)\n"
        "\n"
        "one\n"
        "\n"
        "two\n"
        "three"
    )


def test_a_head_left_open_ends_at_the_body() -> None:
    assert page_text("<head><title>T</title><body><p>seen</p>") == "seen"


def test_a_link_without_an_address_is_its_text_alone() -> None:
    assert page_text('<a name="top">Top</a> and <a href="/x">x</a>') == "Top and x (/x)"


def test_an_unclosed_tag_and_lone_brackets_do_not_raise() -> None:
    assert page_text("<p>a < b <i>c") == "a < b c"
    assert page_text("<" * 1_000) == "<" * 1_000
