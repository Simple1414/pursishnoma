"""Shared test factories (real PostgreSQL rows, no mocks of the data layer)."""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import docx
from django.utils import timezone

from core.models import User
from surveys import services
from surveys.models import AnswerOption, Question, Questionnaire, Section, SurveyResponse

PASSWORD = "Str0ng-Test-Passw0rd!"


def make_user(username: str, role: str = User.Role.RESEARCHER, **extra) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role, **extra)


def make_questionnaire(owner: User, *, published: bool = True, title: str = "Study") -> Questionnaire:
    qn = Questionnaire.objects.create(title=title, owner=owner, objective="Objective", consent_text="Consent")
    s1 = Section.objects.create(questionnaire=qn, title="Part I", order=0)
    s2 = Section.objects.create(questionnaire=qn, title="Part II", order=1)

    def q(section, number, qtype, options=(), required=True, **kw):
        question = Question.objects.create(
            questionnaire=qn, section=section, number=str(number), code=f"Q{number}", text=f"Question {number}",
            qtype=qtype, required=required, order=number, **kw,
        )
        for i, (text, value, kind, other) in enumerate(options):
            AnswerOption.objects.create(question=question, text=text, value=value, kind=kind, allows_text=other, order=i)
        return question

    O = AnswerOption.Kind.OPTION
    q(s1, 1, Question.Type.SINGLE, [("Male", 1, O, False), ("Female", 2, O, False)])
    q(s1, 2, Question.Type.MULTIPLE, [("A", 1, O, False), ("B", 2, O, False), ("Other", 3, O, True)])
    q(s1, 3, Question.Type.YES_NO, [("Yes", 1, O, False), ("No", 0, O, False)])
    q(s2, 4, Question.Type.LIKERT, [(str(v), v, O, False) for v in range(1, 6)])
    q(s2, 5, Question.Type.LIKERT, [(str(v), v, O, False) for v in range(1, 6)])
    q(s2, 6, Question.Type.RATING, scale_min=1, scale_max=10)
    q(s2, 7, Question.Type.NUMBER, required=False)
    q(s2, 8, Question.Type.DATE, required=False)
    q(s2, 9, Question.Type.LONG_TEXT, required=False)
    q(s2, 10, Question.Type.MATRIX, [("Row A", None, AnswerOption.Kind.ROW, False), ("Row B", None, AnswerOption.Kind.ROW, False),
                                     ("Low", 1, AnswerOption.Kind.COLUMN, False), ("High", 2, AnswerOption.Kind.COLUMN, False)], required=False)
    q(s2, 11, Question.Type.RANKING, [("X", 1, O, False), ("Y", 2, O, False), ("Z", 3, O, False)], required=False)
    q(s2, 12, Question.Type.SHORT_TEXT, required=False)
    services.ensure_participant_fields(qn)
    if published:
        qn.status = Questionnaire.Status.PUBLISHED
        qn.approved_at = timezone.now()
        qn.approved_by = owner
        qn.published_at = timezone.now()
        qn.save()
    return qn


def question(qn: Questionnaire, number: int) -> Question:
    return qn.questions.get(number=str(number))


def option(qn: Questionnaire, number: int, text: str) -> AnswerOption:
    return question(qn, number).options.get(text=text)


def valid_post(qn: Questionnaire, *, gender="Male", likert=(4, 5), rating=7, text="", multiple=("A",)) -> dict:
    data = {
        f"q_{question(qn, 1).pk}": str(option(qn, 1, gender).pk),
        f"q_{question(qn, 2).pk}": [str(option(qn, 2, m).pk) for m in multiple],
        f"q_{question(qn, 3).pk}": str(option(qn, 3, "Yes").pk),
        f"q_{question(qn, 4).pk}": str(option(qn, 4, str(likert[0])).pk),
        f"q_{question(qn, 5).pk}": str(option(qn, 5, str(likert[1])).pk),
        f"q_{question(qn, 6).pk}": str(rating),
    }
    if text:
        data[f"q_{question(qn, 9).pk}"] = text
    return data


def submit_response(qn: Questionnaire, data: dict, *, faculty: str = "", year: str = "") -> SurveyResponse:
    """Create a completed response through the service layer (same path as the views)."""
    from django.http import QueryDict

    response = services.start_response(qn, {"faculty": faculty, "academic_year": year} if (faculty or year) else {}, consent=True, ip_hash="", language="en")
    qd = QueryDict(mutable=True)
    for k, v in data.items():
        if isinstance(v, (list, tuple)):
            qd.setlist(k, list(v))
        else:
            qd[k] = v
    ok, parsed = services.submit(response, qd, None)
    assert ok, parsed.errors
    response.refresh_from_db()
    return response


# --------------------------------------------------------------------------- docx builders
def docx_bytes(build) -> BytesIO:
    document = docx.Document()
    build(document)
    buf = BytesIO()
    document.save(buf)
    buf.seek(0)
    buf.name = "questionnaire.docx"
    return buf


def build_original_style(d):
    """Mirrors the structure of the real BSU questionnaire (paragraph options, banner tables)."""
    d.add_paragraph("ДОНИШГОҲИ ДАВЛАТИИ БОХТАР")
    d.add_paragraph("«ИСТИФОДАИ ЗЕҲНИ СУНЪӢ ДАР ОМӮЗИШИ ФАНҲОИ ДАҚИҚ»")
    d.add_paragraph("Муҳтарам респондент!")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Ному насаби респондент"
    t.cell(0, 1).text = "________________"
    t.cell(1, 0).text = "Курс, синну сол, ҷинс"
    t.cell(1, 1).text = "Курс: ____ Синну сол: ____"
    d.add_table(rows=1, cols=1).cell(0, 0).text = "ҚИСМИ I. МАЪЛУМОТИ УМУМӢ"
    d.add_paragraph("Ин бахш барои муайян намудан.")
    d.add_paragraph("1. Шакли таҳсил:")
    d.add_paragraph("□ рӯзона    □ ғоибона    □ фосилавӣ/омехта")
    d.add_paragraph("2. Кадом воситаҳо? (чанд вариант мумкин аст)")
    d.add_paragraph("□ ChatGPT    □ Gemini    □ дигар: __________")
    d.add_paragraph("3. Оё истифода мебаред?")
    d.add_paragraph("□ Ҳа    □ Не")
    d.add_paragraph("Барои саволҳои 4-5 аз миқёси зерин истифода баред:")
    legend = d.add_table(rows=1, cols=5)
    for i, label in enumerate(["комилан розӣ нестам", "розӣ нестам", "бетараф", "розӣ ҳастам", "комилан розӣ ҳастам"], start=1):
        legend.cell(0, i - 1).text = f"{i} - {label}"
    d.add_paragraph("A. Дарки фоида")
    grid = d.add_table(rows=3, cols=7)
    for j, h in enumerate(["№", "Мазмуни изҳорот", "1", "2", "3", "4", "5"]):
        grid.cell(0, j).text = h
    grid.cell(1, 0).text = "4"
    grid.cell(1, 1).text = "Зеҳни сунъӣ ба ман кӯмак мерасонад."
    grid.cell(2, 0).text = "5"
    grid.cell(2, 1).text = "Шарҳи қадам ба қадам муфид аст."
    for r in (1, 2):
        for c in range(2, 7):
            grid.cell(r, c).text = "□"
    d.add_table(rows=1, cols=1).cell(0, 0).text = "ҚИСМИ II. САВОЛҲОИ КУШОДА"
    d.add_paragraph("6. Ба фикри шумо, зеҳни сунъӣ дар кадом ҷанбаҳо муфид аст?")
    d.add_table(rows=1, cols=1)
    d.add_paragraph("7. Пешниҳоди Шумо (ихтиёрӣ)")
    d.add_table(rows=1, cols=1).cell(0, 0).text = "ҚИСМИ III. ЁДДАШТ БАРОИ МУҲАҚҚИҚ"
    d.add_paragraph("99. This researcher-only line must be ignored")
