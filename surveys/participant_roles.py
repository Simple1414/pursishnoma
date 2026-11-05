"""Which participant fields apply to each status (pupil, student, teacher …).

When a questionnaire collects "Your status" (``education_level``), the start form
shows only the fields listed for the chosen status, with status-specific labels.
Values of fields that do not apply are discarded on the server.
"""
from django.utils.translation import gettext_lazy as _

# key -> (label, required, hint)
ROLE_FIELDS = {
    "Хонандаи мактаб": {
        "university": (_("Name of your school"), True, _("For example: Secondary school No. 5, Bokhtar")),
        "academic_year": (_("Grade"), False, _("For example: 9th grade")),
    },
    "Донишҷӯи коллеҷ / литсей": {
        "university": (_("Name of your college or lyceum"), True, ""),
        "specialty": (_("Specialty"), False, ""),
        "academic_year": (_("Year of study"), False, _("For example: 2nd year")),
    },
    "Донишҷӯи донишгоҳ (бакалавр)": {
        "university": (_("Name of your university"), True, ""),
        "faculty": (_("Faculty"), False, ""),
        "specialty": (_("Specialty"), False, ""),
        "academic_year": (_("Year of study"), False, _("For example: 2nd year")),
    },
    "Магистрант / докторант": {
        "university": (_("University or research institution"), True, ""),
        "faculty": (_("Faculty"), False, ""),
        "specialty": (_("Specialty"), False, ""),
        "academic_year": (_("Year of study"), False, ""),
    },
    "Омӯзгори мактаб": {
        "university": (_("Name of your school"), True, ""),
        "subject": (_("Subject you teach"), False, _("For example: mathematics")),
    },
    "Омӯзгори коллеҷ / литсей": {
        "university": (_("Name of your college or lyceum"), True, ""),
        "faculty": (_("Department"), False, ""),
        "subject": (_("Subject you teach"), False, ""),
    },
    "Омӯзгори донишгоҳ": {
        "university": (_("Name of your university"), True, ""),
        "faculty": (_("Faculty / department"), False, ""),
        "subject": (_("Subject you teach"), False, ""),
    },
    "Дигар": {
        "university": (_("Institution or organisation"), False, ""),
    },
}

CONDITIONAL_KEYS = {"university", "faculty", "specialty", "academic_year", "subject"}
