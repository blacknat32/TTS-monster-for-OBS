from django import forms

class prompt_Form(forms.Form):
    name = forms.ChoiceField(label="Select a name", choices=[('John', 'John'), ('Jane', 'Jane')])
    your_message = forms.CharField(label="Your message", max_length=1000, widget=forms.Textarea(attrs={'rows': 4, 'cols': 50}))