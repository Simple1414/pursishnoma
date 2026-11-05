"""Automatic evaluation on the real corrected questionnaire, with hand-checked expected scores."""
import json
import shutil
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from core.models import User
from evaluation.models import Evaluation
from evaluation.services import auto_evaluate, save_evaluation
from surveys import services
from surveys.models import Questionnaire
from surveys.tests.helpers import PASSWORD, make_user

SEED = Path(settings.BASE_DIR) / "seed"


def answer(qn, data: dict, *, submit=True):
    """data: {"Q13": 1 | [1, 2] | "text"} with option *codes* → stored response."""
    qd = QueryDict(mutable=True)
    for code, v in data.items():
        q = qn.questions.get(code=code)
        if isinstance(v, str):
            qd[f"q_{q.pk}"] = v
        else:
            vals = v if isinstance(v, list) else [v]
            qd.setlist(f"q_{q.pk}", [str(q.options.get(value=x, kind="option").pk) for x in vals])
    r = services.start_response(qn, {}, consent=True, ip_hash="", language="tg")
    services.autosave(r, qd)
    if submit:
        from django.utils import timezone

        type(r).objects.filter(pk=r.pk).update(status="completed", submitted_at=timezone.now())
        r.refresh_from_db()
    return r


class AutoScoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", role=User.Role.ADMIN)
        d = Path(tempfile.mkdtemp())
        for f in ("02-pursishnoma-islohshuda.docx", "auto_rules.json"):
            shutil.copy(SEED / f, d / f)
        rules = json.loads((SEED / "auto_rules.json").read_text(encoding="utf-8"))
        crit = [{"name": n, "max_score": 10, "weight": w} for n, w in zip(rules, [1, 2, 3, 2, 2])]
        (d / "manifest.json").write_text(json.dumps([{"file": "02-pursishnoma-islohshuda.docx", "publish": True, "criteria": crit}], ensure_ascii=False), encoding="utf-8")
        call_command("seed_questionnaires", "--dir", str(d), stdout=StringIO())
        shutil.rmtree(d)
        cls.qn = Questionnaire.objects.get()

    def full_answers(self, **override):
        data = {f"Q{i}": 1 for i in range(1, 12)}
        data.update({"Q12": [1, 2], "Q13": 1, "Q14": 1, "Q15": [1, 2, 4, 6], "Q16": 2, "Q17": [1, 2, 3, 4],
                     "Q18": [1], "Q19": 1, "Q20": 1, "Q21": 2, "Q22": 1, "Q23": 1, "Q24": [2], "Q25": 1})
        data.update({f"Q{i}": 5 for i in range(26, 31)})
        data["Q31"] = 1
        data.update({f"Q{i}": 4 for i in range(32, 37)})
        data["Q37"] = 2
        data.update({f"Q{i}": 5 for i in range(38, 49)})
        long = " ".join(["калима"] * 35)
        data.update({f"Q{i}": long for i in range(49, 54)})
        data.update(override)
        return data

    def scores(self, ev):
        return {s.criterion.name: s.score for s in ev.scores.select_related("criterion")}

    def test_excellent_response_scores_full_marks(self):
        ev = auto_evaluate(answer(self.qn, self.full_answers()))
        self.assertTrue(ev.is_automatic)
        self.assertEqual(set(self.scores(ev).values()), {Decimal("10.0")})
        self.assertEqual(ev.final_score, Decimal("100.00"))
        self.assertEqual(len(ev.auto_details), 5)

    def test_inconsistencies_and_short_answers_are_penalised(self):
        data = self.full_answers(Q31=5, Q15=[1, 11], Q13=4, Q22=5)
        data.update({f"Q{i}": "ҳа" for i in range(49, 54)})
        ev = auto_evaluate(answer(self.qn, data))
        s = self.scores(ev)
        # reverse item 31 contradicts A-mean, Q15 "none" with tools, daily use (Q16=2) vs "not familiar" (Q13=4): 3 × 2.5
        self.assertEqual(s["Мувофиқати дохилии ҷавобҳо"], Decimal("2.5"))
        self.assertEqual(s["Амиқ ва асоснок будани ҷавобҳои кушода"], Decimal("2.0"))  # 1 word → 0.2 everywhere
        # critical stance: Q22 never (0/4) + C mean 5 (3/3) + D mean 5 (3/3) = 6/10
        self.assertEqual(s["Мавқеи интиқодӣ ва масъулиятнок"], Decimal("6.0"))
        notes = " ".join(n for d in ev.auto_details for n in d["notes"])
        self.assertIn("Q31", notes)

    def test_non_user_skip_logic_in_completeness(self):
        data = {k: v for k, v in self.full_answers(Q15=[11]).items() if not (16 <= int(k[1:]) <= 23 or 26 <= int(k[1:]) <= 37)}
        ev = auto_evaluate(answer(self.qn, data))
        self.assertEqual(self.scores(ev)["Пуррагии ҷавобҳо"], Decimal("10.0"))

    def test_manual_evaluation_is_never_overwritten(self):
        r = answer(self.qn, self.full_answers())
        crit = {c.pk: Decimal("3") for c in self.qn.criteria.all()}
        save_evaluation(r, self.admin, scores=crit, comment="manual", recommendations="", reviewed=True)
        self.assertIsNone(auto_evaluate(r, force=True))
        self.assertEqual(Evaluation.objects.get(response=r).comment, "manual")

    def test_public_submission_is_evaluated_automatically(self):
        r = answer(self.qn, self.full_answers(), submit=False)
        client = self.client_class()
        session = client.session
        session[f"rs_response_{self.qn.pk}"] = r.pk
        session.save()
        # re-post the stored answers through the real submit view
        values = services.existing_answer_map(r)
        qd = {k: v if len(v) > 1 else v[0] for k, v in values.items()}
        resp = client.post(reverse("public:submit", args=[self.qn.public_id]), qd)
        self.assertEqual(resp.status_code, 302)
        ev = Evaluation.objects.get(response=r)
        self.assertTrue(ev.is_automatic)
        self.assertEqual(ev.status, Evaluation.Status.IN_REVIEW)

    def test_queue_button_and_command(self):
        answer(self.qn, self.full_answers())
        out = StringIO()
        call_command("auto_evaluate", stdout=out)
        self.assertIn("1 response", out.getvalue())
        self.client.login(username="admin", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("evaluation:run_auto")).status_code, 405)
        r = self.client.post(reverse("evaluation:run_auto"), {"questionnaire": self.qn.pk})
        self.assertEqual(r.status_code, 302)
        page = self.client.get(reverse("evaluation:evaluate", args=[Evaluation.objects.get().response_id]))
        self.assertContains(page, "Q13: варианти 1")
