"""Thread and message URL building tests: module ``bot.services.threads``.

Specification (wiki, General-idea; BRIEF step 5):
- a thread link looks like ``https://t.me/<chat>/<message_id>?thread=<message_id>``;
  in the wiki example the message id and the thread id coincide:
  ``build_thread_url("lolsSupport", 100158)`` →
  ``"https://t.me/lolsSupport/100158?thread=100158"``;
- a plain message link (no ``?thread=``) is built for the header: a
  public ``@name`` → ``https://t.me/name/<id>``, a private chat (int
  id) → ``https://t.me/c/<id without -100>/<id>`` —
  ``build_message_url``;
- private chats use the same ``t.me/c`` form for thread links:
  ``build_thread_url(-100111, 42)`` →
  ``"https://t.me/c/111/42?thread=42"``;
- the chat name may arrive with a leading ``@`` — the result is the same
  (``@`` is stripped, not encoded);
- invalid input → ``ValueError``: an empty chat name, a
  non-positive or non-numeric ``message_id``;
- security review L1: an INTEGER chat must be a strict supergroup id —
  ``str(chat)`` starts with ``-100`` AND leaves a non-empty digit tail
  after that prefix. ``100555`` (digits without the ``-100`` prefix) and
  ``-12345`` (a minus without ``100``) raise ``ValueError`` instead of
  yielding a broken ``t.me/c/…`` link that points at another chat,
  while ``-100123456`` keeps working.

``build_message_url`` does not exist yet (RED): its tests import it
inside the test body, so only they fail — the ``build_thread_url``
tests above stay green.
"""

import pytest

from bot.services.threads import build_thread_url

#: The wiki example: chat, message id and the expected link.
WIKI_CHAT = "lolsSupport"
WIKI_MESSAGE_ID = 100158
WIKI_URL = "https://t.me/lolsSupport/100158?thread=100158"

#: Positive cases: (chat, message_id, expected URL).
VALID_CASES = [
    pytest.param(
        "lolsSupport",
        100158,
        "https://t.me/lolsSupport/100158?thread=100158",
        id="wiki-example",
    ),
    pytest.param(
        "@lolsSupport",
        100158,
        "https://t.me/lolsSupport/100158?thread=100158",
        id="leading-at-stripped",
    ),
    pytest.param(
        "@somechannel",
        42,
        "https://t.me/somechannel/42?thread=42",
        id="at-with-other-chat-and-id",
    ),
    pytest.param(
        "general",
        1,
        "https://t.me/general/1?thread=1",
        id="minimal-positive-id",
    ),
]

#: Negative cases: (chat, message_id) → an expected ``ValueError``.
INVALID_CASES = [
    pytest.param("", WIKI_MESSAGE_ID, id="empty-chat-name"),
    pytest.param("@", WIKI_MESSAGE_ID, id="at-only-is-empty-chat-name"),
    pytest.param(WIKI_CHAT, 0, id="zero-message-id"),
    pytest.param(WIKI_CHAT, -1, id="negative-message-id"),
    pytest.param(WIKI_CHAT, "not-a-number", id="non-numeric-message-id"),
    pytest.param(WIKI_CHAT, None, id="missing-message-id"),
]


class TestBuildThreadUrlValid:
    """Positive cases of ``build_thread_url()``."""

    @pytest.mark.parametrize(("chat", "message_id", "expected_url"), VALID_CASES)
    def test_builds_wiki_style_thread_url(self, chat: str, message_id: int, expected_url: str):
        """A link of the form ``https://t.me/<chat>/<id>?thread=<id>`` (wiki example)."""
        assert build_thread_url(chat, message_id) == expected_url

    @pytest.mark.parametrize(("chat", "message_id", "_expected_url"), VALID_CASES)
    def test_message_id_duplicated_in_path_and_thread_param(
        self, chat: str, message_id: int, _expected_url: str
    ):
        """``message_id`` is duplicated in the path and in the ``thread`` param.

        This is the key difference between a thread link and a plain
        message link (wiki: ``https://t.me/<chat>/<message_id>?thread=<message_id>``).
        """
        url = build_thread_url(chat, message_id)

        assert url == f"https://t.me/{chat.lstrip('@')}/{message_id}?thread={message_id}"

    def test_wiki_example_verbatim(self):
        """The verbatim wiki example — the contract of the function."""
        assert build_thread_url(WIKI_CHAT, WIKI_MESSAGE_ID) == WIKI_URL

    def test_leading_at_is_dropped_not_encoded(self):
        """Specification: a leading ``@`` in the chat is dropped and never reaches the URL."""
        url = build_thread_url(f"@{WIKI_CHAT}", WIKI_MESSAGE_ID)

        assert url == WIKI_URL
        assert "@" not in url, "the thread link must not contain the @ of the chat name"


class TestBuildThreadUrlInvalid:
    """Invalid input → ``ValueError``."""

    @pytest.mark.parametrize(("chat", "message_id"), INVALID_CASES)
    def test_invalid_input_raises_value_error(self, chat: str, message_id: int):
        """Empty chat / non-positive / non-numeric id — a ValueError.

        ValueError, not TypeError: the calling code catches a single
        error kind for all sorts of invalid data.
        """
        with pytest.raises(ValueError):
            build_thread_url(chat, message_id)

    @pytest.mark.parametrize(("chat", "message_id"), INVALID_CASES)
    def test_invalid_input_does_not_return_url(self, chat: str, message_id: int):
        """Invalid input must not silently turn into a link."""
        with pytest.raises(ValueError) as excinfo:
            build_thread_url(chat, message_id)

        assert not str(excinfo.value).startswith("https://")


class TestBuildThreadUrlChatNameFormat:
    """Username-format chat name: protection against path/query injection.

    Security review (cycle 4): the chat name is inserted into the path
    ``https://t.me/<chat>/<id>?thread=<id>`` without encoding, so ``/``,
    ``?``, ``#`` repaint the link's path/query/fragment (the ``thread``
    can be swapped or an arbitrary address supplied), quotes break the
    surrounding ``<a href="...">`` markup, and spaces and newlines are
    invalid in a URL. Contract: after the leading ``@`` is removed the
    name must match ``[A-Za-z0-9_]{1,64}`` (Telegram username format) —
    otherwise ``ValueError``.
    """

    @pytest.mark.parametrize(
        "chat",
        [
            pytest.param('a/1?thread=2#x"y', id="path-query-fragment-injection"),
            pytest.param("foo bar", id="space-inside-name"),
            pytest.param("foo\nbar", id="newline-inside-name"),
            pytest.param("%", id="percent-sign"),
            pytest.param("#", id="hash-marker"),
            pytest.param("?", id="question-mark-marker"),
            pytest.param('chan"name', id="double-quote"),
            pytest.param("chan'name", id="single-quote"),
            pytest.param("chan<name>", id="angle-brackets"),
            pytest.param("-chan", id="leading-dash-outside-username-class"),
            pytest.param("имя-канала", id="non-ascii-name"),
            pytest.param("a" * 65, id="name-longer-than-64"),
        ],
    )
    def test_chat_name_outside_username_format_raises_value_error(self, chat: str):
        """A name outside ``[A-Za-z0-9_]{1,64}`` → ``ValueError``, not a URL.

        This is not about link "beauty": stray ``/``, ``?``, ``#``
        change the structure of the t.me address, while quotes and
        newlines can rip the URL out of the surrounding notification
        markup.
        """
        with pytest.raises(ValueError):
            build_thread_url(chat, WIKI_MESSAGE_ID)

    def test_maximum_length_username_is_accepted(self):
        """Contract boundary: a name of exactly 64 characters is accepted.

        The upper limit is inclusive (``{1,64}``), so validation must not
        be stricter — otherwise valid long chat names would be rejected.
        """
        chat = "b" * 64

        url = build_thread_url(chat, WIKI_MESSAGE_ID)

        assert url == f"https://t.me/{chat}/{WIKI_MESSAGE_ID}?thread={WIKI_MESSAGE_ID}"


def message_url_builder():
    """``build_message_url``, imported at test time.

    While the plain-link builder does not exist (RED), only these
    tests fail with ``ImportError`` — the ``build_thread_url`` tests
    above stay green.
    """
    from bot.services.threads import build_message_url

    return build_message_url


#: Plain links of public chats: ``t.me/<name>/<id>`` (no ``?thread=``).
PUBLIC_MESSAGE_CASES = [
    pytest.param("@lolsSupport", 100158, "https://t.me/lolsSupport/100158", id="with-at"),
    pytest.param("lolsSupport", 100158, "https://t.me/lolsSupport/100158", id="without-at"),
    pytest.param("@forumgroup", 42, "https://t.me/forumgroup/42", id="other-chat-and-id"),
]

#: Private chats arrive as integer ids: ``t.me/c/<id without -100>/<id>``.
PRIVATE_MESSAGE_CASES = [
    pytest.param(-100111, 42, "https://t.me/c/111/42", id="short-private-id"),
    pytest.param(
        -1001234567890, 100158, "https://t.me/c/1234567890/100158", id="typical-private-id"
    ),
]

#: Private thread links: same ``t.me/c`` form plus ``?thread=<id>``.
PRIVATE_THREAD_CASES = [
    pytest.param(-100111, 42, "https://t.me/c/111/42?thread=42", id="short-private-id"),
    pytest.param(
        -1001234567890,
        100158,
        "https://t.me/c/1234567890/100158?thread=100158",
        id="typical-private-id",
    ),
]

#: Invalid plain links: empty/non-username chats, non-positive ids, -100 alone.
INVALID_MESSAGE_CASES = [
    pytest.param("", 42, id="empty-chat-name"),
    pytest.param("@", 42, id="at-only-is-empty-chat-name"),
    pytest.param("foo bar", 42, id="space-inside-name"),
    pytest.param("имя-канала", 42, id="non-ascii-name"),
    pytest.param(-100, 42, id="private-id-without-digits"),
    pytest.param("lolsSupport", 0, id="zero-message-id"),
    pytest.param("lolsSupport", -1, id="negative-message-id"),
]


class TestBuildMessageUrl:
    """``build_message_url``: the plain link to a single message (BRIEF step 5).

    The header posted into the target chat links back to the original
    first message of the flood — without a ``?thread=`` parameter,
    because the thread does not exist yet at that moment.
    """

    @pytest.mark.parametrize(("chat", "message_id", "expected_url"), PUBLIC_MESSAGE_CASES)
    def test_public_chat_builds_a_t_me_link(self, chat, message_id, expected_url):
        """A public ``@name`` → ``https://t.me/name/<id>`` (no thread parameter)."""
        build_message_url = message_url_builder()

        url = build_message_url(chat, message_id)

        assert url == expected_url
        assert "?thread=" not in url, "a plain message link carries no thread parameter"

    @pytest.mark.parametrize(("chat", "message_id", "expected_url"), PRIVATE_MESSAGE_CASES)
    def test_private_chat_builds_a_c_link(self, chat, message_id, expected_url):
        """A private int id → ``https://t.me/c/<id without -100>/<id>``."""
        build_message_url = message_url_builder()

        assert build_message_url(chat, message_id) == expected_url

    @pytest.mark.parametrize(("chat", "message_id"), INVALID_MESSAGE_CASES)
    def test_invalid_input_raises_value_error(self, chat, message_id):
        """Empty/non-username chat or bad id → ``ValueError``, not a URL."""
        build_message_url = message_url_builder()

        with pytest.raises(ValueError):
            build_message_url(chat, message_id)


class TestBuildThreadUrlPrivateChat:
    """Thread links of private chats: ``t.me/c/<id without -100>/<id>?thread=<id>``.

    BRIEF step 5: public chats keep ``t.me/<name>/<id>?thread=<id>``,
    private ones switch to the ``/c/`` form with the ``-100`` prefix of
    the chat id dropped.
    """

    @pytest.mark.parametrize(("chat", "message_id", "expected_url"), PRIVATE_THREAD_CASES)
    def test_private_chat_thread_url(self, chat, message_id, expected_url):
        """A private int id → the ``t.me/c`` thread link."""
        assert build_thread_url(chat, message_id) == expected_url

    @pytest.mark.parametrize(("chat", "message_id", "_expected_url"), PRIVATE_THREAD_CASES)
    def test_private_thread_url_keeps_the_thread_parameter(self, chat, message_id, _expected_url):
        """The message id is duplicated in the path and in ``?thread=``, as everywhere."""
        url = build_thread_url(chat, message_id)
        internal_id = str(chat).removeprefix("-100")

        assert url == f"https://t.me/c/{internal_id}/{message_id}?thread={message_id}"

    def test_private_id_without_digits_is_rejected(self):
        """``-100`` alone has no internal id left after the prefix → ``ValueError``."""
        with pytest.raises(ValueError):
            build_thread_url(-100, 42)


class TestStrictMinus100PrivateId:
    """Integer chats are accepted ONLY in the strict ``-100<digits>`` form (L1).

    Security review (L1): dropping the prefix with
    ``str(chat).removeprefix("-100")`` alone also accepts ids that never
    HAD the prefix — ``100555`` keeps its digits and builds a bogus
    ``t.me/c/100555/…`` link (a different chat than the configured one),
    and ``-12345`` is not a supergroup id at all. Contract for BOTH
    builders: ``str(chat)`` must start with ``-100`` AND leave at least
    one digit behind the prefix — otherwise ``ValueError``.
    """

    #: Ids that must be rejected by both link builders.
    OUTSIDE_PREFIX_IDS = [
        pytest.param(100555, id="digits-without-minus-100-prefix"),
        pytest.param(-12345, id="minus-without-100-prefix"),
    ]

    @pytest.mark.parametrize("chat", OUTSIDE_PREFIX_IDS)
    def test_build_thread_url_rejects_ids_outside_minus_100(self, chat: int):
        """A thread link for a non-``-100`` id must raise, not produce ``t.me/c/…``."""
        with pytest.raises(ValueError):
            build_thread_url(chat, WIKI_MESSAGE_ID)

    @pytest.mark.parametrize("chat", OUTSIDE_PREFIX_IDS)
    def test_build_message_url_rejects_ids_outside_minus_100(self, chat: int):
        """Same strictness for the plain message link (BRIEF step 5 header link)."""
        build_message_url = message_url_builder()

        with pytest.raises(ValueError):
            build_message_url(chat, WIKI_MESSAGE_ID)

    def test_strict_minus_100_thread_url_still_works(self):
        """Boundary of the contract: ``-100123456`` is a valid private id."""
        assert build_thread_url(-100123456, 42) == "https://t.me/c/123456/42?thread=42"

    def test_strict_minus_100_message_url_still_works(self):
        """The plain link of the same valid private id."""
        build_message_url = message_url_builder()

        assert build_message_url(-100123456, 42) == "https://t.me/c/123456/42"
