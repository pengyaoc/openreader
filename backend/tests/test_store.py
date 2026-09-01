"""Per-user read/starred isolation, tested at the store layer where it's
cheapest. This is the whole point of the multi-user change: two accounts
reading the same shared feed must not see each other's state.

Naming: alice is user 1 (the account pre-multi-user state migrated onto),
bob is user 2.
"""
import pytest

from app import store


@pytest.fixture
def db(two_users):
    """two_users plus one source and three articles, shared by both."""
    conn, alice, bob = two_users
    conn.execute(
        "INSERT INTO sources (key, type, title, folder) VALUES ('s1', 'rss', 'S', 'F')"
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    for n in (1, 2, 3):
        conn.execute(
            """INSERT INTO articles (source_id, guid, url, title, published_at, origin)
               VALUES (?, ?, ?, ?, ?, 'feed')""",
            (source_id, f"g{n}", f"https://x/{n}", f"Post {n}", f"2026-08-0{n}T00:00:00Z"),
        )
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM articles ORDER BY id")]
    return conn, alice, bob, source_id, ids


def unread_guids(conn, user_id):
    return {a["guid"] for a in store.list_articles(conn, user_id, view="unread")}


def starred_guids(conn, user_id):
    return {a["guid"] for a in store.list_articles(conn, user_id, view="starred")}


def unread_count(conn, user_id, source_id):
    return store.get_source(conn, user_id, source_id)["unread_count"]


def test_an_account_with_no_state_rows_sees_everything_unread(db):
    conn, alice, bob, source_id, _ = db
    assert unread_guids(conn, bob) == {"g1", "g2", "g3"}
    assert starred_guids(conn, bob) == set()
    assert unread_count(conn, bob, source_id) == 3
    assert store.list_sources(conn, bob)[0]["unread_count"] == 3


def test_reading_an_article_does_not_mark_it_read_for_the_other_account(db):
    conn, alice, bob, source_id, ids = db

    assert store.mark_read(conn, alice, ids[0]) is True

    assert unread_guids(conn, alice) == {"g2", "g3"}
    assert unread_guids(conn, bob) == {"g1", "g2", "g3"}
    assert unread_count(conn, alice, source_id) == 2
    assert unread_count(conn, bob, source_id) == 3
    assert store.get_article(conn, alice, ids[0])["is_read"] is True
    assert store.get_article(conn, bob, ids[0])["is_read"] is False


def test_starring_an_article_does_not_star_it_for_the_other_account(db):
    conn, alice, bob, _, ids = db

    assert store.toggle_star(conn, alice, ids[1]) == {"is_starred": True}

    assert starred_guids(conn, alice) == {"g2"}
    assert starred_guids(conn, bob) == set()
    assert store.get_article(conn, bob, ids[1])["is_starred"] is False

    assert store.toggle_star(conn, alice, ids[1]) == {"is_starred": False}
    assert starred_guids(conn, alice) == set()


def test_starring_and_reading_do_not_clobber_each_other(db):
    # Both live in one article_states row, so each write has to touch only
    # its own columns — an upsert that rewrote the whole row would reset
    # the other flag.
    conn, alice, _, _, ids = db

    store.mark_read(conn, alice, ids[0])
    store.toggle_star(conn, alice, ids[0])
    article = store.get_article(conn, alice, ids[0])
    assert (article["is_read"], article["is_starred"]) == (True, True)

    store.toggle_read(conn, alice, ids[0])  # back to unread
    article = store.get_article(conn, alice, ids[0])
    assert (article["is_read"], article["is_starred"]) == (False, True)

    store.toggle_star(conn, alice, ids[0])  # unstar
    store.mark_read(conn, alice, ids[0])
    article = store.get_article(conn, alice, ids[0])
    assert (article["is_read"], article["is_starred"]) == (True, False)


def test_mark_read_is_idempotent_and_does_not_restamp_read_at(db):
    conn, alice, _, _, ids = db

    store.mark_read(conn, alice, ids[0])
    first = store.get_article(conn, alice, ids[0])["read_at"]
    store.mark_read(conn, alice, ids[0])

    assert store.get_article(conn, alice, ids[0])["read_at"] == first


def test_mark_all_read_is_scoped_to_one_account_and_idempotent(db):
    conn, alice, bob, source_id, _ = db

    assert store.mark_all_read(conn, alice, source_id) == 3
    assert store.mark_all_read(conn, alice, source_id) == 0  # nothing left to flip

    assert unread_guids(conn, alice) == set()
    assert unread_guids(conn, bob) == {"g1", "g2", "g3"}
    assert store.mark_all_read(conn, bob, source_id) == 3  # bob's turn, full count


def test_mark_all_read_global_is_scoped_to_one_account(db):
    conn, alice, bob, source_id, ids = db
    store.mark_read(conn, alice, ids[0])

    assert store.mark_all_read_global(conn, alice) == 2  # only the still-unread two
    assert unread_count(conn, alice, source_id) == 0
    assert unread_count(conn, bob, source_id) == 3


def test_mark_all_read_returns_none_for_an_unknown_source(db):
    conn, alice, _, _, _ = db
    assert store.mark_all_read(conn, alice, 9999) is None


def test_writes_to_an_unknown_article_report_not_found(db):
    conn, alice, _, _, _ = db
    assert store.mark_read(conn, alice, 9999) is False
    assert store.toggle_read(conn, alice, 9999) is None
    assert store.toggle_star(conn, alice, 9999) is None
    assert store.get_article(conn, alice, 9999) is None


def test_summaries_are_shared_across_accounts(db):
    # Deliberate: a summary is a property of the article, generated once
    # and read by everyone. Only read/starred state is per-account.
    conn, alice, bob, _, ids = db
    store.save_summary(conn, ids[0], "<p>summary</p>")

    assert store.get_article(conn, bob, ids[0])["llm_summary_html"] == "<p>summary</p>"
    listed = {a["guid"]: a["has_summary"] for a in store.list_articles(conn, bob)}
    assert listed == {"g1": True, "g2": False, "g3": False}


def test_list_articles_filters_and_ordering_survive_the_state_join(db):
    # The join added a bound parameter ahead of every filter's placeholder,
    # so bind order is the thing most likely to have silently broken.
    conn, alice, _, source_id, ids = db
    store.mark_read(conn, alice, ids[2])

    by_source = store.list_articles(conn, alice, view="unread", source_id=source_id)
    assert [a["guid"] for a in by_source] == ["g2", "g1"]  # published_at DESC

    assert store.list_articles(conn, alice, folder="F") != []
    assert store.list_articles(conn, alice, folder="nope") == []
    assert [a["guid"] for a in store.list_articles(conn, alice, limit=1)] == ["g3"]
    assert [a["guid"] for a in store.list_articles(conn, alice, limit=1, offset=1)] == ["g2"]


def test_list_sources_valid_keys_filter_still_binds_correctly(db):
    conn, alice, _, _, ids = db
    store.mark_read(conn, alice, ids[0])

    assert [s["key"] for s in store.list_sources(conn, alice, valid_keys={"s1"})] == ["s1"]
    assert store.list_sources(conn, alice, valid_keys={"s1"})[0]["unread_count"] == 2
    assert store.list_sources(conn, alice, valid_keys=set()) == []
