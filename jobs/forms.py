from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet

from .exclusions import normalize_exclusion_pattern
from .matching import (
    DEFAULT_WEIGHTS,
    MAX_JOB_MATCH_SCORE,
    MIN_JOB_MATCH_SCORE,
    normalize_text,
)
from .models import ExclusionRule, GermanPlace, ProfileLocation, SearchProfile, WorkspaceUser


class WorkspaceUserForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = WorkspaceUser
        fields = ["name"]


def _normalized_terms(value: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in value.replace("\n", ",").split(","):
        term = " ".join(raw_term.split())
        normalized = normalize_text(term)
        if normalized and normalized not in seen:
            seen.add(normalized)
            terms.append(term)
    return terms


class SearchProfileForm(forms.ModelForm):  # type: ignore[type-arg]
    included_titles = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    excluded_titles = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    required_skill_groups = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    preferred_skills = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    excluded_skills = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    employment_types = forms.CharField(required=False)
    departments = forms.CharField(required=False)
    industries = forms.CharField(required=False)
    seniority_levels = forms.CharField(required=False)

    for _weight_name, _weight_default in DEFAULT_WEIGHTS.items():
        locals()[f"weight_{_weight_name}"] = forms.IntegerField(
            min_value=MIN_JOB_MATCH_SCORE,
            max_value=MAX_JOB_MATCH_SCORE,
            initial=_weight_default,
            label=_weight_name.replace("_", " ").capitalize() + " weight",
        )
    del _weight_name, _weight_default

    class Meta:
        model = SearchProfile
        fields = [
            "name",
            "is_enabled",
            "include_remote",
            "included_titles",
            "excluded_titles",
            "required_skill_groups",
            "preferred_skills",
            "excluded_skills",
            "employment_types",
            "departments",
            "industries",
            "seniority_levels",
            "minimum_salary",
            "maximum_german_level",
            "minimum_score",
        ]
        widgets = {
            "maximum_german_level": forms.Select(
                choices=[
                    ("", "No maximum"),
                    ("A1", "A1"),
                    ("A2", "A2"),
                    ("B1", "B1"),
                    ("B2", "B2"),
                    ("C1", "C1"),
                    ("C2", "C2"),
                ]
            )
        }
        help_texts = {
            "required_skill_groups": (
                "One alternative group per line. Separate terms in a group with commas."
            ),
            "included_titles": "Separate titles with commas.",
            "preferred_skills": "Separate terms with commas.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field_name in _json_list_fields():
            value = getattr(self.instance, field_name, [])
            if field_name == "required_skill_groups" and isinstance(value, list):
                self.initial[field_name] = "\n".join(
                    ", ".join(group) for group in value if isinstance(group, list)
                )
            elif isinstance(value, list):
                self.initial[field_name] = ", ".join(
                    item for item in value if isinstance(item, str)
                )
        weights = self.instance.weights if isinstance(self.instance.weights, dict) else {}
        for weight_name, default in DEFAULT_WEIGHTS.items():
            self.initial[f"weight_{weight_name}"] = weights.get(weight_name, default)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        for field_name in _json_list_fields():
            raw_value = cleaned_data.get(field_name, "")
            if field_name == "required_skill_groups":
                groups: list[list[str]] = []
                seen_groups: set[tuple[str, ...]] = set()
                if isinstance(raw_value, str):
                    for raw_group in raw_value.splitlines():
                        terms = _normalized_terms(raw_group)
                        identity = tuple(normalize_text(term) for term in terms)
                        if terms and identity not in seen_groups:
                            seen_groups.add(identity)
                            groups.append(terms)
                cleaned_data[field_name] = groups
            elif isinstance(raw_value, str):
                cleaned_data[field_name] = _normalized_terms(raw_value)
        if all(f"weight_{weight_name}" in cleaned_data for weight_name in DEFAULT_WEIGHTS):
            cleaned_data["weights"] = {
                weight_name: cleaned_data[f"weight_{weight_name}"]
                for weight_name in DEFAULT_WEIGHTS
            }
        return cleaned_data

    def save(self, commit: bool = True) -> SearchProfile:
        profile = super().save(commit=False)
        for field_name in _json_list_fields():
            setattr(profile, field_name, self.cleaned_data[field_name])
        profile.weights = self.cleaned_data["weights"]
        if commit:
            profile.save()
        return cast(SearchProfile, profile)


def _json_list_fields() -> tuple[str, ...]:
    return (
        "included_titles",
        "excluded_titles",
        "required_skill_groups",
        "preferred_skills",
        "excluded_skills",
        "employment_types",
        "departments",
        "industries",
        "seniority_levels",
    )


class ProfileLocationForm(forms.ModelForm):  # type: ignore[type-arg]
    place = forms.ModelChoiceField(
        queryset=GermanPlace.objects.all(), empty_label="Choose a place", required=False
    )
    radius_km = forms.IntegerField(min_value=1, max_value=500)

    class Meta:
        model = ProfileLocation
        fields = ["place", "radius_km"]


class BaseProfileLocationFormSet(BaseInlineFormSet):  # type: ignore[type-arg]
    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        places: set[int] = set()
        for form in self.forms:
            if form.cleaned_data.get("DELETE"):
                continue
            place = form.cleaned_data.get("place")
            if place is None:
                if form.instance.pk is None:
                    form.cleaned_data["DELETE"] = True
                    continue
                form.add_error("place", "Choose a German place or remove this location.")
                continue
            if place is not None:
                if place.pk in places:
                    raise ValidationError("Choose each German place only once.")
                places.add(place.pk)

    def selected_places(self) -> Iterable[GermanPlace]:
        for form in self.forms:
            place = form.cleaned_data.get("place")
            if place is not None and not form.cleaned_data.get("DELETE"):
                yield place


ProfileLocationFormSet = forms.inlineformset_factory(
    SearchProfile,
    ProfileLocation,
    form=ProfileLocationForm,
    formset=BaseProfileLocationFormSet,
    extra=1,
    can_delete=True,
)


class ExclusionRuleForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = ExclusionRule
        fields = ["kind", "pattern", "is_enabled"]

    def clean_pattern(self) -> str:
        pattern = " ".join(self.cleaned_data["pattern"].split())
        if not normalize_exclusion_pattern(pattern):
            raise ValidationError("Enter a pattern with letters or numbers.")
        return pattern

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        pattern = cleaned_data.get("pattern")
        kind = cleaned_data.get("kind")
        if isinstance(pattern, str) and isinstance(kind, str):
            normalized_pattern = normalize_exclusion_pattern(pattern)
            self.instance.normalized_pattern = normalized_pattern
            if (
                self.instance.user_id
                and ExclusionRule.objects.filter(
                    user=self.instance.user,
                    kind=kind,
                    normalized_pattern=normalized_pattern,
                )
                .exclude(pk=self.instance.pk)
                .exists()
            ):
                self.add_error("pattern", "An equivalent exclusion rule already exists.")
        return cleaned_data

    def save(self, commit: bool = True) -> ExclusionRule:
        rule = super().save(commit=False)
        rule.normalized_pattern = normalize_exclusion_pattern(rule.pattern)
        if commit:
            rule.save()
        return cast(ExclusionRule, rule)
