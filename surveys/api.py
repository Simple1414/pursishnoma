"""Read-only REST API (staff only, object-level filtered)."""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from analytics import stats

from .models import AnswerOption, IndividualAnswer, Question, Questionnaire, Section, SurveyResponse


class OptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerOption
        fields = ["id", "kind", "text", "value", "allows_text", "order"]


class QuestionSerializer(serializers.ModelSerializer):
    options = OptionSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ["id", "number", "code", "text", "help_text", "qtype", "required", "order", "scale_min", "scale_max", "options"]


class SectionSerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Section
        fields = ["id", "title", "description", "order", "questions"]


class QuestionnaireSerializer(serializers.ModelSerializer):
    participant_count = serializers.IntegerField(read_only=True)
    completed_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Questionnaire
        fields = ["id", "public_id", "title", "description", "objective", "author", "language", "status", "is_active", "opens_at", "closes_at", "created_at", "participant_count", "completed_count"]


class AnswerSerializer(serializers.ModelSerializer):
    question_code = serializers.CharField(source="question.code")
    option_text = serializers.CharField(source="option.text", default=None)
    option_value = serializers.IntegerField(source="option.value", default=None)

    class Meta:
        model = IndividualAnswer
        fields = ["question_id", "question_code", "option_id", "option_text", "option_value", "row_id", "text_value", "number_value", "date_value", "rank"]


class ResponseSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    answers = AnswerSerializer(many=True, read_only=True)

    class Meta:
        model = SurveyResponse
        fields = ["id", "reference", "status", "started_at", "submitted_at", "answers"]


class QuestionnaireViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = QuestionnaireSerializer

    def get_queryset(self):
        return Questionnaire.objects.visible_to(self.request.user).with_counts().order_by("-created_at")

    @action(detail=True)
    def structure(self, request, pk=None):
        qn = get_object_or_404(self.get_queryset(), pk=pk)
        sections = qn.sections.prefetch_related("questions__options")
        return Response(SectionSerializer(sections, many=True).data)

    @action(detail=True)
    def responses(self, request, pk=None):
        qn = get_object_or_404(self.get_queryset(), pk=pk)
        qs = (
            SurveyResponse.objects.filter(questionnaire=qn, status=SurveyResponse.Status.COMPLETED)
            .prefetch_related("answers__question", "answers__option")
            .order_by("pk")
        )
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(ResponseSerializer(page, many=True).data)

    @action(detail=True)
    def statistics(self, request, pk=None):
        qn = get_object_or_404(self.get_queryset(), pk=pk)
        ds = stats.load_dataset(qn)
        out = []
        for q in ds.questions:
            s = stats.question_summary(ds, q)
            out.append({k: v for k, v in s.items() if k not in ("question", "texts", "other_texts", "chart")} | {"question_id": q.pk, "code": q.code})
        return Response({"N": ds.n, "questions": out})
