from django import forms


class PromptForm(forms.Form):
    message = forms.CharField(
        label="Message à convertir en audio",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4, "cols": 50}),
    )