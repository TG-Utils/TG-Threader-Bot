"""Template renderer tests: module ``bot.services.templates``.

RED phase: module ``bot.services.templates`` does not exist yet, so
every test here must fail with ImportError/ModuleNotFoundError.

Specification (wiki, General-idea):
- templates contain ``{{variables}}`` (e.g. ``topicTitle``,
  ``initialMessageUrl``) and BBCode-like links ``[a]label[/a]``;
- an unknown variable is a ``TemplateError`` (the error type is fixed by
  this test) and the message names the offending variable;
- ``[a]label[/a]`` becomes the HTML ``<a href="{link_url}">label</a>``;
  the link address is passed to the function separately (the
  ``link_url`` parameter);
- variable values and the label text are escaped (``&``, ``<``, ``>``,
  quotes) — a user-supplied title must not break the markup;
- ``link_url`` must start with ``http://`` or ``https://`` — otherwise
  ``ValueError`` (protection against ``javascript:``/``data:`` schemes).
"""

import re

import pytest

from bot.services.templates import TemplateError, render

#: The notification thread link of the form https://t.me/<chat>/<id>?thread=<id>.
THREAD_URL = "https://t.me/somechannel/42?thread=7"

#: Extracts the label text from the assembled anchor ``<a href="...">label</a>``.
ANCHOR_LABEL_RE = re.compile(r'<a href="[^"]*">(?P<label>.*)</a>', re.DOTALL)


def extract_label(rendered: str) -> str:
    """Return the label text of the assembled link (escaping is checked on it)."""
    match = ANCHOR_LABEL_RE.search(rendered)
    assert match is not None, f"no HTML link <a href=...> in the output: {rendered!r}"
    return match.group("label")


class TestVariableSubstitution:
    """Substitution of ``{{variables}}``."""

    def test_plain_text_without_variables_passes_through(self):
        """A template without variables is returned as is."""
        rendered = render("Plain text without variables", {}, link_url=THREAD_URL)

        assert rendered == "Plain text without variables"

    def test_topic_title_and_initial_message_url_are_substituted(self):
        """Specification: ``{{topicTitle}}`` and ``{{initialMessageUrl}}`` are substituted.

        Both occurrences of the same variable are replaced and no
        unreplaced ``{{...}}`` remains.
        """
        template = (
            "Topic «{{topicTitle}}» created.\n"
            "First message: {{initialMessageUrl}}\n"
            "Title repeated: {{topicTitle}}"
        )
        variables = {
            "topicTitle": "Quarter plans",
            "initialMessageUrl": "https://t.me/somechannel/10",
        }

        rendered = render(template, variables, link_url=THREAD_URL)

        assert rendered.count("Quarter plans") == 2
        assert "https://t.me/somechannel/10" in rendered
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_unknown_variable_raises_template_error(self):
        """Specification: an unknown variable is a ``TemplateError``.

        The error message must name the offending variable, otherwise
        the template author cannot tell what is wrong.
        """
        template = "Hello, {{who}}!"

        with pytest.raises(TemplateError) as exc_info:
            render(template, {"topicTitle": "Some title"}, link_url=THREAD_URL)

        assert "who" in str(exc_info.value)


class TestBbCodeLink:
    """BBCode link ``[a]label[/a]`` → HTML anchor."""

    def test_bbcode_link_becomes_html_anchor(self):
        """Specification: ``[a]label[/a]`` → ``<a href="{link_url}">label</a>``.

        The address comes from the separate ``link_url`` parameter, not
        from the template variables.
        """
        rendered = render("Reply ready: [a]open thread[/a]", {}, link_url=THREAD_URL)

        assert f'<a href="{THREAD_URL}">open thread</a>' in rendered

    @pytest.mark.parametrize(
        "link_url",
        ["http://example.com/page", "https://t.me/c/123456/987?thread=555"],
    )
    def test_http_and_https_link_urls_are_accepted(self, link_url):
        """Specification: the ``http://`` and ``https://`` schemes are allowed."""
        rendered = render("[a]label[/a]", {}, link_url=link_url)

        assert f'<a href="{link_url}">label</a>' in rendered

    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "ftp://example.com/file.txt",
            "//evil.example/phish",
            "",
        ],
    )
    @pytest.mark.parametrize("template", ["Link: [a]label[/a]", "Plain text"])
    def test_link_url_must_start_with_http_or_https(self, template, bad_url):
        """Specification: ``link_url`` must start with ``http://`` or ``https://``.

        The check is mandatory in every case — including when the
        template contains no BBCode link at all (protection against
        ``javascript:``/``data:`` schemes before the address reaches the
        markup).
        """
        with pytest.raises(ValueError):
            render(template, {}, link_url=bad_url)


class TestHtmlEscaping:
    """HTML escaping: a user-supplied title must not break the markup."""

    def test_variable_value_is_escaped(self):
        """Variable values are escaped: ``&``, ``<``, ``>``, quotes.

        No raw markup and no raw quotes may remain in the output, while
        the escaped entities must be present.
        """
        evil_value = 'A & B <script>alert("x")</script> \'quoted\''

        rendered = render(
            "Topic: {{topicTitle}}",
            {"topicTitle": evil_value},
            link_url=THREAD_URL,
        )

        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
        assert "&amp;" in rendered
        assert '"' not in rendered
        assert "'" not in rendered

    def test_bbcode_label_is_escaped(self):
        """The BBCode link label text is escaped while the anchor is still assembled.

        Raw markup inside the label must not become real HTML, and the
        quotes must not wreck the ``href`` attribute.
        """
        rendered = render(
            '[a]<b>label & "Co"</b>[/a]',
            {},
            link_url=THREAD_URL,
        )

        assert f'<a href="{THREAD_URL}">' in rendered
        assert rendered.endswith("</a>")

        label = extract_label(rendered)
        assert "<b>" not in label
        assert "</b>" not in label
        assert "&lt;b&gt;" in label
        assert "&amp;" in label
        assert '"' not in label

    def test_escaped_value_cannot_break_anchor_structure(self):
        """A variable value inside a template with a link does not break the anchor itself."""
        rendered = render(
            '{{topicTitle}}: [a]see thread[/a]',
            {"topicTitle": 'x" onmouseover="evil()'},
            link_url=THREAD_URL,
        )

        # The anchor is assembled exactly per specification; nothing is inserted into the attribute.
        assert f'<a href="{THREAD_URL}">see thread</a>' in rendered
        # Quotes from the value are escaped: only two quotes remain in the
        # output — those of the anchor format itself <a href="...">label</a>.
        assert rendered.count('"') == 2
        assert "&quot;" in rendered or "&#34;" in rendered or "&#x22;" in rendered


class TestLinkUrlAttributeInjection:
    """Strict ``link_url`` validation: attribute injection into ``href`` is forbidden.

    Security review (cycle 4): the link address is placed into
    ``<a href="...">`` almost unprocessed, so a ``link_url`` able to
    break out of the attribute (quotes), smuggle markup (``<``, ``>``)
    or containing spaces/tabs/newlines must be rejected at validation
    time with ``ValueError`` — the same "strict validation → reject"
    contract as for forbidden schemes. The check is mandatory even for a
    template without a BBCode link: an invalid address must never make
    it to rendering.
    """

    @pytest.mark.parametrize(
        "link_url",
        [
            pytest.param('https://x.com/" onclick="alert(1)', id="onclick-attr-injection"),
            pytest.param('https://x"><b id="', id="href-break-opens-tag"),
            pytest.param("https://x.com/' onmouseover='x", id="single-quote-attr-injection"),
            pytest.param("https://x.com/<b>bold</b>", id="inline-tag-in-url"),
            pytest.param("https://x.com/quote>'\"", id="mixed-quotes-and-brackets"),
            pytest.param("https://x.com/a b", id="space-inside-url"),
            pytest.param("https://x.com/a\tb", id="tab-inside-url"),
            pytest.param("https://x.com/a\nb", id="newline-inside-url"),
        ],
    )
    @pytest.mark.parametrize("template", ["Link: [a]label[/a]", "Plain text"])
    def test_link_url_that_can_break_href_is_rejected(self, template, link_url):
        """A ``link_url`` with quotes/brackets/spaces → ``ValueError``.

        Such an address either closes the ``href`` attribute and adds its
        own (``onclick``/``onmouseover``) or opens new markup — an
        attribute injection. Scheme validation does not catch this (the
        address starts with ``https://``), hence the strict character
        check before it can reach the markup.
        """
        with pytest.raises(ValueError):
            render(template, {}, link_url=link_url)
