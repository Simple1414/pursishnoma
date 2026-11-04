"""Import a questionnaire from a Word (.docx) document.

The importer is deliberately conservative: it copies question and option
wording verbatim, never invents content, and records everything it could
not interpret in an ``import report`` so the researcher can review and
correct the draft before approval.

Recognised structure (Tajik / Russian / English):

* Section headings — paragraphs or single-cell tables starting with
  ``ҚИСМИ``, ``БАХШИ``, ``ЧАСТЬ``, ``РАЗДЕЛ``, ``PART`` or ``SECTION``;
  sub-sections like ``A. Title``.
* Questions — paragraphs starting with a number: ``12. Text`` / ``12) Text``.
* Options — check boxes (□ ☐ ◻ ▢ ❑) in the following paragraphs or tables.
* Likert grids — tables whose header ends with consecutive numbers
  (``1 2 3 4 5``); every row becomes one Likert item, labels are taken from
  a preceding legend such as ``1 - strongly disagree``.
* Appendix / researcher-only parts (``ЗАМИМА``, ``ЁДДАШТ БАРОИ МУҲАҚҚИҚ``,
  ``ПРИЛОЖЕНИЕ``, ``APPENDIX``) stop the import.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import IO, Iterator

import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

BOX_CHARS = "□☐◻▢❑⬜"
BOX_RE = re.compile(f"[{BOX_CHARS}]")
QUESTION_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+(\S.*)$", re.S)
SECTION_RE = re.compile(r"^\s*(ҚИСМИ|ҚИСМ|БАХШИ|ЧАСТЬ|РАЗДЕЛ|PART|SECTION)\b", re.I)
SUBSECTION_RE = re.compile(r"^\s*([A-ZА-ЯЁ])\.\s+(\S.{2,150})$")
STOP_RE = re.compile(
    r"(ЗАМИМА|ЁДДАШТ\s+БАРОИ\s+МУҲАҚҚИҚ|БАРОИ\s+МУҲАҚҚИҚ|ПРИЛОЖЕНИЕ|ДЛЯ\s+ИССЛЕДОВАТЕЛЯ|APPENDIX|FOR\s+THE\s+RESEARCHER)",
    re.I,
)
LEGEND_RE = re.compile(r"(\d{1,2})\s*[-–—=:]\s*([^\d|]+)")
MULTI_RE = re.compile(
    r"чанд\s+(ҷавоб|вариант)|якчанд\s+вариант|зиёда\s+аз\s+як|несколько\s+(вариант|ответ)|"
    r"все\s+подходящие|multiple|select\s+all|choose\s+all",
    re.I,
)
OPTIONAL_RE = re.compile(r"\((ихтиёрӣ|необязательно|optional)\)", re.I)
REQUIRED_RE = re.compile(r"\((ҳатмӣ|обязательно|required)\)|\*\s*$", re.I)
OTHER_RE = re.compile(r"^(дигар|другое|иное|other)\b", re.I)
FILL_LINE_RE = re.compile(r"_{4,}")
TITLE_RE = re.compile(r"«([^»]{10,})»", re.S)
YES_WORDS = {"ҳа", "да", "yes"}
NO_WORDS = {"не", "нет", "no"}

PARTICIPANT_HINTS = {
    key: re.compile(pattern, re.I)
    for key, pattern in {
        "name": r"\bному\s+насаб|\bф\.?\s?и\.?\s?о\b|\bfull\s+name\b|\bимя\b",
        "university": r"\bдонишгоҳ|\bмуассиса|\bуниверситет|\buniversity\b|\bвуз\b",
        "faculty": r"\bфакул[ь]?тет|\bfaculty\b",
        "specialty": r"\bихтисос|\bспециальност|\bspecialty\b|\bmajor\b",
        "academic_year": r"\bкурс|\bacademic\s+year\b",
        "age": r"\bсинну\s+сол|\bвозраст|\bage\b",
        "gender": r"\bҷинс|\bпол\b|\bgender\b",
        "group_name": r"\bгурӯҳ|\bгрупп[аы]\b|\bgroup\b",
    }.items()
}
FIELD_EXCLUDE_RE = re.compile(r"муҳаққиқ|исследовател|researcher|тамос|контакт|contact|рамз|код\s+анкет|code|сана|дата|date", re.I)


class ImportError_(ValueError):
    """The uploaded file is not a readable questionnaire document."""


@dataclass
class ParsedOption:
    text: str
    value: int | None = None
    allows_text: bool = False


@dataclass
class ParsedQuestion:
    number: str
    text: str
    qtype: str = "single"
    required: bool = True
    help_text: str = ""
    options: list[ParsedOption] = field(default_factory=list)


@dataclass
class ParsedSection:
    title: str
    description: str = ""
    questions: list[ParsedQuestion] = field(default_factory=list)


@dataclass
class ParsedQuestionnaire:
    title: str = ""
    description: str = ""
    instructions: str = ""
    consent_text: str = ""
    completion_message: str = ""
    sections: list[ParsedSection] = field(default_factory=list)
    participant_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def questions(self) -> list[ParsedQuestion]:
        return [q for s in self.sections for q in s.questions]

    def report(self) -> dict:
        types: dict[str, int] = {}
        for q in self.questions:
            types[q.qtype] = types.get(q.qtype, 0) + 1
        return {
            "sections": len(self.sections),
            "questions": len(self.questions),
            "types": types,
            "participant_fields": self.participant_fields,
            "warnings": self.warnings,
            "ignored": self.ignored[:50],
        }


# --------------------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text.replace("\r", "")).strip()


def _iter_blocks(document: DocxDocument) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def _table_rows(table: Table) -> list[list[str]]:
    """Cell texts per row with merged cells de-duplicated."""
    rows = []
    for row in table.rows:
        seen: set[int] = set()
        cells = []
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(_clean(cell.text))
        rows.append(cells)
    return rows


def _split_options(text: str) -> list[ParsedOption]:
    options = []
    for chunk in BOX_RE.split(text)[1:]:
        raw = _clean(chunk)
        if not raw:
            continue
        allows_text = bool(FILL_LINE_RE.search(raw)) or bool(OTHER_RE.match(raw) and ":" in raw)
        cleaned = _clean(FILL_LINE_RE.sub("", raw)).rstrip(":").strip()
        if cleaned:
            options.append(ParsedOption(text=cleaned, allows_text=allows_text))
    return options


def _is_fill_line(text: str) -> bool:
    if not FILL_LINE_RE.search(text):
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters < 60


def _legend(texts: list[str]) -> dict[int, str]:
    joined = " | ".join(texts)
    found = {int(n): _clean(label).strip(" ;,") for n, label in LEGEND_RE.findall(joined)}
    values = sorted(found)
    if len(values) >= 3 and values == list(range(values[0], values[0] + len(values))):
        return found
    return {}


def _likert_columns(header: list[str]) -> tuple[int, list[int]] | None:
    """Return (statement column index, scale values) for a Likert grid header."""
    digit_idx = [i for i, t in enumerate(header) if re.fullmatch(r"-?\d{1,2}", t)]
    if len(digit_idx) < 3 or digit_idx[0] == 0:
        return None
    values = [int(header[i]) for i in digit_idx]
    if values != list(range(values[0], values[0] + len(values))):
        return None
    if digit_idx != list(range(digit_idx[0], digit_idx[0] + len(digit_idx))):
        return None
    return digit_idx[0] - 1, values


def _detect_participant_fields(text: str) -> set[str]:
    if FIELD_EXCLUDE_RE.search(text):
        return set()
    return {key for key, pattern in PARTICIPANT_HINTS.items() if pattern.search(text)}


def _starts_with_box(text: str) -> bool:
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in BOX_CHARS


# --------------------------------------------------------------------------- parser
class _Parser:
    def __init__(self) -> None:
        self.result = ParsedQuestionnaire()
        self.preamble: list[str] = []
        self.section: ParsedSection | None = None
        self.question: ParsedQuestion | None = None
        self.pending_notes: list[str] = []
        self.legend: dict[int, str] = {}
        self.stopped = False
        self.fields: set[str] = set()
        self._carry = ""

    # --- section handling
    def _flush_notes(self, *, to_new_section: bool) -> None:
        if not self.pending_notes:
            return
        text = "\n".join(self.pending_notes)
        self.pending_notes = []
        if to_new_section:
            self._carry = text
            return
        if self.section and self.section.questions:
            last = self.section.questions[-1]
            last.help_text = f"{last.help_text}\n{text}".strip()
        elif self.section:
            self.section.description = f"{self.section.description}\n{text}".strip()

    def open_section(self, title: str, *, sub: bool) -> None:
        self._flush_notes(to_new_section=sub)
        self.section = ParsedSection(title=title)
        if self._carry:
            self.section.description = self._carry
            self._carry = ""
        self.result.sections.append(self.section)
        self.question = None

    def ensure_section(self) -> ParsedSection:
        if self.section is None:
            self.open_section(self.result.title or "Questions", sub=False)
        return self.section  # type: ignore[return-value]

    # --- questions
    def add_question(self, number: str, text: str) -> None:
        section = self.ensure_section()
        if self.pending_notes:
            help_text = "\n".join(self.pending_notes)
            self.pending_notes = []
        else:
            help_text = ""
        required = True
        if OPTIONAL_RE.search(text):
            required = False
        q = ParsedQuestion(number=number, text=text, help_text=help_text, required=required)
        q._explicit_required = bool(REQUIRED_RE.search(text))  # type: ignore[attr-defined]
        section.questions.append(q)
        self.question = q

    def add_options(self, options: list[ParsedOption]) -> None:
        if self.question is None:
            # A check box before any question is the consent statement.
            if not self.result.consent_text and options:
                self.result.consent_text = " ".join(o.text for o in options)
            else:
                self.result.ignored.extend(o.text for o in options)
            return
        self.question.options.extend(options)

    def add_note(self, text: str) -> None:
        if self.section is None:
            self.preamble.append(text)
        elif self.question is None and not self.section.questions:
            self.section.description = f"{self.section.description}\n{text}".strip()
        else:
            self.pending_notes.append(text)

    # --- block handlers
    def paragraph(self, text: str) -> None:
        if not text or self.stopped:
            return
        looks_like_heading = SECTION_RE.match(text) or (self.section is not None and len(text) < 160 and text.isupper())
        if looks_like_heading and STOP_RE.search(text):
            self.stop(text)
            return
        if SECTION_RE.match(text):
            self.open_section(text, sub=False)
            return
        m = QUESTION_RE.match(text)
        if m and not _starts_with_box(text):
            self.add_question(m.group(1), _clean(m.group(2)))
            return
        if _starts_with_box(text):
            self.add_options(_split_options(text))
            return
        sub = SUBSECTION_RE.match(text)
        if sub and self.section is not None and not text.endswith(("?", ":")):
            self.open_section(text, sub=True)
            return
        if _is_fill_line(text):
            self.fields |= _detect_participant_fields(text)
            return
        legend = _legend([text])
        if legend:
            self.legend = legend
        self.add_note(text)

    def table(self, rows: list[list[str]]) -> None:
        if self.stopped or not rows:
            return
        flat = [t for r in rows for t in r if t]
        if not flat:
            return
        # Single-cell table used as a heading banner
        if len(flat) == 1 and len(rows) <= 2:
            self.paragraph(flat[0])
            return
        legend = _legend(flat)
        if legend and not any(_starts_with_box(t) for t in flat):
            self.legend = legend
            return
        likert = _likert_columns(rows[0])
        if likert:
            self.likert_table(rows, *likert)
            return
        box_cells = [t for t in flat if _starts_with_box(t)]
        if box_cells:
            self.add_options([o for t in box_cells for o in _split_options(t)])
            return
        # Participant information tables (label | ______)
        if any(FILL_LINE_RE.search(t) for t in flat):
            for row in rows:
                self.fields |= _detect_participant_fields(" ".join(row))
            return
        self.result.ignored.append(" | ".join(flat)[:300])

    def likert_table(self, rows: list[list[str]], text_col: int, values: list[int]) -> None:
        section = self.ensure_section()
        for row in rows[1:]:
            if len(row) <= text_col or not row[text_col]:
                continue
            number = row[0] if text_col > 0 and re.fullmatch(r"\d{1,3}", row[0] or "") else ""
            options = [ParsedOption(text=self.legend.get(v, str(v)), value=v) for v in values]
            q = ParsedQuestion(number=number, text=row[text_col], qtype="likert", options=options)
            if self.pending_notes:
                section.description = f"{section.description}\n" + "\n".join(self.pending_notes)
                section.description = section.description.strip()
                self.pending_notes = []
            section.questions.append(q)
            self.question = None
        if not self.legend:
            self.result.warnings.append(
                f"Likert grid in “{section.title}” has no scale legend; numeric labels were used."
            )

    def stop(self, text: str) -> None:
        self.stopped = True
        self.result.ignored.append(f"[researcher-only part skipped] {text}")

    # --- finalisation
    def finish(self, fallback_title: str) -> ParsedQuestionnaire:
        r = self.result
        trailing = list(self.pending_notes)
        self.pending_notes = []
        if trailing:
            r.completion_message = "\n".join(trailing)

        # Title / description / instructions from the preamble
        title_idx = None
        for i, line in enumerate(self.preamble):
            m = TITLE_RE.search(line)
            if m:
                r.title = _clean(m.group(1)).replace("\n", " ")
                title_idx = i
                break
        if title_idx is None:
            title_idx = -1
            r.title = fallback_title
        header = [ln for ln in self.preamble[: max(title_idx, 0)]]
        r.description = "\n".join(header)
        r.instructions = "\n".join(self.preamble[title_idx + 1:])

        # Drop empty sections (e.g. heading-only parts), finalise question types
        kept = []
        carried = ""
        for s in r.sections:
            if s.questions:
                if carried:
                    s.description = f"{carried}\n{s.description}".strip()
                    carried = ""
                kept.append(s)
            else:
                # Keep the wording of heading-only parts: move it to the next section.
                carried = "\n".join(filter(None, [carried, s.title, s.description]))
                r.ignored.append(f"[empty section merged into next] {s.title}")
        r.sections = kept
        for s in r.sections:
            has_optional_marker = any(OPTIONAL_RE.search(q.text) for q in s.questions)
            for q in s.questions:
                self._finalise_question(q, section_marks_optional=has_optional_marker)
        r.participant_fields = sorted(self.fields)
        if not r.questions:
            r.warnings.append("No numbered questions were recognised in the document.")
        numbers = [q.number for q in r.questions if q.number]
        dupes = sorted({n for n in numbers if numbers.count(n) > 1}, key=int)
        if dupes:
            r.warnings.append(f"Duplicate question numbers: {', '.join(dupes)}")
        return r

    def _finalise_question(self, q: ParsedQuestion, section_marks_optional: bool = False) -> None:
        # If a section marks some questions "(optional)", the unmarked ones are required.
        explicit_required = getattr(q, "_explicit_required", False) or (
            section_marks_optional and not OPTIONAL_RE.search(q.text)
        )
        if q.qtype == "likert":
            return
        if not q.options:
            low = q.text.lower()
            if any(h in low for h in ("синну сол", "возраст", "how old", "чанд сол")) and len(q.text) < 80:
                q.qtype = "number"
            else:
                q.qtype = "long_text"
            q.required = explicit_required
            return
        words = {o.text.strip().lower().rstrip(".") for o in q.options}
        if len(q.options) == 2 and words & YES_WORDS and words & NO_WORDS:
            q.qtype = "yes_no"
        elif MULTI_RE.search(q.text):
            q.qtype = "multiple"
        else:
            q.qtype = "single"
        for i, opt in enumerate(q.options, start=1):
            if opt.value is None:
                opt.value = i
        if OPTIONAL_RE.search(q.text):
            q.required = False


def parse_docx(fileobj: IO[bytes] | str, fallback_title: str = "Imported questionnaire") -> ParsedQuestionnaire:
    """Parse a .docx file into a structured, unsaved questionnaire."""
    data = fileobj.read() if hasattr(fileobj, "read") else open(fileobj, "rb").read()  # noqa: SIM115
    if not data.startswith(b"PK"):
        raise ImportError_("The file is not a Word .docx document.")
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                raise ImportError_("The archive does not contain a Word document.")
            total = sum(i.file_size for i in zf.infolist())
            if total > 100 * 1024 * 1024:
                raise ImportError_("The document is too large when decompressed.")
        document = docx.Document(BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        if isinstance(exc, ImportError_):
            raise
        raise ImportError_("The Word document could not be read.") from exc

    parser = _Parser()
    for block in _iter_blocks(document):
        if parser.stopped:
            break
        if isinstance(block, Paragraph):
            text = block.text
            if QUESTION_RE.match(text) or TITLE_RE.search(text):
                parser.paragraph(_clean(text.replace("\n", " ")))
            else:
                for line in text.split("\n"):
                    parser.paragraph(_clean(line))
        else:
            parser.table(_table_rows(block))
    return parser.finish(fallback_title)
