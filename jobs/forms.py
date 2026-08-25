from django import forms

from .models import WorkspaceUser


class WorkspaceUserForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = WorkspaceUser
        fields = ["name"]
