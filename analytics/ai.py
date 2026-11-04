"""Optional AI research assistant (Anthropic Claude via the official SDK).

Guarantees:
* Only aggregated statistics and actual open-ended answers (with response IDs)
  are sent; no participant names or demographics beyond aggregates.
* Output is stored as a DRAFT interpretation, never mixed with statistics,
  and must be reviewed/approved by the researcher.
* When the provider is not configured or fails, the platform keeps working.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

from surveys.models import Question

from . import stats
from .models import AIAnalysis

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TEXT_ANSWERS_PER_QUESTION = 60
MAX_CHARS_PER_ANSWER = 600

SYSTEM_PROMPT = """You are a careful research methodology assistant for an academic survey platform.
You receive aggregated survey statistics and verbatim open-ended answers (each tagged with a response ID).

Strict rules:
- Use ONLY the data provided. Never invent numbers, participants, quotes or results.
- When you cite a number, it must appear in the data. When you quote an answer, include its response ID, e.g. (R-000012).
- Do not claim statistical significance unless a test result with a p-value is present in the data; then name the test.
- Always mention sample sizes (N or n) when describing results; flag small samples (n < 30) as a limitation.
- Clearly separate description of the data from interpretation; phrase interpretations as possibilities, not facts.
- If the data are insufficient for the request, say so.
- Write in the language requested by the researcher. Use plain academic style, short paragraphs and bullet lists."""

TASK_INSTRUCTIONS = {
    AIAnalysis.Task.EXPLAIN: "Explain the main statistical results in plain academic language for a researcher.",
    AIAnalysis.Task.SUMMARIZE: "Summarize what respondents said, including the open-ended answers, with representative quotes and their response IDs.",
    AIAnalysis.Task.PATTERNS: "Identify possible patterns or relationships worth checking. Label each as a hypothesis and say which test would verify it.",
    AIAnalysis.Task.METHODS: "Suggest appropriate analytical approaches and statistical tests for these data, with justification and assumptions to check.",
    AIAnalysis.Task.CONCLUSIONS: "Draft cautious research conclusions strictly supported by the data, followed by limitations.",
    AIAnalysis.Task.DISCUSSION: "Suggest discussion points for the research report, connecting results to possible explanations and further research. Mark speculation clearly.",
}

LANGUAGE_NAMES = {"tg": "Tajik", "ru": "Russian", "en": "English"}


class AIUnavailable(Exception):
    pass


def is_configured() -> bool:
    return settings.AI_PROVIDER == "anthropic" and bool(settings.AI_API_KEY)


def build_context(questionnaire, question_ids: list[int] | None = None) -> dict[str, Any]:
    ds = stats.load_dataset(questionnaire)
    overview = stats.overview(ds)
    items = []
    for q in ds.questions:
        if question_ids and q.pk not in question_ids:
            continue
        s = stats.question_summary(ds, q)
        entry: dict[str, Any] = {
            "number": q.number or q.code,
            "question": q.text,
            "type": q.get_qtype_display(),
            "N": s["N"],
            "n_answered": s["n"],
            "missing": s["missing"],
        }
        if s.get("rows"):
            entry["distribution"] = [{"option": r["option"], "count": r["count"], "percent": r["pct"]} for r in s["rows"]]
        if s.get("stats"):
            entry["statistics"] = s["stats"]
        if q.is_text:
            entry["answers"] = [
                {"response_id": f"R-{rid:06d}", "text": t[:MAX_CHARS_PER_ANSWER]}
                for rid, t in s.get("texts", [])[:MAX_TEXT_ANSWERS_PER_QUESTION]
            ]
            entry["answers_included"] = len(entry["answers"])
        if q.qtype == Question.Type.MATRIX and s.get("matrix"):
            entry["matrix"] = s["matrix"]
        if q.qtype == Question.Type.RANKING and s.get("ranking"):
            entry["ranking"] = s["ranking"]
        items.append(entry)
    return {
        "questionnaire": {"title": questionnaire.title, "objective": questionnaire.objective, "language": questionnaire.language},
        "sample": {"completed_responses_N": overview["N"], "started": overview["started"], "completion_rate_percent": overview["completion_rate"]},
        "questions": items,
        "section_means": stats.section_comparison(ds)["rows"],
    }


def run_analysis(questionnaire, task: str, user, researcher_prompt: str = "", question_ids: list[int] | None = None, language: str = "tg") -> AIAnalysis:
    context = build_context(questionnaire, question_ids)
    analysis = AIAnalysis(
        questionnaire=questionnaire,
        task=task,
        researcher_prompt=researcher_prompt[:4000],
        context_data=context,
        provider=settings.AI_PROVIDER,
        model_name=settings.AI_MODEL or DEFAULT_MODEL,
        created_by=user,
    )
    if not is_configured():
        analysis.error = _("The AI assistant is not configured (set AI_PROVIDER and AI_API_KEY).")
        analysis.save()
        return analysis
    if context["sample"]["completed_responses_N"] == 0:
        analysis.error = _("There are no completed responses to analyse.")
        analysis.save()
        return analysis
    try:
        analysis.output_text = _call_anthropic(task, context, researcher_prompt, language)
    except AIUnavailable as exc:
        analysis.error = str(exc)
    analysis.save()
    return analysis


def _call_anthropic(task: str, context: dict, researcher_prompt: str, language: str) -> str:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise AIUnavailable(_("The anthropic package is not installed.")) from exc

    client = anthropic.Anthropic(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL or None,
        timeout=float(settings.AI_TIMEOUT_SECONDS),
        max_retries=2,
    )
    user_content = (
        f"Task: {TASK_INSTRUCTIONS.get(task, TASK_INSTRUCTIONS[AIAnalysis.Task.EXPLAIN])}\n"
        f"Write the answer in {LANGUAGE_NAMES.get(language, 'English')}.\n"
        + (f"Additional request from the researcher: {researcher_prompt}\n" if researcher_prompt else "")
        + "\n<survey_data>\n"
        + json.dumps(context, ensure_ascii=False, default=str)
        + "\n</survey_data>"
    )
    try:
        response = client.beta.messages.create(
            model=settings.AI_MODEL or DEFAULT_MODEL,
            max_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        logger.warning("AI authentication failed")
        raise AIUnavailable(_("The AI provider rejected the API key.")) from exc
    except anthropic.RateLimitError as exc:
        raise AIUnavailable(_("The AI provider rate limit was reached. Try again later.")) from exc
    except anthropic.BadRequestError as exc:
        logger.warning("AI bad request: %s", getattr(exc, "message", exc))
        raise AIUnavailable(_("The AI provider rejected the request.")) from exc
    except anthropic.APIStatusError as exc:
        logger.warning("AI API error %s", exc.status_code)
        raise AIUnavailable(_("The AI provider returned an error. Try again later.")) from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailable(_("The AI provider could not be reached.")) from exc

    if response.stop_reason == "refusal":
        raise AIUnavailable(_("The AI provider declined this request."))
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIUnavailable(_("The AI provider returned an empty answer."))
    if response.stop_reason == "max_tokens":
        text += "\n\n[" + _("Output truncated at the configured token limit.") + "]"
    return text


def review(analysis: AIAnalysis, user, *, approve: bool, edited_text: str | None = None) -> None:
    from .models import ReviewStatus

    if edited_text is not None:
        analysis.edited_text = edited_text
    analysis.status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
    analysis.reviewed_by = user
    analysis.reviewed_at = timezone.now()
    analysis.save(update_fields=["edited_text", "status", "reviewed_by", "reviewed_at"])
