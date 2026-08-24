from app.ml.search_rank import bounded_edit_distance, field_lexical_score


def test_short_words_do_not_fuzzy_match_unrelated_words():
    """Regression: "sakar" and "safar" are unrelated real words, exactly 1
    edit apart -- a query for "sakar" must not surface a song titled "Sifar
    Safar" as if it were a near-match. Same for "pattalam" vs "pattellam"
    (2 edits, different word)."""
    assert bounded_edit_distance("sakar", "safar") == 1
    assert field_lexical_score("sakar", "Sifar Safar") == 0.0
    assert field_lexical_score("pattalam", "Paadatha Pattellam") == 0.0


def test_exact_and_substring_matches_still_score_highly():
    assert field_lexical_score("sakar", "Nirakar Sakar") > 0.0
    assert field_lexical_score("pattalam", "Mazhalai Pattalam") > 0.0
    assert field_lexical_score("believer", "Believer") == 1.0


def test_longer_words_still_tolerate_a_real_typo():
    """Typo tolerance is the point of fuzzy matching -- it must not vanish
    entirely, only stop firing on short words where it produces false
    positives."""
    assert field_lexical_score("believr", "Believer") > 0.0
    assert field_lexical_score("levitatin", "Levitating") > 0.5


def test_case_insensitive_exact_match_is_a_perfect_score():
    assert field_lexical_score("Believer", "believer") == 1.0
