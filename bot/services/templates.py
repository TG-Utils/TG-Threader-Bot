"""Renderer for notification templates.

Templates combine ``{{variable}}`` placeholders with BBCode-style links
``[a]label[/a]`` that are converted into HTML anchors pointing at
``link_url``. Substituted values and link labels are HTML-escaped so user
content cannot break the markup, and ``link_url`` must use an allowed
``http``/``https`` scheme.
"""

import html
import re
from collections.abc import Mapping

#: Placeholder of the form ``{{variableName}}``.
VARIABLE_RE = re.compile(r"\{\{(\w+)\}\}")

#: BBCode-style link ``[a]label[/a]``.
BB_CODE_LINK_RE = re.compile(r"\[a\](.*?)\[/a\]", re.DOTALL)

#: URL schemes allowed for notification links.
ALLOWED_URL_SCHEMES = ("http://", "https://")

#: Characters that could break out of the ``href`` attribute or inject markup.
_FORBIDDEN_URL_CHARS = "\"'<>"


class TemplateError(Exception):
    """Raised when a template references an unknown variable."""


def _validate_link_url(link_url: str) -> None:
    """Reject link targets outside the allowed ``http``/``https`` schemes.

    Additionally reject URLs containing quotes, angle brackets, whitespace
    or control characters: such a value could close the ``href`` attribute,
    inject a new attribute or open raw markup.
    """
    if not isinstance(link_url, str) or not link_url.startswith(ALLOWED_URL_SCHEMES):
        raise ValueError(f"link_url must start with 'http://' or 'https://', got {link_url!r}")
    for char in link_url:
        if char in _FORBIDDEN_URL_CHARS or char.isspace() or not char.isprintable():
            raise ValueError(
                f"link_url must not contain quotes, angle brackets, whitespace "
                f"or control characters, got {link_url!r}"
            )


def render(template: str, variables: Mapping[str, str], link_url: str) -> str:
    """Render ``template`` by building BBCode links and substituting variables.

    Every link label and variable value is HTML-escaped.

    Raises:
        ValueError: If ``link_url`` does not start with ``http://`` or
            ``https://``, or contains quotes, angle brackets, whitespace or
            control characters (validated even when the template has no
            links).
        TemplateError: If the template references a variable that is not in
            ``variables``; the message names the offending variable.
    """
    _validate_link_url(link_url)

    def replace_link(match: re.Match) -> str:
        label = html.escape(match.group(1), quote=True)
        href = html.escape(link_url, quote=True)
        return f'<a href="{href}">{label}</a>'

    def replace_variable(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            raise TemplateError(f"Unknown template variable: {name}")
        return html.escape(variables[name], quote=True)

    with_links = BB_CODE_LINK_RE.sub(replace_link, template)
    return VARIABLE_RE.sub(replace_variable, with_links)
