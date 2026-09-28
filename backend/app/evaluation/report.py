"""Render an evaluation result (the JSON written by evaluate.py) as a Markdown report."""

import json
from typing import Any


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_markdown(result: dict[str, Any]) -> str:
    # Same shape whether the result is fresh or loaded from JSON (where the k keys are strings).
    result = json.loads(json.dumps(result, default=str))
    meta, lines = result["meta"], []
    lines += [
        f"# RAG evaluation: {meta['dataset']} v{meta['dataset_version']}",
        "",
        f"- Run: {meta['started_at']} ({meta['duration_seconds']} s)",
        f"- Corpus: {meta['corpus']['documents']} documents, {meta['corpus']['chunks']} chunks "
        f"(fingerprint `{meta['corpus']['fingerprint']}`)",
        f"- Questions: {meta['questions']['total']} ({meta['questions']['answerable']} answerable, "
        f"{meta['questions']['unanswerable']} unanswerable)",
        "- Settings: " + ", ".join(f"{k}={v}" for k, v in meta["settings"].items()),
        "",
    ]

    retrieval = result.get("retrieval") or {}
    if retrieval:
        ks = sorted(next(iter(retrieval.values()))["overall"].get("hit", {}), key=int)
        lines += ["## Retrieval (answerable questions)", ""]
        header = ["Configuration", *[f"Hit@{k}" for k in ks], *[f"Recall@{k}" for k in ks], "MRR"]
        header += [f"nDCG@{ks[-1]}", "p50 ms", "p95 ms"]
        lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        for name, run in retrieval.items():
            o = run["overall"]
            cells = [f"**{name}**", *[_pct(o["hit"][k]) for k in ks], *[_pct(o["recall"][k]) for k in ks]]
            cells += [
                _num(o["mrr"]),
                _num(o["ndcg"][ks[-1]]),
                str(run["latency_ms"]["p50"]),
                str(run["latency_ms"]["p95"]),
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

        k_mid = "3" if "3" in ks else ks[0]
        categories = sorted({c for run in retrieval.values() for c in run["by_category"]})
        lines += [f"### Recall@{k_mid} by question category", ""]
        header = [
            "Configuration",
            *[f"{c} ({next(iter(retrieval.values()))['by_category'][c]['questions']})" for c in categories],
        ]
        lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        for name, run in retrieval.items():
            cells = [f"**{name}**", *[_pct(run["by_category"][c]["recall"][k_mid]) for c in categories]]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

        lines += [
            "### Unanswerable questions at retrieval",
            "",
            "| Configuration | Nothing retrieved | Mean chunks retrieved |",
            "|---|---|---|",
        ]
        for name, run in retrieval.items():
            u = run["unanswerable"]
            if u.get("questions"):
                lines.append(
                    f"| **{name}** | {_pct(u['empty_retrieval_rate'])} | {_num(u['mean_retrieved'], 1)} |"
                )
        lines.append("")

        separations = [(name, run.get("answerability_separation")) for name, run in retrieval.items()]
        separations = [(name, sep) for name, sep in separations if sep]
        if separations:
            lines += [
                "### Can a score threshold detect unanswerable questions?",
                "",
                "Top result's score: lowest among answerable questions vs highest among unanswerable ones.",
                "",
                "| Configuration | Score | Answerable min | Unanswerable max | Separable |",
                "|---|---|---|---|---|",
            ]
            for name, sep in separations:
                verdict = (
                    "yes" if sep["separable"] else f"no ({sep['unanswerable_above_answerable_min']} overlap)"
                )
                cells = [
                    f"**{name}**",
                    sep["score"],
                    str(sep["answerable_min"]),
                    str(sep["unanswerable_max"]),
                    verdict,
                ]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

        misses = [
            (name, q)
            for name, run in retrieval.items()
            for q in run["questions"]
            if "mrr" in q and q["recall"][ks[-1]] < 1
        ]
        if misses:
            lines += [f"### Misses (evidence not fully retrieved in the top {ks[-1]})", ""]
            for name, q in misses:
                recall = _pct(q["recall"][ks[-1]])
                lines.append(f"- `{name}` {q['id']} ({q['category']}): {q['question']} (recall {recall})")
            lines.append("")

    answers = result.get("answers")
    if answers:
        a, u = answers["answerable"], answers["unanswerable"]
        lines += [
            "## Answers (production pipeline: hybrid + rerank + Claude)",
            "",
            f"Model `{meta['settings'].get('model')}`, judge `{meta.get('judge_model') or 'none'}`. "
            f"{answers['questions']} questions, {answers['errors']} errors, "
            f"{answers['tokens']['input']} input / {answers['tokens']['output']} output tokens.",
            "",
            "| Metric | Value | How it is measured |",
            "|---|---|---|",
        ]
        latency = f"{answers['latency_ms']['p50']} / {answers['latency_ms']['p95']} ms"
        rows = [
            ('Answered (not "not found")', _pct(a["answered_rate"]), "answerable questions"),
            ("Fact recall", _pct(a["fact_recall"]), "expected facts stated in the answer (string match)"),
            (
                "Faithfulness",
                _pct(a["faithfulness"]),
                "claims supported by the retrieved passages (LLM judge)",
            ),
            ("Answer relevance", _pct(a["answer_relevance"]), "judge rating 1-5, scaled to 0-100%"),
            ("Answers with citations", _pct(a["cited_rate"]), "answered questions"),
            (
                "Quotes verified",
                _pct(a["quote_verification"]),
                "cited quotes found verbatim in the cited source",
            ),
            (
                "Cited sources relevant",
                _pct(a["cited_source_relevance"]),
                "cited sources with labelled evidence",
            ),
            (
                "Evidence cited",
                _pct(a["evidence_citation_recall"]),
                "labelled evidence covered by the citations",
            ),
            ("Correct abstention", _pct(u["abstention_rate"]), 'unanswerable questions answered "not found"'),
            ("Latency p50 / p95", latency, "per question, end to end"),
        ]
        lines += [f"| {label} | {value} | {how} |" for label, value, how in rows]
        lines.append("")
        if a["judge_errors"]:
            lines += [f"{a['judge_errors']} answers could not be judged (see the JSON details).", ""]
    return "\n".join(lines).rstrip() + "\n"
