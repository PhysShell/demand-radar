"""Analyst (Claude) classification prompt — one evidence item in, one
Classification out. See schemas/classification.schema.json for the schema
passed as this call's --json-schema/--output-schema.
"""

from __future__ import annotations

from demand_radar.agents.prompts.trust import TRUST_PREAMBLE, wrap_untrusted
from demand_radar.config import ProductConfig
from demand_radar.models import EvidenceItem


def build_classification_prompt(evidence: EvidenceItem, product: ProductConfig) -> str:
    positive = "\n".join(f"- {p}" for p in product.positive_examples) or "(none provided)"
    negative = "\n".join(f"- {n}" for n in product.negative_examples) or "(none provided)"
    personas = ", ".join(product.personas) or "(none provided)"

    task = f"""\
TASK: classify exactly one piece of evidence for the product "{product.display_name}".

Known personas for this product: {personas}

Examples of RELEVANT content for this product (relevant=true candidates):
{positive}

Examples of NOT relevant content for this product (relevant=false; news, \
tutorials, generic praise, off-topic chatter):
{negative}

Extract, grounded ONLY in the evidence text below -- never invent details \
not present in it:
- relevance: is this item on-topic and analytically useful for this \
product (product_fit, confidence, relevant)? Generic praise, tutorials, \
interview questions, and unrelated news are NOT relevant even if they \
mention the product's technology.
- persona: the closest matching persona label for whoever is speaking.
- situation: one short sentence describing their context.
- problem: one short sentence stating the problem, plus a short slug \
problem_key (lowercase, hyphen-separated) that would group this with other \
evidence describing the SAME underlying problem -- reuse an obvious \
existing slug pattern rather than inventing a needlessly novel one.
- desired_outcome: one short sentence on what they actually want.
- current_workarounds: any manual workaround they describe using today \
(empty list if none is described -- do not invent one).
- signals: pain, urgency, commercial_intent, commitment, each 0.0-1.0. \
commitment means an explicit signal of willingness to pay/adopt now (e.g. \
"we've budgeted for this"), not general enthusiasm.
- mentioned_tools: any named tools/products mentioned.
- evidence_spans: for every non-empty claim above, at least one \
{{start, end, supports}} character-offset span into the evidence text \
below that grounds it. A claim with no supporting span should not be \
asserted.

Do not summarize or retell the evidence text at length -- every field is a \
short structured claim, not a paraphrase of the whole post. If the item is \
not relevant, still fill every required field with the shortest honest \
placeholder (e.g. situation "not applicable") and set relevant=false; do \
not omit required fields.
"""

    evidence_block = wrap_untrusted(
        evidence.id,
        f"language: {evidence.content.language}\ntext:\n{evidence.content.raw_text}",
    )

    return (
        f"{TRUST_PREAMBLE}\n\n{task}\n{evidence_block}\n"
        f"evidence_id to use in your response: {evidence.id}\n"
    )
