"""Chat-label resolution through the shared TTL cache (security review N1a).

``bot.keyboards.chat_title`` labels every menu/picker button with
``await bot.get_chat(ref)`` at RENDER time — one menu costs two lookups
per pair, so spamming ``/settings`` burned one API call per button.
The contract pinned here (behavioural: the tests count ``bot.get_chat``
awaits and read the labels, they never look inside the cache):

- ``bot.admin_cache.get_chat(bot, ref)`` — the cached wrapper around
  ``bot.get_chat``, living in ``bot.admin_cache`` so the per-test reset
  of ``tests/conftest.py`` (``admin_cache.clear()``) drops it as well;
  the window is the module constant ``bot.admin_cache.CHAT_TTL``
  seconds (both names are pinned below);
- two consecutive ``chat_title(bot, ref)`` calls for ONE ref hit the
  API exactly ONCE and keep the old label chain
  ``.title`` → ``@username`` → the ref;
- a RAISING ``get_chat`` is cached too (the NEGATIVE answer): the label
  falls back to ``str(ref)`` and the retry inside the window does not
  touch the API again;
- ``bot.admin_cache.clear()`` and an elapsed ``CHAT_TTL`` window both
  force a refetch, so no label can outlive its entry.

``bot.keyboards`` and ``bot.admin_cache`` exist already (the baseline
is green), so only the CACHE pins are red in this phase — they fail
inside their own bodies, never at collection time.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

#: The one chat ref every pin below labels.
REF = -100111


def keyboards_mod():
    """The ``bot.keyboards`` module, imported at test time."""
    from bot import keyboards

    return keyboards


def admin_cache_mod():
    """The ``bot.admin_cache`` module, imported at test time."""
    from bot import admin_cache

    return admin_cache


def make_bot(chats=None):
    """An AsyncMock bot whose ``get_chat`` answers from a ``{str(ref): chat}`` map.

    Args:
        chats: chat objects to return; an ``Exception`` value is raised
            as-is and an UNKNOWN ref raises ``TelegramBadRequest`` —
            both are the fallback-to-ref case of the label chain.

    The call count lives on ``bot.get_chat.await_count``: that is what
    every pin of this module reads.
    """
    bot = AsyncMock(name="bot")
    bot.chats_map = dict(chats or {})

    async def get_chat(*args, **kwargs):
        ref = args[0] if args else kwargs.get("chat_id")
        value = bot.chats_map.get(str(ref))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise TelegramBadRequest(method=None, message="chat not found")
        return value

    bot.get_chat.side_effect = get_chat
    return bot


class TestChatTitleCacheContract:
    """The names and the window of the cache ``chat_title`` must go through."""

    def test_the_admin_cache_exposes_the_cached_get_chat(self):
        """``bot.admin_cache.get_chat(bot, ref)`` is the entry point of the cache.

        Pinning the NAME matters: the conftest reset and every other
        consumer talk to this module, and a wrapper living anywhere
        else would silently escape the per-test ``clear()``.
        """
        admin_cache = admin_cache_mod()

        cached_get_chat = getattr(admin_cache, "get_chat", None)
        assert callable(cached_get_chat), (
            "bot.admin_cache must expose the cached wrapper `get_chat(bot, chat_ref)` "
            f"— got {cached_get_chat!r}"
        )

    def test_the_chat_ttl_constant_is_a_positive_number(self):
        """``bot.admin_cache.CHAT_TTL`` is the (patchable) window of the labels."""
        ttl = getattr(admin_cache_mod(), "CHAT_TTL", None)

        assert isinstance(ttl, (int, float)), (
            f"bot.admin_cache must expose the numeric constant CHAT_TTL, got {ttl!r}"
        )
        assert ttl > 0, f"CHAT_TTL must describe a real window, got {ttl!r}"

    async def test_patching_the_chat_ttl_to_zero_makes_the_next_label_fresh(self, monkeypatch):
        """A zero window must not serve the second label from the cache."""
        bot = make_bot({str(REF): SimpleNamespace(title="Alpha", username="srcgroup")})
        monkeypatch.setattr(admin_cache_mod(), "CHAT_TTL", 0)

        assert await keyboards_mod().chat_title(bot, REF) == "Alpha", "sanity: labelled"
        assert await keyboards_mod().chat_title(bot, REF) == "Alpha", "and labelled again"

        assert bot.get_chat.await_count == 2, (
            "CHAT_TTL = 0 must leave nothing to serve the second label from"
        )


class TestChatTitleIsCached:
    """``chat_title`` resolves every label through ``bot.admin_cache.get_chat``."""

    async def test_two_consecutive_labels_for_one_ref_hit_the_api_once(self):
        """Menu = 2N renders of the same refs — they cost ONE ``get_chat`` each.

        The label itself keeps the old contract: ``.title`` wins over
        the username of the very same chat.
        """
        bot = make_bot({str(REF): SimpleNamespace(title="Alpha", username="srcgroup")})

        first = await keyboards_mod().chat_title(bot, REF)
        second = await keyboards_mod().chat_title(bot, REF)

        assert first == "Alpha", "the label is the chat title (title wins over username)"
        assert second == first, "a repeated render must not change the label"
        assert bot.get_chat.await_count == 1, (
            "the second label must be served from the cache — one API call per ref"
        )

    async def test_a_raising_get_chat_falls_back_to_the_ref_and_is_cached(self):
        """The NEGATIVE answer is cached: one failed call, then ``str(ref)`` forever."""
        bot = make_bot({str(REF): TelegramBadRequest(method=None, message="chat not found")})

        first = await keyboards_mod().chat_title(bot, REF)
        second = await keyboards_mod().chat_title(bot, REF)

        assert first == str(REF), "a raising get_chat still reads as the ref itself"
        assert second == str(REF), "and the repeat reads exactly the same"
        assert bot.get_chat.await_count == 1, (
            "the failure must be cached: the retry inside the window never "
            "reaches the API again"
        )

    async def test_a_username_only_chat_is_labelled_at_username_and_cached(self):
        """No title → ``@username``, and that label is cached the same way."""
        bot = make_bot({str(REF): SimpleNamespace(title=None, username="name")})

        first = await keyboards_mod().chat_title(bot, REF)
        second = await keyboards_mod().chat_title(bot, REF)

        assert first == "@name", "a chat without a title is labelled by its username"
        assert second == first, "the username label is repeated unchanged"
        assert bot.get_chat.await_count == 1, (
            "2 chat_title calls for one ref = 1 get_chat, username labels included"
        )

    async def test_clearing_the_shared_cache_forces_a_refetch(self):
        """``admin_cache.clear()`` (the per-test reset) drops the labels too."""
        bot = make_bot({str(REF): SimpleNamespace(title="Alpha", username=None)})

        assert await keyboards_mod().chat_title(bot, REF) == "Alpha", "sanity: cached"
        assert bot.get_chat.await_count == 1, "sanity: one call warms the cache"

        admin_cache_mod().clear()
        assert await keyboards_mod().chat_title(bot, REF) == "Alpha", "still the label"

        assert bot.get_chat.await_count == 2, (
            "after admin_cache.clear() the label must be resolved from the API again"
        )
