"""Baseline: TF-IDF over word and character n-grams + logistic regression (no neural network).

If a model cannot beat this, it is not learning anything a bag of words does not already see.
Usage: m_tfidf.py [train_cap]
"""
import sys

import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

import common as C


def main(train_cap=300, exclude=None):
    sp = C.splits(C.load(), train_cap=train_cap)
    tr, va = sp["train"], sp["val"]
    if exclude:   # leave-one-attack-out: the family is never seen in training or threshold picking
        tr = [r for r in tr if r["family"] != exclude]
        va = [r for r in va if r["family"] != exclude]
    word = TfidfVectorizer(token_pattern=r"[A-Za-z_][\w./-]+|<\w+>", ngram_range=(1, 2), min_df=2, max_features=200000,
                           sublinear_tf=True)
    char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, max_features=200000, sublinear_tf=True)
    t = C.Timer()
    Xtr = hstack([word.fit_transform([r["text"] for r in tr]), char.fit_transform([r["text"] for r in tr])]).tocsr()

    def X(rows):
        return hstack([word.transform([r["text"] for r in rows]), char.transform([r["text"] for r in rows])]).tocsr()

    ytr = np.array([r["malicious"] for r in tr])
    ttr = np.array([C.TACTICS.index(r["tactic"]) for r in tr])
    Xva = X(va)
    yva = np.array([r["malicious"] for r in va])
    best = None
    for c in (0.3, 1, 3, 10, 30):      # pick C on val AUROC
        m = LogisticRegression(C=c, max_iter=3000, class_weight="balanced").fit(Xtr, ytr)
        a = C.auroc(yva.astype(float), m.predict_proba(Xva)[:, 1], np.array([r["w"] for r in va]))
        print(f"C={c} val auroc={a:.4f}", flush=True)
        if best is None or a > best[0]:
            best = (a, c, m)
    _, c, mal = best
    tac = LogisticRegression(C=c, max_iter=3000, class_weight="balanced").fit(Xtr, ttr)
    train_s = t()

    def tactic_probs(Xs):
        p = np.zeros((Xs.shape[0], len(C.TACTICS)))
        p[:, tac.classes_] = tac.predict_proba(Xs)
        return p

    val_p = mal.predict_proba(Xva)[:, 1]
    res, scores = {}, {}
    for name in ("test_core", "test_full"):
        rows = sp[name]
        t = C.Timer()
        Xs = X(rows)
        p = mal.predict_proba(Xs)[:, 1]
        tp = tactic_probs(Xs)
        secs = t()
        res[name] = C.evaluate(rows, p, tp, val=(va, val_p))
        res[name]["ms_per_window_cpu"] = 1000 * secs / len(rows)
        scores[name] = p
    names = np.array(word.get_feature_names_out().tolist() + char.get_feature_names_out().tolist())
    coef = mal.coef_[0]
    info = {"train_windows": len(tr), "C": c, "train_seconds": train_s, "exclude_family": exclude,
            "top_malicious_features": names[np.argsort(-coef)[:40]].tolist(),
            "top_benign_features": names[np.argsort(coef)[:40]].tolist()}
    C.save(f"tfidf_cap{train_cap}" + (f"_loao_{exclude}" if exclude else ""), info, res, scores)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300, sys.argv[2] if len(sys.argv) > 2 else None)
