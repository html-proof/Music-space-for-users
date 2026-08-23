"""Machine-learned recommendation, ranking and search components.

The pipeline is the standard three-stage recommender shape:

    candidates.py  ->  features.py  ->  ranker.py  ->  diversify.py
    (retrieval)        (feature build)   (scoring)      (post-process)

`ranker.py` scores with hand-tuned prior weights until `training.py` produces a
model that clears the promotion gate, at which point learned coefficients take
over through the same code path. That is what makes the system useful on a
near-empty database and better as interactions accumulate, without a rewrite.

Nothing here is imported at app startup beyond `config`; the heavier modules are
imported by the services that use them, so `ML_ENABLED=False` keeps the whole
subsystem out of the request path.
"""
