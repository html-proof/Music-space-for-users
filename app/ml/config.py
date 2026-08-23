"""ML hyperparameters and the ranking feature registry.

FEATURE_NAMES is the contract between four places that must never drift apart:
the feature builder (`features.build_ranking_features`), the prior weights
below, the trained coefficient vector persisted in `ml_models.artifact`, and
the offline evaluator. A stored model records the feature names it was fitted
on, so a model trained before a feature was added is rejected at load time
rather than silently scoring a misaligned vector.

The tunables that operators actually need to change in production are sourced
from `app.config.settings` (the `ML_*` env vars); the rest are literals here,
because changing them is a modelling decision that belongs in a commit rather
than in a dashboard.
"""
from app.config.settings import settings

# Song content vector width. The hashing trick means this is a pure
# space/collision tradeoff and needs no vocabulary, so a new Gaana upsert is
# rankable the moment it lands. Changing this invalidates persisted item_sim
# artifacts -- retrain after doing so.
EMBEDDING_DIM = settings.ML_EMBEDDING_DIM

# Field weights for the content vector. Artist dominates because "more of this
# artist" is the single strongest content signal in music; title tokens are
# damped because they are noisy ("Remix", "Live", "Lofi").
FIELD_WEIGHTS = {
    "artist": 3.0,
    "genre": 2.0,
    "language": 1.5,
    "mood": 1.5,
    "album": 1.0,
    "title_token": 0.6,
}

# Interaction weights used for both the user taste vector and training labels.
SIGNAL_WEIGHTS = {
    "like": 3.0,
    "playlist_add": 2.5,
    "follow_artist": 2.0,
    "save_album": 1.5,
    "complete": 2.0,
    "play": 1.0,
    "search": 0.5,
    "skip": -1.0,
}

# Taste-vector half-life. Music taste drifts, so a play from 60 days ago counts
# half as much as one from today.
TASTE_HALFLIFE_DAYS = 60.0

# Item-item CF. SHRINKAGE damps pairs supported by very few co-occurrences:
# two songs seen together once would otherwise score a perfect 1.0 cosine and
# outrank genuinely related pairs.
CF_TOP_K = 50
CF_SHRINKAGE = 10.0
CF_MAX_SESSION_SPAN_HOURS = 6.0

# Candidate generation caps, per source, before ranking.
CANDIDATE_LIMIT = settings.ML_CANDIDATE_LIMIT
SOURCE_CAPS = {
    "content": 150,
    "cf": 120,
    "artist_genre": 100,
    "popular": 80,
    "playlist": 60,
}

# Diversification.
MMR_LAMBDA = 0.72          # 1.0 = pure relevance, 0.0 = pure diversity
MAX_PER_ARTIST = 2
EXPLORATION_RATE = settings.ML_EXPLORATION_RATE

# Training.
MIN_TRAINING_SAMPLES = settings.ML_MIN_TRAINING_SAMPLES
MIN_TRAINING_USERS = 5
HOLDOUT_FRACTION = 0.2
NEGATIVE_SAMPLES_PER_POSITIVE = 3
SGD_EPOCHS = 40
SGD_LEARNING_RATE = 0.08
SGD_L2 = 1e-4
# A trained model must clear this on held-out data AND beat the incumbent
# before it is allowed to serve traffic.
PROMOTION_MIN_AUC = 0.55

# Ranking feature order. Adding a feature here means appending to PRIOR_WEIGHTS
# too; the module-level assert at the bottom enforces it.
FEATURE_NAMES = (
    "content_cos",
    "cf_score",
    "artist_affinity",
    "genre_affinity",
    "language_affinity",
    "mood_affinity",
    "popularity",
    "global_completion_rate",
    "global_skip_rate",
    "is_repeat",
    "plays_by_user",
    "recency_decay",
    "lang_pref_match",
    "genre_pref_match",
    "artist_pref_match",
    "explicit_penalty",
    "duration_dev",
    "artist_followed",
    "album_saved",
    "in_user_playlist",
    "novelty",
    "src_content",
    "src_cf",
    "src_artist_genre",
    "src_popular",
    "src_playlist",
)

# Hand-tuned prior. This is what serves before any model is trained, and what
# a trained model must beat on held-out data to replace. Signs encode domain
# knowledge: a high global skip rate is bad, novelty is mildly good (discovery),
# and an explicit track for a user who disabled explicit content is heavily
# penalised.
PRIOR_WEIGHTS = {
    "content_cos": 2.20,
    "cf_score": 1.80,
    "artist_affinity": 1.40,
    "genre_affinity": 0.90,
    "language_affinity": 0.70,
    "mood_affinity": 0.45,
    "popularity": 0.55,
    "global_completion_rate": 0.60,
    "global_skip_rate": -0.90,
    "is_repeat": 0.25,
    "plays_by_user": 0.30,
    "recency_decay": -0.35,
    "lang_pref_match": 0.65,
    "genre_pref_match": 0.55,
    # Artist is the strongest content signal (see FIELD_WEIGHTS) and a
    # declared favorite is an explicit choice, not an inferred one -- weighted
    # above genre/language but below artist_followed, which is a stronger,
    # separately-taken action.
    "artist_pref_match": 0.85,
    "explicit_penalty": -2.50,
    "duration_dev": -0.30,
    "artist_followed": 1.10,
    "album_saved": 0.70,
    "in_user_playlist": 0.80,
    "novelty": 0.20,
    "src_content": 0.15,
    "src_cf": 0.20,
    "src_artist_genre": 0.05,
    "src_popular": 0.00,
    "src_playlist": 0.10,
}
PRIOR_BIAS = -0.50

CANDIDATE_SOURCES = ("content", "cf", "artist_genre", "popular", "playlist")

# Search re-ranking. Lexical relevance has to dominate personalization: a user
# who types an exact title wants that title, not their favourite artist.
SEARCH_LEXICAL_WEIGHT = 3.0
SEARCH_PERSONAL_WEIGHT = 1.0
SEARCH_POPULARITY_WEIGHT = 0.35

# Serving cache TTLs (seconds).
TASTE_VECTOR_TTL = 900
ITEM_SIM_TTL = 3600

assert set(FEATURE_NAMES) == set(PRIOR_WEIGHTS), (
    "PRIOR_WEIGHTS must cover exactly FEATURE_NAMES; missing="
    f"{sorted(set(FEATURE_NAMES) - set(PRIOR_WEIGHTS))} "
    f"extra={sorted(set(PRIOR_WEIGHTS) - set(FEATURE_NAMES))}"
)

PRIOR_WEIGHT_VECTOR = [PRIOR_WEIGHTS[name] for name in FEATURE_NAMES]

# Coefficients that training must leave at their prior value.
#
# The `src_*` indicators say which retrieval source produced a candidate, and
# that is knowable only when retrieval actually ran. Training labels come from
# rows in listening_history / liked_songs, which record what the user did -- not
# which shelf surfaced the song -- so there is no honest way to reconstruct the
# flags for a historical interaction. Fabricating them (e.g. "high content_cos
# implies src_content") would teach the model a correlation that exists only in
# the fabrication.
#
# So training sets them to 0 and skips them in both the gradient and the L2 term,
# leaving the hand-tuned prior in place. Once impression logging exists these can
# be unfrozen and learned properly.
FROZEN_FEATURE_INDICES = frozenset(
    i for i, name in enumerate(FEATURE_NAMES) if name.startswith("src_")
)
