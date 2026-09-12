"""Did the agent get the answer right?

The outcome reward, and the one place answer strings are compared. It uses the
SQuAD/HotpotQA normalisation — lowercase, strip punctuation, drop articles,
collapse whitespace — because those are the metrics HotpotQA's own evaluation
script defines, and inventing a different one would make every number here
incomparable with the dataset it came from for no gain.

Two properties this module must keep:

**No LLM judge on the main path.** The answers are short spans, dates, names and
yes/no; exact match after normalisation decides almost all of them correctly, it
costs nothing, and it is deterministic — so the same trajectory scores the same
today and in six weeks. A judge would introduce a second model whose drift would
silently move every reported number.

**Nothing here reads the trajectory.** It compares two strings. Whether the
agent actually earned the answer — cited real documents, did not simply
remember it — is a separate question answered by `grounding.py` and
`antigaming.py`. Keeping them apart is what lets the reward be recomposed for
an ablation without touching the correctness definition.
"""
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

# Yes/no questions are ~7% of HotpotQA and their answers are exactly these two
# strings. A model that writes "Yes, they are." is right, so a bare `==` after
# normalisation would score it wrong; these are matched by prefix instead.
YES_NO = ("yes", "no")


def normalize(text: str) -> str:
    """The SQuAD/HotpotQA answer normalisation, with one deliberate deviation.

    The official script strips `string.punctuation`, which is ASCII-only. The
    corpus is Wikipedia, where en-dashes are everywhere — "Trenton–Mercer
    Airport", "a record of 13–3", "26–30 August 1914" — so an ASCII strip leaves
    U+2013 in place and a model that typed a hyphen scores wrong for a
    difference no one would call an error.

    Stripping by Unicode category instead fixes that. Measured before changing
    it: 0.49% of gold answers carry non-ASCII punctuation and 1 verdict in 300
    flips. Small, but this is a reward function — a systematic error at that
    rate is worth three lines, and the direction of the bias is always against
    the model.

    Deviating from the reference implementation is noted here because it makes
    these numbers very slightly stricter-than-official in one direction and
    looser in another; absolute comparability with published HotpotQA results
    is already ruled out by the retrieval setting (see `rl/README.md`).
    """
    text = text.lower()
    text = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch for ch in text
    )
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def _tokens(text: str) -> list[str]:
    return normalize(text).split()


@dataclass(frozen=True)
class Outcome:
    """How a predicted answer scored against the gold one."""

    exact_match: bool
    f1: float
    predicted: str
    gold: str

    @property
    def reward(self) -> float:
        """The scalar the RL loop sees.

        Exact match, not F1. A partial-credit outcome reward is an invitation to
        hedge: padding an answer with plausible extra tokens raises expected F1
        while making the answer worse. F1 is reported alongside because it is
        the standard secondary metric and it is informative when diagnosing
        near-misses, but it does not drive training.
        """
        return 1.0 if self.exact_match else 0.0


def score(predicted: str, gold: str) -> Outcome:
    pred_norm, gold_norm = normalize(predicted), normalize(gold)

    if gold_norm in YES_NO:
        # "Yes, both were directors." counts. A bare equality check would not,
        # and the model has no way to know we wanted the bare token.
        first = pred_norm.split()[0] if pred_norm.split() else ""
        em = first == gold_norm
        return Outcome(em, 1.0 if em else 0.0, predicted, gold)

    em = pred_norm == gold_norm
    return Outcome(em, _f1(pred_norm, gold_norm), predicted, gold)


def _f1(pred_norm: str, gold_norm: str) -> float:
    pred, gold = pred_norm.split(), gold_norm.split()
    if not pred or not gold:
        return float(pred == gold)

    common = Counter(pred) & Counter(gold)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(gold)
    return 2 * precision * recall / (precision + recall)


def aggregate(outcomes: list[Outcome]) -> dict:
    """Corpus-level EM / F1, the pair HotpotQA reports."""
    n = max(len(outcomes), 1)
    return {
        "n": len(outcomes),
        "em": sum(1 for o in outcomes if o.exact_match) / n,
        "f1": sum(o.f1 for o in outcomes) / n,
    }
