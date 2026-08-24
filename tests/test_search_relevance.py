"""Search result *ordering*.

Two defects made results look arbitrary to a user, and both are pinned here
because neither shows up as an error -- the right songs came back, in an order
that made no sense.

1. Ties were broken alphabetically by title. Ties are not rare: a query naming
   an artist matches the artist field of every result exactly, so a dozen
   results score identically and nothing else separates them. Sorting those on
   title threw away the order Gaana ranked them in, which is the only
   popularity signal available for a track fetched seconds ago (our own
   `play_count` counts plays in *this* app, so it is 0 for all of them).

2. `artist_name` holds the whole credit list -- "Amaal Mallik, Arijit Singh,
   Rashmi Virag" -- and was matched as one string. Searching "arijit singh"
   therefore scored 1.00 for a solo track, 0.50 with one collaborator and 0.19
   with five, so his most-collaborative songs sank regardless of relevance.
"""
import pytest

from app.ml import search_rank


class FakeSong:
    """Just the fields the ranker reads."""

    def __init__(self, song_id, title, artist_name, album_name="", play_count=0):
        self.id = song_id
        self.title = title
        self.artist_name = artist_name
        self.album_name = album_name
        self.language = "Hindi"
        self.genre = "Pop"
        self.mood = ""
        self.duration = 200
        self.play_count = play_count


# The real credit strings Gaana returns for a search of "arijit singh", in the
# order it returned them.
ARIJIT_RESULTS = [
    ("Humdard", "Arijit Singh"),
    ("Yeh Awarapan", "Amaal Mallik, Arijit Singh, Rashmi Virag"),
    ("Heeriye", "Jasleen Royal, Arijit Singh, Dulquer Salmaan"),
    ("Phir Se", "Shashwat Sachdev, Arijit Singh, Irshad Kamil"),
    ("Tujhko", "Pritam, Arijit Singh, Sunidhi Chauhan, Amitabh Bhattacharya"),
    ("Sanam Re", "Mithoon, Arijit Singh"),
    ("Ghar Kab Aaoge", "Anu Malik, Mithoon, Sonu Nigam, Arijit Singh, Roop Kumar"),
    ("Saware", "Pritam, Arijit Singh"),
]


def test_a_collaboration_scores_as_highly_as_a_solo_credit():
    """Every one of these songs is by Arijit Singh, so every one scores 1.00.

    Matching the query against the joined credit string scored how much of the
    credit the query accounted for, which is a measure of how *few* other
    artists were on the track -- not of whether the searched artist was on it.
    """
    scores = [
        search_rank.artist_lexical_score("arijit singh", credit)
        for _, credit in ARIJIT_RESULTS
    ]
    assert scores == [1.0] * len(ARIJIT_RESULTS), dict(
        zip((c for _, c in ARIJIT_RESULTS), scores)
    )


def test_an_artist_query_preserves_upstream_order():
    """With every result tying on relevance, Gaana's ranking is what remains.

    This is the user-visible half of both bugs: before, the top result was
    whichever title happened to sort first alphabetically.
    """
    songs = [
        FakeSong(f"s{i}", title, credit)
        for i, (title, credit) in enumerate(ARIJIT_RESULTS)
    ]
    ranked = search_rank.rank("arijit singh", songs)

    assert [r.song.title for r in ranked] == [title for title, _ in ARIJIT_RESULTS]


def test_ties_never_fall_back_to_alphabetical_order():
    """A tie must resolve to upstream position, not to the alphabet."""
    songs = [
        FakeSong("s1", "Zebra", "Same Artist"),
        FakeSong("s2", "Apple", "Same Artist"),
    ]
    ranked = search_rank.rank("same artist", songs)

    assert [r.lexical for r in ranked] == [1.0, 1.0], "expected a genuine tie"
    assert [r.song.title for r in ranked] == ["Zebra", "Apple"]


def test_a_real_relevance_gap_still_beats_upstream_position():
    """The upstream prior settles ties; it must not override relevance.

    Gaana's first result here does not match the query at all -- a real case:
    searching "naatu naatu" returned "Mere Nath" ahead of the actual song.
    """
    songs = [
        FakeSong("s1", "Mere Nath", "Someone Else"),
        FakeSong("s2", "Naatu Naatu", "Rahul Sipligunj"),
    ]
    ranked = search_rank.rank("naatu naatu", songs)

    assert ranked[0].song.title == "Naatu Naatu"


def test_exact_title_match_outranks_an_album_wide_match():
    """One track *is* the thing searched for; the rest merely share its album.

    Guards the tier ordering: an exact title (1.00) must stay above every track
    whose album prefix-matches (~0.88), however Gaana ordered them.
    """
    songs = [
        FakeSong("s1", "Kuthanthram", "Sushin Shyam", album_name="Manjummel Boys (OST)"),
        FakeSong("s2", "Ammanu", "Sushin Shyam", album_name="Manjummel Boys (OST)"),
        FakeSong("s3", "Manjummel Boys", "Sushin Shyam", album_name="Manjummel Boys (OST)"),
    ]
    ranked = search_rank.rank("manjummel boys", songs)

    assert ranked[0].song.title == "Manjummel Boys"
    # ...and the album-mates keep Gaana's relative order among themselves.
    assert [r.song.title for r in ranked[1:]] == ["Kuthanthram", "Ammanu"]


def test_personalization_credits_every_artist_on_a_track():
    """Affinity is keyed per artist, but the song carries the whole credit list.

    A user who plays Arijit Singh constantly got no affinity credit for a track
    billed "Mithoon, Arijit Singh", because the lookup used the joined string as
    a single key and missed.
    """
    from datetime import datetime, timezone

    from app.ml.features import UserState

    state = UserState(
        user_id="u1",
        as_of=datetime.now(timezone.utc),
        taste_vector=None,
        artist_affinity={"arijit singh": 10.0},
        n_interactions=25,
    )

    solo = FakeSong("s1", "Humdard", "Arijit Singh")
    collab = FakeSong("s2", "Sanam Re", "Mithoon, Arijit Singh")

    assert search_rank.personal_score(collab, state) == pytest.approx(
        search_rank.personal_score(solo, state)
    )
    assert search_rank.personal_score(collab, state) > 0.0


def test_credited_artists_splits_and_trims():
    assert search_rank.credited_artists("A, B ,C") == ["A", "B", "C"]
    assert search_rank.credited_artists("Solo") == ["Solo"]
    assert search_rank.credited_artists("") == []
    assert search_rank.credited_artists(None) == []


def test_a_multi_artist_query_still_matches_the_whole_credit():
    """Per-artist scoring must not lose the "artist A and artist B" query shape."""
    credit = "Arijit Singh, Shreya Ghoshal"
    assert search_rank.artist_lexical_score("arijit singh shreya ghoshal", credit) > 0.5
